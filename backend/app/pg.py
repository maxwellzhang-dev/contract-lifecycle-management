"""PostgreSQL support behind the same small interface the app uses with sqlite3.

The routers write portable SQL with `?` placeholders and read rows by name or position.
This adapter translates the few SQLite-isms they use (placeholders, INSERT OR IGNORE,
case-insensitive LIKE, lastrowid) and returns rows that behave like sqlite3.Row.
"""

import re

import psycopg
from psycopg.rows import BaseRowFactory


class Row:
    """Index by position or column name, iterate values, dict(row) — like sqlite3.Row."""

    __slots__ = ("_keys", "_values", "_index")

    def __init__(self, keys: tuple[str, ...], values: tuple):
        self._keys = keys
        self._values = values
        self._index = {key: i for i, key in enumerate(keys)}

    def __getitem__(self, item):
        if isinstance(item, (int, slice)):
            return self._values[item]
        return self._values[self._index[item]]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return list(self._keys)

    def __repr__(self):
        return f"Row({dict(zip(self._keys, self._values))!r})"


def row_factory(cursor) -> BaseRowFactory:
    keys = tuple(column.name for column in cursor.description or ())
    return lambda values: Row(keys, tuple(values))


_PLACEHOLDER = re.compile(r"\?")
_STRING = re.compile(r"'(?:[^']|'')*'")


def translate(sql: str) -> str:
    """Rewrite SQLite-flavoured SQL for PostgreSQL. String literals are left untouched."""
    parts, last = [], 0
    for match in _STRING.finditer(sql):
        parts.append(_translate_code(sql[last:match.start()]))
        parts.append(match.group(0).replace("%", "%%"))
        last = match.end()
    parts.append(_translate_code(sql[last:]))
    text = "".join(parts)
    if re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", text, re.I):
        text = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", text, flags=re.I).rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return text


def _translate_code(code: str) -> str:
    code = code.replace("%", "%%")
    code = _PLACEHOLDER.sub("%s", code)
    return re.sub(r"\bLIKE\b", "ILIKE", code)


# Tables whose primary key is not a generated id; every other INSERT returns its new id.
NO_ID_TABLES = {"sessions", "sso_states", "rag_documents", "contract_members", "calendar_feeds"}
_INSERT = re.compile(r"^\s*INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+(\w+)", re.I)


class Cursor:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor.fetchall() if self._cursor.description else [])

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount



class Connection:
    def __init__(self, raw: psycopg.Connection):
        self.raw = raw

    def execute(self, sql: str, params=()) -> Cursor:
        cursor = self.raw.cursor(row_factory=row_factory)
        text = translate(sql)
        insert = _INSERT.match(sql)
        if insert and insert.group(1).lower() not in NO_ID_TABLES and not re.search(r"\bRETURNING\b", text, re.I):
            cursor.execute(text + " RETURNING id", tuple(params))
            first = cursor.fetchone() if cursor.description else None
            return Cursor(cursor, first[0] if first else None)
        cursor.execute(text, tuple(params))
        return Cursor(cursor)

    def executescript(self, script: str):
        self.raw.execute(script)

    def commit(self):
        self.raw.commit()

    def close(self):
        self.raw.close()


def connect(url: str) -> Connection:
    return Connection(psycopg.connect(url))


def schema_sql(sqlite_schema: str) -> str:
    """The SQLite schema, adapted for PostgreSQL."""
    sql = sqlite_schema
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    sql = re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql)
    sql = sql.replace(" COLLATE NOCASE", "")
    return ROUND_FUNCTION + sql + """
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_email_lower ON users (lower(email));
"""


# SQLite's ROUND accepts floating point numbers; PostgreSQL's two-argument ROUND only takes numeric.
ROUND_FUNCTION = """
CREATE OR REPLACE FUNCTION round(double precision, integer) RETURNS double precision
    AS $$ SELECT round($1::numeric, $2)::double precision $$ LANGUAGE sql IMMUTABLE;
"""
