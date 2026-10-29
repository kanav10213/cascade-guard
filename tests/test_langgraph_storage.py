"""LangGraph engine (parity with the built-in orchestrator), SQLite storage and
review queue, and the guard_step wrapper. No key, no network."""
import json
from typing import TypedDict

import pytest
from fastapi.testclient import TestClient
from langgraph.graph import END, START, StateGraph

from cascade_guard import llm_api
from cascade_guard.agents.llm_agents import build_agents
from cascade_guard.agents.pipeline import AGENTS
from cascade_guard.api import app
from cascade_guard.evaluation.error_injector import ErrorInjector
from cascade_guard.evaluation.runner import make_inputs, run_arm, run_chain, run_experiment
from cascade_guard.guard.cascade_guard import CascadeGuard
from cascade_guard.guard.llm_guard import LLMGuard
from cascade_guard.guard.wrapper import GuardHalted, guard_step, guarded_node, llm_check, rule_check, run_guarded
from cascade_guard.llm.client import FakeLLMClient
from cascade_guard.orchestration.langgraph_chain import build_graph, mermaid, run_chain_langgraph
from cascade_guard.storage.db import Store, reset_store
from tests.test_llm import perfect, wrong_once

api = TestClient(app)


def fake(responder=perfect, **kw):
    return FakeLLMClient(responder, cache_path=":memory:", **kw)


# ------------------------------------------------------------------ LangGraph parity

@pytest.mark.parametrize("mode", ["full", "invariants"])
def test_langgraph_gives_identical_experiment_results(mode):
    a = run_experiment(300, seed=7, probability=0.3, mode=mode)
    b = run_experiment(300, seed=7, probability=0.3, mode=mode, engine="langgraph")
    assert a.pop("config")["engine"] == "custom" and b.pop("config")["engine"] == "langgraph"
    assert a == b


def test_langgraph_parity_per_order_including_traces():
    inj = ErrorInjector(0.4, 3)
    guard = CascadeGuard("full", 2)
    for i, raw in enumerate(make_inputs(100, 3)):
        assert run_chain(raw, i, inj, guard) == run_chain_langgraph(raw, i, inj, guard)
        assert run_chain(raw, i, inj) == run_chain_langgraph(raw, i, inj)


def test_langgraph_parity_with_llm_agents_and_llm_guard():
    c = fake(wrong_once)
    agents = build_agents(c)
    for guard in (None, LLMGuard(c, "recompute"), LLMGuard(c, "naive", max_retries=0)):
        a = run_chain("3, 7, 2, 9", 0, ErrorInjector(0.0), guard, agents)
        b = run_chain_langgraph("3, 7, 2, 9", 0, ErrorInjector(0.0), guard, agents)
        assert a == b


def test_langgraph_crash_path_matches():
    agents = dict(AGENTS, analyze=lambda raw, state: 1 / 0)
    a = run_chain("1 2 3", 0, ErrorInjector(0.0), CascadeGuard(), agents)
    b = run_chain_langgraph("1 2 3", 0, ErrorInjector(0.0), CascadeGuard(), agents)
    assert a == b and a["status"] == "crashed"


def test_graph_shape():
    nodes = set(build_graph(ErrorInjector(0.0), CascadeGuard()).get_graph().nodes)
    assert {"extract", "guard_extract", "analyze", "guard_analyze", "reason", "guard_reason",
            "report", "guard_report", "finish"} <= nodes
    assert "guard_analyze" in mermaid()


def test_run_arm_engine_option_and_workers():
    inputs = make_inputs(60, 1)
    a = run_arm(inputs, ErrorInjector(0.2, 1), CascadeGuard(), engine="langgraph", workers=4)
    b = run_arm(inputs, ErrorInjector(0.2, 1), CascadeGuard())
    assert a == b
    with pytest.raises(ValueError):
        run_arm(inputs, ErrorInjector(0.0), engine="nope")


# ------------------------------------------------------------------ storage

def halted_result():
    return {"status": "halted", "final_output": None, "expected_output": "x", "detected": 1, "recovered": 0,
            "false_alarms": 0, "trace": [
                {"stage": "extract", "attempt": 0, "injected": None, "guard_issue": None, "agent_ok": True,
                 "output_ok": True, "output": {"numbers": [1, 2]}},
                {"stage": "analyze", "attempt": 0, "injected": None, "guard_issue": "LLM_REJECTED",
                 "agent_ok": False, "output_ok": False, "output": {"sum": 4}}]}


def test_store_pipeline_run_review_and_resolve():
    s = Store(":memory:")
    run_id = s.save_pipeline_run(halted_result(), "1 2", guard="recompute", engine="langgraph",
                                 model={"provider": "fake", "model": "m"},
                                 usage_delta={"calls": 3, "cached_calls": 1, "input_tokens": 90,
                                              "output_tokens": 30, "cost_usd": 0.0001},
                                 validator_log=[{"stage": "analyze", "verdict": "reject", "reason": "sum off"}],
                                 prompt="recompute")
    run = s.get_run(run_id)
    assert run["status"] == "halted" and len(run["steps"]) == 2 and run["steps"][1]["output"] == {"sum": 4}
    assert run["verdicts"][0]["reason"] == "sum off" and run["review"]["state"] == "open"
    [item] = s.reviews("open")
    assert item["reason"] == "halted at analyze: LLM_REJECTED" and item["input"] == "1 2"
    done = s.resolve_review(item["id"], "fixed", note="re-ran by hand", by="kanav")
    assert done["state"] == "resolved" and done["resolution"] == "fixed"
    with pytest.raises(ValueError):
        s.resolve_review(item["id"], "approved")
    with pytest.raises(ValueError):
        s.resolve_review(item["id"], "maybe")
    assert s.reviews("open") == [] and len(s.reviews("resolved")) == 1
    u = s.usage_summary()
    assert u["all_time"]["calls"] == 3 and u["runs_by_status"] == {"halted": 1} and u["open_reviews"] == 0


def test_store_verify_and_purge():
    s = Store(":memory:")
    ok = s.save_verify({"task": "t", "source": "a", "output": "b", "prompt": "recompute"},
                       {"verdict": "accept", "reason": "fine"}, model={})
    bad = s.save_verify({"task": "t", "source": "a", "output": "c", "prompt": "naive"},
                        {"verdict": "reject", "reason": "wrong"}, model={})
    assert [r["id"] for r in s.list_runs(kind="verify")] == [bad, ok]
    assert [i["run_id"] for i in s.reviews()] == [bad]  # only the failed check needs a person
    s._conn.execute("UPDATE runs SET created_at = '2000-01-01T00:00:00+00:00'")
    assert s.purge_older_than(30) == 2 and s.list_runs() == [] and s.reviews() == []


def test_store_file_persists(tmp_path):
    path = tmp_path / "cg.db"
    Store(path).save_verify({"prompt": "naive"}, {"verdict": "reject", "reason": "x"}, model={})
    assert len(Store(path).list_runs()) == 1


# ------------------------------------------------------------------ API with storage

@pytest.fixture
def stored():
    store = Store(":memory:")
    reset_store(store)
    yield store
    reset_store(None)
    llm_api.reset_client()


def test_api_stores_runs_and_resolves_reviews(stored):
    def always_reject(system, user, sample):
        if "TASK: validate" in user:
            return json.dumps({"verdict": "reject", "reason": "looks off"})
        return perfect(system, user, sample)

    llm_api.reset_client(fake(always_reject))
    r = api.post("/llm/pipeline", json={"numbers": "3, 7, 2, 9", "guard": "recompute", "max_retries": 1}).json()
    assert r["engine"] == "langgraph" and r["status"] == "halted" and r["run_id"]
    run = api.get(f"/runs/{r['run_id']}").json()
    assert run["engine"] == "langgraph" and len(run["verdicts"]) == 2 and run["review"]["state"] == "open"
    [item] = api.get("/review-queue").json()
    res = api.post(f"/review-queue/{item['id']}/resolve", json={"resolution": "rejected", "by": "lead"})
    assert res.status_code == 200 and res.json()["resolved_by"] == "lead (admin)"
    assert api.post(f"/review-queue/{item['id']}/resolve", json={"resolution": "approved"}).status_code == 409
    assert api.post("/review-queue/999/resolve", json={"resolution": "approved"}).status_code == 404
    assert api.get("/runs/999").status_code == 404
    u = api.get("/usage").json()
    assert u["all_time"]["calls"] >= 3 and u["runs_by_status"]["halted"] == 1


def test_api_custom_engine_and_verify_stored(stored):
    def responder(system, user, sample):
        if "STAGE: custom" in user:
            return json.dumps({"verdict": "accept", "reason": "same text"})
        return perfect(system, user, sample)

    llm_api.reset_client(fake(responder))
    r = api.post("/llm/pipeline", json={"numbers": "1 2 3", "guard": "none", "engine": "custom"}).json()
    assert r["engine"] == "custom" and r["status"] == "correct"
    v = api.post("/llm/verify", json={"task": "copy it", "source": "a", "output": "a"}).json()
    kinds = [x["kind"] for x in api.get("/runs").json()]
    assert kinds == ["verify", "pipeline"] and v["passed"] and v["run_id"]
    assert api.get("/review-queue").json() == []  # an accepted check needs no review


def test_storage_off_returns_503():
    reset_store(None)
    assert api.get("/runs").status_code == 503


def test_rule_pipeline_and_experiment_accept_engine():
    a = api.post("/pipeline", json={"numbers": "3 7 2 9", "inject_probability": 0.5, "seed": 4}).json()
    b = api.post("/pipeline", json={"numbers": "3 7 2 9", "inject_probability": 0.5, "seed": 4,
                                    "engine": "langgraph"}).json()
    assert a == b
    e = api.post("/experiment", json={"trials": 50, "engine": "langgraph"}).json()
    assert e["config"]["engine"] == "langgraph"


# ------------------------------------------------------------------ guard_step wrapper

def test_guard_step_retries_then_passes():
    calls = []

    @guard_step(rule_check(lambda out, src: out == sum(src), "total does not match"), max_retries=2)
    def add(items, attempt=0):
        calls.append(attempt)
        return sum(items) + (1 if attempt == 0 else 0)  # wrong on the first try

    assert add([1, 2, 3]) == 6 and calls == [0, 1]
    assert add.last_report.attempts == 2 and add.last_report.verdicts[0]["reason"] == "total does not match"


def test_guard_step_halts():
    @guard_step(lambda out, src: (False, "never right"), max_retries=1, name="bad_step")
    def bad(x):
        return x

    with pytest.raises(GuardHalted) as e:
        bad(5)
    assert e.value.report.status == "halted" and e.value.report.attempts == 2
    assert "bad_step" in str(e.value)


def test_rule_check_crash_is_a_fail():
    ok, reason = rule_check(lambda o, s: o["missing"])({}, None)
    assert ok is False and "KeyError" in reason


def test_llm_check_with_any_function():
    def judge(system, user, sample):
        out = json.loads(user.split("DATA: ", 1)[1])["output"]
        return json.dumps({"verdict": "accept" if out == "Total: 70.74" else "reject", "reason": "recomputed"})

    check = llm_check("Add the line items.", client=fake(judge))
    answers = iter(["Total: 71.74", "Total: 70.74"])
    report = run_guarded(lambda text: next(answers), check, "19.99, 5.00, 42.50, 3.25")
    assert report.status == "passed" and report.output == "Total: 70.74" and report.attempts == 2


def test_guarded_node_in_a_langgraph_graph():
    class S(TypedDict, total=False):
        items: list
        total: int
        halted: dict

    tries = {"n": 0}

    def total_node(state):
        tries["n"] += 1
        return {"total": sum(state["items"]) + (0 if tries["n"] > 1 else 100)}

    g = StateGraph(S)
    g.add_node("total", guarded_node(total_node, rule_check(lambda o, s: o == sum(s)),
                                     source_key="items", output_key="total"))
    g.add_edge(START, "total")
    g.add_edge("total", END)
    out = g.compile().invoke({"items": [1, 2, 3]})
    assert out["total"] == 6 and "halted" not in out

    g2 = StateGraph(S)
    g2.add_node("total", guarded_node(lambda s: {"total": -1}, rule_check(lambda o, s: o == sum(s)),
                                      source_key="items", output_key="total", max_retries=1))
    g2.add_edge(START, "total")
    g2.add_edge("total", END)
    out = g2.compile().invoke({"items": [1, 2]})
    assert out["halted"]["attempts"] == 2 and "total" not in out
