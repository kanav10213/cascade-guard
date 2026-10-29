"""The bill checker: free-form bills and invoices, recomputed exactly, mistakes explained."""
import time

import pytest

from cascade_guard.bills import check_bill, fmt, looks_like_bill, segments
from cascade_guard.pilot.invoice import make_invoice, parse_invoice, price_items


def test_the_example_from_the_claude_app_instructions():
    r = check_bill("Use the cascade guard tool to check this invoice: 3 x $4.00, 2 x $1.50, total $16.00")
    assert r["status"] == "wrong"
    assert r["total"] == 15.0 and r["stated"]["total"] == 16.0 and r["difference"] == 1.0
    assert [(x["qty"], x["unit_price"], x["line_total"]) for x in r["lines"]] == [(3, 4.0, 12.0), (2, 1.5, 3.0)]
    assert "should be $15.00" in r["summary"] and "$16.00" in r["summary"] and "too much" in r["summary"]
    assert [c["ok"] for c in r["checks"]] == [True, True, False]


@pytest.mark.parametrize("text,status,total", [
    ("3 x $4.00, 2 x $1.50, total $15.00", "correct", 15.0),
    ("3 x $4.00, 2 x $1.50", "no_total", 15.0),
    ("3 x $4.00, 2 x $1.50 = $16.00", "wrong", 15.0),            # the last "= X" is the bill's total
    ("3 x $4.00 + 2 x $1.50 = $16.00", "wrong", 15.0),
    ("check my bill 3 x 4.00, 2 x 1.50 total 16", "wrong", 15.0),  # no currency, no comma before total
    ("3 x $5 Tax 8% Total $16.20", "correct", 16.2),
    ("Subtotal: $15.00, Tax (8%): $1.20, Total: $16.20, 3 x $4.00, 2 x $1.50", "correct", 16.2),
    ("2 pens at ₹15 each, Notebook 3 x ₹40, GST 18%, Total ₹177", "correct", 177.0),
    ("Rice: 2 kg x ₹60 = ₹120\nDal 1 kg x ₹110\nCGST 2.5% SGST 2.5%\nGrand total: ₹241.50", "correct", 241.5),
    ("3 x Shirt @ $20, Discount 10%, Tax 8%, Total $58.32", "correct", 58.32),
    ("1 x Laptop @ ₹1,23,456.00, Total ₹1,23,456", "correct", 123456.0),
    ("1 x Laptop @ $1,299.00\nShipping: $25\nTotal: $1,324.00", "correct", 1324.0),
    ("Coffee $3.50, Muffin $2.25, total $5.75", "correct", 5.75),
    ("2 tickets for $30, popcorn $5, total $35", "correct", 35.0),
    ("Hammer - ₹250, Nails 2 x ₹30, Round off -0.40, Total ₹309.60", "correct", 309.6),
    ("3 pens at $2 and 2 books at $5, total $16", "correct", 16.0),
    ("Fish and chips @ $5, 2 x Coke @ $2, total $9", "correct", 9.0),
    ("4,50 € x 2, total 9,00 €", "correct", 9.0),
    ("Total items: 5, 5 x $2, total $10", "correct", 10.0),
    ("Cash: $20, 2 x $5, Change: $10, total $10", "correct", 10.0),
    ("Return: -$5, 2 x $10, total $15", "correct", 15.0),
    ("Hammer x 3 @ $14.99, 3 hammers $14.99 each, 3 boxes x $5, $4.00 x 3, 3 @ 4.00", "no_total", 128.94),
])
def test_bill_formats(text, status, total):
    r = check_bill(text)
    assert (r["status"], r["total"]) == (status, total), r["summary"]


def test_pilot_invoice_layout_agrees_with_the_pilot_maths():
    for seed in range(40):
        inv = make_invoice(seed)
        expected = price_items(parse_invoice(inv["text"]))["total"]
        r = check_bill(inv["text"])
        assert r["status"] == "no_total" and r["total"] == pytest.approx(expected), inv["text"]
        assert r["invoice_id"] == inv["id"]
        right = check_bill(inv["text"] + f"Total: ${expected:.2f}\n")
        assert right["status"] == "correct", right["summary"]
        off = check_bill(inv["text"] + f"Total: ${expected + 1:.2f}\n")
        assert off["status"] == "wrong" and off["difference"] == 1.0


def test_wrong_line_total_is_named():
    r = check_bill("3 x $4.00 = $12.00\n2 x $1.50 = $4.00\nTotal: $16.00")
    assert r["status"] == "wrong"
    assert any(p.startswith("Line 2") and "$3.00" in p and "$4.00" in p for p in r["problems"])
    assert r["lines"][1]["ok"] is False and r["lines"][0]["ok"] is True


def test_right_total_with_a_wrong_line_is_still_flagged():
    r = check_bill("3 x $4.00 = $13.00, 2 x $1.50 = $3.00, total $15.00")
    assert r["status"] == "wrong" and "is right" in r["summary"] and "Line 1" in r["summary"]


def test_wrong_tax_amount_is_named():
    r = check_bill("3 x $4.00, 2 x $1.50, Tax (8%): $1.30, Total: $16.30")
    assert r["status"] == "wrong"
    assert any("Tax at 8% of $15.00 is $1.20" in p for p in r["problems"])


@pytest.mark.parametrize("text,hint", [
    ("3 x $4.00, 2 x $1.50, Discount 10%, total $15.00", "discount was not taken off"),
    ("3 x $4.00, 2 x $1.50, Tax 10%, total $15.00", "leaves out the tax"),
    ("2 x $14.99, total $299.80", "decimal point"),
    ("3 x $4.00, 2 x $1.50, total $18.00", "counted twice"),
    ("5 x $10, total $60", "one too many"),
    ("2 x $10, Delivery $5, total $20", "charges were not added"),
])
def test_likely_cause_of_a_wrong_total(text, hint):
    r = check_bill(text)
    assert r["status"] == "wrong" and any(hint in h for h in r["hints"]), r["hints"]


def test_other_common_conventions_are_not_called_wrong():
    # Indian restaurant bill: GST also on the service charge
    r = check_bill("Pizza ₹450, Coke 2 x ₹60, Service charge 10%, GST 5%, Total ₹658.35")
    assert r["status"] == "correct" and r["total"] == 658.35
    assert r["assumptions"] == ["Here tax is also charged on the extra charges."]
    # discount taken off after tax
    r = check_bill("1 x Lamp @ $100, Discount $10, Tax 10%, Total $100")
    assert r["status"] == "correct" and "after tax" in r["summary"]


def test_tax_included_in_prices():
    for text in ("Total (incl. 18% GST): ₹1,180, 1 x Chair @ ₹1,180",
                 "1 x Chair @ ₹1,180 (incl. GST 18%), Total ₹1,180"):
        r = check_bill(text)
        assert r["status"] == "correct", (text, r["summary"])
        assert r["taxes"][0]["included"] and r["taxes"][0]["amount"] == 180.0


def test_unreadable_lines_are_reported_not_dropped():
    r = check_bill("3 x $4.00, $5 ?? 7, total $12")
    assert r["unread"] == ["$5 ?? 7"] and r["uncertain"] is True
    assert any("couldn't read 1 line" in h for h in r["hints"])
    assert r["summary"].startswith("The items I could read add up") and "'$5 ?? 7'" in r["summary"]


def test_not_a_bill():
    for text in ("hello", "Is this right? Total: $16", ""):
        r = check_bill(text)
        assert r["status"] == "unreadable" and "couldn't find any items" in r["summary"]


def test_looks_like_bill():
    assert looks_like_bill("check this invoice: 3 x $4.00, 2 x $1.50, total $16.00")
    assert looks_like_bill("Coffee $3.50, Muffin $2.25")
    assert not looks_like_bill("Coffee $3.50")
    assert not looks_like_bill("what can you do?")
    assert not looks_like_bill("is 17 x 23 = 391?")


def test_money_formatting():
    assert fmt(15) == "15.00" and fmt(1234.5, "$") == "$1,234.50" and fmt(-0.4, "₹") == "-₹0.40"
    assert fmt(123456, "₹") == "₹1,23,456.00" and fmt(12345678.9, "₹") == "₹1,23,45,678.90"
    assert fmt(2.675, "$") == "$2.68"  # exact decimal rounding, half up


def test_splitting_keeps_thousands_and_decimal_commas():
    assert segments("1 x A @ $1,299.00, 2 x B @ ₹1,23,456") == ["1 x A @ $1,299.00", "2 x B @ ₹1,23,456"]
    assert segments("2 x 4,50 €, total 9,00 €") == ["2 x 4.50 €", "total 9.00 €"]


def test_hostile_input_is_fast_and_bounded():
    start = time.perf_counter()
    check_bill(("3 x " * 5000) + "$4")
    check_bill("a" * 50_000 + " @ $5")
    check_bill(", ".join(["2 x $1.00"] * 2000) + ", total $1")
    check_bill("9" * 5000 + " x " + "9" * 5000)
    assert time.perf_counter() - start < 3
    r = check_bill(", ".join(["2 x $1.00"] * 2000))
    assert len(r["lines"]) <= 300
