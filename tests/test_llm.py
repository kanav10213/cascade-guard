import io
import json
import time
import urllib.error

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.llm import client as client_mod
from app.llm.agents import figures_correct, run_validator, trend_correct
from app.llm.client import AnthropicClient, FakeLLM, LLMError, get_client
from app.llm.experiments import build_case, plan, run_all
from app.llm.prompts import parse_json
from app.llm.stats import stat, wilson

api = TestClient(app)


# ---------- statistics and parsing
def test_wilson_known_values():
    lo, hi = wilson(8, 10)
    assert (round(lo, 3), round(hi, 3)) == (0.490, 0.943)
    lo, hi = wilson(0, 20)
    assert lo == 0.0 and 0.13 < hi < 0.17
    assert wilson(0, 0) == (0.0, 1.0)
    assert stat(5, 10)["rate"] == 0.5


def test_parse_json_tolerates_fences_and_chatter():
    assert parse_json('```json\n{"valid": true}\n```') == {"valid": True}
    assert parse_json('Sure! {"count": 3} hope that helps') == {"count": 3}
    assert parse_json("no json here") is None
    assert parse_json(None) is None


def test_correctness_checks_are_strict():
    truth = {"count": 3, "sum": 6, "mean": 2.0, "max": 3}
    assert figures_correct(dict(truth), truth)
    assert not figures_correct({**truth, "sum": 7}, truth)
    assert not figures_correct({**truth, "sum": None}, truth)
    assert not figures_correct(None, truth)
    assert not figures_correct({**truth, "count": True}, truth)
    assert trend_correct({"trend": " Mixed "}, "mixed") and not trend_correct({"trend": 1}, "mixed")


def test_error_cases_have_the_right_kind():
    import random
    nums = [10, 20, 30, 40]
    internal = build_case(nums, "internal", "medium", random.Random(1))
    assert internal["sum"] != 100 and abs(internal["mean"] - round(internal["sum"] / internal["count"], 2)) > 0.01
    consistent = build_case(nums, "consistent", "medium", random.Random(1))
    assert consistent["sum"] != 100 and abs(consistent["mean"] - round(consistent["sum"] / consistent["count"], 2)) <= 0.01


# ---------- the real client, with HTTP mocked
class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_anthropic_client_request_shape(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"], seen["headers"], seen["body"] = req.full_url, dict(req.header_items()), json.loads(req.data)
        return _Resp({"content": [{"type": "text", "text": '{"ok": 1}'}], "usage": {"input_tokens": 11, "output_tokens": 5}})

    monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake_urlopen)
    c = AnthropicClient(model="claude-haiku-5-5", api_key="k")
    assert c.complete("sys", "user") == '{"ok": 1}'
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["X-api-key"] == "k" and seen["headers"]["Anthropic-version"] == "2023-06-01"
    assert seen["body"]["model"] == "claude-haiku-5-5" and seen["body"]["system"] == "sys"
    assert seen["body"]["messages"] == [{"role": "user", "content": "user"}]
    assert (c.usage.calls, c.usage.input_tokens, c.usage.output_tokens) == (1, 11, 5)


def test_anthropic_client_retries_rate_limits_then_gives_up(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def flaky(req, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(req.full_url, 429, "rate", {"retry-after": "0"}, io.BytesIO(b"slow down"))
        return _Resp({"content": [{"type": "text", "text": "{}"}], "usage": {}})

    monkeypatch.setattr(client_mod.urllib.request, "urlopen", flaky)
    assert AnthropicClient(api_key="k").complete("s", "u") == "{}" and calls["n"] == 3

    def bad_key(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b"bad key"))

    monkeypatch.setattr(client_mod.urllib.request, "urlopen", bad_key)
    c = AnthropicClient(api_key="k")
    with pytest.raises(LLMError, match="401"):
        c.complete("s", "u")
    assert c.usage.errors == 1


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMError):
        AnthropicClient()
    assert get_client(simulated=None).simulated is True


def test_api_failures_become_unparseable_not_crashes():
    class Down(FakeLLM):
        def complete(self, system, user):
            raise LLMError("down")

    assert run_validator(Down(), [1, 2, 3], {}, "mixed", "naive") == {"valid": None}


# ---------- experiments (simulated model, single worker for determinism)
CFG = {"agent_trials": 30, "validator_trials": 10, "e2e_trials": 30, "workers": 1, "seed": 7}


def test_plan_bounds_the_real_calls():
    pl = plan(CFG)
    assert pl["total_min"] <= pl["total_max"]
    c = FakeLLM(seed=7)
    run_all(c, CFG)
    assert pl["total_min"] <= c.usage.calls <= pl["total_max"]


def test_simulated_run_is_reproducible_and_labelled():
    a, b = run_all(FakeLLM(seed=7), CFG), run_all(FakeLLM(seed=7), CFG)
    a["meta"].pop("calls"), b["meta"].pop("calls")
    assert a == b
    assert a["meta"]["simulated"] is True and a["meta"]["llm"] == "simulated"


def test_report_structure_and_sanity():
    r = run_all(FakeLLM(seed=3), {**CFG, "validator_trials": 30, "e2e_trials": 60})
    assert set(r) == {"agents", "validator", "end_to_end", "meta"}
    arms = r["end_to_end"]["arms"]
    for arm in arms.values():
        assert arm["correct"]["k"] + arm["wrong"]["k"] + arm["halted"]["k"] == arm["correct"]["n"]
    assert arms["oracle_reference"]["wrong"]["k"] == 0
    assert arms["no_guard"]["halted"]["k"] == 0
    # the simulated validators are built so that recomputing catches consistent errors better
    v = r["validator"]
    assert v["recompute"]["overall"]["consistent"]["rate"] > v["naive"]["overall"]["consistent"]["rate"]


def test_injected_faults_lower_unguarded_accuracy():
    r = run_all(FakeLLM(seed=5), {**CFG, "parts": ["e2e"], "e2e_trials": 60, "inject": 0.4})
    arms = r["end_to_end"]["arms"]
    assert arms["no_guard"]["correct"]["rate"] < arms["oracle_reference"]["correct"]["rate"]


# ---------- API and spending safeguards
def test_llm_status_and_plan():
    s = api.get("/llm/status").json()
    assert {"real_available", "auth_enabled", "model", "max_real_calls"} <= set(s)
    p = api.post("/llm/plan", json={}).json()
    assert p["total_max"] >= p["total_min"] > 0


def test_simulated_run_through_the_api():
    job = api.post("/llm/run", json={"agent_trials": 10, "validator_trials": 4, "e2e_trials": 10, "workers": 1}).json()["job_id"]
    for _ in range(100):
        j = api.get(f"/llm/jobs/{job}").json()
        if j["status"] != "running":
            break
        time.sleep(0.1)
    assert j["status"] == "done" and j["report"]["meta"]["simulated"] is True
    assert api.get("/llm/jobs/nope").status_code == 404


def test_real_runs_are_guarded(monkeypatch):
    body = {"simulated": False, "agent_trials": 2, "validator_trials": 1, "e2e_trials": 2, "confirm": True}
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert api.post("/llm/run", json=body).status_code == 422          # no model key
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("API_KEY", raising=False)
    assert api.post("/llm/run", json=body).status_code == 403          # no auth: strangers could spend money
    monkeypatch.setenv("API_KEY", "secret")
    h = {"X-API-Key": "secret"}
    assert api.post("/llm/run", json={**body, "confirm": False}, headers=h).status_code == 422   # not confirmed
    monkeypatch.setenv("LLM_MAX_CALLS", "5")
    assert api.post("/llm/run", json=body, headers=h).status_code == 422                        # over the cap


# ---------- repeats, spread and the one-call check
import argparse

from app.llm.experiments import merge, run_repeated
from experiments import llm_eval


def test_repeats_pool_counts_and_keep_the_spread():
    r = run_repeated(FakeLLM(seed=7), {**CFG, "repeats": 3, "parts": ["agents"]})
    a = r["agents"]["analyst"]
    assert a["n"] == 3 * CFG["agent_trials"]
    assert len(a["repeat_rates"]) == 3
    assert r["meta"]["repeats"] == 3 and r["meta"]["seeds"] == [7, 8, 9]
    assert r["meta"]["calls"] == plan({**CFG, "repeats": 3, "parts": ["agents"]})["total_min"]


def test_one_repeat_is_just_a_normal_run():
    a = run_repeated(FakeLLM(seed=7), {**CFG, "repeats": 1})
    b = run_all(FakeLLM(seed=7), CFG)
    a["meta"].pop("calls"), b["meta"].pop("calls")
    assert a == b


def test_plan_scales_with_repeats():
    assert plan({**CFG, "repeats": 3})["total_max"] == 3 * plan(CFG)["total_max"]
    assert plan({**CFG, "repeats": 3})["tokens_max"]["input"] == 3 * plan(CFG)["tokens_max"]["input"]


def test_merge_pools_counts_and_sums_integers():
    one = {"x": stat(3, 10), "n": 2, "f": 0.5, "s": "a"}
    two = {"x": stat(5, 10), "n": 3, "f": 0.7, "s": "a"}
    m = merge([one, two])
    assert (m["x"]["k"], m["x"]["n"]) == (8, 20) and m["x"]["repeat_rates"] == [0.3, 0.5]
    assert m["n"] == 5 and m["f"] == 0.6 and m["s"] == "a"


def test_check_command_simulated(capsys):
    assert llm_eval.check(argparse.Namespace(simulated=True, model=None, seed=1)) == 0
    out = capsys.readouterr().out
    assert "ONE test request" in out and "only tests the code" in out


def test_check_command_explains_failures(monkeypatch, capsys):
    class Bad(FakeLLM):
        def complete(self, system, user):
            raise LLMError("HTTP 404: model: not_found")

    monkeypatch.setattr(llm_eval, "get_client", lambda **k: Bad())
    assert llm_eval.check(argparse.Namespace(simulated=False, model="x", seed=1)) == 1
    assert "--model" in capsys.readouterr().out

    class Chatty(FakeLLM):
        def complete(self, system, user):
            return "Sure, the answer is 21."

    monkeypatch.setattr(llm_eval, "get_client", lambda **k: Chatty())
    assert llm_eval.check(argparse.Namespace(simulated=False, model=None, seed=1)) == 1
    assert "not JSON" in capsys.readouterr().out


def test_check_command_without_a_key(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm_eval.check(argparse.Namespace(simulated=False, model=None, seed=1)) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_api_accepts_and_scales_repeats():
    one = api.post("/llm/plan", json={}).json()["total_max"]
    assert api.post("/llm/plan", json={"repeats": 2}).json()["total_max"] == 2 * one
    assert api.post("/llm/plan", json={"repeats": 11}).status_code == 422
