"""The LLM routes of the web service, and the OpenAI-compatible client.
No key and no network: the model is a fake or a mocked HTTP transport."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from cascade_guard import llm_api
from cascade_guard.api import app
from cascade_guard.config import load_env
from cascade_guard.llm.client import FakeLLMClient, NotConfigured, OpenAICompatClient, make_client
from tests.test_llm import perfect, wrong_once

api = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_client():
    llm_api.reset_client()
    yield
    llm_api.reset_client()


def use(responder=perfect, **kw):
    llm_api.reset_client(FakeLLMClient(responder, cache_path=":memory:", **kw))


def test_status_explains_how_to_fix_when_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = api.get("/llm/status").json()
    assert r["configured"] is False and "ANTHROPIC_API_KEY" in r["error"]
    r = api.post("/llm/pipeline", json={})
    assert r.status_code == 503 and ".env" in r.json()["detail"]


def test_status_with_fake_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    r = api.get("/llm/status").json()
    assert r["configured"] and r["simulated"] and r["provider"] == "fake"


def test_pipeline_guard_catches_the_models_mistake():
    use(wrong_once)
    off = api.post("/llm/pipeline", json={"numbers": "3, 7, 2, 9", "guard": "none"}).json()
    on = api.post("/llm/pipeline", json={"numbers": "3, 7, 2, 9", "guard": "recompute"}).json()
    assert off["status"] == "wrong"
    assert on["status"] == "correct" and on["recovered"] == 1
    assert [j["verdict"] for j in on["validator_log"]] == ["reject", "accept", "accept"]
    analyze = [t for t in on["trace"] if t["stage"] == "analyze"]
    assert analyze[0]["agent_ok"] is False and analyze[0]["output"]["sum"] == 22
    # both runs started from the same cached first answer
    assert on["usage"]["cached_calls"] >= 2


def test_pipeline_rule_guard_and_limits():
    use()
    r = api.post("/llm/pipeline", json={"numbers": "1 2 3", "guard": "rules"}).json()
    assert r["status"] == "correct" and r["validator_log"] == []
    too_many = ", ".join(["1"] * 61)
    assert api.post("/llm/pipeline", json={"numbers": too_many}).status_code == 422


def test_budget_limit_returns_429():
    use(max_calls=1)
    r = api.post("/llm/pipeline", json={"numbers": "3, 7, 2, 9", "guard": "recompute"})
    assert r.status_code == 429 and "LLM_MAX_CALLS" in r.json()["detail"]


def test_failed_model_call_returns_502():
    def down(system, user, sample):
        raise ConnectionError("no route to host")
    use(down)
    r = api.post("/llm/pipeline", json={"numbers": "3, 7, 2, 9"})
    assert r.status_code == 502 and "no route to host" in r.json()["detail"]


def test_verify_any_output():
    seen = {}

    def judge(system, user, sample):
        seen["system"], seen["user"] = system, user
        out = json.loads(user.split("DATA: ", 1)[1])["output"]
        return "working\n" + json.dumps({"verdict": "accept" if out == "Total: 70.74" else "reject",
                                          "reason": "recomputed 70.74"})

    use(judge)
    body = {"task": "Add the line items.", "source": "19.99, 5.00, 42.50, 3.25", "output": "Total: 71.74"}
    r = api.post("/llm/verify", json=body).json()
    assert r["passed"] is False and r["verdict"] == "reject" and "70.74" in r["reason"]
    assert "Add the line items." in seen["system"] and "recompute" in seen["system"]
    r = api.post("/llm/verify", json={**body, "output": "Total: 70.74"}).json()
    assert r["passed"] is True
    assert api.post("/llm/verify", json={**body, "output": "x" * 20_001}).status_code == 422


def test_llm_routes_respect_api_key(monkeypatch):
    use()
    monkeypatch.setenv("API_KEY", "secret")
    assert api.get("/llm/status").status_code == 401
    assert api.get("/llm/status", headers={"X-API-Key": "secret"}).status_code == 200


# ------------------------------------------------------------------ .env and providers

def test_load_env(tmp_path, monkeypatch):
    monkeypatch.delenv("CASCADE_NO_DOTENV", raising=False)
    monkeypatch.delenv("CG_A", raising=False)
    monkeypatch.delenv("CG_B", raising=False)
    monkeypatch.setenv("CG_C", "from-real-env")
    f = tmp_path / ".env"
    f.write_text('# comment\nCG_A="quoted value"\nexport CG_B=plain  # note\nCG_C=from-file\nCG_EMPTY=\n')
    loaded = load_env(f)
    import os
    assert os.environ["CG_A"] == "quoted value" and os.environ["CG_B"] == "plain"
    assert os.environ["CG_C"] == "from-real-env"  # the real environment wins
    assert "CG_EMPTY" not in loaded
    monkeypatch.delenv("CG_A")
    monkeypatch.delenv("CG_B")


def test_make_client_providers(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    assert make_client().is_fake
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(NotConfigured, match="LLM_MODEL"):
        make_client()
    monkeypatch.setenv("LLM_PROVIDER", "nonsense")
    with pytest.raises(NotConfigured):
        make_client()


def _openai_transport(log, reject=()):
    def handler(request):
        body = json.loads(request.content)
        log.append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": body})
        for field in reject:
            if field in body:
                return httpx.Response(400, json={"error": {"message": f"Unsupported parameter: '{field}'"}})
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": '{"trend": "mixed"}'}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 5}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_openai_compatible_client(monkeypatch):
    log = []
    c = OpenAICompatClient(model="some-model", base_url="https://api.example.com/v1/", api_key="k",
                           http_client=_openai_transport(log), strict_prices=False)
    r = c.complete("sys", "usr", max_tokens=40)
    assert r.text == '{"trend": "mixed"}' and (r.input_tokens, r.output_tokens) == (50, 5)
    sent = log[0]
    assert sent["url"] == "https://api.example.com/v1/chat/completions" and sent["auth"] == "Bearer k"
    assert sent["body"]["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}]
    assert sent["body"]["max_tokens"] == 40
    assert c.describe()["price_per_million_tokens"] is None and c.usage.cost == 0


def test_openai_client_adapts_to_newer_models():
    log = []
    c = OpenAICompatClient(model="m", base_url="https://x/v1", api_key="k", strict_prices=False,
                           http_client=_openai_transport(log, reject=("max_tokens", "temperature")))
    c.complete("s", "u", max_tokens=10, temperature=0.0)
    final = log[-1]["body"]
    assert "max_tokens" not in final and final["max_completion_tokens"] == 10 and "temperature" not in final


def test_local_ollama_needs_no_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    log = []
    c = OpenAICompatClient(model="llama3.2", base_url="http://localhost:11434/v1",
                           http_client=_openai_transport(log), strict_prices=False)
    c.complete("s", "u")
    assert log[0]["auth"] is None
    with pytest.raises(NotConfigured, match="OPENAI_API_KEY"):
        OpenAICompatClient(model="m", base_url="https://api.openai.com/v1", http_client=_openai_transport([]))
