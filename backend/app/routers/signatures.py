"""Built-in electronic signatures.

A signature request freezes one document version (by SHA-256) and sends each signer a
private link, in order. Signers do not need an account: the link token is the credential,
so only its hash is stored and it expires. When the last signer signs, a signed copy is
produced: the original PDF (or nothing, for other formats) followed by a certificate of
completion with every signer's name, time, IP address and the document fingerprint.
"""

import base64
import hashlib
import io
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pymupdf
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel

from ..auth import current_user, require
from ..database import add_audit, connection, utc_now
from ..storage import stored_file, upload_dir
from ..workflow import contract_recipients, notify, send_email
from .contracts import docx_preview, load_contract

router = APIRouter(prefix="/api")

SIGNABLE_STATUSES = {"Cleared to Start", "In Progress"}
MAX_SIGNERS = 10
MAX_SIGNATURE_BYTES = 300_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignerIn(BaseModel):
    name: str
    email: str


class SignatureCreate(BaseModel):
    signers: list[SignerIn]
    message: str | None = None


class SignIn(BaseModel):
    signed_name: str
    consent: bool
    signature_image: str | None = None  # optional drawn signature, data:image/png;base64,...


class DeclineIn(BaseModel):
    reason: str


class VoidIn(BaseModel):
    reason: str | None = None


def link_days() -> int:
    return int(os.getenv("SIGNING_LINK_DAYS", "30"))


def app_url() -> str:
    return os.getenv("PUBLIC_APP_URL", "http://localhost:3000").rstrip("/")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalise_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def issue_link(conn, signer_id: int) -> str:
    """Create a fresh signing link for a signer, replacing any earlier one."""
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=link_days())).isoformat()
    conn.execute("UPDATE signature_signers SET token_hash=?, token_expires_at=? WHERE id=?",
                 (token_hash(token), expires, signer_id))
    return f"{app_url()}/sign/{token}"


def invite(conn, request, contract, signer, link: str):
    subject = f"Please sign: {contract['title']}"
    body = (f"Hello {signer['name']},\n\nYou are asked to sign “{contract['title']}”"
            f"{' with ' + contract['vendor_name'] if contract['vendor_name'] else ''}.\n"
            + (f"\nMessage: {request['message']}\n" if request["message"] else "")
            + f"\nReview and sign here (the link is personal and expires in {link_days()} days):\n{link}\n")
    send_email(signer["email"], subject, body)
    if signer["user_id"]:
        notify(conn, [signer["user_id"]], "signature", subject, "Open the signing link in your email.", contract["id"],
               f"sign:{signer['id']}:{request['id']}")


def request_detail(conn, request_id: int, links: dict[int, str] | None = None) -> dict:
    request = conn.execute(
        """SELECT r.*, u.name created_by_name FROM signature_requests r JOIN users u ON u.id=r.created_by WHERE r.id=?""",
        (request_id,),
    ).fetchone()
    signers = conn.execute("SELECT * FROM signature_signers WHERE request_id=? ORDER BY signer_order", (request_id,)).fetchall()
    item = {k: request[k] for k in request.keys() if k not in {"file_path", "signed_file_path"}}
    item["has_signed_copy"] = bool(request["signed_file_path"])
    item["signers"] = [{
        **{k: s[k] for k in ("id", "signer_order", "name", "email", "status", "viewed_at", "signed_name", "signed_at",
                             "decline_reason", "token_expires_at")},
        "link": (links or {}).get(s["id"]),
    } for s in signers]
    return item


def active_request(conn, contract_id: int):
    return conn.execute("SELECT * FROM signature_requests WHERE contract_id=? AND status='Sent'", (contract_id,)).fetchone()


# --- sender side ---------------------------------------------------------------------

@router.get("/contracts/{contract_id}/signatures")
def list_requests(contract_id: int, _: dict = Depends(current_user)):
    with connection() as conn:
        load_contract(conn, contract_id)
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM signature_requests WHERE contract_id=? ORDER BY id DESC", (contract_id,))]
        return [request_detail(conn, request_id) for request_id in ids]


@router.post("/contracts/{contract_id}/signatures", status_code=201)
def create_request(contract_id: int, body: SignatureCreate, user: dict = Depends(require("signature.send"))):
    signers = [(" ".join(s.name.split()), s.email.strip().lower()) for s in body.signers]
    if not 1 <= len(signers) <= MAX_SIGNERS:
        raise HTTPException(422, f"Add between 1 and {MAX_SIGNERS} signers.")
    for name, email in signers:
        if not name or not EMAIL_RE.match(email):
            raise HTTPException(422, "Every signer needs a name and a valid email.")
    if len({email for _, email in signers}) != len(signers):
        raise HTTPException(422, "Each signer must have a different email.")
    with connection() as conn:
        contract = load_contract(conn, contract_id)
        if contract["status"] not in SIGNABLE_STATUSES:
            raise HTTPException(409, f"Contracts can be sent for signature once approved (Cleared to Start or In Progress); this one is {contract['status']}.")
        if not contract["file_path"]:
            raise HTTPException(409, "Upload the contract document before sending it for signature.")
        if active_request(conn, contract_id):
            raise HTTPException(409, "This contract already has a signature request in progress. Void it first.")
        path = stored_file(contract["file_path"])
        now = utc_now()
        cursor = conn.execute(
            """INSERT INTO signature_requests (contract_id, status, document_version, document_sha256, file_name, file_type,
               file_path, message, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (contract_id, "Sent", contract["document_version"], file_sha256(path), contract["file_name"], contract["file_type"],
             str(path), (body.message or "").strip() or None, user["id"], now),
        )
        request_id = cursor.lastrowid
        links = {}
        for order, (name, email) in enumerate(signers, start=1):
            account = conn.execute("SELECT id FROM users WHERE lower(email)=? AND active=1", (email,)).fetchone()
            signer_id = conn.execute(
                """INSERT INTO signature_signers (request_id, signer_order, name, email, user_id, status) VALUES (?,?,?,?,?,?)""",
                (request_id, order, name, email, account["id"] if account else None, "Pending" if order == 1 else "Waiting"),
            ).lastrowid
            if order == 1:  # later signers get their link when it is their turn
                links[signer_id] = issue_link(conn, signer_id)
        request = conn.execute("SELECT * FROM signature_requests WHERE id=?", (request_id,)).fetchone()
        first = conn.execute("SELECT * FROM signature_signers WHERE request_id=? AND signer_order=1", (request_id,)).fetchone()
        invite(conn, request, contract, first, links[first["id"]])
        add_audit(conn, "Sent for signature", user["name"], contract_id,
                  f"Document v{contract['document_version']} to " + ", ".join(f"{n} <{e}>" for n, e in signers))
        # Links are returned once, here, so the sender can share them if email is not configured.
        return request_detail(conn, request_id, links)


def load_request(conn, request_id: int):
    row = conn.execute("SELECT * FROM signature_requests WHERE id=?", (request_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Signature request not found")
    return row


@router.post("/signatures/{request_id}/signers/{signer_id}/link")
def new_link(request_id: int, signer_id: int, user: dict = Depends(require("signature.send"))):
    """Issue a new link for a signer (the old one stops working) and email it again."""
    with connection() as conn:
        request = load_request(conn, request_id)
        if request["status"] != "Sent":
            raise HTTPException(409, f"This request is {request['status'].lower()}.")
        signer = conn.execute("SELECT * FROM signature_signers WHERE id=? AND request_id=?", (signer_id, request_id)).fetchone()
        if not signer:
            raise HTTPException(404, "Signer not found")
        if signer["status"] != "Pending":
            raise HTTPException(409, f"{signer['name']} has already signed." if signer["status"] == "Signed"
                                else f"It is not {signer['name']}'s turn yet.")
        link = issue_link(conn, signer_id)
        invite(conn, request, load_contract(conn, request["contract_id"]), signer, link)
        add_audit(conn, "Signing link reissued", user["name"], request["contract_id"], f"{signer['name']} <{signer['email']}>")
        return request_detail(conn, request_id, {signer_id: link})


@router.post("/signatures/{request_id}/void")
def void_request(request_id: int, body: VoidIn, user: dict = Depends(current_user)):
    with connection() as conn:
        request = load_request(conn, request_id)
        if request["status"] != "Sent":
            raise HTTPException(409, f"This request is already {request['status'].lower()}.")
        if request["created_by"] != user["id"] and user["role"] != "admin":
            raise HTTPException(403, "Only the sender or an admin can void this request.")
        reason = (body.reason or "").strip() or None
        conn.execute("UPDATE signature_requests SET status='Voided', completed_at=?, void_reason=? WHERE id=?",
                     (utc_now(), reason, request_id))
        conn.execute("UPDATE signature_signers SET token_hash=NULL WHERE request_id=?", (request_id,))
        add_audit(conn, "Signature request voided", user["name"], request["contract_id"], reason)
        return request_detail(conn, request_id)


@router.get("/signatures/{request_id}/signed-copy")
def signed_copy(request_id: int, _: dict = Depends(current_user)):
    with connection() as conn:
        request = load_request(conn, request_id)
    if not request["signed_file_path"]:
        raise HTTPException(404, "No signed copy yet")
    stem = (request["file_name"] or "contract").rsplit(".", 1)[0]
    return FileResponse(stored_file(request["signed_file_path"]), media_type="application/pdf",
                        filename=f"{stem} - signed.pdf", content_disposition_type="inline")


# --- signer side (no account; the link token is the credential) ----------------------

def signer_by_token(conn, token: str):
    signer = conn.execute("SELECT * FROM signature_signers WHERE token_hash=?", (token_hash(token),)).fetchone()
    if not signer or not signer["token_expires_at"] or signer["token_expires_at"] < utc_now():
        raise HTTPException(404, "This signing link is invalid or has expired. Ask the sender for a new one.")
    request = load_request(conn, signer["request_id"])
    return signer, request


def public_view(conn, signer, request) -> dict:
    contract = load_contract(conn, request["contract_id"])
    others = conn.execute("SELECT name, status, signer_order, signed_at FROM signature_signers WHERE request_id=? ORDER BY signer_order",
                          (request["id"],)).fetchall()
    return {
        "request_status": request["status"], "contract_title": contract["title"], "vendor_name": contract["vendor_name"],
        "file_name": request["file_name"], "file_type": request["file_type"], "document_sha256": request["document_sha256"],
        "message": request["message"], "signer": {k: signer[k] for k in ("name", "email", "status", "signed_at", "signer_order")},
        "signers": [dict(o) for o in others],
        "can_sign": request["status"] == "Sent" and signer["status"] == "Pending",
    }


@router.get("/sign/{token}")
def view_signing(token: str):
    with connection() as conn:
        signer, request = signer_by_token(conn, token)
        if not signer["viewed_at"]:
            conn.execute("UPDATE signature_signers SET viewed_at=? WHERE id=?", (utc_now(), signer["id"]))
            add_audit(conn, "Signing link opened", signer["name"], request["contract_id"], signer["email"])
        return public_view(conn, signer, request)


@router.get("/sign/{token}/document")
def signing_document(token: str):
    with connection() as conn:
        signer, request = signer_by_token(conn, token)
    path = stored_file(request["file_path"])
    if path.suffix.lower() == ".docx":
        return docx_preview(path, request["file_name"] or "Contract")
    return FileResponse(path, media_type=request["file_type"], filename=request["file_name"], content_disposition_type="inline")


def clean_signature_image(data_url: str | None) -> str | None:
    if not data_url:
        return None
    prefix = "data:image/png;base64,"
    if not data_url.startswith(prefix) or len(data_url) > MAX_SIGNATURE_BYTES * 4 // 3 + len(prefix):
        raise HTTPException(422, "The drawn signature must be a PNG image under 300 KB.")
    try:
        image = Image.open(io.BytesIO(base64.b64decode(data_url[len(prefix):], validate=True)))
        image.load()
    except Exception:
        raise HTTPException(422, "The drawn signature could not be read.")
    if image.width > 2000 or image.height > 1000:
        raise HTTPException(422, "The drawn signature is too large.")
    out = io.BytesIO()
    image.convert("RGBA").save(out, format="PNG")  # re-encode: keep pixels only
    return prefix + base64.b64encode(out.getvalue()).decode()


def client_ip(http: Request) -> str:
    forwarded = http.headers.get("x-forwarded-for", "")
    return (forwarded.split(",")[0].strip() if forwarded else None) or (http.client.host if http.client else "unknown")


@router.post("/sign/{token}")
def sign(token: str, body: SignIn, http: Request):
    if not body.consent:
        raise HTTPException(422, "Tick the box to agree to sign electronically.")
    image = clean_signature_image(body.signature_image)
    with connection() as conn:
        signer, request = signer_by_token(conn, token)
        if request["status"] != "Sent":
            raise HTTPException(409, f"This signature request is {request['status'].lower()}.")
        if signer["status"] != "Pending":
            raise HTTPException(409, "You have already signed." if signer["status"] == "Signed"
                                else "It is not your turn to sign yet." if signer["status"] == "Waiting"
                                else f"You have {signer['status'].lower()} this request.")
        if normalise_name(body.signed_name) != normalise_name(signer["name"]):
            raise HTTPException(422, f"Type your full name exactly as shown: {signer['name']}")
        if file_sha256(stored_file(request["file_path"])) != request["document_sha256"]:
            raise HTTPException(409, "The document no longer matches the one sent for signature. Contact the sender.")
        now = utc_now()
        conn.execute(
            """UPDATE signature_signers SET status='Signed', signed_name=?, signature_image=?, signed_at=?, ip_address=?,
               user_agent=?, token_hash=NULL WHERE id=?""",
            (" ".join(body.signed_name.split()), image, now, client_ip(http), http.headers.get("user-agent", "")[:300], signer["id"]),
        )
        contract = load_contract(conn, request["contract_id"])
        add_audit(conn, "Signed", signer["name"], contract["id"], f"{signer['email']} signed document v{request['document_version']}")
        following = conn.execute(
            "SELECT * FROM signature_signers WHERE request_id=? AND status='Waiting' ORDER BY signer_order LIMIT 1",
            (request["id"],),
        ).fetchone()
        if following:
            conn.execute("UPDATE signature_signers SET status='Pending' WHERE id=?", (following["id"],))
            invite(conn, request, contract, following, issue_link(conn, following["id"]))
        else:
            complete(conn, request, contract)
        signer = conn.execute("SELECT * FROM signature_signers WHERE id=?", (signer["id"],)).fetchone()
        return public_view(conn, signer, load_request(conn, request["id"]))


@router.post("/sign/{token}/decline")
def decline(token: str, body: DeclineIn):
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(422, "Tell the sender why you are declining.")
    with connection() as conn:
        signer, request = signer_by_token(conn, token)
        if request["status"] != "Sent" or signer["status"] != "Pending":
            raise HTTPException(409, "This request can no longer be declined.")
        now = utc_now()
        conn.execute("UPDATE signature_signers SET status='Declined', decline_reason=?, signed_at=?, token_hash=NULL WHERE id=?",
                     (reason[:1000], now, signer["id"]))
        conn.execute("UPDATE signature_requests SET status='Declined', completed_at=? WHERE id=?", (now, request["id"]))
        conn.execute("UPDATE signature_signers SET token_hash=NULL WHERE request_id=?", (request["id"],))
        contract = load_contract(conn, request["contract_id"])
        add_audit(conn, "Signature declined", signer["name"], contract["id"], reason[:300])
        notify(conn, [request["created_by"], *contract_recipients(conn, contract["owner"])], "signature",
               f"Signature declined: {contract['title']}", f"{signer['name']}: {reason[:300]}", contract["id"])
        signer = conn.execute("SELECT * FROM signature_signers WHERE id=?", (signer["id"],)).fetchone()
        return public_view(conn, signer, load_request(conn, request["id"]))


# --- completion ----------------------------------------------------------------------

def complete(conn, request, contract):
    now = utc_now()
    signers = conn.execute("SELECT * FROM signature_signers WHERE request_id=? ORDER BY signer_order", (request["id"],)).fetchall()
    path = upload_dir() / f"{uuid.uuid4().hex}-signed.pdf"
    build_signed_pdf(stored_file(request["file_path"]), path, request, contract, signers, now)
    conn.execute(
        "UPDATE signature_requests SET status='Completed', completed_at=?, signed_file_path=?, signed_file_sha256=? WHERE id=?",
        (now, str(path), file_sha256(path), request["id"]),
    )
    conn.execute("UPDATE contracts SET signed_at=?, updated_at=? WHERE id=?", (now, now, contract["id"]))
    add_audit(conn, "Signing completed", "System", contract["id"], f"All {len(signers)} signer(s) signed document v{request['document_version']}")
    notify(conn, [request["created_by"], *contract_recipients(conn, contract["owner"])], "signature",
           f"Signed: {contract['title']}", "Everyone has signed. The signed copy is on the contract's Signatures tab.", contract["id"])
    for signer in signers:
        send_email(signer["email"], f"Completed: {contract['title']}",
                   f"All parties have signed “{contract['title']}”. Ask the sender for the signed copy if you need one.")


def build_signed_pdf(source, target, request, contract, signers, completed_at: str):
    output = pymupdf.open()
    if source.suffix.lower() == ".pdf":
        with pymupdf.open(source) as original:
            output.insert_pdf(original)
    page = output.new_page(width=595, height=842)  # A4
    y = 60

    def line(text, size=10, bold=False, color=(0.1, 0.13, 0.2), gap=None):
        nonlocal page, y
        if y > 780:
            page = output.new_page(width=595, height=842)
            y = 60
        page.insert_text((50, y), text, fontsize=size, fontname="hebo" if bold else "helv", color=color)
        y += gap or size + 6

    line("Certificate of completion", 20, True, gap=30)
    line(f"Contract: {contract['title']}", 11, True)
    if contract["vendor_name"]:
        line(f"Vendor: {contract['vendor_name']}")
    line(f"Document: {request['file_name']} (version {request['document_version']})")
    line(f"Document SHA-256: {request['document_sha256']}", 8)
    line(f"Sent: {request['created_at'][:19].replace('T', ' ')} UTC    Completed: {completed_at[:19].replace('T', ' ')} UTC", gap=28)
    for signer in signers:
        if y > 640:
            page = output.new_page(width=595, height=842)
            y = 60
        line(f"{signer['signer_order']}. {signer['name']} <{signer['email']}>", 12, True)
        line(f"Signed: {signer['signed_at'][:19].replace('T', ' ')} UTC    IP address: {signer['ip_address']}")
        line(f"Typed name: {signer['signed_name']}")
        if signer["viewed_at"]:
            line(f"First opened link: {signer['viewed_at'][:19].replace('T', ' ')} UTC")
        line(f"Device: {(signer['user_agent'] or 'unknown')[:95]}", 8)
        if signer["signature_image"]:
            png = base64.b64decode(signer["signature_image"].split(",", 1)[1])
            page.insert_image(pymupdf.Rect(50, y, 230, y + 54), stream=png, keep_proportion=True)
            y += 62
        else:
            y += 14  # text is drawn from its baseline; leave room for the larger glyphs
            line(signer["signed_name"], 18, gap=26, color=(0.15, 0.2, 0.5))
        y += 8
    line("Each signer agreed to sign electronically, typed their full name and used a personal link sent to the", 8, gap=11)
    line("email address above. The SHA-256 fingerprint identifies the exact document that was signed.", 8)
    output.set_metadata({"title": f"{contract['title']} - signed", "producer": "Clause"})
    output.save(target)
    output.close()
