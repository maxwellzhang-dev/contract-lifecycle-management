from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..auth import (
    SESSION_COOKIE, LoginRequest, PasswordChange, UserCreate, UserUpdate, create_session,
    current_user, delete_session, hash_password, public_user, request_token, require,
    set_session_cookie, validate_password, validate_role, verify_password,
)
from ..database import add_audit, connection, utc_now
from . import sso

router = APIRouter(prefix="/api")

# Verified against when the email is unknown, so both paths take the same time.
_DUMMY_HASH = hash_password("timing-equaliser")


@router.post("/auth/login")
def login(body: LoginRequest, response: Response):
    if sso.enabled() and not sso.password_login_enabled():
        raise HTTPException(403, "Password sign-in is turned off. Use single sign-on.")
    with connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?)", (body.email.strip(),)).fetchone()
    valid = verify_password(body.password, row["password_hash"] if row else _DUMMY_HASH)
    if not row or not valid or not row["active"]:
        raise HTTPException(401, "Email or password is incorrect.")
    token = create_session(row["id"])
    set_session_cookie(response, token)
    with connection() as conn:
        add_audit(conn, "Signed in", row["name"])
    return {"user": public_user(row), "token": token}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    token = request_token(request)
    if token:
        delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"signed_out": True}


@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return user


@router.post("/auth/password")
def change_password(body: PasswordChange, user: dict = Depends(current_user)):
    validate_password(body.new_password)
    with connection() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not verify_password(body.current_password, row["password_hash"]):
            raise HTTPException(400, "Current password is incorrect.")
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(body.new_password), user["id"]))
        add_audit(conn, "Password changed", user["name"])
    return {"changed": True}


@router.get("/users/directory")
def directory(_: dict = Depends(current_user)):
    """Names and roles of active users, for owner pickers. Any signed-in user can read it."""
    with connection() as conn:
        rows = conn.execute("SELECT id, name, role FROM users WHERE active=1 ORDER BY name").fetchall()
    return [dict(row) for row in rows]


@router.get("/users")
def list_users(_: dict = Depends(require("user.manage"))):
    with connection() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY name").fetchall()
    return [public_user(row) for row in rows]


@router.post("/users", status_code=201)
def create_user(body: UserCreate, admin: dict = Depends(require("user.manage"))):
    validate_role(body.role)
    validate_password(body.password)
    if "@" not in body.email or not body.name.strip():
        raise HTTPException(422, "A name and a valid email are required.")
    with connection() as conn:
        if conn.execute("SELECT 1 FROM users WHERE lower(email)=lower(?)", (body.email.strip(),)).fetchone():
            raise HTTPException(409, "A user with this email already exists.")
        cursor = conn.execute(
            "INSERT INTO users (email, name, role, password_hash, active, created_at) VALUES (?,?,?,?,1,?)",
            (body.email.strip(), body.name.strip(), body.role, hash_password(body.password), utc_now()),
        )
        add_audit(conn, "User created", admin["name"], details=f"{body.name} ({body.email}) as {body.role}")
        row = conn.execute("SELECT * FROM users WHERE id=?", (cursor.lastrowid,)).fetchone()
    return public_user(row)


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, admin: dict = Depends(require("user.manage"))):
    changes = body.model_dump(exclude_unset=True)
    if user_id == admin["id"] and (changes.get("active") is False or changes.get("role", "admin") != "admin"):
        raise HTTPException(409, "You cannot deactivate or demote your own account.")
    with connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        notes = []
        if "role" in changes:
            validate_role(changes["role"])
            conn.execute("UPDATE users SET role=? WHERE id=?", (changes["role"], user_id))
            notes.append(f"role: {row['role']} → {changes['role']}")
        if changes.get("name"):
            conn.execute("UPDATE users SET name=? WHERE id=?", (changes["name"].strip(), user_id))
            notes.append(f"name: {row['name']} → {changes['name'].strip()}")
        if "active" in changes:
            conn.execute("UPDATE users SET active=? WHERE id=?", (int(bool(changes["active"])), user_id))
            notes.append("reactivated" if changes["active"] else "deactivated")
        if changes.get("password"):
            validate_password(changes["password"])
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(changes["password"]), user_id))
            notes.append("password reset")
        if changes.get("password") or changes.get("active") is False or "role" in changes:
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        if notes:
            add_audit(conn, "User updated", admin["name"], details=f"{row['email']}: " + "; ".join(notes))
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return public_user(row)
