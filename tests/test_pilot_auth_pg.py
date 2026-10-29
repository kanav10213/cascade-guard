"""Invoice pilot, access control (admin / reviewer / viewer), and storage on
PostgreSQL. The PostgreSQL tests run only when CASCADE_TEST_PG holds a database
URL, e.g. postgresql://postgres@localhost:5432/cascade_test."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from cascade_guard import llm_api
from cascade_guard.api import app
from cascade_guard.llm.client import FakeLLMClient
from cascade_guard.pilot.invoice import (
    PilotFaults,
    decide,
    make_invoice,
    make_invoices,
    parse_invoice,
    price_items,
    run_invoice,
    run_pilot_experiment,
    same,
    truth,
)
from cascade_guard.storage.db import Store, reset_store
from tests.test_langgraph_storage import halted_result

api = TestClient(app)

TEXT = """INVOICE INV-2041
Customer: Sharma Traders
Items:
  3 x Steel bolts (box of 50) @ $2.50
  1 x Claw hammer @ $14.99
  2 x LED work light @ $39.90
Tax: 8%
"""


# ------------------------------------------------------------------ business logic

def test_parse_and_price_to_the_cent():
    e = parse_invoice(TEXT)
    assert e["invoice_id"] == "INV-2041" and e["tax_rate"] == 8.0 and len(e["items"]) == 3
    p = price_items(e)
    # 7.50 + 14.99 + 79.80 = 102.29; tax 8.1832 -> 8.18; total 110.47
    assert p == {"line_totals": [7.5, 14.99, 79.8], "subtotal": 102.29, "tax": 8.18, "total": 110.47}
    assert decide(p) == {"decision": "auto-approve"}
    assert decide({"total": 500.01}) == {"decision": "needs sign-off"}
    assert truth(TEXT)["report"]["summary"] == ("INV-2041: 3 items, subtotal $102.29, tax $8.18, "
                                                "total $110.47, auto-approve")


def test_rejects_text_that_is_not_an_invoice():
    with pytest.raises(ValueError):
        parse_invoice("hello")


def test_sample_invoices_are_valid_and_reproducible():
    assert make_invoice(5) == make_invoice(5)
    for inv in make_invoices(50, 1):
        assert truth(inv["text"])["extract"]["invoice_id"] == inv["id"]


def test_faults_are_deterministic_and_change_the_output():
    out = parse_invoice(TEXT)
    f = PilotFaults(1.0, seed=3)
    a = f.apply("extract", out, "INV-2041", 0)
    assert a == f.apply("extract", out, "INV-2041", 0) and a[1] and not same(a[0], out)
    assert PilotFaults(0.0).apply("extract", out, "x", 0) == (out, None)


# ------------------------------------------------------------------ the guarded workflow

def test_clean_invoice_passes_every_step():
    r = run_invoice(TEXT, "rules")
    assert r["status"] == "correct" and r["final_output"] == r["expected_output"]
    assert [t["stage"] for t in r["trace"]] == ["extract", "price", "approve", "report"]
    assert r["false_alarms"] == 0 and len(r["validator_log"]) == 4


def test_guard_catches_and_repairs_an_injected_error():
    found = False
    for seed in range(40):
        r = run_invoice(TEXT, "rules", probability=0.5, seed=seed)
        if r["detected"] and r["status"] == "correct":
            rejected = [t for t in r["trace"] if t["guard_issue"]]
            assert rejected[0]["injected"] and r["recovered"] >= 1
            found = True
            break
    assert found


def test_without_guard_errors_go_through_silently():
    statuses = {run_invoice(TEXT, "none", probability=1.0, seed=s)["status"] for s in range(10)}
    assert "wrong" in statuses and "halted" not in statuses


def test_pilot_experiment_guard_removes_silent_errors():
    e = run_pilot_experiment(trials=300, seed=2, probability=0.15)
    assert e["baseline"]["wrong"] > 0 and e["protected"]["wrong"] == 0
    assert e["protected"]["accuracy"] > e["baseline"]["accuracy"] and e["cri"] == 1.0
    assert e["protected"]["false_alarms"] == 0
    assert run_pilot_experiment(100, 4, 0.2) == run_pilot_experiment(100, 4, 0.2)


def test_pilot_with_llm_agents_and_llm_checks():
    c = FakeLLMClient(cache_path=":memory:")
    r = run_invoice(TEXT, guard="llm", agents="llm", client=c)
    assert r["status"] in ("correct", "halted") and c.usage.calls >= 2
    with pytest.raises(ValueError):
        run_invoice(TEXT, guard="llm")  # no client


def test_llm_check_catches_a_model_price_mistake():
    def responder(system, user, sample):
        data = json.loads(user.split("DATA: ", 1)[1])
        if user.startswith("TASK: pilot_extract"):
            return json.dumps(parse_invoice(data["invoice_text"]))
        if user.startswith("TASK: pilot_price"):
            p = price_items(data)
            if sample == 0:
                p["total"] += 1  # first answer wrong
            return json.dumps(p)
        out, src = data["output"], data["source"]
        ok = "total" not in out or same(out, price_items(src))
        return json.dumps({"verdict": "accept" if ok else "reject", "reason": "recomputed"})

    c = FakeLLMClient(responder, cache_path=":memory:")
    r = run_invoice(TEXT, guard="llm", agents="llm", client=c)
    assert r["status"] == "correct" and r["detected"] == 1 and r["recovered"] == 1
    assert [t["agent_ok"] for t in r["trace"] if t["stage"] == "price"] == [False, True]
    r = run_invoice(TEXT, guard="none", agents="llm", client=FakeLLMClient(responder, cache_path=":memory:"))
    assert r["status"] == "wrong"


# ------------------------------------------------------------------ pilot API

@pytest.fixture
def mem_store():
    s = Store(":memory:")
    reset_store(s)
    yield s
    reset_store(None)
    llm_api.reset_client()


def test_pilot_api_runs_and_stores(mem_store):
    sample = api.get("/pilot/sample?seed=3").json()
    r = api.post("/pilot/invoice", json={"text": sample["text"], "guard": "rules",
                                         "inject_probability": 0.6, "seed": 1}).json()
    assert r["engine"] == "langgraph" and r["run_id"]
    assert api.get(f"/runs/{r['run_id']}").json()["kind"] == "pilot"
    assert [x["kind"] for x in api.get("/runs?kind=pilot").json()] == ["pilot"]
    assert api.post("/pilot/invoice", json={"text": "not an invoice at all"}).status_code == 422
    e = api.post("/pilot/experiment", json={"trials": 50}).json()
    assert e["protected"]["wrong"] == 0


def test_pilot_api_llm_needs_a_model(mem_store, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert api.post("/pilot/invoice", json={"text": TEXT, "guard": "llm"}).status_code == 503
    llm_api.reset_client(FakeLLMClient(cache_path=":memory:"))
    r = api.post("/pilot/invoice", json={"text": TEXT, "guard": "llm", "agents": "llm"})
    assert r.status_code == 200 and r.json()["model"]["simulated"]


# ------------------------------------------------------------------ access control

@pytest.fixture
def keys(monkeypatch, mem_store):
    monkeypatch.setenv("API_KEY", "admin-k")
    monkeypatch.setenv("REVIEWER_KEY", "rev-k")
    monkeypatch.setenv("VIEWER_KEY", "view-k")
    llm_api.reset_client(FakeLLMClient(cache_path=":memory:"))
    return {"admin": {"X-API-Key": "admin-k"}, "reviewer": {"X-API-Key": "rev-k"},
            "viewer": {"X-API-Key": "view-k"}, "bad": {"X-API-Key": "nope"}}


def test_whoami(keys):
    assert api.get("/whoami", headers=keys["viewer"]).json() == {"role": "viewer", "auth_enabled": True}
    assert api.get("/whoami", headers=keys["admin"]).json()["role"] == "admin"
    assert api.get("/whoami", headers=keys["bad"]).status_code == 401
    assert api.get("/health").status_code == 200


def test_role_permissions(keys, mem_store):
    llm = {"numbers": "1 2 3", "guard": "none"}
    # money-spending routes: admin only
    assert api.post("/llm/pipeline", json=llm, headers=keys["viewer"]).status_code == 403
    assert api.post("/llm/pipeline", json=llm, headers=keys["reviewer"]).status_code == 403
    assert api.post("/llm/pipeline", json=llm, headers=keys["admin"]).status_code == 200
    assert api.post("/pilot/invoice", json={"text": TEXT, "guard": "llm"}, headers=keys["viewer"]).status_code == 403
    # free demos and reads: everyone with a key
    for k in ("viewer", "reviewer", "admin"):
        h = keys[k]
        assert api.post("/pilot/invoice", json={"text": TEXT}, headers=h).status_code == 200
        assert api.post("/pipeline", json={"numbers": "1 2"}, headers=h).status_code == 200
        assert api.get("/runs", headers=h).status_code == 200
        assert api.get("/llm/status", headers=h).status_code == 200
    assert api.get("/runs").status_code == 401
    # deciding review items: reviewer or admin
    rid = mem_store.save_pipeline_run(halted_result(), "1 2", guard="rules", engine="custom", model={})
    item = mem_store.reviews()[0]["id"]
    body = {"resolution": "approved"}
    assert api.post(f"/review-queue/{item}/resolve", json=body, headers=keys["viewer"]).status_code == 403
    r = api.post(f"/review-queue/{item}/resolve", json=body, headers=keys["reviewer"])
    assert r.status_code == 200 and r.json()["resolved_by"] == "reviewer" and rid


def test_open_server_when_no_keys(mem_store, monkeypatch):
    for k in ("API_KEY", "REVIEWER_KEY", "VIEWER_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert api.get("/whoami").json() == {"role": "admin", "auth_enabled": False}


# ------------------------------------------------------------------ PostgreSQL

PG = os.getenv("CASCADE_TEST_PG")
try:
    import psycopg  # noqa: F401
    HAVE_PSYCOPG = True
except ImportError:
    HAVE_PSYCOPG = False
needs_pg = pytest.mark.skipif(not (PG and HAVE_PSYCOPG),
                              reason="set CASCADE_TEST_PG to a PostgreSQL URL and install cascade-guard-ai[postgres]")


@needs_pg
def test_postgres_store_round_trip():
    s = Store(PG)
    s.clear()
    rid = s.save_pipeline_run(halted_result(), "1 2", guard="recompute", engine="langgraph",
                              model={"provider": "fake", "model": "m"},
                              usage_delta={"calls": 2, "cached_calls": 0, "input_tokens": 10,
                                           "output_tokens": 5, "cost_usd": 0.0002},
                              validator_log=[{"stage": "analyze", "verdict": "reject", "reason": "sum off"}])
    run = s.get_run(rid)
    assert run["steps"][1]["output"] == {"sum": 4} and run["verdicts"][0]["reason"] == "sum off"
    [item] = s.reviews()
    assert s.resolve_review(item["id"], "fixed", by="k")["resolution"] == "fixed"
    with pytest.raises(ValueError):
        s.resolve_review(item["id"], "approved")
    v = s.save_verify({"prompt": "naive"}, {"verdict": "reject", "reason": "x"}, model={})
    assert [r["id"] for r in s.list_runs()] == [v, rid]
    u = s.usage_summary()
    assert u["all_time"]["calls"] == 2 and u["database"].startswith("PostgreSQL") and u["open_reviews"] == 1
    assert s.purge_older_than(0) >= 0
    s.clear()


@needs_pg
def test_postgres_through_the_api():
    s = Store(PG)
    s.clear()
    reset_store(s)
    try:
        r = api.post("/pilot/invoice", json={"text": TEXT, "inject_probability": 1.0, "seed": 2}).json()
        assert api.get(f"/runs/{r['run_id']}").json()["kind"] == "pilot"
        assert api.get("/usage").json()["database"].startswith("PostgreSQL")
    finally:
        s.clear()
        reset_store(None)
