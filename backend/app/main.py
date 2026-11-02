import csv
from html import escape
import io
import json
import os
import shutil
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from docx import Document

from .database import add_audit, connection, init_db, rows_to_dicts, utc_now
from .services.extraction import FIELDS, extract_contract

def cors_origins() -> list[str]:
    configured = os.getenv("CORS_ORIGINS", "")
    if configured:
        return [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]
    return ["http://localhost:3000", "http://127.0.0.1:3000"]

CONTRACT_STATUSES = (
    "Quoting", "Under Review", "Cleared to Start", "In Progress",
    "Completed", "On Hold", "Cancelled",
)

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", PROJECT_ROOT / "backend/data/uploads"))
if not UPLOAD_DIR.is_absolute():
    UPLOAD_DIR = PROJECT_ROOT / UPLOAD_DIR
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Clause Contract Lifecycle API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.on_event("startup")
def startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok", "database": "sqlite", "model": os.getenv("AZURE_OPENAI_DEPLOYMENT", "not configured")}


@app.get("/api/dashboard")
def dashboard(months: int = Query(12)):
    if months not in {1, 3, 6, 12}:
        raise HTTPException(422, "months must be one of 1, 3, 6 or 12")
    today = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=30)).isoformat()
    period_start = (date.today() - timedelta(days={1: 31, 3: 92, 6: 183, 12: 366}[months])).isoformat()
    with connection() as conn:
        totals = conn.execute(
            """SELECT COUNT(*) total,
                COALESCE(SUM(CASE WHEN status='In Progress' THEN 1 ELSE 0 END),0) active,
                COALESCE(SUM(CASE WHEN status='Under Review' THEN 1 ELSE 0 END),0) under_review,
                COALESCE(SUM(CASE WHEN status='Completed' THEN 1 ELSE 0 END),0) completed,
                COALESCE(SUM(CASE WHEN status='Quoting' THEN 1 ELSE 0 END),0) draft,
                COALESCE(SUM(CASE WHEN project_end_date < ? THEN 1 ELSE 0 END),0) expired,
                COALESCE(SUM(CASE WHEN validation_status!='Validated' THEN 1 ELSE 0 END),0) awaiting_validation,
                COALESCE(SUM(CASE WHEN missing_fields!='[]' THEN 1 ELSE 0 END),0) missing_information
                FROM contracts
                WHERE COALESCE(project_start_date, substr(created_at,1,10)) BETWEEN ? AND ?""",
            (today, period_start, today),
        ).fetchone()
        expiring = conn.execute(
            """SELECT COUNT(*) count FROM contracts
               WHERE COALESCE(project_start_date, substr(created_at,1,10)) BETWEEN ? AND ?
               AND project_end_date BETWEEN ? AND ?""",
            (period_start, today, today, cutoff),
        ).fetchone()[0]
        values = rows_to_dicts(conn.execute(
            """SELECT currency, ROUND(SUM(COALESCE(one_time_amount,0)+COALESCE(optional_amount,0)),2) value
               FROM contracts WHERE COALESCE(project_start_date, substr(created_at,1,10)) BETWEEN ? AND ?
               GROUP BY currency ORDER BY value DESC""",
            (period_start, today),
        ).fetchall())
        total_values = rows_to_dicts(conn.execute(
            """SELECT currency, ROUND(SUM(COALESCE(one_time_amount,0)+COALESCE(optional_amount,0)),2) value
               FROM contracts WHERE currency IS NOT NULL
               GROUP BY currency ORDER BY value DESC""",
        ).fetchall())
        statuses = rows_to_dicts(conn.execute(
            """SELECT status, COUNT(*) count FROM contracts
               WHERE COALESCE(project_start_date, substr(created_at,1,10)) BETWEEN ? AND ?
               GROUP BY status ORDER BY count DESC""",
            (period_start, today),
        ).fetchall())
    return {**dict(totals), "expiring_30_days": expiring, "value_by_currency": values, "total_value_by_currency": total_values, "status_distribution": statuses}


@app.get("/api/contracts")
def list_contracts(
    search: str = "",
    status: str = "",
    vendor: str = "",
    department: str = "",
    limit: int = Query(100, ge=1, le=500),
):
    clauses, params = [], []
    if search:
        clauses.append("(title LIKE ? OR vendor_name LIKE ? OR project_name LIKE ? OR reference_number LIKE ? OR owner LIKE ?)")
        params.extend([f"%{search}%"] * 5)
    for field, value in (("status", status), ("vendor_name", vendor), ("department", department)):
        if value:
            clauses.append(f"{field} = ?")
            params.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connection() as conn:
        rows = conn.execute(f"SELECT * FROM contracts {where} ORDER BY updated_at DESC LIMIT ?", (*params, limit)).fetchall()
    return rows_to_dicts(rows)


@app.get("/api/contracts/{contract_id}")
def get_contract(contract_id: int):
    with connection() as conn:
        row = conn.execute("SELECT * FROM contracts WHERE id=?", (contract_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Contract not found")
    return rows_to_dicts([row])[0]


@app.get("/api/contracts/{contract_id}/file")
def get_contract_file(contract_id: int):
    with connection() as conn:
        row = conn.execute("SELECT file_path, file_name, file_type FROM contracts WHERE id=?", (contract_id,)).fetchone()
    if not row or not row["file_path"]:
        raise HTTPException(404, "Contract file not found")
    path = Path(row["file_path"]).resolve()
    if UPLOAD_DIR.resolve() not in path.parents or not path.exists():
        raise HTTPException(404, "Contract file not found")
    return FileResponse(path, media_type=row["file_type"], filename=row["file_name"], content_disposition_type="inline")


@app.get("/api/contracts/{contract_id}/preview")
def preview_contract(contract_id: int):
    with connection() as conn:
        row = conn.execute("SELECT file_path, file_name FROM contracts WHERE id=?", (contract_id,)).fetchone()
    if not row or not row["file_path"]:
        raise HTTPException(404, "Contract file not found")
    path = Path(row["file_path"]).resolve()
    if UPLOAD_DIR.resolve() not in path.parents or not path.exists() or path.suffix.lower() != ".docx":
        raise HTTPException(404, "DOCX preview not available")

    document = Document(path)
    paragraphs = "".join(f"<p>{escape(paragraph.text)}</p>" for paragraph in document.paragraphs if paragraph.text.strip())
    tables = "".join(
        "<table>" + "".join(
            "<tr>" + "".join(f"<td>{escape(cell.text)}</td>" for cell in row.cells) + "</tr>"
            for row in table.rows
        ) + "</table>"
        for table in document.tables
    )
    return StreamingResponse(
        iter([f"""<!doctype html><html><head><meta charset='utf-8'><title>{escape(row['file_name'] or 'DOCX preview')}</title>
        <style>body{{font-family:Arial,sans-serif;color:#172033;line-height:1.55;margin:36px;max-width:900px}}p{{margin:0 0 14px}}table{{border-collapse:collapse;width:100%;margin:20px 0}}td{{border:1px solid #dfe2ea;padding:8px;vertical-align:top}}</style>
        </head><body>{paragraphs or '<p>No previewable text was found.</p>'}{tables}</body></html>"""]),
        media_type="text/html",
    )


@app.delete("/api/contracts/{contract_id}")
def delete_contract(contract_id: int, actor: str = "Maxwell Zhang"):
    with connection() as conn:
        contract = conn.execute(
            "SELECT title, reference_number, file_name FROM contracts WHERE id=?",
            (contract_id,),
        ).fetchone()
        if not contract:
            raise HTTPException(404, "Contract not found")
        conn.execute("UPDATE audit_log SET contract_id=NULL WHERE contract_id=?", (contract_id,))
        conn.execute("DELETE FROM contracts WHERE id=?", (contract_id,))
        reference = contract["reference_number"] or f"Contract #{contract_id}"
        add_audit(
            conn,
            "Contract deleted",
            actor,
            details=f"Deleted {reference}: {contract['title']}. Original file retained locally: {contract['file_name'] or 'none'}",
        )
    return {"deleted": True, "id": contract_id}


@app.post("/api/contracts/upload", status_code=201)
def upload_contract(
    file: UploadFile = File(...),
    uploaded_by: str = Form("Maxwell Zhang"),
    related_vendor: str = Form(""),
    related_project: str = Form(""),
):
    suffix = Path(file.filename or "contract").suffix.lower()
    if suffix not in {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(400, "Supported formats are PDF, DOCX, PNG, JPG and WEBP.")
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    if file_size > 25 * 1024 * 1024:
        raise HTTPException(413, "File exceeds the 25 MB prototype limit.")
    safe_name = f"{uuid.uuid4().hex}{suffix}"
    destination = UPLOAD_DIR / safe_name
    with destination.open("wb") as target:
        shutil.copyfileobj(file.file, target)
    extracted = extract_contract(destination)
    now = utc_now()
    title = extracted.get("title") or Path(file.filename or "Untitled contract").stem
    extracted["title"] = title
    extracted["status"] = extracted.get("status") if extracted.get("status") in CONTRACT_STATUSES else "Quoting"
    extracted["vendor_name"] = extracted.get("vendor_name") or related_vendor or None
    extracted["project_name"] = extracted.get("project_name") or related_project or None
    columns = FIELDS + ["extraction_confidence", "missing_fields", "low_confidence_fields", "field_confidences", "extraction_note"]
    values = []
    for column in columns:
        value = extracted.get(column)
        if column in {"missing_fields", "low_confidence_fields", "field_confidences"}:
            value = json.dumps(value or ([] if column != "field_confidences" else {}))
        values.append(value)
    with connection() as conn:
        cursor = conn.execute(
            f"""INSERT INTO contracts ({','.join(columns)}, file_name, file_type, file_path, file_size,
                document_version, uploaded_by, upload_date, validation_status, created_at, updated_at)
                VALUES ({','.join(['?'] * len(columns))},?,?,?,?,?,?,?,?,?,?)""",
            (*values, file.filename, file.content_type, str(destination), destination.stat().st_size,
             1, uploaded_by, now, "Pending", now, now),
        )
        contract_id = cursor.lastrowid
        add_audit(conn, "Contract uploaded", uploaded_by, contract_id, f"Uploaded {file.filename}")
        add_audit(conn, "AI extraction completed", "Azure OpenAI", contract_id, extracted.get("extraction_note"))
        row = conn.execute("SELECT * FROM contracts WHERE id=?", (contract_id,)).fetchone()
    return rows_to_dicts([row])[0]


@app.patch("/api/contracts/{contract_id}")
def update_contract(contract_id: int, update: ContractUpdate, actor: str = "Maxwell Zhang"):
    changes = update.model_dump(exclude_unset=True)
    if not changes:
        return get_contract(contract_id)
    allowed = set(ContractUpdate.model_fields)
    changes = {key: value for key, value in changes.items() if key in allowed}
    if "status" in changes and changes["status"] not in CONTRACT_STATUSES:
        raise HTTPException(422, f"status must be one of: {', '.join(CONTRACT_STATUSES)}")
    assignments = ", ".join(f"{key}=?" for key in changes)
    with connection() as conn:
        old = conn.execute("SELECT * FROM contracts WHERE id=?", (contract_id,)).fetchone()
        if not old:
            raise HTTPException(404, "Contract not found")
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
        conn.execute(
            f"UPDATE contracts SET {assignments}, missing_fields=?, low_confidence_fields=?, field_confidences=?, updated_at=? WHERE id=?",
            (*changes.values(), json.dumps(missing_fields), json.dumps(low_confidence_fields), json.dumps(field_confidences), utc_now(), contract_id),
        )
        details = "; ".join(f"{key}: {old[key]!r} → {value!r}" for key, value in changes.items())
        add_audit(conn, "Contract fields updated", actor, contract_id, details)
        row = conn.execute("SELECT * FROM contracts WHERE id=?", (contract_id,)).fetchone()
    return rows_to_dicts([row])[0]


@app.post("/api/contracts/{contract_id}/validate")
def validate_contract(contract_id: int, reviewer: str = "Maxwell Zhang"):
    now = utc_now()
    with connection() as conn:
        row = conn.execute("SELECT id FROM contracts WHERE id=?", (contract_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Contract not found")
        conn.execute(
            "UPDATE contracts SET validation_status='Validated', reviewed_by=?, validated_at=?, updated_at=? WHERE id=?",
            (reviewer, now, now, contract_id),
        )
        add_audit(conn, "Contract validated", reviewer, contract_id, "Extraction reviewed and confirmed")
        updated = conn.execute("SELECT * FROM contracts WHERE id=?", (contract_id,)).fetchone()
    return rows_to_dicts([updated])[0]


@app.get("/api/audit")
def audit_log(limit: int = Query(100, ge=1, le=500)):
    with connection() as conn:
        rows = conn.execute(
            """SELECT audit_log.*, contracts.title contract_title, contracts.vendor_name
               FROM audit_log LEFT JOIN contracts ON contracts.id=audit_log.contract_id
               ORDER BY audit_log.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return rows_to_dicts(rows)


@app.get("/api/export/contracts.csv")
def export_contracts(search: str = "", status: str = ""):
    contracts = list_contracts(search=search, status=status, limit=500)
    stream = io.StringIO()
    fields = ["reference_number", "title", "vendor_name", "project_name", "status", "owner", "department", "project_start_date", "project_end_date", "renewal_date", "currency", "one_time_amount", "validation_status"]
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(contracts)
    add_time = datetime.now(timezone.utc).strftime("%Y%m%d")
    return StreamingResponse(
        iter([stream.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="contracts-{add_time}.csv"'},
    )
