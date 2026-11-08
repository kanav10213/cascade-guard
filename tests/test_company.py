"""The company product: keys and workspaces, documents, checked answers, the OpenAI-format API, payments."""
import base64
import hashlib
import hmac
import io
import json
import time
import zipfile

import pytest

from cascade_guard.company import answer as A
from cascade_guard.company import kb
from cascade_guard.company import keys as K
from cascade_guard.company.api import SAMPLES  # noqa: E402  (shipped in the package)
from cascade_guard.llm.presets import function_client
from cascade_guard.storage.db import Store

Q_LEAVE = "How many days of paid leave do we get, and how many can be carried forward?"
GOOD = "Every permanent employee gets 24 days of paid annual leave per year [1]. Up to 10 unused days can be " \
       "carried forward; the rest lapse on 31 March [1]."
ACCEPT = '{"verdict": "accept", "problems": []}'


@pytest.fixture()
def store():
    s = Store(":memory:")
    for f in sorted(SAMPLES.iterdir()):
        if f.name != "README.md":
            kb.add_document("default", f.name, f.read_bytes(), store=s)
    kb._cache.clear()
    return s


def scripted(replies, checks=None):
    """A stand-in AI: answers in order; the independent reader answers from `checks` (accept by default)."""
    it, ch = iter(replies), iter(checks or [])

    def fn(system, user):
        if system.startswith("You check an answer"):
            return next(ch, ACCEPT)
        return next(it)
    return function_client(fn)


# ------------------------------------------------------------------ documents

def _docx(paragraphs: list[tuple[str, str]], table: list[list[str]] | None = None) -> bytes:
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = ""
    for style, text in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body += f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"
    if table:
        body += "<w:tbl>" + "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row)
                                    + "</w:tr>" for row in table) + "</w:tbl>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {w}><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def test_reads_word_pdf_html_and_spreadsheets():
    d = _docx([("Heading1", "Canteen"), ("", "Lunch costs ₹60 for staff.")], [["Item", "Price"], ["Tea", "10"]])
    text = kb.read_document("policy.docx", d)[0][1]
    assert "# Canteen" in text and "₹60" in text and "Tea | 10" in text
    from cascade_guard.pilot.pdf import text_to_pdf
    pages = kb.read_document("x.pdf", text_to_pdf("Notice period is 60 days."))
    assert pages[0][0] == 1 and "60 days" in pages[0][1]
    assert "Hello" in kb.read_document("a.html", b"<html><script>x()</script><p>Hello</p></html>")[0][1]
    rows = kb.read_document("p.csv", (SAMPLES / "product-price-list.csv").read_bytes())[0][1]
    assert "Product: Angle grinder 100 mm; Price per unit (INR, excluding GST): 3,299" in rows
    with pytest.raises(kb.DocumentError, match="xlsx"):
        kb.read_document("a.xlsx", b"PK..")


def test_passages_keep_their_heading_and_search_finds_them(store):
    hits = kb.search("default", "Who approves an expense of 20,000 rupees?", store=store)
    assert kb.relevant(hits) and "Approval limits" in hits[0]["text"]
    assert not kb.relevant(kb.search("default", "what is the capital of France", store=store))
    assert kb.search("default", "angle grinder warranty", store=store)[0]["doc"] in (
        "customer-support-faq.md", "product-price-list.csv")


def test_workspaces_do_not_see_each_other(store):
    kb.add_document("acme", "acme.txt", b"Acme canteen opens at 8 AM.", store=store)
    assert all(h["doc"] != "acme.txt" for h in kb.search("default", "canteen opens", store=store))
    assert kb.search("acme", "canteen opens", store=store)[0]["doc"] == "acme.txt"
    assert not kb.delete_document("acme", kb.list_documents("default", store=store)[0]["id"], store=store)


# ------------------------------------------------------------------ the checks

def _passages(store, q=Q_LEAVE):
    hits = kb.search("default", q, store=store)
    return [h for h in hits if h["score"] >= hits[0]["score"] * 0.25]


def test_numbers_must_be_in_the_passage_cited(store):
    ps = _passages(store)
    assert A.check_answer(GOOD, ps, Q_LEAVE) == []
    wrong = A.check_answer("Every permanent employee gets 30 days of paid annual leave per year. [1]", ps, Q_LEAVE)
    assert wrong and "30" in wrong[0]["text"]  # 30 appears in another passage (claims within 30 days): still wrong
    uncited = A.check_answer("Every permanent employee gets 30 days of paid annual leave.", ps, Q_LEAVE)
    assert uncited and uncited[0]["kind"] == "number"
    assert A.check_answer("Up to 12 days carry forward [1].", ps, Q_LEAVE)  # small numbers with a unit count too


def test_arithmetic_is_allowed_only_when_shown(store):
    q = "If I travel to Pune for 3 days, what daily allowance do I get in total?"
    ps = _passages(store, q)
    assert A.check_answer("Pune is a metro city, so it is ₹1,500 per day [1]; 3 days come to ₹4,500 [1].", ps, q) == []
    assert A.check_answer("The daily allowance in Pune is ₹4,500 [1].", ps, q)


def test_citations_quotes_and_unrelated_sentences(store):
    ps = _passages(store)
    assert any(p["kind"] == "citation" for p in A.check_answer("You get 24 days of paid leave [9].", ps, Q_LEAVE))
    assert any(p["kind"] == "quote" for p in A.check_answer('The handbook says "leave is unlimited for all staff" [1].',
                                                             ps, Q_LEAVE))
    soft = A.check_answer("Our cafeteria serves excellent biryani every Friday afternoon [1].", ps, Q_LEAVE)
    assert soft and not soft[0]["hard"]
    assert A.sentences("It is 24 days. [1] Up to 10 carry forward. [1]") == ["It is 24 days. [1]",
                                                                             "Up to 10 carry forward. [1]"]


# ------------------------------------------------------------------ answers

def test_a_wrong_number_is_sent_back_and_fixed(store):
    r = A.answer(Q_LEAVE, client=scripted(["You get 30 days of paid leave a year [1], and 10 carry forward [1].",
                                           GOOD]), store=store)
    assert r["verdict"] == "fixed" and "24 days" in r["answer"] and "30" in r["caught"][0]
    assert r["sources"][0]["doc"] == "employee-handbook.md" and r["usage"]["ai_calls"] == 3


def test_what_cannot_be_fixed_is_removed(store):
    bad = "You get 24 days of paid leave [1]. Unused leave is paid out at ₹2,000 a day [1]."
    r = A.answer(Q_LEAVE, client=scripted([bad, bad, bad]), store=store)
    assert r["verdict"] == "trimmed" and "₹2,000" not in r["answer"] and "24 days" in r["answer"]
    assert r["removed"] == ["Unused leave is paid out at ₹2,000 a day [1]."]
    r = A.answer(Q_LEAVE, client=scripted(["Leave is ₹2,000 a day [1]."] * 3), store=store)
    assert r["verdict"] == "not_found" and "couldn't give an answer I could check" in r["answer"]


def test_the_independent_reading_can_warn_but_never_rewrite(store):
    doubt = '{"verdict": "revise", "problems": ["the notice period during probation is not 15 days"]}'
    right = "New employees are on probation for 6 months [1]. During probation the notice period is 15 days [1]."
    q = "How long is probation, and what is the notice period during probation?"
    r = A.answer(q, client=scripted([right], [doubt]), store=store)
    assert r["answer"] == right and r["verdict"] == "unconfirmed" and "not 15 days" in r["notice"]
    assert r["usage"]["ai_calls"] == 2  # a small model's mistaken doubt cannot turn a right answer into a wrong one
    assert A.answer(q, client=scripted([right], [ACCEPT]), store=store)["verdict"] == "checked"


def test_a_correction_never_replaces_a_better_answer(store):
    first = "Every permanent employee gets 24 days of paid annual leave [1]. Unused leave is paid at ₹2,000 a day [1]."
    r = A.answer(Q_LEAVE, client=scripted([first, "NOT IN THE DOCUMENTS"]), store=store)
    assert r["verdict"] == "trimmed" and "24 days" in r["answer"] and "₹2,000" not in r["answer"]
    worse = "It is 30 days [1], paid at ₹2,000 a day [1], and 12 carry forward [1]."
    r = A.answer(Q_LEAVE, client=scripted([first, worse, worse]), store=store)
    assert "24 days" in r["answer"] and "30 days" not in r["answer"]


def test_an_obvious_citation_slip_is_repaired(store):
    q = "What is the GST rate on the vernier caliper?"
    r = A.answer(q, client=scripted(["The GST rate on the vernier caliper is 12%. [4]"]), store=store)
    assert r["verdict"] == "checked" and r["repaired"] and "[4]" not in r["answer"] and r["usage"]["ai_calls"] == 2


def test_not_in_the_documents_and_general_questions(store, monkeypatch):
    r = A.answer(Q_LEAVE, client=scripted(["NOT IN THE DOCUMENTS"]), store=store)
    assert r["verdict"] == "not_found"
    r = A.answer("what is 15% of 6,499", store=store)  # no AI: the built-in exact tools
    assert r["mode"] == "general" and "974.85" in r["answer"]
    monkeypatch.setenv("CHAT_DOCS_ONLY", "1")
    assert A.answer("Tell me a joke", client=scripted([]), store=store)["verdict"] == "not_found"


def test_without_an_ai_or_when_it_is_down_it_quotes(store):
    r = A.answer(Q_LEAVE, store=store)
    assert r["verdict"] == "quoted" and "24 days of paid annual leave" in r["answer"]

    def down(s, u):
        raise RuntimeError("connection refused")
    r = A.answer(Q_LEAVE, client=function_client(down), store=store)
    assert r["verdict"] == "quoted" and "not available" in r["notice"]


# ------------------------------------------------------------------ keys

def test_keys_plans_quota_and_revoke():
    s = Store(":memory:")
    key, rec = K.create_key("Acme", "Acme Pvt Ltd", role="manager", plan="free", monthly_limit=2, store=s)
    assert rec["workspace"] == "acme-pvt-ltd" and key.startswith("cg_") and "key_hash" not in rec
    p = K.principal_for(key, store=s)
    assert (p.workspace, p.role, p.monthly_limit) == ("acme-pvt-ltd", "manager", 2)
    K.record(p, 3, 500, store=s)
    K.record(p, 1, 100, store=s)
    with pytest.raises(Exception, match="used its 2 answers"):
        K.check_quota(p, store=s)
    assert K.usage_report(store=s)[0]["ai_calls"] == 4
    assert K.revoke_key(rec["id"], store=s)
    assert K.key_from(None, "Bearer abc") == "abc" and K.key_from("x", "Bearer abc") == "x"


# ------------------------------------------------------------------ the API

@pytest.fixture()
def app_client(monkeypatch, store):
    from fastapi.testclient import TestClient

    from cascade_guard.api import app
    from cascade_guard.llm_api import reset_client
    from cascade_guard.storage.db import reset_store
    monkeypatch.setenv("API_KEY", "owner-key")
    reset_store(store)
    reset_client(scripted([GOOD] * 50))
    try:
        yield TestClient(app)
    finally:
        reset_client(None)
        reset_store(None)


def test_chat_api_documents_and_permissions(app_client):
    c = app_client
    own = {"X-API-Key": "owner-key"}
    info = c.get("/chat/info", headers=own).json()
    assert info["you"]["role"] == "owner" and info["documents"] == 5 and info["sample_questions"]
    r = c.post("/chat/ask", headers=own, json={"question": Q_LEAVE}).json()
    assert r["verdict"] == "checked" and r["sources"]
    made = c.post("/admin/keys", headers=own, json={"name": "Acme", "workspace": "acme", "role": "manager",
                                                    "plan": "free"}).json()
    mgr = {"Authorization": "Bearer " + made["key"]}
    assert c.get("/kb/docs", headers=mgr).json()["documents"] == []  # its own, empty workspace
    up = c.post("/kb/docs", headers=mgr, json={"name": "hr.docx", "file_base64": base64.b64encode(
        _docx([("", "Acme staff get 18 days of paid leave.")])).decode()})
    assert up.status_code == 200 and up.json()["passages"] == 1
    member = c.post("/admin/keys", headers=mgr, json={"name": "Ravi"}).json()
    assert member["record"]["workspace"] == "acme" and member["record"]["role"] == "member"
    assert c.post("/admin/keys", headers=mgr, json={"name": "x", "role": "manager"}).status_code == 403
    mem = {"X-API-Key": member["key"]}
    assert c.post("/kb/docs", headers=mem, json={"name": "a.txt", "text": "x"}).status_code == 403
    assert c.get("/runs", headers=mem).status_code == 401          # customer keys never open the older routes
    assert len(c.get("/admin/keys", headers=mgr).json()["keys"]) == 2  # only its workspace's keys
    assert c.get("/me", headers=mem).json()["plan"] == "free"


def test_openai_format_api(app_client):
    c = app_client
    h = {"Authorization": "Bearer owner-key"}
    assert [m["id"] for m in c.get("/v1/models", headers=h).json()["data"]][0] == "cascade-guard"
    r = c.post("/v1/chat/completions", headers=h, json={"model": "cascade-guard", "messages": [
        {"role": "system", "content": "be nice"}, {"role": "user", "content": [{"type": "text", "text": Q_LEAVE}]}]})
    body = r.json()
    msg = body["choices"][0]["message"]["content"]
    assert "24 days" in msg and "Sources: [1] employee-handbook.md" in msg and body["cascade_guard"]["verdict"]
    s = c.post("/v1/chat/completions", headers=h, json={"stream": True, "messages": [{"role": "user", "content": Q_LEAVE}]})
    chunks = [json.loads(line[6:]) for line in s.text.splitlines() if line.startswith("data: {")]
    assert s.text.rstrip().endswith("data: [DONE]")
    assert "".join(ch["choices"][0]["delta"].get("content", "") for ch in chunks) == msg
    bad = c.post("/v1/chat/completions", headers={"Authorization": "Bearer nope"}, json={"messages": []})
    assert bad.status_code == 401 and bad.json()["error"]["type"] == "invalid_api_key"
    assert c.post("/v1/chat/completions", headers=h, json={"messages": [{"role": "assistant", "content": "x"}]}
                  ).json()["error"]["code"] == 400


# ------------------------------------------------------------------ payments

def _stripe_sig(body: bytes, secret: str, t: int | None = None) -> str:
    t = t or int(time.time())
    return f"t={t},v1=" + hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()


def test_stripe_payment_makes_a_key_shown_once_and_cancel_revokes(app_client, monkeypatch):
    c = app_client
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("BILLING_PLANS", json.dumps({"plink_team": "team"}))
    ev = {"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_test_123456", "payment_status": "paid", "payment_link": "plink_team", "subscription": "sub_9",
        "customer_details": {"email": "ops@acme.example", "name": "Acme Tools"}, "metadata": {}}}}
    body = json.dumps(ev).encode()
    assert c.post("/billing/stripe", content=body, headers={"Stripe-Signature": "t=1,v1=bad"}).status_code == 400
    old = _stripe_sig(body, "whsec_test", int(time.time()) - 3600)
    assert c.post("/billing/stripe", content=body, headers={"Stripe-Signature": old}).status_code == 400
    r = c.post("/billing/stripe", content=body, headers={"Stripe-Signature": _stripe_sig(body, "whsec_test")}).json()
    assert r["status"] == "issued" and r["plan"] == "team" and r["workspace"] == "acme-tools"
    again = c.post("/billing/stripe", content=body, headers={"Stripe-Signature": _stripe_sig(body, "whsec_test")})
    assert again.json()["status"] == "already issued"  # Stripe retries webhooks
    page = c.get("/welcome?ref=cs_test_123456").text
    key = page.split('<div class="key">')[1].split("</div>")[0]
    assert key.startswith("cg_") and "cg_" not in c.get("/welcome?ref=cs_test_123456").text  # only once
    assert c.get("/me", headers={"X-API-Key": key}).json()["plan"] == "team"
    cancel = json.dumps({"type": "customer.subscription.deleted", "data": {"object": {"id": "sub_9"}}}).encode()
    c.post("/billing/stripe", content=cancel, headers={"Stripe-Signature": _stripe_sig(cancel, "whsec_test")})
    assert c.get("/me", headers={"X-API-Key": key}).status_code == 401


def test_razorpay_payment_link_and_pricing_page(app_client, monkeypatch):
    c = app_client
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "rzp_secret")
    monkeypatch.setenv("PLAN_PRICES", json.dumps({"starter": "₹999 a month"}))
    monkeypatch.setenv("BILLING_LINKS", json.dumps({"starter": "https://rzp.io/l/example"}))
    ev = {"event": "payment_link.paid", "payload": {
        "payment_link": {"entity": {"id": "plink_X1", "notes": {"plan": "starter", "company": "Mehta Traders"},
                                    "customer": {"email": "a@mehta.example"}}},
        "payment": {"entity": {"id": "pay_ABC123", "amount": 99900}}}}
    body = json.dumps(ev).encode()
    sig = hmac.new(b"rzp_secret", body, hashlib.sha256).hexdigest()
    assert c.post("/billing/razorpay", content=body, headers={"X-Razorpay-Signature": "0" * 64}).status_code == 400
    r = c.post("/billing/razorpay", content=body, headers={"X-Razorpay-Signature": sig}).json()
    assert r["status"] == "issued" and r["workspace"] == "mehta-traders"
    assert "cg_" in c.get("/welcome?razorpay_payment_id=pay_ABC123").text
    price = c.get("/pricing").text
    assert "₹999 a month" in price and "https://rzp.io/l/example" in price


# ------------------------------------------------------------------ command line

def test_command_line(tmp_path, monkeypatch, capsys):
    from cascade_guard.cli import main
    from cascade_guard.storage.db import reset_store
    monkeypatch.setenv("CASCADE_DB", str(tmp_path / "cg.db"))
    reset_store(None)
    kb._cache.clear()
    try:
        assert main(["kb", "add", "--samples"]) == 0
        assert "5 added" in capsys.readouterr().out
        assert main(["chat", "What is the hotel limit in Pune for a manager?"]) == 0
        assert "₹9,000" in capsys.readouterr().out
        assert main(["keys", "create", "Acme", "--workspace", "acme", "--plan", "team"]) == 0
        assert "cg_" in capsys.readouterr().out
        assert main(["keys", "list"]) == 0 and "acme" in capsys.readouterr().out
    finally:
        reset_store(None)
