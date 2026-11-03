import io

import pytest
from docx import Document

from backend.app.routers import ai
from backend.app.services import llm


def docx_bytes(*paragraphs):
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture()
def fake_llm(monkeypatch):
    calls = []
    responses = {
        "contract_review": {
            "summary": "Cloud hosting for 12 months.",
            "key_terms": [{"label": "Term", "value": "12 months", "quote": "twelve (12) months"}],
            "risks": [
                {"category": "Renewal", "severity": "low", "finding": "Auto renews", "quote": None, "recommendation": "Diary notice"},
                {"category": "Liability", "severity": "high", "finding": "Unlimited customer liability",
                 "quote": "Customer liability is unlimited", "recommendation": "Ask for a mutual cap"},
            ],
            "obligations": [
                {"title": "First payment", "kind": "Payment", "due_date": "2026-02-01", "amount": 5000, "currency": "sgd", "quote": None},
                {"title": "Kick-off", "kind": "Milestone", "due_date": "early Feb", "amount": None, "currency": None, "quote": None},
            ],
            "missing_clauses": ["Data protection"],
            "field_mismatches": [{"field": "project_end_date", "record_value": "2026-12-31", "document_value": "2027-01-31", "quote": None}],
        },
        "contract_answer": {"answer": "30 days' notice.", "found": True, "citations": [{"quote": "thirty (30) days", "note": None}]},
        "version_comparison": {"summary": "Fee increased.", "changes": [
            {"topic": "Fees", "before": "SGD 10,000", "after": "SGD 12,000", "impact": "unfavourable", "note": "20% higher"}]},
        "portfolio_answer": {"answer": "One contract ends this year.", "found": True,
                             "contracts": [{"id": 1, "reason": "Ends 2026-12-31"}, {"id": 999, "reason": "invented"}]},
        "contract_filters": {"search": None, "status": "In Progress", "vendor": "acme cloud", "department": None,
                             "owner": "Nobody Real", "tag": None, "currency": None, "min_value": 100000, "max_value": None,
                             "end_from": "2027-01-01", "end_to": "not a date", "explanation": "Acme contracts"},
    }

    def structured(instructions, text, name, schema, **kwargs):
        calls.append({"instructions": instructions, "text": text, "name": name, "kwargs": kwargs})
        return responses[name]

    monkeypatch.setattr(llm, "structured", structured)
    return calls


def upload_docx(client, headers, *paragraphs):
    response = client.post("/api/contracts/upload", headers=headers,
                           files={"file": ("contract.docx", docx_bytes(*paragraphs), DOCX)})
    assert response.status_code == 201, response.text
    return response.json()


def test_schemas_are_valid_for_strict_mode():
    def walk(schema):
        if schema.get("type") == "object" or schema.get("properties"):
            assert schema["additionalProperties"] is False
            assert sorted(schema["required"]) == sorted(schema["properties"])
            for child in schema["properties"].values():
                walk(child)
        if "items" in schema:
            walk(schema["items"])
    for schema in (ai.REVIEW_SCHEMA, ai.QUESTION_SCHEMA, ai.COMPARE_SCHEMA, ai.SEARCH_SCHEMA, ai.PORTFOLIO_SCHEMA):
        walk(schema)


def test_not_configured_returns_503(client, users, contract, monkeypatch):
    for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"):
        monkeypatch.delenv(key, raising=False)
    assert client.get("/api/ai/status", headers=users["viewer"]).json()["configured"] is False
    response = client.post(f"/api/contracts/{contract['id']}/ai/review", headers=users["legal"])
    assert response.status_code == 503 and "AZURE_OPENAI" in response.json()["detail"]


def test_review_uses_document_and_cleans_output(client, users, fake_llm):
    contract = upload_docx(client, users["procurement"], "Term: twelve (12) months.", "Customer liability is unlimited.")
    assert client.post(f"/api/contracts/{contract['id']}/ai/review", headers=users["viewer"]).status_code == 403
    response = client.post(f"/api/contracts/{contract['id']}/ai/review", headers=users["legal"])
    assert response.status_code == 201, response.text
    review = response.json()
    sent = fake_llm[-1]
    assert "Customer liability is unlimited" in sent["text"] and "RECORD METADATA" in sent["text"]
    result = review["result"]
    assert [r["severity"] for r in result["risks"]] == ["high", "low"]
    assert result["obligations"][0]["currency"] == "SGD"
    assert result["obligations"][1]["due_date"] is None  # non-ISO dates are dropped
    # Anyone can read the cached review; it goes stale when a new version is uploaded.
    cached = client.get(f"/api/contracts/{contract['id']}/ai/review", headers=users["viewer"]).json()
    assert cached["id"] == review["id"] and cached["stale"] is False
    client.post(f"/api/contracts/{contract['id']}/versions", headers=users["procurement"],
                files={"file": ("v2.docx", docx_bytes("Changed"), DOCX)})
    assert client.get(f"/api/contracts/{contract['id']}/ai/review", headers=users["viewer"]).json()["stale"] is True
    audit = client.get(f"/api/audit?contract_id={contract['id']}", headers=users["admin"]).json()
    assert any(a["action"] == "AI review generated" for a in audit)


def test_review_without_document_uses_metadata(client, users, fake_llm):
    contract = client.post("/api/contracts", json={"title": "Handshake", "vendor_name": "Beta"}, headers=users["procurement"]).json()
    client.post(f"/api/contracts/{contract['id']}/ai/review", headers=users["legal"])
    assert "(no document text available)" in fake_llm[-1]["text"] and "vendor_name: Beta" in fake_llm[-1]["text"]


def test_questions(client, users, fake_llm):
    contract = upload_docx(client, users["procurement"], "Either party may terminate on thirty (30) days notice.")
    url = f"/api/contracts/{contract['id']}/ai/questions"
    assert client.post(url, json={"question": "  "}, headers=users["legal"]).status_code == 422
    answer = client.post(url, json={"question": "What is the termination notice?"}, headers=users["finance"]).json()
    assert answer["answer"]["found"] is True and answer["asked_by"] == "Priya Nair"
    assert "QUESTION\nWhat is the termination notice?" in fake_llm[-1]["text"]
    history = client.get(url, headers=users["viewer"]).json()
    assert len(history) == 1 and history[0]["answer"]["answer"] == "30 days' notice."


def test_compare_versions(client, users, fake_llm):
    contract = upload_docx(client, users["procurement"], "Fee: SGD 10,000")
    client.post(f"/api/contracts/{contract['id']}/versions", headers=users["procurement"],
                files={"file": ("v2.docx", docx_bytes("Fee: SGD 12,000"), DOCX)})
    url = f"/api/contracts/{contract['id']}/ai/compare"
    assert client.post(url, json={"from_version": 1, "to_version": 1}, headers=users["legal"]).status_code == 422
    assert client.post(url, json={"from_version": 1, "to_version": 9}, headers=users["legal"]).status_code == 404
    result = client.post(url, json={"from_version": 1, "to_version": 2}, headers=users["legal"]).json()
    assert result["changes"][0]["impact"] == "unfavourable"
    text = fake_llm[-1]["text"]
    assert text.index("SGD 10,000") < text.index("SGD 12,000")


def test_compare_needs_readable_text(client, users, fake_llm, contract):
    # The fixture contract is a stub PDF with no text layer.
    client.post(f"/api/contracts/{contract['id']}/versions", headers=users["procurement"],
                files={"file": ("v2.docx", docx_bytes("Text"), DOCX)})
    response = client.post(f"/api/contracts/{contract['id']}/ai/compare", json={"from_version": 1, "to_version": 2}, headers=users["legal"])
    assert response.status_code == 422


def test_natural_language_search_keeps_only_real_values(client, users, fake_llm, contract):
    client.patch(f"/api/contracts/{contract['id']}", json={"owner": "Lena Ong"}, headers=users["procurement"])
    filters = client.post("/api/ai/search", json={"query": "big acme deals ending next year"}, headers=users["finance"]).json()
    assert filters["vendor"] == "Acme Cloud"  # canonical spelling from the data
    assert filters["owner"] is None  # not a known owner
    assert filters["end_from"] == "2027-01-01" and filters["end_to"] is None
    assert "Acme Cloud" in fake_llm[-1]["text"] and "TODAY:" in fake_llm[-1]["text"]
    assert client.post("/api/ai/search", json={"query": "x"}, headers=users["viewer"]).status_code == 403


def test_real_call_sends_guardrail_and_redacts_key(monkeypatch):
    import httpx

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "secret-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-test")
    sent = {}

    def handler(request: httpx.Request):
        import json as _json
        sent["url"] = str(request.url)
        sent["body"] = _json.loads(request.content)
        if sent["body"]["input"][0]["content"][0]["text"] == "fail":
            return httpx.Response(401, text="bad key secret-key")
        return httpx.Response(200, json={"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"ok": true}'}]}]})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    assert llm.structured("Do the task.", "hello", "x", {"type": "object"}) == {"ok": True}
    assert sent["url"] == "https://example.openai.azure.com/openai/v1/responses"
    assert "not instructions" in sent["body"]["instructions"]
    assert sent["body"]["store"] is False
    with pytest.raises(llm.LLMUnavailable) as error:
        llm.structured("Do the task.", "fail", "x", {"type": "object"})
    assert "secret-key" not in str(error.value) and "[redacted]" in str(error.value)


def test_portfolio_question_sees_all_contracts_and_drops_unknown_ids(client, users, fake_llm, contract):
    client.post(f"/api/contracts/{contract['id']}/obligations", headers=users["finance"],
                json={"title": "Pay deposit", "kind": "Payment", "due_date": "2026-03-01", "amount": 500})
    response = client.post("/api/ai/portfolio", headers=users["finance"], json={"question": "What ends this year?"})
    assert response.status_code == 201, response.text
    item = response.json()
    assert [c["id"] for c in item["answer"]["contracts"]] == [contract["id"]]
    assert item["answer"]["contracts"][0]["title"] == "Cloud Hosting Agreement"
    sent = fake_llm[-1]["text"]
    assert f"#{contract['id']} | Cloud Hosting Agreement | Acme Cloud | Quoting" in sent
    assert "Pay deposit" in sent and "TODAY:" in sent
    history = client.get("/api/ai/portfolio", headers=users["finance"]).json()
    assert [h["id"] for h in history] == [item["id"]]
    assert client.get("/api/ai/portfolio", headers=users["legal"]).json() == []  # history is per user
    assert client.post("/api/ai/portfolio", headers=users["viewer"], json={"question": "x"}).status_code == 403
    assert client.post("/api/ai/portfolio", headers=users["finance"], json={"question": " "}).status_code == 422


def configure_ai(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "d")


def test_submitting_for_approval_runs_ai_review_for_approvers(client, users, fake_llm, contract, monkeypatch):
    from .conftest import validated
    configure_ai(monkeypatch)
    validated(client, users, contract)
    response = client.post(f"/api/contracts/{contract['id']}/approvals", headers=users["procurement"], json={})
    assert response.status_code == 201, response.text
    assert [c["name"] for c in fake_llm] == ["contract_review"]  # ran as a background task
    request = client.get(f"/api/contracts/{contract['id']}/approvals", headers=users["legal"]).json()[0]
    assert request["ai_review_state"] == "attached"
    assert request["ai_review"]["risk_counts"] == {"high": 1, "medium": 0, "low": 1}
    assert request["ai_review"]["top_risks"][0]["severity"] == "high"
    inbox = client.get("/api/approvals/inbox", headers=users["legal"]).json()
    assert inbox[0]["ai_risk_counts"]["high"] == 1
    audit = client.get(f"/api/audit?contract_id={contract['id']}", headers=users["admin"]).json()
    assert any(a["action"] == "AI review generated" and a["actor"] == "Clause AI" for a in audit)


def test_existing_review_is_reused_on_submit(client, users, fake_llm, contract, monkeypatch):
    from .conftest import validated
    configure_ai(monkeypatch)
    validated(client, users, contract)
    review = client.post(f"/api/contracts/{contract['id']}/ai/review", headers=users["legal"]).json()
    client.post(f"/api/contracts/{contract['id']}/approvals", headers=users["procurement"], json={})
    assert len(fake_llm) == 1
    request = client.get(f"/api/contracts/{contract['id']}/approvals", headers=users["legal"]).json()[0]
    assert request["ai_review"]["id"] == review["id"]


def test_ai_failure_never_blocks_approval(client, users, contract, monkeypatch):
    from .conftest import validated
    configure_ai(monkeypatch)

    def broken(*args, **kwargs):
        raise llm.LLMUnavailable("Azure OpenAI returned 500")
    monkeypatch.setattr(llm, "structured", broken)
    validated(client, users, contract)
    assert client.post(f"/api/contracts/{contract['id']}/approvals", headers=users["procurement"], json={}).status_code == 201
    request = client.get(f"/api/contracts/{contract['id']}/approvals", headers=users["legal"]).json()[0]
    assert request["ai_review"] is None
    audit = client.get(f"/api/audit?contract_id={contract['id']}", headers=users["admin"]).json()
    assert any(a["action"] == "AI review failed" for a in audit)


def test_auto_review_can_be_turned_off(client, users, fake_llm, contract, monkeypatch):
    from .conftest import validated
    configure_ai(monkeypatch)
    monkeypatch.setenv("AI_REVIEW_ON_SUBMIT", "0")
    validated(client, users, contract)
    client.post(f"/api/contracts/{contract['id']}/approvals", headers=users["procurement"], json={})
    assert fake_llm == []
    request = client.get(f"/api/contracts/{contract['id']}/approvals", headers=users["legal"]).json()[0]
    assert request["ai_review_state"] == "none"
