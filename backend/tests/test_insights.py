import io

from openpyxl import load_workbook

from .conftest import upload


def setup_contracts(client, users):
    h = users["procurement"]
    a, b, c = (upload(client, h) for _ in range(3))
    client.patch(f"/api/contracts/{a['id']}", headers=h, json={
        "vendor_name": "Acme", "department": "IT", "currency": "SGD", "one_time_amount": 100, "optional_amount": 50,
        "project_start_date": "2026-01-10", "project_end_date": "2026-12-31", "tags": ["Cloud"]})
    client.patch(f"/api/contracts/{b['id']}", headers=h, json={
        "vendor_name": "Acme", "department": "Finance", "currency": "USD", "one_time_amount": 200,
        "project_start_date": "2026-02-01", "project_end_date": "2027-01-31", "tags": ["Cloud", "Audit"]})
    client.patch(f"/api/contracts/{c['id']}", headers=h, json={
        "vendor_name": "Beta", "department": "IT", "currency": "SGD", "one_time_amount": 1000,
        "project_start_date": "2025-06-01", "project_end_date": "2026-01-31"})
    client.post(f"/api/contracts/{a['id']}/obligations", headers=users["finance"], json={"title": "Pay", "due_date": "2020-01-01"})
    return a, b, c


def test_vendor_rollup(client, users):
    setup_contracts(client, users)
    vendors = {v["vendor_name"]: v for v in client.get("/api/vendors", headers=users["viewer"]).json()}
    acme = vendors["Acme"]
    assert acme["contract_count"] == 2
    assert {v["currency"]: v["value"] for v in acme["value_by_currency"]} == {"SGD": 150, "USD": 200}
    assert acme["departments"] == ["Finance", "IT"]
    assert acme["open_obligations"] == 1 and acme["overdue_obligations"] == 1
    assert vendors["Beta"]["open_obligations"] == 0


def test_grouped_report(client, users):
    setup_contracts(client, users)
    h = users["viewer"]
    by_dept = client.get("/api/reports/summary?group_by=department", headers=h).json()
    rows = {r["group"]: r for r in by_dept["rows"]}
    assert rows["IT"]["count"] == 2
    assert {v["currency"]: v["value"] for v in rows["IT"]["value_by_currency"]} == {"SGD": 1150}
    by_tag = {r["group"]: r["count"] for r in client.get("/api/reports/summary?group_by=tag", headers=h).json()["rows"]}
    assert by_tag == {"Cloud": 2, "Audit": 1, "(none)": 1}
    months = client.get("/api/reports/summary?group_by=start_month&start=2026-01-01&end=2026-12-31", headers=h).json()
    assert [r["group"] for r in months["rows"]] == ["2026-01", "2026-02"]
    assert client.get("/api/reports/summary?group_by=nope", headers=h).status_code == 422


def test_excel_export(client, users):
    setup_contracts(client, users)
    client.patch("/api/contracts/1", headers=users["procurement"], json={"title": "=SUM(A1:A9)"})
    response = client.get("/api/export/report.xlsx", headers=users["viewer"])
    assert response.status_code == 200
    book = load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == ["Contracts", "By department", "By vendor", "By status", "By owner", "By start month", "Obligations"]
    contracts = list(book["Contracts"].values)
    assert len(contracts) == 4
    titles = [row[1] for row in contracts[1:]]
    assert "'=SUM(A1:A9)" in titles
    headers = list(book["By vendor"].values)[0]
    assert headers == ("Vendor", "Contracts", "Active", "Completed", "Total SGD", "Total USD")
    assert len(list(book["Obligations"].values)) == 2
