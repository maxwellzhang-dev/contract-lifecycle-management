import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv("DATABASE_PATH", PROJECT_ROOT / "backend/data/contracts.db"))
if not DB_PATH.is_absolute():
    DB_PATH = PROJECT_ROOT / DB_PATH


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def rows_to_dicts(rows):
    result = []
    for row in rows:
        item = dict(row)
        for key in ("missing_fields", "low_confidence_fields", "field_confidences"):
            if key in item and isinstance(item[key], str):
                try:
                    item[key] = json.loads(item[key])
                except json.JSONDecodeError:
                    item[key] = [] if key != "field_confidences" else {}
        result.append(item)
    return result


def init_db():
    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS contracts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                reference_number TEXT,
                vendor_name TEXT,
                project_name TEXT,
                phase TEXT,
                status TEXT NOT NULL DEFAULT 'Draft',
                project_start_date TEXT,
                project_end_date TEXT,
                renewal_date TEXT,
                currency TEXT,
                one_time_amount REAL,
                optional_amount REAL,
                vendor_contact TEXT,
                internal_contact TEXT,
                deliverables TEXT,
                payment_terms TEXT,
                notes TEXT,
                department TEXT,
                owner TEXT,
                validation_status TEXT NOT NULL DEFAULT 'Pending',
                reviewed_by TEXT,
                validated_at TEXT,
                renewal_decision TEXT,
                renewal_owner TEXT,
                renewal_notice_deadline TEXT,
                renewal_action TEXT,
                renewal_comments TEXT,
                renewal_completion_date TEXT,
                replacement_contract_reference TEXT,
                extraction_confidence REAL,
                missing_fields TEXT NOT NULL DEFAULT '[]',
                low_confidence_fields TEXT NOT NULL DEFAULT '[]',
                field_confidences TEXT NOT NULL DEFAULT '{}',
                extraction_note TEXT,
                file_name TEXT,
                file_type TEXT,
                file_path TEXT,
                file_size INTEGER,
                document_version INTEGER NOT NULL DEFAULT 1,
                uploaded_by TEXT,
                upload_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                contract_id INTEGER,
                actor TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(contract_id) REFERENCES contracts(id)
            );
            CREATE INDEX IF NOT EXISTS idx_contract_status ON contracts(status);
            CREATE INDEX IF NOT EXISTS idx_contract_vendor ON contracts(vendor_name);
            CREATE INDEX IF NOT EXISTS idx_contract_end_date ON contracts(project_end_date);
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
        if count == 0:
            seed_contracts(conn)
        else:
            conn.execute("UPDATE contracts SET status='Quoting' WHERE status='Draft'")
            conn.execute("UPDATE contracts SET status='In Progress' WHERE status='Active'")


def seed_contracts(conn):
    today = date.today()
    seeds = [
        ("Creative Cloud Enterprise Agreement", "CL-2026-001", "Adobe", "Creative Cloud Enterprise", "Operations", "In Progress", today - timedelta(days=310), today + timedelta(days=46), "SGD", 184000, "Sarah Lim", "Technology", "Validated", 0.96),
        ("CRM Platform Renewal", "CL-2026-002", "Salesforce", "CRM Platform Renewal", "Renewal", "Under Review", today - timedelta(days=340), today + timedelta(days=26), "USD", 96500, "Daniel Tan", "Sales", "Pending", 0.72),
        ("Media Delivery Services", "CL-2026-003", "Akamai", "Media Delivery Services", "Operations", "In Progress", today - timedelta(days=330), today + timedelta(days=18), "USD", 210000, "Wei Ming", "Engineering", "Validated", 0.91),
        ("Finance Transformation SOW", "CL-2026-004", "Deloitte", "Finance Transformation", "Planning", "Quoting", None, None, "SGD", 325000, "Priya Nair", "Finance", "Pending", 0.64),
        ("Enterprise Support Services", "CL-2025-031", "Microsoft", "Enterprise Support", "Operations", "In Progress", today - timedelta(days=250), today + timedelta(days=112), "SGD", 287000, "Marcus Lee", "Technology", "Validated", 0.98),
        ("Broadcast Monitoring Platform", "CL-2025-028", "Nielsen", "Audience Measurement", "Completed", "Completed", today - timedelta(days=730), today - timedelta(days=14), "SGD", 156000, "Rachel Wong", "Insights", "Validated", 0.94),
    ]
    now = utc_now()
    for seed in seeds:
        title, ref, vendor, project, phase, status, start, end, currency, amount, owner, department, validation, confidence = seed
        missing = ["renewal_date"] if not end or status == "Quoting" else []
        low = ["payment_terms"] if confidence < 0.8 else []
        conn.execute(
            """INSERT INTO contracts (
                title, reference_number, vendor_name, project_name, phase, status,
                project_start_date, project_end_date, renewal_date, currency,
                one_time_amount, owner, internal_contact, department,
                validation_status, reviewed_by, validated_at, extraction_confidence,
                missing_fields, low_confidence_fields, field_confidences,
                extraction_note, document_version, uploaded_by, upload_date,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                title, ref, vendor, project, phase, status,
                start.isoformat() if start else None, end.isoformat() if end else None,
                (end - timedelta(days=30)).isoformat() if end else None,
                currency, amount, owner, owner, department, validation,
                owner if validation == "Validated" else None,
                now if validation == "Validated" else None, confidence,
                json.dumps(missing), json.dumps(low), json.dumps({"vendor_name": confidence, "one_time_amount": confidence}),
                "Seeded demonstration record", 1, "Demo User", now, now, now,
            ),
        )
    conn.execute(
        "INSERT INTO audit_log (action, actor, details, created_at) VALUES (?,?,?,?)",
        ("Prototype initialized", "System", "Created local demonstration records", now),
    )


def add_audit(conn, action: str, actor: str, contract_id=None, details=None):
    conn.execute(
        "INSERT INTO audit_log (action, contract_id, actor, details, created_at) VALUES (?,?,?,?,?)",
        (action, contract_id, actor, details, utc_now()),
    )
