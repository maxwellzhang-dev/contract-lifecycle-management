from .conftest import PASSWORD, login, upload


def test_requests_without_a_session_are_rejected(client):
    assert client.get("/api/contracts").status_code == 401
    assert client.get("/health").status_code == 200


def test_wrong_password_is_rejected(client):
    response = client.post("/api/auth/login", json={"email": "maxwell@clause.local", "password": "nope"})
    assert response.status_code == 401


def test_login_sets_cookie_and_me_returns_permissions(client):
    response = client.post("/api/auth/login", json={"email": "legal@clause.local", "password": PASSWORD})
    assert response.status_code == 200
    assert "clause_session" in response.cookies
    me = client.get("/api/auth/me").json()  # cookie is carried by the client
    assert me["role"] == "legal"
    assert "contract.delete" not in me["permissions"]


def test_logout_ends_session(client):
    headers = login(client, "maxwell@clause.local")
    client.post("/api/auth/logout", headers=headers)
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_viewer_is_read_only(client, users, contract):
    assert client.get("/api/contracts", headers=users["viewer"]).status_code == 200
    assert client.patch(f"/api/contracts/{contract['id']}", json={"notes": "x"}, headers=users["viewer"]).status_code == 403
    assert client.post("/api/contracts/upload", headers=users["viewer"],
                       files={"file": ("a.pdf", b"%PDF", "application/pdf")}).status_code == 403
    assert client.get("/api/audit", headers=users["viewer"]).status_code == 403


def test_only_admin_can_delete(client, users, contract):
    assert client.delete(f"/api/contracts/{contract['id']}", headers=users["legal"]).status_code == 403
    assert client.delete(f"/api/contracts/{contract['id']}", headers=users["admin"]).status_code == 200


def test_actor_comes_from_session_not_from_query(client, users, contract):
    client.patch(f"/api/contracts/{contract['id']}?actor=Mallory", json={"notes": "hello"}, headers=users["legal"])
    audit = client.get(f"/api/audit?contract_id={contract['id']}", headers=users["admin"]).json()
    assert audit[0]["actor"] == "Lena Ong"


def test_admin_manages_users(client, users):
    created = client.post("/api/users", headers=users["admin"], json={
        "email": "new@clause.local", "name": "New Person", "role": "finance", "password": "a-long-password"})
    assert created.status_code == 201
    new_headers = login_with(client, "new@clause.local", "a-long-password")
    assert client.get("/api/auth/me", headers=new_headers).json()["role"] == "finance"
    # Deactivating ends the user's sessions.
    client.patch(f"/api/users/{created.json()['id']}", headers=users["admin"], json={"active": False})
    assert client.get("/api/auth/me", headers=new_headers).status_code == 401
    assert client.get("/api/users", headers=users["legal"]).status_code == 403


def test_admin_cannot_lock_themselves_out(client, users):
    me = client.get("/api/auth/me", headers=users["admin"]).json()
    response = client.patch(f"/api/users/{me['id']}", headers=users["admin"], json={"role": "viewer"})
    assert response.status_code == 409


def test_password_change(client, users):
    response = client.post("/api/auth/password", headers=users["finance"],
                           json={"current_password": PASSWORD, "new_password": "another-password"})
    assert response.status_code == 200
    login_with(client, "finance@clause.local", "another-password")


def login_with(client, email, password):
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}
