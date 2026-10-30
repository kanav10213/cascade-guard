"""Guided repair, the MCP server, PDF invoices, and operations (limits, audit, metrics)."""
import asyncio
import base64
import inspect
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cascade_guard import llm_api
from cascade_guard.agents.llm_agents import build_agents
from cascade_guard.api import app
from cascade_guard.evaluation.error_injector import ErrorInjector
from cascade_guard.evaluation.runner import make_inputs, run_chain, run_experiment
from cascade_guard.guard.cascade_guard import CascadeGuard
from cascade_guard.guard.llm_guard import LLMGuard
from cascade_guard.guard.wrapper import run_guarded
from cascade_guard.llm.client import FakeLLMClient
from cascade_guard.llm.prompts import parse_request
from cascade_guard.ops import RateLimiter
from cascade_guard.orchestration.langgraph_chain import run_chain_langgraph
from cascade_guard.pilot.invoice import make_invoice, make_invoices, run_invoice, run_pilot_experiment, truth
from cascade_guard.pilot.pdf import PDFError, pdf_to_text, text_to_pdf
from cascade_guard.storage.db import Store, reset_store
from tests.test_llm import perfect

ROOT = Path(__file__).resolve().parent.parent
api = TestClient(app)


# ------------------------------------------------------------------ guided repair

def test_sticky_faults_repeat_and_guided_repair_fixes_them():
    out = {"count": 4, "sum": 21, "mean": 5.25, "max": 9}
    inj = ErrorInjector(1.0, seed=1, sticky=1.0, repair_success=1.0)
    first = inj.inject("analyze", out, 0, 0)
    assert first[1] is not None
    assert inj.inject("analyze", out, 0, 1) == first             # blind retry: the same mistake
    assert inj.inject("analyze", out, 0, 1, guided=True) == (out, None)  # told what was wrong: fixed
    # sticky=0 keeps the original independent model exactly
    assert ErrorInjector(0.3, 5).inject("analyze", out, 3, 1) == ErrorInjector(0.3, 5, 0.0)._draw("analyze", out, 3, 1)


def test_guided_repair_cuts_halts_without_letting_errors_through():
    r = run_experiment(600, seed=4, probability=0.15, sticky=0.6)
    blind, guided = r["protected"], r["guided"]["protected"]
    assert blind["wrong"] == 0 and guided["wrong"] == 0
    assert guided["halted"] < blind["halted"] and r["guided"]["crs"] > r["crs"]


def test_guided_repair_parity_between_engines():
    inj = ErrorInjector(0.4, 2, sticky=0.7)
    for i, raw in enumerate(make_inputs(60, 2)):
        for repair in ("blind", "guided"):
            assert run_chain(raw, i, inj, CascadeGuard(), repair=repair) == \
                run_chain_langgraph(raw, i, inj, CascadeGuard(), repair=repair)


def test_llm_agent_is_told_why_it_was_rejected():
    seen = []

    def responder(system, user, sample):
        req = parse_request(user)
        if req["TASK"] == "analyze":
            seen.append(req.get("FEEDBACK"))
            s = json.loads(perfect(system, user, sample))
            if "FEEDBACK" not in req:
                s["sum"] += 1  # wrong until told
                s["mean"] = round(s["sum"] / s["count"], 2)
            return json.dumps(s)
        if req["TASK"] == "validate":
            ok = json.loads(perfect(system, user, sample).split("\n")[-1])["verdict"] == "accept"
            return json.dumps({"verdict": "accept" if ok else "reject", "reason": "sum should be 21"})
        return perfect(system, user, sample)

    c = FakeLLMClient(responder, cache_path=":memory:")
    blind = run_chain("3, 7, 2, 9", 0, ErrorInjector(0.0), LLMGuard(c, "recompute"), build_agents(c), "blind")
    assert blind["status"] == "halted"
    seen.clear()
    c = FakeLLMClient(responder, cache_path=":memory:")  # fresh: no cached first answer
    guided = run_chain("3, 7, 2, 9", 0, ErrorInjector(0.0), LLMGuard(c, "recompute"), build_agents(c), "guided")
    assert guided["status"] == "correct" and seen == [None, "sum should be 21"]


def test_wrapper_passes_feedback_to_functions_that_ask_for_it():
    got = []

    def agent(items, feedback=None):
        got.append(feedback)
        return sum(items) + (0 if feedback else 1)

    rep = run_guarded(agent, lambda o, s: (o == sum(s), "total is off by one"), [1, 2, 3])
    assert rep.status == "passed" and got[0] is None
    assert got[1] == {"reason": "total is off by one", "previous_output": 7}


def test_pilot_guided_repair():
    e = run_pilot_experiment(300, seed=5, probability=0.15, sticky=0.6)
    assert e["guided"]["halted"] < e["protected"]["halted"]
    assert e["guided"]["wrong"] == 0 and e["protected"]["wrong"] == 0


def test_fake_llm_systematic_errors_need_guidance():
    def count(repair):
        c = FakeLLMClient(cache_path=":memory:")
        return sum(run_invoice(i["text"], "llm", "llm", client=c, repair=repair)["status"] == "halted"
                   for i in make_invoices(80, 9))
    assert count("guided") < count("blind")


# ------------------------------------------------------------------ PDF

def test_pdf_round_trip_and_errors():
    inv = make_invoice(11)
    text = pdf_to_text(text_to_pdf(inv["text"], inv["id"]))
    assert truth(text)["report"] == truth(inv["text"])["report"]
    with pytest.raises(PDFError):
        pdf_to_text(b"plain text")
    with pytest.raises(PDFError):
        pdf_to_text(b"%PDF-1.4 broken")
    with pytest.raises(PDFError):
        pdf_to_text(b"%PDF" + b"0" * (5 * 1024 * 1024 + 1))


def test_pdf_from_another_producer():
    pytest.importorskip("reportlab")
    from io import BytesIO

    from reportlab.pdfgen import canvas
    buf = BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for line in make_invoice(12)["text"].splitlines():
        c.drawString(60, y, line)
        y -= 16
    c.save()
    assert run_invoice(pdf_to_text(buf.getvalue()), "rules")["status"] == "correct"


def test_pdf_api():
    pdf = api.get("/pilot/sample.pdf?seed=4")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    r = api.post("/pilot/invoice-pdf", json={"pdf_base64": base64.b64encode(pdf.content).decode()})
    assert r.status_code == 200 and r.json()["status"] == "correct" and "INVOICE" in r.json()["extracted_text"]
    assert api.post("/pilot/invoice-pdf", json={"pdf_base64": base64.b64encode(b"x" * 20).decode()}).status_code == 422


# ------------------------------------------------------------------ operations

def test_rate_limiter_window():
    lim = RateLimiter()
    assert all(lim.allow("k", 3, now=100.0)[0] for _ in range(3))
    ok, wait = lim.allow("k", 3, now=110.0)
    assert not ok and 1 <= wait <= 51
    assert lim.allow("k", 3, now=161.0)[0]
    assert lim.allow("other", 3, now=110.0)[0]


def test_rate_limit_returns_429(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "3")
    codes = [api.post("/validate", json={"text": "ok"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert api.get("/health").status_code == 200  # never limited


def test_llm_routes_have_their_own_tighter_limit(monkeypatch):
    monkeypatch.setenv("LLM_RATE_LIMIT_PER_MIN", "2")
    llm_api.reset_client(FakeLLMClient(perfect, cache_path=":memory:"))
    try:
        codes = [api.post("/llm/pipeline", json={"numbers": "1 2 3", "guard": "none"}).status_code
                 for _ in range(3)]
        assert codes == [200, 200, 429]
        assert api.post("/pipeline", json={"numbers": "1 2"}).status_code == 200
    finally:
        llm_api.reset_client()


def test_audit_log_and_metrics(monkeypatch):
    s = Store(":memory:")
    reset_store(s)
    try:
        monkeypatch.setenv("API_KEY", "adm")
        monkeypatch.setenv("VIEWER_KEY", "vw")
        api.post("/pipeline", json={"numbers": "1 2"}, headers={"X-API-Key": "vw"})
        api.get("/runs", headers={"X-API-Key": "wrong"})
        assert api.get("/audit", headers={"X-API-Key": "vw"}).status_code == 403
        rows = api.get("/audit", headers={"X-API-Key": "adm"}).json()
        kinds = {(r["role"], r["method"], r["path"], r["status"]) for r in rows}
        assert ("viewer", "POST", "/pipeline", 200) in kinds and ("none", "GET", "/runs", 401) in kinds
        assert all(r["key_id"] != "vw" for r in rows)  # the key itself is never stored
        m = api.get("/metrics", headers={"X-API-Key": "vw"}).text
        assert 'cascade_requests_total{method="POST",route="/pipeline",status="200"}' in m
        assert "cascade_open_reviews 0" in m
        ready = api.get("/health/ready").json()
        assert ready["status"] == "ok" and ready["database"] == "ok"  # no path or host name on an open route
    finally:
        reset_store(None)


# ------------------------------------------------------------------ MCP server

def test_mcp_server_over_stdio(tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**os.environ, "LLM_PROVIDER": "fake", "CASCADE_DB": ":memory:", "LLM_CACHE": ":memory:",
           "CASCADE_NO_DOTENV": "1", "PYTHONPATH": str(ROOT),  # importable from a source checkout too
           "CASCADE_HOME": str(tmp_path)}
    params = StdioServerParameters(command=sys.executable, args=["-m", "cascade_guard", "mcp"],
                                   cwd=str(tmp_path), env=env)

    async def go():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                v = await s.call_tool("verify_output", {"task": "Add the items", "source": "1, 2", "output": "3"})
                inv = await s.call_tool("check_invoice", {"invoice_text": make_invoice(3)["text"]})
                bad = await s.call_tool("check_invoice", {"invoice_text": "hello"})
                bill = await s.call_tool("check_bill", {"bill_text": "check this invoice: 3 x $4.00, 2 x $1.50, "
                                                                    "total $16.00"})
                math = await s.call_tool("check_math", {"claim": "is 17 x 23 = 401?"})
                day = await s.call_tool("check_everyday", {"request": "split ₹2,400 among 3 people"})
                return (names, json.loads(v.content[0].text), json.loads(inv.content[0].text),
                        json.loads(bad.content[0].text), json.loads(bill.content[0].text),
                        json.loads(math.content[0].text), json.loads(day.content[0].text))

    names, v, inv, bad, bill, math, day = asyncio.run(go())
    assert day["status"] == "computed" and day["answer"].startswith("₹800.00")
    assert names == {"check_bill", "check_math", "check_everyday", "process_receipt", "show_cascade_demo", "verify_output", "check_invoice", "guard_status",
                     "list_review_queue", "list_agent_chains", "run_agent_chain"}  # no tool decides review items
    assert bill["status"] == "wrong" and bill["total"] == 15.0 and bill["stated"]["total"] == 16.0
    assert math["status"] == "wrong" and math["value"] == "391"
    assert v["verdict"] in ("accept", "reject") and v["model"]["simulated"]
    assert inv["status"] == "correct" and inv["delivered"] == inv["expected"]
    assert "error" in bad


# ------------------------------------------------------------------ fixes from the independent review

def test_engines_match_when_an_agent_returns_none():
    from cascade_guard.agents.pipeline import AGENTS

    def flaky(raw, state, attempt=0):
        return None if attempt == 0 else AGENTS["analyze"](raw, state)
    flaky.accepts_attempt = True
    agents = dict(AGENTS, analyze=flaky)
    a = run_chain("3, 7, 2, 9", 0, ErrorInjector(0.0), CascadeGuard(), agents)
    b = run_chain_langgraph("3, 7, 2, 9", 0, ErrorInjector(0.0), CascadeGuard(), agents)
    assert a == b and a["status"] == "correct"


def test_false_alarm_is_not_counted_as_a_detection():
    def picky(system, user, sample):
        if "TASK: validate" in user and sample == 0 and "STAGE: analyze" in user:
            return json.dumps({"verdict": "reject", "reason": "looks odd"})
        return perfect(system, user, sample)

    calls = {"n": 0}

    def once_picky(system, user, sample):
        if "TASK: validate" in user and "STAGE: analyze" in user:
            calls["n"] += 1
            if calls["n"] == 1:
                return json.dumps({"verdict": "reject", "reason": "looks odd"})
        return perfect(system, user, sample)

    c = FakeLLMClient(once_picky)  # no cache: the second check is asked again
    r = run_chain("3, 7, 2, 9", 0, ErrorInjector(0.0), LLMGuard(c, "recompute"), build_agents(c))
    assert r["status"] == "correct" and r["false_alarms"] == 1
    assert r["detected"] == 0 and r["recovered"] == 0  # nothing wrong was caught or repaired


def test_open_server_only_answers_this_computer(monkeypatch):
    if "client" not in inspect.signature(TestClient.__init__).parameters:
        pytest.skip("this Starlette TestClient cannot set the client address")
    remote = TestClient(app, client=("203.0.113.9", 5000))
    r = remote.post("/validate", json={"text": "ok"})
    assert r.status_code == 403 and "API_KEY" in r.json()["detail"]
    assert remote.get("/health").status_code == 200
    monkeypatch.setenv("API_KEY", "adm")
    assert remote.post("/validate", json={"text": "ok"}, headers={"X-API-Key": "adm"}).status_code == 200
    assert remote.post("/validate", json={"text": "ok"}).status_code == 401


def test_non_ascii_key_is_a_401_not_a_crash(monkeypatch):
    monkeypatch.setenv("API_KEY", "adm")
    r = api.get("/runs", headers=[(b"x-api-key", "clé".encode())])
    assert r.status_code == 401


def test_junk_keys_share_one_bucket(monkeypatch):
    monkeypatch.setenv("API_KEY", "adm")
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "3")
    codes = [api.get("/whoami", headers={"X-API-Key": f"junk{i}"}).status_code for i in range(4)]
    assert codes == [401, 401, 401, 429]


def test_pilot_llm_runs_share_the_model_rate_limit(monkeypatch):
    monkeypatch.setenv("LLM_RATE_LIMIT_PER_MIN", "1")
    llm_api.reset_client(FakeLLMClient(cache_path=":memory:"))
    try:
        text = make_invoice(2)["text"]
        codes = [api.post("/pilot/invoice", json={"text": text, "guard": "llm"}).status_code for _ in range(2)]
        assert codes == [200, 429]
        assert api.post("/pilot/invoice", json={"text": text, "guard": "rules"}).status_code == 200
    finally:
        llm_api.reset_client()
