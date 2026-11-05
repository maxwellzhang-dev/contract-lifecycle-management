"""Invoices, budget use, spend alerts and invoice reminders."""

from datetime import date, timedelta

from backend.app.database import connection
from backend.app.workflow import run_reminders


def make(client, users, **values):
    body = {"title": "Hosting", "currency": "SGD", "one_time_amount": 1000, "optional_amount": 0, "owner": "Daniel Tan", **values}
    response = client.post("/api/contracts", headers=users["procurement"], json=body)
    assert response.status_code == 201, response.text
    return response.json()


def add(client, headers, contract_id, **values):
    return client.post(f"/api/contracts/{contract_id}/invoices", headers=headers, json={"amount": 100, **values})


def notifications(client, headers, kind):
    return [n for n in client.get("/api/notifications", headers=headers).json()["items"] if n["kind"] == kind]


def test_invoices_track_budget_use(client, users):
    contract = make(client, users)
    first = add(client, users["finance"], contract["id"], invoice_number="INV-1", amount=300, invoice_date="2026-01-31",
                due_date="2026-02-28").json()
    assert first["currency"] == "SGD" and first["status"] == "Received" and first["created_by"] == "Priya Nair"
    add(client, users["procurement"], contract["id"], invoice_number="INV-2", amount=200, status="Paid")
    add(client, users["finance"], contract["id"], invoice_number="INV-3", amount=999, status="Disputed")

    data = client.get(f"/api/contracts/{contract['id']}/invoices", headers=users["viewer"]).json()
    spend = data["spend"]
    assert spend["budget"] == 1000 and spend["invoiced"] == 500 and spend["paid"] == 200 and spend["unpaid"] == 300
    assert spend["disputed"] == 999 and spend["remaining"] == 500 and spend["percent"] == 50 and spend["level"] == "ok"
    assert spend["overdue"] == 300 and [i["invoice_number"] for i in data["items"]][-1] == "INV-1"
    assert next(i for i in data["items"] if i["invoice_number"] == "INV-1")["overdue"] is True

    # Validation.
    assert add(client, users["viewer"], contract["id"]).status_code == 403
    assert add(client, users["legal"], contract["id"]).status_code == 403
    assert add(client, users["finance"], contract["id"], currency="USD").status_code == 422
    assert add(client, users["finance"], contract["id"], amount=0).status_code == 422
    assert add(client, users["finance"], contract["id"], due_date="soon").status_code == 422
    assert add(client, users["finance"], contract["id"], status="Lost").status_code == 422
    assert add(client, users["finance"], contract["id"], invoice_number="inv-1").status_code == 409
    no_currency = make(client, users, currency=None)
    assert add(client, users["finance"], no_currency["id"]).status_code == 422

    # Paying settles a linked payment obligation.
    obligation = client.post(f"/api/contracts/{contract['id']}/obligations", headers=users["finance"],
                             json={"title": "Instalment 1", "kind": "Payment"}).json()
    paid = client.patch(f"/api/invoices/{first['id']}", headers=users["finance"],
                        json={"status": "Paid", "obligation_id": obligation["id"]}).json()
    assert paid["status"] == "Paid" and paid["paid_at"]
    assert client.get(f"/api/obligations?contract_id={contract['id']}", headers=users["finance"]).json()[0]["status"] == "Done"
    other = make(client, users, title="Other")
    other_obligation = client.post(f"/api/contracts/{other['id']}/obligations", headers=users["finance"], json={"title": "X"}).json()
    assert client.patch(f"/api/invoices/{first['id']}", headers=users["finance"],
                        json={"obligation_id": other_obligation["id"]}).status_code == 422

    assert client.delete(f"/api/invoices/{first['id']}", headers=users["finance"]).json() == {"deleted": True}
    assert client.get(f"/api/contracts/{contract['id']}/invoices", headers=users["viewer"]).json()["spend"]["invoiced"] == 200
    audit = [a["action"] for a in client.get(f"/api/audit?contract_id={contract['id']}", headers=users["admin"]).json()]
    assert {"Invoice recorded", "Invoice paid", "Invoice deleted", "Obligation completed"} <= set(audit)


def test_spend_alerts_fire_once_per_level(client, users):
    contract = make(client, users)
    add(client, users["finance"], contract["id"], amount=500)
    assert notifications(client, users["procurement"], "spend") == []
    add(client, users["finance"], contract["id"], amount=300)  # 80%
    alerts = notifications(client, users["procurement"], "spend")
    assert len(alerts) == 1 and "80%" in alerts[0]["title"]
    add(client, users["finance"], contract["id"], amount=100)  # 90%: still a warning, no new alert
    assert len(notifications(client, users["procurement"], "spend")) == 1
    add(client, users["admin"], contract["id"], amount=200)  # 110%
    alerts = notifications(client, users["procurement"], "spend")
    assert len(alerts) == 2 and alerts[0]["title"] == "Hosting is over budget"
    assert notifications(client, users["finance"], "spend")  # finance hears about it too
    assert client.get("/api/dashboard", headers=users["viewer"]).json()["over_budget"] == 1

    overview = client.get("/api/spend", headers=users["viewer"]).json()
    assert overview["counts"]["over"] == 1 and overview["items"][0]["percent"] == 110
    assert overview["totals"] == [{"currency": "SGD", "budget": 1000, "invoiced": 1100, "paid": 0}]
    assert client.get("/api/spend?level=ok", headers=users["viewer"]).json()["items"] == []


def test_unpaid_invoices_due_soon_are_reminded(client, users):
    contract = make(client, users)
    soon = (date.today() + timedelta(days=3)).isoformat()
    add(client, users["procurement"], contract["id"], invoice_number="INV-9", due_date=soon)
    add(client, users["procurement"], contract["id"], invoice_number="INV-10", due_date=soon, status="Paid")
    with connection() as conn:
        assert run_reminders(conn) == 1
        assert run_reminders(conn) == 0  # deduplicated
    reminders = notifications(client, users["finance"], "invoice")
    assert len(reminders) == 1 and reminders[0]["title"] == "Invoice INV-9 for Hosting is due in 3 days"


def test_family_and_portfolio_include_invoiced(client, users, monkeypatch):
    parent = make(client, users, title="MSA")
    child = make(client, users, title="SOW", parent_id=parent["id"], relationship="SOW")
    add(client, users["finance"], child["id"], amount=250)
    family = client.get(f"/api/contracts/{parent['id']}/family", headers=users["viewer"]).json()
    assert family["invoiced_by_currency"] == [{"currency": "SGD", "value": 250}]
    assert family["root"]["children"][0]["invoiced"] == 250

    from backend.app.routers.ai import portfolio_material
    me = client.get("/api/auth/me", headers=users["admin"]).json()
    with connection() as conn:
        material = portfolio_material(conn, me)
    assert "invoiced (not disputed)" in material and "| SOW of #" in material and "| 250.0 | 0" in material


def test_restricted_users_cannot_reach_invoices_of_hidden_contracts(client, users):
    contract = make(client, users, department="Legal")
    invoice = add(client, users["finance"], contract["id"]).json()
    finance = next(u for u in client.get("/api/users", headers=users["admin"]).json() if u["email"] == "finance@clause.local")
    client.patch(f"/api/users/{finance['id']}", headers=users["admin"], json={"department": "Finance", "access_scope": "department"})
    assert client.get(f"/api/contracts/{contract['id']}/invoices", headers=users["finance"]).status_code == 404
    assert client.patch(f"/api/invoices/{invoice['id']}", headers=users["finance"], json={"status": "Paid"}).status_code == 404
    assert client.get("/api/spend", headers=users["finance"]).json()["items"] == []
