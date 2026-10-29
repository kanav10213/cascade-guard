"""Ask Cascade Guard: the built-in assistant, the model path with its answer checked, and the web routes."""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from cascade_guard import llm_api
from cascade_guard.api import app
from cascade_guard.assistant import DEMOS, ask, check_reply, describe, understand
from cascade_guard.claims import check_math, check_stats
from cascade_guard.llm.client import FakeLLMClient, LLMCallError
from cascade_guard.pilot.pdf import text_to_pdf
from cascade_guard.storage.db import Store, reset_store

api = TestClient(app)
BILL = "Use the cascade guard tool to check this invoice: 3 x $4.00, 2 x $1.50, total $16.00"


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "a.db"))
    reset_store(s)
    yield s
    reset_store(None)


# ---------------------------------------------------------------- understanding a request (built-in)

@pytest.mark.parametrize("message,kind", [
    (BILL, "check_bill"),
    ("2 pens at ₹15 each, Notebook 3 x ₹40, GST 18%, Total ₹177", "check_bill"),
    ("Coffee $3.50, Muffin $2.25, total $5.75", "check_bill"),
    ("Is 17 x 23 = 401?", "check_math"),
    ("what is 15% of 240", "check_math"),
    ("the average of 3, 7, 2, 9 is 5.5", "check_stats"),
    ("3, 7, 2, 9 add up to 20", "check_stats"),
    ("Run support ticket triage on: I was charged twice", "run_chain"),
    ("triage this ticket: my parcel never arrived", "run_chain"),
    ("turn these meeting notes into action items: ship Friday, Asha tests", "run_chain"),
    ("Show me Cascade Guard catching a mistake", "demo"),
    ("show me another example", "demo"),
    ("how does it work?", "demo"),
    ("List my agent chains", "list_chains"),
    ("which agents are connected?", "list_agents"),
    ("what's in the review queue?", "review_queue"),
    ("status", "status"),
    ("which model is connected?", "status"),
    ("hi", "help"), ("what can you do?", "help"), ("", "help"),
    ("is this summary correct? The customer wants a refund", "verify_answer"),
    ("tell me a joke", "unknown"),
])
def test_understand(message, kind):
    assert understand(message)[0] == kind


def test_the_claude_app_example_now_gives_the_right_answer():
    r = ask(BILL)
    assert r["kind"] == "check_bill" and r["answered_by"] == {"mode": "built-in"}
    assert r["reply"].startswith("The total is wrong. It should be $15.00") and "$16.00" in r["reply"]
    assert r["guard"]["verdict"] == "caught" and any("bill says $16.00" in c for c in r["guard"]["checked"])
    assert r["data"]["total"] == 15.0 and r["suggestions"]


def test_sums_and_lists():
    assert ask("Is 17 x 23 = 401?")["reply"] == "Wrong: 17 × 23 = 391, not 401."
    assert ask("the average of 3, 7, 2, 9 is 5.25")["reply"] == "Correct: the average of 3, 7, 2, 9 is 5.25."
    assert check_math("0.1 + 0.2 = 0.3")["status"] == "correct"            # exact, not floating point
    assert check_math("22/7 = 3.14")["note"] == "correct to 2 decimal places"
    assert check_math("10 / 0 = 1")["status"] == "unreadable"
    assert check_math("2 ** 100000")["status"] == "unreadable"            # no huge powers
    assert check_math("-5 + 3 = -2")["status"] == "correct"
    assert check_stats("the median of 3, 7, 2, 9 is 5")["status"] == "correct"


def test_demo_shows_the_guard_catching_planted_mistakes():
    r = ask("Show me Cascade Guard catching a mistake")
    d = r["data"]
    assert d["without_guard"]["status"] == "wrong" and d["with_guard"]["status"] == "correct"
    assert "$352.29" in r["reply"] and "$390.34" in r["reply"] and "both were caught" in r["reply"]
    assert all(t["rejected"] for t in d["with_guard"]["trace"] if t["planted"])
    nxt = ask("show me another example", [{"role": "assistant", "text": "", "kind": "demo", "example": 0}])
    assert nxt["data"]["example"] == 1
    last = ask("show me another example", [{"role": "assistant", "text": "", "kind": "demo",
                                            "example": len(DEMOS) - 1}])
    assert last["data"]["example"] == 0
    for n in range(len(DEMOS)):  # every demo: wrong without the guard, right with it
        from cascade_guard.assistant import Context, t_demo
        d = t_demo({"example": n}, Context())
        assert (d["without_guard"]["status"], d["with_guard"]["status"]) == ("wrong", "correct")


def test_run_a_template_chain_and_save_the_run(store):
    r = ask("Run support ticket triage on: I was charged twice for INV-1007 and want a refund before Friday")
    assert r["kind"] == "run_chain" and r["data"]["status"] == "completed"
    assert r["data"]["simulated"] is True and "free simulator" in r["reply"]
    assert store.get_run(r["data"]["run_id"]) is not None
    assert [s["step"] for s in r["data"]["steps"]] == ["summarise", "classify", "reply"]


def test_run_a_saved_chain_by_a_loose_name(store):
    from cascade_guard.assistant import TEMPLATES
    definition = json.loads((TEMPLATES / "support-triage.json").read_text(encoding="utf-8"))
    definition["name"] = "Refund desk"
    store.save_chain(definition)
    r = ask("run the refund desk chain on: charged twice")
    assert r["data"]["chain"] == "Refund desk"


def test_unknown_chain_lists_what_can_run():
    r = ask("run the foo bar chain on: hello")
    assert "no chain called 'foo bar'" in r["reply"] and "Support ticket triage" in r["reply"]


def test_lists_and_status(store):
    assert "Support ticket triage" in ask("List my agent chains")["reply"]
    assert "No outside agents" in ask("which agents are connected?")["reply"]
    assert "review queue is empty" in ask("what's in the review queue?")["reply"]
    assert "Runs saved" in ask("status")["reply"]


def test_free_text_checks_need_a_model():
    r = ask("is this summary correct? The customer wants a refund")
    assert "needs a real model" in r["reply"]


def test_unknown_request_says_what_it_can_do():
    r = ask("tell me a joke")
    assert r["kind"] == "unknown" and "check a bill" in r["reply"]


# ---------------------------------------------------------------- the model path, checked by Cascade Guard

class Scripted(FakeLLMClient):
    """A stand-in for a real model: replies come from a script, one per call."""
    is_fake = False
    provider = "test"

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []
        super().__init__(self._next, model="test-model", cache_path=None)

    def _next(self, system, user, sample):
        self.prompts.append(user)
        r = self.replies.pop(0) if self.replies else '{"reply": "done"}'
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, str) else json.dumps(r)


CALL_BILL = {"tool": "check_bill", "args": {"text": "3 x $4.00, 2 x $1.50, total $16.00"}}


def test_model_answer_with_the_tools_numbers_passes():
    c = Scripted([CALL_BILL, {"reply": "The bill is wrong: it should be $15.00, not $16.00 ($1.00 too much)."}])
    r = ask(BILL, client=c, use_model=True)
    assert r["answered_by"]["mode"] == "model" and r["kind"] == "check_bill"
    assert r["reply"].startswith("The bill is wrong")
    assert r["guard"]["reply_check"]["passed"] and not r["guard"]["reply_check"]["replaced"]
    assert r["actions"] == [{"tool": "check_bill", "args": CALL_BILL["args"]}]
    assert "RESULT" in c.prompts[1] and '"total": 15.0' in c.prompts[1]


def test_a_made_up_number_is_caught_and_the_model_fixes_it():
    c = Scripted([CALL_BILL, {"reply": "It should be $16.50."},
                  {"reply": "The total is wrong: it should be $15.00, not $16.00."}])
    r = ask(BILL, client=c, use_model=True)
    attempts = r["guard"]["reply_check"]["attempts"]
    assert [a["passed"] for a in attempts] == [False, True]
    assert "16.50" in attempts[0]["reason"] and "REJECTED" in c.prompts[-1]
    assert r["reply"] == "The total is wrong: it should be $15.00, not $16.00."
    assert any("assistant's own answer" in x for x in r["guard"]["caught"])


def test_a_model_that_keeps_misreporting_is_replaced_by_the_built_in_answer():
    c = Scripted([CALL_BILL, {"reply": "It's $14.00."}, {"reply": "It's $14.00."}, {"reply": "It's $14.00."}])
    r = ask(BILL, client=c, use_model=True)
    assert r["guard"]["reply_check"]["replaced"] is True
    assert r["reply"] == describe("check_bill", r["data"])        # the exact, built-in answer


def test_a_wrong_verdict_is_caught():
    c = Scripted([CALL_BILL, {"reply": "All good, the bill is correct."},
                  {"reply": "The bill has a mistake: the total should be $15.00."}])
    r = ask(BILL, client=c, use_model=True)
    assert r["guard"]["reply_check"]["attempts"][0]["reason"].startswith("the tool found a mistake")
    assert r["guard"]["reply_check"]["passed"]


def test_plain_text_replies_and_greetings():
    r = ask("hello", client=Scripted(["Hi! Paste a bill and I'll check it."]), use_model=True)
    assert r["reply"] == "Hi! Paste a bill and I'll check it." and r["kind"] == "chat"


def test_tool_calls_are_bounded():
    c = Scripted([{"tool": "status", "args": {}}] * 10 + [{"reply": "done"}])
    r = ask("status please", client=c, use_model=True)
    assert len(r["actions"]) == 4


def test_model_failure_falls_back_to_the_built_in_assistant():
    r = ask(BILL, client=Scripted([LLMCallError("boom")]), use_model=True)
    assert r["answered_by"] == {"mode": "built-in"} and "not available" in r["notice"]
    assert r["reply"].startswith("The total is wrong")


def test_check_reply_rules():
    actions = [{"tool": "check_bill", "result": {"status": "wrong", "total": 15.0, "stated": {"total": 16.0}}}]
    assert check_reply("Wrong: 2 items, should be $15.00, not $16.00", actions, "")[0]
    assert not check_reply("Wrong: should be $15.01", actions, "")[0]
    assert check_reply("Wrong: you wrote 2026-10-29", actions, "dated 2026-10-29")[0]   # from the message
    assert not check_reply("", [], "")[0]
    ok = [{"tool": "check_math", "result": {"status": "correct", "value": "391"}}]
    assert check_reply("Correct, 391. Nothing wrong.", ok, "")[0]
    assert not check_reply("That is wrong.", ok, "")[0]


# ---------------------------------------------------------------- web routes

def test_page_and_ask_route(monkeypatch):
    monkeypatch.setenv("API_KEY", "adm")
    monkeypatch.setenv("VIEWER_KEY", "view")
    assert api.get("/assistant").status_code == 200 and "Ask Cascade Guard" in api.get("/assistant").text
    assert api.post("/assistant/ask", json={"message": "hi"}).status_code == 401
    r = api.post("/assistant/ask", json={"message": BILL}, headers={"X-API-Key": "view"})
    assert r.status_code == 200 and r.json()["data"]["total"] == 15.0
    info = api.get("/assistant/info", headers={"X-API-Key": "view"}).json()
    assert info["role"] == "viewer" and info["model_available"] is False and info["examples"]


def test_model_mode_needs_a_model_and_an_admin_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "adm")
    monkeypatch.setenv("VIEWER_KEY", "view")
    llm_api.reset_client(FakeLLMClient(cache_path=":memory:"))  # the simulator is not a real model
    try:
        r = api.post("/assistant/ask", json={"message": "hi", "use_model": True}, headers={"X-API-Key": "adm"})
        assert r.status_code == 409
        llm_api.reset_client(Scripted([CALL_BILL, {"reply": "Wrong: it should be $15.00, not $16.00."}]))
        info = api.get("/assistant/info", headers={"X-API-Key": "view"}).json()
        assert info["model_needs_admin"] and not info["model_available"]
        viewer = api.post("/assistant/ask", json={"message": BILL}, headers={"X-API-Key": "view"}).json()
        assert viewer["answered_by"] == {"mode": "built-in"}          # viewers never spend money
        assert api.post("/assistant/ask", json={"message": BILL, "use_model": True},
                        headers={"X-API-Key": "view"}).status_code == 403
        admin = api.post("/assistant/ask", json={"message": BILL}, headers={"X-API-Key": "adm"}).json()
        assert admin["answered_by"]["mode"] == "model" and admin["guard"]["reply_check"]["passed"]
    finally:
        llm_api.reset_client(None)


def test_pdf_invoice(monkeypatch):
    text = "INVOICE INV-2041\n3 x Steel bolts @ $2.50\n1 x Claw hammer @ $14.99\nTax: 8%\nTotal: $24.29\n"
    pdf = base64.b64encode(text_to_pdf(text)).decode()
    r = api.post("/assistant/ask", json={"message": "", "pdf_base64": pdf, "pdf_name": "inv.pdf"})
    j = r.json()
    assert r.status_code == 200 and j["kind"] == "check_bill" and j["pdf_name"] == "inv.pdf"
    assert "Claw hammer" in j["extracted_text"] and j["data"]["status"] == "correct"   # 7.50 + 14.99 + 8% = 24.29
    assert api.post("/assistant/ask", json={"message": "", "pdf_base64": "%%%"}).status_code == 422
    assert api.post("/assistant/ask", json={"message": ""}).status_code == 422
    assert api.post("/assistant/ask", json={"message": "x" * 20_001}).status_code == 422


# ---------------------------------------------------------------- command line and the Claude app installer

def test_cli_ask(capsys, tmp_path):
    from cascade_guard.cli import main
    assert main(["ask", "--home", str(tmp_path), BILL]) == 0
    out = capsys.readouterr().out
    assert "It should be $15.00" in out and "WRONG (bill says $16.00)" in out
    assert main(["ask", "--home", str(tmp_path), "--exit-code", "Is 17 x 23 = 401?"]) == 1
    capsys.readouterr()
    assert main(["ask", "--home", str(tmp_path), "--json", "Is 17 x 23 = 391?"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["status"] == "correct"
    bill = tmp_path / "bill.pdf"
    bill.write_bytes(text_to_pdf("INVOICE INV-9\n2 x Lamp @ $10.00\nTotal: $20.00\n"))
    assert main(["ask", "--home", str(tmp_path), "--file", str(bill)]) == 0
    assert "The bill is correct" in capsys.readouterr().out


def test_cli_ask_conversation(monkeypatch, capsys, tmp_path):
    from cascade_guard.cli import main
    lines = iter(["show me cascade guard catching a mistake", "show me another example", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))
    assert main(["ask", "--home", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "$352.29" in out and "$449.40" in out   # the second demo is a different example


def test_install_claude_keeps_other_settings(tmp_path, monkeypatch):
    from cascade_guard.mcp_server import install_claude
    monkeypatch.setenv("CASCADE_HOME", str(tmp_path))
    existing = tmp_path / "a" / "claude_desktop_config.json"
    existing.parent.mkdir()
    existing.write_text(json.dumps({"preferences": {"x": 1}, "mcpServers": {"other": {"command": "x"}}}))
    fresh = tmp_path / "b" / "claude_desktop_config.json"
    broken = tmp_path / "c" / "claude_desktop_config.json"
    broken.parent.mkdir()
    broken.write_text("{not json")
    assert install_claude([existing, fresh, broken]) == 0
    cfg = json.loads(existing.read_text())
    assert cfg["preferences"] == {"x": 1} and set(cfg["mcpServers"]) == {"other", "cascade-guard"}
    assert cfg["mcpServers"]["cascade-guard"]["env"]["CASCADE_HOME"] == str(tmp_path.resolve())
    assert (tmp_path / "a" / "claude_desktop_config.json.bak").exists()
    assert "cascade-guard" in json.loads(fresh.read_text())["mcpServers"]
    assert broken.read_text() == "{not json"          # never overwrites a file it cannot read
    assert install_claude([broken]) == 1
