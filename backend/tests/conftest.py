import os

import pytest
from fastapi.testclient import TestClient

PASSWORD = "ChangeMe123!"


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("REMINDERS_ENABLED", "0")
    monkeypatch.setenv("SEED_DEMO_DATA", "0")
    monkeypatch.setenv("CLAUSE_SEED_PASSWORD", PASSWORD)
    monkeypatch.delenv("SMTP_HOST", raising=False)

    from backend.app.routers import contracts

    def fake_extract(path):
        return {
            "title": "Cloud Hosting Agreement", "vendor_name": "Acme Cloud", "currency": "SGD",
            "one_time_amount": 120000, "project_start_date": "2026-01-01", "project_end_date": "2026-12-31",
            "status": "In Progress",  # must be ignored: uploads always start in Quoting
            "extraction_confidence": 0.9, "missing_fields": [], "low_confidence_fields": [],
            "field_confidences": {}, "extraction_note": "test stub",
        }

    monkeypatch.setattr(contracts, "extract_contract", fake_extract)
    return tmp_path


@pytest.fixture()
def client(app_env):
    from backend.app.main import app

    with TestClient(app) as test_client:
        yield test_client


def login(client, email):
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture()
def users(client):
    return {
        "admin": login(client, "maxwell@clause.local"),
        "legal": login(client, "legal@clause.local"),
        "procurement": login(client, "procurement@clause.local"),
        "finance": login(client, "finance@clause.local"),
        "viewer": login(client, "viewer@clause.local"),
    }


def upload(client, headers, name="contract.pdf", content=b"%PDF-1.4 test"):
    response = client.post(
        "/api/contracts/upload", headers=headers,
        files={"file": (name, content, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def contract(client, users):
    return upload(client, users["procurement"])


def validated(client, users, contract):
    response = client.post(f"/api/contracts/{contract['id']}/validate", headers=users["procurement"])
    assert response.status_code == 200, response.text
    return response.json()
