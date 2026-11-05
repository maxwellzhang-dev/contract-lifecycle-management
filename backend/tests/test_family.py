"""Main agreements with their SOWs, amendments, order forms and renewals."""

from .conftest import upload


def make(client, users, title, **values):
    response = client.post("/api/contracts", headers=users["procurement"], json={"title": title, **values})
    assert response.status_code == 201, response.text
    return response.json()


def link(client, headers, contract_id, parent_id, relationship="SOW"):
    return client.patch(f"/api/contracts/{contract_id}", headers=headers, json={"parent_id": parent_id, "relationship": relationship})


def test_family_tree_and_totals(client, users):
    msa = make(client, users, "Master services agreement", vendor_name="Acme", currency="SGD", one_time_amount=1000,
               project_start_date="2026-01-01", project_end_date="2027-12-31")
    sow1 = make(client, users, "SOW 1", currency="SGD", one_time_amount=500, optional_amount=50,
                project_start_date="2026-02-01", project_end_date="2026-06-30", parent_id=msa["id"], relationship="SOW")
    sow2 = make(client, users, "SOW 2", currency="USD", one_time_amount=300, project_end_date="2028-03-31")
    assert link(client, users["procurement"], sow2["id"], msa["id"]).status_code == 200
    amendment = make(client, users, "SOW 1 amendment", currency="SGD", one_time_amount=100)
    linked = link(client, users["legal"], amendment["id"], sow1["id"], "Amendment").json()
    assert linked["parent_id"] == sow1["id"] and linked["relationship"] == "Amendment"

    family = client.get(f"/api/contracts/{amendment['id']}/family", headers=users["viewer"]).json()
    root = family["root"]
    assert root["id"] == msa["id"] and family["count"] == 4 and family["hidden_count"] == 0
    assert [c["title"] for c in root["children"]] == ["SOW 1", "SOW 2"]
    assert root["children"][0]["children"][0]["relationship"] == "Amendment"
    assert family["value_by_currency"] == [{"currency": "SGD", "value": 1650}, {"currency": "USD", "value": 300}]
    assert family["first_start"] == "2026-01-01" and family["last_end"] == "2028-03-31"

    listed = {c["id"]: c for c in client.get("/api/contracts", headers=users["viewer"]).json()}
    assert listed[msa["id"]]["child_count"] == 2 and listed[sow1["id"]]["child_count"] == 1

    audit = client.get(f"/api/audit?contract_id={msa['id']}", headers=users["admin"]).json()
    assert any(a["action"] == "Contract linked" and "SOW 2" in a["details"] for a in audit)

    # Unlink; deleting a middle contract moves its children up.
    client.delete(f"/api/contracts/{sow1['id']}", headers=users["admin"])
    moved = client.get(f"/api/contracts/{amendment['id']}", headers=users["viewer"]).json()
    assert moved["parent_id"] == msa["id"] and moved["relationship"] == "Amendment"
    unlinked = client.patch(f"/api/contracts/{sow2['id']}", headers=users["procurement"], json={"parent_id": None}).json()
    assert unlinked["parent_id"] is None and unlinked["relationship"] is None


def test_links_are_validated(client, users):
    a = make(client, users, "A")
    b = make(client, users, "B", parent_id=a["id"], relationship="SOW")
    assert link(client, users["procurement"], a["id"], a["id"]).status_code == 422          # itself
    assert link(client, users["procurement"], a["id"], b["id"]).status_code == 422          # loop
    assert link(client, users["procurement"], b["id"], a["id"], "Spouse").status_code == 422  # unknown relationship
    assert link(client, users["procurement"], b["id"], 9999).status_code == 422             # missing parent
    assert client.post("/api/contracts", headers=users["procurement"],
                       json={"title": "C", "parent_id": a["id"]}).status_code == 422       # relationship required
    chain = [b]
    for i in range(2):  # levels 3 and 4
        chain.append(make(client, users, f"level {i}", parent_id=chain[-1]["id"], relationship="Amendment"))
    too_deep = client.post("/api/contracts", headers=users["procurement"],
                           json={"title": "too deep", "parent_id": chain[-1]["id"], "relationship": "Amendment"})
    assert too_deep.status_code == 422 and "levels deep" in too_deep.json()["detail"]
    # Moving a subtree under another contract counts its depth too.
    other = make(client, users, "Other")
    under_other = make(client, users, "Under other", parent_id=other["id"], relationship="SOW")
    assert link(client, users["procurement"], b["id"], under_other["id"]).status_code == 422  # b's subtree would reach level 5
    assert link(client, users["procurement"], b["id"], other["id"]).status_code == 200        # level 4 is fine


def test_linking_is_allowed_while_awaiting_approval(client, users):
    parent = make(client, users, "Parent")
    contract = upload(client, users["procurement"])
    client.post(f"/api/contracts/{contract['id']}/validate", headers=users["procurement"])
    client.post(f"/api/contracts/{contract['id']}/approvals", headers=users["procurement"], json={})
    assert link(client, users["procurement"], contract["id"], parent["id"]).status_code == 200


def test_family_hides_contracts_the_user_cannot_see(client, users):
    msa = make(client, users, "MSA", department="Finance")
    secret = make(client, users, "Secret SOW", department="Legal", parent_id=msa["id"], relationship="SOW")
    users_list = client.get("/api/users", headers=users["admin"]).json()
    finance = next(u for u in users_list if u["email"] == "finance@clause.local")
    client.patch(f"/api/users/{finance['id']}", headers=users["admin"], json={"department": "Finance", "access_scope": "department"})
    family = client.get(f"/api/contracts/{msa['id']}/family", headers=users["finance"]).json()
    assert family["count"] == 1 and family["hidden_count"] == 1 and family["root"]["children"] == []
    assert client.get(f"/api/contracts/{secret['id']}/family", headers=users["finance"]).status_code == 404
    # A restricted user cannot link to a contract they cannot see.
    mine = make(client, users, "Finance SOW", department="Finance")
    assert link(client, users["finance"], mine["id"], secret["id"]).status_code == 422
