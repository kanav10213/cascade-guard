"""Expense receipts: the agents, the guard's checks against the receipt text, and the benchmark."""
import datetime as dt
import json

from cascade_guard.llm.client import FakeLLMClient
from cascade_guard.receipts import (
    Batch,
    check_extract,
    check_ledger,
    check_policy,
    decide,
    parse_date,
    process_receipt,
    rule_extract,
    score,
)
from cascade_guard.receipts_bench import run_benchmark, summarize, to_markdown

TODAY = dt.date(2026, 10, 30)
# The layouts real OCR produces: labels in one column, then the values in another.
SPLIT_COLUMNS = """KEDAI BUKU MAJU SDN BHD
NO. 12, JALAN MERDEKA, 81100 JOHOR BAHRU
DATE:
25/12/2018 8:13:39 PM
WATER COLOUR SET
1 PC
9.000
9.00
SUB TOTAL
ROUNDING ADJ
TOTAL
CASH
CHANGE
9.00
0.00
9.00
10.00
1.00
THANK YOU"""
SAME_LINE = """KOPI HOUSE RESTAURANT
JALAN AMPANG, KUALA LUMPUR
TAX INVOICE  DATE: 03/01/2018
NASI LEMAK 2 X 6.50 13.00
TEH TARIK 2 X 2.50 5.00
SUB-TOTAL (EXCL GST) 18.00
GST 6% 1.08
TOTAL (INCL GST) 19.08
ROUNDING ADJ -0.03
ROUNDED TOTAL 19.05
CASH 20.00
CHANGE 0.95"""


def test_a_correct_extraction_passes():
    ok, why = check_extract({"vendor": "KEDAI BUKU MAJU SDN BHD", "date": "25/12/2018", "total": "9.00"},
                            SPLIT_COLUMNS, TODAY)
    assert ok, why


def test_the_guard_names_the_usual_mistakes():
    def why(total, text=SAME_LINE, date="03/01/2018", vendor="KOPI HOUSE RESTAURANT"):
        return check_extract({"vendor": vendor, "date": date, "total": total}, text, TODAY)[1] or ""
    assert check_extract({"vendor": "KOPI HOUSE RESTAURANT", "date": "03/01/2018", "total": "19.05"}, SAME_LINE,
                         TODAY)[0]
    assert "cash paid" in why("20.00")                              # the cash handed over
    assert "subtotal" in why("18.00")                               # before tax
    assert "19.05" in why("19.08")                                  # the unrounded total
    assert "does not appear" in why("19.50")                        # not on the receipt at all
    assert "not on the receipt" in why("19.05", date="04/01/2018")
    assert "in the future" in why("19.05", text=SAME_LINE.replace("03/01/2018", "03/01/2099"), date="03/01/2099")
    assert "vendor" in why("19.05", vendor="SOMEWHERE ELSE ENTIRELY LTD")
    assert "change" in why("1.00", text=SPLIT_COLUMNS, date="25/12/2018", vendor="KEDAI BUKU MAJU SDN BHD")


def test_rule_agent_and_its_retry():
    first = rule_extract(SAME_LINE)
    assert first["total"] == "19.05" and first["date"] == "03/01/2018" and "KOPI" in first["vendor"]
    again = rule_extract(SAME_LINE, [{"total": "19.05"}])           # told 19.05 was wrong: tries another
    assert again["total"] != "19.05"


def test_policy_and_ledger_checks():
    f = {"vendor": "KOPI HOUSE RESTAURANT", "date": "03/01/2018", "total": "250.00", "currency": "RM"}
    assert decide(f)["decision"] == "needs_approval"
    assert not check_policy({"decision": "approve"}, f, set())[0]
    good = {"vendor": f["vendor"], "date": "2018-01-03", "amount": "250.00", "currency": "RM", "category": "meals",
            "memo": "Lunch"}
    assert check_ledger(good, f)[0]
    assert "YYYY-MM-DD" in check_ledger({**good, "date": "2018-03-01"}, f)[1]   # month and day swapped
    assert not check_ledger({**good, "amount": "25.00"}, f)[0]
    assert not check_ledger({**good, "category": "party"}, f)[0]
    b = Batch()
    b.remember(f)
    assert decide(f, b.seen)["decision"] == "duplicate"


def test_dates():
    assert parse_date("25/12/2018") == dt.date(2018, 12, 25)
    assert parse_date("12/28/2017") == dt.date(2017, 12, 28)          # month first when day first is impossible
    assert parse_date("20180304") == dt.date(2018, 3, 4)
    assert parse_date("14/MAY/2017") == dt.date(2017, 5, 14)
    assert parse_date("21 MARCH, 2018") == dt.date(2018, 3, 21)
    assert parse_date("no date") is None


def test_rule_workflow_posts_a_ledger_entry():
    r = process_receipt(SAME_LINE, today=TODAY)
    assert r["status"] == "posted" and r["entry"]["amount"] == "19.05" and r["entry"]["date"] == "2018-01-03"
    assert r["entry"]["category"] == "meals" and r["decision"]["decision"] == "approve"


class Model(FakeLLMClient):
    """A stand-in LLM agent that makes a real-world mistake first (the cash paid as the total, a US-style date)
    and corrects it when told why."""
    is_fake = False

    def __init__(self):
        super().__init__(self.reply, cache_path=None)

    def reply(self, system, user, sample):
        told = "REJECTED" in user
        if system.startswith("You read shop receipts"):
            return json.dumps({"vendor": "KOPI HOUSE RESTAURANT", "date": "03/01/2018",
                               "total": "19.05" if told else "20.00", "currency": "RM"})
        if system.startswith("You apply"):
            return json.dumps({"decision": "approve", "reason": "small"})
        return json.dumps({"vendor": "KOPI HOUSE RESTAURANT", "date": "2018-01-03" if told else "2018-03-01",
                           "amount": "19.05", "currency": "RM", "category": "meals", "memo": "Team lunch"})


def test_llm_agents_are_caught_and_fixed():
    plain = process_receipt(SAME_LINE, client=Model(), guard=False, today=TODAY)
    assert plain["status"] == "posted" and plain["entry"]["date"] == "2018-03-01"   # wrong, posted silently
    guarded = process_receipt(SAME_LINE, client=Model(), today=TODAY)
    assert guarded["status"] == "posted" and guarded["entry"]["amount"] == "19.05"
    assert guarded["entry"]["date"] == "2018-01-03" and guarded["caught"] == 2
    truth = {"total": "19.05", "date": "03/01/2018", "company": "KOPI HOUSE RESTAURANT"}
    assert score(plain, truth)["outcome"] == "posted_wrong" and score(guarded, truth)["outcome"] == "posted_ok"
    assert score(guarded, truth, "approve")["outcome"] == "posted_ok"
    assert score(guarded, truth, "needs_approval")["outcome"] == "posted_wrong"     # the policy decision counts too


def test_benchmark_report():
    receipts = [{"id": "1", "text": SAME_LINE, "truth": {"total": "19.05", "date": "03/01/2018", "company": "KOPI"}},
                {"id": "2", "text": SPLIT_COLUMNS, "truth": {"total": "9.00", "date": "25/12/2018", "company": "K"}},
                {"id": "3", "text": "nothing", "truth": {"total": ""}}]
    s = summarize(run_benchmark(receipts, Model()))
    assert s["receipts"] == 2 and s["skipped"] == 1
    assert s["without"]["outcomes"]["posted_wrong"] >= 1 and s["with"]["outcomes"]["posted_wrong"] == 0
    md = to_markdown(s)
    assert "Silent error rate" in md and "Without the guard" in md


def test_receipts_api(monkeypatch, tmp_path):
    import base64

    from fastapi.testclient import TestClient

    from cascade_guard.api import app
    from cascade_guard.pilot.pdf import text_to_pdf
    from cascade_guard.storage.db import Store, reset_store
    monkeypatch.setenv("API_KEY", "adm")
    monkeypatch.setenv("VIEWER_KEY", "view")
    store = Store(str(tmp_path / "r.db"))
    reset_store(store)
    try:
        c = TestClient(app, headers={"X-API-Key": "view"})
        assert c.get("/receipts").status_code == 200 and "Expense desk" in c.get("/receipts").text
        info = c.get("/receipts/info").json()
        assert info["approval_limit"] == "200.00" and info["model_available"] is False
        smudged = "SEDAP CAFE SDN BHD\nDATE: 3I/O1/2O18\nTOTAL 12.00\nCASH 20.00\nCHANGE 8.00"
        pdf = base64.b64encode(text_to_pdf(SAME_LINE)).decode()
        r = c.post("/receipts/process", json={"receipts": [
            {"name": "a.txt", "text": SAME_LINE}, {"name": "b.pdf", "pdf_base64": pdf},
            {"name": "c.txt", "text": smudged}, {"name": "d.jpg", "image_base64": base64.b64encode(b"nope").decode()},
            {"name": "e.txt", "text": ""}]}).json()
        status = {x["name"]: x["status"] for x in r["results"]}
        assert status == {"a.txt": "posted", "b.pdf": "posted", "c.txt": "halted", "d.jpg": "unreadable",
                          "e.txt": "unreadable"}
        b = next(x for x in r["results"] if x["name"] == "b.pdf")
        assert b["decision"]["decision"] == "duplicate"                  # the same receipt twice in one batch
        assert r["summary"]["posted"] == 2 and r["summary"]["halted"] == 1
        assert r["csv"].splitlines()[0].startswith("file,status,vendor,date,amount")
        assert "a.txt,posted,KOPI HOUSE RESTAURANT,2018-01-03,19.05" in r["csv"]
        halted = next(x for x in r["results"] if x["name"] == "c.txt")
        assert store.reviews("open", 10)[0]["run_id"] == halted["run_id"]   # waiting for a person
        assert c.post("/receipts/process", json={"receipts": [{"name": "x", "text": "y"}], "use_model": True}
                      ).status_code == 409
        assert c.post("/receipts/process", json={"receipts": []}).status_code == 422
    finally:
        reset_store(None)
