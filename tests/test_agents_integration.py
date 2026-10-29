"""Integrating outside agents: chain steps done by your agent at a URL, the remote client, the examples."""
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from cascade_guard.agent_call import AgentCallError, call_agent, check_url, extract_output
from cascade_guard.api import app
from cascade_guard.chains import ChainDef, StepDef, run_chain_def
from cascade_guard.llm.client import FakeLLMClient
from cascade_guard.remote import RemoteGuard, RemoteGuardError

ROOT = Path(__file__).resolve().parent.parent
api = TestClient(app)
TEXT = "My invoice INV-1007 was charged twice. Please refund the duplicate before Friday. Thanks."


def agent_step(**kw):
    return StepDef.model_validate({"name": "my agent", "instruction": "Summarise.", "agent": "url",
                                   "url": "https://agent.example.com/agent", **kw})


class FakeAgent:
    """An outside agent behind an httpx MockTransport: records requests, answers per a rule."""

    def __init__(self, answer):
        self.requests = []
        self.answer = answer
        self.client = httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        body = json.loads(request.content)
        self.requests.append({"body": body, "headers": dict(request.headers)})
        status, payload = self.answer(body)
        return httpx.Response(status, json=payload) if not isinstance(payload, str) else httpx.Response(status, text=payload)


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """agent.example.com does not resolve here; pretend it is a public address."""
    import socket
    real = socket.getaddrinfo

    def fake(host, *a, **k):
        if host.endswith("example.com"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        return real(host, *a, **k)
    monkeypatch.setattr(socket, "getaddrinfo", fake)


# ---------------------------------------------------------------- the definition

def test_agent_steps_need_a_url_and_a_variable_name_not_a_token():
    with pytest.raises(ValueError, match="give its URL"):
        StepDef(name="a", instruction="x", agent="url")
    with pytest.raises(ValueError, match="NAME of an environment variable"):
        agent_step(auth_env="sk-live-123")
    s = StepDef(name="a", instruction="x", agent="model", url="https://x.example.com", auth_env="T")
    assert s.url == "" and s.auth_env == ""  # model steps carry no agent settings


# ---------------------------------------------------------------- the call

def test_request_format_and_text_reply():
    a = FakeAgent(lambda b: (200, {"output": "  The customer was charged twice.  "}))
    out = call_agent(agent_step(), "in", "orig", 0, None, a.client)
    assert out == "The customer was charged twice."
    body = a.requests[0]["body"]
    assert body == {"step": "my agent", "instruction": "Summarise.", "output": "text", "keys": [], "input": "in",
                    "original": "orig", "attempt": 0, "feedback": None}


def test_json_replies_and_plain_text_replies():
    js = agent_step(output="json", keys=["category", "urgency"])
    assert extract_output(js, {"output": {"category": "billing", "urgency": "high", "extra": 1}}) == \
        {"category": "billing", "urgency": "high"}
    assert extract_output(js, {"category": "billing"}) == {"category": "billing", "urgency": None}
    assert extract_output(js, {"nothing": 1}) is None
    a = FakeAgent(lambda b: (200, "just text, not JSON"))
    assert call_agent(agent_step(), "in", "o", 0, None, a.client) == "just text, not JSON"


def test_token_comes_from_a_server_variable(monkeypatch):
    a = FakeAgent(lambda b: (200, {"output": "ok"}))
    with pytest.raises(AgentCallError, match="no MY_AGENT_TOKEN set"):
        call_agent(agent_step(auth_env="MY_AGENT_TOKEN"), "in", "o", 0, None, a.client)
    monkeypatch.setenv("MY_AGENT_TOKEN", "s3cret")
    call_agent(agent_step(auth_env="MY_AGENT_TOKEN"), "in", "o", 0, None, a.client)
    assert a.requests[-1]["headers"]["authorization"] == "Bearer s3cret"


def test_agent_errors_are_infrastructure_errors():
    a = FakeAgent(lambda b: (500, {"detail": "boom"}))
    with pytest.raises(AgentCallError, match="HTTP 500"):
        call_agent(agent_step(), "in", "o", 0, None, a.client)

    def unreachable(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(AgentCallError, match="could not be reached"):
        call_agent(agent_step(), "in", "o", 0, None, httpx.Client(transport=httpx.MockTransport(unreachable)))


@pytest.mark.parametrize("url, words", [
    ("ftp://agent.example.com/x", "must start with http"),
    ("http://169.254.169.254/latest/meta-data", "blocked address"),
    ("http://[fe80::1]/x", "blocked address"),
    ("http://0.0.0.0/x", "blocked address"),
])
def test_dangerous_urls_are_always_refused(url, words):
    assert words in check_url(url)


def test_private_addresses_only_on_your_own_computer(monkeypatch):
    assert check_url("http://127.0.0.1:9000/agent") is None  # no keys: local agents are fine
    monkeypatch.setenv("API_KEY", "adm")                    # a shared server
    assert "private address" in check_url("http://127.0.0.1:9000/agent")
    assert "private address" in check_url("http://10.1.2.3/agent")
    monkeypatch.setenv("AGENT_ALLOW_PRIVATE", "1")
    assert check_url("http://10.1.2.3/agent") is None
    assert check_url("https://agent.example.com/agent") is None


# ---------------------------------------------------------------- in a chain

def test_your_agent_in_a_chain_gets_the_feedback_and_fixes_it():
    def answer(b):
        if b["feedback"] is None:
            return 200, {"output": ""}                       # first answer is wrong
        assert "empty" in b["feedback"]["reason"]
        return 200, {"output": "Charged twice for INV-1007; wants a refund."}
    a = FakeAgent(answer)
    chain = ChainDef.model_validate({"name": "mixed", "steps": [
        agent_step().model_dump(),
        {"name": "classify", "instruction": "Classify.", "output": "json", "keys": ["category"]}]})
    r = run_chain_def(chain, TEXT, FakeLLMClient(), http_client=a.client)
    assert r["status"] == "completed"
    first = r["steps"][0]
    assert [x["passed"] for x in first["attempts"]] == [False, True] and len(a.requests) == 2
    assert a.requests[1]["body"]["attempt"] == 1
    assert "INV-1007" in json.dumps(r["steps"][1]["output"])  # the next step used the agent's fixed answer


def test_blind_retry_sends_no_feedback_and_an_agent_that_stays_wrong_halts():
    a = FakeAgent(lambda b: (200, {"output": "N/A"}))
    chain = ChainDef.model_validate({"name": "c", "repair": "blind", "max_retries": 1, "steps": [agent_step().model_dump()]})
    r = run_chain_def(chain, TEXT, FakeLLMClient(), http_client=a.client)
    assert r["status"] == "halted" and all(q["body"]["feedback"] is None for q in a.requests)


# ---------------------------------------------------------------- the API, the sample agent

def test_sample_agent_speaks_the_protocol():
    r = api.post("/demo/agent", json={"step": "s", "instruction": "x", "input": "One. Two. Three."}).json()
    assert r == {"output": "One. Two."}
    j = api.post("/demo/agent", json={"step": "s", "instruction": "x", "input": "A. B.", "output": "json",
                                      "keys": ["a", "b"]}).json()
    assert j == {"output": {"a": "A. B.", "b": "A. B."}}
    empty = [api.post("/demo/agent?mistakes=1", json={"step": f"s{i}", "instruction": "x", "input": "Hi."}).json()["output"]
             for i in range(3)]
    assert empty == ["", "", ""]
    fixed = api.post("/demo/agent?mistakes=1", json={"step": "s", "instruction": "x", "input": "Hi.", "attempt": 1,
                                                     "feedback": {"reason": "empty"}}).json()
    assert fixed == {"output": "Hi."}


def test_unsaved_chains_with_agents_need_admin(monkeypatch):
    monkeypatch.setenv("API_KEY", "adm")
    monkeypatch.setenv("VIEWER_KEY", "view")
    body = {"chain": {"name": "c", "steps": [agent_step().model_dump()]}, "input": TEXT}
    assert api.post("/chains/run", json=body, headers={"X-API-Key": "view"}).status_code == 403


def test_api_reports_an_unreachable_agent_clearly():
    body = {"chain": {"name": "c", "steps": [agent_step(url="http://127.0.0.1:1/none").model_dump()]}, "input": TEXT}
    r = api.post("/chains/run", json=body)
    assert r.status_code == 502 and "could not be reached" in r.json()["detail"]
    body["chain"]["steps"][0]["url"] = "http://169.254.169.254/"
    r = api.post("/chains/run", json=body)
    assert r.status_code == 502 and "blocked address" in r.json()["detail"]


def test_full_loop_over_real_http_with_the_sample_agent():
    """Cascade Guard calls an agent served by a real HTTP server (the sample agent on a uvicorn port)."""
    import socket
    import threading
    import time

    import uvicorn
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        chain = ChainDef.model_validate({"name": "live", "steps": [
            agent_step(url=f"http://127.0.0.1:{port}/demo/agent?mistakes=1").model_dump()]})
        r = run_chain_def(chain, TEXT, FakeLLMClient())
        assert r["status"] == "completed"
        assert [a["passed"] for a in r["steps"][0]["attempts"]] == [False, True]
        assert r["final_output"].startswith("My invoice INV-1007 was charged twice.")
    finally:
        server.should_exit = True
        t.join(5)


# ---------------------------------------------------------------- the remote client

def test_remote_guard_against_a_server(monkeypatch):
    from cascade_guard import guard_step, llm_api
    llm_api.reset_client(FakeLLMClient())
    try:
        cg = RemoteGuard("http://testserver", http_client=TestClient(app))
        v = cg.verify("Add the items", "1, 2", "3")
        assert v["verdict"] in ("accept", "reject") and "passed" in v

        @guard_step(cg.check("Add the items"), max_retries=0)
        def add(src):
            return "3"
        add("1, 2")
        r = cg.run_chain({"name": "c", "steps": [{"name": "a", "instruction": "Summarise."}]}, TEXT, simulated=True)
        assert r["status"] == "completed"
        with pytest.raises(RemoteGuardError, match="no saved chain"):
            cg.run_chain("nope", TEXT)
        monkeypatch.setenv("API_KEY", "adm")
        with pytest.raises(RemoteGuardError, match="HTTP 401"):
            cg.verify("Add the items", "1, 2", "3")
        assert RemoteGuard("http://testserver", api_key="adm", http_client=TestClient(app)).verify("Add the items", "1", "1")
    finally:
        llm_api.reset_client(None)


# ---------------------------------------------------------------- the examples run as written

@pytest.mark.parametrize("name, expect", [("guard_inside_my_agent.py", "summary:"), ("langgraph_agent.py", "category:")])
def test_examples_run(name, expect):
    path = ROOT / "examples" / name
    if not path.exists():
        pytest.skip("examples are in the source checkout")
    env = {**os.environ, "LLM_PROVIDER": "fake", "CASCADE_NO_DOTENV": "1", "PYTHONPATH": str(ROOT)}
    env.pop("CASCADE_URL", None)
    r = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, timeout=120, env=env, cwd=ROOT)
    assert r.returncode == 0 and expect in r.stdout, r.stderr[-800:]


def test_example_agent_server_protocol(monkeypatch):
    path = ROOT / "examples" / "my_agent_server.py"
    if not path.exists():
        pytest.skip("examples are in the source checkout")
    import importlib.util
    spec = importlib.util.spec_from_file_location("my_agent_server", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    c = TestClient(mod.app)
    assert c.post("/agent", json={"step": "s", "instruction": "x", "input": "A. B. C."}).json() == {"output": "A. B."}
    monkeypatch.setenv("MY_AGENT_TOKEN", "t")
    assert c.post("/agent", json={"step": "s", "instruction": "x", "input": "A."}).status_code == 401
    assert c.post("/agent", json={"step": "s", "instruction": "x", "input": "A."},
                  headers={"Authorization": "Bearer t"}).status_code == 200


# ---------------------------------------------------------------- connected agents (registry)

@pytest.fixture
def reg_store(tmp_path):
    from cascade_guard.storage.db import Store, reset_store
    s = Store(str(tmp_path / "agents.db"))
    reset_store(s)
    yield s
    reset_store(None)


def test_agent_definitions_are_validated():
    from cascade_guard.agents_registry import AgentDef
    a = AgentDef(name="Support Bot", url=" https://agent.example.com/agent ")
    assert a.name == "support-bot" and a.url == "https://agent.example.com/agent"
    for bad in ({"name": "x!", "url": "https://a.example.com"}, {"name": "x", "url": "ftp://a"},
                {"name": "x", "url": "https://a.example.com", "auth_env": "sk-123"}):
        with pytest.raises(ValueError):
            AgentDef.model_validate(bad)


def test_settings_and_dashboard_agents(reg_store, monkeypatch):
    from cascade_guard.agents_registry import all_agents, find_agent
    monkeypatch.setenv("CASCADE_AGENTS", json.dumps([
        {"name": "from-settings", "url": "https://agent.example.com/a", "auth_env": "T1"},
        {"name": "bad name!", "url": "https://x.example.com"}]))           # skipped, not fatal
    assert api.post("/agents", json={"name": "mine", "url": "https://agent.example.com/b"}).status_code == 201
    assert api.post("/agents", json={"name": "mine", "url": "https://agent.example.com/b"}).status_code == 409
    assert api.post("/agents", json={"name": "from-settings", "url": "https://agent.example.com/b"}).status_code == 409
    names = {a["name"]: a for a in api.get("/agents").json()}
    assert set(names) == {"from-settings", "mine"} and names["from-settings"]["source"] == "settings"
    assert names["from-settings"]["token_set"] is False and "token" not in json.dumps(names).lower().replace("token_set", "")
    assert find_agent("MINE").url.endswith("/b") and len(all_agents()) == 2
    r = api.put("/agents/mine", json={"name": "mine", "url": "https://agent.example.com/c"})
    assert r.status_code == 200 and find_agent("mine").url.endswith("/c")
    assert api.put("/agents/from-settings", json={"name": "from-settings", "url": "https://a.example.com"}).status_code == 409
    assert api.delete("/agents/mine").status_code == 200 and api.delete("/agents/mine").status_code == 404
    monkeypatch.setenv("CASCADE_AGENTS", "not json")
    assert api.get("/agents").json() == []


def test_connection_test_explains_problems(monkeypatch):
    from cascade_guard.agents_registry import AgentDef, test_agent
    a = AgentDef(name="a", url="https://agent.example.com/agent", auth_env="A_TOKEN")
    assert test_agent(a)["stage"] == "token"
    monkeypatch.setenv("A_TOKEN", "t")

    def agent(status, payload):
        return FakeAgent(lambda b: (status, payload)).client
    assert test_agent(a, agent(200, {"output": "pong"}))["stage"] == "done"
    assert test_agent(a, agent(200, {"output": ""}))["stage"] == "empty"
    assert test_agent(a, agent(401, {"detail": "no"}))["stage"] == "auth"
    assert test_agent(a, agent(404, {"detail": "no"}))["stage"] == "path"
    assert test_agent(a, agent(200, {"answer": "x"}))["stage"] == "format"

    def slow(request):
        raise httpx.ReadTimeout("slow")
    r = test_agent(a, httpx.Client(transport=httpx.MockTransport(slow)))
    assert r["stage"] == "timeout" and "waking up" in r["hint"]
    assert test_agent(AgentDef(name="m", url="http://169.254.169.254/x"))["stage"] == "address"


def test_chain_steps_use_connected_agents_by_name(reg_store):
    from cascade_guard.chains import ChainDef
    assert api.post("/agents", json={"name": "bot", "url": "https://agent.example.com/agent"}).status_code == 201
    calls = FakeAgent(lambda b: (200, {"output": "fine answer" if b["attempt"] else ""}))
    chain = ChainDef.model_validate({"name": "c", "steps": [
        {"name": "s1", "instruction": "Summarise.", "agent": "connected", "agent_name": "Bot"}]})
    assert chain.steps[0].agent_name == "bot" and chain.steps[0].url == ""
    r = run_chain_def(chain, TEXT, FakeLLMClient(), http_client=calls.client)
    assert r["status"] == "completed" and [a["passed"] for a in r["steps"][0]["attempts"]] == [False, True]
    assert all(q["body"]["instruction"] == "Summarise." for q in calls.requests)
    with pytest.raises(ValueError, match="name which one"):
        ChainDef.model_validate({"name": "c", "steps": [{"name": "s", "instruction": "x", "agent": "connected"}]})
    from cascade_guard.agent_call import AgentCallError
    ghost = ChainDef.model_validate({"name": "c", "steps": [{"name": "s", "instruction": "x", "agent": "connected",
                                                             "agent_name": "ghost"}]})
    with pytest.raises(AgentCallError, match="does not exist"):
        run_chain_def(ghost, TEXT, FakeLLMClient())


def test_viewers_can_run_connected_agents_but_not_manage_them(reg_store, monkeypatch):
    monkeypatch.setenv("API_KEY", "adm")
    monkeypatch.setenv("VIEWER_KEY", "view")
    view, adm = {"X-API-Key": "view"}, {"X-API-Key": "adm"}
    assert api.post("/agents", json={"name": "bot", "url": "https://agent.example.com/agent"}, headers=view).status_code == 403
    assert api.post("/agents", json={"name": "bot", "url": "https://agent.example.com/agent"}, headers=adm).status_code == 201
    assert api.get("/agents", headers=view).status_code == 200
    assert api.post("/agents/bot/test", headers=view).status_code == 403


def test_cli_agent_commands(tmp_path, monkeypatch, capsys):
    from cascade_guard import cli
    from cascade_guard.storage.db import reset_store
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CASCADE_DB", str(tmp_path / "c.db"))
    reset_store(None)
    try:
        assert cli.main(["agent", "list"]) == 0 and "No connected agents" in capsys.readouterr().out
        assert cli.main(["agent", "add", "bot", "https://agent.example.com/agent", "--token-var", "BOT_TOKEN"]) == 0
        assert "Connected bot" in capsys.readouterr().out
        assert cli.main(["agent", "list"]) == 0 and "BOT_TOKEN (NOT SET)" in capsys.readouterr().out
        assert cli.main(["agent", "test", "bot"]) == 1 and "BOT_TOKEN" in capsys.readouterr().out
        assert cli.main(["agent", "remove", "bot"]) == 0
        with pytest.raises(SystemExit):
            cli.main(["agent", "test", "bot"])
    finally:
        reset_store(None)
        os.chdir(ROOT)
