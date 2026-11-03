import io

from docx import Document


def test_seeded_templates_and_permissions(client, users):
    templates = client.get("/api/templates", headers=users["viewer"]).json()
    assert {t["name"] for t in templates} == {"Professional Services Agreement", "Software Subscription Order Form"}
    assert "vendor_name" in templates[0]["placeholders"]
    body = {"name": "NDA", "body": "Between {{vendor_name}} and us."}
    assert client.post("/api/templates", json=body, headers=users["procurement"]).status_code == 403
    assert client.post("/api/templates", json=body, headers=users["legal"]).status_code == 201


def test_template_validation(client, users):
    bad = client.post("/api/templates", json={"name": "X", "body": "Hello {{nonsense}}"}, headers=users["legal"])
    assert bad.status_code == 422 and "nonsense" in bad.json()["detail"]
    bad = client.post("/api/templates", json={"name": "X", "body": "ok", "defaults": {"status": "Completed"}}, headers=users["legal"])
    assert bad.status_code == 422


def test_create_from_template_generates_docx(client, users):
    template = client.post("/api/templates", headers=users["legal"], json={
        "name": "Mini", "body": "# Parties\n{{vendor_name}} for {{currency}} {{one_time_amount}}\n\nContact: {{vendor_contact}}",
        "defaults": {"currency": "SGD", "department": "Legal"}}).json()
    created = client.post("/api/contracts", headers=users["procurement"], json={
        "template_id": template["id"], "title": "Mini deal", "vendor_name": "Acme", "one_time_amount": 12500,
        "status": "Completed"})
    assert created.status_code == 201, created.text
    contract = created.json()
    assert contract["status"] == "Quoting"  # status from the client is ignored
    assert contract["currency"] == "SGD" and contract["department"] == "Legal"  # template defaults
    assert contract["validation_status"] == "Validated"
    assert contract["file_name"] == "Mini deal.docx"
    file = client.get(f"/api/contracts/{contract['id']}/file", headers=users["viewer"])
    text = "\n".join(p.text for p in Document(io.BytesIO(file.content)).paragraphs)
    assert "Acme for SGD 12,500.00" in text and "Contact: [to be confirmed]" in text
    versions = client.get(f"/api/contracts/{contract['id']}/versions", headers=users["viewer"]).json()
    assert versions[0]["note"] == "Created from template “Mini”"
    # A template-created contract can go straight to approval.
    assert client.post(f"/api/contracts/{contract['id']}/approvals", json={}, headers=users["procurement"]).status_code == 201


def test_blank_contract(client, users):
    assert client.post("/api/contracts", json={"vendor_name": "No title"}, headers=users["procurement"]).status_code == 422
    assert client.post("/api/contracts", json={"title": "x"}, headers=users["finance"]).status_code == 403
    contract = client.post("/api/contracts", json={"title": "Handshake deal", "tags": ["Manual"]}, headers=users["procurement"]).json()
    assert contract["file_name"] is None and contract["tags"] == ["Manual"]
    assert client.get(f"/api/contracts/{contract['id']}/versions", headers=users["viewer"]).json() == []


def test_inactive_template_is_hidden_and_unusable(client, users):
    template = client.get("/api/templates", headers=users["legal"]).json()[0]
    client.patch(f"/api/templates/{template['id']}", json={"active": False}, headers=users["legal"])
    assert template["id"] not in [t["id"] for t in client.get("/api/templates", headers=users["procurement"]).json()]
    assert template["id"] in [t["id"] for t in client.get("/api/templates?include_inactive=true", headers=users["legal"]).json()]
    response = client.post("/api/contracts", json={"title": "x", "template_id": template["id"]}, headers=users["procurement"])
    assert response.status_code == 404
