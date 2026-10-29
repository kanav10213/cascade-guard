from fastapi.testclient import TestClient

from cascade_guard import __version__
from cascade_guard.api import app

client = TestClient(app)


def test_dashboard_served():
    r = client.get("/")
    assert r.status_code == 200 and "Cascade Guard" in r.text


def test_health():
    assert client.get("/health").json() == {"status": "ok", "version": __version__}


def test_validate_pass_and_fail():
    assert client.post("/validate", json={"text": "all good"}).json()["passed"] is True
    bad = client.post("/validate", json={"text": "x MISSING_DATA y"}).json()
    assert bad["passed"] is False and bad["issues"] == ["MISSING_DATA"]
    assert client.post("/validate", json={"text": "  "}).json()["issues"] == ["MISSING_DATA"]


def test_pipeline_clean_run():
    r = client.post("/pipeline", json={"inject_probability": 0}).json()
    assert r["status"] == "correct"


def test_pipeline_rejects_bad_input():
    assert client.post("/pipeline", json={"numbers": "a, b"}).status_code == 422


def test_pipeline_guard_recovers_or_halts_never_silent():
    for seed in range(40):
        r = client.post("/pipeline", json={"inject_probability": 0.5, "seed": seed, "mode": "full"}).json()
        assert r["status"] in ("correct", "halted")


def test_experiment_and_trial_cap(monkeypatch):
    r = client.post("/experiment", json={"trials": 100}).json()
    assert {"baseline_accuracy", "protected_accuracy", "cri", "crs"} <= set(r)
    monkeypatch.setenv("MAX_TRIALS", "50")
    assert client.post("/experiment", json={"trials": 100}).status_code == 422


def test_api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    assert client.get("/health").status_code == 200
    assert client.post("/validate", json={"text": "x"}).status_code == 401
    ok = client.post("/validate", json={"text": "x"}, headers={"X-API-Key": "secret"})
    assert ok.status_code == 200
