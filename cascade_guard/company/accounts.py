"""People and companies: accounts with passwords or single sign-on, sessions, workspaces, invites, activity.

    workspace   one company: its plan, settings, documents, people and keys
    user        a person in one workspace (member or manager), with a password and/or SSO
    session     a signed-in browser: a random cookie, stored only as its SHA-256, valid 30 days
    invite      a one-time link to join a workspace or to set a new password (stored hashed, expires)
    event       the workspace's activity log: who did what, when

Passwords are hashed with scrypt (a per-user salt). Sign-in attempts are limited per address and email. A request
signed in by cookie must carry the header X-CG: 1 to change anything, which a page on another site cannot send.

Single sign-on (OpenID Connect: Google, Microsoft Entra, Okta, ...): set OIDC_ISSUER, OIDC_CLIENT_ID and
OIDC_CLIENT_SECRET; the redirect URL to register is <PUBLIC_URL>/auth/sso/callback. OIDC_WORKSPACE puts everyone who
signs in that way into one workspace (a company's own install); OIDC_DOMAINS limits which email domains may.
Email (invites, password resets) is sent when SMTP_HOST is set; otherwise the link is shown to the person inviting.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from cascade_guard.company import keys as K
from cascade_guard.company.keys import Principal

COOKIE = "cg_session"
SESSION_DAYS = 30
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")
DEFAULT_SETTINGS: dict = {"widget": {"title": "Ask us", "color": "#23527c", "greeting": "", "contact": ""},
                    "domains": [], "log_questions": True, "collect_leads": True}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _later(**kw) -> str:
    return (datetime.now(UTC) + timedelta(**kw)).isoformat(timespec="seconds")


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _store():
    return K._store()


# ------------------------------------------------------------------ passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def check_password(password: str, stored: str | None) -> bool:
    try:
        kind, salt, dk = (stored or "").split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


def good_password(password: str) -> None:
    if len(password or "") < 8:
        raise HTTPException(status_code=422, detail="Use a password of at least 8 characters.")
    if len(password) > 200:
        raise HTTPException(status_code=422, detail="That password is too long.")


_tries: dict[str, list[float]] = {}
_lock = threading.Lock()


def throttle(what: str, limit: int = 8, window: int = 900) -> None:
    """At most `limit` attempts per `window` seconds for this address/email."""
    now = time.time()
    with _lock:
        recent = [t for t in _tries.get(what, []) if now - t < window]
        if len(recent) >= limit:
            raise HTTPException(status_code=429, detail="Too many attempts. Please wait 15 minutes and try again.")
        recent.append(now)
        _tries[what] = recent
        if len(_tries) > 5000:
            for k in [k for k, v in _tries.items() if not v or now - v[-1] > window]:
                del _tries[k]


# ------------------------------------------------------------------ workspaces

def get_workspace(name: str, store=None) -> dict | None:
    rows = (store or _store())._read("SELECT * FROM workspaces WHERE name = ?", (name,))
    if not rows:
        return None
    w = dict(rows[0])
    try:
        saved = json.loads(w.get("settings") or "{}")
    except ValueError:
        saved = {}
    w["settings"] = {**DEFAULT_SETTINGS, **saved, "widget": {**DEFAULT_SETTINGS["widget"], **(saved.get("widget") or {})}}
    return w


def ensure_workspace(name: str, title: str = "", plan: str = "free", store=None) -> dict:
    store = store or _store()
    w = get_workspace(name, store)
    if w:
        return w
    limit = K.plans().get(plan, 0)
    with store._tx() as c:
        c.execute("INSERT INTO workspaces (name, title, plan, monthly_limit, settings, created_at) VALUES (?, ?, ?, ?, ?, ?)"
                  " ON CONFLICT (name) DO NOTHING", (name, title or name, plan, limit, "{}", _now()))
    return get_workspace(name, store) or {}


def settings(workspace: str, store=None) -> dict:
    w = get_workspace(workspace, store)
    return w["settings"] if w else json.loads(json.dumps(DEFAULT_SETTINGS))


def save_settings(workspace: str, changes: dict, store=None) -> dict:
    store = store or _store()
    ensure_workspace(workspace, store=store)
    cur = settings(workspace, store)
    for k, v in changes.items():
        cur[k] = {**cur.get(k, {}), **v} if k == "widget" and isinstance(v, dict) else v
    with store._tx() as c:
        c.execute("UPDATE workspaces SET settings = ? WHERE name = ?", (json.dumps(cur), workspace))
    return cur


def event(workspace: str, actor: str, action: str, detail: str = "", store=None) -> None:
    try:
        with (store or _store())._tx() as c:
            c.execute("INSERT INTO events (workspace, at, actor, action, detail) VALUES (?, ?, ?, ?, ?)",
                      (workspace, _now(), actor[:120], action[:60], detail[:500]))
    except Exception:  # noqa: S110 - the activity log must never break the action it records
        pass


# ------------------------------------------------------------------ users

def _user_public(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "name": u.get("name") or "", "workspace": u["workspace"],
            "role": u["role"], "created_at": u["created_at"], "last_login_at": u.get("last_login_at"),
            "sso_only": not u.get("password_hash"), "active": not u.get("disabled_at")}


def find_user(email: str, store=None) -> dict | None:
    rows = (store or _store())._read("SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),))
    return dict(rows[0]) if rows else None


def create_user(email: str, workspace: str, *, role: str = "member", name: str = "", password: str | None = None,
                store=None) -> dict:
    email = (email or "").strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(status_code=422, detail="That email address doesn't look right.")
    if role not in ("member", "manager"):
        raise HTTPException(status_code=422, detail="role must be member or manager")
    store = store or _store()
    if find_user(email, store):
        raise HTTPException(status_code=409, detail="This email already has an account. Sign in instead.")
    with store._tx() as c:
        c.insert("INSERT INTO users (email, name, password_hash, workspace, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (email, name.strip()[:100], hash_password(password) if password else None, workspace, role, _now()))
    return _user_public(find_user(email, store) or {})


def signup(company: str, email: str, password: str, name: str = "", store=None) -> dict:
    """A new company: its workspace on the free plan and its first manager."""
    from cascade_guard.company.billing import _workspace_for
    good_password(password)
    store = store or _store()
    email = (email or "").strip().lower()
    if find_user(email, store) or K.workspace_of_email(email, store):
        raise HTTPException(status_code=409, detail="This email already has an account. Sign in instead.")
    try:
        ws = _workspace_for(company.strip(), email, email + str(time.time()))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    if get_workspace(ws, store):
        ws = K.norm_workspace(f"{ws[:50]}-{secrets.token_hex(3)}")
    ensure_workspace(ws, company.strip()[:80], "free", store)
    user = create_user(email, ws, role="manager", name=name, password=password, store=store)
    event(ws, email, "workspace created", company.strip()[:80], store)
    return user


def list_users(workspace: str, store=None) -> list[dict]:
    return [_user_public(dict(r)) for r in (store or _store())._read(
        "SELECT * FROM users WHERE workspace = ? ORDER BY id", (workspace,))]


def set_password(user_id: int, password: str, store=None) -> None:
    good_password(password)
    store = store or _store()
    with store._tx() as c:
        c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
        c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))   # every other browser signs in again


# ------------------------------------------------------------------ sessions

def start_session(user_id: int, store=None) -> str:
    token = secrets.token_urlsafe(32)
    store = store or _store()
    with store._tx() as c:
        c.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                  (_h(token), user_id, _now(), _later(days=SESSION_DAYS)))
        c.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), user_id))
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (_now(),))
    return token


def end_session(token: str, store=None) -> None:
    with (store or _store())._tx() as c:
        c.execute("DELETE FROM sessions WHERE token_hash = ?", (_h(token),))


def session_user(token: str, store=None) -> dict | None:
    if not token or len(token) > 200:
        return None
    rows = (store or _store())._read(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?"
        " AND u.disabled_at IS NULL", (_h(token), _now()))
    return dict(rows[0]) if rows else None


def principal_of(user: dict, store=None) -> Principal:
    """The signed-in person as a principal. Usage is counted against their workspace (key_id < 0: a person)."""
    w = get_workspace(user["workspace"], store) or {"plan": "free", "monthly_limit": K.plans().get("free", 0)}
    return Principal(user["workspace"], user["role"], -int(user["id"]), user.get("name") or user["email"],
                     w["plan"], int(w["monthly_limit"]))


def login(email: str, password: str, address: str = "", store=None) -> tuple[str, dict]:
    email = (email or "").strip().lower()
    throttle(f"login:{address}", limit=20)
    throttle(f"login:{email}")
    user = find_user(email, store)
    # the same work and the same message whether or not the account exists
    ok = check_password(password, user["password_hash"] if user else hash_password("x"))
    if not user or not ok or user.get("disabled_at"):
        raise HTTPException(status_code=401, detail="Wrong email or password.")
    event(user["workspace"], email, "signed in", store=store)
    return start_session(user["id"], store), _user_public(user)


# ------------------------------------------------------------------ invites and password resets

def make_link(kind: str, *, workspace: str | None = None, role: str | None = None, email: str | None = None,
              user_id: int | None = None, hours: int = 72, store=None) -> str:
    token = secrets.token_urlsafe(32)
    with (store or _store())._tx() as c:
        c.execute("INSERT INTO invites (token_hash, kind, workspace, role, email, user_id, created_at, expires_at)"
                  " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (_h(token), kind, workspace, role, (email or "").strip().lower() or None, user_id, _now(),
                   _later(hours=hours)))
    return token


def read_link(token: str, kind: str, store=None) -> dict:
    rows = (store or _store())._read("SELECT * FROM invites WHERE token_hash = ? AND kind = ? AND used_at IS NULL AND"
                                     " expires_at > ?", (_h(token or ""), kind, _now()))
    if not rows:
        raise HTTPException(status_code=404, detail="This link has expired or was already used. Ask for a new one.")
    return dict(rows[0])


def use_link(token: str, store=None) -> None:
    with (store or _store())._tx() as c:
        c.execute("UPDATE invites SET used_at = ? WHERE token_hash = ?", (_now(), _h(token)))


def accept_invite(token: str, name: str, password: str, store=None) -> dict:
    inv = read_link(token, "invite", store)
    good_password(password)
    user = create_user(inv["email"], inv["workspace"], role=inv["role"] or "member", name=name, password=password,
                       store=store)
    use_link(token, store)
    event(inv["workspace"], inv["email"], "joined", inv["role"] or "member", store)
    return user


def reset_password(token: str, password: str, store=None) -> None:
    link = read_link(token, "reset", store)
    set_password(int(link["user_id"]), password, store)
    use_link(token, store)


# ------------------------------------------------------------------ email (optional)

def email_on() -> bool:
    return bool(os.getenv("SMTP_HOST"))


def send_email(to: str, subject: str, body: str) -> bool:
    """Send a plain email if SMTP is set up. Never raises: returns whether it was handed to the mail server."""
    if not email_on():
        return False
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"] = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "no-reply@localhost"
    msg["To"], msg["Subject"] = to, subject
    msg.set_content(body)
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
        with (smtplib.SMTP_SSL(os.environ["SMTP_HOST"], port, timeout=15) if port == 465
              else smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=15)) as s:
            if port != 465:
                s.starttls()
            if os.getenv("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.getenv("SMTP_PASSWORD", ""))
            s.send_message(msg)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ single sign-on (OpenID Connect)

_oidc: dict = {}
_states: dict[str, tuple[float, str, str]] = {}   # state -> (made at, nonce, PKCE verifier)


def sso_on() -> bool:
    return all(os.getenv(k) for k in ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"))


def sso_name() -> str:
    iss = os.getenv("OIDC_ISSUER", "")
    return os.getenv("OIDC_NAME") or ("Google" if "google" in iss else "Microsoft" if "microsoft" in iss else
                                       "Okta" if "okta" in iss else "your company login")


def _discover() -> dict:
    iss = os.environ["OIDC_ISSUER"].rstrip("/")
    if _oidc.get("issuer") != iss:
        import httpx
        r = httpx.get(iss + "/.well-known/openid-configuration", timeout=10)
        r.raise_for_status()
        _oidc.clear()
        _oidc.update({"issuer": iss, **r.json()})
    return _oidc


def sso_start(redirect_uri: str) -> str:
    """The identity provider's sign-in URL (authorization code flow with PKCE, state and nonce)."""
    from urllib.parse import urlencode
    conf = _discover()
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    now = time.time()
    with _lock:
        for k in [k for k, v in _states.items() if now - v[0] > 600]:
            del _states[k]
        _states[state] = (now, nonce, verifier)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return conf["authorization_endpoint"] + "?" + urlencode({
        "response_type": "code", "client_id": os.environ["OIDC_CLIENT_ID"], "redirect_uri": redirect_uri,
        "scope": "openid email profile", "state": state, "nonce": nonce, "code_challenge": challenge,
        "code_challenge_method": "S256"})


def sso_finish(code: str, state: str, redirect_uri: str, store=None) -> tuple[str, dict]:
    """Exchange the code, check the ID token's claims, and sign the person in (creating them if allowed)."""
    import httpx
    with _lock:
        made = _states.pop(state or "", None)
    if not made or time.time() - made[0] > 600:
        raise HTTPException(status_code=400, detail="That sign-in took too long or was already used. Try again.")
    conf = _discover()
    r = httpx.post(conf["token_endpoint"], timeout=15, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "client_id": os.environ["OIDC_CLIENT_ID"], "client_secret": os.environ["OIDC_CLIENT_SECRET"],
        "code_verifier": made[2]})
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="The company login did not confirm the sign-in.")
    # The ID token comes straight from the provider's token endpoint over TLS, which OpenID Connect accepts in place
    # of checking its signature (Core 3.1.3.7); its claims are still checked.
    try:
        part = r.json()["id_token"].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (KeyError, IndexError, ValueError):
        raise HTTPException(status_code=401, detail="The company login sent no usable identity.") from None
    aud = claims.get("aud")
    if (claims.get("iss", "").rstrip("/") != conf.get("issuer", "").rstrip("/")
            or os.environ["OIDC_CLIENT_ID"] not in (aud if isinstance(aud, list) else [aud])
            or claims.get("nonce") != made[1] or float(claims.get("exp", 0)) < time.time()):
        raise HTTPException(status_code=401, detail="The company login's answer did not check out.")
    email = str(claims.get("email") or "").strip().lower()
    if not EMAIL.match(email) or claims.get("email_verified") is False:
        raise HTTPException(status_code=401, detail="The company login did not share a verified email address.")
    domains = [d.strip().lower() for d in os.getenv("OIDC_DOMAINS", "").split(",") if d.strip()]
    if domains and email.split("@")[1] not in domains:
        raise HTTPException(status_code=403, detail="This email's domain may not sign in here.")
    store = store or _store()
    user = find_user(email, store)
    if user and user.get("disabled_at"):
        raise HTTPException(status_code=403, detail="This account is turned off.")
    if not user:
        name = str(claims.get("name") or "")
        fixed = os.getenv("OIDC_WORKSPACE")
        if fixed:
            ws = K.norm_workspace(fixed)
            first = not list_users(ws, store)
            ensure_workspace(ws, os.getenv("COMPANY_NAME") or ws, "unlimited", store)
            create_user(email, ws, role="manager" if first else "member", name=name, store=store)
        else:
            from cascade_guard.company.saas import public
            if not public():
                raise HTTPException(status_code=403, detail="You don't have an account here yet. Ask for an invite.")
            from cascade_guard.company.billing import _workspace_for
            ws = _workspace_for(email.split("@")[1].split(".")[0], email, email + str(time.time()))
            ensure_workspace(ws, ws, "free", store)
            create_user(email, ws, role="manager", name=name, store=store)
        user = find_user(email, store)
    assert user is not None
    event(user["workspace"], email, "signed in", "single sign-on", store)
    return start_session(user["id"], store), _user_public(user)
