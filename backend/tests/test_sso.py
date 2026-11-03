import json
import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.app.routers import sso

ISSUER = "https://login.example/tenant-1/v2.0"
CLIENT = "client-123"


class FakeProvider:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwks = {"keys": [{**public, "kid": "k1", "use": "sig", "alg": "RS256"}]}
        self.claims = {}
        self.token_requests = []
        self.override_token = None

    def get_json(self, url):
        if url.endswith("/.well-known/openid-configuration"):
            return {"issuer": ISSUER, "authorization_endpoint": "https://login.example/authorize",
                    "token_endpoint": "https://login.example/token", "jwks_uri": "https://login.example/keys"}
        if url == "https://login.example/keys":
            return self.jwks
        raise AssertionError(url)

    def post_form(self, url, data):
        self.token_requests.append(data)
        if self.override_token:
            return {"id_token": self.override_token}
        return {"id_token": self.id_token(**self.claims)}

    def id_token(self, key=None, kid="k1", **overrides):
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT, "iat": now, "exp": now + 600, "sub": "sub-1", "oid": "oid-1",
                  "nonce": self.nonce, "name": "Sam Single", "preferred_username": "Sam@Corp.example", **overrides}
        return jwt.encode(claims, key or self.key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture()
def provider(monkeypatch):
    monkeypatch.setenv("SSO_ISSUER", ISSUER)
    monkeypatch.setenv("SSO_CLIENT_ID", CLIENT)
    monkeypatch.setenv("SSO_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://clause.example")
    fake = FakeProvider()
    monkeypatch.setattr(sso, "http_get_json", fake.get_json)
    monkeypatch.setattr(sso, "http_post_form", fake.post_form)
    sso._cache.clear()
    return fake


def start(client, provider):
    response = client.get("/api/auth/sso/login", follow_redirects=False)
    assert response.status_code == 303
    url = urlparse(response.headers["location"])
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "login.example" and params["code_challenge_method"] == "S256"
    assert params["redirect_uri"] == "https://clause.example/backend-api/api/auth/sso/callback"
    provider.nonce = params["nonce"]
    return params


def finish(client, params, code="abc"):
    return client.get(f"/api/auth/sso/callback?code={code}&state={params['state']}", follow_redirects=False)


def error_of(response):
    assert response.status_code == 303
    return parse_qs(urlparse(response.headers["location"]).query).get("sso_error", [None])[0]


def test_config_reports_sso(client, provider, monkeypatch):
    assert client.get("/api/auth/config").json() == {"sso_enabled": True, "sso_label": "Sign in with Microsoft", "password_login": True}
    monkeypatch.delenv("SSO_CLIENT_SECRET")
    assert client.get("/api/auth/config").json()["sso_enabled"] is False
    assert client.get("/api/auth/sso/login", follow_redirects=False).status_code == 404


def test_first_sign_in_creates_viewer_and_session(client, provider):
    params = start(client, provider)
    response = finish(client, params)
    assert response.status_code == 303 and response.headers["location"] == "https://clause.example/"
    assert "clause_session=" in response.headers["set-cookie"]
    assert provider.token_requests[0]["code_verifier"] and provider.token_requests[0]["client_secret"] == "secret"
    me = client.get("/api/auth/me").json()  # TestClient keeps the cookie
    assert (me["email"], me["name"], me["role"]) == ("sam@corp.example", "Sam Single", "viewer")
    # The state is single use.
    assert "already used" in error_of(finish(client, params))
    # SSO-only accounts cannot sign in with a password.
    assert client.post("/api/auth/login", json={"email": "sam@corp.example", "password": "!sso-only"}).status_code == 401


def test_existing_user_is_matched_by_email_and_role_comes_from_app_roles(client, provider, users):
    provider.claims = {"preferred_username": "legal@clause.local", "oid": "oid-legal", "roles": ["Clause.Admin", "legal"]}
    finish(client, start(client, provider))
    me = client.get("/api/auth/me").json()
    assert me["name"] == "Lena Ong" and me["role"] == "admin"
    # The old password sessions of a user whose role changed are ended.
    assert client.get("/api/auth/me", headers=users["legal"]).status_code == 401
    # Later sign-ins match on the stable object id even if the email changes.
    provider.claims = {"preferred_username": "lena.ong@corp.example", "oid": "oid-legal", "roles": ["finance"]}
    finish(client, start(client, provider))
    me = client.get("/api/auth/me").json()
    assert me["name"] == "Lena Ong" and me["role"] == "finance"


@pytest.mark.parametrize("change, message", [
    ({"aud": "someone-else"}, "not valid"),
    ({"iss": "https://evil.example"}, "not valid"),
    ({"exp": 1000}, "not valid"),
    ({"nonce": "replayed"}, "does not match"),
])
def test_bad_tokens_are_rejected(client, provider, change, message):
    provider.claims = change
    assert message in error_of(finish(client, start(client, provider)))
    assert client.get("/api/auth/me").status_code == 401


def test_token_signed_by_another_key_is_rejected(client, provider):
    params = start(client, provider)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider.override_token = provider.id_token(key=other)
    assert "not valid" in error_of(finish(client, params))
    params = start(client, provider)
    provider.override_token = provider.id_token(kid="unknown")
    assert "unknown key" in error_of(finish(client, params))


def test_policies(client, provider, monkeypatch):
    monkeypatch.setenv("SSO_ALLOWED_DOMAINS", "clause.local")
    assert "cannot sign in" in error_of(finish(client, start(client, provider)))
    monkeypatch.setenv("SSO_ALLOWED_DOMAINS", "corp.example")
    monkeypatch.setenv("SSO_AUTO_PROVISION", "0")
    assert "Ask an administrator" in error_of(finish(client, start(client, provider)))
    # Provider errors and unknown states come back as readable messages.
    assert error_of(client.get("/api/auth/sso/callback?error=access_denied&error_description=User+cancelled", follow_redirects=False)) == "User cancelled"
    assert "too long" in error_of(client.get("/api/auth/sso/callback?code=x&state=forged", follow_redirects=False))


def test_password_login_can_be_turned_off(client, provider, monkeypatch):
    monkeypatch.setenv("PASSWORD_LOGIN", "0")
    response = client.post("/api/auth/login", json={"email": "maxwell@clause.local", "password": "ChangeMe123!"})
    assert response.status_code == 403
    assert client.get("/api/auth/config").json()["password_login"] is False


def test_deactivated_user_cannot_use_sso(client, provider, users):
    target = next(u for u in client.get("/api/users", headers=users["admin"]).json() if u["email"] == "finance@clause.local")
    client.patch(f"/api/users/{target['id']}", headers=users["admin"], json={"active": False})
    provider.claims = {"preferred_username": "finance@clause.local", "oid": "oid-fin"}
    assert "deactivated" in error_of(finish(client, start(client, provider)))
