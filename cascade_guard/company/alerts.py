"""Phone alerts: a notification when a website visitor leaves their contact details, or asks something the documents
can't answer.

Two ways to reach a phone, both free:
  web       Web Push (RFC 8030/8291/8292): Chrome or Edge on Android and computers, Firefox, and the iPhone home-screen
            app (iOS 16.4+). Works with nothing to set up: the server makes its own signing key (VAPID) once and keeps
            it in the database.
  android   The Android app, through Firebase Cloud Messaging. Needs a free Firebase project: set FCM_SERVICE_ACCOUNT
            (the service account JSON) on the server, and build the app with google-services.json (see MOBILE.md).

Only managers get alerts, and only for their own workspace. Sending happens in the background, so a visitor's
answer is never slowed down. A device that the push service says is gone is forgotten.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cascade_guard.company import accounts as A
from cascade_guard.company import keys as K
from cascade_guard.company.keys import Principal, caller, need

log = logging.getLogger("cascade_guard.alerts")
router = APIRouter()

# Push services browsers use. The server only ever sends to these, so a made-up "endpoint" can't make it call
# anywhere else.
PUSH_HOSTS = ("fcm.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com")
TTL = 24 * 3600
UNANSWERED_GAP = 15 * 60          # at most one "unanswered question" alert per workspace in this many seconds
MAX_FAILS = 5

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="alerts")
_last_unanswered: dict[str, float] = {}
_lock = threading.Lock()


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _submit(fn, *args) -> None:
    _pool.submit(fn, *args)


# ------------------------------------------------------------------ the server's own signing key (VAPID)

def _config(name: str, make) -> str:
    store = K._store()
    rows = store._read("SELECT value FROM app_config WHERE name = ?", (name,))
    if rows:
        return rows[0]["value"]
    value = make()
    with store._tx() as c:   # two servers starting at once: the first one's key wins, both read it back
        c.execute("INSERT INTO app_config (name, value) VALUES (?, ?) ON CONFLICT (name) DO NOTHING", (name, value))
    return store._read("SELECT value FROM app_config WHERE name = ?", (name,))[0]["value"]


def _vapid_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    def make() -> str:
        if os.getenv("VAPID_PRIVATE_KEY"):
            return os.environ["VAPID_PRIVATE_KEY"].strip()
        k = ec.generate_private_key(ec.SECP256R1())
        return b64u(k.private_numbers().private_value.to_bytes(32, "big"))
    raw = unb64u(_config("vapid_private_key", make))
    key = ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return key, public


def vapid_public_key() -> str:
    return b64u(_vapid_key()[1])


def _vapid_header(endpoint: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    key, public = _vapid_key()
    u = urlparse(endpoint)
    contact = os.getenv("CONTACT_EMAIL", "")
    sub = f"mailto:{contact}" if contact else (os.getenv("PUBLIC_URL") or "https://github.com/kanav10213/cascade-guard")
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": sub},
                           separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
    sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={head}.{body}.{sig}, k={b64u(public)}"


# ------------------------------------------------------------------ Web Push message encryption (RFC 8291)

def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def encrypt(payload: bytes, p256dh: str, auth: str, *, _sender_key=None, _salt: bytes | None = None) -> bytes:
    """The message body as the browser expects it (aes128gcm). Only that browser can read it."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ua_public = unb64u(p256dh)
    secret = unb64u(auth)
    sender = _sender_key or ec.generate_private_key(ec.SECP256R1())
    as_public = sender.public_key().public_bytes(serialization.Encoding.X962,
                                                 serialization.PublicFormat.UncompressedPoint)
    shared = sender.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = _hkdf(secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = _salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!I", 4096) + bytes([len(as_public)]) + as_public + body


def allowed_endpoint(endpoint: str) -> bool:
    u = urlparse(endpoint)
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def _send_web(dev: dict, message: dict) -> bool | None:
    """True sent, False gone (forget the device), None failed this time."""
    if not allowed_endpoint(dev["endpoint"]):
        return False
    body = encrypt(json.dumps(message).encode(), dev["p256dh"], dev["auth"])
    r = httpx.post(dev["endpoint"], content=body, timeout=15, headers={
        "Authorization": _vapid_header(dev["endpoint"]), "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream", "TTL": str(TTL), "Urgency": "high",
        "Topic": message.get("tag", "cg")[:32]})
    if r.status_code in (404, 410):
        return False
    if r.status_code >= 400:
        log.warning("web push refused (%s): %s", r.status_code, r.text[:200])
        return None
    return True


# ------------------------------------------------------------------ Firebase Cloud Messaging (the Android app)

_fcm_token: dict = {}


def fcm_on() -> bool:
    return bool(_fcm_account())


def _fcm_account() -> dict | None:
    raw = os.getenv("FCM_SERVICE_ACCOUNT", "").strip()
    if not raw:
        return None
    try:
        acc = json.loads(raw if raw.startswith("{") else base64.b64decode(raw).decode())
        return acc if acc.get("private_key") and acc.get("client_email") and acc.get("project_id") else None
    except (ValueError, UnicodeDecodeError):
        log.warning("FCM_SERVICE_ACCOUNT is not a service account JSON")
        return None


def _fcm_access_token(acc: dict) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    if _fcm_token.get("exp", 0) > time.time() + 60:
        return _fcm_token["value"]
    now = int(time.time())
    aud = acc.get("token_uri") or "https://oauth2.googleapis.com/token"
    head = b64u(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"iss": acc["client_email"], "scope": "https://www.googleapis.com/auth/firebase.messaging",
                              "aud": aud, "iat": now, "exp": now + 3600}, separators=(",", ":")).encode())
    key = serialization.load_pem_private_key(acc["private_key"].encode(), password=None)
    sig = key.sign(f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())  # type: ignore[call-arg,union-attr]
    r = httpx.post(aud, timeout=15, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                          "assertion": f"{head}.{claims}.{b64u(sig)}"})
    r.raise_for_status()
    j = r.json()
    _fcm_token.update(value=j["access_token"], exp=time.time() + int(j.get("expires_in", 3600)))
    return j["access_token"]


def _send_android(dev: dict, message: dict) -> bool | None:
    acc = _fcm_account()
    if not acc:
        return None
    r = httpx.post(f"https://fcm.googleapis.com/v1/projects/{acc['project_id']}/messages:send", timeout=15,
                   headers={"Authorization": f"Bearer {_fcm_access_token(acc)}"},
                   json={"message": {"token": dev["endpoint"],
                                     "notification": {"title": message["title"], "body": message["body"]},
                                     "data": {"url": message["url"], "tag": message.get("tag", "")},
                                     "android": {"priority": "high", "ttl": f"{TTL}s",
                                                 "notification": {"tag": message.get("tag", ""),
                                                                  "color": "#23527c"}}}})
    if r.status_code == 404 or "UNREGISTERED" in r.text or (r.status_code == 400 and "registration" in r.text):
        return False
    if r.status_code >= 400:
        log.warning("FCM refused (%s): %s", r.status_code, r.text[:200])
        return None
    return True


# ------------------------------------------------------------------ sending

def _devices(workspace: str, user_id: int | None = None) -> list[dict]:
    sql = ("SELECT d.* FROM push_devices d JOIN users u ON u.id = d.user_id WHERE d.workspace = ? "
           "AND u.workspace = d.workspace AND u.role = 'manager' AND u.disabled_at IS NULL")
    args: tuple = (workspace,)
    if user_id is not None:
        sql += " AND d.user_id = ?"
        args += (user_id,)
    return K._store()._read(sql, args)


def deliver(devices: list[dict], message: dict) -> dict:
    sent = failed = 0
    store = K._store()
    for dev in devices:
        try:
            ok = (_send_android if dev["kind"] == "android" else _send_web)(dev, message)
        except Exception as e:  # one bad device must not stop the others
            log.warning("alert to device %s failed: %s", dev.get("id"), e)
            ok = None
        with store._tx() as c:
            if ok:
                sent += 1
                c.execute("UPDATE push_devices SET last_ok_at = ?, fails = 0 WHERE id = ?", (A._now(), dev["id"]))
            elif ok is False or int(dev.get("fails") or 0) + 1 >= MAX_FAILS:
                failed += 1
                c.execute("DELETE FROM push_devices WHERE id = ?", (dev["id"],))
            else:
                failed += 1
                c.execute("UPDATE push_devices SET fails = fails + 1 WHERE id = ?", (dev["id"],))
    return {"sent": sent, "failed": failed}


def notify(workspace: str, kind: Literal["lead", "unanswered"], title: str, body: str, url: str) -> None:
    """Called from the widget. Returns at once; the alert goes out in the background."""
    try:
        conf = A.settings(workspace)
        if not conf.get(f"alert_{kind}", True):
            return
        if kind == "unanswered":
            with _lock:
                if time.time() - _last_unanswered.get(workspace, 0) < UNANSWERED_GAP:
                    return
                _last_unanswered[workspace] = time.time()
        devices = _devices(workspace)
        if devices:
            _submit(deliver, devices, {"title": title[:80], "body": body[:240], "url": url, "tag": kind})
    except Exception as e:  # alerts are extra; the visitor's request must still succeed
        log.warning("could not queue an alert for %s: %s", workspace, e)


def lead_alert(workspace: str, contact: str, question: str) -> None:
    body = contact + (f": {question}" if question else "")
    notify(workspace, "lead", "New lead from your website", body, "/app#leads")


def unanswered_alert(workspace: str, question: str) -> None:
    notify(workspace, "unanswered", "A question your documents couldn't answer", question, "/app#overview")


# ------------------------------------------------------------------ the dashboard's alert settings

def _person(p: Principal) -> int:
    need(p, "manager")
    if p.key_id >= 0:
        raise HTTPException(status_code=400, detail="Sign in to the dashboard to turn on alerts for a device.")
    return -p.key_id


class DeviceIn(BaseModel):
    kind: Literal["web", "android"]
    endpoint: str = Field(min_length=10, max_length=2000)
    p256dh: str | None = Field(default=None, max_length=200)
    auth: str | None = Field(default=None, max_length=100)
    label: str = Field(default="", max_length=80)


class AlertSettingsIn(BaseModel):
    lead: bool
    unanswered: bool


@router.get("/app/alerts")
def alerts_get(p: Principal = Depends(caller)):  # noqa: B008
    uid = _person(p)
    conf = A.settings(p.workspace)
    mine = K._store()._read("SELECT id, kind, label, created_at, last_ok_at FROM push_devices WHERE user_id = ? "
                            "AND workspace = ? ORDER BY id DESC", (uid, p.workspace))
    return {"web_key": vapid_public_key(), "android": fcm_on(), "devices": mine,
            "settings": {"lead": conf.get("alert_lead", True), "unanswered": conf.get("alert_unanswered", True)}}


@router.post("/app/alerts/devices")
def alerts_add(req: DeviceIn, p: Principal = Depends(caller)):  # noqa: B008
    uid = _person(p)
    if req.kind == "web":
        if not allowed_endpoint(req.endpoint):
            raise HTTPException(status_code=422, detail="This browser's push service isn't supported.")
        try:
            if len(unb64u(req.p256dh or "")) != 65 or len(unb64u(req.auth or "")) != 16:
                raise ValueError
        except ValueError as e:
            raise HTTPException(status_code=422, detail="The browser sent incomplete alert keys.") from e
    elif not fcm_on():
        raise HTTPException(status_code=409, detail="Alerts for the Android app aren't set up on this server yet.")
    store = K._store()
    with store._tx() as c:   # the same phone signing in again takes the device over
        c.execute("DELETE FROM push_devices WHERE endpoint = ?", (req.endpoint,))
        c.execute("INSERT INTO push_devices (workspace, user_id, kind, endpoint, p256dh, auth, label, created_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (p.workspace, uid, req.kind, req.endpoint, req.p256dh, req.auth,
                                                      req.label.strip() or req.kind, A._now()))
    A.event(p.workspace, p.name, "alerts on", req.label.strip() or req.kind)
    return {"ok": True}


@router.delete("/app/alerts/devices/{device_id}")
def alerts_remove(device_id: int, p: Principal = Depends(caller)):  # noqa: B008
    uid = _person(p)
    with K._store()._tx() as c:
        c.execute("DELETE FROM push_devices WHERE id = ? AND user_id = ?", (device_id, uid))
    return {"ok": True}


@router.put("/app/alerts/settings")
def alerts_settings(req: AlertSettingsIn, p: Principal = Depends(caller)):  # noqa: B008
    need(p, "manager")
    A.save_settings(p.workspace, {"alert_lead": req.lead, "alert_unanswered": req.unanswered})
    return {"ok": True}


@router.post("/app/alerts/test")
def alerts_test(p: Principal = Depends(caller)):  # noqa: B008
    uid = _person(p)
    devices = _devices(p.workspace, uid)
    if not devices:
        raise HTTPException(status_code=409, detail="Turn on alerts on this device first.")
    return deliver(devices, {"title": "Alerts are working", "url": "/app#leads", "tag": "test",
                             "body": "You'll get one like this when a visitor leaves their number."})
