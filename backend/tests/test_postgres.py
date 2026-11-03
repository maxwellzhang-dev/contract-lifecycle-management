import os
import sqlite3

import pytest

from backend.app.pg import Row, translate

PG_URL = os.getenv("TEST_DATABASE_URL", "")


def test_translate_sqlite_sql_for_postgres():
    assert translate("SELECT * FROM t WHERE a=? AND b LIKE ?") == "SELECT * FROM t WHERE a=%s AND b ILIKE %s"
    # String literals keep their question marks and percent signs.
    assert translate("SELECT 'why?' || ?, '100%'") == "SELECT 'why?' || %s, '100%%'"
    assert translate("INSERT OR IGNORE INTO n (a) VALUES (?)") == "INSERT INTO n (a) VALUES (%s) ON CONFLICT DO NOTHING"


def test_row_behaves_like_sqlite_row():
    row = Row(("id", "name"), (7, "Ada"))
    assert row[0] == 7 and row["name"] == "Ada" and list(row) == [7, "Ada"]
    assert dict(row) == {"id": 7, "name": "Ada"} and row.keys() == ["id", "name"]
    (first, _), = [row]
    assert first == 7


@pytest.mark.skipif(not PG_URL, reason="set TEST_DATABASE_URL to an empty PostgreSQL database")
def test_copy_sqlite_database_to_postgres(tmp_path, monkeypatch):
    import psycopg
    from backend.app import copy_to_postgres
    from backend.app.database import connection, init_db

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "source.db"))
    monkeypatch.setenv("SEED_DEMO_DATA", "1")
    init_db()  # SQLite with demo users, templates, contracts and obligations
    with sqlite3.connect(tmp_path / "source.db") as source:
        expected = {t: source.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("users", "contracts", "obligations")}
        max_contract = source.execute("SELECT MAX(id) FROM contracts").fetchone()[0]

    with psycopg.connect(PG_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    counts = copy_to_postgres.copy(str(tmp_path / "source.db"), PG_URL, log=lambda _: None)
    assert {t: counts[t] for t in expected} == expected and expected["contracts"] > 0

    monkeypatch.setenv("DATABASE_URL", PG_URL)
    with connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users WHERE lower(email)='maxwell@clause.local'").fetchone()[0] == 1
        new_id = conn.execute("INSERT INTO contracts (title, created_at, updated_at) VALUES ('New', 'x', 'x')").lastrowid
    assert new_id == max_contract + 1  # sequences continue after the copied ids
    with pytest.raises(SystemExit):  # refuses to copy into a database that has data
        copy_to_postgres.copy(str(tmp_path / "source.db"), PG_URL, log=lambda _: None)
