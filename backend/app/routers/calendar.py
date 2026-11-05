"""Key dates as iCalendar (.ics): a personal subscription feed for Outlook / Google, and one-off downloads.

Each user can create a secret feed URL. Calendar apps fetch it without signing in, so the token
in the URL is the only credential: only its SHA-256 hash is stored, creating a new URL revokes
the old one, and the feed holds only the contracts that user can see at the time it is fetched.
"""

import hashlib
import os
import secrets
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..access import visibility_sql
from ..auth import current_user, public_user
from ..database import add_audit, connection, utc_now
from .contracts import load_contract

router = APIRouter(prefix="/api")

FEED_DAYS_BACK = 30      # keep recent past events so a calendar does not drop them at once
FEED_DAYS_AHEAD = 730


def public_app_url() -> str:
    return os.getenv("PUBLIC_APP_URL", "http://localhost:3000").rstrip("/")


def public_api_url() -> str:
    """Where calendar apps reach the API: PUBLIC_API_URL, or the app's /backend-api proxy."""
    return os.getenv("PUBLIC_API_URL", "").rstrip("/") or f"{public_app_url()}/backend-api"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- iCalendar text ---------------------------------------------------------------------

def escape(text: str) -> str:
    return (str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def fold(line: str) -> str:
    """Fold to 75 octets per line (RFC 5545 3.1), never splitting a UTF-8 character."""
    out, current = [], b""
    for ch in line:
        encoded = ch.encode()
        if len(current) + len(encoded) > (75 if not out else 74):
            out.append(current.decode())
            current = b""
        current += encoded
    out.append(current.decode())
    return "\r\n ".join(out)


def calendar(events: list[dict], name: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Clause//Contract lifecycle//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{escape(name)}", "X-PUBLISHED-TTL:PT6H", "REFRESH-INTERVAL;VALUE=DURATION:PT6H"]
    for event in events:
        day = date.fromisoformat(event["date"])
        lines += ["BEGIN:VEVENT", f"UID:{event['uid']}", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
                  f"SUMMARY:{escape(event['summary'])}", f"DESCRIPTION:{escape(event['description'])}",
                  f"URL:{event['url']}", f"CATEGORIES:{escape(event['category'])}", "TRANSP:TRANSPARENT"]
        if event.get("alarm_days"):
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{escape(event['summary'])}",
                      f"TRIGGER:-P{event['alarm_days']}D", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


def _valid(value) -> bool:
    try:
        date.fromisoformat(str(value)[:10])
        return True
    except ValueError:
        return False


def key_dates(conn, contract_filter: str, params: tuple, window: bool) -> list[dict]:
    """Calendar events for the contracts matched by `contract_filter` (on alias c)."""
    app = public_app_url()
    low, high = (date.today() - timedelta(days=FEED_DAYS_BACK)).isoformat(), (date.today() + timedelta(days=FEED_DAYS_AHEAD)).isoformat()

    def keep(value) -> bool:
        return bool(value) and _valid(value) and (not window or low <= str(value)[:10] <= high)

    events = []
    contracts = conn.execute(
        f"SELECT * FROM contracts c WHERE c.status NOT IN ('Completed','Cancelled') AND {contract_filter}", params).fetchall()
    by_id = {c["id"]: c for c in contracts}
    for c in contracts:
        who = " · ".join(filter(None, [c["vendor_name"], c["reference_number"], f"Owner: {c['owner']}" if c["owner"] else None]))
        url = f"{app}/?contract={c['id']}"
        for column, label, category, alarm in (
            ("project_end_date", "Contract ends", "Expiry", 30),
            ("renewal_date", "Renewal date", "Renewal", 14),
            ("renewal_notice_deadline", "Renewal notice deadline", "Renewal", 14),
        ):
            if keep(c[column]):
                events.append({"uid": f"clause-{column.replace('_', '-')}-{c['id']}@clause", "date": c[column][:10],
                               "summary": f"{label}: {c['title']}", "description": f"{who}\nOpen in Clause: {url}",
                               "url": url, "category": category, "alarm_days": alarm})
    if not by_id:
        return events
    marks = ",".join("?" * len(by_id))
    for o in conn.execute(f"SELECT * FROM obligations WHERE status='Open' AND contract_id IN ({marks})", tuple(by_id)):
        if keep(o["due_date"]):
            c = by_id[o["contract_id"]]
            amount = f" · {o['currency'] or ''} {o['amount']:,.2f}".rstrip() if o["amount"] is not None else ""
            url = f"{app}/?contract={c['id']}"
            events.append({"uid": f"clause-obligation-{o['id']}@clause", "date": o["due_date"][:10],
                           "summary": f"{o['kind']}: {o['title']} ({c['title']})",
                           "description": f"{c['vendor_name'] or ''}{amount}{' · Owner: ' + o['owner'] if o['owner'] else ''}\nOpen in Clause: {url}",
                           "url": url, "category": "Obligation", "alarm_days": 3})
    for i in conn.execute(f"SELECT * FROM contract_invoices WHERE status IN ('Received','Approved') AND contract_id IN ({marks})",
                          tuple(by_id)):
        if keep(i["due_date"]):
            c = by_id[i["contract_id"]]
            url = f"{app}/?contract={c['id']}"
            events.append({"uid": f"clause-invoice-{i['id']}@clause", "date": i["due_date"][:10],
                           "summary": f"Invoice due: {i['invoice_number'] or '#' + str(i['id'])} ({c['title']})",
                           "description": f"{i['currency']} {i['amount']:,.2f} · {i['status']}\nOpen in Clause: {url}",
                           "url": url, "category": "Invoice", "alarm_days": 3})
    return sorted(events, key=lambda e: (e["date"], e["uid"]))


def ics_response(text: str, filename: str) -> Response:
    return Response(text, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'inline; filename="{filename}"', "Cache-Control": "private, max-age=300"})


# --- endpoints ---------------------------------------------------------------------------

@router.get("/calendar")
def feed_status(user: dict = Depends(current_user)):
    with connection() as conn:
        row = conn.execute("SELECT created_at FROM calendar_feeds WHERE user_id=?", (user["id"],)).fetchone()
    return {"subscribed": bool(row), "created_at": row["created_at"] if row else None}


@router.post("/calendar/token", status_code=201)
def create_feed(user: dict = Depends(current_user)):
    """Create (or replace) this user's secret feed URL. The URL is shown only now."""
    token = secrets.token_urlsafe(32)
    with connection() as conn:
        replaced = conn.execute("DELETE FROM calendar_feeds WHERE user_id=?", (user["id"],)).rowcount
        conn.execute("INSERT INTO calendar_feeds (user_id, token_hash, created_at) VALUES (?,?,?)",
                     (user["id"], _hash(token), utc_now()))
        add_audit(conn, "Calendar link replaced" if replaced else "Calendar link created", user["name"])
    url = f"{public_api_url()}/api/calendar/{token}.ics"
    return {"url": url, "webcal_url": "webcal://" + url.split("://", 1)[1], "subscribed": True}


@router.delete("/calendar/token")
def delete_feed(user: dict = Depends(current_user)):
    with connection() as conn:
        if conn.execute("DELETE FROM calendar_feeds WHERE user_id=?", (user["id"],)).rowcount:
            add_audit(conn, "Calendar link revoked", user["name"])
    return {"subscribed": False}


@router.get("/calendar/{token}.ics", include_in_schema=False)
def feed(token: str):
    with connection() as conn:
        row = conn.execute(
            "SELECT u.* FROM calendar_feeds f JOIN users u ON u.id=f.user_id WHERE f.token_hash=? AND u.active=1",
            (_hash(token),)).fetchone()
        if not row:
            raise HTTPException(404, "Calendar not found")
        user = public_user(row)
        vis, params = visibility_sql(user, "c")
        events = key_dates(conn, vis, params, window=True)
    return ics_response(calendar(events, f"Clause · {user['name']}"), "clause.ics")


@router.get("/contracts/{contract_id}/calendar.ics")
def contract_calendar(contract_id: int, _: dict = Depends(current_user)):
    with connection() as conn:
        contract = load_contract(conn, contract_id)
        events = key_dates(conn, "c.id=?", (contract_id,), window=False)
    if not events:
        raise HTTPException(404, "This contract has no upcoming dates.")
    return ics_response(calendar(events, contract["title"]), f"contract-{contract_id}.ics")
