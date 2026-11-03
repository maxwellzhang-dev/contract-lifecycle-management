"""Single sign-on with Microsoft Entra ID (or any OpenID Connect provider).

Authorization code flow with PKCE, state and nonce. The ID token's signature is checked
against the provider's published keys, along with issuer, audience, expiry and nonce.
Users are matched by their stable subject (Entra `oid`), then by email; unknown users can
be created automatically. Entra app roles named after Clause roles (for example `legal` or
`Clause.Legal`) set the user's role on every sign-in.
"""

import base64
import hashlib
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from ..auth import ROLES, create_session, set_session_cookie
from ..database import add_audit, connection, utc_now

router = APIRouter(prefix="/api/auth")

STATE_MINUTES = 10
ROLE_PRIORITY = ("admin", "legal", "procurement", "finance", "viewer")
_cache: dict[str, tuple[float, dict]] = {}


class SSOError(Exception):
    """Shown to the user on the sign-in screen."""


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def issuer() -> str:
    explicit = env("SSO_ISSUER")
    if explicit:
        return explicit.rstrip("/")
    tenant = env("ENTRA_TENANT_ID")
    return f"https://login.microsoftonline.com/{tenant}/v2.0" if tenant else ""


def enabled() -> bool:
    return bool(issuer() and env("SSO_CLIENT_ID") and env("SSO_CLIENT_SECRET"))


def password_login_enabled() -> bool:
    return env("PASSWORD_LOGIN", "1") != "0"


def app_url() -> str:
    return env("PUBLIC_APP_URL", "http://localhost:3000").rstrip("/")


def redirect_uri() -> str:
    return env("SSO_REDIRECT_URI") or f"{app_url()}/backend-api/api/auth/sso/callback"


# --- provider metadata (cached; tests replace http_get_json / http_post_form) -------------

def http_get_json(url: str) -> dict:
    response = httpx.get(url, timeout=15)
    response.raise_for_status()
    return response.json()


def http_post_form(url: str, data: dict) -> dict:
    response = httpx.post(url, data=data, timeout=15, headers={"Accept": "application/json"})
    if response.status_code >= 400:
        try:
            detail = response.json().get("error_description") or response.json().get("error")
        except ValueError:
            detail = response.text[:200]
        raise SSOError(f"The identity provider rejected the sign-in: {str(detail).splitlines()[0][:200]}")
    return response.json()


def cached(url: str, max_age: float = 3600, refresh: bool = False) -> dict:
    hit = _cache.get(url)
    if hit and not refresh and time.time() - hit[0] < max_age:
        return hit[1]
    try:
        value = http_get_json(url)
    except httpx.HTTPError as exc:
        raise SSOError(f"Could not reach the identity provider ({exc.__class__.__name__}).") from exc
    _cache[url] = (time.time(), value)
    return value


def discovery() -> dict:
    return cached(f"{issuer()}/.well-known/openid-configuration")


def signing_key(token: str):
    kid = jwt.get_unverified_header(token).get("kid")
    jwks_uri = discovery()["jwks_uri"]
    for refresh in (False, True):  # keys rotate: refetch once if the kid is unknown
        for key in cached(jwks_uri, refresh=refresh).get("keys", []):
            if key.get("kid") == kid:
                return jwt.PyJWK(key).key
    raise SSOError("The sign-in token was signed with an unknown key.")


def verify_id_token(token: str, nonce: str) -> dict:
    meta = discovery()
    try:
        claims = jwt.decode(
            token, signing_key(token), algorithms=["RS256"], audience=env("SSO_CLIENT_ID"),
            issuer=meta["issuer"], options={"require": ["exp", "iat", "iss", "aud", "sub"]}, leeway=60,
        )
    except jwt.PyJWTError as exc:
        raise SSOError(f"The sign-in token is not valid ({exc}).") from exc
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise SSOError("The sign-in response does not match this browser session. Try again.")
    return claims


# --- users -----------------------------------------------------------------------------

def role_from_claims(claims: dict) -> str | None:
    found = set()
    for value in claims.get("roles") or []:
        name = str(value).lower().removeprefix("clause.").removeprefix("clause_")
        if name in ROLES:
            found.add(name)
    return next((role for role in ROLE_PRIORITY if role in found), None)


def email_from_claims(claims: dict) -> str:
    email = claims.get("email") or claims.get("preferred_username") or claims.get("upn") or ""
    return str(email).strip().lower()


def find_or_create_user(conn, claims: dict):
    subject = str(claims.get("oid") or claims["sub"])
    email = email_from_claims(claims)
    if "@" not in email:
        raise SSOError("Your account has no email address that Clause can use.")
    domains = [d.strip().lower().lstrip("@") for d in env("SSO_ALLOWED_DOMAINS").split(",") if d.strip()]
    if domains and email.rsplit("@", 1)[1] not in domains:
        raise SSOError(f"Accounts from {email.rsplit('@', 1)[1]} cannot sign in to Clause.")
    role = role_from_claims(claims)
    name = " ".join(str(claims.get("name") or email.split("@")[0]).split())
    user = conn.execute("SELECT * FROM users WHERE sso_subject=?", (subject,)).fetchone() \
        or conn.execute("SELECT * FROM users WHERE lower(email)=?", (email,)).fetchone()
    if user:
        if not user["active"]:
            raise SSOError("Your Clause account is deactivated. Ask an administrator.")
        changes = {"sso_subject": subject}
        if role and role != user["role"]:
            changes["role"] = role
            add_audit(conn, "User updated", "SSO", details=f"{user['email']}: role {user['role']} → {role} (from Entra app roles)")
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        conn.execute(f"UPDATE users SET {', '.join(f'{k}=?' for k in changes)} WHERE id=?", (*changes.values(), user["id"]))
        return conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    if env("SSO_AUTO_PROVISION", "1") != "1":
        raise SSOError("You do not have a Clause account yet. Ask an administrator to add you.")
    role = role or env("SSO_DEFAULT_ROLE", "viewer")
    if role not in ROLES:
        role = "viewer"
    cursor = conn.execute(
        "INSERT INTO users (email, name, role, password_hash, active, created_at, sso_subject) VALUES (?,?,?,?,1,?,?)",
        (email, name, role, "!sso-only", utc_now(), subject),
    )
    add_audit(conn, "User created", "SSO", details=f"{name} ({email}) as {role} on first single sign-on")
    return conn.execute("SELECT * FROM users WHERE id=?", (cursor.lastrowid,)).fetchone()


# --- endpoints -------------------------------------------------------------------------

@router.get("/config")
def auth_config():
    """What the sign-in screen should offer. Public."""
    return {"sso_enabled": enabled(), "sso_label": env("SSO_LABEL", "Sign in with Microsoft"),
            "password_login": password_login_enabled() or not enabled()}


def fail(message: str) -> RedirectResponse:
    return RedirectResponse(f"{app_url()}/?{urlencode({'sso_error': message[:300]})}", status_code=303)


@router.get("/sso/login")
def sso_login():
    if not enabled():
        raise HTTPException(404, "Single sign-on is not configured.")
    try:
        authorize = discovery()["authorization_endpoint"]
    except SSOError as exc:
        return fail(str(exc))
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    now = datetime.now(timezone.utc)
    with connection() as conn:
        conn.execute("DELETE FROM sso_states WHERE created_at < ?", ((now - timedelta(minutes=STATE_MINUTES)).isoformat(),))
        conn.execute("INSERT INTO sso_states (state_hash, nonce, code_verifier, created_at) VALUES (?,?,?,?)",
                     (hashlib.sha256(state.encode()).hexdigest(), nonce, verifier, now.isoformat()))
    params = {"client_id": env("SSO_CLIENT_ID"), "response_type": "code", "redirect_uri": redirect_uri(),
              "response_mode": "query", "scope": "openid profile email", "state": state, "nonce": nonce,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    return RedirectResponse(f"{authorize}?{urlencode(params)}", status_code=303)


@router.get("/sso/callback")
def sso_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        return fail(error_description.splitlines()[0] if error_description else f"Sign-in was cancelled ({error}).")
    if not enabled() or not code or not state:
        return fail("The sign-in response was incomplete. Try again.")
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=STATE_MINUTES)).isoformat()
    with connection() as conn:
        row = conn.execute("SELECT * FROM sso_states WHERE state_hash=?", (hashlib.sha256(state.encode()).hexdigest(),)).fetchone()
        if row:  # single use
            conn.execute("DELETE FROM sso_states WHERE state_hash=?", (row["state_hash"],))
    if not row or row["created_at"] < cutoff:
        return fail("The sign-in took too long or was already used. Try again.")
    try:
        tokens = http_post_form(discovery()["token_endpoint"], {
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(),
            "client_id": env("SSO_CLIENT_ID"), "client_secret": env("SSO_CLIENT_SECRET"),
            "code_verifier": row["code_verifier"],
        })
        if "id_token" not in tokens:
            raise SSOError("The identity provider did not return an ID token.")
        claims = verify_id_token(tokens["id_token"], row["nonce"])
        with connection() as conn:
            user = find_or_create_user(conn, claims)
            add_audit(conn, "Signed in", user["name"], details="Single sign-on")
    except SSOError as exc:
        return fail(str(exc))
    except httpx.HTTPError as exc:
        return fail(f"Could not reach the identity provider ({exc.__class__.__name__}).")
    response = RedirectResponse(f"{app_url()}/", status_code=303)
    set_session_cookie(response, create_session(user["id"]))
    return response

