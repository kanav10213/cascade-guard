"""One --ai setting for any AI: local servers, cloud APIs, URLs, programs and Python functions; receipts run."""
import datetime as dt
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from cascade_guard import receipts
from cascade_guard.llm.client import NotConfigured, make_client
from cascade_guard.llm.presets import (
    CommandClient,
    client_from_ai,
    detect_local,
    function_client,
    list_models,
    parse_ai,
    strip_thinking,
)
from cascade_guard.receipts import check_extract, currency_of, money, process_receipt
from cascade_guard.receipts_run import SAMPLES as EXAMPLES  # the sample receipts shipped in the package
from cascade_guard.receipts_run import find_files, run_files, summary

TODAY = dt.date(2026, 11, 6)


class _Model:
    """A stand-in local AI server in the OpenAI format: it reads receipts, first making the usual mistake
    (the cash paid read as the total), and corrects itself when the guard says why."""
    calls: list = []

    @staticmethod
    def answer(system: str, user: str) -> str:
        if "read shop receipts" in system:
            text = user.split("RECEIPT TEXT:\n", 1)[1].split("\n\nYOUR PREVIOUS", 1)[0]
            first = text.splitlines()[0]
            date = receipts.dates_in(text)[0][1] if receipts.dates_in(text) else ""
            amounts = [(a, ln) for ln in receipts.lines_of(text) for a in receipts.amounts_in(ln)]
            cash = next((a for a, ln in amounts if ln.upper().startswith("CASH")), None)
            total = next((a for a, ln in amounts if ln.upper().startswith("TOTAL ")), None)
            pick = cash if cash is not None and "REJECTED" not in user else total
            return "<think>Let me read it.</think>" + json.dumps(
                {"vendor": first, "date": date, "total": f"{pick}", "currency": "INR"})
        if "expense policy" in system:
            f = json.loads(user.split("RECEIPT FIELDS: ", 1)[1].split("\n", 1)[0])
            return json.dumps(receipts.decide(f))
        if "ledger" in system:
            f = json.loads(user.split("RECEIPT FIELDS: ", 1)[1].split("\n", 1)[0])
            return json.dumps(receipts.rule_ledger(f, {"decision": "approve"}))
        return '{"total": "497.00"}'  # the connection test


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: dict, code: int = 200):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path.endswith("/models"):
            self._send({"object": "list", "data": [{"id": "nomic-embed-text"}, {"id": "tiny-receipt-model"}]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Model.calls.append(body["model"])
        system, user = body["messages"][0]["content"], body["messages"][1]["content"]
        self._send({"choices": [{"message": {"role": "assistant", "content": _Model.answer(system, user)}}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 20}})


@pytest.fixture()
def local_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Model.calls = []
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


@pytest.fixture()
def rupees():
    receipts.configure(currency="INR")
    yield
    receipts.configure(approval_limit="200", currency="RM")


def test_parse_ai_reads_every_kind():
    assert parse_ai("ollama:qwen2.5:7b").model == "qwen2.5:7b"
    assert parse_ai("Claude").kind == "anthropic"
    assert parse_ai("LM-Studio").name == "lmstudio"
    u = parse_ai("http://box:9000/v1#my-model")
    assert (u.kind, u.base_url, u.model) == ("url", "http://box:9000/v1", "my-model")
    assert parse_ai("cmd:ollama run qwen2.5").command == ["ollama", "run", "qwen2.5"]
    assert parse_ai("none").kind == "rules"
    with pytest.raises(NotConfigured, match="unknown AI"):
        parse_ai("skynet")


def test_cloud_ai_without_a_key_says_which_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(NotConfigured, match="GEMINI_API_KEY"):
        client_from_ai("gemini:gemini-2.5-flash")


def test_a_local_server_is_found_and_its_model_picked(local_server, monkeypatch):
    monkeypatch.setenv("LMSTUDIO_BASE_URL", local_server)
    assert list_models(local_server) == ["tiny-receipt-model", "nomic-embed-text"]  # chat models first
    assert ("lmstudio", local_server, ["tiny-receipt-model", "nomic-embed-text"]) in detect_local()
    client = client_from_ai("lmstudio")
    assert client.model == "tiny-receipt-model" and client.price_known and client.cost_of(1000, 1000) == 0


def test_cascade_ai_drives_make_client_everywhere(local_server, monkeypatch):
    monkeypatch.setenv("CASCADE_AI", local_server + "#tiny-receipt-model")
    client = make_client(strict_prices=False)
    assert client.provider == "openai" and client.base_url == local_server
    assert make_client(fake=True).is_fake  # an explicit choice still wins


def test_any_local_ai_reads_receipts_and_the_guard_fixes_its_mistake(local_server, rupees):
    client = client_from_ai(f"{local_server}#tiny-receipt-model", cache_path=None)
    res = process_receipt((EXAMPLES / "kirana-kothrud.txt").read_text(encoding="utf-8"), client=client, today=TODAY)
    assert res["status"] == "posted" and res["entry"]["amount"] == "497.00"
    first = res["trace"][0]
    assert first["output"]["total"] == "500.00" and "cash paid" in first["reason"]  # caught, then fixed
    assert res["caught"] == 1 and _Model.calls


def test_a_python_function_or_a_program_can_be_the_ai(tmp_path, rupees):
    fn = function_client(_Model.answer)
    res = process_receipt((EXAMPLES / "kirana-kothrud.txt").read_text(encoding="utf-8"), client=fn, today=TODAY)
    assert res["status"] == "posted" and res["entry"]["amount"] == "497.00"
    prog = tmp_path / "echo_ai.py"
    prog.write_text("import sys\nprint('\\x1b[32m<think>hm</think>{\"total\": \"497.00\"}\\x1b[0m')\n")
    cmd = CommandClient([sys.executable, str(prog)])
    assert json.loads(cmd.complete("sys", "user").text) == {"total": "497.00"}
    bad = CommandClient([sys.executable, "-c", "import sys; sys.exit(3)"])
    with pytest.raises(Exception, match="exited with 3"):
        bad.complete("s", "u")


def test_thinking_is_dropped_from_answers():
    assert strip_thinking('<think>long thought</think>\n{"a": 1}').strip() == '{"a": 1}'
    assert strip_thinking("<think>never finished") == ""


def test_indian_receipts_rupees_lakhs_gst_and_round_off():
    assert money("₹1,25,000.00") == money("125000") and money("Rs. 2,090.00") == money("2090")
    pune = (EXAMPLES / "restaurant-pune.txt").read_text(encoding="utf-8")
    assert currency_of(pune) == "INR"
    fields = {"vendor": "SHREE KRISHNA BHOJANALAYA", "date": "04/11/2026"}
    assert check_extract({**fields, "total": "430.00"}, pune, TODAY) == (True, None)  # net payable after round off
    ok, why = check_extract({**fields, "total": "430.50"}, pune, TODAY)
    assert not ok and "430.00" in why
    ok, why = check_extract({**fields, "total": "10.25"}, pune, TODAY)
    assert not ok and "tax" in why  # CGST is a tax


def test_a_wrong_currency_is_caught(rupees):
    kirana = (EXAMPLES / "kirana-kothrud.txt").read_text(encoding="utf-8")   # prints no currency
    fields = {"vendor": "SHRI BALAJI KIRANA STORES", "date": "05/11/2026", "total": "497.00"}
    assert check_extract({**fields, "currency": "INR"}, kirana, TODAY)[0]
    ok, why = check_extract({**fields, "currency": "USD"}, kirana, TODAY)
    assert not ok and "country that uses INR" in why   # Kothrud, Pune
    pune = (EXAMPLES / "restaurant-pune.txt").read_text(encoding="utf-8")   # prints ₹
    ok, why = check_extract({"vendor": "SHREE KRISHNA BHOJANALAYA", "date": "04/11/2026", "total": "430.00",
                             "currency": "MYR"}, pune, TODAY)
    assert not ok and "the currency is INR" in why


def test_posted_amounts_are_written_one_way():
    def comma_ai(system, user):
        out = json.loads(strip_thinking(_Model.answer(system, user)))
        if "amount" in out:
            out["amount"] = "2,090.00"
        return json.dumps(out)
    res = process_receipt((EXAMPLES / "fuel-pune.txt").read_text(encoding="utf-8"), client=function_client(comma_ai), today=TODAY)
    assert res["status"] == "posted" and res["entry"]["amount"] == "2090.00"
    assert summary([{"name": "f", **res}])["total_posted"] == {"INR": "2090.00"}


def test_a_receipt_from_a_known_country_gets_its_currency(rupees):
    kl = (EXAMPLES / "kopi-house-kl.txt").read_text(encoding="utf-8")  # Kuala Lumpur, prints no currency
    assert currency_of(kl) == "RM"
    fields = {"vendor": "KOPI HOUSE RESTAURANT", "date": "03/01/2018", "total": "19.05"}
    assert check_extract({**fields, "currency": "MYR"}, kl, dt.date(2026, 11, 6))[0]
    ok, why = check_extract({**fields, "currency": "INR"}, kl, dt.date(2026, 11, 6))
    assert not ok and "country that uses RM" in why
    assert currency_of("SOME SHOP\nTOTAL 10.00") == "INR"   # nothing to go on: the company default


def test_receipts_run_on_a_folder(tmp_path, monkeypatch):
    receipts.configure(approval_limit="5000", currency="INR")
    try:
        files = find_files([str(EXAMPLES)])
        rows = run_files(files, None, today=TODAY)
        by = {Path(r["name"]).name: r for r in rows}
        assert by["laptop-bengaluru.txt"]["decision"]["decision"] == "needs_approval"
        assert by["restaurant-pune.txt"]["entry"]["amount"] == "430.00"
        assert by["cafe-smudged.txt"]["status"] == "halted"  # an unreadable date goes to a person
        s = summary(rows)
        assert s["receipts"] == len(files) and s["halted"] == 1 and "INR" in s["total_posted"]
        from cascade_guard.receipts_run import main
        monkeypatch.chdir(tmp_path)
        assert main([str(EXAMPLES / "kirana-kothrud.txt"), "--out", "out.csv", "--json", "out.json"]) == 0
        assert "497.00" in (tmp_path / "out.csv").read_text(encoding="utf-8")
        assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))["summary"]["posted"] == 1
    finally:
        receipts.configure(approval_limit="200", currency="RM")


def test_cli_ai_flag_reaches_the_commands(local_server, tmp_path, monkeypatch, capsys, rupees):
    from cascade_guard.cli import main
    monkeypatch.delenv("CASCADE_AI", raising=False)
    monkeypatch.chdir(tmp_path)
    assert main(["receipts", "run", str(EXAMPLES / "kirana-kothrud.txt"), "--ai",
                 f"{local_server}#tiny-receipt-model", "--currency", "INR"]) == 0
    out = capsys.readouterr().out
    assert "tiny-receipt-model" in out and "FIXED" in out and "cash paid" in out
    assert main(["ai", "test", "--ai", f"{local_server}#tiny-receipt-model"]) == 0
    assert "It works" in capsys.readouterr().out
    assert main(["ai", "list"]) == 0
    assert "ollama" in capsys.readouterr().out
    assert main(["receipts", "run", "--samples", "--out", "s.csv"]) == 0
    assert "restaurant-pune.txt" in capsys.readouterr().out
