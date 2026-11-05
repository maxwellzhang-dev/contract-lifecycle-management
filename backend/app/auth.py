"""Local users, sessions and role-based permissions.

Sessions are opaque random tokens; only their SHA-256 hash is stored. The token is
accepted from the `clause_session` cookie (browser) or an `Authorization: Bearer`
header (scripts and tests).
"""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .database import connection, utc_now

SESSION_COOKIE = "clause_session"
SESSION_DAYS = 7
PBKDF2_ROUNDS = 240_000

ROLES = ("admin", "legal", "procurement", "finance", "viewer")
APPROVER_ROLES = ("legal", "finance", "procurement", "admin")

PERMISSIONS = {
    "contract.create": {"admin", "procurement", "legal"},
    "contract.edit": {"admin", "procurement", "legal", "finance"},
    "contract.delete": {"admin"},
    "contract.validate": {"admin", "procurement", "legal"},
    "approval.submit": {"admin", "procurement", "legal"},
    "obligation.manage": {"admin", "procurement", "legal", "finance"},
    "audit.read": {"admin", "procurement", "legal", "finance"},
    "user.manage": {"admin"},
    "reminders.run": {"admin"},
    "template.manage": {"admin", "legal"},
    "ai.use": {"admin", "legal", "procurement", "finance"},
    "signature.send": {"admin", "legal", "procurement"},
    "playbook.manage": {"admin", "legal"},
    "invoice.manage": {"admin", "finance", "procurement"},
}


def permissions_for(role: str) -> list[str]:
    return sorted(name for name, roles in PERMISSIONS.items() if role in roles)


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


SEED_USERS = (
    ("maxwell@clause.local", "Maxwell Zhang", "admin"),
    ("legal@clause.local", "Lena Ong", "legal"),
    ("procurement@clause.local", "Daniel Tan", "procurement"),
    ("finance@clause.local", "Priya Nair", "finance"),
    ("viewer@clause.local", "Victor Lim", "viewer"),
)


def seed_users(conn):
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        return
    password = os.getenv("CLAUSE_SEED_PASSWORD", "ChangeMe123!")
    now = utc_now()
    for email, name, role in SEED_USERS:
        conn.execute(
            "INSERT INTO users (email, name, role, password_hash, active, created_at) VALUES (?,?,?,?,1,?)",
            (email, name, role, hash_password(password), now),
        )


def public_user(row) -> dict:
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "active": bool(row["active"]),
        "department": row["department"],
        "access_scope": "all" if row["role"] == "admin" else (row["access_scope"] or "all"),
        "permissions": permissions_for(row["role"]),
    }


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with connection() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(),))
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (_token_hash(token), user_id, now.isoformat(), (now + timedelta(days=SESSION_DAYS)).isoformat()),
        )
    return token


def set_session_cookie(response: Response, token: str):
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
        secure=os.getenv("COOKIE_SECURE", "0") == "1", path="/",
    )


def request_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(SESSION_COOKIE)


def delete_session(token: str):
    with connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))


def current_user(request: Request) -> dict:
    token = request_token(request)
    if not token:
        raise HTTPException(401, "Sign in to continue.")
    with connection() as conn:
        row = conn.execute(
            """SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id
               WHERE sessions.token_hash=? AND sessions.expires_at > ?""",
            (_token_hash(token), utc_now()),
        ).fetchone()
    if not row or not row["active"]:
        raise HTTPException(401, "Your session has expired. Sign in again.")
    return public_user(row)


def require(permission: str):
    def dependency(user: dict = Depends(current_user)) -> dict:
        if permission not in user["permissions"]:
            raise HTTPException(403, "Your role does not allow this action.")
        return user
    return dependency


class LoginRequest(BaseModel):
    email: str
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class UserCreate(BaseModel):
    email: str
    name: str
    role: str
    password: str
    department: str | None = None
    access_scope: str = "all"


class UserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    active: bool | None = None
    password: str | None = None
    department: str | None = None
    access_scope: str | None = None


def validate_password(password: str):
    if len(password) < 10:
        raise HTTPException(422, "Passwords must be at least 10 characters.")


def validate_role(role: str):
    if role not in ROLES:
        raise HTTPException(422, f"role must be one of: {', '.join(ROLES)}")


def validate_scope(scope: str, department: str | None):
    from .access import ACCESS_SCOPES
    if scope not in ACCESS_SCOPES:
        raise HTTPException(422, f"access_scope must be one of: {', '.join(ACCESS_SCOPES)}")
    if scope == "department" and not (department or "").strip():
        raise HTTPException(422, "Set a department for users who see their department's contracts.")
