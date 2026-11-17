"""Phone alerts: Web Push encryption and signing, device sign-up, who gets alerted, and forgetting dead devices."""
import base64
import json
import struct

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from cascade_guard.company import alerts

from .test_accounts import X, _signup, site  # noqa: F401  (fixture)

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


def _browser():
    """A browser's push keys, as PushManager.subscribe() makes them."""
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = b"0123456789abcdef"
    return key, alerts.b64u(pub), alerts.b64u(auth)


def _decrypt(body: bytes, key, p256dh: str, auth: str) -> bytes:
    """What the browser does with an aes128gcm message (RFC 8291), written separately from the sender."""
    salt, rs, idlen = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
    as_public = body[21:21 + idlen]
    assert rs == 4096 and idlen == 65
    shared = key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ua_public = alerts.unb64u(p256dh)
    ikm = alerts._hkdf(alerts.unb64u(auth), shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = alerts._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = alerts._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, body[21 + idlen:], None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def test_rfc8291_example_vector():
    """The worked example in RFC 8291 section 5 gives byte-for-byte the same message."""
    u = alerts.unb64u
    sender = ec.derive_private_key(int.from_bytes(u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
                                   ec.SECP256R1())
    body = alerts.encrypt(b"When I grow up, I want to be a watermelon",
                          "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                          "BTBZMqHH6r4Tts7J_aSIgg", _sender_key=sender, _salt=u("DGv6ra1nlYgDCS1FRnbzlw"))
    assert alerts.b64u(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3"
        "jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def test_message_only_that_browser_can_read():
    key, p256dh, auth = _browser()
    body = alerts.encrypt(b'{"title": "New lead"}', p256dh, auth)
    assert _decrypt(body, key, p256dh, auth) == b'{"title": "New lead"}'
    other, _, _ = _browser()
    with pytest.raises(Exception):  # noqa: B017 - any failure: the wrong key cannot read it
        _decrypt(body, other, p256dh, auth)


def test_vapid_signature_verifies(site):  # noqa: F811
    site()
    head = alerts._vapid_header(ENDPOINT)
    token, k = head.split("vapid t=")[1].split(", k=")
    h, c, s = token.split(".")
    claims = json.loads(alerts.unb64u(c))
    assert claims["aud"] == "https://fcm.googleapis.com" and claims["sub"].startswith(("mailto:", "https://"))
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), alerts.unb64u(k))
    raw = alerts.unb64u(s)
    pub.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
               f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))
    assert k == alerts.vapid_public_key() == alerts.vapid_public_key()  # made once, then kept


def test_only_real_push_services():
    assert alerts.allowed_endpoint(ENDPOINT)
    assert alerts.allowed_endpoint("https://web.push.apple.com/QGhk")
    assert alerts.allowed_endpoint("https://updates.push.services.mozilla.com/wpush/v2/x")
    for bad in ("http://fcm.googleapis.com/x", "https://evil.example/fcm.googleapis.com", "https://169.254.169.254/",
                "https://fcm.googleapis.com.evil.example/x", "https://localhost/x"):
        assert not alerts.allowed_endpoint(bad), bad


@pytest.fixture()
def sent(monkeypatch):
    """Records what would be sent, and runs the background work at once."""
    calls = []

    def fake_post(url, **kw):
        calls.append({"url": url, **kw})
        return httpx.Response(410 if "gone" in url else 201, request=httpx.Request("POST", url))
    monkeypatch.setattr(alerts.httpx, "post", fake_post)
    monkeypatch.setattr(alerts, "_submit", lambda fn, *a: fn(*a))
    alerts._last_unanswered.clear()
    return calls


def _device(c, endpoint=ENDPOINT):
    key, p256dh, auth = _browser()
    r = c.post("/app/alerts/devices", headers=X, json={"kind": "web", "endpoint": endpoint, "p256dh": p256dh,
                                                       "auth": auth, "label": "Chrome on Android"})
    assert r.status_code == 200, r.text
    return key, p256dh, auth


def test_new_lead_alerts_the_manager(site, sent):  # noqa: F811
    c, visitor = site(), site()
    _signup(c)
    info = c.get("/app/alerts").json()
    assert len(alerts.unb64u(info["web_key"])) == 65 and info["settings"] == {"lead": True, "unanswered": True}
    assert info["android"] is False and info["devices"] == []
    key, p256dh, auth = _device(c)
    assert c.get("/app/alerts").json()["devices"][0]["label"] == "Chrome on Android"
    widget = c.get("/widget/key").json()["key"]
    assert visitor.post("/widget/lead", json={"key": widget, "contact": "98200 00000",
                                              "question": "Hostel fees?"}).status_code == 200
    assert len(sent) == 1 and sent[0]["url"] == ENDPOINT
    hdr = sent[0]["headers"]
    assert hdr["Content-Encoding"] == "aes128gcm" and hdr["Authorization"].startswith("vapid t=")
    msg = json.loads(_decrypt(sent[0]["content"], key, p256dh, auth))
    assert msg["title"] == "New lead from your website" and "98200 00000" in msg["body"] and msg["url"] == "/app#leads"
    assert c.get("/app/alerts").json()["devices"][0]["last_ok_at"]


def test_unanswered_alerts_are_spaced_out_and_can_be_turned_off(site, sent):  # noqa: F811
    c, visitor = site(), site()
    _signup(c)
    c.post("/kb/docs", headers=X, json={"name": "fees.txt", "text": "The fee for the JEE batch is 45,000 a year."})
    _device(c)
    widget = c.get("/widget/key").json()["key"]
    origin = {"Origin": "https://www.ashaclasses.in"}
    for q in ("Do you have a hostel?", "Is there a canteen?"):
        assert visitor.post("/widget/ask", headers=origin, json={"key": widget, "question": q}).json()["verdict"] == \
            "not_found"
    assert len(sent) == 1   # the second within 15 minutes is not sent
    visitor.post("/widget/ask", headers=origin, json={"key": widget, "question": "What is the fee for the JEE batch?"})
    assert len(sent) == 1   # answered questions never alert
    assert c.put("/app/alerts/settings", headers=X, json={"lead": False, "unanswered": False}).status_code == 200
    visitor.post("/widget/lead", json={"key": widget, "contact": "a@b.example", "question": ""})
    assert len(sent) == 1


def test_test_alert_and_dead_devices_are_forgotten(site, sent):  # noqa: F811
    c = site()
    _signup(c)
    assert c.post("/app/alerts/test", headers=X).status_code == 409
    _device(c)
    _device(c, "https://fcm.googleapis.com/fcm/send/gone")
    assert c.post("/app/alerts/test", headers=X).json() == {"sent": 1, "failed": 1}
    assert [d["label"] for d in c.get("/app/alerts").json()["devices"]] == ["Chrome on Android"]
    dev = c.get("/app/alerts").json()["devices"][0]["id"]
    assert c.delete(f"/app/alerts/devices/{dev}", headers=X).status_code == 200
    assert c.get("/app/alerts").json()["devices"] == []


def test_refuses_bad_devices_and_other_people(site, sent):  # noqa: F811
    c, other = site(), site()
    _signup(c)
    _, p256dh, auth = _browser()
    bad = c.post("/app/alerts/devices", headers=X, json={"kind": "web", "endpoint": "https://evil.example/x",
                                                         "p256dh": p256dh, "auth": auth})
    assert bad.status_code == 422
    short = c.post("/app/alerts/devices", headers=X, json={"kind": "web", "endpoint": ENDPOINT, "p256dh": "AAAA",
                                                           "auth": auth})
    assert short.status_code == 422
    assert c.post("/app/alerts/devices", headers=X, json={"kind": "android", "endpoint": "fcm-token-123456"}
                  ).status_code == 409   # no Firebase on this server
    _device(c)
    _signup(other, company="Other Co", email="o@other.example")
    assert other.get("/app/alerts").json()["devices"] == []
    mine = c.get("/app/alerts").json()["devices"][0]["id"]
    other.delete(f"/app/alerts/devices/{mine}", headers=X)
    assert len(c.get("/app/alerts").json()["devices"]) == 1
    key = {"X-API-Key": "owner-key"}
    assert site().get("/app/alerts", headers=key).status_code == 400   # alerts belong to a person, not a key


def test_android_app_alerts_through_firebase(site, sent, monkeypatch):  # noqa: F811
    pk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
    acc = {"type": "service_account", "project_id": "cg-test", "client_email": "push@cg-test.iam.example",
           "private_key": pem, "token_uri": "https://oauth2.googleapis.com/token"}
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT", base64.b64encode(json.dumps(acc).encode()).decode())
    alerts._fcm_token.clear()

    def fake_post(url, **kw):
        sent.append({"url": url, **kw})
        if url.endswith("/token"):
            return httpx.Response(200, json={"access_token": "ya29.x", "expires_in": 3600},
                                  request=httpx.Request("POST", url))
        return httpx.Response(200, json={"name": "projects/cg-test/messages/1"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(alerts.httpx, "post", fake_post)
    c, visitor = site(), site()
    _signup(c)
    assert c.get("/app/alerts").json()["android"] is True
    assert c.post("/app/alerts/devices", headers=X, json={"kind": "android", "endpoint": "fcm-token-123456",
                                                          "label": "Android app"}).status_code == 200
    widget = c.get("/widget/key").json()["key"]
    visitor.post("/widget/lead", json={"key": widget, "contact": "98200 00000", "question": "Fees?"})
    grant, push = sent[-2], sent[-1]
    h, cl, sig = grant["data"]["assertion"].split(".")
    pk.public_key().verify(alerts.unb64u(sig), f"{h}.{cl}".encode(),
                           __import__("cryptography.hazmat.primitives.asymmetric.padding",
                                      fromlist=["PKCS1v15"]).PKCS1v15(), hashes.SHA256())
    assert push["url"] == "https://fcm.googleapis.com/v1/projects/cg-test/messages:send"
    assert push["headers"]["Authorization"] == "Bearer ya29.x"
    m = push["json"]["message"]
    assert m["token"] == "fcm-token-123456" and "98200 00000" in m["notification"]["body"]
    assert m["data"]["url"] == "/app#leads"


def test_deleting_the_workspace_forgets_devices(site, sent):  # noqa: F811
    from cascade_guard.company import keys as K
    c = site()
    _signup(c)
    _device(c)
    assert c.post("/app/delete-workspace", headers=X, json={"confirm": "asha-classes"}).status_code == 200
    assert K._store()._read("SELECT COUNT(*) AS n FROM push_devices", ())[0]["n"] == 0


def test_people_can_delete_their_own_account(site):  # noqa: F811
    from cascade_guard.company import accounts as A
    c = site()
    _signup(c)
    assert "Delete workspace" in site().get("/delete-account").text
    only = c.post("/auth/delete-account", headers=X, json={"password": "correct horse"})
    assert only.status_code == 409 and "only manager" in only.json()["detail"]
    link = A.make_link("invite", workspace="asha-classes", role="member", email="m@asha.example")
    m = site()
    assert m.post("/auth/accept", headers=X, json={"token": link, "name": "M", "password": "member pass 1"}
                  ).status_code == 200
    assert m.post("/auth/delete-account", headers=X, json={"password": "wrong one"}).status_code == 403
    assert m.post("/auth/delete-account", headers=X, json={"password": "member pass 1"}).json() == {"deleted": True}
    assert A.find_user("m@asha.example") is None and m.get("/auth/me").status_code == 401
