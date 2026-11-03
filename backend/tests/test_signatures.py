import base64
import hashlib
import io

import pymupdf
import pytest
from PIL import Image

from .conftest import upload, validated


def pdf_bytes(text="Master services agreement"):
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def approved_contract(client, users, content=None, name="contract.pdf"):
    contract = upload(client, users["procurement"], name=name, content=content or pdf_bytes())
    validated(client, users, contract)
    request = client.post(f"/api/contracts/{contract['id']}/approvals", headers=users["procurement"],
                          json={"roles": ["legal"]}).json()
    response = client.post(f"/api/approvals/{request['id']}/decision", headers=users["legal"], json={"decision": "approve"})
    assert response.status_code == 200, response.text
    return client.get(f"/api/contracts/{contract['id']}", headers=users["admin"]).json()


SIGNERS = [{"name": "Vera Vendor", "email": "vera@vendor.example"}, {"name": "Lena  Ong", "email": "Legal@Clause.local"}]


def send(client, users, contract, signers=SIGNERS):
    response = client.post(f"/api/contracts/{contract['id']}/signatures", headers=users["procurement"],
                           json={"signers": signers, "message": "Please sign by Friday"})
    assert response.status_code == 201, response.text
    return response.json()


def token_of(link):
    return link.rsplit("/sign/", 1)[1]


def png_data_url():
    out = io.BytesIO()
    Image.new("RGBA", (200, 60), (0, 0, 0, 0)).save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def test_full_sequential_signing_produces_signed_pdf(client, users):
    contract = approved_contract(client, users)
    assert contract["status"] == "Cleared to Start"
    request = send(client, users, contract)
    first, second = request["signers"]
    assert (first["status"], second["status"]) == ("Pending", "Waiting")
    assert second["email"] == "legal@clause.local" and second["name"] == "Lena Ong"
    assert second["link"] is None  # issued when it is their turn
    first_token = token_of(first["link"])
    assert client.post(f"/api/signatures/{request['id']}/signers/{second['id']}/link",
                       headers=users["procurement"]).status_code == 409

    # Links are only shown once; listing does not reveal them.
    listed = client.get(f"/api/contracts/{contract['id']}/signatures", headers=users["viewer"]).json()
    assert all(s["link"] is None for s in listed[0]["signers"])

    view = client.get(f"/api/sign/{first_token}").json()
    assert view["can_sign"] and view["contract_title"] == contract["title"] and view["message"] == "Please sign by Friday"
    assert client.get(f"/api/sign/{first_token}/document").headers["content-type"] == "application/pdf"
    assert client.post(f"/api/sign/{first_token}", json={"signed_name": "Vera Vendor", "consent": False}).status_code == 422
    assert client.post(f"/api/sign/{first_token}", json={"signed_name": "Someone Else", "consent": True}).status_code == 422
    signed = client.post(f"/api/sign/{first_token}", json={"signed_name": " vera   vendor ", "consent": True,
                                                            "signature_image": png_data_url()},
                         headers={"x-forwarded-for": "203.0.113.9"})
    assert signed.status_code == 200, signed.text
    assert signed.json()["signer"]["status"] == "Signed"
    assert client.get(f"/api/sign/{first_token}").status_code == 404  # used links stop working

    # The second signer has an account, so gets an in-app notification with their turn.
    notes = client.get("/api/notifications", headers=users["legal"]).json()
    assert any(n["kind"] == "signature" for n in (notes["items"] if isinstance(notes, dict) else notes))
    second_link = client.post(f"/api/signatures/{request['id']}/signers/{second['id']}/link", headers=users["procurement"]).json()
    second_token = token_of(next(s["link"] for s in second_link["signers"] if s["id"] == second["id"]))
    assert client.post(f"/api/sign/{second_token}", json={"signed_name": "Lena Ong", "consent": True}).status_code == 200

    done = client.get(f"/api/contracts/{contract['id']}/signatures", headers=users["viewer"]).json()[0]
    assert done["status"] == "Completed" and done["has_signed_copy"]
    assert client.get(f"/api/contracts/{contract['id']}", headers=users["viewer"]).json()["signed_at"]
    pdf = client.get(f"/api/signatures/{request['id']}/signed-copy", headers=users["viewer"])
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    with pymupdf.open(stream=pdf.content, filetype="pdf") as document:
        assert document.page_count == 2  # original page + certificate
        certificate = document[1].get_text()
    assert "Certificate of completion" in certificate and "203.0.113.9" in certificate
    assert done["document_sha256"] in certificate.replace("\n", "")
    audit = client.get(f"/api/audit?contract_id={contract['id']}", headers=users["admin"]).json()
    assert {"Sent for signature", "Signed", "Signing completed"} <= {a["action"] for a in audit}


def test_sending_rules(client, users):
    quoting = upload(client, users["procurement"], content=pdf_bytes())
    assert client.post(f"/api/contracts/{quoting['id']}/signatures", headers=users["procurement"],
                       json={"signers": SIGNERS}).status_code == 409  # not approved yet
    contract = approved_contract(client, users)
    url = f"/api/contracts/{contract['id']}/signatures"
    assert client.post(url, headers=users["finance"], json={"signers": SIGNERS}).status_code == 403
    assert client.post(url, headers=users["procurement"], json={"signers": []}).status_code == 422
    assert client.post(url, headers=users["procurement"], json={"signers": [{"name": "A", "email": "nope"}]}).status_code == 422
    dupes = [{"name": "A", "email": "a@x.example"}, {"name": "B", "email": "A@x.example"}]
    assert client.post(url, headers=users["procurement"], json={"signers": dupes}).status_code == 422
    send(client, users, contract)
    assert client.post(url, headers=users["procurement"], json={"signers": SIGNERS}).status_code == 409
    version = client.post(f"/api/contracts/{contract['id']}/versions", headers=users["procurement"],
                          files={"file": ("v2.pdf", pdf_bytes("v2"), "application/pdf")})
    assert version.status_code == 409


def test_decline_and_void(client, users):
    contract = approved_contract(client, users)
    request = send(client, users, contract)
    token = token_of(request["signers"][0]["link"])
    assert client.post(f"/api/sign/{token}/decline", json={"reason": " "}).status_code == 422
    declined = client.post(f"/api/sign/{token}/decline", json={"reason": "Liability cap is too low"})
    assert declined.status_code == 200 and declined.json()["request_status"] == "Declined"
    notes = client.get("/api/notifications", headers=users["procurement"]).json()
    items = notes["items"] if isinstance(notes, dict) else notes
    assert any("declined" in n["title"].lower() for n in items)

    again = send(client, users, contract)  # a declined request no longer blocks a new one
    assert client.post(f"/api/signatures/{again['id']}/void", headers=users["finance"], json={}).status_code == 403
    voided = client.post(f"/api/signatures/{again['id']}/void", headers=users["procurement"], json={"reason": "Wrong signer"})
    assert voided.json()["status"] == "Voided"
    assert client.get(f"/api/sign/{token_of(again['signers'][0]['link'])}").status_code == 404


def test_putting_contract_on_hold_voids_signature(client, users):
    contract = approved_contract(client, users)
    request = send(client, users, contract)
    response = client.patch(f"/api/contracts/{contract['id']}", headers=users["procurement"], json={"status": "On Hold"})
    assert response.status_code == 200, response.text
    assert client.get(f"/api/contracts/{contract['id']}/signatures", headers=users["admin"]).json()[0]["status"] == "Voided"
    assert client.get(f"/api/sign/{token_of(request['signers'][0]['link'])}").status_code == 404


def test_docx_gets_certificate_only_and_tampering_is_detected(client, users):
    from docx import Document
    buffer = io.BytesIO()
    Document().save(buffer)
    contract = approved_contract(client, users, content=buffer.getvalue(), name="contract.docx")
    request = send(client, users, contract, [{"name": "Vera Vendor", "email": "vera@vendor.example"}])
    token = token_of(request["signers"][0]["link"])
    assert client.get(f"/api/sign/{token}/document").headers["content-type"].startswith("text/html")

    from backend.app.database import connection
    with connection() as conn:
        path = conn.execute("SELECT file_path FROM signature_requests WHERE id=?", (request["id"],)).fetchone()[0]
    original = open(path, "rb").read()
    open(path, "ab").write(b"tampered")
    assert client.post(f"/api/sign/{token}", json={"signed_name": "Vera Vendor", "consent": True}).status_code == 409
    open(path, "wb").write(original)
    assert hashlib.sha256(original).hexdigest() == request["document_sha256"]
    assert client.post(f"/api/sign/{token}", json={"signed_name": "Vera Vendor", "consent": True}).status_code == 200
    pdf = client.get(f"/api/signatures/{request['id']}/signed-copy", headers=users["admin"])
    with pymupdf.open(stream=pdf.content, filetype="pdf") as document:
        assert document.page_count == 1


def test_bad_signature_image_and_unknown_token(client, users):
    contract = approved_contract(client, users)
    token = token_of(send(client, users, contract)["signers"][0]["link"])
    bad = client.post(f"/api/sign/{token}", json={"signed_name": "Vera Vendor", "consent": True,
                                                  "signature_image": "data:image/png;base64,bm90IGEgcG5n"})
    assert bad.status_code == 422
    assert client.get("/api/sign/not-a-real-token").status_code == 404


@pytest.fixture(autouse=True)
def _public_url(monkeypatch):
    monkeypatch.setenv("PUBLIC_APP_URL", "https://clause.example")
