"""Post workflow events to a Microsoft Teams channel.

Set `TEAMS_WEBHOOK_URL` to the URL of a Teams Workflows flow ("Post to a channel when a webhook
request is received") or a legacy incoming webhook. Each event that notifies people in the app
(approvals, signatures, reminders, spend alerts, status changes) is also posted once to the
channel as an Adaptive Card with a button back to the contract. Posts go through the job queue,
so a Teams outage only delays them. `TEAMS_EVENTS` limits which kinds are posted.

Everyone in the channel sees the posted titles, whatever their access in Clause, so point the
webhook at a channel for the people who manage contracts.
"""

import os

import httpx

from .. import jobs

DEFAULT_EVENTS = ("approval", "signature", "spend", "expiry", "renewal", "renewal-notice", "obligation", "invoice", "status")
COLOURS = {"approval": "accent", "signature": "good", "spend": "attention", "expiry": "warning", "renewal": "warning",
           "renewal-notice": "warning", "obligation": "default", "invoice": "default", "status": "default"}


def webhook_url() -> str:
    return os.getenv("TEAMS_WEBHOOK_URL", "").strip()


def configured() -> bool:
    return bool(webhook_url())


def events() -> set[str]:
    raw = os.getenv("TEAMS_EVENTS", "")
    return {e.strip() for e in raw.split(",") if e.strip()} if raw.strip() else set(DEFAULT_EVENTS)


def post(conn, kind: str, title: str, body: str | None, contract_id: int | None):
    """Queue a channel post in the caller's transaction, when Teams is set up for this kind."""
    if configured() and kind in events():
        jobs.enqueue(conn, "teams", {"kind": kind, "title": title, "body": body, "contract_id": contract_id})


def card(payload: dict, contract) -> dict:
    app = os.getenv("PUBLIC_APP_URL", "http://localhost:3000").rstrip("/")
    facts = []
    if contract:
        for label, key in (("Vendor", "vendor_name"), ("Owner", "owner"), ("Status", "status"), ("Reference", "reference_number")):
            if contract[key]:
                facts.append({"title": label, "value": str(contract[key])})
    content = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": payload["kind"].replace("-", " ").upper(), "size": "Small", "weight": "Bolder",
             "color": COLOURS.get(payload["kind"], "default"), "spacing": "None"},
            {"type": "TextBlock", "text": payload["title"], "size": "Medium", "weight": "Bolder", "wrap": True},
            *([{"type": "TextBlock", "text": payload["body"], "wrap": True, "isSubtle": True}] if payload.get("body") else []),
            *([{"type": "FactSet", "facts": facts}] if facts else []),
        ],
        "actions": [{"type": "Action.OpenUrl", "title": "Open in Clause",
                     "url": f"{app}/?contract={payload['contract_id']}" if payload.get("contract_id") else app}],
    }
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None,
                                                 "content": content}]}


def client() -> httpx.Client:  # replaced in tests
    return httpx.Client(timeout=20)


@jobs.handler("teams", max_attempts=5)
def teams_job(payload: dict, attempt: int):
    url = webhook_url()
    if not url:
        return  # switched off since the post was queued
    from ..database import connection
    contract = None
    if payload.get("contract_id"):
        with connection() as conn:
            contract = conn.execute("SELECT * FROM contracts WHERE id=?", (payload["contract_id"],)).fetchone()
    with client() as http:
        response = http.post(url, json=card(payload, contract))
    if response.status_code >= 300:
        raise RuntimeError(f"Teams returned {response.status_code}: {response.text[:200]}")
