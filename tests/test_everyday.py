"""Everyday calculators: exact answers for money, dates, units, travel, studies and recipes, read from plain English."""
import datetime as dt

import pytest

from cascade_guard import everyday
from cascade_guard.everyday import CALCULATORS, calculate, check_everyday, looks_like_everyday, understand_everyday


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(everyday, "today", lambda: dt.date(2026, 10, 29))


# (what a person types, the calculator it should pick, the status, text the summary must contain)
CASES = [
    # shopping and money
    ("I bought a shirt for ₹1,500 at 20% off, they charged ₹1,250", "discount", "wrong", "₹1,200.00, not ₹1,250.00"),
    ("20% off ₹1,500 is ₹1,250?", "discount", "wrong", "₹1,200.00"),
    ("is 20% off 1,500 1,200?", "discount", "correct", "1,200.00"),
    ("what is 20% off 1500", "discount", "computed", "1,200.00"),
    ("20% + 10% off on 2000", "discount", "computed", "1,440.00"),
    ("add 18% GST to 2,500", "add_tax", "computed", "2,950.00"),
    ("is ₹2,000 + 18% GST ₹2,360?", "add_tax", "correct", "₹2,360.00"),
    ("₹1,180 including 18% GST, what is the base price?", "remove_tax", "computed", "₹1,000.00"),
    ("50% of 3 lakh", "percent_of", "computed", "₹1,50,000.00"),
    ("is 15% of 240 36?", "percent_of", "correct", "36"),
    ("is 15% of 240 = 40?", "percent_of", "wrong", "36, not 40"),
    ("18% of 5000 is 900", "percent_of", "correct", "900"),
    ("price went from 80 to 100, is that 25% increase?", "percent_change", "correct", "25%"),
    ("increase 40,000 by 10%", "change_by", "computed", "44,000"),
    ("I scored 432 out of 500, what percent?", "what_percent", "computed", "86.4%"),
    ("is 432 out of 500 86.4%?", "what_percent", "correct", "86.4%"),
    # eating out and sharing
    ("15% tip on $80 split 4 ways", "tip", "computed", "$23.00 each"),
    ("Is a 15% tip on $80 split 4 ways $23 each?", "tip", "correct", "$23.00 each"),
    ("split ₹2,400 among 3 people", "split_equally", "computed", "₹800.00"),
    ("split the 1,800 dinner bill with 2 friends", "split_equally", "computed", "3 ways: 600.00"),
    ("Asha paid 600, Ravi paid 300 and I paid 0 for dinner", "settle_up", "computed", "You pay Asha 300.00"),
    # loans and savings
    ("EMI for 10 lakh at 10% for 3 years", "loan_emi", "computed", "₹32,267.19"),
    ("is the emi on 5 lakh at 9% for 5 years 10,379?", "loan_emi", "correct", "₹10,379.18"),
    ("is ₹45,000 at 6% simple interest for 2 years 5,400?", "simple_interest", "correct", "on ₹45,000.00"),
    ("₹50,000 at 7% compound interest for 3 years", "compound_interest", "computed", "₹11,252.15"),
    ("FD of 1 lakh at 7% for 5 years", "compound_interest", "computed", "₹1,41,477.82"),
    ("SIP of 5000 per month at 12% for 10 years", "sip", "computed", "₹11,61,695.38"),
    # dates and time
    ("days between 2026-01-01 and 2026-12-25", "days_between", "computed", "358 days"),
    ("what day is 15 August 2027", "weekday", "computed", "Sunday"),
    ("add 90 days to 1 March 2026", "add_days", "computed", "Saturday 30 May 2026"),
    ("subtract 2 weeks from 10 Jan 2027", "add_days", "computed", "Sunday 27 December 2026"),
    ("30 days after 1 Feb 2026 is 3 March 2026?", "add_days", "correct", "Tuesday 3 March 2026"),
    ("I was born on 12 May 2000, how old am I on 29 October 2026?", "age", "computed", "26 years"),
    ("from 9:30 am to 6:15 pm", "time_between", "computed", "8 h 45 min"),
    ("3 pm IST in New York", "convert_time_zone", "computed", "5:30 am in New York"),
    # units and travel
    ("5 km in miles", "convert_units", "computed", "3.10686 miles"),
    ("1 GB in MB", "convert_units", "computed", "1,000 MB"),
    ("100 F in C", "convert_units", "computed", "37.7778 °C"),
    ("3 cups to ml", "convert_units", "computed", "709.765 ml"),
    ("450 km at 60 km/h", "trip", "computed", "7 h 30 min"),
    ("fuel cost for 450 km at 15 km/l and ₹105 per litre", "fuel_cost", "computed", "₹3,150.00"),
    # casual phrasings
    ("how much is 18% gst on 850", "add_tax", "computed", "₹153.00"),
    ("mobile cost 15999 with 10% discount what will i pay", "discount", "computed", "14,399.10"),
    ("my salary went from 40000 to 46000 what is the hike percentage", "percent_change", "computed", "15%"),
    ("dinner was 3000 including 5% gst what was the base", "remove_tax", "computed", "₹2,857.14"),
    ("what is 37 degree celsius in fahrenheit", "convert_units", "computed", "98.6 °F"),
    ("what is 1 kg in pounds", "convert_units", "computed", "2.20462 pounds"),
    ("5 feet 8 inches in cm", "convert_units", "computed", "172.72 cm"),
    ("my exam is on 20 november 2026, how many days left", "days_between", "computed", "22 days"),
    ("a 20000 laptop on emi 12 months at 15%", "loan_emi", "computed", "for 1 year: ₹1,805.17"),
    ("10 am london time in india", "convert_time_zone", "computed", "3:30 pm in India"),
    # studies and cooking
    ("SGPA with credits 4, 3, 3 and grades 9, 8, 10", "gpa", "computed", ": 9"),
    ("recipe for 4 needs 200 g flour, make it for 6", "scale_recipe", "computed", "300 g flour"),
    ("scale this recipe from 4 to 6: 200 g flour, 2 eggs, 1/2 cup milk", "scale_recipe", "computed", "¾ cup milk"),
    ("serves 2: 1 cup rice. make it for 5", "scale_recipe", "computed", "2½ cups rice"),
]


@pytest.mark.parametrize("text,calc,status,expect", CASES, ids=[c[0][:40] for c in CASES])
def test_reads_everyday_requests(text, calc, status, expect):
    r = check_everyday(text)
    assert r["calculator"] == calc, r
    assert r["status"] == status, r["summary"]
    assert expect in r["summary"], r["summary"]
    assert looks_like_everyday(text)
    assert r["lines"], "the working is always shown"


@pytest.mark.parametrize("text", ["what is the weather", "write me a poem about rain", "hello", ""])
def test_says_so_when_it_is_not_a_calculation(text):
    r = check_everyday(text)
    assert r["status"] == "unreadable" and r["calculator"] is None
    assert understand_everyday(text) is None


def test_every_calculator_is_registered_with_a_description():
    assert len(CALCULATORS) >= 25
    for name, (fn, doc) in CALCULATORS.items():
        assert callable(fn) and doc and len(doc) < 300, name


def test_calculate_by_name_with_exact_money():
    r = calculate("loan_emi", {"principal": "500000", "annual_rate": "9", "months": 60})
    assert r["status"] == "computed" and r["answer"] == "₹10,379.18"
    r = calculate("discount", {"price": "1500", "rates": ["20"], "claimed": "1250", "cur": "₹"})
    assert r["status"] == "wrong" and float(r["value"]) == 1200


def test_calculate_rejects_bad_input_without_crashing():
    assert calculate("no_such_thing", {})["status"] == "unreadable"
    r = calculate("loan_emi", {"principal": "lots", "annual_rate": "9", "months": 60})
    assert r["status"] == "unreadable"
    r = calculate("split_equally", {"total": "100", "people": 0})
    assert r["status"] == "unreadable"


def test_split_shares_always_add_up_to_the_total():
    r = calculate("split_equally", {"total": "100", "people": 3, "cur": "₹"})
    assert "₹33.34" in r["answer"] and "₹33.33" in r["answer"]
