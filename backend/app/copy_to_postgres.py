"""Copy an existing Clause SQLite database into an empty PostgreSQL database.

    python -m backend.app.copy_to_postgres --sqlite backend/data/contracts.db \
        --postgres postgresql://user:password@host:5432/clause

The SQLite file is upgraded to the current schema first (as the app would on start), the
PostgreSQL schema is created, every table is copied with its ids, and id sequences are moved
past the copied rows. Sign-in sessions and pending single sign-on attempts are not copied.
Uploaded files stay where they are; point UPLOAD_DIR at the same folder.
"""

import argparse
import sqlite3
import sys

import psycopg

from .database import SCHEMA, migrate
from .pg import Connection, schema_sql

# Parents before children, so foreign keys hold while copying.
TABLES = (
    "users", "contracts", "audit_log", "contract_templates", "approval_requests", "approval_steps",
    "contract_versions", "obligations", "notifications", "contract_comments", "ai_reviews", "ai_questions",
    "ai_portfolio_questions", "signature_requests", "signature_signers",
)


def copy(sqlite_path: str, postgres_url: str, log=print) -> dict[str, int]:
    source = sqlite3.connect(sqlite_path)
    source.row_factory = sqlite3.Row
    source.executescript(SCHEMA)
    migrate(source)
    source.commit()

    target = Connection(psycopg.connect(postgres_url))
    try:
        target.executescript(schema_sql(SCHEMA))
        migrate(target)
        busy = [t for t in TABLES if target.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]]
        if busy:
            raise SystemExit(f"The PostgreSQL database already has data in: {', '.join(busy)}. Use an empty database.")
        counts = {}
        for table in TABLES:
            target_columns = {r[0] for r in target.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name=?", (table,))}
            columns = [r["name"] for r in source.execute(f"PRAGMA table_info({table})") if r["name"] in target_columns]
            rows = source.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY id").fetchall()
            if rows:
                with target.raw.cursor() as cursor:
                    cursor.executemany(
                        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
                        [tuple(row) for row in rows],
                    )
            target.raw.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")
            counts[table] = len(rows)
            log(f"{table}: {len(rows)} row(s)")
        target.commit()
        return counts
    finally:
        target.close()
        source.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sqlite", required=True, help="Path to the existing contracts.db")
    parser.add_argument("--postgres", required=True, help="postgresql:// URL of an empty database")
    args = parser.parse_args(argv)
    copy(args.sqlite, args.postgres)
    print("Done. Set DATABASE_URL to the PostgreSQL URL and restart the API.", file=sys.stderr)


if __name__ == "__main__":
    main()
