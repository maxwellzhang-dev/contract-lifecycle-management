def test_comments_and_mentions(client, users, contract):
    url = f"/api/contracts/{contract['id']}/comments"
    assert client.post(url, json={"body": "   "}, headers=users["legal"]).status_code == 422
    created = client.post(url, json={"body": "@Priya Nair can you check the payment terms? cc @daniel tan"},
                          headers=users["legal"])
    assert created.status_code == 201
    assert created.json()["author_name"] == "Lena Ong"
    # Viewers can take part in the discussion.
    assert client.post(url, json={"body": "Noted"}, headers=users["viewer"]).status_code == 201
    assert [c["body"] for c in client.get(url, headers=users["admin"]).json()][1] == "Noted"

    finance = client.get("/api/notifications", headers=users["finance"]).json()["items"]
    assert any(n["kind"] == "mention" and "Lena Ong mentioned you" in n["title"] for n in finance)
    procurement = client.get("/api/notifications", headers=users["procurement"]).json()["items"]
    assert any(n["kind"] == "mention" for n in procurement)
    legal = client.get("/api/notifications", headers=users["legal"]).json()["items"]
    assert not any(n["kind"] == "mention" for n in legal)


def test_mention_needs_full_name(client, users, contract):
    url = f"/api/contracts/{contract['id']}/comments"
    client.post(url, json={"body": "@Priya Nairobi is a city"}, headers=users["legal"])
    finance = client.get("/api/notifications", headers=users["finance"]).json()["items"]
    assert not any(n["kind"] == "mention" for n in finance)


def test_only_author_or_admin_deletes(client, users, contract):
    url = f"/api/contracts/{contract['id']}/comments"
    comment = client.post(url, json={"body": "hello"}, headers=users["legal"]).json()
    assert client.delete(f"/api/comments/{comment['id']}", headers=users["finance"]).status_code == 403
    assert client.delete(f"/api/comments/{comment['id']}", headers=users["admin"]).status_code == 200
    assert client.get(url, headers=users["legal"]).json() == []
