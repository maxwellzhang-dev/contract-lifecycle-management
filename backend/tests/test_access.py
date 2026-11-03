import pytest

from .conftest import PASSWORD, login, upload, validated


def make_user(client, admin, email, name, role, scope, department=None):
    response = client.post("/api/users", headers=admin, json={"email": email, "name": name, "role": role,
                                                               "password": PASSWORD, "access_scope": scope, "department": department})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def setup(client, users):
    a = upload(client, users["procurement"])
    b = upload(client, users["procurement"], name="b.pdf")
    client.patch(f"/api/contracts/{a['id']}", headers=users["admin"], json={"department": "Sales", "title": "Sales deal"})
    client.patch(f"/api/contracts/{b['id']}", headers=users["admin"], json={"department": "Engineering", "title": "Eng deal"})
    dana = make_user(client, users["admin"], "dana@clause.local", "Dana Dept", "procurement", "department", "sales")
    ari = make_user(client, users["admin"], "ari@clause.local", "Ari Assigned", "finance", "assigned")
    return {"a": a, "b": b, "dana": dana, "ari": ari,
            "dana_h": login(client, "dana@clause.local"), "ari_h": login(client, "ari@clause.local")}


def titles(client, headers):
    return sorted(c["title"] for c in client.get("/api/contracts", headers=headers).json())


def test_department_scope_limits_every_view(client, users, setup):
    s, h = setup, setup["dana_h"]
    me = client.get("/api/auth/me", headers=h).json()
    assert me["access_scope"] == "department" and me["department"] == "sales"
    assert titles(client, h) == ["Sales deal"]
    assert titles(client, users["legal"]) == ["Eng deal", "Sales deal"]  # scope "all" by default
    b = s["b"]["id"]
    for url in (f"/api/contracts/{b}", f"/api/contracts/{b}/file", f"/api/contracts/{b}/versions",
                f"/api/contracts/{b}/comments", f"/api/contracts/{b}/approvals", f"/api/contracts/{b}/signatures",
                f"/api/contracts/{b}/ai/review", f"/api/contracts/{b}/members"):
        assert client.get(url, headers=h).status_code == 404, url
    assert client.patch(f"/api/contracts/{b}", headers=h, json={"notes": "x"}).status_code == 404
    assert client.post(f"/api/contracts/{b}/comments", headers=h, json={"body": "hi"}).status_code == 404

    ob = client.post(f"/api/contracts/{b}/obligations", headers=users["finance"], json={"title": "Pay", "kind": "Payment"}).json()
    assert client.patch(f"/api/obligations/{ob['id']}", headers=h, json={"status": "Done"}).status_code == 404
    assert [o["contract_id"] for o in client.get("/api/obligations", headers=h).json()] == []
    assert client.get("/api/dashboard", headers=h).json()["total"] == 1
    assert [v["vendor_name"] for v in client.get("/api/vendors", headers=h).json()] == ["Acme Cloud"]
    assert client.get("/api/reports/summary", headers=h).json()["contract_count"] == 1
    csv = client.get("/api/export/contracts.csv", headers=h).text
    assert "Sales deal" in csv and "Eng deal" not in csv
    audit = client.get("/api/audit", headers=h).json()
    assert audit and all(item["contract_id"] == s["a"]["id"] for item in audit)


def test_assigned_scope_members_and_involvement(client, users, setup):
    s, h = setup, setup["ari_h"]
    assert titles(client, h) == []
    b = s["b"]["id"]
    assert client.post(f"/api/contracts/{b}/members", headers=users["viewer"], json={"user_id": s["ari"]["id"]}).status_code == 403
    members = client.post(f"/api/contracts/{b}/members", headers=users["legal"], json={"user_id": s["ari"]["id"]}).json()
    assert [m["name"] for m in members] == ["Ari Assigned"]
    assert titles(client, h) == ["Eng deal"]
    notes = client.get("/api/notifications", headers=h).json()["items"]
    assert any(n["kind"] == "access" for n in notes)
    client.delete(f"/api/contracts/{b}/members/{s['ari']['id']}", headers=users["legal"])
    assert client.get(f"/api/contracts/{b}", headers=h).status_code == 404

    # Being named owner makes a contract visible.
    client.patch(f"/api/contracts/{b}", headers=users["admin"], json={"owner": "ari assigned"})
    assert titles(client, h) == ["Eng deal"]


def test_approvers_see_contracts_waiting_for_them(client, users, setup):
    admin = users["admin"]
    make_user(client, admin, "lee@clause.local", "Lee Legal", "legal", "assigned")
    lee = login(client, "lee@clause.local")
    a = setup["a"]
    validated(client, users, a)
    request = client.post(f"/api/contracts/{a['id']}/approvals", headers=users["procurement"], json={"roles": ["legal"]}).json()
    assert titles(client, lee) == ["Sales deal"]
    assert [i["contract_id"] for i in client.get("/api/approvals/inbox", headers=lee).json()] == [a["id"]]
    assert client.post(f"/api/approvals/{request['id']}/decision", headers=lee, json={"decision": "approve"}).status_code == 200
    assert titles(client, lee) == ["Sales deal"]  # still visible after deciding


def test_mentions_do_not_leak_contracts(client, users, setup):
    b = setup["b"]["id"]
    client.post(f"/api/contracts/{b}/comments", headers=users["legal"], json={"body": "@Dana Dept and @Priya Nair please check"})
    assert client.get("/api/notifications", headers=setup["dana_h"]).json()["items"] == []
    assert client.get("/api/notifications", headers=users["finance"]).json()["items"]


def test_scope_validation_and_admin_always_sees_all(client, users):
    bad = client.post("/api/users", headers=users["admin"], json={"email": "x@clause.local", "name": "X", "role": "viewer",
                                                                   "password": PASSWORD, "access_scope": "department"})
    assert bad.status_code == 422
    assert client.post("/api/users", headers=users["admin"], json={"email": "y@clause.local", "name": "Y", "role": "viewer",
                                                                     "password": PASSWORD, "access_scope": "galaxy"}).status_code == 422
    target = next(u for u in client.get("/api/users", headers=users["admin"]).json() if u["email"] == "maxwell@clause.local")
    client.patch(f"/api/users/{target['id']}", headers=users["admin"], json={"access_scope": "assigned"})
    assert client.get("/api/auth/me", headers=users["admin"]).json()["access_scope"] == "all"


def test_portfolio_assistant_only_reads_visible_contracts(client, users, setup, monkeypatch):
    from backend.app.services import llm
    sent = []

    def structured(instructions, text, name, schema, **kwargs):
        sent.append(text)
        return {"answer": "ok", "found": True, "contracts": [{"id": setup["a"]["id"], "reason": "a"}, {"id": setup["b"]["id"], "reason": "b"}]}
    monkeypatch.setattr(llm, "structured", structured)
    answer = client.post("/api/ai/portfolio", headers=setup["dana_h"], json={"question": "What do we have?"}).json()
    assert "Sales deal" in sent[0] and "Eng deal" not in sent[0]
    assert [c["id"] for c in answer["answer"]["contracts"]] == [setup["a"]["id"]]
