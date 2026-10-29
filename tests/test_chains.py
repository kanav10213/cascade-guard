"""User-defined agent chains: definition, engine, storage, API, CLI and MCP."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from cascade_guard import cli, llm_api
from cascade_guard.api import app
from cascade_guard.chains import (
    ChainDef,
    StepDef,
    build_chain_graph,
    format_problem,
    mermaid,
    parse_step_reply,
    run_chain_def,
    step_messages,
)
from cascade_guard.chains_api import TEMPLATES  # the shipped templates, in a checkout or an installed wheel
from cascade_guard.llm.client import FakeLLMClient
from cascade_guard.storage.db import Store, reset_store

ROOT = Path(__file__).resolve().parent.parent
api = TestClient(app)

TRIAGE = {"name": "Triage", "steps": [
    {"name": "summarise", "instruction": "Summarise the problem.", "output": "text", "max_chars": 500},
    {"name": "classify", "instruction": "Classify it.", "output": "json", "keys": ["category", "urgency"]},
    {"name": "reply", "instruction": "Draft a reply.", "check": "format"},
]}
TEXT = "My invoice INV-1007 was charged twice; please refund the duplicate before Friday."


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "chains.db"))
    reset_store(s)
    yield s
    reset_store(None)


# ---------------------------------------------------------------- the definition

def test_templates_are_valid_and_shipped():
    files = sorted(TEMPLATES.glob("*.json"))
    assert len(files) >= 3
    for f in files:
        ChainDef.model_validate(json.loads(f.read_text(encoding="utf-8")))


@pytest.mark.parametrize("bad, words", [
    ({"name": "x", "steps": []}, "at least 1"),
    ({"name": "x", "steps": [{"name": "a", "instruction": ""}]}, "at least 1"),
    ({"name": "x", "steps": [{"name": "a", "instruction": "do", "output": "json"}]}, "at least one key"),
    ({"name": "x", "steps": [{"name": "a", "instruction": "do"}, {"name": "A", "instruction": "do"}]}, "unique"),
    ({"name": "x", "steps": [{"name": "a", "instruction": "do", "output": "json", "keys": ["bad key"]}]}, "simple names"),
    ({"name": "x", "steps": [{"name": "<script>", "instruction": "do"}]}, "letters, digits"),
    ({"name": "x", "steps": [{"name": f"s{i}", "instruction": "do"} for i in range(11)]}, "at most 10"),
    ({"name": "", "steps": [{"name": "a", "instruction": "do"}]}, "at least 1"),
])
def test_bad_definitions_are_refused_with_a_reason(bad, words):
    with pytest.raises(ValidationError) as e:
        ChainDef.model_validate(bad)
    assert words in str(e.value)


def test_text_steps_drop_keys_and_names_are_tidied():
    s = StepDef(name="  my   step ", instruction="  do it ", keys=["a"])
    assert s.name == "my step" and s.instruction == "do it" and s.keys == []


def test_format_checks():
    text = StepDef(name="t", instruction="x", max_chars=5)
    js = StepDef(name="j", instruction="x", output="json", keys=["a", "b"])
    assert format_problem(text, None) == "the reply was not the requested JSON object"
    assert "empty" in format_problem(text, "  ")
    assert "limit of 5" in format_problem(text, "abcdefg")
    assert format_problem(text, "fine") is None
    assert format_problem(js, {"a": "1", "b": ""}) == "missing or empty: b"
    assert format_problem(js, {"a": "1", "b": [1]}) is None


def test_prompt_and_reply_parsing():
    js = StepDef(name="classify", instruction="Classify it.", output="json", keys=["category"])
    system, user = step_messages(js, "some text", "orig", {"reason": "wrong category", "previous_output": {"category": "x"}})
    assert "Classify it." in system and "category" in system
    assert "FEEDBACK: wrong category" in user and '"original": "orig"' in user
    assert parse_step_reply(js, 'Sure: {"category": "billing"}') == {"category": "billing"}
    assert parse_step_reply(js, "no json here") is None
    assert parse_step_reply(StepDef(name="t", instruction="x"), '{"output": " hi "}') == "hi"


# ---------------------------------------------------------------- the engine

def test_runs_on_a_langgraph_graph_built_from_the_definition():
    graph, _ = build_chain_graph(ChainDef.model_validate(TRIAGE), FakeLLMClient())
    nodes = set(graph.get_graph().nodes)
    assert {"step_0", "step_1", "step_2"} <= nodes
    m = mermaid(ChainDef.model_validate(TRIAGE))
    assert "summarise" in m and "review queue" in m


def test_clean_run_completes_and_passes_outputs_along():
    r = run_chain_def(ChainDef.model_validate(TRIAGE), TEXT, FakeLLMClient(), seed=1)
    assert r["status"] == "completed" and [s["step"] for s in r["steps"]] == ["summarise", "classify", "reply"]
    assert set(r["steps"][1]["output"]) == {"category", "urgency"}
    assert "summarise" in json.dumps(r["steps"][1]["output"])  # step 2 worked on step 1's output
    assert r["final_output"] == r["steps"][-1]["output"] and r["usage"]["calls"] >= 5


def test_planted_mistakes_are_caught_and_repaired():
    r = run_chain_def(ChainDef.model_validate(TRIAGE), TEXT, FakeLLMClient(), inject=1.0, seed=3)
    checked = r["steps"][:2]
    assert all(s["attempts"][0]["injected"] and not s["attempts"][0]["passed"] for s in checked)
    assert all(s["attempts"][-1]["passed"] for s in checked) and r["detected"] >= 2 and r["recovered"] >= 2


def broken(system, user, sample):
    """A model that never answers the classify step in the requested format."""
    if "TASK: validate" in user:
        return '{"verdict": "accept", "reason": "ok"}'
    return "I will not answer in JSON." if "STEP: classify" in user else '{"output": "fine"}'


def test_a_step_that_stays_wrong_halts_the_chain():
    r = run_chain_def(ChainDef.model_validate(TRIAGE), TEXT, FakeLLMClient(responder=broken), max_retries=1)
    assert r["status"] == "halted" and r["halted_at"] == "classify" and r["final_output"] is None
    assert [s["step"] for s in r["steps"]] == ["summarise", "classify"]  # reply never ran
    assert len(r["steps"][1]["attempts"]) == 2 and "not the requested JSON" in r["steps"][1]["attempts"][0]["reason"]


def test_guided_repair_sends_the_reason_and_blind_does_not():
    seen = []

    def responder(system, user, sample):
        seen.append(user)
        if "TASK: validate" in user:
            return '{"verdict": "accept", "reason": "ok"}'
        return '{"output": ""}' if sample == 0 else '{"output": "fixed"}'
    chain = ChainDef.model_validate({"name": "c", "steps": [{"name": "a", "instruction": "write"}]})
    assert run_chain_def(chain, "in", FakeLLMClient(responder=responder))["status"] == "completed"
    assert any("FEEDBACK: the output is empty" in u for u in seen)
    seen.clear()
    run_chain_def(chain, "in", FakeLLMClient(responder=responder), repair="blind")
    assert not any("FEEDBACK" in u for u in seen)


def test_criteria_reach_the_checker_and_none_skips_it():
    seen = []

    def responder(system, user, sample):
        seen.append(system)
        return '{"verdict": "accept", "reason": "ok"}' if "TASK: validate" in user else '{"output": "x"}'
    chain = ChainDef.model_validate({"name": "c", "steps": [
        {"name": "a", "instruction": "write", "criteria": "must mention the date"},
        {"name": "b", "instruction": "write", "check": "none"}]})
    r = run_chain_def(chain, "in", FakeLLMClient(responder=responder))
    assert any("must mention the date" in s for s in seen)
    assert r["steps"][1]["status"] == "unchecked" and len([c for c in r["checks"] if c["step"] == "b"]) == 0


# ---------------------------------------------------------------- storage

def test_store_round_trip(store):
    d = ChainDef.model_validate(TRIAGE).model_dump()
    cid = store.save_chain(d, by="admin")
    assert store.list_chains()[0]["steps"] == 3 and store.find_chain("Triage")["id"] == cid
    d["description"] = "changed"
    store.save_chain(d, chain_id=cid)
    assert store.get_chain(cid)["definition"]["description"] == "changed"
    with pytest.raises(ValueError):
        store.save_chain(dict(d), by="x")  # same name, new chain
    with pytest.raises(KeyError):
        store.save_chain(dict(d, name="other"), chain_id=999)
    assert store.delete_chain(cid) and not store.delete_chain(cid) and store.list_chains() == []


@pytest.mark.skipif(not os.getenv("CASCADE_TEST_PG"), reason="set CASCADE_TEST_PG to run")
def test_store_round_trip_postgres():
    pytest.importorskip("psycopg")
    s = Store(os.environ["CASCADE_TEST_PG"])
    s.clear()
    cid = s.save_chain(ChainDef.model_validate(TRIAGE).model_dump())
    assert s.get_chain(cid)["definition"]["steps"][1]["keys"] == ["category", "urgency"]
    assert s.delete_chain(cid)
    s.clear()


# ---------------------------------------------------------------- the API

def test_api_crud_run_and_history(store):
    t = api.get("/chains/templates").json()
    assert {x["name"] for x in t} >= {"Support ticket triage", "Meeting notes to action items", "Invoice review"}
    r = api.post("/chains", json=TRIAGE)
    assert r.status_code == 201
    cid = r.json()["id"]
    assert api.post("/chains", json=TRIAGE).status_code == 409  # name taken
    assert api.get("/chains").json()[0]["name"] == "Triage"
    changed = dict(TRIAGE, description="v2")
    assert api.put(f"/chains/{cid}", json=changed).json()["definition"]["description"] == "v2"
    assert "summarise" in api.get(f"/chains/{cid}/graph").json()["mermaid"]

    run = api.post(f"/chains/{cid}/run", json={"input": TEXT, "inject": 1.0, "seed": 2}).json()
    assert run["status"] == "completed" and run["model"]["simulated"] and run["chain_id"] == cid and run["run_id"]
    saved = api.get(f"/runs/{run['run_id']}").json()
    assert saved["kind"] == "chain" and saved["engine"] == "langgraph" and len(saved["steps"]) >= 4

    adhoc = api.post("/chains/run", json={"chain": TRIAGE, "input": TEXT}).json()
    assert adhoc["status"] == "completed" and adhoc["chain_id"] is None

    assert api.delete(f"/chains/{cid}").status_code == 200
    assert api.get(f"/chains/{cid}").status_code == 404 and api.delete(f"/chains/{cid}").status_code == 404


def test_api_validation_messages():
    r = api.post("/chains/run", json={"chain": {"name": "x", "steps": [{"name": "a", "instruction": "do",
                                                                        "output": "json"}]}, "input": "hi"})
    assert r.status_code == 422 and "at least one key" in json.dumps(r.json())
    assert api.post("/chains/run", json={"chain": TRIAGE, "input": ""}).status_code == 422
    assert api.post("/chains/run", json={"chain": TRIAGE, "input": "x" * 20_001}).status_code == 422


def test_halted_run_goes_to_the_review_queue(store, monkeypatch):
    from cascade_guard import chains_api
    monkeypatch.setattr(chains_api, "_client", lambda opts, role, key: FakeLLMClient(responder=broken))
    r = api.post("/chains/run", json={"chain": TRIAGE, "input": TEXT}).json()
    assert r["status"] == "halted"
    items = api.get("/review-queue").json()
    assert any(i["run_id"] == r["run_id"] for i in items)


def test_roles(store, monkeypatch):
    monkeypatch.setenv("API_KEY", "adm")
    monkeypatch.setenv("VIEWER_KEY", "view")
    view, adm = {"X-API-Key": "view"}, {"X-API-Key": "adm"}
    assert api.post("/chains", json=TRIAGE, headers=view).status_code == 403  # editing: admin
    cid = api.post("/chains", json=TRIAGE, headers=adm).json()["id"]
    assert api.get("/chains", headers=view).status_code == 200
    assert api.post(f"/chains/{cid}/run", json={"input": TEXT}, headers=view).status_code == 200  # simulated: free
    real = api.post(f"/chains/{cid}/run", json={"input": TEXT, "simulated": False}, headers=view)
    assert real.status_code == 403  # the real model costs money: admin
    assert api.delete(f"/chains/{cid}", headers=view).status_code == 403
    assert api.get("/chains").status_code == 401


def test_real_model_run_uses_the_configured_client(store):
    llm_api.reset_client(FakeLLMClient())
    try:
        r = api.post("/chains/run", json={"chain": TRIAGE, "input": TEXT, "simulated": False}).json()
        assert r["status"] == "completed" and r["model"]["provider"] == "fake"
        assert llm_api.get_client().usage.calls >= 5  # the shared, capped client was used
    finally:
        llm_api.reset_client(None)


def test_saving_needs_storage(monkeypatch):
    reset_store(None)
    monkeypatch.setenv("CASCADE_DB", "off")
    assert api.post("/chains", json=TRIAGE).status_code == 503
    assert api.get("/chains").json() == []
    assert api.post("/chains/run", json={"chain": TRIAGE, "input": TEXT}).json()["status"] == "completed"


# ---------------------------------------------------------------- CLI and MCP

def test_cli_chain_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CASCADE_DB", str(tmp_path / "c.db"))
    reset_store(None)
    try:
        assert cli.main(["chain", "templates"]) == 0 and "support-triage" in capsys.readouterr().out
        f = tmp_path / "mine.json"
        f.write_text(json.dumps(TRIAGE))
        assert cli.main(["chain", "import", str(f)]) == 0 and "Saved chain #" in capsys.readouterr().out
        assert cli.main(["chain", "list"]) == 0 and "Triage" in capsys.readouterr().out
        assert cli.main(["chain", "run", "Triage", "--input", TEXT]) == 0
        assert "COMPLETED" in capsys.readouterr().out
        assert cli.main(["chain", "run", "support-triage", "--input", TEXT, "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "completed"
        assert cli.main(["chain", "export", "Triage"]) == 0
        assert json.loads(capsys.readouterr().out)["steps"][1]["keys"] == ["category", "urgency"]
        with pytest.raises(SystemExit):
            cli.main(["chain", "run", "nope", "--input", "x"])
    finally:
        reset_store(None)
        os.chdir(ROOT)


def test_mcp_lists_and_runs_chains(tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env = {**os.environ, "LLM_PROVIDER": "fake", "CASCADE_DB": ":memory:", "LLM_CACHE": ":memory:",
           "CASCADE_NO_DOTENV": "1", "PYTHONPATH": str(ROOT), "CASCADE_HOME": str(tmp_path)}
    params = StdioServerParameters(command=sys.executable, args=["-m", "cascade_guard", "mcp"], cwd=str(tmp_path), env=env)

    async def go():
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            listed = json.loads((await s.call_tool("list_agent_chains", {})).content[0].text)
            ran = json.loads((await s.call_tool("run_agent_chain", {"chain": "Support ticket triage",
                                                                     "input": TEXT})).content[0].text)
            missing = json.loads((await s.call_tool("run_agent_chain", {"chain": "nope", "input": "x"})).content[0].text)
            return listed, ran, missing

    listed, ran, missing = asyncio.run(go())
    assert "Support ticket triage" in [t["name"] for t in listed["templates"]]
    assert ran["status"] == "completed" and len(ran["steps"]) == 3 and "trace" not in ran
    assert "no saved chain" in missing["error"]
