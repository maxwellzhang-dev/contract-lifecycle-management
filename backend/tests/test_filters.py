from .conftest import upload


def patch(client, headers, cid, **values):
    response = client.patch(f"/api/contracts/{cid}", json=values, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def ids(client, headers, query):
    return {c["id"] for c in client.get(f"/api/contracts{query}", headers=headers).json()}


def test_tags_are_cleaned_and_filterable(client, users):
    a = upload(client, users["procurement"])
    b = upload(client, users["procurement"])
    tagged = patch(client, users["procurement"], a["id"], tags=["  Cloud ", "cloud", "Critical vendor"])
    assert tagged["tags"] == ["Cloud", "Critical vendor"]
    assert ids(client, users["viewer"], "?tag=cloud") == {a["id"]}
    assert ids(client, users["viewer"], "?search=critical") == {a["id"]}
    assert b["id"] not in ids(client, users["viewer"], "?tag=Cloud")


def test_tag_limits(client, users, contract):
    url = f"/api/contracts/{contract['id']}"
    assert client.patch(url, json={"tags": [f"t{i}" for i in range(11)]}, headers=users["procurement"]).status_code == 422
    assert client.patch(url, json={"tags": ["x" * 31]}, headers=users["procurement"]).status_code == 422


def test_value_date_owner_filters(client, users):
    small = upload(client, users["procurement"])
    big = upload(client, users["procurement"])
    patch(client, users["procurement"], small["id"], one_time_amount=1000, optional_amount=500,
          project_end_date="2026-03-01", owner="Lena Ong", department="Legal")
    patch(client, users["procurement"], big["id"], one_time_amount=90000, optional_amount=None,
          project_end_date="2027-06-30", owner="Priya Nair", department="Finance")
    h = users["viewer"]
    assert ids(client, h, "?min_value=1500") == {small["id"], big["id"]}
    assert ids(client, h, "?min_value=1501") == {big["id"]}
    assert ids(client, h, "?max_value=1500") == {small["id"]}
    assert ids(client, h, "?end_from=2026-12-01&end_to=2027-12-31") == {big["id"]}
    assert ids(client, h, "?owner=Lena%20Ong") == {small["id"]}
    assert ids(client, h, "?department=Finance") == {big["id"]}


def test_export_uses_filters_and_includes_tags(client, users):
    a = upload(client, users["procurement"])
    upload(client, users["procurement"])
    patch(client, users["procurement"], a["id"], tags=["Cloud"], reference_number="REF-A")
    body = client.get("/api/export/contracts.csv?tag=Cloud", headers=users["viewer"]).text
    lines = body.strip().splitlines()
    assert len(lines) == 2 and "REF-A" in lines[1] and lines[0].endswith("tags")


def test_tags_editable_during_approval(client, users, contract):
    client.post(f"/api/contracts/{contract['id']}/validate", headers=users["procurement"])
    client.post(f"/api/contracts/{contract['id']}/approvals", json={}, headers=users["procurement"])
    assert patch(client, users["procurement"], contract["id"], tags=["Urgent"])["tags"] == ["Urgent"]
