"""The clause playbook and how the AI review checks contracts against it."""

import pytest

from backend.app.database import connection
from backend.app.routers import ai
from backend.app.services import llm

from .test_ai import upload_docx


@pytest.fixture()
def ai_on(monkeypatch):
    for key, value in {"AZURE_OPENAI_ENDPOINT": "https://x.invalid", "AZURE_OPENAI_API_KEY": "k", "AZURE_OPENAI_DEPLOYMENT": "d"}.items():
        monkeypatch.setenv(key, value)


def base_review(**extra):
    return {"summary": "Hosting.", "key_terms": [], "risks": [], "obligations": [], "missing_clauses": [],
            "field_mismatches": [], **extra}


def test_seeded_rules_and_management_permissions(client, users):
    listed = client.get("/api/playbook", headers=users["viewer"]).json()
    assert len(listed["rules"]) == 6 and "Liability" in listed["categories"]
    rule = {"title": "Audit rights", "category": "Other", "severity": "low", "standard": "We may audit once a year."}
    assert client.post("/api/playbook", headers=users["procurement"], json=rule).status_code == 403
    created = client.post("/api/playbook", headers=users["legal"], json=rule).json()
    assert created["active"] and created["created_by"] == "Lena Ong"
    assert client.post("/api/playbook", headers=users["legal"], json={**rule, "severity": "urgent"}).status_code == 422
    assert client.post("/api/playbook", headers=users["legal"], json={"title": "No standard"}).status_code == 422
    assert client.post("/api/playbook", headers=users["legal"], json={**rule, "category": "Cooking"}).status_code == 422

    off = client.patch(f"/api/playbook/{created['id']}", headers=users["legal"], json={"active": False}).json()
    assert off["active"] is False and off["title"] == "Audit rights"
    assert client.patch(f"/api/playbook/{created['id']}", headers=users["legal"], json={"title": " "}).status_code == 422
    assert client.delete(f"/api/playbook/{created['id']}", headers=users["legal"]).json() == {"deleted": True}
    assert client.delete(f"/api/playbook/{created['id']}", headers=users["legal"]).status_code == 404
    audit = [a["action"] for a in client.get("/api/audit", headers=users["admin"]).json()]
    assert {"Playbook rule added", "Playbook rule turned off", "Playbook rule deleted"} <= set(audit)


def test_review_checks_every_active_rule(client, users, ai_on, monkeypatch):
    rules = client.get("/api/playbook", headers=users["legal"]).json()["rules"]
    by_title = {r["title"]: r for r in rules}
    # Turn one rule off: it must not be sent or reported.
    client.patch(f"/api/playbook/{by_title['Governing law']['id']}", headers=users["legal"], json={"active": False})
    calls = []

    def structured(instructions, text, name, schema, **kwargs):
        calls.append({"instructions": instructions, "text": text, "schema": schema})
        return base_review(playbook=[
            {"rule_id": by_title["Liability cap"]["id"], "status": "deviates", "finding": "Customer liability unlimited",
             "quote": "Customer liability is unlimited", "suggested_language": "Each party's liability is capped at 12 months of fees."},
            {"rule_id": by_title["Payment terms"]["id"], "status": "meets", "finding": "Net 30", "quote": "within 30 days",
             "suggested_language": "should be dropped"},
            {"rule_id": by_title["Liability cap"]["id"], "status": "meets", "finding": "duplicate", "quote": None, "suggested_language": None},
            {"rule_id": 9999, "status": "missing", "finding": "invented rule", "quote": None, "suggested_language": None},
        ])
    monkeypatch.setattr(llm, "structured", structured)
    contract = upload_docx(client, users["procurement"], "Customer liability is unlimited.", "Invoices are payable within 30 days.")
    review = client.post(f"/api/contracts/{contract['id']}/ai/review", headers=users["legal"]).json()

    sent = calls[0]
    assert "COMPANY PLAYBOOK" in sent["text"] and "Liability cap" in sent["text"] and "Governing law" not in sent["text"]
    assert "playbook" in sent["schema"]["properties"] and "playbook:" in sent["instructions"]
    playbook = review["result"]["playbook"]
    assert [p["title"] for p in playbook] == ["Liability cap", "Data protection", "Termination for convenience",
                                              "Auto-renewal", "Payment terms"]  # high first, one per active rule
    liability = playbook[0]
    assert liability["status"] == "deviates" and liability["finding"] == "Customer liability unlimited"
    assert liability["severity"] == "high" and liability["standard"].startswith("The supplier's total liability")
    assert playbook[-1]["status"] == "meets" and playbook[-1]["suggested_language"] is None
    assert playbook[1]["status"] == "not_applicable" and "did not assess" in playbook[1]["finding"]

    audit = client.get(f"/api/audit?contract_id={contract['id']}", headers=users["admin"]).json()
    assert any("playbook: 1 deviation(s), 0 missing" in (a["details"] or "") for a in audit)
    with connection() as conn:
        summary = ai.review_brief(conn, review["id"])
    assert summary["playbook_counts"]["deviates"] == 1 and summary["playbook_issues"][0]["title"] == "Liability cap"


def test_review_without_active_rules_uses_plain_schema(client, users, ai_on, monkeypatch):
    for rule in client.get("/api/playbook", headers=users["legal"]).json()["rules"]:
        client.patch(f"/api/playbook/{rule['id']}", headers=users["legal"], json={"active": False})
    seen = []

    def structured(instructions, text, name, schema, **kwargs):
        seen.append(schema)
        return base_review()
    monkeypatch.setattr(llm, "structured", structured)
    contract = upload_docx(client, users["procurement"], "Plain contract.")
    review = client.post(f"/api/contracts/{contract['id']}/ai/review", headers=users["legal"]).json()
    assert seen[0] is ai.REVIEW_SCHEMA and "playbook" not in review["result"]


def test_playbook_schema_is_strict():
    schema = ai.PLAYBOOK_REVIEW_SCHEMA
    assert schema["additionalProperties"] is False and sorted(schema["required"]) == sorted(schema["properties"])
    item = schema["properties"]["playbook"]["items"]
    assert sorted(item["required"]) == sorted(item["properties"]) and item["additionalProperties"] is False
