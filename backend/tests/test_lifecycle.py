from .conftest import upload, validated


def patch(client, headers, contract_id, **values):
    return client.patch(f"/api/contracts/{contract_id}", json=values, headers=headers)


def submit(client, headers, contract_id, **body):
    return client.post(f"/api/contracts/{contract_id}/approvals", json=body, headers=headers)


def decide(client, headers, request_id, decision="approve", comment=None):
    return client.post(f"/api/approvals/{request_id}/decision", json={"decision": decision, "comment": comment}, headers=headers)


def test_upload_starts_in_quoting_even_if_extraction_says_otherwise(contract):
    assert contract["status"] == "Quoting"
    assert contract["allowed_transitions"] == ["On Hold", "Cancelled"]
    assert "file_path" not in contract


def test_status_cannot_skip_the_approval(client, users, contract):
    for target in ("Under Review", "Cleared to Start", "In Progress", "Completed"):
        response = patch(client, users["admin"], contract["id"], status=target)
        assert response.status_code == 409, target


def test_resaving_unchanged_status_is_not_a_transition(client, users, contract):
    response = patch(client, users["procurement"], contract["id"], status="Quoting", notes="only notes")
    assert response.status_code == 200
    assert response.json()["notes"] == "only notes"


def test_end_date_before_start_date_is_rejected(client, users, contract):
    response = patch(client, users["procurement"], contract["id"], project_end_date="2025-01-01")
    assert response.status_code == 422


def test_submission_requires_validation(client, users, contract):
    assert submit(client, users["procurement"], contract["id"]).status_code == 409


def test_full_approval_flow(client, users, contract):
    validated(client, users, contract)
    request = submit(client, users["procurement"], contract["id"], note="Please review").json()
    assert [s["status"] for s in request["steps"]] == ["Pending", "Waiting"]
    assert client.get(f"/api/contracts/{contract['id']}", headers=users["admin"]).json()["status"] == "Under Review"

    # Finance cannot jump ahead of legal; the submitter cannot approve.
    assert decide(client, users["finance"], request["id"]).status_code == 403
    assert decide(client, users["procurement"], request["id"]).status_code == 403
    # Terms are locked while approval is pending.
    assert patch(client, users["procurement"], contract["id"], one_time_amount=1).status_code == 409

    inbox = client.get("/api/approvals/inbox", headers=users["legal"]).json()
    assert [item["request_id"] for item in inbox] == [request["id"]]

    assert decide(client, users["legal"], request["id"]).status_code == 200
    assert decide(client, users["legal"], request["id"]).status_code == 403  # step is finance's now
    result = decide(client, users["finance"], request["id"]).json()
    assert result["status"] == "Approved"

    contract_now = client.get(f"/api/contracts/{contract['id']}", headers=users["admin"]).json()
    assert contract_now["status"] == "Cleared to Start"
    assert contract_now["allowed_transitions"] == ["In Progress", "On Hold", "Cancelled"]
    assert patch(client, users["procurement"], contract["id"], status="In Progress").status_code == 200
    assert patch(client, users["procurement"], contract["id"], status="Completed").status_code == 200
    assert patch(client, users["admin"], contract["id"], status="In Progress").status_code == 409  # terminal

    notes = client.get("/api/notifications", headers=users["procurement"]).json()
    assert any(item["title"].startswith("Approved:") for item in notes["items"])


def test_admin_cannot_approve_two_steps_alone(client, users, contract):
    validated(client, users, contract)
    request = submit(client, users["procurement"], contract["id"]).json()
    assert decide(client, users["admin"], request["id"]).status_code == 200
    assert decide(client, users["admin"], request["id"]).status_code == 403


def test_rejection_needs_comment_and_returns_to_quoting(client, users, contract):
    validated(client, users, contract)
    request = submit(client, users["procurement"], contract["id"]).json()
    assert decide(client, users["legal"], request["id"], "reject").status_code == 422
    result = decide(client, users["legal"], request["id"], "reject", "Liability cap missing").json()
    assert result["status"] == "Rejected"
    assert [s["status"] for s in result["steps"]] == ["Rejected", "Skipped"]
    assert client.get(f"/api/contracts/{contract['id']}", headers=users["admin"]).json()["status"] == "Quoting"
    # It can be resubmitted after fixing.
    assert submit(client, users["procurement"], contract["id"]).status_code == 201


def test_withdraw(client, users, contract):
    validated(client, users, contract)
    request = submit(client, users["procurement"], contract["id"]).json()
    assert client.post(f"/api/approvals/{request['id']}/withdraw", headers=users["legal"]).status_code == 403
    assert client.post(f"/api/approvals/{request['id']}/withdraw", headers=users["procurement"]).status_code == 200
    assert client.get(f"/api/contracts/{contract['id']}", headers=users["admin"]).json()["status"] == "Quoting"


def test_custom_chain_and_invalid_roles(client, users, contract):
    validated(client, users, contract)
    assert submit(client, users["procurement"], contract["id"], roles=["viewer"]).status_code == 422
    request = submit(client, users["procurement"], contract["id"], roles=["legal"]).json()
    assert decide(client, users["legal"], request["id"]).json()["status"] == "Approved"


def test_hold_cancels_approval_and_resumes(client, users, contract):
    validated(client, users, contract)
    request = submit(client, users["procurement"], contract["id"]).json()
    held = patch(client, users["procurement"], contract["id"], status="On Hold").json()
    assert held["pending_approval_id"] is None
    assert held["allowed_transitions"] == ["Quoting", "Cancelled"]
    approvals = client.get(f"/api/contracts/{contract['id']}/approvals", headers=users["admin"]).json()
    assert approvals[0]["id"] == request["id"] and approvals[0]["status"] == "Cancelled"
    assert patch(client, users["procurement"], contract["id"], status="Quoting").status_code == 200


def test_hold_resumes_to_previous_status(client, users, contract):
    validated(client, users, contract)
    request = submit(client, users["procurement"], contract["id"], roles=["legal"]).json()
    decide(client, users["legal"], request["id"])
    patch(client, users["procurement"], contract["id"], status="In Progress")
    held = patch(client, users["procurement"], contract["id"], status="On Hold").json()
    assert held["allowed_transitions"] == ["In Progress", "Cancelled"]
    assert patch(client, users["procurement"], contract["id"], status="Completed").status_code == 409
    assert patch(client, users["procurement"], contract["id"], status="In Progress").json()["status"] == "In Progress"


def test_versions(client, users, contract):
    validated(client, users, contract)
    response = client.post(
        f"/api/contracts/{contract['id']}/versions", headers=users["procurement"],
        files={"file": ("contract-v2.pdf", b"%PDF-1.4 version two", "application/pdf")}, data={"note": "Redlined"},
    )
    assert response.status_code == 201, response.text
    updated = response.json()
    assert updated["document_version"] == 2
    assert updated["validation_status"] == "Pending"  # new document needs a new check
    versions = client.get(f"/api/contracts/{contract['id']}/versions", headers=users["viewer"]).json()
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["note"] == "Redlined"
    old = client.get(f"/api/contracts/{contract['id']}/versions/1/file", headers=users["viewer"])
    assert old.content == b"%PDF-1.4 test"
    current = client.get(f"/api/contracts/{contract['id']}/file", headers=users["viewer"])
    assert current.content == b"%PDF-1.4 version two"


def test_versions_blocked_during_approval(client, users, contract):
    validated(client, users, contract)
    submit(client, users["procurement"], contract["id"])
    response = client.post(
        f"/api/contracts/{contract['id']}/versions", headers=users["procurement"],
        files={"file": ("v2.pdf", b"%PDF", "application/pdf")},
    )
    assert response.status_code == 409


def test_rejects_unsupported_files(client, users):
    response = client.post("/api/contracts/upload", headers=users["procurement"],
                           files={"file": ("evil.exe", b"MZ", "application/octet-stream")})
    assert response.status_code == 400


def test_csv_export_neutralises_formulas(client, users, contract):
    patch(client, users["procurement"], contract["id"], title="=HYPERLINK(\"http://x\")")
    body = client.get("/api/export/contracts.csv", headers=users["viewer"]).text
    assert "'=HYPERLINK" in body
