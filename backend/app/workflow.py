"""Lifecycle rules, approvals, notifications and reminders."""

import logging
import os
import smtplib
from datetime import date
from email.message import EmailMessage

from . import jobs
from .database import utc_now

log = logging.getLogger("clause.workflow")

CONTRACT_STATUSES = (
    "Quoting", "Under Review", "Cleared to Start", "In Progress",
    "Completed", "On Hold", "Cancelled",
)
TERMINAL_STATUSES = {"Completed", "Cancelled"}

# Status changes a user can make directly. "Under Review" and "Cleared to Start" are only
# reached through the approval workflow (submit / final approval), never by editing.
MANUAL_TRANSITIONS = {
    "Quoting": {"On Hold", "Cancelled"},
    "Under Review": {"On Hold", "Cancelled"},
    "Cleared to Start": {"In Progress", "On Hold", "Cancelled"},
    "In Progress": {"Completed", "On Hold", "Cancelled"},
    "On Hold": {"Cancelled"},  # plus resuming to the status held before (see below)
    "Completed": set(),
    "Cancelled": set(),
}

APPROVAL_DEFAULT_CHAIN = ["legal", "finance"]
OBLIGATION_KINDS = ("Deliverable", "Payment", "Milestone", "Compliance", "Renewal", "Other")
OBLIGATION_STATUSES = ("Open", "Done", "Waived")


def resume_status(contract) -> str:
    previous = contract["status_before_hold"]
    # An approval is cancelled when a contract is put on hold, so it resumes in Quoting.
    if not previous or previous in {"Under Review", "On Hold"} | TERMINAL_STATUSES:
        return "Quoting"
    return previous


def allowed_transitions(contract, has_pending_approval: bool) -> list[str]:
    status = contract["status"]
    allowed = set(MANUAL_TRANSITIONS.get(status, set()))
    if status == "On Hold":
        allowed.add(resume_status(contract))
    if status == "Under Review" and not has_pending_approval:
        # Legacy records can be Under Review without an approval request; let them go back.
        allowed.add("Quoting")
    return [s for s in CONTRACT_STATUSES if s in allowed]


def pending_approval(conn, contract_id: int):
    return conn.execute(
        "SELECT * FROM approval_requests WHERE contract_id=? AND status='Pending' ORDER BY id DESC LIMIT 1",
        (contract_id,),
    ).fetchone()


def close_approval(conn, request_id: int, status: str):
    now = utc_now()
    conn.execute("UPDATE approval_requests SET status=?, completed_at=? WHERE id=?", (status, now, request_id))
    conn.execute(
        "UPDATE approval_steps SET status='Skipped' WHERE request_id=? AND status IN ('Pending','Waiting')",
        (request_id,),
    )


def approval_detail(conn, request_id: int) -> dict:
    request = conn.execute(
        """SELECT r.*, u.name submitted_by_name FROM approval_requests r
           JOIN users u ON u.id = r.submitted_by WHERE r.id=?""",
        (request_id,),
    ).fetchone()
    steps = conn.execute(
        """SELECT s.*, u.name decided_by_name FROM approval_steps s
           LEFT JOIN users u ON u.id = s.decided_by
           WHERE s.request_id=? ORDER BY s.step_order""",
        (request_id,),
    ).fetchall()
    return {**dict(request), "steps": [dict(step) for step in steps]}


# --- notifications -------------------------------------------------------------------

def users_with_roles(conn, roles) -> list[int]:
    marks = ",".join("?" * len(roles))
    return [r["id"] for r in conn.execute(f"SELECT id FROM users WHERE active=1 AND role IN ({marks})", tuple(roles))]


def users_named(conn, *names) -> list[int]:
    wanted = list(dict.fromkeys(n.strip().lower() for n in names if n and n.strip()))
    if not wanted:
        return []
    marks = ",".join("?" * len(wanted))
    return [r["id"] for r in conn.execute(
        f"SELECT id FROM users WHERE active=1 AND LOWER(name) IN ({marks})", tuple(wanted))]


def contract_recipients(conn, *names) -> list[int]:
    """Users named as owner / renewal owner, or admins and procurement when nobody matches."""
    return users_named(conn, *names) or users_with_roles(conn, ("admin", "procurement"))


def notify(conn, user_ids, kind: str, title: str, body: str | None = None,
           contract_id: int | None = None, dedupe_key: str | None = None) -> int:
    """Create one notification per user. With a dedupe_key, a user gets it at most once."""
    created = 0
    for user_id in dict.fromkeys(user_ids):
        cursor = conn.execute(
            """INSERT OR IGNORE INTO notifications (user_id, kind, title, body, contract_id, dedupe_key, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (user_id, kind, title, body, contract_id, dedupe_key, utc_now()),
        )
        if cursor.rowcount:
            created += 1
            email = conn.execute("SELECT email FROM users WHERE id=?", (user_id,)).fetchone()
            if email:
                send_email(email["email"], title, body or title, conn)
    return created


def send_email(to: str, subject: str, body: str, conn=None):
    """Send an email when SMTP is configured. With `conn`, it is queued in that transaction and
    retried by the job worker; without, it is sent now and failures are only logged."""
    if not os.getenv("SMTP_HOST"):
        return
    if conn is not None:
        jobs.enqueue(conn, "email", {"to": to, "subject": subject, "body": body})
        return
    try:
        deliver_email(to, subject, body)
    except Exception:  # email is best effort; the in-app notification already exists
        log.exception("Could not send email to %s", to)


def deliver_email(to: str, subject: str, body: str):
    message = EmailMessage()
    message["From"] = os.getenv("SMTP_FROM", "clause@localhost")
    message["To"] = to
    message["Subject"] = f"[Clause] {subject}"
    message.set_content(body)
    with smtplib.SMTP(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587")), timeout=10) as smtp:
        if os.getenv("SMTP_STARTTLS", "1") == "1":
            smtp.starttls()
        if os.getenv("SMTP_USERNAME"):
            smtp.login(os.getenv("SMTP_USERNAME"), os.getenv("SMTP_PASSWORD", ""))
        smtp.send_message(message)


@jobs.handler("email", max_attempts=5)
def email_job(payload: dict, attempt: int):
    if os.getenv("SMTP_HOST"):
        deliver_email(payload["to"], payload["subject"], payload["body"])


# --- reminders -----------------------------------------------------------------------

def _bucket(days: int, thresholds) -> int | None:
    for limit in sorted(thresholds):
        if 0 <= days <= limit:
            return limit
    return None


def _days(value: str | None, today: date) -> int | None:
    if not value:
        return None
    try:
        return (date.fromisoformat(value[:10]) - today).days
    except ValueError:
        return None


def run_reminders(conn, today: date | None = None) -> int:
    """Create due-date notifications. Safe to run repeatedly: each reminder is deduplicated."""
    today = today or date.today()
    created = 0
    contracts = conn.execute(
        "SELECT * FROM contracts WHERE status NOT IN ('Completed','Cancelled')"
    ).fetchall()
    checks = (
        ("project_end_date", "expiry", (7, 30, 60), "ends"),
        ("renewal_date", "renewal", (7, 30), "is due for renewal"),
        ("renewal_notice_deadline", "renewal-notice", (7, 30), "has a renewal notice deadline"),
    )
    for contract in contracts:
        for column, kind, thresholds, verb in checks:
            value = contract[column]
            days = _days(value, today)
            bucket = _bucket(days, thresholds) if days is not None else None
            if bucket is None:
                continue
            when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
            created += notify(
                conn, contract_recipients(conn, contract["owner"], contract["renewal_owner"]), kind,
                f"{contract['title']} {verb} {when}",
                f"{contract['vendor_name'] or 'Unknown vendor'} · {column.replace('_', ' ')}: {value}",
                contract["id"], f"{kind}:{contract['id']}:{value}:{bucket}",
            )
    obligations = conn.execute(
        """SELECT o.*, c.title contract_title, c.owner contract_owner, c.renewal_owner
           FROM obligations o JOIN contracts c ON c.id = o.contract_id
           WHERE o.status='Open' AND o.due_date IS NOT NULL AND c.status NOT IN ('Completed','Cancelled')"""
    ).fetchall()
    for item in obligations:
        days = _days(item["due_date"], today)
        if days is None or days > 7:
            continue
        overdue = days < 0
        recipients = users_named(conn, item["owner"]) or contract_recipients(conn, item["contract_owner"], item["renewal_owner"])
        title = (f"Overdue: {item['title']} ({item['contract_title']})" if overdue
                 else f"{item['title']} ({item['contract_title']}) is due {'today' if days == 0 else f'in {days} days'}")
        created += notify(
            conn, recipients, "obligation", title,
            f"{item['kind']} for {item['contract_title']} · due {item['due_date']}",
            item["contract_id"], f"obligation:{item['id']}:{item['due_date']}:{'overdue' if overdue else 'soon'}",
        )
    return created
