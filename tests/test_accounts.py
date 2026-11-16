"""Accounts, sessions, team, the dashboard's data, leads, widget settings, website import, export and SSO."""
import base64
import hashlib
import hmac
import json
import time

import pytest

from cascade_guard.company import accounts as A
from cascade_guard.company import kb, web
from cascade_guard.storage.db import Store  # noqa: F401

X = {"X-CG": "1"}


@pytest.fixture()
def site(monkeypatch, fresh_store):
    """A public server with no AI connected (answers quote the documents): returns a function making clients."""
    from fastapi.testclient import TestClient

    from cascade_guard.api import app
    from cascade_guard.company import saas
    from cascade_guard.llm_api import reset_client
    from cascade_guard.storage.db import reset_store
    monkeypatch.setenv("API_KEY", "owner-key")
    monkeypatch.setenv("PUBLIC_SIGNUP", "1")
    reset_store(fresh_store())
    reset_client(None)
    kb._cache.clear()
    A._tries.clear()
    saas._signups.clear()
    try:
        yield lambda: TestClient(app)
    finally:
        reset_client(None)
        reset_store(None)


def _signup(c, company="Asha Classes", email="asha@asha.example", password="correct horse"):
    r = c.post("/auth/signup", headers=X, json={"company": company, "email": email, "password": password,
                                                "agree": True})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def test_signup_signs_in_and_passwords_are_hashed(site):
    c = site()
    assert c.get("/app/overview").status_code == 401
    assert c.post("/auth/signup", headers=X, json={"company": "A Co", "email": "a@a.example", "password": "short",
                                                   "agree": True}).status_code == 422
    user = _signup(c)
    assert user["role"] == "manager" and user["workspace"] == "asha-classes"
    cookie = c.cookies.get("cg_session")
    assert cookie and "httponly" in str(c.post("/auth/login", json={"email": "asha@asha.example",
                                                                    "password": "correct horse"}).headers).lower()
    o = c.get("/app/overview").json()
    assert o["workspace"]["plan"] == "free" and o["workspace"]["monthly_limit"] == 50 and not o["setup"]["documents"]
    stored = A.find_user("asha@asha.example")["password_hash"]
    assert stored.startswith("scrypt$") and "correct horse" not in stored
    assert _dup(c).status_code == 409
    assert c.get("/auth/me").json()["user"]["email"] == "asha@asha.example"


def _dup(c):
    return c.post("/auth/signup", headers=X, json={"company": "Other", "email": "ASHA@asha.example",
                                                   "password": "another one", "agree": True})


def test_wrong_password_logout_and_throttle(site):
    c = site()
    _signup(c)
    assert c.post("/auth/logout", headers=X).status_code == 200 and c.get("/app/overview").status_code == 401
    bad = c.post("/auth/login", json={"email": "asha@asha.example", "password": "nope nope"})
    nobody = c.post("/auth/login", json={"email": "ghost@asha.example", "password": "nope nope"})
    assert bad.status_code == nobody.status_code == 401 and bad.json() == nobody.json()   # no hint which exists
    for _ in range(8):
        c.post("/auth/login", json={"email": "asha@asha.example", "password": "nope nope"})
    assert c.post("/auth/login", json={"email": "asha@asha.example", "password": "correct horse"}).status_code == 429


def test_a_cookie_alone_cannot_change_anything(site):
    c = site()
    _signup(c)
    assert c.post("/kb/docs", json={"name": "a.txt", "text": "Fees are ₹500."}).status_code == 403  # no X-CG header
    assert c.post("/kb/docs", headers=X, json={"name": "a.txt", "text": "Fees are ₹500."}).status_code == 200
    assert c.get("/kb/docs").json()["documents"][0]["name"] == "a.txt"


def test_team_invite_roles_and_reset(site):
    boss, staff = site(), site()
    _signup(boss)
    inv = boss.post("/app/team/invite", headers=X, json={"email": "ravi@asha.example", "role": "member"}).json()
    assert not inv["emailed"] and "/login?invite=" in inv["link"]
    token = inv["link"].split("invite=")[1]
    assert staff.get(f"/auth/invite?token={token}").json()["workspace"] == "Asha Classes"
    joined = staff.post("/auth/accept", json={"token": token, "name": "Ravi", "password": "ravi's password"}).json()
    assert joined["user"]["role"] == "member" and joined["user"]["workspace"] == "asha-classes"
    assert staff.post("/auth/accept", json={"token": token, "name": "x", "password": "again again"}).status_code == 404
    assert staff.get("/chat/info").json()["you"]["role"] == "member"
    assert staff.get("/app/team").status_code == 403 and staff.get("/app/conversations").status_code == 403
    people = boss.get("/app/team").json()["people"]
    ravi = next(u for u in people if u["email"] == "ravi@asha.example")
    me = next(u for u in people if u["role"] == "manager")
    assert boss.patch(f"/app/team/{me['id']}", headers=X, json={"role": "member"}).status_code == 409  # last manager
    link = boss.post(f"/app/team/{ravi['id']}/reset-link", headers=X).json()["link"]
    assert staff.post("/auth/reset", json={"token": link.split("reset=")[1], "password": "brand new pass"}).status_code == 200
    assert staff.get("/chat/info").status_code == 401                       # old sessions end with the password
    assert staff.post("/auth/login", json={"email": "ravi@asha.example", "password": "brand new pass"}).status_code == 200
    assert boss.delete(f"/app/team/{ravi['id']}", headers=X).status_code == 200
    assert staff.get("/chat/info").status_code == 401
    acts = [e["action"] for e in boss.get("/app/activity").json()["events"]]
    assert {"invited", "joined", "removed", "workspace created"} <= set(acts)


def test_conversations_overview_widget_settings_leads(site):
    c, visitor = site(), site()
    _signup(c)
    c.post("/kb/docs", headers=X, json={"name": "fees.txt", "text": "The fee for the JEE batch is ₹45,000 per year."})
    key = c.get("/widget/key").json()["key"]
    site_hdr = {"Origin": "https://www.ashaclasses.in"}
    a = visitor.post("/widget/ask", headers=site_hdr, json={"key": key, "question": "What is the fee for the JEE batch?"}).json()
    assert "45,000" in a["answer"] and a["token"]
    miss = visitor.post("/widget/ask", headers=site_hdr, json={"key": key, "question": "Do you have a hostel?"}).json()
    assert miss["verdict"] == "not_found"
    visitor.post("/widget/feedback", json={"key": key, "token": a["token"], "helpful": True})
    assert visitor.post("/widget/lead", json={"key": key, "contact": "98200 00000", "question": "Do you have a hostel?"}).status_code == 200
    o = c.get("/app/overview").json()
    assert o["last_30_days"]["questions"] == 2 and o["last_30_days"]["not_found"] == 1 and o["open_leads"] == 1
    assert o["unanswered"][0]["question"] == "Do you have a hostel?" and o["setup"]["widget"]
    assert o["last_30_days"]["helpful"] == 1 and o["workspace"]["used_this_month"] == 2
    conv = c.get("/app/conversations?verdict=not_found").json()["conversations"]
    assert len(conv) == 1 and conv[0]["channel"] == "widget"
    c.post("/chat/ask", headers=X, json={"question": "=HYPERLINK(1)"})
    assert "'=HYPERLINK(1)" in c.get("/app/conversations.csv").text                  # a formula can't run in Excel
    lead = c.get("/app/leads").json()["leads"][0]
    assert lead["contact"] == "98200 00000" and c.post(f"/app/leads/{lead['id']}/done", headers=X).status_code == 200
    # the look is set in the dashboard; the widget is limited to the company's own site
    put = c.put("/app/settings", headers=X, json={"widget": {"title": "Ask Asha", "color": "#0a7d55", "greeting": "Hi",
                                                             "contact": "Call 98200"},
                                                  "domains": ["https://www.AshaClasses.in/"], "collect_leads": False})
    assert put.json()["domains"] == ["ashaclasses.in"]
    assert visitor.get(f"/widget/config?key={key}", headers=site_hdr).json()["title"] == "Ask Asha"
    elsewhere = visitor.post("/widget/ask", headers={"Origin": "https://evil.example"}, json={"key": key, "question": "fee?"})
    assert elsewhere.status_code == 403
    assert visitor.post("/widget/ask", headers={"Origin": "https://m.ashaclasses.in"}, json={"key": key, "question": "fee?"}).status_code == 200
    assert visitor.post("/widget/lead", headers=site_hdr, json={"key": key, "contact": "someone@x.example"}).status_code == 403
    # logging can be turned off
    c.put("/app/settings", headers=X, json={"log_questions": False})
    before = len(c.get("/app/conversations").json()["conversations"])
    visitor.post("/widget/ask", headers=site_hdr, json={"key": key, "question": "What is the fee?"})
    assert len(c.get("/app/conversations").json()["conversations"]) == before


def test_companies_cannot_see_each_other_and_delete_removes_everything(site):
    a, b = site(), site()
    _signup(a)
    _signup(b, "Mehta Traders", "m@mehta.example", "mehta password")
    a.post("/kb/docs", headers=X, json={"name": "secret.txt", "text": "Asha's margin is 40 percent."})
    a.post("/chat/ask", headers=X, json={"question": "What is the margin?"})
    assert b.get("/kb/docs").json()["documents"] == [] and b.get("/app/conversations").json()["conversations"] == []
    assert b.get("/app/team").json()["people"][0]["email"] == "m@mehta.example"
    export = a.get("/app/export")
    data = export.json()
    assert "attachment" in export.headers["content-disposition"] and "40 percent" in data["documents"][0]["text"]
    assert data["people"][0]["email"] == "asha@asha.example" and "password" not in json.dumps(data).lower().replace(
        "password changed", "")
    assert a.post("/app/delete-workspace", headers=X, json={"confirm": "wrong"}).status_code == 422
    assert a.post("/app/delete-workspace", headers=X, json={"confirm": "asha-classes"}).json() == {"deleted": "asha-classes"}
    assert a.get("/app/overview").status_code == 401 and A.find_user("asha@asha.example") is None
    assert b.get("/app/overview").status_code == 200


def test_paying_upgrades_an_account_and_quota_is_per_workspace(site, monkeypatch):
    c = site()
    _signup(c)
    c.post("/kb/docs", headers=X, json={"name": "fees.txt", "text": "The fee is ₹45,000 per year."})
    from cascade_guard.storage.db import get_store
    with get_store()._tx() as tx:
        tx.execute("UPDATE workspaces SET monthly_limit = 2 WHERE name = 'asha-classes'")
    key = c.get("/widget/key").json()["key"]
    c.post("/chat/ask", headers=X, json={"question": "What is the fee?"})
    c.post("/widget/ask", json={"key": key, "question": "What is the fee?"})
    assert c.post("/chat/ask", headers=X, json={"question": "What is the fee?"}).status_code == 429
    assert c.post("/widget/ask", json={"key": key, "question": "What is the fee?"}).status_code == 429
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "rzp")
    ev = {"event": "payment_link.paid", "payload": {
        "payment_link": {"entity": {"id": "plink_1", "notes": {"plan": "team"}, "customer": {"email": "asha@asha.example"}}},
        "payment": {"entity": {"id": "pay_UPGRADE1", "amount": 199900}}}}
    body = json.dumps(ev).encode()
    r = c.post("/billing/razorpay", content=body,
               headers={"X-Razorpay-Signature": hmac.new(b"rzp", body, hashlib.sha256).hexdigest()}).json()
    assert r == {"status": "upgraded", "workspace": "asha-classes", "plan": "team"}
    assert c.get("/me").json()["monthly_limit"] == 10000
    assert c.post("/widget/ask", json={"key": key, "question": "What is the fee?"}).status_code == 200
    assert "team" in c.get("/welcome?razorpay_payment_id=pay_UPGRADE1").text


def test_website_import_refuses_private_addresses(site, monkeypatch):
    for bad in ("http://127.0.0.1:8000/", "http://localhost/", "http://169.254.169.254/latest/meta-data/",
                "http://10.0.0.5/", "http://[::1]/", "ftp://example.com/", "https://user:pw@example.com/",
                "http://192.168.1.1/admin"):
        with pytest.raises(web.FetchError):
            web.check_url(bad)
    c = site()
    _signup(c)
    assert c.post("/kb/url", headers=X, json={"url": "http://127.0.0.1:9/"}).status_code == 422
    pages = {
        "https://shop.example/": (b"<html><head><title>Asha Shop</title><script>x()</script></head><body><nav>Home</nav>"
                                  b"<h1>Opening hours</h1><p>We are open Monday to Saturday, 10 AM to 8 PM, and closed on "
                                  b"Sundays and public holidays.</p><a href='/returns'>Returns</a>"
                                  b"<a href='https://other.example/x'>Elsewhere</a></body></html>", "text/html"),
        "https://shop.example/returns": (b"<html><title>Returns</title><body><p>Unused items can be returned within 10 "
                                         b"days of purchase with the original bill for a full refund.</p></body></html>",
                                         "text/html"),
    }
    monkeypatch.setattr(web, "is_public_host", lambda host: host == "shop.example")
    monkeypatch.setattr(web, "fetch", lambda url: (url, *pages[web.check_url(url)]))
    r = c.post("/kb/url", headers=X, json={"url": "shop.example", "pages": 5}).json()
    assert [a["url"] for a in r["added"]] == ["https://shop.example/", "https://shop.example/returns"]   # same site only
    ans = c.post("/chat/ask", headers=X, json={"question": "Within how many days can I return an item?"}).json()
    assert "10 days" in ans["answer"] and "Home" not in json.dumps(c.get("/app/export").json()["documents"])


def test_single_sign_on(site, monkeypatch):
    import httpx
    c = site()
    monkeypatch.setenv("OIDC_ISSUER", "https://id.example")
    monkeypatch.setenv("OIDC_CLIENT_ID", "client-1")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("OIDC_WORKSPACE", "kestrel")
    monkeypatch.setenv("OIDC_DOMAINS", "kestrel.example")
    A._oidc.clear()
    conf = {"issuer": "https://id.example", "authorization_endpoint": "https://id.example/auth",
            "token_endpoint": "https://id.example/token"}
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(200, json=conf, request=httpx.Request("GET", url)))
    claims: dict = {}

    def token(url, data=None, **kw):
        assert data["code"] == "the-code" and data["client_secret"] == "s3cret" and data["code_verifier"]
        body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        return httpx.Response(200, json={"id_token": f"h.{body}.sig"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", token)

    def sign_in(email, **extra):
        start = c.get("/auth/sso/start", follow_redirects=False)
        q = dict(p.split("=", 1) for p in start.headers["location"].split("?", 1)[1].split("&"))
        assert q["code_challenge_method"] == "S256" and q["client_id"] == "client-1"
        claims.clear()
        claims.update({"iss": "https://id.example", "aud": "client-1", "exp": time.time() + 300, "nonce": q["nonce"],
                       "email": email, "email_verified": True, "name": "Priya", **extra})
        return c.get(f"/auth/sso/callback?code=the-code&state={q['state']}", follow_redirects=False), q["state"]

    r, state = sign_in("priya@kestrel.example")
    assert r.status_code == 302 and r.headers["location"] == "/app"            # the first person manages the workspace
    assert c.get("/auth/me").json()["user"]["sso_only"]
    assert c.get(f"/auth/sso/callback?code=the-code&state={state}", follow_redirects=False).status_code == 400  # once
    assert sign_in("dev@kestrel.example")[0].headers["location"] == "/chat"     # later people are members
    assert sign_in("x@outside.example")[0].status_code == 403                   # only the company's domain
    assert sign_in("y@kestrel.example", nonce="forged")[0].status_code == 401
    assert sign_in("z@kestrel.example", aud="someone-else")[0].status_code == 401
    assert c.get("/auth/options").json()["sso"] == "your company login"


def test_each_visitor_has_their_own_limits_behind_a_proxy(site, monkeypatch):
    from cascade_guard.clientip import client_address

    class Req:
        def __init__(self, headers):
            self.headers, self.client = headers, type("C", (), {"host": "10.0.0.5"})()
    assert client_address(Req({"x-forwarded-for": "1.2.3.4"})) == "10.0.0.5"          # not trusted unless told
    monkeypatch.setenv("CLIENT_IP_HEADER", "cf-connecting-ip, x-forwarded-for")
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    assert client_address(Req({"cf-connecting-ip": "203.0.113.9", "x-forwarded-for": "6.6.6.6"})) == "203.0.113.9"
    assert client_address(Req({"x-forwarded-for": "6.6.6.6, 203.0.113.9, 172.70.1.1"})) == "203.0.113.9"  # faked first
    assert client_address(Req({"x-forwarded-for": "203.0.113.9"})) == "203.0.113.9"
    assert client_address(Req({"x-forwarded-for": "not-an-address"})) == "10.0.0.5"
    # four companies sign up from four addresses through one proxy: none is refused for the others
    for i in range(4):
        r = site().post("/auth/signup", headers={**X, "CF-Connecting-IP": f"203.0.113.{i + 1}"}, json={
            "company": f"Shop {i}", "email": f"o{i}@shop{i}.example", "password": "long enough", "agree": True})
        assert r.status_code == 200, r.text


def test_the_widget_script_is_plain_ascii(site):
    r = site().get("/widget.js")
    assert "charset=utf-8" in r.headers["content-type"] and r.content.isascii()   # safe on a page in any encoding


def test_the_operator_sees_customers_sets_plans_and_resets_passwords(site):
    c = site()
    _signup(c)
    owner = {"X-API-Key": "owner-key"}
    assert c.get("/admin/workspaces", headers=X).status_code == 403            # a customer is not the operator
    ws = c.get("/admin/workspaces", headers=owner).json()["workspaces"]
    assert ws[0]["title"] == "Asha Classes" and ws[0]["contact"] == "asha@asha.example" and ws[0]["plan"] == "free"
    name = ws[0]["name"]
    assert c.post(f"/admin/workspaces/{name}/plan", headers=owner, json={"plan": "gold"}).status_code == 422
    assert c.post(f"/admin/workspaces/{name}/plan", headers=owner, json={"plan": "starter"}).json()["monthly_limit"] == 1000
    assert c.get("/auth/me").json()["workspace"]["plan"] == "starter"          # paid by UPI, upgraded by hand
    link = c.post("/admin/users/reset-link", headers=owner, json={"email": "asha@asha.example"}).json()["link"]
    fresh = site()
    assert fresh.post("/auth/reset", headers=X, json={"token": link.split("reset=")[1], "password": "brand new pass"}).status_code == 200
    assert fresh.post("/auth/login", headers=X, json={"email": "asha@asha.example", "password": "brand new pass"}).status_code == 200
    assert c.post("/admin/users/reset-link", headers=owner, json={"email": "nobody@x.example"}).status_code == 404
