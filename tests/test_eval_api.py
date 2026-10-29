"""The connection check and the evaluation from the API (ported from the earlier LLM version)."""
import time

import pytest
from fastapi.testclient import TestClient

from cascade_guard import eval_api, llm_api
from cascade_guard.api import app
from cascade_guard.experiments.check import _hint, connection_check
from cascade_guard.llm.client import FakeLLMClient, LLMCallError

api = TestClient(app)
SMALL = {"command": "agents", "seeds": [1], "n": 3, "simulated": True}


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # results/ lands here
    eval_api._JOBS.clear()
    yield
    eval_api._JOBS.clear()
    llm_api.reset_client(None)


def wait(job_id, seconds=60):
    t = time.time()
    while time.time() - t < seconds:
        j = api.get(f"/llm/eval/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_check_with_the_simulator():
    llm_api.reset_client(FakeLLMClient())
    r = api.post("/llm/check", json={}).json()
    assert r["ok"] and r["stage"] == "done" and r["simulated"] and "work" in r["hint"]


def test_check_without_a_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm_api.reset_client(None)
    r = api.post("/llm/check", json={}).json()
    assert not r["ok"] and r["stage"] == "settings" and "ANTHROPIC_API_KEY" in r["hint"]


def test_check_explains_provider_errors():
    class Broken(FakeLLMClient):
        def _call(self, *a, **k):
            raise RuntimeError("Error code: 401 - authentication_error: invalid x-api-key")
    r = connection_check(Broken())
    assert not r["ok"] and r["stage"] == "call" and "rejected the API key" in r["hint"]
    assert "model name" in _hint("Error code: 404 not_found_error model: claude-x")
    assert "credit" in _hint("429 rate limit")


def test_check_flags_a_wrong_reply_format():
    r = connection_check(FakeLLMClient(responder=lambda system, user, sample: "Sure! The numbers look great."))
    assert not r["ok"] and r["stage"] == "format"


def test_plan_spends_nothing_and_needs_no_key():
    r = api.post("/llm/eval/plan", json={**SMALL, "simulated": False}).json()
    assert r["plan"]["unique_new_calls"] > 0 and not r["simulated"] and r["plan"]["est_cost_usd"] >= 0


def test_simulated_run_to_completion_and_download():
    r = api.post("/llm/eval/run", json=SMALL)
    assert r.status_code == 200 and r.json()["simulated"]
    j = wait(r.json()["job_id"])
    assert j["status"] == "done", j
    assert "SIMULATED" in j["report"]["label"] and "agents" in j["report"]
    assert j["markdown"].startswith("# LLM evaluation") and j["usage"]["calls"] > 0
    assert [x["id"] for x in api.get("/llm/eval/jobs").json()] == [j["id"]]
    assert "report" not in api.get("/llm/eval/jobs").json()[0]  # the list stays small


def test_real_run_needs_admin_confirm_and_a_model(monkeypatch):
    monkeypatch.setenv("API_KEY", "adm")
    monkeypatch.setenv("VIEWER_KEY", "view")
    real = {**SMALL, "simulated": False}
    assert api.post("/llm/eval/run", json=real, headers={"X-API-Key": "view"}).status_code == 403
    assert api.post("/llm/eval/run", json=SMALL, headers={"X-API-Key": "view"}).status_code == 200  # simulated is free
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = api.post("/llm/eval/run", json=real, headers={"X-API-Key": "adm"})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_real_run_asks_for_confirmation_and_respects_the_cap(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:9/v1")  # local: no key needed, never called here
    monkeypatch.setenv("LLM_MODEL", "local-model")
    monkeypatch.setenv("LLM_PRICE_IN", "1000")
    monkeypatch.setenv("LLM_PRICE_OUT", "1000")
    real = {**SMALL, "simulated": False, "seeds": [1, 2], "n": 40}
    r = api.post("/llm/eval/run", json=real)
    assert r.status_code == 422 and "confirm=true" in r.json()["detail"]
    r = api.post("/llm/eval/run", json={**real, "confirm": True, "max_cost": 0.01})
    assert r.status_code == 422 and "above the cap" in r.json()["detail"]
    assert eval_api._JOBS == {}  # nothing started


def test_unknown_job_and_bad_input():
    assert api.get("/llm/eval/jobs/nope").status_code == 404
    assert api.post("/llm/eval/run", json={**SMALL, "seeds": []}).status_code == 422
    assert api.post("/llm/eval/run", json={**SMALL, "min_len": 10, "max_len": 5}).status_code == 422


def test_cli_check_with_simulator(capsys):
    from cascade_guard import cli
    assert cli.main(["eval", "check", "--fake"]) == 0
    assert capsys.readouterr().out.startswith("OK: fake")


def test_cli_check_reports_a_missing_key(monkeypatch, capsys):
    from cascade_guard import cli
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli.main(["eval", "check"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_llm_error_type_is_reported():
    assert issubclass(LLMCallError, RuntimeError)
