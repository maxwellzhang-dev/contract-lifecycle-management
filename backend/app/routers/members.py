"""People who can see a contract in addition to those its scope rules already let in."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..access import can_view, sees_everything
from ..auth import current_user, public_user, require
from ..database import add_audit, connection, utc_now
from ..workflow import notify
from .contracts import load_contract

router = APIRouter(prefix="/api")


class MemberIn(BaseModel):
    user_id: int


def members_out(conn, contract_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT u.id, u.name, u.email, u.role, u.department, m.added_by, m.added_at
           FROM contract_members m JOIN users u ON u.id=m.user_id WHERE m.contract_id=? ORDER BY u.name""",
        (contract_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/contracts/{contract_id}/members")
def list_members(contract_id: int, _: dict = Depends(current_user)):
    with connection() as conn:
        load_contract(conn, contract_id)
        return members_out(conn, contract_id)


@router.post("/contracts/{contract_id}/members", status_code=201)
def add_member(contract_id: int, body: MemberIn, user: dict = Depends(require("contract.edit"))):
    with connection() as conn:
        contract = load_contract(conn, contract_id)
        row = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (body.user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        member = public_user(row)
        if not conn.execute("SELECT 1 FROM contract_members WHERE contract_id=? AND user_id=?", (contract_id, body.user_id)).fetchone():
            already = sees_everything(member) or can_view(conn, member, contract_id)
            conn.execute("INSERT INTO contract_members (contract_id, user_id, added_by, added_at) VALUES (?,?,?,?)",
                         (contract_id, body.user_id, user["name"], utc_now()))
            add_audit(conn, "Member added", user["name"], contract_id, f"{member['name']} can now see this contract")
            if not already:
                notify(conn, [body.user_id], "access", f"You were added to {contract['title']}",
                       f"{user['name']} shared this contract with you.", contract_id)
        return members_out(conn, contract_id)


@router.delete("/contracts/{contract_id}/members/{user_id}")
def remove_member(contract_id: int, user_id: int, user: dict = Depends(require("contract.edit"))):
    with connection() as conn:
        load_contract(conn, contract_id)
        removed = conn.execute("DELETE FROM contract_members WHERE contract_id=? AND user_id=?", (contract_id, user_id)).rowcount
        if removed:
            name = conn.execute("SELECT name FROM users WHERE id=?", (user_id,)).fetchone()
            add_audit(conn, "Member removed", user["name"], contract_id, name["name"] if name else f"user #{user_id}")
        return members_out(conn, contract_id)
