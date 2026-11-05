"""iCalendar feeds and Microsoft Teams channel posts."""

from datetime import date, timedelta

import httpx
import pytest

from backend.app.routers import calendar as cal
from backend.app.services import teams

from .conftest import upload, validated


def make(client, users, **values):
    soon = (date.today() + timedelta(days=40)).isoformat()
    body = {"title": "Hosting, EU; tier 1", "vendor_name": "Acme", "currency": "SGD", "one_time_amount": 1000,
            "project_end_date": soon, "renewal_notice_deadline": (date.today() + timedelta(days=10)).isoformat(), **values}
    response = client.post("/api/contracts", headers=users["procurement"], json=body)
    assert response.status_code == 201, response.text
    return response.json()


def feed_path(url: str) -> str:
    return url.split("/backend-api", 1)[1]


def test_personal_feed_lists_visible_key_dates(client, users, monkeypatch):
    monkeypatch.setenv("PUBLIC_APP_URL", "https://clause.example.com")
    contract = make(client, users)
    client.post(f"/api/contracts/{contract['id']}/obligations", headers=users["finance"],
                json={"title": "Instalment", "kind": "Payment", "due_date": (date.today() + timedelta(days=5)).isoformat(),
                      "amount": 250, "currency": "SGD"})
    client.post(f"/api/contracts/{contract['id']}/invoices", headers=users["finance"],
                json={"invoice_number": "INV-7", "amount": 100, "due_date": (date.today() + timedelta(days=12)).isoformat()})
    make(client, users, title="Old", project_end_date=(date.today() - timedelta(days=400)).isoformat(), renewal_notice_deadline=None)
    make(client, users, title="Finished", project_end_date=None, renewal_notice_deadline=None)

    assert client.get("/api/calendar", headers=users["viewer"]).json()["subscribed"] is False
    created = client.post("/api/calendar/token", headers=users["viewer"]).json()
    assert created["url"].startswith("https://clause.example.com/backend-api/api/calendar/") and created["url"].endswith(".ics")
    assert created["webcal_url"].startswith("webcal://clause.example.com/")
    client.cookies.clear()
    response = client.get(feed_path(created["url"]))  # no sign-in: calendar apps fetch it directly
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/calendar")
    text = response.text
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert "SUMMARY:Contract ends: Hosting\\, EU\\; tier 1" in text
    assert "SUMMARY:Renewal notice deadline: Hosting\\, EU\\; tier 1" in text and "TRIGGER:-P14D" in text
    assert "SUMMARY:Payment: Instalment (Hosting\\, EU\\; tier 1)" in text
    assert "Invoice due: INV-7" in text
    assert "Old" not in text  # outside the feed window
    assert f"URL:https://clause.example.com/?contract={contract['id']}" in text
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    assert text.count("BEGIN:VEVENT") == 4

    # Rotating the link revokes the old one; deleting revokes it too.
    rotated = client.post("/api/calendar/token", headers=users["viewer"]).json()
    assert client.get(feed_path(created["url"])).status_code == 404
    assert client.get(feed_path(rotated["url"])).status_code == 200
    client.delete("/api/calendar/token", headers=users["viewer"])
    assert client.get(feed_path(rotated["url"])).status_code == 404
    audit = [a["action"] for a in client.get("/api/audit", headers=users["admin"]).json()]
    assert {"Calendar link created", "Calendar link replaced", "Calendar link revoked"} <= set(audit)


def test_feed_follows_access_scope_and_deactivation(client, users):
    make(client, users, title="Legal matter", department="Legal")
    finance = next(u for u in client.get("/api/users", headers=users["admin"]).json() if u["email"] == "finance@clause.local")
    client.patch(f"/api/users/{finance['id']}", headers=users["admin"], json={"department": "Finance", "access_scope": "department"})
    url = client.post("/api/calendar/token", headers=users["finance"]).json()["url"]
    assert "Legal matter" not in client.get(feed_path(url)).text
    client.patch(f"/api/users/{finance['id']}", headers=users["admin"], json={"access_scope": "all"})
    assert "Legal matter" in client.get(feed_path(url)).text
    client.patch(f"/api/users/{finance['id']}", headers=users["admin"], json={"active": False})
    assert client.get(feed_path(url)).status_code == 404


def test_single_contract_download(client, users):
    contract = make(client, users)
    response = client.get(f"/api/contracts/{contract['id']}/calendar.ics", headers=users["viewer"])
    assert response.status_code == 200 and response.text.count("BEGIN:VEVENT") == 2
    assert f'filename="contract-{contract["id"]}.ics"' in response.headers["content-disposition"]
    bare = make(client, users, title="No dates", project_end_date=None, renewal_notice_deadline=None)
    assert client.get(f"/api/contracts/{bare['id']}/calendar.ics", headers=users["viewer"]).status_code == 404
    client.cookies.clear()  # signing in set a session cookie on this test client
    assert client.get(f"/api/contracts/{contract['id']}/calendar.ics").status_code == 401


def test_folding_keeps_multibyte_characters_whole():
    line = "SUMMARY:" + "合同到期提醒" * 20
    folded = cal.fold(line)
    parts = folded.split("\r\n ")
    assert "".join(parts) == line and all(len(p.encode()) <= 75 for p in parts)


@pytest.fixture()
def teams_posts(monkeypatch):
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://example.webhook.office.com/hook")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://clause.example.com")
    posts, failures = [], []

    def handler(request: httpx.Request):
        if failures:
            failures.pop()
            return httpx.Response(500, text="busy")
        posts.append(request)
        return httpx.Response(202)
    monkeypatch.setattr(teams, "client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return posts, failures


def test_workflow_events_are_posted_to_teams(client, users, teams_posts):
    import json
    posts, _ = teams_posts
    contract = upload(client, users["procurement"])
    validated(client, users, contract)
    client.post(f"/api/contracts/{contract['id']}/approvals", headers=users["procurement"], json={})
    assert len(posts) == 1
    card = json.loads(posts[0].content)
    content = card["attachments"][0]["content"]
    assert card["type"] == "message" and content["type"] == "AdaptiveCard"
    assert content["body"][1]["text"] == "Approval needed: Cloud Hosting Agreement"
    assert {"title": "Vendor", "value": "Acme Cloud"} in content["body"][3]["facts"]
    assert content["actions"][0]["url"] == f"https://clause.example.com/?contract={contract['id']}"

    # Mentions stay personal.
    client.post(f"/api/contracts/{contract['id']}/comments", headers=users["legal"], json={"body": "@Priya Nair look"})
    assert len(posts) == 1


def test_teams_events_filter_and_retry(client, users, teams_posts, monkeypatch):
    from backend.app import jobs
    posts, failures = teams_posts
    monkeypatch.setenv("TEAMS_EVENTS", "spend")
    contract = upload(client, users["procurement"])
    validated(client, users, contract)
    client.post(f"/api/contracts/{contract['id']}/approvals", headers=users["procurement"], json={})
    assert posts == []  # approvals are not in TEAMS_EVENTS

    monkeypatch.setenv("JOBS_MODE", "worker")
    monkeypatch.setenv("JOBS_ENABLED", "0")
    failures.append(1)
    client.post(f"/api/contracts/{contract['id']}/invoices", headers=users["finance"], json={"amount": 119000})
    jobs.run_due()
    assert posts == []
    from backend.app.database import connection
    with connection() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE kind='teams'").fetchone()
        assert job["status"] == "queued" and "500" in job["last_error"]
        conn.execute("UPDATE jobs SET run_after='2000-01-01T00:00:00+00:00' WHERE id=?", (job["id"],))
    jobs.run_due()
    assert len(posts) == 1 and "99%" in posts[0].content.decode()


def test_integration_status_and_test_message(client, users, teams_posts):
    posts, _ = teams_posts
    status = client.get("/api/integrations", headers=users["admin"]).json()
    assert status["teams"]["configured"] and "approval" in status["teams"]["events"]
    assert client.get("/api/integrations", headers=users["legal"]).status_code == 403
    assert client.post("/api/integrations/teams/test", headers=users["admin"]).json() == {"queued": True}
    assert "Clause is connected to this channel" in posts[0].content.decode()


def test_teams_off_without_webhook(client, users, monkeypatch):
    monkeypatch.delenv("TEAMS_WEBHOOK_URL", raising=False)
    assert client.post("/api/integrations/teams/test", headers=users["admin"]).status_code == 503
