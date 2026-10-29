"""The cascade-guard command, the working folder, packaging and HTTP hardening."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cascade_guard import __version__, cli
from cascade_guard.api import app
from cascade_guard.config import home, use_home

ROOT = Path(__file__).resolve().parent.parent
ORIG = os.getcwd()
TEMPLATE = Path(cli.__file__).with_name("env.example")  # works for a source checkout and an installed wheel


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CASCADE_HOME", raising=False)
    yield tmp_path
    os.chdir(ORIG)


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0 and __version__ in capsys.readouterr().out


def test_help_without_command(capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    for cmd in ("init", "serve", "mcp", "compare", "eval"):
        assert cmd in out


def test_init_creates_env_once(in_tmp, capsys):
    assert cli.main(["init"]) == 0
    env = in_tmp / ".env"
    assert env.read_text() == TEMPLATE.read_text()
    env.write_text("LLM_PROVIDER=fake\n")
    assert cli.main(["init"]) == 0 and env.read_text() == "LLM_PROVIDER=fake\n"  # never overwritten silently
    assert cli.main(["init", "--force"]) == 0 and "ANTHROPIC_API_KEY" in env.read_text()


def test_init_with_home(tmp_path, monkeypatch):
    monkeypatch.delenv("CASCADE_HOME", raising=False)
    target = tmp_path / "work" / "here"
    try:
        assert cli.main(["init", "--home", str(target)]) == 0
        assert (target / ".env").is_file() and os.environ["CASCADE_HOME"] == str(target.resolve())
    finally:
        os.chdir(ORIG)


def test_env_template_matches_repo_copy():
    if not (ROOT / ".env.example").is_file():
        pytest.skip("not a source checkout")
    assert (ROOT / ".env.example").read_text() == TEMPLATE.read_text()


def test_home_prefers_cascade_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CASCADE_HOME", str(tmp_path))
    assert home() == tmp_path.resolve()


def test_use_home_falls_back_when_unwritable(tmp_path, monkeypatch):
    if sys.platform == "win32" or os.geteuid() == 0:
        pytest.skip("needs a non-root POSIX user (folder permissions)")
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    try:
        assert use_home(ro, fallback=True) == (tmp_path / "user" / ".cascade-guard")
        with pytest.raises(SystemExit):
            use_home(ro)
    finally:
        os.chdir(ORIG)


def test_compare_command_writes_report(in_tmp, capsys):
    assert cli.main(["compare", "--trials", "60", "--seed", "3"]) == 0
    report = json.loads((in_tmp / "report.json").read_text())
    assert report["protected_accuracy"] >= report["baseline_accuracy"]
    assert "wrote report.json" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["baseline", "protected"])
def test_single_arm_commands(in_tmp, capsys, name):
    assert cli.main([name, "--trials", "30"]) == 0
    assert json.loads(capsys.readouterr().out)["trials"] == 30


def test_eval_dry_run_through_cli(in_tmp, capsys):
    assert cli.main(["eval", "agents", "--fake", "--dry-run", "--seeds", "1", "--n", "3"]) == 0
    assert "calls" in capsys.readouterr().out.lower()


def test_mcp_print_config(tmp_path):
    env = {**os.environ, "CASCADE_HOME": str(tmp_path), "CASCADE_NO_DOTENV": "1", "PYTHONPATH": str(ROOT)}
    r = subprocess.run([sys.executable, "-m", "cascade_guard", "mcp", "--print-config"], env=env,
                       capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    cfg = json.loads(r.stdout)["mcpServers"]["cascade-guard"]
    assert cfg["args"] == ["-m", "cascade_guard", "mcp"] and cfg["env"]["CASCADE_HOME"] == str(tmp_path.resolve())
    assert "claude mcp add cascade-guard" in r.stderr


def test_python_dash_m_version():
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    r = subprocess.run([sys.executable, "-m", "cascade_guard", "--version"], env=env, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0 and __version__ in r.stdout


def test_is_local():
    assert cli._is_local("127.0.0.1") and cli._is_local("::1") and cli._is_local("localhost")
    assert not cli._is_local("0.0.0.0") and not cli._is_local("192.168.1.5")


# ------------------------------------------------------------------ HTTP hardening

def test_security_headers_on_dashboard_and_api():
    c = TestClient(app)
    for path in ("/", "/health", "/runs"):
        h = c.get(path).headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert "content-security-policy" not in c.get("/docs").headers  # Swagger UI loads its own script


def test_body_size_limit(monkeypatch):
    monkeypatch.setenv("MAX_BODY_BYTES", "1000")
    c = TestClient(app)
    r = c.post("/validate", json={"text": "x" * 5000})
    assert r.status_code == 413
    assert c.post("/validate", json={"text": "1, 2"}).status_code == 200


def test_health_reports_version():
    assert TestClient(app).get("/health").json()["version"] == __version__


# ------------------------------------------------------------------ the README example runs as written

def test_readme_guard_step_example(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    from cascade_guard import GuardHalted, guard_step, llm_check, rule_check
    from cascade_guard.llm.client import make_client

    checker = llm_check("Add up the invoice line items and give the total.", client=make_client())
    seen = []

    @guard_step(checker, max_retries=2)
    def total_agent(invoice_text, feedback=None):
        seen.append(feedback)
        return "total $15.00"

    total_agent("3 x $4.00, 2 x $1.50")  # passes or halts; either way the wiring runs
    assert seen[0] is None

    @guard_step(rule_check(lambda out, items: out == sum(items), "total does not match"))
    def add(items, feedback=None):
        seen.append(feedback)
        return sum(items) + 1  # always wrong

    with pytest.raises(GuardHalted) as stop:
        add([1, 2])
    assert stop.value.report.status == "halted" and stop.value.report.attempts == 3
    assert seen[-1]["reason"] == "total does not match" and seen[-1]["previous_output"] == 4


# ------------------------------------------------------------------ release review fixes

def test_chunked_body_over_the_limit_is_refused_before_reading(monkeypatch):
    monkeypatch.setenv("MAX_BODY_BYTES", "1000")
    monkeypatch.setenv("API_KEY", "adm")
    c = TestClient(app)

    def chunks():  # no Content-Length: httpx sends this chunked
        for _ in range(50):
            yield b"x" * 100

    r = c.post("/validate", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413  # not 401: the size check runs before anything reads the body
    ok = c.post("/validate", json={"text": "1, 2"}, headers={"X-API-Key": "adm"})
    assert ok.status_code == 200


def test_small_chunked_body_still_works(monkeypatch):
    c = TestClient(app)

    def chunks():
        yield b'{"text": '
        yield b'"1, 2"}'

    assert c.post("/validate", content=chunks(), headers={"content-type": "application/json"}).status_code == 200


def test_keyless_refuses_proxies_and_foreign_host_names():
    c = TestClient(app)
    assert c.get("/whoami").status_code == 200
    for h in ({"X-Forwarded-For": "127.0.0.1"}, {"Forwarded": "for=1.2.3.4"}, {"X-Real-IP": "1.2.3.4"}):
        r = c.get("/whoami", headers=h)
        assert r.status_code == 403 and "proxy" in r.json()["detail"]
    r = c.get("/whoami", headers={"Host": "attacker.example"})  # DNS rebinding
    assert r.status_code == 403 and "localhost" in r.json()["detail"]
    for good in ("localhost:8000", "127.0.0.1:8000", "[::1]:8000", "app.localhost"):
        assert c.get("/whoami", headers={"Host": good}).status_code == 200, good
    assert c.get("/health", headers={"Host": "attacker.example"}).status_code == 200


def test_keys_lift_the_keyless_checks(monkeypatch):
    monkeypatch.setenv("API_KEY", "adm")
    c = TestClient(app)
    r = c.get("/whoami", headers={"Host": "guard.example.com", "X-Forwarded-For": "1.2.3.4", "X-API-Key": "adm"})
    assert r.status_code == 200


def test_allow_open_network_keeps_the_host_check(monkeypatch):
    monkeypatch.setenv("ALLOW_OPEN_NETWORK", "1")
    c = TestClient(app, base_url="http://localhost")
    assert c.get("/whoami").status_code == 200
    assert c.get("/whoami", headers={"Host": "cascade-guard:8000"}).status_code == 403


def test_is_local_handles_ipv6_mapped():
    from cascade_guard.ops import _is_local
    assert _is_local("::ffff:127.0.0.1") and _is_local("127.0.0.5") and _is_local("::1")
    assert not _is_local("::ffff:10.0.0.1") and not _is_local("10.0.0.1") and not _is_local("garbage")


def test_refused_requests_are_audited_at_most_n_per_minute(monkeypatch, tmp_path):
    from cascade_guard.storage.db import Store, reset_store
    monkeypatch.setenv("API_KEY", "adm")
    monkeypatch.setenv("AUDIT_REFUSED_PER_MIN", "5")
    store = Store(str(tmp_path / "a.db"))
    reset_store(store)
    try:
        c = TestClient(app)
        for _ in range(20):
            assert c.get("/whoami", headers={"X-API-Key": "wrong"}).status_code == 401
        assert len(store.list_audit(100)) == 5
    finally:
        reset_store(None)


def test_serve_reads_host_and_port_from_env_file(in_tmp, monkeypatch):
    (in_tmp / ".env").write_text("PORT=18555\nHOST=127.0.0.1\nLOG_LEVEL=warning\nLLM_PROVIDER=fake\n")
    seen = {}
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    for k in ("PORT", "HOST", "LOG_LEVEL", "LLM_PROVIDER", "CASCADE_NO_DOTENV"):
        monkeypatch.delenv(k, raising=False)
    try:
        assert cli.main(["serve"]) == 0
        assert seen["port"] == 18555 and seen["host"] == "127.0.0.1" and seen["log_level"] == "warning"
        assert cli.main(["serve", "--port", "9001"]) == 0 and seen["port"] == 9001  # the flag wins
    finally:  # load_env wrote these straight into os.environ
        for k in ("PORT", "HOST", "LOG_LEVEL", "LLM_PROVIDER"):
            os.environ.pop(k, None)


def test_serve_refuses_proxy_headers_without_keys(in_tmp, monkeypatch, capsys):
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
    assert cli.main(["serve", "--proxy-headers"]) == 2
    assert "API_KEY" in capsys.readouterr().err


def test_home_flag_anywhere(in_tmp, tmp_path):
    target = tmp_path / "elsewhere"
    assert cli.main(["--home", str(target), "init"]) == 0 and (target / ".env").is_file()
    os.chdir(in_tmp)
    assert cli.main(["compare", "--trials", "10", "--home", str(target)]) == 0
    assert (target / "report.json").is_file()
