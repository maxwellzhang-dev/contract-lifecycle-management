"""The background job queue, driven by hand in worker mode."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.app import jobs
from backend.app.database import connection

from .conftest import upload


@pytest.fixture()
def worker_mode(monkeypatch):
    monkeypatch.setenv("JOBS_MODE", "worker")
    monkeypatch.setenv("JOBS_ENABLED", "0")  # no threads: tests call run_due themselves


def make_due():
    with connection() as conn:
        conn.execute("UPDATE jobs SET run_after=? WHERE status='queued'", ("2000-01-01T00:00:00+00:00",))


def job_rows(kind=None):
    with connection() as conn:
        sql, params = ("SELECT * FROM jobs WHERE kind=? ORDER BY id", (kind,)) if kind else ("SELECT * FROM jobs ORDER BY id", ())
        return [dict(r) for r in conn.execute(sql, params)]


def test_upload_returns_before_extraction_and_job_fills_it_in(client, users, worker_mode):
    contract = upload(client, users["procurement"], name="hosting.pdf")
    assert contract["extraction_status"] == "Queued" and contract["title"] == "hosting" and contract["vendor_name"] is None
    assert client.post(f"/api/contracts/{contract['id']}/validate", headers=users["procurement"]).status_code == 409
    # Someone fills in a field while the AI is still working: it must be kept.
    client.patch(f"/api/contracts/{contract['id']}", headers=users["procurement"], json={"currency": "EUR"})

    assert jobs.run_due() == 1
    after = client.get(f"/api/contracts/{contract['id']}", headers=users["procurement"]).json()
    assert after["extraction_status"] == "Done"
    assert after["title"] == "Cloud Hosting Agreement" and after["vendor_name"] == "Acme Cloud"
    assert after["currency"] == "EUR" and after["status"] == "Quoting"
    assert job_rows("extract")[0]["status"] == "done"
    assert client.post(f"/api/contracts/{contract['id']}/validate", headers=users["procurement"]).status_code == 200


def test_failed_extraction_is_retried_with_backoff_then_falls_back(client, users, worker_mode, monkeypatch):
    from backend.app.routers import contracts
    calls = []

    def flaky(path, allow_fallback=True):
        calls.append(allow_fallback)
        if not allow_fallback:
            raise RuntimeError("Azure OpenAI returned 429")
        return {"title": "Fallback title", "extraction_note": "Local fallback used", "missing_fields": [], "low_confidence_fields": [],
                "field_confidences": {}, "extraction_confidence": 0.5}
    monkeypatch.setattr(contracts, "extract_contract", flaky)
    contract = upload(client, users["procurement"])

    jobs.run_due()
    job = job_rows("extract")[0]
    assert job["status"] == "queued" and job["attempts"] == 1 and "429" in job["last_error"]
    assert datetime.fromisoformat(job["run_after"]) > datetime.now(timezone.utc) + timedelta(seconds=20)
    assert jobs.run_due() == 0  # not due yet
    for _ in range(3):
        make_due()
        jobs.run_due()
    assert calls == [False, False, False, True]  # the last attempt accepts the local fallback
    job = job_rows("extract")[0]
    assert job["status"] == "done" and job["attempts"] == 4
    after = client.get(f"/api/contracts/{contract['id']}", headers=users["admin"]).json()
    assert after["extraction_status"] == "Done" and after["title"] == "Fallback title"


def test_jobs_are_claimed_once_and_stale_ones_recover(client, users, worker_mode):
    upload(client, users["procurement"])
    first = jobs.claim("worker-a")
    assert first is not None and jobs.claim("worker-b") is None
    with connection() as conn:  # worker-a crashed long ago
        conn.execute("UPDATE jobs SET locked_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", first))
    assert jobs.claim("worker-b") == first


def test_ai_review_job_gives_up_and_records_failure(client, users, worker_mode, monkeypatch):
    from backend.app.services import llm
    from .conftest import validated
    for key, value in {"AZURE_OPENAI_ENDPOINT": "https://x.invalid", "AZURE_OPENAI_API_KEY": "k", "AZURE_OPENAI_DEPLOYMENT": "d"}.items():
        monkeypatch.setenv(key, value)

    def down(*args, **kwargs):
        raise llm.LLMUnavailable("Azure OpenAI returned 503")
    monkeypatch.setattr(llm, "structured", down)
    contract = upload(client, users["procurement"])
    jobs.run_due()
    validated(client, users, contract)
    client.post(f"/api/contracts/{contract['id']}/approvals", headers=users["procurement"], json={})
    request = client.get(f"/api/contracts/{contract['id']}/approvals", headers=users["legal"]).json()[0]
    assert request["ai_review_state"] == "running"
    for _ in range(3):
        make_due()
        jobs.run_due()
    job = job_rows("ai_review")[0]
    assert job["status"] == "failed" and job["attempts"] == 3
    request = client.get(f"/api/contracts/{contract['id']}/approvals", headers=users["legal"]).json()[0]
    assert request["ai_review_state"] == "none" and request["status"] == "Pending"
    audit = client.get(f"/api/audit?contract_id={contract['id']}", headers=users["admin"]).json()
    assert any(a["action"] == "AI review failed" for a in audit)

    # An admin can see and retry it.
    listed = client.get("/api/jobs?status=failed", headers=users["admin"]).json()
    assert listed["counts"]["failed"] == 1 and listed["items"][0]["label"] == "AI review for approvers"
    assert client.get("/api/jobs", headers=users["legal"]).status_code == 403
    retried = client.post(f"/api/jobs/{job['id']}/retry", headers=users["admin"]).json()
    assert retried["status"] == "queued" and retried["attempts"] == 0
    assert client.post(f"/api/jobs/{job['id']}/retry", headers=users["admin"]).status_code == 409


def test_emails_are_queued_and_retried(client, users, worker_mode, monkeypatch):
    from backend.app import workflow
    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    sent, failures = [], [1]

    def deliver(to, subject, body):
        if failures:
            failures.pop()
            raise OSError("connection refused")
        sent.append((to, subject))
    monkeypatch.setattr(workflow, "deliver_email", deliver)
    contract = upload(client, users["procurement"])
    jobs.run_due()
    client.post(f"/api/contracts/{contract['id']}/comments", headers=users["legal"], json={"body": "@Priya Nair please check"})
    email = job_rows("email")[0]
    assert json.loads(email["payload"])["to"] == "finance@clause.local"
    jobs.run_due()
    assert job_rows("email")[0]["status"] == "queued" and sent == []
    make_due()
    jobs.run_due()
    assert job_rows("email")[0]["status"] == "done" and sent[0][0] == "finance@clause.local"
