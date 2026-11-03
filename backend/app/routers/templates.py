"""Contract templates and creating contracts without an upload."""

import json
import re
import uuid
from datetime import date

from docx import Document
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user, require
from ..database import add_audit, connection, utc_now
from ..services import rag
from ..storage import upload_dir
from .contracts import ContractUpdate, check_dates, contract_response, normalise_tags

router = APIRouter(prefix="/api")

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PLACEHOLDERS = (
    "title", "reference_number", "vendor_name", "project_name", "project_start_date", "project_end_date",
    "currency", "one_time_amount", "optional_amount", "payment_terms", "deliverables", "department",
    "owner", "vendor_contact", "internal_contact", "today",
)
DEFAULT_FIELDS = ("department", "phase", "currency", "payment_terms", "deliverables")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
MISSING = "[to be confirmed]"


class TemplateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    body: str | None = None
    defaults: dict[str, str] | None = None
    active: bool | None = None


class ContractCreate(ContractUpdate):
    template_id: int | None = None


def check_template(values: dict):
    if "name" in values and not (values["name"] or "").strip():
        raise HTTPException(422, "A template needs a name.")
    if "body" in values:
        if not (values["body"] or "").strip():
            raise HTTPException(422, "A template needs some text.")
        unknown = sorted(set(PLACEHOLDER_RE.findall(values["body"])) - set(PLACEHOLDERS))
        if unknown:
            raise HTTPException(422, f"Unknown placeholders: {', '.join(unknown)}. Available: {', '.join(PLACEHOLDERS)}")
    if values.get("defaults"):
        bad = sorted(set(values["defaults"]) - set(DEFAULT_FIELDS))
        if bad:
            raise HTTPException(422, f"Defaults can only set: {', '.join(DEFAULT_FIELDS)}")


def template_out(row) -> dict:
    item = dict(row)
    item["defaults"] = json.loads(item["defaults"] or "{}")
    item["active"] = bool(item["active"])
    item["placeholders"] = sorted(set(PLACEHOLDER_RE.findall(item["body"])))
    return item


def format_value(key: str, value) -> str:
    if value in (None, ""):
        return MISSING
    if key in {"one_time_amount", "optional_amount"} and isinstance(value, (int, float)):
        return f"{value:,.2f}"
    return str(value)


def render(body: str, values: dict) -> str:
    values = {**values, "today": date.today().isoformat()}
    return PLACEHOLDER_RE.sub(lambda m: format_value(m.group(1), values.get(m.group(1))), body)


def write_docx(text: str, title: str):
    document = Document()
    document.add_heading(title, level=0)
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
        if lines and lines[0].startswith("# "):
            document.add_heading(lines[0][2:].strip(), level=1)
            lines = lines[1:]
        if lines:
            document.add_paragraph("\n".join(line.rstrip() for line in lines))
    path = upload_dir() / f"{uuid.uuid4().hex}.docx"
    document.save(path)
    return path


@router.get("/templates")
def list_templates(include_inactive: bool = False, user: dict = Depends(current_user)):
    show_all = include_inactive and "template.manage" in user["permissions"]
    with connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM contract_templates {'' if show_all else 'WHERE active=1'} ORDER BY name"
        ).fetchall()
    return [template_out(row) for row in rows]


@router.get("/templates/placeholders")
def placeholders(_: dict = Depends(current_user)):
    return {"placeholders": PLACEHOLDERS, "default_fields": DEFAULT_FIELDS}


@router.post("/templates", status_code=201)
def create_template(body: TemplateIn, user: dict = Depends(require("template.manage"))):
    values = body.model_dump()
    values.setdefault("name", None)
    check_template({"name": values["name"], "body": values["body"], "defaults": values["defaults"]})
    now = utc_now()
    with connection() as conn:
        cursor = conn.execute(
            """INSERT INTO contract_templates (name, description, body, defaults, active, created_by, created_at, updated_at)
               VALUES (?,?,?,?,1,?,?,?)""",
            (values["name"].strip(), (values["description"] or "").strip() or None, values["body"],
             json.dumps(values["defaults"] or {}), user["name"], now, now),
        )
        add_audit(conn, "Template created", user["name"], details=values["name"].strip())
        return template_out(conn.execute("SELECT * FROM contract_templates WHERE id=?", (cursor.lastrowid,)).fetchone())


@router.patch("/templates/{template_id}")
def update_template(template_id: int, body: TemplateIn, user: dict = Depends(require("template.manage"))):
    changes = body.model_dump(exclude_unset=True)
    check_template(changes)
    with connection() as conn:
        row = conn.execute("SELECT * FROM contract_templates WHERE id=?", (template_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Template not found")
        if "defaults" in changes:
            changes["defaults"] = json.dumps(changes["defaults"] or {})
        if "active" in changes:
            changes["active"] = int(bool(changes["active"]))
        if "name" in changes:
            changes["name"] = changes["name"].strip()
        if changes:
            conn.execute(
                f"UPDATE contract_templates SET {', '.join(f'{k}=?' for k in changes)}, updated_at=? WHERE id=?",
                (*changes.values(), utc_now(), template_id),
            )
            action = {0: "Template deactivated", 1: "Template reactivated"}.get(changes.get("active"), "Template updated")
            add_audit(conn, action, user["name"], details=row["name"])
        return template_out(conn.execute("SELECT * FROM contract_templates WHERE id=?", (template_id,)).fetchone())


@router.post("/contracts", status_code=201)
def create_contract(body: ContractCreate, user: dict = Depends(require("contract.create"))):
    values = {k: (v.strip() or None) if isinstance(v, str) else v
              for k, v in body.model_dump(exclude_unset=True).items()}
    template_id = values.pop("template_id", None)
    if not values.get("title"):
        raise HTTPException(422, "A contract title is required.")
    values.pop("status", None)  # new contracts always start in Quoting
    check_dates(values)
    if "tags" in values:
        values["tags"] = normalise_tags(values["tags"])
    template = None
    with connection() as conn:
        if template_id is not None:
            template = conn.execute("SELECT * FROM contract_templates WHERE id=? AND active=1", (template_id,)).fetchone()
            if not template:
                raise HTTPException(404, "Template not found")
            for key, value in json.loads(template["defaults"] or "{}").items():
                values.setdefault(key, value)
    now = utc_now()
    file_values = {}
    if template:
        path = write_docx(render(template["body"], values), values["title"])
        file_values = {"file_name": f"{values['title'][:80]}.docx", "file_type": DOCX_TYPE,
                       "file_path": str(path), "file_size": path.stat().st_size}
    note = f"Created from template “{template['name']}”" if template else "Created manually"
    record = {
        **values, **file_values, "status": "Quoting", "validation_status": "Validated", "reviewed_by": user["name"],
        "validated_at": now, "extraction_confidence": 1.0, "extraction_note": note, "document_version": 1,
        "uploaded_by": user["name"], "upload_date": now, "created_at": now, "updated_at": now,
    }
    with connection() as conn:
        cursor = conn.execute(
            f"INSERT INTO contracts ({', '.join(record)}) VALUES ({', '.join('?' * len(record))})",
            tuple(record.values()),
        )
        contract_id = cursor.lastrowid
        if file_values:
            conn.execute(
                """INSERT INTO contract_versions (contract_id, version, file_name, file_type, file_path, file_size, uploaded_by, uploaded_at, note)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (contract_id, 1, file_values["file_name"], DOCX_TYPE, file_values["file_path"], file_values["file_size"],
                 user["name"], now, note),
            )
        add_audit(conn, "Contract created", user["name"], contract_id, note)
        if file_values:
            rag.queue(conn, contract_id)
        return contract_response(conn, contract_id)


SEED_TEMPLATES = (
    ("Professional Services Agreement", "Statement of work for consulting or delivery services.",
     {"phase": "Planning", "payment_terms": "30% on signature, 70% on acceptance of deliverables. Invoices payable within 30 days."},
     """# Parties
This Professional Services Agreement ("Agreement") is made on {{today}} between the Client, for its {{department}} department, and {{vendor_name}} ("the Supplier").

# Scope of services
The Supplier will deliver the following for the project {{project_name}}:
{{deliverables}}

# Term
The services start on {{project_start_date}} and end on {{project_end_date}}, unless extended in writing by both parties.

# Fees and payment
The fixed fee is {{currency}} {{one_time_amount}}. Optional services, if ordered, are capped at {{currency}} {{optional_amount}}.
Payment terms: {{payment_terms}}

# Contacts
Client contact: {{internal_contact}}. Supplier contact: {{vendor_contact}}.

# Reference
Contract reference {{reference_number}}. Contract owner: {{owner}}."""),
    ("Software Subscription Order Form", "Annual SaaS subscription with optional add-ons.",
     {"phase": "Operations", "payment_terms": "Annual in advance. Invoices payable within 30 days."},
     """# Order details
Customer department: {{department}}
Provider: {{vendor_name}}
Service: {{project_name}}
Order reference: {{reference_number}}

# Subscription term
{{project_start_date}} to {{project_end_date}}. The subscription does not renew automatically; renewal needs a new order.

# Fees
Subscription fee: {{currency}} {{one_time_amount}}
Optional add-ons: {{currency}} {{optional_amount}}
Payment terms: {{payment_terms}}

# Included services
{{deliverables}}

# Contacts
Customer contact: {{internal_contact}} · Provider contact: {{vendor_contact}}"""),
)


def seed_templates(conn):
    if conn.execute("SELECT COUNT(*) FROM contract_templates").fetchone()[0]:
        return
    now = utc_now()
    for name, description, defaults, body in SEED_TEMPLATES:
        conn.execute(
            """INSERT INTO contract_templates (name, description, body, defaults, active, created_by, created_at, updated_at)
               VALUES (?,?,?,?,1,'System',?,?)""",
            (name, description, body, json.dumps(defaults), now, now),
        )
