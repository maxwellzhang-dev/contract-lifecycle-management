import sqlite3


def test_old_database_is_upgraded(tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "a.pdf").write_bytes(b"%PDF")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE contracts (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, reference_number TEXT,
          vendor_name TEXT, project_name TEXT, phase TEXT, status TEXT NOT NULL DEFAULT 'Draft',
          project_start_date TEXT, project_end_date TEXT, renewal_date TEXT, currency TEXT, one_time_amount REAL,
          optional_amount REAL, vendor_contact TEXT, internal_contact TEXT, deliverables TEXT, payment_terms TEXT,
          notes TEXT, department TEXT, owner TEXT, validation_status TEXT NOT NULL DEFAULT 'Pending', reviewed_by TEXT,
          validated_at TEXT, renewal_decision TEXT, renewal_owner TEXT, renewal_notice_deadline TEXT,
          renewal_action TEXT, renewal_comments TEXT, renewal_completion_date TEXT, replacement_contract_reference TEXT,
          extraction_confidence REAL, missing_fields TEXT NOT NULL DEFAULT '[]', low_confidence_fields TEXT NOT NULL DEFAULT '[]',
          field_confidences TEXT NOT NULL DEFAULT '{}', extraction_note TEXT, file_name TEXT, file_type TEXT, file_path TEXT,
          file_size INTEGER, document_version INTEGER NOT NULL DEFAULT 1, uploaded_by TEXT, upload_date TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, contract_id INTEGER,
          actor TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL);
    """)
    conn.execute("INSERT INTO contracts (title, status, file_name, file_path, created_at, updated_at) VALUES ('Old', 'Draft', 'a.pdf', ?, 'x', 'x')",
                 (str(uploads / "a.pdf"),))
    conn.commit()
    conn.close()

    monkeypatch.setenv("DATABASE_PATH", str(db))
    monkeypatch.setenv("UPLOAD_DIR", str(uploads))
    from backend.app.database import connection, init_db

    init_db()
    init_db()  # idempotent
    with connection() as conn:
        row = conn.execute("SELECT status, status_before_hold FROM contracts").fetchone()
        assert row["status"] == "Quoting"
        assert conn.execute("SELECT COUNT(*) FROM contract_versions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 5
