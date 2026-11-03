import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def db_path() -> Path:
    # Read on every call so tests (and deployments) can point DATABASE_PATH elsewhere.
    path = Path(os.getenv("DATABASE_PATH", PROJECT_ROOT / "backend/data/contracts.db"))
    return path if path.is_absolute() else PROJECT_ROOT / path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


JSON_LIST_COLUMNS = ("missing_fields", "low_confidence_fields", "tags")
JSON_DICT_COLUMNS = ("field_confidences", "defaults")


def rows_to_dicts(rows):
    result = []
    for row in rows:
        item = dict(row)
        for key in JSON_LIST_COLUMNS + JSON_DICT_COLUMNS:
            if key in item and isinstance(item[key], str):
                try:
                    item[key] = json.loads(item[key])
                except json.JSONDecodeError:
                    item[key] = {} if key in JSON_DICT_COLUMNS else []
        result.append(item)
    return result


SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    reference_number TEXT,
    vendor_name TEXT,
    project_name TEXT,
    phase TEXT,
    status TEXT NOT NULL DEFAULT 'Quoting',
    status_before_hold TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
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
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approval_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'Pending',
    submitted_by INTEGER NOT NULL REFERENCES users(id),
    note TEXT,
    submitted_at TEXT NOT NULL,
    completed_at TEXT,
    ai_review_id INTEGER
);
CREATE TABLE IF NOT EXISTS approval_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES approval_requests(id) ON DELETE CASCADE,
    step_order INTEGER NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Waiting',
    decided_by INTEGER REFERENCES users(id),
    decided_at TEXT,
    comment TEXT
);
CREATE TABLE IF NOT EXISTS contract_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    file_name TEXT,
    file_type TEXT,
    file_path TEXT NOT NULL,
    file_size INTEGER,
    uploaded_by TEXT,
    uploaded_at TEXT NOT NULL,
    note TEXT,
    UNIQUE(contract_id, version)
);
CREATE TABLE IF NOT EXISTS obligations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'Deliverable',
    due_date TEXT,
    amount REAL,
    currency TEXT,
    owner TEXT,
    status TEXT NOT NULL DEFAULT 'Open',
    notes TEXT,
    completed_at TEXT,
    completed_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    contract_id INTEGER REFERENCES contracts(id) ON DELETE CASCADE,
    dedupe_key TEXT,
    created_at TEXT NOT NULL,
    read_at TEXT,
    UNIQUE(user_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_contract_status ON contracts(status);
CREATE INDEX IF NOT EXISTS idx_contract_vendor ON contracts(vendor_name);
CREATE INDEX IF NOT EXISTS idx_contract_end_date ON contracts(project_end_date);
CREATE INDEX IF NOT EXISTS idx_approval_contract ON approval_requests(contract_id);
CREATE INDEX IF NOT EXISTS idx_obligation_contract ON obligations(contract_id);
CREATE INDEX IF NOT EXISTS idx_obligation_due ON obligations(due_date);
CREATE TABLE IF NOT EXISTS contract_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    edited_at TEXT
);
CREATE TABLE IF NOT EXISTS contract_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    body TEXT NOT NULL,
    defaults TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notification_user ON notifications(user_id, read_at);
CREATE INDEX IF NOT EXISTS idx_comment_contract ON contract_comments(contract_id);
CREATE TABLE IF NOT EXISTS ai_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    document_version INTEGER NOT NULL,
    result TEXT NOT NULL,
    model TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    document_version INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_portfolio_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_review_contract ON ai_reviews(contract_id);
CREATE INDEX IF NOT EXISTS idx_ai_question_contract ON ai_questions(contract_id);
"""

# Columns added after the first prototype release; older databases get them via ALTER TABLE.
ADDED_COLUMNS = {
    "contracts": {"status_before_hold": "TEXT", "tags": "TEXT NOT NULL DEFAULT '[]'"},
    "approval_requests": {"ai_review_id": "INTEGER"},
}


def migrate(conn):
    for table, columns in ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, kind in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    conn.execute("UPDATE contracts SET status='Quoting' WHERE status='Draft'")
    conn.execute("UPDATE contracts SET status='In Progress' WHERE status='Active'")
    # Give every uploaded document a version 1 row so version history starts complete.
    conn.execute(
        """INSERT INTO contract_versions (contract_id, version, file_name, file_type, file_path, file_size, uploaded_by, uploaded_at, note)
           SELECT id, document_version, file_name, file_type, file_path, file_size, uploaded_by, COALESCE(upload_date, created_at), 'Original upload'
           FROM contracts c
           WHERE file_path IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM contract_versions v WHERE v.contract_id = c.id)"""
    )


def init_db():
    from .auth import seed_users  # local imports: these modules import this one
    from .routers.templates import seed_templates

    with connection() as conn:
        conn.executescript(SCHEMA)
        migrate(conn)
        seed_users(conn)
        seed_templates(conn)
        if conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 0 and os.getenv("SEED_DEMO_DATA", "1") == "1":
            seed_contracts(conn)


def seed_contracts(conn):
    today = date.today()
    seeds = [
        ("Creative Cloud Enterprise Agreement", "CL-2026-001", "Adobe", "Creative Cloud Enterprise", "Operations", "In Progress", today - timedelta(days=310), today + timedelta(days=46), "SGD", 184000, "Sarah Lim", "Technology", "Validated", 0.96),
        ("CRM Platform Renewal", "CL-2026-002", "Salesforce", "CRM Platform Renewal", "Renewal", "Quoting", today - timedelta(days=340), today + timedelta(days=26), "USD", 96500, "Daniel Tan", "Sales", "Pending", 0.72),
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
        cursor = conn.execute(
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
        if status == "In Progress" and end:
            # A couple of obligations per active contract so the obligations view is not empty.
            for title_suffix, kind, offset, share in (("Quarterly service review", "Milestone", -20, None), ("Instalment payment", "Payment", 10, 0.25)):
                conn.execute(
                    """INSERT INTO obligations (contract_id, title, kind, due_date, amount, currency, owner, status, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (cursor.lastrowid, title_suffix, kind, (today + timedelta(days=offset)).isoformat(),
                     round(amount * share, 2) if share else None, currency if share else None, owner, "Open", now, now),
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
