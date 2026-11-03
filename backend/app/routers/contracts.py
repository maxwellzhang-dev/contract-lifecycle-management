import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

from docx import Document
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ..auth import current_user, require
from ..database import add_audit, connection, rows_to_dicts, tag_match_sql, utc_now
from ..services.extraction import FIELDS, extract_contract
from ..storage import save_upload, stored_file
from ..workflow import (
    CONTRACT_STATUSES, allowed_transitions, close_approval, contract_recipients, notify,
    pending_approval,
)

router = APIRouter(prefix="/api")


class ContractUpdate(BaseModel):
    title: str | None = None
    reference_number: str | None = None
    vendor_name: str | None = None
    project_name: str | None = None
    phase: str | None = None
    status: str | None = None
    project_start_date: str | None = None
    project_end_date: str | None = None
    renewal_date: str | None = None
    currency: str | None = None
    one_time_amount: float | None = None
    optional_amount: float | None = None
    vendor_contact: str | None = None
    internal_contact: str | None = None
    deliverables: str | None = None
    payment_terms: str | None = None
    notes: str | None = None
    department: str | None = None
    owner: str | None = None
    renewal_decision: str | None = None
    renewal_owner: str | None = None
    renewal_notice_deadline: str | None = None
    renewal_action: str | None = None
    renewal_comments: str | None = None
    tags: list[str] | None = None


MAX_TAGS = 10
MAX_TAG_LENGTH = 30


def normalise_tags(tags: list[str] | None) -> str:
    cleaned = []
    for tag in tags or []:
        tag = " ".join(str(tag).split())
        if not tag:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            raise HTTPException(422, f"Tags are limited to {MAX_TAG_LENGTH} characters.")
        if tag.lower() not in {t.lower() for t in cleaned}:
            cleaned.append(tag)
    if len(cleaned) > MAX_TAGS:
        raise HTTPException(422, f"A contract can have at most {MAX_TAGS} tags.")
    return json.dumps(sorted(cleaned, key=str.lower))


# Fields that can still change while an approval is pending; everything else is what the
# approvers are signing off on, so it is locked until the request is withdrawn or decided.
EDITABLE_DURING_APPROVAL = {"notes", "renewal_comments", "status", "tags"}
DATE_FIELDS = {"project_start_date", "project_end_date", "renewal_date", "renewal_notice_deadline"}


def with_workflow(conn, rows) -> list[dict]:
    """Attach the pending approval id and the statuses a user may move each contract to."""
    items = rows_to_dicts(rows)
    pending = {
        row["contract_id"]: row["id"]
        for row in conn.execute("SELECT id, contract_id FROM approval_requests WHERE status='Pending'")
    }
    for item, row in zip(items, rows):
        item["pending_approval_id"] = pending.get(item["id"])
        item["allowed_transitions"] = allowed_transitions(row, item["id"] in pending)
        item.pop("file_path", None)  # server paths are not the client's business
    return items


def load_contract(conn, contract_id: int):
    row = conn.execute("SELECT * FROM contracts WHERE id=?", (contract_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Contract not found")
    return row


def contract_response(conn, contract_id: int) -> dict:
    return with_workflow(conn, [load_contract(conn, contract_id)])[0]


@router.get("/dashboard")
def dashboard(months: int = Query(12), _: dict = Depends(current_user)):
    if months not in {1, 3, 6, 12}:
        raise HTTPException(422, "months must be one of 1, 3, 6 or 12")
    today = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=30)).isoformat()
    period_start = (date.today() - timedelta(days={1: 31, 3: 92, 6: 183, 12: 366}[months])).isoformat()
    in_period = "COALESCE(project_start_date, substr(created_at,1,10)) BETWEEN ? AND ?"
    with connection() as conn:
        totals = conn.execute(
            f"""SELECT COUNT(*) total,
                COALESCE(SUM(CASE WHEN status='In Progress' THEN 1 ELSE 0 END),0) active,
                COALESCE(SUM(CASE WHEN status='Under Review' THEN 1 ELSE 0 END),0) under_review,
                COALESCE(SUM(CASE WHEN status='Completed' THEN 1 ELSE 0 END),0) completed,
                COALESCE(SUM(CASE WHEN status='Quoting' THEN 1 ELSE 0 END),0) draft,
                COALESCE(SUM(CASE WHEN project_end_date < ? THEN 1 ELSE 0 END),0) expired,
                COALESCE(SUM(CASE WHEN validation_status!='Validated' THEN 1 ELSE 0 END),0) awaiting_validation,
                COALESCE(SUM(CASE WHEN missing_fields!='[]' THEN 1 ELSE 0 END),0) missing_information
                FROM contracts WHERE {in_period}""",
            (today, period_start, today),
        ).fetchone()
        expiring = conn.execute(
            f"SELECT COUNT(*) FROM contracts WHERE {in_period} AND project_end_date BETWEEN ? AND ?",
            (period_start, today, today, cutoff),
        ).fetchone()[0]
        values = rows_to_dicts(conn.execute(
            f"""SELECT currency, ROUND(SUM(COALESCE(one_time_amount,0)+COALESCE(optional_amount,0)),2) value
               FROM contracts WHERE {in_period} GROUP BY currency ORDER BY value DESC""",
            (period_start, today),
        ).fetchall())
        total_values = rows_to_dicts(conn.execute(
            """SELECT currency, ROUND(SUM(COALESCE(one_time_amount,0)+COALESCE(optional_amount,0)),2) value
               FROM contracts WHERE currency IS NOT NULL GROUP BY currency ORDER BY value DESC""",
        ).fetchall())
        statuses = rows_to_dicts(conn.execute(
            f"SELECT status, COUNT(*) count FROM contracts WHERE {in_period} GROUP BY status ORDER BY count DESC",
            (period_start, today),
        ).fetchall())
        pending_approvals = conn.execute("SELECT COUNT(*) FROM approval_requests WHERE status='Pending'").fetchone()[0]
        obligations = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN o.due_date < ? THEN 1 ELSE 0 END),0) overdue,
                      COALESCE(SUM(CASE WHEN o.due_date BETWEEN ? AND ? THEN 1 ELSE 0 END),0) due_30
               FROM obligations o JOIN contracts c ON c.id=o.contract_id
               WHERE o.status='Open' AND c.status NOT IN ('Completed','Cancelled')""",
            (today, today, cutoff),
        ).fetchone()
    return {
        **dict(totals), "expiring_30_days": expiring, "value_by_currency": values,
        "total_value_by_currency": total_values, "status_distribution": statuses,
        "pending_approvals": pending_approvals, "obligations_overdue": obligations["overdue"],
        "obligations_due_30_days": obligations["due_30"],
    }


TOTAL_VALUE = "(COALESCE(one_time_amount,0)+COALESCE(optional_amount,0))"


def query_contracts(search="", status="", vendor="", department="", limit=100, owner="", tag="",
                    currency="", min_value=None, max_value=None, end_from="", end_to=""):
    clauses, params = [], []
    if search:
        clauses.append("(title LIKE ? OR vendor_name LIKE ? OR project_name LIKE ? OR reference_number LIKE ? OR owner LIKE ? OR tags LIKE ?)")
        params.extend([f"%{search}%"] * 6)
    for field, value in (("status", status), ("vendor_name", vendor), ("department", department),
                         ("owner", owner), ("currency", currency)):
        if value:
            clauses.append(f"{field} = ?")
            params.append(value)
    if tag:
        clauses.append(tag_match_sql())
        params.append(tag)
    if min_value is not None:
        clauses.append(f"{TOTAL_VALUE} >= ?")
        params.append(min_value)
    if max_value is not None:
        clauses.append(f"{TOTAL_VALUE} <= ?")
        params.append(max_value)
    if end_from:
        clauses.append("project_end_date >= ?")
        params.append(end_from)
    if end_to:
        clauses.append("project_end_date <= ?")
        params.append(end_to)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connection() as conn:
        rows = conn.execute(f"SELECT * FROM contracts {where} ORDER BY updated_at DESC LIMIT ?", (*params, limit)).fetchall()
        return with_workflow(conn, rows)


class ContractFilters:
    """Query parameters shared by the list and export endpoints."""

    def __init__(
        self, search: str = "", status: str = "", vendor: str = "", department: str = "", owner: str = "",
        tag: str = "", currency: str = "", min_value: float | None = None, max_value: float | None = None,
        end_from: str = "", end_to: str = "",
    ):
        self.values = dict(search=search, status=status, vendor=vendor, department=department, owner=owner, tag=tag,
                           currency=currency, min_value=min_value, max_value=max_value, end_from=end_from, end_to=end_to)


@router.get("/contracts")
def list_contracts(
    filters: ContractFilters = Depends(), limit: int = Query(100, ge=1, le=1000), _: dict = Depends(current_user),
):
    return query_contracts(limit=limit, **filters.values)


@router.get("/contracts/{contract_id}")
def get_contract(contract_id: int, _: dict = Depends(current_user)):
    with connection() as conn:
        return contract_response(conn, contract_id)


@router.get("/contracts/{contract_id}/file")
def get_contract_file(contract_id: int, _: dict = Depends(current_user)):
    with connection() as conn:
        row = load_contract(conn, contract_id)
    path = stored_file(row["file_path"])
    return FileResponse(path, media_type=row["file_type"], filename=row["file_name"], content_disposition_type="inline")


def docx_preview(path: Path, title: str) -> StreamingResponse:
    document = Document(path)
    paragraphs = "".join(f"<p>{escape(p.text)}</p>" for p in document.paragraphs if p.text.strip())
    tables = "".join(
        "<table>" + "".join(
            "<tr>" + "".join(f"<td>{escape(cell.text)}</td>" for cell in row.cells) + "</tr>"
            for row in table.rows
        ) + "</table>"
        for table in document.tables
    )
    return StreamingResponse(
        iter([f"""<!doctype html><html><head><meta charset='utf-8'><title>{escape(title)}</title>
        <style>body{{font-family:Arial,sans-serif;color:#172033;line-height:1.55;margin:36px;max-width:900px}}p{{margin:0 0 14px}}table{{border-collapse:collapse;width:100%;margin:20px 0}}td{{border:1px solid #dfe2ea;padding:8px;vertical-align:top}}</style>
        </head><body>{paragraphs or '<p>No previewable text was found.</p>'}{tables}</body></html>"""]),
        media_type="text/html",
    )


@router.get("/contracts/{contract_id}/preview")
def preview_contract(contract_id: int, _: dict = Depends(current_user)):
    with connection() as conn:
        row = load_contract(conn, contract_id)
    path = stored_file(row["file_path"])
    if path.suffix.lower() != ".docx":
        raise HTTPException(404, "DOCX preview not available")
    return docx_preview(path, row["file_name"] or "DOCX preview")


@router.delete("/contracts/{contract_id}")
def delete_contract(contract_id: int, user: dict = Depends(require("contract.delete"))):
    with connection() as conn:
        contract = load_contract(conn, contract_id)
        conn.execute("UPDATE audit_log SET contract_id=NULL WHERE contract_id=?", (contract_id,))
        conn.execute("DELETE FROM contracts WHERE id=?", (contract_id,))
        reference = contract["reference_number"] or f"Contract #{contract_id}"
        add_audit(
            conn, "Contract deleted", user["name"],
            details=f"Deleted {reference}: {contract['title']}. Original files retained locally: {contract['file_name'] or 'none'}",
        )
    return {"deleted": True, "id": contract_id}


@router.post("/contracts/upload", status_code=201)
def upload_contract(
    file: UploadFile = File(...),
    related_vendor: str = Form(""),
    related_project: str = Form(""),
    user: dict = Depends(require("contract.create")),
):
    destination = save_upload(file)
    extracted = extract_contract(destination)
    now = utc_now()
    extracted["title"] = extracted.get("title") or Path(file.filename or "Untitled contract").stem
    # New uploads always start at Quoting: Under Review and later are reached through approvals.
    extracted["status"] = "Quoting"
    extracted["vendor_name"] = extracted.get("vendor_name") or related_vendor or None
    extracted["project_name"] = extracted.get("project_name") or related_project or None
    columns = FIELDS + ["extraction_confidence", "missing_fields", "low_confidence_fields", "field_confidences", "extraction_note"]
    values = []
    for column in columns:
        value = extracted.get(column)
        if column in {"missing_fields", "low_confidence_fields", "field_confidences"}:
            value = json.dumps(value or ([] if column != "field_confidences" else {}))
        values.append(value)
    size = destination.stat().st_size
    with connection() as conn:
        cursor = conn.execute(
            f"""INSERT INTO contracts ({','.join(columns)}, file_name, file_type, file_path, file_size,
                document_version, uploaded_by, upload_date, validation_status, created_at, updated_at)
                VALUES ({','.join(['?'] * len(columns))},?,?,?,?,?,?,?,?,?,?)""",
            (*values, file.filename, file.content_type, str(destination), size,
             1, user["name"], now, "Pending", now, now),
        )
        contract_id = cursor.lastrowid
        conn.execute(
            """INSERT INTO contract_versions (contract_id, version, file_name, file_type, file_path, file_size, uploaded_by, uploaded_at, note)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (contract_id, 1, file.filename, file.content_type, str(destination), size, user["name"], now, "Original upload"),
        )
        add_audit(conn, "Contract uploaded", user["name"], contract_id, f"Uploaded {file.filename}")
        add_audit(conn, "AI extraction completed", "Azure OpenAI", contract_id, extracted.get("extraction_note"))
        return contract_response(conn, contract_id)


def check_dates(values: dict):
    for key in DATE_FIELDS & values.keys():
        if values[key]:
            try:
                date.fromisoformat(values[key])
            except ValueError:
                raise HTTPException(422, f"{key} must be a date in YYYY-MM-DD format")
    start = values.get("project_start_date")
    end = values.get("project_end_date")
    if start and end and end < start:
        raise HTTPException(422, "The end date cannot be before the start date.")


@router.patch("/contracts/{contract_id}")
def update_contract(contract_id: int, update: ContractUpdate, user: dict = Depends(require("contract.edit"))):
    changes = {
        key: (value.strip() or None) if isinstance(value, str) else value
        for key, value in update.model_dump(exclude_unset=True).items()
    }
    if "tags" in changes:
        changes["tags"] = normalise_tags(changes["tags"])
    with connection() as conn:
        old = load_contract(conn, contract_id)
        # Ignore fields the client re-sent unchanged so a full-form save is not a status change.
        changes = {key: value for key, value in changes.items() if old[key] != value}
        if not changes:
            return contract_response(conn, contract_id)
        if not changes.get("title", old["title"]):
            raise HTTPException(422, "A contract title is required.")
        check_dates({
            "project_start_date": old["project_start_date"], "project_end_date": old["project_end_date"],
            **changes,
        })
        pending = pending_approval(conn, contract_id)
        if pending and set(changes) - EDITABLE_DURING_APPROVAL:
            raise HTTPException(409, "This contract is awaiting approval. Withdraw the approval request before editing its terms.")
        extra = {}
        if "status" in changes:
            target = changes["status"]
            if target not in CONTRACT_STATUSES:
                raise HTTPException(422, f"status must be one of: {', '.join(CONTRACT_STATUSES)}")
            allowed = allowed_transitions(old, bool(pending))
            if target not in allowed:
                hint = {
                    "Under Review": "Submit the contract for approval instead.",
                    "Cleared to Start": "Contracts are cleared to start when their approval completes.",
                }.get(target, f"Allowed next statuses: {', '.join(allowed) or 'none'}.")
                raise HTTPException(409, f"Cannot move from {old['status']} to {target}. {hint}")
            if target == "On Hold":
                extra["status_before_hold"] = old["status"]
            if pending and target in {"On Hold", "Cancelled"}:
                close_approval(conn, pending["id"], "Cancelled")
                add_audit(conn, "Approval cancelled", user["name"], contract_id, f"Contract moved to {target}")
            if target in {"On Hold", "Cancelled"}:
                voided = conn.execute(
                    "UPDATE signature_requests SET status='Voided', completed_at=?, void_reason=? WHERE contract_id=? AND status='Sent'",
                    (utc_now(), f"Contract moved to {target}", contract_id),
                ).rowcount
                if voided:
                    conn.execute(
                        "UPDATE signature_signers SET token_hash=NULL WHERE request_id IN (SELECT id FROM signature_requests WHERE contract_id=?)",
                        (contract_id,))
                    add_audit(conn, "Signature request voided", user["name"], contract_id, f"Contract moved to {target}")
        missing_fields = json.loads(old["missing_fields"] or "[]")
        low_confidence_fields = json.loads(old["low_confidence_fields"] or "[]")
        field_confidences = json.loads(old["field_confidences"] or "{}")
        for key, value in changes.items():
            if key in FIELDS:
                if value not in (None, ""):
                    missing_fields = [field for field in missing_fields if field != key]
                    low_confidence_fields = [field for field in low_confidence_fields if field != key]
                    field_confidences[key] = 1.0
                elif key not in missing_fields:
                    missing_fields.append(key)
        assignments = {**changes, **extra}
        conn.execute(
            f"UPDATE contracts SET {', '.join(f'{key}=?' for key in assignments)}, missing_fields=?, low_confidence_fields=?, field_confidences=?, updated_at=? WHERE id=?",
            (*assignments.values(), json.dumps(missing_fields), json.dumps(low_confidence_fields), json.dumps(field_confidences), utc_now(), contract_id),
        )
        details = "; ".join(f"{key}: {old[key]!r} → {value!r}" for key, value in changes.items())
        add_audit(conn, "Status changed" if list(changes) == ["status"] else "Contract fields updated", user["name"], contract_id, details)
        if "status" in changes:
            notify(
                conn, [uid for uid in contract_recipients(conn, old["owner"]) if uid != user["id"]],
                "status", f"{old['title']} is now {changes['status']}",
                f"Changed by {user['name']} (was {old['status']})", contract_id,
            )
        return contract_response(conn, contract_id)


@router.post("/contracts/{contract_id}/validate")
def validate_contract(contract_id: int, user: dict = Depends(require("contract.validate"))):
    now = utc_now()
    with connection() as conn:
        load_contract(conn, contract_id)
        conn.execute(
            "UPDATE contracts SET validation_status='Validated', reviewed_by=?, validated_at=?, updated_at=? WHERE id=?",
            (user["name"], now, now, contract_id),
        )
        add_audit(conn, "Contract validated", user["name"], contract_id, "Extraction reviewed and confirmed")
        return contract_response(conn, contract_id)


# --- document versions ---------------------------------------------------------------

@router.get("/contracts/{contract_id}/versions")
def list_versions(contract_id: int, _: dict = Depends(current_user)):
    with connection() as conn:
        load_contract(conn, contract_id)
        rows = conn.execute(
            """SELECT id, contract_id, version, file_name, file_type, file_size, uploaded_by, uploaded_at, note
               FROM contract_versions WHERE contract_id=? ORDER BY version DESC""",
            (contract_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@router.post("/contracts/{contract_id}/versions", status_code=201)
def upload_version(
    contract_id: int,
    file: UploadFile = File(...),
    note: str = Form(""),
    user: dict = Depends(require("contract.edit")),
):
    with connection() as conn:
        contract = load_contract(conn, contract_id)
        if pending_approval(conn, contract_id):
            raise HTTPException(409, "This contract is awaiting approval. Withdraw the request before uploading a new version.")
        if conn.execute("SELECT 1 FROM signature_requests WHERE contract_id=? AND status='Sent'", (contract_id,)).fetchone():
            raise HTTPException(409, "This contract is out for signature. Void the signature request before uploading a new version.")
        if contract["status"] in {"Completed", "Cancelled"}:
            raise HTTPException(409, f"{contract['status']} contracts cannot receive new document versions.")
    destination = save_upload(file)
    now = utc_now()
    size = destination.stat().st_size
    with connection() as conn:
        latest = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM contract_versions WHERE contract_id=?", (contract_id,)
        ).fetchone()[0]
        version = max(latest, contract["document_version"] if contract["file_path"] else 0) + 1
        conn.execute(
            """INSERT INTO contract_versions (contract_id, version, file_name, file_type, file_path, file_size, uploaded_by, uploaded_at, note)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (contract_id, version, file.filename, file.content_type, str(destination), size, user["name"], now, note.strip() or None),
        )
        # The record now describes a new document, so it needs a fresh human validation.
        conn.execute(
            """UPDATE contracts SET file_name=?, file_type=?, file_path=?, file_size=?, document_version=?,
               uploaded_by=?, upload_date=?, validation_status='Pending', reviewed_by=NULL, validated_at=NULL, updated_at=?
               WHERE id=?""",
            (file.filename, file.content_type, str(destination), size, version, user["name"], now, now, contract_id),
        )
        add_audit(conn, "New document version", user["name"], contract_id,
                  f"Version {version}: {file.filename}" + (f" — {note.strip()}" if note.strip() else ""))
        return contract_response(conn, contract_id)


def load_version(contract_id: int, version: int):
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM contract_versions WHERE contract_id=? AND version=?", (contract_id, version)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Version not found")
    return row


@router.get("/contracts/{contract_id}/versions/{version}/file")
def version_file(contract_id: int, version: int, _: dict = Depends(current_user)):
    row = load_version(contract_id, version)
    path = stored_file(row["file_path"])
    return FileResponse(path, media_type=row["file_type"], filename=row["file_name"], content_disposition_type="inline")


@router.get("/contracts/{contract_id}/versions/{version}/preview")
def version_preview(contract_id: int, version: int, _: dict = Depends(current_user)):
    row = load_version(contract_id, version)
    path = stored_file(row["file_path"])
    if path.suffix.lower() != ".docx":
        raise HTTPException(404, "DOCX preview not available")
    return docx_preview(path, f"{row['file_name']} (v{version})")


# --- audit and export ----------------------------------------------------------------

@router.get("/audit")
def audit_log(
    limit: int = Query(100, ge=1, le=500),
    contract_id: int | None = None,
    _: dict = Depends(require("audit.read")),
):
    where, params = ("WHERE audit_log.contract_id=?", (contract_id,)) if contract_id else ("", ())
    with connection() as conn:
        rows = conn.execute(
            f"""SELECT audit_log.*, contracts.title contract_title, contracts.vendor_name
               FROM audit_log LEFT JOIN contracts ON contracts.id=audit_log.contract_id
               {where} ORDER BY audit_log.created_at DESC, audit_log.id DESC LIMIT ?""",
            (*params, limit),
        ).fetchall()
    return rows_to_dicts(rows)


def csv_safe(value):
    # Stop spreadsheet apps from executing cell values that look like formulas.
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + value
    return value


@router.get("/export/contracts.csv")
def export_contracts(filters: ContractFilters = Depends(), _: dict = Depends(current_user)):
    contracts = query_contracts(limit=5000, **filters.values)
    stream = io.StringIO()
    fields = ["reference_number", "title", "vendor_name", "project_name", "status", "owner", "department", "project_start_date", "project_end_date", "renewal_date", "currency", "one_time_amount", "optional_amount", "validation_status", "tags"]
    for item in contracts:
        item["tags"] = ", ".join(item.get("tags") or [])
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows({key: csv_safe(item.get(key)) for key in fields} for item in contracts)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return StreamingResponse(
        iter([stream.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="contracts-{stamp}.csv"'},
    )
