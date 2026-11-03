from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import APPROVER_ROLES, current_user, require
from ..database import add_audit, connection, utc_now
from ..workflow import (
    APPROVAL_DEFAULT_CHAIN, approval_detail, close_approval, contract_recipients, notify,
    pending_approval, users_with_roles,
)
from .. import jobs
from .ai import auto_review_enabled, current_review_id, review_brief, review_job_pending

router = APIRouter(prefix="/api")


class ApprovalSubmit(BaseModel):
    roles: list[str] | None = None
    note: str | None = None


class ApprovalDecision(BaseModel):
    decision: str  # "approve" | "reject"
    comment: str | None = None


def load_request(conn, request_id: int):
    row = conn.execute("SELECT * FROM approval_requests WHERE id=?", (request_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Approval request not found")
    return row


def detail(conn, request_id: int) -> dict:
    """Approval request with its steps and a summary of the AI review approvers should read."""
    item = approval_detail(conn, request_id)
    item["ai_review"] = review_brief(conn, item.get("ai_review_id"))
    state = "attached" if item["ai_review"] else "none"
    if state == "none" and item["status"] == "Pending" and review_job_pending(conn, request_id):
        state = "running"
    item["ai_review_state"] = state
    return item


def set_contract_status(conn, contract_id: int, status: str):
    conn.execute("UPDATE contracts SET status=?, updated_at=? WHERE id=?", (status, utc_now(), contract_id))


@router.get("/contracts/{contract_id}/approvals")
def contract_approvals(contract_id: int, _: dict = Depends(current_user)):
    with connection() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM approval_requests WHERE contract_id=? ORDER BY id DESC", (contract_id,))]
        return [detail(conn, request_id) for request_id in ids]


@router.post("/contracts/{contract_id}/approvals", status_code=201)
def submit_for_approval(contract_id: int, body: ApprovalSubmit, user: dict = Depends(require("approval.submit"))):
    roles = body.roles or APPROVAL_DEFAULT_CHAIN
    if not 1 <= len(roles) <= 5 or any(role not in APPROVER_ROLES for role in roles):
        raise HTTPException(422, f"Choose 1–5 approval steps from: {', '.join(APPROVER_ROLES)}")
    with connection() as conn:
        contract = conn.execute("SELECT * FROM contracts WHERE id=?", (contract_id,)).fetchone()
        if not contract:
            raise HTTPException(404, "Contract not found")
        if pending_approval(conn, contract_id):
            raise HTTPException(409, "This contract already has an approval request in progress.")
        if contract["status"] != "Quoting":
            raise HTTPException(409, f"Only contracts in Quoting can be submitted for approval (this one is {contract['status']}).")
        if contract["validation_status"] != "Validated":
            raise HTTPException(409, "Confirm and validate the contract details before submitting for approval.")
        now = utc_now()
        review_id = current_review_id(conn, contract)
        cursor = conn.execute(
            "INSERT INTO approval_requests (contract_id, status, submitted_by, note, submitted_at, ai_review_id) VALUES (?,?,?,?,?,?)",
            (contract_id, "Pending", user["id"], (body.note or "").strip() or None, now, review_id),
        )
        request_id = cursor.lastrowid
        if review_id is None and auto_review_enabled():
            # A background job, so submitting never waits on the model (retried if the model is down).
            jobs.enqueue(conn, "ai_review", {"request_id": request_id, "contract_id": contract_id})
        for order, role in enumerate(roles, start=1):
            conn.execute(
                "INSERT INTO approval_steps (request_id, step_order, role, status) VALUES (?,?,?,?)",
                (request_id, order, role, "Pending" if order == 1 else "Waiting"),
            )
        set_contract_status(conn, contract_id, "Under Review")
        add_audit(conn, "Submitted for approval", user["name"], contract_id,
                  f"Approval chain: {' → '.join(roles)}" + (f". Note: {body.note.strip()}" if body.note and body.note.strip() else ""))
        notify_step(conn, contract, roles[0], exclude=user["id"])
        return detail(conn, request_id)


def notify_step(conn, contract, role: str, exclude: int):
    recipients = [uid for uid in users_with_roles(conn, (role,)) if uid != exclude]
    notify(conn, recipients, "approval", f"Approval needed: {contract['title']}",
           f"{contract['vendor_name'] or 'Unknown vendor'} is waiting for {role} approval.", contract["id"])


@router.post("/approvals/{request_id}/decision")
def decide(request_id: int, body: ApprovalDecision, user: dict = Depends(current_user)):
    if body.decision not in {"approve", "reject"}:
        raise HTTPException(422, "decision must be approve or reject")
    comment = (body.comment or "").strip() or None
    if body.decision == "reject" and not comment:
        raise HTTPException(422, "Add a comment explaining the rejection.")
    with connection() as conn:
        request = load_request(conn, request_id)
        if request["status"] != "Pending":
            raise HTTPException(409, f"This request is already {request['status'].lower()}.")
        step = conn.execute(
            "SELECT * FROM approval_steps WHERE request_id=? AND status='Pending' ORDER BY step_order LIMIT 1",
            (request_id,),
        ).fetchone()
        if user["role"] not in {step["role"], "admin"}:
            raise HTTPException(403, f"This step needs a {step['role']} approver.")
        if request["submitted_by"] == user["id"]:
            raise HTTPException(403, "You cannot approve a request you submitted.")
        if conn.execute("SELECT 1 FROM approval_steps WHERE request_id=? AND decided_by=?", (request_id, user["id"])).fetchone():
            raise HTTPException(403, "You have already decided a step of this request; another approver must decide this one.")
        now = utc_now()
        contract = conn.execute("SELECT * FROM contracts WHERE id=?", (request["contract_id"],)).fetchone()
        conn.execute(
            "UPDATE approval_steps SET status=?, decided_by=?, decided_at=?, comment=? WHERE id=?",
            ("Approved" if body.decision == "approve" else "Rejected", user["id"], now, comment, step["id"]),
        )
        label = f"{step['role'].title()} step {step['step_order']}"
        if body.decision == "reject":
            close_approval(conn, request_id, "Rejected")
            set_contract_status(conn, contract["id"], "Quoting")
            add_audit(conn, "Approval rejected", user["name"], contract["id"], f"{label}: {comment}")
            notify(conn, [request["submitted_by"], *contract_recipients(conn, contract["owner"])], "approval",
                   f"Rejected: {contract['title']}", f"{user['name']} ({step['role']}): {comment}", contract["id"])
        else:
            next_step = conn.execute(
                "SELECT * FROM approval_steps WHERE request_id=? AND status='Waiting' ORDER BY step_order LIMIT 1",
                (request_id,),
            ).fetchone()
            add_audit(conn, "Approval step approved", user["name"], contract["id"], label + (f": {comment}" if comment else ""))
            if next_step:
                conn.execute("UPDATE approval_steps SET status='Pending' WHERE id=?", (next_step["id"],))
                notify_step(conn, contract, next_step["role"], exclude=user["id"])
            else:
                conn.execute("UPDATE approval_requests SET status='Approved', completed_at=? WHERE id=?", (now, request_id))
                set_contract_status(conn, contract["id"], "Cleared to Start")
                add_audit(conn, "Approval completed", user["name"], contract["id"], "status: 'Under Review' → 'Cleared to Start'")
                notify(conn, [request["submitted_by"], *contract_recipients(conn, contract["owner"])], "approval",
                       f"Approved: {contract['title']}", "All approval steps are complete. The vendor is cleared to start.",
                       contract["id"])
        return detail(conn, request_id)


@router.post("/approvals/{request_id}/withdraw")
def withdraw(request_id: int, user: dict = Depends(current_user)):
    with connection() as conn:
        request = load_request(conn, request_id)
        if request["status"] != "Pending":
            raise HTTPException(409, f"This request is already {request['status'].lower()}.")
        if request["submitted_by"] != user["id"] and user["role"] != "admin":
            raise HTTPException(403, "Only the submitter or an admin can withdraw this request.")
        close_approval(conn, request_id, "Withdrawn")
        set_contract_status(conn, request["contract_id"], "Quoting")
        add_audit(conn, "Approval withdrawn", user["name"], request["contract_id"], "status: 'Under Review' → 'Quoting'")
        return detail(conn, request_id)


@router.get("/approvals/inbox")
def inbox(user: dict = Depends(current_user)):
    """Approval steps the signed-in user can decide right now."""
    roles = (user["role"],) if user["role"] != "admin" else APPROVER_ROLES
    marks = ",".join("?" * len(roles))
    with connection() as conn:
        rows = conn.execute(
            f"""SELECT s.id step_id, s.role, s.step_order, r.id request_id, r.note, r.submitted_at,
                       u.name submitted_by_name, c.id contract_id, c.title, c.vendor_name, c.currency,
                       c.one_time_amount, c.optional_amount, c.project_start_date, c.project_end_date,
                       r.ai_review_id, (SELECT COUNT(*) FROM approval_steps x WHERE x.request_id=r.id) total_steps
                FROM approval_steps s
                JOIN approval_requests r ON r.id=s.request_id
                JOIN contracts c ON c.id=r.contract_id
                JOIN users u ON u.id=r.submitted_by
                WHERE s.status='Pending' AND r.status='Pending' AND s.role IN ({marks})
                  AND r.submitted_by != ?
                  AND NOT EXISTS (SELECT 1 FROM approval_steps d WHERE d.request_id=r.id AND d.decided_by=?)
                ORDER BY r.submitted_at""",
            (*roles, user["id"], user["id"]),
        ).fetchall()
        items = []
        for row in rows:
            brief = review_brief(conn, row["ai_review_id"])
            items.append({**dict(row), "ai_risk_counts": brief["risk_counts"] if brief else None})
    return items
