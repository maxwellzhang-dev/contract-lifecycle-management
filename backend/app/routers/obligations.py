from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..access import visibility_sql
from ..auth import current_user, require
from ..database import add_audit, connection, utc_now
from ..workflow import OBLIGATION_KINDS, OBLIGATION_STATUSES, run_reminders

router = APIRouter(prefix="/api")


class ObligationIn(BaseModel):
    title: str | None = None
    kind: str | None = None
    due_date: str | None = None
    amount: float | None = None
    currency: str | None = None
    owner: str | None = None
    status: str | None = None
    notes: str | None = None


SELECT = """SELECT o.*, c.title contract_title, c.vendor_name, c.status contract_status
            FROM obligations o JOIN contracts c ON c.id = o.contract_id"""


def decorate(row) -> dict:
    item = dict(row)
    days = None
    if item.get("due_date"):
        days = (date.fromisoformat(item["due_date"]) - date.today()).days
    item["days_until_due"] = days
    item["overdue"] = item["status"] == "Open" and days is not None and days < 0
    return item


def clean(values: dict) -> dict:
    values = {k: (v.strip() or None) if isinstance(v, str) else v for k, v in values.items()}
    if "title" in values and not values["title"]:
        raise HTTPException(422, "An obligation needs a title.")
    if values.get("kind") and values["kind"] not in OBLIGATION_KINDS:
        raise HTTPException(422, f"kind must be one of: {', '.join(OBLIGATION_KINDS)}")
    if values.get("status") and values["status"] not in OBLIGATION_STATUSES:
        raise HTTPException(422, f"status must be one of: {', '.join(OBLIGATION_STATUSES)}")
    if values.get("due_date"):
        try:
            date.fromisoformat(values["due_date"])
        except ValueError:
            raise HTTPException(422, "due_date must be a date in YYYY-MM-DD format")
    return values


@router.get("/obligations")
def list_obligations(
    status: str = "",
    due_within: int | None = Query(None, ge=0, le=3650),
    contract_id: int | None = None,
    user: dict = Depends(current_user),
):
    vis, vparams = visibility_sql(user, "c")
    clauses, params = [vis], list(vparams)
    if status:
        clauses.append("o.status=?")
        params.append(status)
    if due_within is not None:
        clauses.append("o.due_date IS NOT NULL AND o.due_date <= ?")
        params.append((date.today() + timedelta(days=due_within)).isoformat())
    if contract_id:
        clauses.append("o.contract_id=?")
        params.append(contract_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connection() as conn:
        rows = conn.execute(f"{SELECT} {where} ORDER BY o.due_date IS NULL, o.due_date, o.id", params).fetchall()
    return [decorate(row) for row in rows]


@router.post("/contracts/{contract_id}/obligations", status_code=201)
def create_obligation(contract_id: int, body: ObligationIn, user: dict = Depends(require("obligation.manage"))):
    values = clean(body.model_dump())
    if not values.get("title"):
        raise HTTPException(422, "An obligation needs a title.")
    now = utc_now()
    with connection() as conn:
        if not conn.execute("SELECT 1 FROM contracts WHERE id=?", (contract_id,)).fetchone():
            raise HTTPException(404, "Contract not found")
        status = values.get("status") or "Open"
        cursor = conn.execute(
            """INSERT INTO obligations (contract_id, title, kind, due_date, amount, currency, owner, status, notes,
                                        completed_at, completed_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (contract_id, values["title"], values.get("kind") or "Deliverable", values.get("due_date"),
             values.get("amount"), values.get("currency"), values.get("owner"), status, values.get("notes"),
             now if status != "Open" else None, user["name"] if status != "Open" else None, now, now),
        )
        add_audit(conn, "Obligation added", user["name"], contract_id,
                  f"{values['title']}" + (f" due {values['due_date']}" if values.get("due_date") else ""))
        return decorate(conn.execute(f"{SELECT} WHERE o.id=?", (cursor.lastrowid,)).fetchone())


@router.patch("/obligations/{obligation_id}")
def update_obligation(obligation_id: int, body: ObligationIn, user: dict = Depends(require("obligation.manage"))):
    changes = clean(body.model_dump(exclude_unset=True))
    with connection() as conn:
        old = conn.execute("SELECT * FROM obligations WHERE id=?", (obligation_id,)).fetchone()
        if not old:
            raise HTTPException(404, "Obligation not found")
        changes = {k: v for k, v in changes.items() if old[k] != v}
        if not changes:
            return decorate(conn.execute(f"{SELECT} WHERE o.id=?", (obligation_id,)).fetchone())
        now = utc_now()
        if "status" in changes:
            done = changes["status"] != "Open"
            changes["completed_at"] = now if done else None
            changes["completed_by"] = user["name"] if done else None
        conn.execute(
            f"UPDATE obligations SET {', '.join(f'{k}=?' for k in changes)}, updated_at=? WHERE id=?",
            (*changes.values(), now, obligation_id),
        )
        action = {"Done": "Obligation completed", "Waived": "Obligation waived", "Open": "Obligation reopened"}.get(
            changes.get("status"), "Obligation updated")
        details = "; ".join(f"{k}: {old[k]!r} → {v!r}" for k, v in changes.items() if k not in {"completed_at", "completed_by"})
        add_audit(conn, action, user["name"], old["contract_id"], f"{old['title']} — {details}")
        return decorate(conn.execute(f"{SELECT} WHERE o.id=?", (obligation_id,)).fetchone())


@router.delete("/obligations/{obligation_id}")
def delete_obligation(obligation_id: int, user: dict = Depends(require("obligation.manage"))):
    with connection() as conn:
        old = conn.execute("SELECT * FROM obligations WHERE id=?", (obligation_id,)).fetchone()
        if not old:
            raise HTTPException(404, "Obligation not found")
        conn.execute("DELETE FROM obligations WHERE id=?", (obligation_id,))
        add_audit(conn, "Obligation removed", user["name"], old["contract_id"], old["title"])
    return {"deleted": True, "id": obligation_id}


# --- notifications -------------------------------------------------------------------

@router.get("/notifications")
def my_notifications(limit: int = Query(50, ge=1, le=200), user: dict = Depends(current_user)):
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (user["id"], limit),
        ).fetchall()
        unread = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL", (user["id"],)
        ).fetchone()[0]
    return {"unread": unread, "items": [dict(row) for row in rows]}


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: int, user: dict = Depends(current_user)):
    with connection() as conn:
        conn.execute(
            "UPDATE notifications SET read_at=COALESCE(read_at, ?) WHERE id=? AND user_id=?",
            (utc_now(), notification_id, user["id"]),
        )
    return {"read": True}


@router.post("/notifications/read-all")
def mark_all_read(user: dict = Depends(current_user)):
    with connection() as conn:
        conn.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", (utc_now(), user["id"]))
    return {"read": True}


@router.post("/reminders/run")
def trigger_reminders(user: dict = Depends(require("reminders.run"))):
    with connection() as conn:
        created = run_reminders(conn)
        add_audit(conn, "Reminders run", user["name"], details=f"{created} new notification(s)")
    return {"created": created}
