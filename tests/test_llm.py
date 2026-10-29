"""Tests for the LLM version. None of them needs an API key or network."""
import json
import sys
import types

import pytest

from cascade_guard.agents.llm_agents import build_agents, normalise
from cascade_guard.agents.pipeline import compute_stats, compute_trend, expected_output, parse_numbers
from cascade_guard.evaluation.error_injector import ErrorInjector
from cascade_guard.evaluation.runner import make_inputs, run_arm, run_chain
from cascade_guard.evaluation.stats import bootstrap_product, wilson
from cascade_guard.experiments import llm_eval
from cascade_guard.guard.cascade_guard import CascadeGuard
from cascade_guard.guard.llm_guard import REJECTED, UNPARSEABLE, LLMGuard
from cascade_guard.llm.client import (
    BudgetExceeded,
    FakeLLMClient,
    LLMCallError,
    ResponseCache,
    UnknownPrice,
    resolve_prices,
)
from cascade_guard.llm.prompts import parse_last_json, parse_request

# ------------------------------------------------------------------ helpers

def perfect(system, user, sample):
    """A responder that is always right: correct agents, correct validator."""
    req = parse_request(user)
    data = req["DATA"]
    if req["TASK"] == "analyze":
        return json.dumps(compute_stats(data["numbers"]))
    if req["TASK"] == "reason":
        return json.dumps({"trend": compute_trend(data["numbers"])})
    if req["TASK"] == "validate":
        src, out = data["source"], data["output"]
        if req["STAGE"] == "analyze":
            ok = out == compute_stats(src["numbers"])
        else:
            ok = out == {"trend": compute_trend(src["numbers"])}
        return "working...\n" + json.dumps({"verdict": "accept" if ok else "reject", "reason": "x"})
    raise AssertionError(req)


def client(responder=perfect, **kw):
    return FakeLLMClient(responder, cache_path=":memory:", **kw)


# ------------------------------------------------------------------ parsing

def test_parse_last_json_handles_prose_and_fences():
    assert parse_last_json('{"a": 1}') == {"a": 1}
    assert parse_last_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_last_json('first {"a": 1} then {"verdict": "reject"}') == {"verdict": "reject"}
    assert parse_last_json('{"outer": {"inner": 2}}') == {"outer": {"inner": 2}}
    assert parse_last_json("no json here {oops") is None
    assert parse_last_json(None) is None


def test_normalise_rounding_uses_agents_own_numbers_only():
    # 41 / 8 = 5.125: Python gives 5.12, a person says 5.13. Both are accepted.
    out = normalise("analyze", {"count": 8, "sum": 41, "mean": 5.13, "max": 9})
    assert out["mean"] == round(41 / 8, 2)
    # a wrong sum is NOT repaired
    out = normalise("analyze", {"count": 8, "sum": 40, "mean": 5.13, "max": 9})
    assert out["sum"] == 40 and out["mean"] == 5.13
    # type noise and extra keys
    out = normalise("analyze", {"count": "4", "sum": 21.0, "mean": "5.25", "max": 9, "note": "hi"})
    assert out == {"count": 4, "sum": 21, "mean": 5.25, "max": 9}
    assert normalise("reason", {"trend": " Mixed "}) == {"trend": "mixed"}


# ------------------------------------------------------------------ client

def test_cache_and_sample_and_usage():
    calls = []
    c = client(lambda s, u, k: calls.append(k) or "{}")
    c.complete("sys", "TASK: x", sample=0)
    c.complete("sys", "TASK: x", sample=0)  # cached
    c.complete("sys", "TASK: x", sample=1)  # a retry is a new draw
    assert calls == [0, 1]
    assert c.usage.calls == 2 and c.usage.cached_calls == 1
    assert c.usage.cost > 0


def test_cache_persists_and_survives_a_torn_line(tmp_path):
    path = tmp_path / "cache.jsonl"
    c = FakeLLMClient(lambda s, u, k: "hello", cache_path=path)
    c.complete("s", "u")
    with path.open("a") as f:
        f.write('{"key": "half-writ')
    c2 = FakeLLMClient(lambda s, u, k: pytest.fail("should be cached"), cache_path=path)
    assert c2.complete("s", "u").text == "hello"
    assert c2.usage.calls == 0 and len(ResponseCache(path)) == 1


def test_budget_cap_stops_new_calls():
    c = client(lambda s, u, k: "x" * 4000, max_cost=1e-9)
    c.complete("s", "u1")
    with pytest.raises(BudgetExceeded):
        c.complete("s", "u2")


def test_budget_exceeded_is_not_counted_as_an_agent_crash():
    c = client(max_cost=0.0)
    with pytest.raises(BudgetExceeded):
        run_chain("3, 7, 2, 9", 0, ErrorInjector(0.0), agents=build_agents(c))


def test_prices():
    assert resolve_prices("claude-haiku-5-5") == (0.10, 0.50)
    assert resolve_prices("some-model", 1.0, 2.0) == (1.0, 2.0)
    with pytest.raises(UnknownPrice):
        resolve_prices("some-model")


def test_anthropic_client_request_and_temperature_fallback(monkeypatch):
    sent = []

    class BadRequestError(Exception):
        pass

    class Messages:
        def create(self, *, model, max_tokens, system, messages, temperature=None):
            kw = dict(model=model, max_tokens=max_tokens, system=system, messages=messages)
            if temperature is not None:
                kw["temperature"] = temperature
            sent.append(kw)
            if "temperature" in kw:
                raise BadRequestError("temperature is not supported for this model")
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text='{"trend": "mixed"}')],
                usage=types.SimpleNamespace(input_tokens=100, output_tokens=10))

    fake_sdk = types.ModuleType("anthropic")
    fake_sdk.BadRequestError = BadRequestError
    fake_sdk.Anthropic = lambda **kw: types.SimpleNamespace(messages=Messages())
    monkeypatch.setitem(sys.modules, "anthropic", fake_sdk)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    from cascade_guard.llm.client import AnthropicClient

    c = AnthropicClient(model="claude-haiku-5-5")
    r = c.complete("system text", "user text", max_tokens=50, temperature=0.0)
    assert r.text == '{"trend": "mixed"}'
    assert sent[0]["model"] == "claude-haiku-5-5" and sent[0]["system"] == "system text"
    assert sent[0]["messages"] == [{"role": "user", "content": "user text"}]
    assert "temperature" not in sent[1]
    assert c.usage.calls == 1 and c.usage.cost == pytest.approx((100 * 0.10 + 10 * 0.50) / 1e6)


def test_anthropic_client_omits_temperature_when_sdk_has_none(monkeypatch):
    sent = []

    class Messages:
        def create(self, *, model, max_tokens, system, messages):
            sent.append(model)
            return types.SimpleNamespace(content=[types.SimpleNamespace(text="{}")],
                                         usage=types.SimpleNamespace(input_tokens=1, output_tokens=1))

    fake_sdk = types.ModuleType("anthropic")
    fake_sdk.Anthropic = lambda **kw: types.SimpleNamespace(messages=Messages())
    monkeypatch.setitem(sys.modules, "anthropic", fake_sdk)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    from cascade_guard.llm.client import AnthropicClient

    AnthropicClient().complete("s", "u", temperature=1.0, sample=1)
    assert sent == ["claude-haiku-5-5"]


def test_failed_api_call_stops_the_run_instead_of_counting_as_a_crash():
    def down(system, user, sample):
        raise ConnectionError("network unreachable")

    with pytest.raises(LLMCallError):
        run_chain("3, 7, 2, 9", 0, ErrorInjector(0.0), agents=build_agents(client(down)))


def test_anthropic_client_needs_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", types.ModuleType("anthropic"))
    from cascade_guard.llm.client import AnthropicClient
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicClient()


# ------------------------------------------------------------------ agents + guard in the chain

def test_perfect_llm_agents_match_the_rule_chain():
    agents = build_agents(client())
    for i, raw in enumerate(make_inputs(50, 4)):
        r = run_chain(raw, i, ErrorInjector(0.0), agents=agents)
        assert r["status"] == "correct" and r["final_output"] == expected_output(raw)


def test_perfect_llm_validator_has_no_false_alarms_and_catches_injection():
    c = client()
    agents = build_agents(c)
    clean = run_arm(make_inputs(60, 2), ErrorInjector(0.0), LLMGuard(c, "recompute"), agents)
    assert clean["correct"] == 60 and clean["false_alarms"] == 0
    faulty = run_arm(make_inputs(200, 3), ErrorInjector(0.3, 3), LLMGuard(c, "recompute"), agents)
    assert faulty["wrong"] == 0  # every injected fault was rejected and retried
    assert faulty["detected"] > 0 and faulty["false_alarms"] == 0


def wrong_once(system, user, sample):
    """analyze adds 1 to the sum on the first attempt only."""
    req = parse_request(user)
    if req["TASK"] == "analyze" and sample == 0:
        s = compute_stats(req["DATA"]["numbers"])
        s["sum"] += 1
        s["mean"] = round(s["sum"] / s["count"], 2)
        return json.dumps(s)
    return perfect(system, user, sample)


def test_agent_error_caught_by_llm_guard_is_a_detection_not_a_false_alarm():
    c = client(wrong_once)
    agents = build_agents(c)
    base = run_chain("3, 7, 2, 9", 0, ErrorInjector(0.0), agents=agents)
    assert base["status"] == "wrong"  # silent cascade without a guard
    prot = run_chain("3, 7, 2, 9", 0, ErrorInjector(0.0), LLMGuard(c, "recompute"), agents)
    assert prot["status"] == "correct"
    assert prot["detected"] == 1 and prot["recovered"] == 1 and prot["false_alarms"] == 0
    assert prot["trace"][1]["guard_issue"] == REJECTED and prot["trace"][1]["agent_ok"] is False


def test_unparseable_agent_reply_is_retried():
    def junk_first(system, user, sample):
        if parse_request(user)["TASK"] == "analyze" and sample == 0:
            return "I think the answer is about twenty"
        return perfect(system, user, sample)

    c = client(junk_first)
    r = run_chain("3, 7, 2, 9", 0, ErrorInjector(0.0), LLMGuard(c, "naive"), build_agents(c))
    assert r["status"] == "correct" and r["recovered"] == 1
    no_guard = run_chain("3, 7, 2, 9", 0, ErrorInjector(0.0), agents=build_agents(c))
    assert no_guard["status"] == "crashed"  # report stage cannot render missing stats


def test_unparseable_validator_reply_blocks_the_stage():
    def mute_validator(system, user, sample):
        return "hmm" if parse_request(user)["TASK"] == "validate" else perfect(system, user, sample)

    c = client(mute_validator)
    g = LLMGuard(c, "naive", max_retries=1)
    r = run_chain("3, 7, 2, 9", 0, ErrorInjector(0.0), g, build_agents(c))
    assert r["status"] == "halted"
    assert r["trace"][-1]["guard_issue"] == UNPARSEABLE and g.stats["unparseable"] == 2


def test_rule_stages_fall_back_to_the_rule_guard():
    c = client()
    g = LLMGuard(c, "recompute")
    assert g.check("extract", {"numbers": [3, 7]}, "3, 7, 2, 9", {}) is not None  # source check, no LLM call
    assert c.usage.calls == 0


def test_rule_based_version_unchanged_by_llm_support():
    r = run_arm(make_inputs(300, 5), ErrorInjector(0.2, 5), CascadeGuard("full"))
    assert r["wrong"] == 0 and r["false_alarms"] == 0


# ------------------------------------------------------------------ statistics

def test_wilson_known_values():
    lo, hi = wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-4) and hi == pytest.approx(0.7634, abs=1e-4)
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-4)
    assert wilson(0, 0) == (0.0, 1.0)


def test_bootstrap_product():
    rows = [{"a": True, "b": True}] * 80 + [{"a": False, "b": None}] * 20
    point, (lo, hi) = bootstrap_product(rows, ["a", "b"], iters=500)
    assert point == pytest.approx(0.8) and lo < 0.8 < hi


# ------------------------------------------------------------------ evaluation script

def test_detect_cases_have_the_intended_kinds():
    items = llm_eval.make_llm_inputs(30, 1, 5, 20)
    for c in llm_eval.make_cases(1, items):
        nums = c["source"]["numbers"]
        out = c["output"]
        if c["stage"] != "analyze" or c["kind"] == "clean":
            continue
        assert out["sum"] != sum(nums)
        consistent = out["mean"] == round(out["sum"] / out["count"], 2)
        assert consistent == (c["kind"] == "consistent")


def test_inputs_include_hard_trend_cases():
    items = llm_eval.make_llm_inputs(300, 1, 5, 30)
    shapes = {i["shape"] for i in items}
    assert shapes == {"random", "increasing", "decreasing", "near_sorted"}
    assert all(compute_trend(i["nums"]) == "mixed" for i in items if i["shape"] == "near_sorted")
    assert all(parse_numbers(i["raw"]) == i["nums"] for i in items)


def test_eval_end_to_end_with_fake(tmp_path, capsys):
    rc = llm_eval.main(["all", "--fake", "--n", "8", "--seeds", "1", "2", "--out", str(tmp_path), "--workers", "2"])
    assert rc == 0
    [jf] = list(tmp_path.glob("*.json"))
    rep = json.loads(jf.read_text())
    assert rep["label"].startswith("SIMULATED")
    assert set(rep["agents"]["per_stage"]) == {"analyze", "reason"}
    assert set(rep["detect"]["per_prompt"]) == {"naive", "recompute"}
    assert rep["chain"]["arms"]["rules_noguard"]["correct"]["rate"] == 1.0
    assert "predicted_prod_p_i" in rep["chain"]["multiplication"]
    assert rep["usage"]["calls"] > 0
    assert "ESTIMATED COST" in capsys.readouterr().out
    assert list(tmp_path.glob("*.md"))


def test_dry_run_makes_no_calls(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = llm_eval.main(["all", "--dry-run", "--n", "5", "--cache", str(tmp_path / "c.jsonl"), "--out", str(tmp_path)])
    assert rc == 0
    assert "ESTIMATED COST" in capsys.readouterr().out
    assert not list(tmp_path.glob("*.json"))
