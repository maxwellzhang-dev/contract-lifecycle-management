import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..database import add_audit, connection, utc_now
from ..workflow import notify

router = APIRouter(prefix="/api")

MAX_COMMENT_LENGTH = 4000


class CommentIn(BaseModel):
    body: str


def mentioned_users(conn, body: str) -> list[dict]:
    """Active users written as @Full Name (case-insensitive, word boundary after the name)."""
    lowered = body.lower()
    found = []
    for row in conn.execute("SELECT id, name FROM users WHERE active=1"):
        pattern = r"@" + re.escape(row["name"].lower()) + r"(?![\w])"
        if re.search(pattern, lowered):
            found.append(dict(row))
    return found


def comment_row(conn, comment_id: int) -> dict:
    row = conn.execute(
        """SELECT c.*, u.name author_name, u.role author_role FROM contract_comments c
           JOIN users u ON u.id=c.user_id WHERE c.id=?""",
        (comment_id,),
    ).fetchone()
    return dict(row)


@router.get("/contracts/{contract_id}/comments")
def list_comments(contract_id: int, _: dict = Depends(current_user)):
    with connection() as conn:
        rows = conn.execute(
            """SELECT c.*, u.name author_name, u.role author_role FROM contract_comments c
               JOIN users u ON u.id=c.user_id WHERE c.contract_id=? ORDER BY c.created_at, c.id""",
            (contract_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@router.post("/contracts/{contract_id}/comments", status_code=201)
def add_comment(contract_id: int, body: CommentIn, user: dict = Depends(current_user)):
    text = body.body.strip()
    if not text:
        raise HTTPException(422, "A comment cannot be empty.")
    if len(text) > MAX_COMMENT_LENGTH:
        raise HTTPException(422, f"Comments are limited to {MAX_COMMENT_LENGTH} characters.")
    with connection() as conn:
        contract = conn.execute("SELECT id, title FROM contracts WHERE id=?", (contract_id,)).fetchone()
        if not contract:
            raise HTTPException(404, "Contract not found")
        cursor = conn.execute(
            "INSERT INTO contract_comments (contract_id, user_id, body, created_at) VALUES (?,?,?,?)",
            (contract_id, user["id"], text, utc_now()),
        )
        mentions = [m for m in mentioned_users(conn, text) if m["id"] != user["id"]]
        notify(conn, [m["id"] for m in mentions], "mention", f"{user['name']} mentioned you on {contract['title']}",
               text[:280], contract_id)
        add_audit(conn, "Comment added", user["name"], contract_id,
                  f"Mentioned {', '.join(m['name'] for m in mentions)}" if mentions else None)
        return comment_row(conn, cursor.lastrowid)


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, user: dict = Depends(current_user)):
    with connection() as conn:
        row = conn.execute("SELECT * FROM contract_comments WHERE id=?", (comment_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Comment not found")
        if row["user_id"] != user["id"] and user["role"] != "admin":
            raise HTTPException(403, "You can only delete your own comments.")
        conn.execute("DELETE FROM contract_comments WHERE id=?", (comment_id,))
        add_audit(conn, "Comment deleted", user["name"], row["contract_id"])
    return {"deleted": True, "id": comment_id}
