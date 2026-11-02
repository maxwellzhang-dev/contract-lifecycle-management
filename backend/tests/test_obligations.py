from datetime import date, timedelta

from backend.app.database import connection
from backend.app.workflow import run_reminders


def iso(days):
    return (date.today() + timedelta(days=days)).isoformat()


def test_obligation_crud(client, users, contract):
    url = f"/api/contracts/{contract['id']}/obligations"
    assert client.post(url, json={"title": "x"}, headers=users["viewer"]).status_code == 403
    assert client.post(url, json={"title": "x", "kind": "Bogus"}, headers=users["finance"]).status_code == 422
    created = client.post(url, json={"title": "First instalment", "kind": "Payment", "due_date": iso(-1),
                                     "amount": 30000, "currency": "SGD"}, headers=users["finance"]).json()
    assert created["overdue"] is True

    done = client.patch(f"/api/obligations/{created['id']}", json={"status": "Done"}, headers=users["finance"]).json()
    assert done["status"] == "Done" and done["completed_by"] == "Priya Nair" and done["overdue"] is False

    listed = client.get(f"/api/obligations?contract_id={contract['id']}", headers=users["viewer"]).json()
    assert len(listed) == 1
    assert client.delete(f"/api/obligations/{created['id']}", headers=users["finance"]).status_code == 200
    assert client.get(f"/api/obligations?contract_id={contract['id']}", headers=users["viewer"]).json() == []


def test_due_within_filter(client, users, contract):
    url = f"/api/contracts/{contract['id']}/obligations"
    client.post(url, json={"title": "Soon", "due_date": iso(5)}, headers=users["finance"])
    client.post(url, json={"title": "Later", "due_date": iso(90)}, headers=users["finance"])
    titles = [o["title"] for o in client.get("/api/obligations?due_within=30", headers=users["viewer"]).json()]
    assert titles == ["Soon"]


def test_reminders_are_created_once_and_routed_to_owner(client, users, contract):
    client.patch(f"/api/contracts/{contract['id']}", headers=users["procurement"],
                 json={"project_end_date": iso(20), "owner": "Priya Nair"})
    client.post(f"/api/contracts/{contract['id']}/obligations", headers=users["finance"],
                json={"title": "Deliver report", "due_date": iso(-2), "owner": "Lena Ong"})
    with connection() as conn:
        first = run_reminders(conn)
        second = run_reminders(conn)
    assert first == 2 and second == 0

    finance = client.get("/api/notifications", headers=users["finance"]).json()
    assert any("ends in 20 days" in n["title"] for n in finance["items"])
    legal = client.get("/api/notifications", headers=users["legal"]).json()
    assert any(n["title"].startswith("Overdue: Deliver report") for n in legal["items"])

    client.post("/api/notifications/read-all", headers=users["legal"])
    assert client.get("/api/notifications", headers=users["legal"]).json()["unread"] == 0


def test_reminders_without_matching_owner_go_to_admins(client, users, contract):
    client.patch(f"/api/contracts/{contract['id']}", headers=users["procurement"],
                 json={"project_end_date": iso(3), "owner": "Somebody External"})
    response = client.post("/api/reminders/run", headers=users["admin"])
    assert response.json()["created"] >= 1
    admin = client.get("/api/notifications", headers=users["admin"]).json()
    assert any("ends in 3 days" in n["title"] for n in admin["items"])
    assert client.post("/api/reminders/run", headers=users["legal"]).status_code == 403


def test_dashboard_counts(client, users, contract):
    client.post(f"/api/contracts/{contract['id']}/obligations", headers=users["finance"],
                json={"title": "Overdue thing", "due_date": iso(-3)})
    data = client.get("/api/dashboard", headers=users["viewer"]).json()
    assert data["obligations_overdue"] == 1
    assert data["pending_approvals"] == 0
