"""Exact checks for everyday life: money, dates, units, travel, studies and cooking.

Every calculator takes plain arguments, works the answer out exactly (decimal or rational arithmetic, never
floating point), shows the working line by line, and, when the person says what they think the answer is,
says whether that is right:

    percent / discount / GST / tip     split a bill / who owes whom     EMI, loans, simple and compound interest,
    FD, SIP     days between dates, adding days, weekdays, ages, time between, time zones     unit conversions
    travel time, speed, distance and fuel cost     SGPA / CGPA and marks     scaling a recipe

    understand_everyday("EMI for 5 lakh at 9% for 5 years")  -> ("loan_emi", {...})
    check_everyday("20% off ₹1,500 is ₹1,250?")               -> status "wrong", answer "₹1,200.00"

The result of every calculator has the same shape, so a page or a model can show any of them:
    {kind, status (correct | wrong | computed | unreadable), title, answer, value, claimed, lines, summary, notes}
"""
import datetime as dt
import re
from collections.abc import Callable
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction

MAX_TEXT = 4000
D0 = Decimal(0)
CENT = Decimal("0.01")

# --------------------------------------------------------------------------- numbers and money in text

CUR_RX = r"(?:\$|₹|€|£|\brs\.?(?=\s|\d)|\binr\b|\busd\b|\beur\b|\bgbp\b|\brupees?\b|\bdollars?\b|\beuros?\b|\bpounds?\b)"
NUM_RX = r"(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3}|\d+)(?:\.\d+)?|\.\d+"
SCALE_RX = r"(?:k|thousands?|lakhs?|lacs?|crores?|cr|millions?|mn|billions?|bn)\b"
SCALES = {"k": 1000, "thousand": 1000, "thousands": 1000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
          "lacs": 100_000, "crore": 10**7, "crores": 10**7, "cr": 10**7, "million": 10**6, "millions": 10**6,
          "mn": 10**6, "billion": 10**9, "billions": 10**9, "bn": 10**9}
CURRENCY = {"$": "$", "usd": "$", "dollar": "$", "dollars": "$", "₹": "₹", "rs": "₹", "rs.": "₹", "inr": "₹",
            "rupee": "₹", "rupees": "₹", "€": "€", "eur": "€", "euro": "€", "euros": "€", "£": "£", "gbp": "£",
            "pound": "£", "pounds": "£"}
AMOUNT = re.compile(rf"(?<![\w.])(?P<c1>{CUR_RX})?\s*(?P<num>{NUM_RX})(?![\d])\s*(?P<scale>{SCALE_RX})?"
                    rf"(?:\s*(?P<c2>{CUR_RX}))?(?:\s*/-)?", re.I)
PERCENT = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?:%|percent\b|per\s*cent\b|pc\b)", re.I)
CLAIM_WORDS = (r"(?:is|=|equals?|comes?\s+to|will\s+be|would\s+be|should\s+be|i\s+got|they\s+(?:said|say|charged|"
               r"quoted)|was|are|be|making|makes|gives|=>|->|→|of)")


def _dec(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def _cur(token) -> str | None:
    if not token:
        return None
    t = token.strip().lower()
    return CURRENCY.get(t) or CURRENCY.get(t.rstrip("."))


def find_amounts(text: str) -> list[dict]:
    """Every number in the text with its scale (lakh, crore, k) and currency, percentages marked as such."""
    out = []
    for m in AMOUNT.finditer(text):
        value = _dec(m.group("num"))
        if m.group("scale"):
            value *= SCALES[m.group("scale").lower()]
        after = text[m.end():m.end() + 12].lower()
        out.append({"value": value, "currency": _cur(m.group("c1")) or _cur(m.group("c2")),
                    "percent": bool(re.match(r"\s*(?:%|percent\b|per\s*cent\b)", after)),
                    "start": m.start(), "end": m.end(), "text": m.group(0).strip()})
    return out


def currency_of(text: str) -> str:
    found = [a["currency"] for a in find_amounts(text) if a["currency"]]
    if found:
        return found[0]
    return "₹" if re.search(r"\b(?:lakhs?|lacs?|crores?|gst|cgst|sgst|rupees?)\b", text, re.I) else ""


def money(x, cur: str = "") -> str:
    from cascade_guard.bills import fmt
    return fmt(x, cur)


def number(x, places: int = 4) -> str:
    """1,234.5 · 0.3333 · 12 (no trailing zeros)."""
    d = Decimal(str(x)) if not isinstance(x, Decimal) else x
    q = d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    s = f"{q:,f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def pct(x) -> str:
    return number(x, 2) + "%"


def _cents(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def _places(text) -> int:
    t = str(text).replace(",", "")
    return len(t.split(".", 1)[1]) if "." in t else 0


def judge(value: Decimal, claimed, *, is_money: bool = False, tolerance: Decimal | None = None):
    """(ok, note): exact, or right to the claim's own precision. Whole-number money claims may round either way
    (banks often round an EMI up)."""
    if claimed is None:
        return None, None
    c = claimed if isinstance(claimed, Decimal) else _dec(str(claimed))
    if tolerance is not None and abs(value - c) <= tolerance:
        return True, None
    places = _places(claimed)
    exact = value.quantize(Decimal(1).scaleb(-max(places, 2 if is_money else 6)), rounding=ROUND_HALF_UP)
    if c in (value, exact) or (is_money and c == _cents(value)):
        return True, None
    rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if c == rounded:
        return True, ("correct to the nearest whole number" if places == 0 else
                      f"correct to {places} decimal place{'s' if places != 1 else ''}")
    if is_money and places == 0 and c == value.quantize(Decimal(1), rounding=ROUND_CEILING):
        return True, "correct when rounded up to a whole amount, as banks often do"
    return False, None


def _result(kind, title, value, answer, lines, claimed=None, claimed_text=None, *, is_money=False, notes=None,
            tolerance=None, extra=None) -> dict:
    ok, note = judge(value, claimed, is_money=is_money, tolerance=tolerance) if value is not None else (None, None)
    if claimed is None or ok is None:
        status, summary = "computed", f"{title}: {answer}."
    elif ok:
        status, summary = "correct", f"Correct: {title} is {answer}" + (f" ({note})." if note else ".")
    else:
        status, summary = "wrong", f"Wrong: {title} is {answer}, not {claimed_text}."
    out = {"kind": kind, "status": status, "title": title, "answer": answer,
           "value": float(value) if value is not None else None, "claimed": claimed_text, "lines": lines,
           "summary": summary, "notes": list(notes or [])}
    if extra:
        out.update(extra)
    return out


def unreadable(kind: str, hint: str) -> dict:
    return {"kind": kind, "status": "unreadable", "title": "", "answer": "", "value": None, "claimed": None,
            "lines": [], "summary": hint, "notes": [], "calculator": None}


def L(label, value, *, total=False, wrong=None) -> dict:
    """One line of working: label ... value. total marks the answer line; wrong is the claimed value it replaces."""
    line = {"label": label, "value": value}
    if total:
        line["total"] = True
    if wrong:
        line["wrong"] = wrong
    return line


def _with_claim(res: dict) -> dict:
    """Put the claimed value next to the answer line when it is wrong (the red-pen correction)."""
    if res["status"] == "wrong":
        for line in res["lines"]:
            if line.get("total"):
                line["wrong"] = res["claimed"]
    return res


# =========================================================================== money

def percent_of(rate, base, claimed=None, cur: str = "") -> dict:
    rate, base = Decimal(str(rate)), Decimal(str(base))
    v = base * rate / 100
    is_money = bool(cur)
    ans = money(v, cur) if is_money else number(v)
    title = f"{pct(rate)} of {money(base, cur) if is_money else number(base)}"
    return _with_claim(_result("percent", title, v, ans, [L("Amount", money(base, cur) if is_money else number(base)),
                                                           L(f"× {pct(rate)}", f"÷ 100 × {number(rate)}"),
                                                           L("Answer", ans, total=True)],
                               claimed, _show(claimed, cur, is_money), is_money=is_money))


def discount(price, rates=None, amount_off=None, claimed=None, cur: str = "", claim_is="final") -> dict:
    """One or more percentage discounts applied one after another (20% + 10% is not 30%), or a fixed amount off."""
    price = Decimal(str(price))
    if not rates and amount_off is None:
        return unreadable("discount", "Say the discount, e.g. '20% off ₹1,500'.")
    lines, now = [L("Price", money(price, cur))], price
    rates = [Decimal(str(r)) for r in (rates or [])]
    for r in rates:
        cut = _cents(now * r / 100)
        lines.append(L(f"{pct(r)} off {money(now, cur)}" if len(rates) > 1 else f"{pct(r)} off", "-" + money(cut, cur)))
        now -= cut
    if amount_off is not None:
        cut = Decimal(str(amount_off))
        lines.append(L("Amount off", "-" + money(cut, cur)))
        now -= cut
    saved = price - now
    lines.append(L("You pay", money(now, cur), total=claim_is == "final"))
    lines.append(L("You save", money(saved, cur) + (f" ({pct(saved / price * 100)})" if price else ""),
                   total=claim_is == "saving"))
    label = " + ".join(pct(r) for r in rates) or money(Decimal(str(amount_off or 0)), cur)
    notes = []
    if len(rates) > 1:
        notes.append(f"Discounts are applied one after another, so {label} off is {pct(saved / price * 100)} in "
                     f"total, not {pct(sum(rates))}.")
    value = now if claim_is == "final" else saved
    title = f"{label} off {money(price, cur)}" if claim_is == "final" else f"the saving on {money(price, cur)}"
    return _with_claim(_result("discount", title, value, money(value, cur), lines, claimed,
                               _show(claimed, cur, True), is_money=True, notes=notes))


def add_tax(base, rate, claimed=None, cur: str = "", tax_name: str = "tax", claim_is="total") -> dict:
    base, rate = Decimal(str(base)), Decimal(str(rate))
    tax = _cents(base * rate / 100)
    total = base + tax
    name = tax_name.upper() if tax_name.lower() in ("gst", "vat", "cgst", "sgst", "igst") else tax_name
    lines = [L("Price before " + name, money(base, cur)),
             L(f"{name} at {pct(rate)}", money(tax, cur), total=claim_is == "tax"),
             L("Total", money(total, cur), total=claim_is == "total")]
    notes = []
    if name == "GST" and rate in (Decimal(5), Decimal(12), Decimal(18), Decimal(28)):
        half = rate / 2
        notes.append(f"Within a state this is CGST {pct(half)} + SGST {pct(half)} ({money(_cents(tax / 2), cur)} "
                     "each); between states it is IGST at the full rate.")
    value = total if claim_is == "total" else tax
    title = (f"{money(base, cur)} + {name} {pct(rate)}" if claim_is == "total"
             else f"{name} at {pct(rate)} on {money(base, cur)}")
    return _with_claim(_result("tax", title, value, money(value, cur), lines, claimed, _show(claimed, cur, True),
                               is_money=True, notes=notes))


def remove_tax(total, rate, claimed=None, cur: str = "", tax_name: str = "tax", claim_is="base") -> dict:
    """A price that already includes tax: the price before tax and the tax inside it."""
    total, rate = Decimal(str(total)), Decimal(str(rate))
    with localcontext() as c:
        c.prec = 34
        base = _cents(total * 100 / (100 + rate))
    tax = total - base
    name = tax_name.upper() if tax_name.lower() in ("gst", "vat") else tax_name
    lines = [L("Price including " + name, money(total, cur)),
             L(f"Divide by 1 + {pct(rate)}", f"÷ {number(1 + rate / 100, 4)}"),
             L("Price before " + name, money(base, cur), total=claim_is == "base"),
             L(f"{name} inside the price", money(tax, cur), total=claim_is == "tax")]
    notes = [f"A common mistake is to take {pct(rate)} off the total: that gives "
             f"{money(_cents(total * (100 - rate) / 100), cur)}, which is wrong."]
    value = base if claim_is == "base" else tax
    title = (f"the price before {pct(rate)} {name} in {money(total, cur)}" if claim_is == "base"
             else f"the {name} inside {money(total, cur)}")
    return _with_claim(_result("tax", title, value, money(value, cur), lines, claimed, _show(claimed, cur, True),
                               is_money=True, notes=notes))


def percent_change(old, new, claimed=None, cur: str = "") -> dict:
    old, new = Decimal(str(old)), Decimal(str(new))
    if old == 0:
        return unreadable("percent_change", "A change from 0 has no percentage.")
    with localcontext() as c:
        c.prec = 34
        change = (new - old) / old * 100
    word = "increase" if change >= 0 else "decrease"
    show = (lambda v: money(v, cur)) if cur else number
    lines = [L("From", show(old)), L("To", show(new)), L("Change", show(new - old)),
             L(f"{word.capitalize()} (change ÷ from × 100)", pct(abs(change)), total=True)]
    title = f"the {word} from {show(old)} to {show(new)}"
    res = _result("percent_change", title, abs(change), pct(abs(change)), lines,
                  None if claimed is None else abs(Decimal(str(claimed))),
                  None if claimed is None else pct(abs(Decimal(str(claimed)))), tolerance=Decimal("0.005"))
    if res["status"] == "wrong" and claimed is not None:
        wrong_base = abs((new - old) / new * 100) if new else None
        if wrong_base is not None and judge(wrong_base, abs(Decimal(str(claimed))), tolerance=Decimal("0.005"))[0]:
            res["notes"].append(f"{pct(abs(Decimal(str(claimed))))} is the change divided by the new value; a "
                                "percentage change is divided by the starting value.")
    return _with_claim(res)


def what_percent(part, whole, claimed=None) -> dict:
    part, whole = Decimal(str(part)), Decimal(str(whole))
    if whole == 0:
        return unreadable("what_percent", "The total cannot be 0.")
    with localcontext() as c:
        c.prec = 34
        v = part / whole * 100
    lines = [L("Part", number(part)), L("Whole", number(whole)), L("Part ÷ whole × 100", pct(v), total=True)]
    title = f"{number(part)} out of {number(whole)}"
    return _with_claim(_result("what_percent", title, v, pct(v), lines, claimed,
                               None if claimed is None else pct(Decimal(str(claimed))), tolerance=Decimal("0.005")))


def change_by(base, rate, claimed=None, cur: str = "", up: bool = True) -> dict:
    base, rate = Decimal(str(base)), Decimal(str(rate))
    delta = base * rate / 100
    v = base + delta if up else base - delta
    show = (lambda x: money(x, cur)) if cur else number
    lines = [L("Start", show(base)), L(f"{'+' if up else '-'} {pct(rate)}", ("+" if up else "-") + show(delta)),
             L("Result", show(v), total=True)]
    title = f"{show(base)} {'increased' if up else 'decreased'} by {pct(rate)}"
    return _with_claim(_result("change_by", title, v, show(v), lines, claimed, _show(claimed, cur, bool(cur)),
                               is_money=bool(cur)))


def tip(bill, rate, people=1, claimed=None, cur: str = "") -> dict:
    bill, rate, people = Decimal(str(bill)), Decimal(str(rate)), int(people or 1)
    t = _cents(bill * rate / 100)
    total = bill + t
    lines = [L("Bill", money(bill, cur)), L(f"Tip at {pct(rate)}", money(t, cur)),
             L("Total with tip", money(total, cur), total=people == 1)]
    value = total
    if people > 1:
        shares = _equal_shares(total, people)
        value = shares[0]
        lines.append(L(f"Each of {people} pays", _shares_text(shares, cur), total=True))
    title = f"a {pct(rate)} tip on {money(bill, cur)}" + (f" split {people} ways" if people > 1 else "")
    return _with_claim(_result("tip", title, value, money(value, cur) if people == 1 else _shares_text(
        _equal_shares(total, people), cur) + f" each ({money(total, cur)} in all)", lines, claimed,
        _show(claimed, cur, True), is_money=True,
        tolerance=CENT if people > 1 else None))


def _show(claimed, cur, is_money) -> str | None:
    if claimed is None:
        return None
    return money(Decimal(str(claimed)), cur) if is_money else number(Decimal(str(claimed)))


def _equal_shares(total: Decimal, n: int) -> list[Decimal]:
    """Split to the cent; the leftover cents go to the first people, so the shares add up exactly."""
    cents = int(_cents(total) * 100)
    base, extra = divmod(cents, n)
    return [Decimal(base + (1 if i < extra else 0)) / 100 for i in range(n)]


def _shares_text(shares: list[Decimal], cur: str) -> str:
    if len(set(shares)) == 1:
        return money(shares[0], cur)
    hi, lo = max(shares), min(shares)
    n_hi = sum(1 for s in shares if s == hi)
    return f"{money(hi, cur)} ({n_hi} of you) and {money(lo, cur)} ({len(shares) - n_hi})"


def split_equally(total, people, claimed=None, cur: str = "", names=None) -> dict:
    total, people = Decimal(str(total)), int(people)
    if people < 1 or people > 1000:
        return unreadable("split", "Say how many people share it (1 to 1,000).")
    shares = _equal_shares(total, people)
    lines = [L("Total", money(total, cur)), L("People", str(people)),
             L("Each pays", _shares_text(shares, cur), total=True)]
    notes = []
    if len(set(shares)) > 1:
        notes.append(f"{money(total, cur)} doesn't divide evenly into {people}, so some pay one cent more; the "
                     "shares add up to the total exactly.")
    title = f"{money(total, cur)} split {people} ways"
    return _with_claim(_result("split", title, shares[0], _shares_text(shares, cur), lines, claimed,
                               _show(claimed, cur, True), is_money=True, notes=notes, tolerance=CENT,
                               extra={"shares": [float(s) for s in shares]}))


def settle_up(paid: dict, cur: str = "", people=None) -> dict:
    """Who paid what -> each person's fair share and the fewest payments that settle everyone."""
    paid = {str(k): Decimal(str(v)) for k, v in paid.items()}
    for p in people or []:
        paid.setdefault(str(p), D0)
    names = list(paid)
    if len(names) < 2:
        return unreadable("settle", "Name at least two people and what each paid, e.g. 'Asha paid 900, Ravi paid 300'.")
    total = sum(paid.values(), D0)
    shares = dict(zip(names, _equal_shares(total, len(names)), strict=True))
    balance = {n: paid[n] - shares[n] for n in names}
    lines = [L(f"{n} paid", money(paid[n], cur)) for n in names]
    lines += [L("Total", money(total, cur)), L("Fair share each", _shares_text(list(shares.values()), cur))]
    owe = sorted(((n, -b) for n, b in balance.items() if b < 0), key=lambda x: -x[1])
    get = sorted(((n, b) for n, b in balance.items() if b > 0), key=lambda x: -x[1])
    payments, i, j = [], 0, 0
    owe_l, get_l = [list(x) for x in owe], [list(x) for x in get]
    while i < len(owe_l) and j < len(get_l):
        amt = min(owe_l[i][1], get_l[j][1])
        if amt > 0:
            payments.append({"from": owe_l[i][0], "to": get_l[j][0], "amount": float(amt)})
        owe_l[i][1] -= amt
        get_l[j][1] -= amt
        if owe_l[i][1] == 0:
            i += 1
        if get_l[j][1] == 0:
            j += 1
    def pays(p):
        return f"{p['from']} {'pay' if p['from'] == 'You' else 'pays'} {'you' if p['to'] == 'You' else p['to']}"
    for p in payments:
        lines.append(L(pays(p), money(Decimal(str(p["amount"])), cur), total=True))
    if not payments:
        lines.append(L("Everyone is even", "nothing to pay", total=True))
    summary = ("To settle up: " + "; ".join(f"{pays(p)} {money(Decimal(str(p['amount'])), cur)}"
                                             for p in payments) + "." if payments else "Everyone has paid their share.")
    return {"kind": "settle", "status": "computed", "title": f"settling {money(total, cur)} between {len(names)} people",
            "answer": summary, "value": float(total), "claimed": None, "lines": lines, "summary": summary,
            "notes": ["Everyone pays an equal share; the fewest payments are suggested."], "payments": payments}


# --------------------------------------------------------------------------- loans and savings

def _pow(base: Decimal, n) -> Decimal:
    with localcontext() as c:
        c.prec = 40
        return base ** Decimal(str(n))


def loan_emi(principal, annual_rate, months, claimed=None, cur: str = "₹") -> dict:
    p, r, n = Decimal(str(principal)), Decimal(str(annual_rate)), int(months)
    if p <= 0 or n <= 0 or n > 1200 or r < 0 or r > 100:
        return unreadable("loan", "Give the loan amount, the yearly interest rate and the time, e.g. 'EMI for 5 lakh "
                                  "at 9% for 5 years'.")
    with localcontext() as c:
        c.prec = 40
        if r == 0:
            emi = p / n
        else:
            i = r / 1200
            f = _pow(1 + i, n)
            emi = p * i * f / (f - 1)
    emi_c = _cents(emi)
    total = _cents(emi * n)
    interest = total - p
    years = (f"{n // 12} year{'s' if n // 12 != 1 else ''}" + (f" {n % 12} month{'s' if n % 12 != 1 else ''}"
                                                              if n % 12 else "")) if n >= 12 else \
        f"{n} month{'s' if n != 1 else ''}"
    lines = [L("Loan", money(p, cur)), L("Interest", f"{pct(r)} a year ({number(r / 12, 4)}% a month)"),
             L("Time", f"{n} months ({years})" if n >= 12 else years),
             L("EMI (monthly payment)", money(emi_c, cur), total=True),
             L("Total paid", money(total, cur)), L("Total interest", money(interest, cur))]
    notes = ["EMI = P × i × (1 + i)^n ÷ ((1 + i)^n − 1), with i the monthly rate, on a reducing balance. Banks may "
             "differ by a few paise or round up."]
    return _with_claim(_result("loan", f"the EMI on {money(p, cur)} at {pct(r)} for {years}", emi, money(emi_c, cur),
                               lines, claimed, _show(claimed, cur, True), is_money=True, notes=notes,
                               extra={"total_paid": float(total), "total_interest": float(interest)}))


def simple_interest(principal, annual_rate, years, claimed=None, cur: str = "₹", claim_is="interest") -> dict:
    p, r, t = Decimal(str(principal)), Decimal(str(annual_rate)), Decimal(str(years))
    si = _cents(p * r * t / 100)
    amount = p + si
    lines = [L("Principal", money(p, cur)), L("Rate", pct(r) + " a year"), L("Time", _years_text(t)),
             L("Interest = P × R × T ÷ 100", money(si, cur), total=claim_is == "interest"),
             L("Amount at the end", money(amount, cur), total=claim_is == "amount")]
    value = si if claim_is == "interest" else amount
    title = (f"simple interest on {money(p, cur)} at {pct(r)} for {_years_text(t)}" if claim_is == "interest"
             else f"the amount after {_years_text(t)}")
    return _with_claim(_result("interest", title, value, money(value, cur), lines, claimed,
                               _show(claimed, cur, True), is_money=True))


FREQ = {"yearly": 1, "annually": 1, "annual": 1, "half-yearly": 2, "half yearly": 2, "semi-annually": 2,
        "quarterly": 4, "monthly": 12, "daily": 365}


def compound_interest(principal, annual_rate, years, per_year: int = 1, claimed=None, cur: str = "₹",
                      claim_is="amount", label: str = "") -> dict:
    p, r, t = Decimal(str(principal)), Decimal(str(annual_rate)), Decimal(str(years))
    k = int(per_year)
    amount = _cents(p * _pow(1 + r / (100 * k), Decimal(k) * t))
    ci = amount - p
    freq = {1: "yearly", 2: "half-yearly", 4: "quarterly", 12: "monthly", 365: "daily"}.get(k, f"{k} times a year")
    lines = [L("Principal", money(p, cur)), L("Rate", f"{pct(r)} a year, compounded {freq}"),
             L("Time", _years_text(t)), L("Amount = P × (1 + r/k)^(k×t)", money(amount, cur), total=claim_is == "amount"),
             L("Interest earned", money(ci, cur), total=claim_is == "interest")]
    notes = []
    simple = _cents(p * r * t / 100)
    if ci > simple:
        notes.append(f"Simple interest would be {money(simple, cur)}; compounding adds {money(ci - simple, cur)}.")
    value = amount if claim_is == "amount" else ci
    what = label or f"{money(p, cur)} at {pct(r)} compounded {freq} for {_years_text(t)}"
    title = (f"the amount from {what}" if claim_is == "amount" else f"the interest on {what}")
    return _with_claim(_result("interest", title, value, money(value, cur), lines, claimed,
                               _show(claimed, cur, True), is_money=True, notes=notes))


def sip(monthly, annual_rate, years, claimed=None, cur: str = "₹") -> dict:
    """Future value of a monthly investment (paid at the start of each month), at a constant yearly return."""
    m, r, t = Decimal(str(monthly)), Decimal(str(annual_rate)), Decimal(str(years))
    n = int(t * 12)
    i = r / 1200
    with localcontext() as c:
        c.prec = 40
        fv = m * n if i == 0 else m * (_pow(1 + i, n) - 1) / i * (1 + i)
    fv = _cents(fv)
    invested = m * n
    lines = [L("Every month", money(m, cur)), L("For", f"{n} months"), L("Expected return", pct(r) + " a year"),
             L("You put in", money(invested, cur)), L("Value at the end", money(fv, cur), total=True),
             L("Gain", money(fv - invested, cur))]
    notes = ["Assumes the same return every month, which real markets never give; mutual fund returns vary."]
    return _with_claim(_result("sip", f"the value of a {money(m, cur)} monthly SIP at {pct(r)} for "
                                      f"{_years_text(t)}", fv, money(fv, cur), lines, claimed,
                               _show(claimed, cur, True), is_money=True, notes=notes))


def _years_text(t: Decimal) -> str:
    months = t * 12
    if months == months.to_integral_value() and t != t.to_integral_value():
        return f"{int(months)} months"
    return f"{number(t)} year{'s' if t != 1 else ''}"


# =========================================================================== dates and time

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"], 1)}
MONTH_RX = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
            r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
DATE_RX = re.compile(
    rf"(?P<rel>\btoday\b|\btomorrow\b|\byesterday\b)"
    rf"|(?P<iso>\b\d{{4}}-\d{{1,2}}-\d{{1,2}}\b)"
    rf"|(?P<dmy>\b\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}}\b)"
    rf"|(?P<d1>\b\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<m1>{MONTH_RX})\.?,?\s*(?P<y1>\d{{4}})?\b"
    rf"|\b(?P<m2>{MONTH_RX})\.?\s+(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?,?\s*(?P<y2>\d{{4}})?\b", re.I)
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def today() -> dt.date:
    """Today in India unless TZ says otherwise (most users of this server are there)."""
    import os
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(os.getenv("CASCADE_TZ", "Asia/Kolkata"))).date()
    except Exception:
        return dt.date.today()


def find_dates(text: str, base: dt.date | None = None) -> list[dict]:
    base = base or today()
    out = []
    for m in DATE_RX.finditer(text):
        g = m.groupdict()
        try:
            if g["rel"]:
                d = base + dt.timedelta(days={"today": 0, "tomorrow": 1, "yesterday": -1}[g["rel"].lower()])
                note = None
            elif g["iso"]:
                y, mo, da = (int(x) for x in g["iso"].split("-"))
                d, note = dt.date(y, mo, da), None
            elif g["dmy"]:
                a, b, c = (int(x) for x in re.split(r"[/.-]", g["dmy"]))
                c = c + 2000 if c < 100 else c
                if a > 12 or (b <= 12 and a <= 12):     # day first, as in India and the UK
                    d, note = dt.date(c, b, a), ("read as day/month/year" if a <= 12 and b <= 12 and a != b else None)
                else:
                    d, note = dt.date(c, a, b), "read as month/day/year"
            else:
                day = int(g["d1"] or g["d2"])
                mon = MONTHS[(g["m1"] or g["m2"])[:3].lower()]
                year = g["y1"] or g["y2"]
                d = dt.date(int(year) if year else base.year, mon, day)
                note = None if year else f"no year given, so {d.year} is assumed"
        except (ValueError, KeyError):
            continue
        out.append({"date": d, "start": m.start(), "end": m.end(), "note": note})
    return out


def show_date(d: dt.date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.day} {d.strftime('%B')} {d.year}"


def _ymd(a: dt.date, b: dt.date) -> tuple[int, int, int]:
    """Whole years, months and days from a to b (a <= b)."""
    y, m, d = b.year - a.year, b.month - a.month, b.day - a.day
    if d < 0:
        m -= 1
        prev = (b.replace(day=1) - dt.timedelta(days=1))
        d += prev.day
    if m < 0:
        y -= 1
        m += 12
    return y, m, d


def _ymd_text(y, m, d) -> str:
    parts = [f"{y} year{'s' if y != 1 else ''}"] if y else []
    parts += [f"{m} month{'s' if m != 1 else ''}"] if m else []
    parts += [f"{d} day{'s' if d != 1 else ''}"] if d or not parts else []
    return ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]


def days_between(start, end, claimed=None, inclusive: bool = False, notes=None) -> dict:
    a, b = _as_date(start), _as_date(end)
    n = (b - a).days + (1 if inclusive else 0) * (1 if b >= a else -1)
    weeks, rest = divmod(abs(n), 7)
    lines = [L("From", show_date(a)), L("To", show_date(b)),
             L("Days" + (" (counting both days)" if inclusive else ""), f"{abs(n):,}", total=True),
             L("In weeks", f"{weeks} week{'s' if weeks != 1 else ''} and {rest} day{'s' if rest != 1 else ''}")]
    lo, hi = (a, b) if a <= b else (b, a)
    lines.append(L("In months", _ymd_text(*_ymd(lo, hi))))
    notes = list(notes or [])
    if not inclusive:
        notes.append("The first day is not counted; add 1 to count both days.")
    res = _result("days", f"the number of days from {show_date(a)} to {show_date(b)}", Decimal(abs(n)),
                  f"{abs(n):,} days",
                  lines, None if claimed is None else Decimal(str(claimed)),
                  None if claimed is None else f"{number(Decimal(str(claimed)))} days", notes=notes)
    if res["status"] == "wrong" and claimed is not None and abs(Decimal(str(claimed)) - abs(n)) == 1:
        res["notes"].insert(0, "The claim is off by one: it counts " + ("both days." if Decimal(str(claimed)) > abs(n)
                                                                         else "neither day."))
    return _with_claim(res)


def add_days(start, days=0, weeks=0, months=0, years=0, claimed=None, notes=None) -> dict:
    a = _as_date(start)
    total_days = int(days) + 7 * int(weeks)
    d = _add_months(a, int(months) + 12 * int(years)) + dt.timedelta(days=total_days)
    parts = []
    for v, word in ((years, "year"), (months, "month"), (weeks, "week"), (days, "day")):
        v = int(v)
        if v:
            parts.append(f"{abs(v)} {word}{'s' if abs(v) != 1 else ''}")
    span = " ".join(parts) or "0 days"
    later = (int(days) + 7 * int(weeks) + 31 * (int(months) + 12 * int(years))) >= 0
    lines = [L("Start", show_date(a)), L("Plus" if later else "Minus", span), L("Date", show_date(d), total=True)]
    notes = list(notes or [])
    if (months or years) and d.day != a.day and not total_days:
        notes.append(f"{a.strftime('%B')} {a.day} has no match in that month, so the last day of the month is used.")
    claimed_date = _as_date(claimed) if claimed is not None else None
    res = _result("add_days", f"{span} {'after' if later else 'before'} {show_date(a)}", Decimal(d.toordinal()),
                  show_date(d), lines, None if claimed_date is None else Decimal(claimed_date.toordinal()),
                  None if claimed_date is None else show_date(claimed_date), notes=notes)
    res["value"] = None
    res["date"] = d.isoformat()
    return _with_claim(res)


def weekday(date, claimed=None, notes=None) -> dict:
    d = _as_date(date)
    name = WEEKDAYS[d.weekday()]
    lines = [L("Date", f"{d.day} {d.strftime('%B')} {d.year}"), L("Day of the week", name, total=True)]
    res = {"kind": "weekday", "title": f"the day of the week of {d.day} {d.strftime('%B')} {d.year}", "answer": name,
           "value": None, "claimed": claimed, "lines": lines, "notes": list(notes or [])}
    if claimed is None:
        res.update(status="computed", summary=f"{d.day} {d.strftime('%B')} {d.year} is a {name}.")
    elif claimed.lower()[:3] == name.lower()[:3]:
        res.update(status="correct", summary=f"Correct: {d.day} {d.strftime('%B')} {d.year} is a {name}.")
    else:
        res.update(status="wrong", summary=f"Wrong: {d.day} {d.strftime('%B')} {d.year} is a {name}, not a "
                                           f"{claimed.capitalize()}.")
        lines[-1]["wrong"] = claimed.capitalize()
    return res


def age(born, on=None, claimed=None, notes=None) -> dict:
    b = _as_date(born)
    o = _as_date(on) if on is not None else today()
    if o < b:
        return unreadable("age", "The birth date is after the other date.")
    y, m, d = _ymd(b, o)
    lines = [L("Born", show_date(b)), L("On", show_date(o)), L("Age", _ymd_text(y, m, d), total=True),
             L("In days", f"{(o - b).days:,}")]
    nxt = b.replace(year=o.year) if not (b.month == 2 and b.day == 29) else dt.date(o.year, 3, 1)
    if nxt <= o:
        nxt = nxt.replace(year=o.year + 1)
    lines.append(L("Next birthday", f"{show_date(nxt)}, in {(nxt - o).days} days"))
    res = _result("age", f"the age on {show_date(o)} of someone born {show_date(b)}", Decimal(y), f"{y} years",
                  lines, None if claimed is None else Decimal(str(claimed)),
                  None if claimed is None else f"{number(Decimal(str(claimed)))} years", notes=notes)
    res["answer"] = _ymd_text(y, m, d)
    if res["status"] == "computed":
        res["summary"] = f"Born {show_date(b)}: on {show_date(o)} that is {_ymd_text(y, m, d)} old."
    return _with_claim(res)


def _as_date(x) -> dt.date:
    if isinstance(x, dt.datetime):
        return x.date()
    if isinstance(x, dt.date):
        return x
    found = find_dates(str(x))
    if not found:
        raise ValueError(f"not a date: {x!r}")
    return found[0]["date"]


def _add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    year, month = d.year + y, m + 1
    import calendar
    return dt.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


TIME_RX = re.compile(r"\b(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)?(?![\d:])", re.I)


def parse_time(s: str) -> dt.time:
    m = re.fullmatch(r"\s*(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\s*", s, re.I)
    if not m:
        raise ValueError(f"not a time: {s!r}")
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower().replace(".", "")
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if not (0 <= h <= 23 and 0 <= mi <= 59) or (not ap and not m.group(2)):
        raise ValueError(f"not a time: {s!r}")
    return dt.time(h, mi)


def show_time(t: dt.time) -> str:
    h12 = t.hour % 12 or 12
    return f"{h12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def _hm(minutes: int) -> str:
    h, m = divmod(abs(minutes), 60)
    return (f"{h} h " if h else "") + f"{m} min" if m or not h else f"{h} h"


def time_between(start, end, claimed_minutes=None, claimed_text=None) -> dict:
    a, b = (parse_time(x) if isinstance(x, str) else x for x in (start, end))
    mins = (b.hour * 60 + b.minute) - (a.hour * 60 + a.minute)
    overnight = mins < 0
    if overnight:
        mins += 24 * 60
    lines = [L("From", show_time(a)), L("To", show_time(b) + (" (next day)" if overnight else "")),
             L("Time between", _hm(mins), total=True), L("In minutes", f"{mins:,}")]
    return _with_claim(_result("time_between", f"the time from {show_time(a)} to {show_time(b)}", Decimal(mins),
                               _hm(mins), lines, None if claimed_minutes is None else Decimal(claimed_minutes),
                               claimed_text, notes=["Crosses midnight."] if overnight else None))


ZONES = {"ist": "Asia/Kolkata", "india": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "delhi": "Asia/Kolkata",
         "bangalore": "Asia/Kolkata", "bengaluru": "Asia/Kolkata", "pune": "Asia/Kolkata", "chennai": "Asia/Kolkata",
         "kolkata": "Asia/Kolkata", "hyderabad": "Asia/Kolkata",
         "utc": "UTC", "gmt": "Europe/London", "london": "Europe/London", "uk": "Europe/London", "bst": "Europe/London",
         "est": "America/New_York", "edt": "America/New_York", "et": "America/New_York", "new york": "America/New_York",
         "nyc": "America/New_York", "toronto": "America/Toronto", "boston": "America/New_York",
         "cst": "America/Chicago", "cdt": "America/Chicago", "chicago": "America/Chicago", "texas": "America/Chicago",
         "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles", "pt": "America/Los_Angeles",
         "california": "America/Los_Angeles", "san francisco": "America/Los_Angeles",
         "los angeles": "America/Los_Angeles", "seattle": "America/Los_Angeles",
         "cet": "Europe/Berlin", "cest": "Europe/Berlin", "berlin": "Europe/Berlin", "paris": "Europe/Paris",
         "germany": "Europe/Berlin", "france": "Europe/Paris", "amsterdam": "Europe/Amsterdam",
         "dubai": "Asia/Dubai", "uae": "Asia/Dubai", "gst": "Asia/Dubai", "singapore": "Asia/Singapore",
         "sgt": "Asia/Singapore", "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo", "jst": "Asia/Tokyo",
         "sydney": "Australia/Sydney", "aest": "Australia/Sydney", "aedt": "Australia/Sydney",
         "beijing": "Asia/Shanghai", "china": "Asia/Shanghai", "hong kong": "Asia/Hong_Kong",
         "kathmandu": "Asia/Kathmandu", "nepal": "Asia/Kathmandu", "dhaka": "Asia/Dhaka", "karachi": "Asia/Karachi",
         "riyadh": "Asia/Riyadh", "saudi": "Asia/Riyadh", "moscow": "Europe/Moscow"}


def convert_time_zone(time, from_zone, to_zone, on=None) -> dict:
    from zoneinfo import ZoneInfo
    t = parse_time(time) if isinstance(time, str) else time
    fz, tz = ZONES.get(str(from_zone).lower(), from_zone), ZONES.get(str(to_zone).lower(), to_zone)
    day = _as_date(on) if on is not None else today()
    start = dt.datetime.combine(day, t, tzinfo=ZoneInfo(fz))
    end = start.astimezone(ZoneInfo(tz))
    shift = ""
    if end.date() != start.date():
        shift = " (next day)" if end.date() > start.date() else " (previous day)"
    off_a, off_b = start.utcoffset(), end.utcoffset()
    diff = ((off_b or dt.timedelta(0)) - (off_a or dt.timedelta(0))).total_seconds() / 3600
    lines = [L(f"In {_zone_name(from_zone)}", f"{show_time(t)}, {show_date(day)}"),
             L("Difference", f"{'+' if diff >= 0 else '-'}{number(Decimal(str(abs(diff))), 2)} h"),
             L(f"In {_zone_name(to_zone)}", show_time(end.time()) + shift, total=True)]
    return {"kind": "time_zone", "status": "computed", "title": f"{show_time(t)} {_zone_name(from_zone)} in "
                                                                f"{_zone_name(to_zone)}",
            "answer": show_time(end.time()) + shift, "value": None, "claimed": None, "lines": lines,
            "summary": f"{show_time(t)} in {_zone_name(from_zone)} is {show_time(end.time())}{shift} in "
                       f"{_zone_name(to_zone)}.",
            "notes": ["Daylight saving is taken into account for that date."]}


def _zone_name(z: str) -> str:
    z = str(z)
    return z.upper() if len(z) <= 4 else z.title()


# =========================================================================== units

F = Fraction
UNITS: dict[str, tuple[str, Fraction]] = {}  # name -> (dimension, size in the base unit)


def _units(dim, table):
    for names, size in table:
        for n in names.split("|"):
            UNITS[n] = (dim, F(size))


_units("length", [("mm|millimetre|millimeter|millimetres|millimeters", "1/1000"),
                  ("cm|centimetre|centimeter|centimetres|centimeters", "1/100"),
                  ("m|metre|meter|metres|meters|mtr|mtrs", 1), ("km|kms|kilometre|kilometer|kilometres|kilometers", 1000),
                  ("in|inch|inches", "0.0254"), ("ft|foot|feet", "0.3048"), ("yd|yard|yards", "0.9144"),
                  ("mi|mile|miles", "1609.344"), ("nmi|nautical mile|nautical miles", 1852)])
_units("mass", [("mg|milligram|milligrams", "1/1000000"), ("g|gm|gms|gram|grams|gramme", "1/1000"),
                ("kg|kgs|kilo|kilos|kilogram|kilograms", 1), ("tonne|tonnes|ton|tons|t", 1000),
                ("quintal|quintals", 100), ("oz|ounce|ounces", "0.028349523125"), ("lb|lbs|pound|pounds", "0.45359237"),
                ("st|stone|stones", "6.35029318"), ("tola|tolas", "0.0116638038")])
_units("volume", [("ml|millilitre|milliliter|millilitres|milliliters", "1/1000"),
                  ("cl|centilitre|centiliter", "1/100"), ("l|litre|liter|litres|liters|ltr|ltrs", 1),
                  ("tsp|teaspoon|teaspoons", "0.00492892159375"), ("tbsp|tablespoon|tablespoons", "0.01478676478125"),
                  ("cup|cups", "0.2365882365"), ("fl oz|fluid ounce|fluid ounces", "0.0295735295625"),
                  ("pint|pints", "0.473176473"), ("quart|quarts", "0.946352946"),
                  ("gallon|gallons|gal", "3.785411784"), ("imperial gallon|imperial gallons|uk gallon", "4.54609"),
                  ("m3|cubic metre|cubic meter|cubic metres", 1000)])
_units("area", [("sq m|sqm|m2|square metre|square meter|square metres|square meters", 1),
                ("sq ft|sqft|ft2|square foot|square feet", "0.09290304"),
                ("sq yd|sq yard|square yard|square yards|gaj", "0.83612736"),
                ("acre|acres", "4046.8564224"), ("hectare|hectares|ha", 10000),
                ("sq km|km2|square kilometre|square kilometer|square km", 1000000),
                ("sq mi|square mile|square miles", "2589988.110336")])
_units("speed", [("m/s|mps|metres per second|meters per second", 1),
                 ("km/h|kmph|kph|kmh|km per hour|kilometres per hour|kilometers per hour", "5/18"),
                 ("mph|miles per hour", "0.44704"), ("knot|knots|kn", "1852/3600")])
_units("time", [("s|sec|secs|second|seconds", 1), ("min|mins|minute|minutes", 60), ("h|hr|hrs|hour|hours", 3600),
                ("day|days", 86400), ("week|weeks", 604800)])
_units("data", [("b|byte|bytes", 1), ("kb|kilobyte|kilobytes", 1000), ("mb|megabyte|megabytes", 10**6),
                ("gb|gigabyte|gigabytes", 10**9), ("tb|terabyte|terabytes", 10**12), ("kib", 1024),
                ("mib", 1024**2), ("gib", 1024**3), ("tib", 1024**4)])
TEMPS = {"c": "°C", "°c": "°C", "celsius": "°C", "centigrade": "°C", "degrees c": "°C", "degree c": "°C",
         "f": "°F", "°f": "°F", "fahrenheit": "°F", "degrees f": "°F", "degree f": "°F", "k": "K", "kelvin": "K"}
FUEL = {"km/l|kmpl|km per litre|km per liter|kilometres per litre": "km/l", "mpg|miles per gallon": "mpg",
        "l/100km|l/100 km|litres per 100 km|liters per 100 km": "l/100km"}
FUEL_NAMES = {n: v for k, v in FUEL.items() for n in k.split("|")}
UNIT_WORDS = sorted(list(UNITS) + list(TEMPS) + list(FUEL_NAMES), key=len, reverse=True)
UNIT_RX = "|".join(re.escape(u) for u in UNIT_WORDS)


def _temp_to_k(v: Fraction, u: str) -> Fraction:
    return {"°C": v + F("273.15"), "°F": (v - 32) * F(5, 9) + F("273.15"), "K": v}[u]


def _temp_from_k(k: Fraction, u: str) -> Fraction:
    return {"°C": k - F("273.15"), "°F": (k - F("273.15")) * F(9, 5) + 32, "K": k}[u]


def _fuel_to_kml(v: Fraction, u: str) -> Fraction:
    return {"km/l": v, "mpg": v * F("1.609344") / F("3.785411784"), "l/100km": F(100) / v}[u]


def _fuel_from_kml(k: Fraction, u: str) -> Fraction:
    return {"km/l": k, "mpg": k * F("3.785411784") / F("1.609344"), "l/100km": F(100) / k}[u]


def _sig(x: Fraction, digits: int = 6) -> str:
    """A tidy number: exact when short, otherwise rounded to `digits` significant digits."""
    d = Decimal(x.numerator) / Decimal(x.denominator)
    if d == 0:
        return "0"
    exp = d.adjusted()
    places = max(0, digits - 1 - exp)
    return number(d, min(places, 10))


SHOW_UNIT = {"b": "B", "kb": "KB", "mb": "MB", "gb": "GB", "tb": "TB", "kib": "KiB", "mib": "MiB", "gib": "GiB",
             "tib": "TiB", "kmph": "km/h", "kph": "km/h", "kmh": "km/h", "kms": "km", "mtr": "m", "mtrs": "m", "kgs": "kg",
             "gms": "g", "gm": "g", "lbs": "lb", "ltr": "l", "ltrs": "l", "sqft": "sq ft", "sqm": "sq m", "m2": "sq m",
             "ft2": "sq ft", "km2": "sq km", "mps": "m/s"}


def convert_units(value, from_unit, to_unit, claimed=None) -> dict:
    fu, tu = str(from_unit).strip().lower(), str(to_unit).strip().lower()
    v = F(str(value))
    if fu in TEMPS and tu in TEMPS:
        a, b = TEMPS[fu], TEMPS[tu]
        out = _temp_from_k(_temp_to_k(v, a), b)
        fu_show, tu_show, dim = a, b, "temperature"
        how = {("°C", "°F"): "× 9/5 + 32", ("°F", "°C"): "− 32, then × 5/9", ("°C", "K"): "+ 273.15",
               ("K", "°C"): "− 273.15"}.get((a, b), "through kelvin")
    elif fu in FUEL_NAMES and tu in FUEL_NAMES:
        a, b = FUEL_NAMES[fu], FUEL_NAMES[tu]
        out = _fuel_from_kml(_fuel_to_kml(v, a), b)
        fu_show, tu_show, dim, how = a, b, "fuel economy", "1 mile = 1.609344 km, 1 US gallon = 3.785411784 l"
    elif fu in UNITS and tu in UNITS:
        (da, sa), (db, sb) = UNITS[fu], UNITS[tu]
        if da != db:
            return unreadable("convert", f"{from_unit} is a {da} and {to_unit} is a {db}: they can't be converted.")
        out, fu_show, tu_show, dim = v * sa / sb, SHOW_UNIT.get(fu, fu), SHOW_UNIT.get(tu, tu), da
        ratio = sa / sb
        how = f"1 {fu_show} = {_sig(ratio, 8)} {tu_show}"
    else:
        missing = from_unit if fu not in UNITS and fu not in TEMPS and fu not in FUEL_NAMES else to_unit
        return unreadable("convert", f"I don't know the unit '{missing}'.")
    ans = f"{_sig(out)} {tu_show}"
    lines = [L("Value", f"{_sig(v, 10)} {fu_show}"), L("How", how), L("Result", ans, total=True)]
    notes = []
    if dim == "volume" and (fu in ("cup", "cups", "tbsp", "tsp", "gallon", "gallons") or
                            tu in ("cup", "cups", "tbsp", "tsp", "gallon", "gallons")):
        notes.append("US cups, spoons and gallons; an Indian or metric cup is 250 ml, a UK gallon is 4.546 l.")
    if dim == "data":
        notes.append("KB, MB, GB are powers of 1,000 here; computers often show powers of 1,024 (KiB, MiB, GiB).")
    claimed_d = None if claimed is None else Decimal(str(claimed))
    out_d = Decimal(out.numerator) / Decimal(out.denominator)
    res = _result("convert", f"{_sig(v, 10)} {fu_show} in {tu_show}", out_d, ans, lines, claimed_d,
                  None if claimed is None else f"{number(claimed_d, 6)} {tu_show}", notes=notes)
    return _with_claim(res)


# =========================================================================== travel

def trip(distance=None, speed=None, hours=None, claimed=None, claimed_text=None, unit: str = "km") -> dict:
    """Any two of distance, speed and time give the third."""
    d = None if distance is None else Decimal(str(distance))
    s = None if speed is None else Decimal(str(speed))
    h = None if hours is None else Decimal(str(hours))
    per = f"{unit}/h"
    with localcontext() as c:
        c.prec = 34
        if d is not None and s is not None:
            if s == 0:
                return unreadable("trip", "The speed cannot be 0.")
            h = d / s
            mins = int((h * 60).quantize(Decimal(1), rounding=ROUND_HALF_UP))
            lines = [L("Distance", f"{number(d)} {unit}"), L("Speed", f"{number(s)} {per}"),
                     L("Time = distance ÷ speed", _hm(mins), total=True)]
            return _with_claim(_result("trip", f"the time for {number(d)} {unit} at {number(s)} {per}",
                                       Decimal(mins), _hm(mins), lines, claimed, claimed_text,
                                       notes=["Driving time only: no stops or traffic."]))
        if d is not None and h is not None:
            if h == 0:
                return unreadable("trip", "The time cannot be 0.")
            v = d / h
            lines = [L("Distance", f"{number(d)} {unit}"), L("Time", _hm(int(h * 60))),
                     L("Average speed = distance ÷ time", f"{number(v, 2)} {per}", total=True)]
            return _with_claim(_result("trip", f"the average speed for {number(d)} {unit} in {_hm(int(h * 60))}",
                                       v, f"{number(v, 2)} {per}", lines, claimed, claimed_text,
                                       tolerance=Decimal("0.01")))
        if s is not None and h is not None:
            v = s * h
            lines = [L("Speed", f"{number(s)} {per}"), L("Time", _hm(int(h * 60))),
                     L("Distance = speed × time", f"{number(v, 2)} {unit}", total=True)]
            return _with_claim(_result("trip", f"the distance at {number(s)} {per} for {_hm(int(h * 60))}", v,
                                       f"{number(v, 2)} {unit}", lines, claimed, claimed_text,
                                       tolerance=Decimal("0.01")))
    return unreadable("trip", "Give two of: the distance, the speed, the time.")


def fuel_cost(distance, mileage, price, people=1, claimed=None, cur: str = "₹", unit: str = "km") -> dict:
    d, k, p = Decimal(str(distance)), Decimal(str(mileage)), Decimal(str(price))
    if k == 0:
        return unreadable("fuel", "The mileage cannot be 0.")
    with localcontext() as c:
        c.prec = 34
        litres = d / k
    cost = _cents(litres * p)
    lines = [L("Distance", f"{number(d)} {unit}"), L("Mileage", f"{number(k)} {unit}/l"),
             L("Fuel needed = distance ÷ mileage", f"{number(litres, 2)} l"), L("Price", money(p, cur) + " a litre"),
             L("Fuel cost", money(cost, cur), total=int(people) <= 1)]
    value, answer, split = cost, money(cost, cur), ""
    if int(people) > 1:
        shares = _equal_shares(cost, int(people))
        value, split = shares[0], f", split {people} ways"
        answer = f"{_shares_text(shares, cur)} each ({money(cost, cur)} in all)"
        lines.append(L(f"Each of {people} pays", _shares_text(shares, cur), total=True))
    return _with_claim(_result("fuel", f"the fuel cost for {number(d)} {unit} at {number(k)} {unit}/l and "
                                       f"{money(p, cur)}/l{split}", value, answer, lines, claimed,
                               _show(claimed, cur, True), is_money=True, tolerance=CENT if int(people) > 1 else None,
                               notes=["One way; double it for a return trip."]))


# =========================================================================== studies

def gpa(credits, points, claimed=None, label: str = "SGPA") -> dict:
    cs = [Decimal(str(c)) for c in credits]
    ps = [Decimal(str(p)) for p in points]
    if not cs or len(cs) != len(ps):
        return unreadable("gpa", "Give the same number of credits and grade points, e.g. 'credits 4, 4, 3 and grade "
                                 "points 9, 8, 10'.")
    if sum(cs) == 0:
        return unreadable("gpa", "The credits add up to 0.")
    weighted = sum((c * p for c, p in zip(cs, ps, strict=True)), D0)
    with localcontext() as c:
        c.prec = 34
        v = weighted / sum(cs)
    lines = [L(f"Subject {i}", f"{number(c)} credits × {number(p)} = {number(c * p)}")
             for i, (c, p) in enumerate(zip(cs, ps, strict=True), 1)]
    lines += [L("Total credits", number(sum(cs))), L("Credit points", number(weighted)),
              L(f"{label} = credit points ÷ credits", number(v, 2), total=True)]
    plain = sum(ps, D0) / len(ps)
    notes = []
    if claimed is not None and abs(Decimal(str(claimed)) - plain.quantize(CENT)) < Decimal("0.006") and \
            not judge(v, Decimal(str(claimed)), tolerance=Decimal("0.005"))[0]:
        notes.append(f"{number(Decimal(str(claimed)), 2)} is the plain average of the grade points; the {label} is "
                     "weighted by credits.")
    res = _result("gpa", f"the {label} for these " + ("semesters" if all(c == 1 for c in cs) else "subjects"), v,
                  number(v, 2), lines,
                  None if claimed is None else Decimal(str(claimed)),
                  None if claimed is None else number(Decimal(str(claimed)), 2), notes=notes,
                  tolerance=Decimal("0.005"))
    return _with_claim(res)


# =========================================================================== cooking

ING = re.compile(rf"^\s*(?P<qty>\d+(?:\.\d+)?|\d+\s*/\s*\d+|\d+\s+\d+\s*/\s*\d+|½|¼|¾)\s*(?P<unit>{UNIT_RX}|"
                 r"pinch(?:es)?|cloves?|pieces?|pcs?|nos?|slices?|sticks?|leaves|leaf|handful|bunch|large|medium|"
                 r"small)?\.?\s+(?:of\s+)?(?P<name>.+?)\s*$", re.I)


def _qty(s: str) -> Fraction:
    s = s.strip().replace("½", "1/2").replace("¼", "1/4").replace("¾", "3/4")
    if " " in s and "/" in s:
        whole, frac = s.split(None, 1)
        return F(int(whole)) + F(frac.replace(" ", ""))
    return F(s.replace(" ", ""))


def _nice(q: Fraction) -> str:
    whole, rest = divmod(q, 1)
    for frac, sym in ((F(1, 4), "¼"), (F(1, 2), "½"), (F(3, 4), "¾"), (F(1, 3), "⅓"), (F(2, 3), "⅔")):
        if rest == frac:
            return (f"{int(whole)}" if whole else "") + sym
    return _sig(q, 4)


def scale_recipe(ingredients, from_servings, to_servings) -> dict:
    a, b = F(str(from_servings)), F(str(to_servings))
    if a <= 0 or b <= 0:
        return unreadable("recipe", "Say how many it serves now and how many you want, e.g. 'from 4 to 6'.")
    k = b / a
    lines, rows, unread, said = [], [], [], []
    for item in ingredients:
        m = ING.match(str(item))
        if not m:
            if str(item).strip():
                unread.append(str(item).strip())
            continue
        q = _qty(m.group("qty")) * k
        unit = (m.group("unit") or "").strip()
        name = m.group("name").strip()
        countable = not unit or unit.lower() in ("cloves", "clove", "pieces", "piece", "pcs", "pc", "nos", "no",
                                                 "slices", "slice", "sticks", "stick", "large", "medium", "small")
        if unit.lower() in ("cup", "cups", "tbsp", "tsp", "teaspoon", "teaspoons", "tablespoon", "tablespoons",
                            "glass", "glasses", "bowl", "bowls"):
            one = re.sub(r"(?:es|s)$", "", unit) if unit.lower() not in ("tbsp", "tsp") else unit
            one = "glass" if unit.lower().startswith("glass") else one
            unit = one if q <= 1 or unit.lower() in ("tbsp", "tsp") else one + ("es" if one.endswith("s") else "s")
        shown = _nice(q) + (f" {unit}" if unit else "")
        note = " (round to a whole number)" if countable and q.denominator != 1 else ""
        lines.append(L(name, shown + note))
        rows.append({"name": name, "quantity": float(q), "unit": unit})
        said.append(f"{shown} {name}")
    if not rows:
        return unreadable("recipe", "List the ingredients with amounts, e.g. 'scale from 4 to 6: 200 g flour, 2 eggs'.")
    lines.append(L("Scale", f"× {_nice(k)} ({_sig(a, 6)} → {_sig(b, 6)} servings)", total=True))
    summary = f"For {_sig(b, 6)} instead of {_sig(a, 6)}: " + ", ".join(said) + "."
    notes = ["Baking times don't scale the same way; spices often need a little less than the full scale."]
    if unread:
        notes.insert(0, "Not read: " + "; ".join(unread))
    return {"kind": "recipe", "status": "computed", "title": f"the recipe for {_sig(b, 6)}", "answer": summary,
            "value": float(k), "claimed": None, "lines": lines, "summary": summary, "notes": notes,
            "ingredients": rows}


# =========================================================================== reading everyday requests

CLAIM_RX = re.compile(
    rf"(?:\bis\b|=|\bequals?\b|\bcomes?\s+to\b|\bwill\s+be\b|\bwould\s+be\b|\bshould\s+be\b|\bi\s+got\b|"
    rf"\bthey\s+(?:said|say|charged|quoted|told\s+me)\b|\bmakes?\b|\bgives?\b|=>|->|→|\bto\s+be\b|\bcame\s+to\b|"
    rf"\bworks\s+out\s+(?:to|at)\b|\bas\b)\s*(?:it\s+|about\s+|around\s+|exactly\s+|only\s+|just\s+|roughly\s+)?"
    rf"(?P<amt>(?:{CUR_RX})?\s*(?:{NUM_RX})\s*(?:{SCALE_RX})?(?:\s*(?:{CUR_RX}))?)(?P<pc>\s*%)?", re.I)
DURATION_RX = re.compile(r"(?P<n>\d+(?:\.\d+)?)\s*(?P<u>years?|yrs?|y\b|months?|mos?\b|mths?|weeks?|wks?|days?)",
                         re.I)


def _norm(text: str) -> str:
    t = (text or "")[:MAX_TEXT]
    t = t.replace("×", " x ").replace("−", "-").replace("–", "-").replace("—", "-").replace(" ", " ")
    t = re.sub(r"(?<=\d)\s*(?:/-)", "", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def _claim(t: str, *, percent: bool | None = False):
    """The answer the person says they have, from the last 'is X' / '= X' / 'they charged X' in the text, or the
    number that ends a yes/no question ("is 20% off 1,500 1,200?")."""
    tail = re.search(rf"(?P<amt>(?:{CUR_RX})?\s*(?:{NUM_RX})\s*(?:{SCALE_RX})?(?:\s*(?:{CUR_RX}))?)(?P<pc>\s*%)?\s*"
                     rf"(?:each|per\s+head|a\s+head|total|in\s+all)?\s*[,.]?\s*(?:right|correct|ok|true|no)?\s*\?+\s*$",
                     t, re.I)
    if tail and re.match(r"\s*(?:is|are|does|do|will|would|should|can|was|did)\b", t, re.I) and \
            (percent is None or bool(tail.group("pc")) == percent):
        amt = AMOUNT.match(tail.group("amt").strip())
        before = t[:tail.start()]
        if amt and len(find_amounts(before)) >= 1 and not re.search(r"(?:\bof|\bon|\bfor|\bat|\bin|x|\*|by)\s*$",
                                                                    before, re.I):
            v = _dec(amt.group("num")) * (SCALES[amt.group("scale").lower()] if amt.group("scale") else 1)
            lead = len(tail.group("amt")) - len(tail.group("amt").lstrip())
            return {"value": v, "start": tail.start("amt") + lead, "end": tail.end("amt"),
                    "text": tail.group("amt").strip()}
    found = None
    for m in CLAIM_RX.finditer(t):
        is_pc = bool(m.group("pc"))
        if percent is not None and is_pc != percent:
            continue
        amt = AMOUNT.match(m.group("amt").strip())
        if not amt:
            continue
        if m.start() <= 4 and re.match(r"\s*(?:is|are|was)\b", t[m.start():], re.I):
            continue  # "Is ₹45,000 at 6% ...": the question's first number, not an answer
        v = _dec(amt.group("num"))
        if amt.group("scale"):
            v *= SCALES[amt.group("scale").lower()]
        lead = len(m.group("amt")) - len(m.group("amt").lstrip())
        found = {"value": v, "start": m.start("amt") + lead, "end": m.end(), "text": m.group("amt").strip()}
    return found


def _numbers_outside(t: str, spans: list) -> list[dict]:
    return [a for a in find_amounts(t) if not any(a["start"] < e and a["end"] > s for s, e in spans)]


PEOPLE_WORDS = r"(?:people|persons|friends|of\s+us|members|ways|guys|roommates|flatmates|colleagues)"


def _people_match(t: str):
    return (re.search(r"\b(?:among|between|amongst)\s+(?:the\s+)?(\d+)\b(?!\s*(?:km|kg|%|rs|₹|\$))", t, re.I) or
            re.search(rf"\b(?:by|into|with|for)\s+(?:the\s+)?(\d+)\s*{PEOPLE_WORDS}\b", t, re.I) or
            re.search(rf"\b(\d+)\s*{PEOPLE_WORDS}\b", t, re.I) or
            re.search(r"\bwe\s+(?:were|are)\s+(\d+)\b", t, re.I))


def _people(t: str) -> int | None:
    m = _people_match(t)
    if m:
        n = int(m.group(1))
        if re.match(r"(?:with)\s", m.group(0), re.I) and re.search(r"friends|others|people|colleagues|guys|"
                                                                    r"roommates|flatmates", m.group(0), re.I):
            n += 1  # "split it with 2 friends": you and two friends
        return n
    m = re.search(r"\b(?:among|between|with)\s+((?:[a-z]+\s*,\s*)*[a-z]+\s+and\s+[a-z]+)\b", t, re.I)
    if m:
        names = [n for n in re.split(r"\s*,\s*|\s+and\s+", m.group(1)) if n]
        return len(names)
    return None


def _tenure_months(t: str):
    m = DURATION_RX.search(t)
    if not m:
        return None, None
    n, u = Decimal(m.group("n")), m.group("u").lower()
    months = n * 12 if u.startswith("y") else (n if u.startswith("m") else None)
    return months, (m.start(), m.end())


def _years(t: str):
    m = DURATION_RX.search(t)
    if not m:
        return None, None
    n, u = Decimal(m.group("n")), m.group("u").lower()
    if u.startswith("y"):
        return n, (m.start(), m.end())
    if u.startswith("m"):
        return n / 12, (m.start(), m.end())
    if u.startswith("w"):
        return n * 7 / 365, (m.start(), m.end())
    return n / 365, (m.start(), m.end())


def _spans(*xs):
    return [x for x in xs if x]


def _pc_list(t: str) -> list[Decimal]:
    return [Decimal(m.group("num")) for m in PERCENT.finditer(t)]


def _pc_spans(t: str) -> list:
    return [(m.start(), m.end()) for m in PERCENT.finditer(t)]


def _main_amount(t: str, exclude: list, prefer: str = "largest"):
    xs = [a for a in _numbers_outside(t, exclude) if not a["percent"]]
    if not xs:
        return None
    if prefer == "first":
        return xs[0]
    return max(xs, key=lambda a: a["value"])


def p_settle(t, low):
    pays = list(re.finditer(rf"\b(?P<name>[a-z][a-z'.]{{0,20}})\s+(?:has\s+)?(?:paid|spent|gave|put\s+in|"
                            rf"contributed|covered)\s+(?:for\s+\w+\s+)?(?P<amt>(?:{CUR_RX})?\s*(?:{NUM_RX})\s*"
                            rf"(?:{SCALE_RX})?(?:\s*(?:{CUR_RX}))?)", low, re.I))
    if len(pays) < 2 and not (pays and re.search(r"\b(?:didn'?t|did\s+not|hasn'?t|never)\s+pay|paid\s+nothing", low)):
        return None
    paid = {}
    for m in pays:
        name = m.group("name")
        if name in ("and", "who", "we", "they", "he", "she", "it", "have", "has", "had"):
            continue
        a = AMOUNT.match(m.group("amt").strip())
        v = _dec(a.group("num")) * (SCALES[a.group("scale").lower()] if a.group("scale") else 1)
        who = "You" if name in ("i", "me", "myself") else name.capitalize()
        paid[who] = paid.get(who, D0) + v
    for m in re.finditer(r"\b(?P<name>[a-z]+)\s+(?:didn'?t\s+pay|did\s+not\s+pay|paid\s+nothing|hasn'?t\s+paid|paid\s+0)"
                         r"\b", low):
        who = "You" if m.group("name") in ("i", "me") else m.group("name").capitalize()
        paid.setdefault(who, D0)
    n = _people(low)
    people = []
    if n and n > len(paid):
        people = [f"Person {i}" for i in range(len(paid) + 1, n + 1)]
    if len(paid) + len(people) < 2:
        return None
    return "settle_up", {"paid": {k: str(v) for k, v in paid.items()}, "people": people, "cur": currency_of(low)}


def p_split(t, low):
    if not re.search(r"\b(?:split|divide[ds]?|share[ds]?|each)\b", low):
        return None
    n = _people(low)
    if not n:
        return None
    claim = _claim(low)
    exclude = _spans((claim["start"], claim["end"]) if claim else None)
    pm = _people_match(low)
    if pm:
        exclude.append(pm.span())
    amt = _main_amount(low, exclude)
    if not amt:
        return None
    return "split_equally", {"total": str(amt["value"]), "people": n, "claimed": _cstr(claim),
                             "cur": amt["currency"] or currency_of(low)}


def _cstr(claim):
    return None if claim is None else str(claim["value"])


def p_tip(t, low):
    if not re.search(r"\btips?\b|\bgratuity\b", low):
        return None
    pcs = _pc_list(low)
    if not pcs:
        return None
    claim = _claim(low)
    exclude = _pc_spans(low) + _spans((claim["start"], claim["end"]) if claim else None)
    pm = _people_match(low)
    if pm:
        exclude.append(pm.span())
    amt = _main_amount(low, exclude)
    if not amt:
        return None
    return "tip", {"bill": str(amt["value"]), "rate": str(pcs[0]), "people": _people(low) or 1,
                   "claimed": _cstr(claim), "cur": amt["currency"] or currency_of(low)}


def p_loan(t, low):
    if not re.search(r"\b(?:emi|loan|mortgage|instal+ments?)\b", low):
        return None
    pcs = _pc_list(low)
    months, tspan = _tenure_months(low)
    if not pcs or months is None:
        return None
    claim = None
    m = re.search(rf"\b(?:emi|instal+ment|monthly\s+payment)\s*(?:is|of|=|will\s+be|comes\s+to|:|would\s+be|should\s+"
                  rf"be|amount\s+is)\s*(?:only\s+|about\s+|around\s+)?(?P<amt>(?:{CUR_RX})?\s*(?:{NUM_RX})\s*"
                  rf"(?:{SCALE_RX})?)", low)
    if m and not re.match(r"\s*(?:%|years?|months?)", low[m.end():]):
        a = AMOUNT.match(m.group("amt").strip())
        claim = {"value": _dec(a.group("num")) * (SCALES[a.group("scale").lower()] if a.group("scale") else 1),
                 "start": m.start("amt"), "end": m.end("amt")}
    if claim is None:
        claim = _claim(low)
    exclude = _pc_spans(low) + _spans(tspan, (claim["start"], claim["end"]) if claim else None)
    amt = _main_amount(low, exclude)
    if not amt:
        return None
    return "loan_emi", {"principal": str(amt["value"]), "annual_rate": str(pcs[0]), "months": int(months),
                        "claimed": _cstr(claim), "cur": amt["currency"] or "₹"}


def p_sip(t, low):
    if not re.search(r"\bsip\b|\bmonthly\s+(?:investment|saving)|invest(?:ing)?\s+\S+\s+(?:per|every|a|each)\s+month",
                     low):
        return None
    pcs = _pc_list(low)
    years, tspan = _years(low)
    if not pcs or years is None:
        return None
    claim = _claim(low)
    exclude = _pc_spans(low) + _spans(tspan, (claim["start"], claim["end"]) if claim else None)
    amt = _main_amount(low, exclude, prefer="first")
    if not amt:
        return None
    return "sip", {"monthly": str(amt["value"]), "annual_rate": str(pcs[0]), "years": str(years),
                   "claimed": _cstr(claim), "cur": amt["currency"] or "₹"}


def p_interest(t, low):
    fd = re.search(r"\b(?:fd|fixed\s+deposit|term\s+deposit)\b", low)
    kind = ("compound" if re.search(r"\bcompound|\bcompounded|\bcompounding|\bci\b", low) else
            "simple" if re.search(r"\bsimple\s+interest|\bsi\b", low) else "fd" if fd else
            "simple" if re.search(r"\binterest\b", low) else None)
    if not kind:
        return None
    pcs = _pc_list(low)
    years, tspan = _years(low)
    if not pcs or years is None:
        return None
    claim = _claim(low)
    exclude = _pc_spans(low) + _spans(tspan, (claim["start"], claim["end"]) if claim else None)
    amt = _main_amount(low, exclude)
    if not amt:
        return None
    wants_amount = bool(re.search(r"\b(?:amount|maturity|matures?|total|grow|grows|become|becomes|get\s+back|"
                                  r"final\s+value|worth)\b", low))
    cur = amt["currency"] or "₹"
    if kind == "simple":
        return "simple_interest", {"principal": str(amt["value"]), "annual_rate": str(pcs[0]), "years": str(years),
                                   "claimed": _cstr(claim), "cur": cur,
                                   "claim_is": "amount" if wants_amount else "interest"}
    per = 4 if kind == "fd" else 1
    for word, k in FREQ.items():
        if re.search(rf"\b{word}\b", low):
            per = k
    return "compound_interest", {"principal": str(amt["value"]), "annual_rate": str(pcs[0]), "years": str(years),
                                 "per_year": per, "claimed": _cstr(claim), "cur": cur,
                                 "claim_is": "amount" if (wants_amount or kind == "fd") else "interest",
                                 "label": f"an FD of {money(amt['value'], cur)} at {pct(pcs[0])} for "
                                          f"{_years_text(years)}" if kind == "fd" else ""}


TAX_RX = r"(?:gst|tax|vat|cgst|sgst|igst|sales\s+tax)"


def p_tax(t, low):
    if not re.search(rf"\b{TAX_RX}\b", low):
        return None
    pcs = _pc_list(low)
    if not pcs:
        return None
    name = re.search(rf"\b({TAX_RX})\b", low).group(1)
    claim = _claim(low)
    exclude = _pc_spans(low) + _spans((claim["start"], claim["end"]) if claim else None)
    amt = _main_amount(low, exclude)
    if not amt:
        return None
    cur = amt["currency"] or currency_of(low)
    asks_tax = bool(re.search(rf"\bhow\s+much\s+(?:is\s+|will\s+be\s+|would\s+be\s+)?(?:the\s+)?(?:\d+(?:\.\d+)?\s*%\s*)?"
                              rf"{TAX_RX}(?!\s+(?:included|inclusive))|\b(?:\d+(?:\.\d+)?\s*%\s*)?{TAX_RX}\s+amount\b|\b{TAX_RX}\s+(?:amount|is|comes|will\s+be|would\s+be)\b|"
                              rf"\bthe\s+{TAX_RX}\b(?!\s+(?:of|at|@))", low))
    inclusive = re.search(rf"\b(?:incl(?:uding|usive|\.)?|inclusive\s+of|with\s+{TAX_RX}\s+included|after\s+{TAX_RX})\b"
                          rf"|\bbefore\s+{TAX_RX}\b|\bwithout\s+{TAX_RX}\b|\bexcluding\s+{TAX_RX}\b|\bbase\s+price\b|"
                          rf"\bremove\s+{TAX_RX}|\bexclusive\b", low)
    if inclusive:
        return "remove_tax", {"total": str(amt["value"]), "rate": str(pcs[0]), "claimed": _cstr(claim), "cur": cur,
                              "tax_name": name, "claim_is": "tax" if asks_tax else "base"}
    return "add_tax", {"base": str(amt["value"]), "rate": str(pcs[0]), "claimed": _cstr(claim), "cur": cur,
                       "tax_name": name, "claim_is": "tax" if asks_tax else "total"}


def p_discount(t, low):
    if not re.search(r"\boff\b|\bdiscount|\bsale\b", low):
        return None
    claim = _claim(low)
    pcs = [Decimal(m.group("num")) for m in PERCENT.finditer(low)
           if not (claim and m.start() >= claim["start"] and m.end() <= claim["end"] + 2)]
    amount_off = None
    exclude = _pc_spans(low) + _spans((claim["start"], claim["end"]) if claim else None)
    m = re.search(rf"(?P<amt>(?:{CUR_RX})\s*(?:{NUM_RX})|(?:{NUM_RX})\s*(?:{CUR_RX}))\s*(?:flat\s+)?off\b", low)
    if not pcs and m:
        a = AMOUNT.match(m.group("amt").strip())
        amount_off = _dec(a.group("num"))
        exclude.append((m.start("amt"), m.end("amt")))
    if not pcs and amount_off is None:
        return None
    amt = _main_amount(low, exclude)
    if not amt:
        return None
    saving = bool(re.search(r"\b(?:save|saving|savings|discount\s+(?:amount|is|of\s+₹)|how\s+much\s+(?:off|less|do\s+i\s+"
                            r"save))\b", low))
    return "discount", {"price": str(amt["value"]), "rates": [str(x) for x in pcs], "amount_off":
                        None if amount_off is None else str(amount_off), "claimed": _cstr(claim),
                        "cur": amt["currency"] or currency_of(low), "claim_is": "saving" if saving else "final"}


def p_change(t, low):
    m = re.search(rf"\b(?:from|was|went\s+from|rose\s+from|fell\s+from|increased\s+from|decreased\s+from|dropped\s+from)"
                  rf"\s+(?P<a>(?:{CUR_RX})?\s*(?:{NUM_RX})\s*(?:{SCALE_RX})?)\s+(?:to|now|became|is\s+now|and\s+now)\s+"
                  rf"(?P<b>(?:{CUR_RX})?\s*(?:{NUM_RX})\s*(?:{SCALE_RX})?)", low)
    if not m or not re.search(r"%|percent|per\s*cent|increase|decrease|rise|rose|fall|fell|drop|change|grew|growth|"
                              r"hike|cut", low):
        return None
    a, b = AMOUNT.match(m.group("a").strip()), AMOUNT.match(m.group("b").strip())

    def val(x):
        return _dec(x.group("num")) * (SCALES[x.group("scale").lower()] if x.group("scale") else 1)
    claim = _claim(low[m.end():], percent=True)
    if claim is None:
        pm = re.search(r"(\d+(?:\.\d+)?)\s*%", low[m.end():])
        claim = {"value": Decimal(pm.group(1))} if pm else None
    return "percent_change", {"old": str(val(a)), "new": str(val(b)), "claimed": _cstr(claim),
                              "cur": _cur(a.group("c1")) or currency_of(low)}


def p_what_percent(t, low):
    if not re.search(r"%|percent|per\s*cent|percentage|marks|score|scored", low):
        return None
    m = (re.search(rf"(?P<a>{NUM_RX})\s*(?:marks\s+)?(?:out\s+of|/|of)\s*(?P<b>{NUM_RX})", low) or
         re.search(rf"(?P<a>{NUM_RX})\s+is\s+what\s+(?:percent|percentage|%)\s+of\s+(?P<b>{NUM_RX})", low))
    if not m:
        m2 = re.search(rf"what\s+(?:percent|percentage|%)\s+(?:of\s+)?(?P<b>{NUM_RX})\s+is\s+(?P<a>{NUM_RX})", low)
        if not m2:
            return None
        m = m2
    if re.search(r"\d\s*%\s*of", low[max(0, m.start() - 2):m.end()]):
        return None  # "15% of 240" is a percentage of an amount
    claim = _claim(low[m.end():], percent=None)
    if claim is None:
        pm = re.search(r"(\d+(?:\.\d+)?)\s*%", low[m.end():])
        claim = {"value": Decimal(pm.group(1))} if pm else None
    return "what_percent", {"part": str(_dec(m.group("a"))), "whole": str(_dec(m.group("b"))),
                            "claimed": _cstr(claim)}


def p_change_by(t, low):
    m = re.search(rf"\b(?P<dir>increase|raise|grow|decrease|reduce|cut|lower)\s+(?P<amt>(?:{CUR_RX})?\s*(?:{NUM_RX})\s*"
                  rf"(?:{SCALE_RX})?)\s+by\s+(?P<pc>\d+(?:\.\d+)?)\s*%", low) or \
        re.search(rf"(?P<amt>(?:{CUR_RX})?\s*(?:{NUM_RX})\s*(?:{SCALE_RX})?)\s*(?:(?P<dir>increased|raised|"
                  rf"decreased|reduced|cut)\s+by|(?P<sign>[+-]))\s*(?P<pc>\d+(?:\.\d+)?)\s*%", low)
    if not m:
        return None
    a = AMOUNT.match(m.group("amt").strip())
    up = not ((m.groupdict().get("dir") or "").startswith(("decrea", "reduc", "cut", "lower")) or
              m.groupdict().get("sign") == "-")
    claim = _claim(low[m.end():])
    return "change_by", {"base": str(_dec(a.group("num")) * (SCALES[a.group("scale").lower()] if a.group("scale")
                                                              else 1)),
                         "rate": m.group("pc"), "up": up, "claimed": _cstr(claim),
                         "cur": _cur(a.group("c1")) or _cur(a.group("c2")) or currency_of(low)}


def p_percent_of(t, low):
    m = re.search(rf"(?P<pc>\d+(?:\.\d+)?)\s*(?:%|percent|per\s*cent)\s+of\s+(?P<amt>(?:{CUR_RX})?\s*(?:{NUM_RX})\s*"
                  rf"(?:{SCALE_RX})?(?:\s*(?:{CUR_RX}))?)", low)
    if not m:
        return None
    a = AMOUNT.match(m.group("amt").strip())
    whole = _claim(low)
    claim = whole if whole and whole["start"] >= m.end("amt") - 1 and whole["text"] != m.group("amt").strip() \
        else _claim(low[m.end():])
    return "percent_of", {"rate": m.group("pc"),
                          "base": str(_dec(a.group("num")) * (SCALES[a.group("scale").lower()] if a.group("scale")
                                                              else 1)),
                          "claimed": _cstr(claim), "cur": _cur(a.group("c1")) or _cur(a.group("c2")) or currency_of(low)}


def _num_list(s: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", s)


def p_gpa(t, low):
    label = re.search(r"\b(sgpa|cgpa|gpa|spi|cpi)\b", low)
    if not label:
        return None
    lab = label.group(1).upper()
    cm = re.search(r"\bcredits?\s*(?:are|:|=|of|-)?\s*(?P<l>\d+(?:\.\d+)?(?:\s*(?:,|and|&|\s)\s*\d+(?:\.\d+)?)*)", low)
    gm = re.search(r"\b(?:grade\s*points?|grades?|points?|gps?|pointers?|sgpas?|marks)\s*(?:are|:|=|of|-)?\s*"
                   r"(?P<l>\d+(?:\.\d+)?(?:\s*(?:,|and|&|\s)\s*\d+(?:\.\d+)?)*)", low)
    claim = _claim(low[max(cm.end() if cm else 0, gm.end() if gm else 0):]) if (cm or gm) else None
    if cm and gm:
        return "gpa", {"credits": _num_list(cm.group("l")), "points": _num_list(gm.group("l")),
                       "claimed": _cstr(claim), "label": lab}
    if gm and lab == "CGPA":
        pts = _num_list(gm.group("l"))
        return "gpa", {"credits": ["1"] * len(pts), "points": pts, "claimed": _cstr(claim), "label": lab,
                       "notes": ["Each semester is counted equally; give the credits too for an exact CGPA."]}
    return None


def p_fuel(t, low):
    if not re.search(r"\b(?:fuel|petrol|diesel|cng|mileage|kmpl|km\s*/\s*l|km\s+per\s+lit(?:re|er)|per\s+lit(?:re|er)|"
                     r"/\s*l(?:itre)?\b)", low):
        return None
    dm = re.search(r"(?P<n>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<u>km|kms|kilometres?|kilometers?|miles?)\b(?!\s*(?:/|per)\s*l)",
                   low)
    km = (re.search(r"(?P<n>\d+(?:\.\d+)?)\s*(?:km\s*/\s*l(?:itre|iter)?|kmpl|km\s+per\s+lit(?:re|er)|kilomet(?:re|er)s?"
                    r"\s+per\s+lit(?:re|er))", low) or
          re.search(r"mileage\s*(?:is|of|:|=)?\s*(?P<n>\d+(?:\.\d+)?)", low))
    pm = (re.search(rf"(?P<amt>(?:{CUR_RX})?\s*(?:{NUM_RX}))\s*(?:{CUR_RX})?\s*(?:/\s*l(?:itre|iter)?|per\s+lit(?:re|er)|"
                    rf"a\s+lit(?:re|er)|each\s+lit(?:re|er))", low) or
          re.search(rf"(?:petrol|diesel|fuel|cng)\s*(?:is|at|costs?|price|@|:)\s*(?P<amt>(?:{CUR_RX})?\s*(?:{NUM_RX}))",
                    low))
    if not (dm and km and pm):
        return None
    a = AMOUNT.match(pm.group("amt").strip())
    claim = _claim(low[max(dm.end(), km.end(), pm.end()):])
    unit = "miles" if dm.group("u").startswith("mile") else "km"
    return "fuel_cost", {"distance": str(_dec(dm.group("n"))), "mileage": km.group("n"), "price": str(_dec(a.group("num"))),
                         "people": _people(low) or 1, "claimed": _cstr(claim),
                         "cur": _cur(a.group("c1")) or currency_of(low) or "₹", "unit": unit}


def _duration_minutes(s: str):
    m = re.search(r"(?:(?P<h>\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b)?\s*(?:and\s+)?(?:(?P<m>\d+)\s*(?:minutes?|mins?|m)\b)?",
                  s, re.I)
    if m and (m.group("h") or m.group("m")):
        return int((Decimal(m.group("h") or 0) * 60 + Decimal(m.group("m") or 0)).quantize(Decimal(1))), m
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*(?:hours?|hrs?|h)\b", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2)), m
    return None, None


def p_trip(t, low):
    sm = re.search(r"(?P<n>\d+(?:\.\d+)?)\s*(?P<u>km\s*/\s*h(?:r|our)?|kmph|kph|km\s+per\s+hour|kilomet(?:re|er)s\s+per\s+"
                   r"hour|mph|miles\s+per\s+hour)", low)
    dm = re.search(r"(?P<n>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<u>km|kms|kilomet(?:re|er)s?|miles?)\b(?!\s*(?:/|per)\s*h|ph)",
                   low)
    rest = low
    for m in (sm, dm):
        if m:
            rest = rest[:m.start()] + " " * (m.end() - m.start()) + rest[m.end():]
    claim_m = re.search(r"(?:\bis\b|=|\btakes?\b|\bwill\s+take\b|\bshould\s+take\b|\bin\b|[,;])\s*(?:about\s+|around\s+)?"
                        r"(?P<d>\d+(?:\.\d+)?\s*(?:hours?|hrs?|h)\b(?:\s*(?:and\s+)?\d+\s*(?:minutes?|mins?))?|\d+\s*"
                        r"(?:minutes?|mins?)\b)\s*\??\s*$", rest)
    hm = re.search(r"(?P<d>\d+(?:\.\d+)?\s*(?:hours?|hrs?|h)\b(?:\s*(?:and\s+)?\d+\s*(?:minutes?|mins?))?|\d+\s*"
                   r"(?:minutes?|mins?)\b)", rest)
    unit = "miles" if (sm and "mi" in sm.group("u")) or (dm and dm.group("u").startswith("mile")) else "km"
    if sm and dm:
        claimed = text = None
        if claim_m:
            claimed, _ = _duration_minutes(claim_m.group("d"))
            text = claim_m.group("d").strip()
        return "trip", {"distance": str(_dec(dm.group("n"))), "speed": sm.group("n"), "claimed": claimed,
                        "claimed_text": text, "unit": unit}
    if (dm and hm and not sm) and re.search(r"\b(?:speed|fast|average|avg)\b", low):
        mins, _ = _duration_minutes(hm.group("d"))
        claim = _claim(low[hm.end():] if hm.end() < len(low) else "")
        return "trip", {"distance": str(_dec(dm.group("n"))), "hours": str(Decimal(mins) / 60),
                        "claimed": _cstr(claim), "claimed_text": None if claim is None else claim["text"], "unit": unit}
    if (sm and hm and not dm) and re.search(r"\b(?:distance|far|how\s+many\s+(?:km|kilomet|miles))\b", low):
        mins, _ = _duration_minutes(hm.group("d"))
        return "trip", {"speed": sm.group("n"), "hours": str(Decimal(mins) / 60), "unit": unit}
    return None


def p_recipe(t, low):
    if not re.search(r"\brecipe\b|\bserves?\b|\bservings?\b|\bscale\b|\bportions?\b", low):
        return None
    m = re.search(r"\bfrom\s+(?P<a>\d+(?:\.\d+)?)\s*(?:people|persons|servings?|portions?)?\s+to\s+(?P<b>\d+(?:\.\d+)?)",
                  low) or re.search(r"\b(?:serves|for)\s+(?P<a>\d+)\b.*?\b(?:for|to|make\s+it\s+for|need\s+it\s+for|"
                                    r"scale\s+(?:it\s+)?to)\s+(?P<b>\d+)\s*(?:people|persons|servings?|portions?|guests|"
                                    r"(?=[.?!]*\s*$))", low, re.S)
    if not m:
        return None
    body = t[t.find(":") + 1:] if ":" in t else t
    for cut in re.finditer(r"\b(?:serves|for|from|to|make\s+it\s+for|need\s+it\s+for|scale\s+(?:it\s+)?to)\s+\d+\s*"
                           r"(?:people|persons|servings?|portions?|guests)?", body, re.I):
        body = body.replace(cut.group(0), ",", 1)
    items = []
    for x in re.split(r"[,;\n]|\band\b", body):
        x = re.sub(r"^\s*(?:a\s+)?(?:recipe|it|this)?\s*(?:needs|uses|takes|calls\s+for|with|has|is)?\s*:?", "", x,
                   flags=re.I).strip(" .?!")
        if x and ING.match(x):
            items.append(x)
    if not items:
        return None
    return "scale_recipe", {"ingredients": items, "from_servings": m.group("a"), "to_servings": m.group("b")}


def _dates_in(low):
    return find_dates(low)


def _date_notes(ds):
    return [d["note"] for d in ds if d["note"]]


def p_age(t, low):
    if not re.search(r"\b(?:age|old|born|birthday|dob|date\s+of\s+birth)\b", low):
        return None
    ds = _dates_in(low)
    if not ds:
        return None
    key = re.search(r"\b(?:born|dob|date\s+of\s+birth|birthday)\b", low)
    born = min(ds, key=lambda d: abs(d["start"] - key.end())) if key else ds[0]
    others = [d for d in ds if d is not born]
    on = others[0]["date"].isoformat() if others else None
    cm = (re.search(r"\b(?:is|am|turns?|turned|was|be)\s+(\d{1,3})\s*(?:years?|yrs?)?(?:\s+old)?\s*\??\s*$", low) or
          re.search(r"\b(?:am\s+i|i'?m|i\s+am|is\s+(?:he|she|it))\s+(\d{1,3})\b", low))
    return "age", {"born": born["date"].isoformat(), "on": on, "claimed": cm.group(1) if cm else None,
                   "notes": _date_notes(ds)}


def p_weekday(t, low):
    claim = re.search(r"\b(?:was|is|falls?\s+on|fell\s+on|will\s+be)\s+(?:a\s+)?(?P<w>monday|tuesday|wednesday|thursday|"
                      r"friday|saturday|sunday)\b", low)
    if not (claim or re.search(r"\bwhat\s+day\b|\bwhich\s+day\b|\bday\s+of\s+(?:the\s+)?week\b|\bweekday\b", low)):
        return None
    ds = _dates_in(low)
    if len(ds) != 1:
        return None
    return "weekday", {"date": ds[0]["date"].isoformat(), "claimed": claim.group("w") if claim else None,
                       "notes": _date_notes(ds)}


def p_add_days(t, low):
    m = re.search(r"\b(?P<dir>add|subtract)\s+(?P<n>\d+)\s*(?P<u>days?|weeks?|months?|years?)\s+(?:to|from)\s+(?P<rest>.+)$",
                  low) or \
        re.search(r"(?P<n>\d+)\s*(?P<u>days?|weeks?|months?|years?)\s+(?P<dir>after|from|before|since|later\s+than|prior"
                  r"\s+to|past)\s+(?P<rest>.+)$", low) or \
        re.search(r"(?P<rest>.+?)\s*(?P<dir>plus|\+|minus|-|add|after)\s*(?P<n>\d+)\s*(?P<u>days?|weeks?|months?|years?)",
                  low)
    if not m:
        return None
    rest = m.group("rest")
    ds = find_dates(rest)
    if not ds and re.search(r"\b(?:now|today)\b", rest):
        ds = [{"date": today(), "start": 0, "end": 0, "note": None}]
    if not ds:
        return None
    n = int(m.group("n")) * (-1 if m.group("dir") in ("before", "prior to", "minus", "-", "subtract") else 1)
    u = m.group("u").lower()
    unit = "days" if u.startswith("d") else "weeks" if u.startswith("w") else "months" if u.startswith("m") else "years"
    claimed = None
    after = rest[ds[0]["end"]:] if ds[0]["end"] else ""
    later = find_dates(after) if after else []
    if later and re.search(r"\b(?:is|=|on|be)\b", after):
        claimed = later[0]["date"].isoformat()
    return "add_days", {"start": ds[0]["date"].isoformat(), unit: n, "claimed": claimed, "notes": _date_notes(ds)}


def p_days_between(t, low):
    if not re.search(r"\bdays?\b|\bweeks?\b|\bhow\s+long\b", low):
        return None
    ds = _dates_in(low)
    if not ds:
        return None
    until = re.search(r"\b(?:until|till|to|before|left|remaining|away|more)\b", low)
    since = re.search(r"\b(?:since|ago|passed|have\s+passed)\b", low)
    if len(ds) >= 2:
        a, b = ds[0]["date"], ds[1]["date"]
    elif until and not since:
        a, b = today(), ds[0]["date"]
    elif since:
        a, b = ds[0]["date"], today()
    else:
        return None
    covered = [(d["start"], d["end"]) for d in ds]
    rest = "".join(ch if not any(s <= i < e for s, e in covered) else " " for i, ch in enumerate(low))
    claim = _claim(rest)
    inclusive = bool(re.search(r"\b(?:including|inclusive|both\s+days|counting\s+both)\b", low))
    return "days_between", {"start": a.isoformat(), "end": b.isoformat(), "claimed": _cstr(claim),
                            "inclusive": inclusive, "notes": _date_notes(ds)}


ZONE_RX = "|".join(re.escape(z) for z in sorted(ZONES, key=len, reverse=True))


def p_time_zone(t, low):
    m = re.search(rf"(?P<time>\d{{1,2}}(?:[:.]\d{{2}})?\s*(?:a\.?m\.?|p\.?m\.?)?)\s*(?:in\s+)?(?P<a>{ZONE_RX})\b.*?"
                  rf"\b(?:in|to|for|at)\s+(?P<b>{ZONE_RX})\b", low) or \
        re.search(rf"\bwhat\s+time\s+(?:is\s+it\s+)?in\s+(?P<b>{ZONE_RX})\b.*?\b(?P<time>\d{{1,2}}(?:[:.]\d{{2}})?\s*"
                  rf"(?:a\.?m\.?|p\.?m\.?)?)\s*(?:in\s+)?(?P<a>{ZONE_RX})\b", low)
    if not m:
        return None
    try:
        parse_time(m.group("time"))
    except ValueError:
        return None
    ds = find_dates(low)
    return "convert_time_zone", {"time": m.group("time").strip(), "from_zone": m.group("a"), "to_zone": m.group("b"),
                                 "on": ds[0]["date"].isoformat() if ds else None}


def p_time_between(t, low):
    times = [m for m in TIME_RX.finditer(low) if m.group("m") or m.group("ap")]
    if len(times) < 2 or not re.search(r"\b(?:from|between|to|till|until|and|how\s+long|duration|hours?)\b", low):
        return None
    try:
        a, b = parse_time(times[0].group(0)), parse_time(times[1].group(0))
    except ValueError:
        return None
    rest = low[times[1].end():]
    mins, _ = _duration_minutes(rest) if re.search(r"\b(?:is|=|that's|thats)\b", rest) else (None, None)
    return "time_between", {"start": show_time(a), "end": show_time(b), "claimed_minutes": mins,
                            "claimed_text": _hm(mins) if mins is not None else None}


def p_convert(t, low):
    low = re.sub(r"(\d)\s*°\s*([cf])\b", r"\1 \2", low)
    low = re.sub(r"\bdeg(?:ree)?s?\s+(celsius|centigrade|fahrenheit|c|f)\b", r"\1", low)
    num = r"(?P<v>(?<![\w.])-?\d+(?:,\d{3})*(?:\.\d+)?|\ba\b|\ban\b|\bone\b)"
    fi = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:feet|foot|ft|')\s*(?:and\s+)?(\d+(?:\.\d+)?)\s*(?:inches|inch|in\b|\")",
                   low)
    if fi:  # 5 feet 8 inches: one length in inches
        inches = Decimal(fi.group(1)) * 12 + Decimal(fi.group(2))
        low = low[:fi.start()] + f"{inches} inches" + low[fi.end():]
    pats = [rf"\bhow\s+many\s+(?P<u2>{UNIT_RX})\s+(?:are\s+)?(?:there\s+)?(?:in|is|make)\s+{num}\s*(?P<u>{UNIT_RX})\b",
            rf"{num}\s*(?P<u>{UNIT_RX})(?![a-z])\s*(?:is|=|equals?|equal\s+to|makes?)\s*(?P<c>-?\d+(?:,\d{{3}})*(?:\.\d+)?)\s*"
            rf"(?P<u2>{UNIT_RX})\b",
            rf"{num}\s*(?P<u>{UNIT_RX})\s+(?:in|to|into|in\s+terms\s+of|as)\s+(?P<u2>{UNIT_RX})\b"]
    for p in pats:
        m = re.search(p, low)
        if not m:
            continue
        u, u2 = m.group("u"), m.group("u2")
        if u == u2:
            continue
        v = m.group("v")
        v = "1" if v in ("a", "an", "one") else v.replace(",", "")
        claimed = m.groupdict().get("c")
        if claimed is None:
            c = _claim(low[m.end():])
            claimed = _cstr(c)
        return "convert_units", {"value": v, "from_unit": u, "to_unit": u2,
                                 "claimed": claimed.replace(",", "") if claimed else None}
    return None


PARSERS = [p_settle, p_fuel, p_tip, p_split, p_loan, p_sip, p_interest, p_tax, p_discount, p_change, p_change_by,
           p_what_percent, p_percent_of, p_gpa, p_trip, p_recipe, p_time_zone, p_age, p_weekday, p_add_days,
           p_days_between, p_time_between, p_convert]


def understand_everyday(text: str):
    """(calculator, arguments) for an everyday request the calculators can answer, else None."""
    t = _norm(text)
    low = t.lower()
    for p in PARSERS:
        try:
            r = p(t, low)
        except (ValueError, ArithmeticError, KeyError, IndexError, AttributeError):
            r = None
        if r:
            return r
    return None


CALCULATORS: dict[str, tuple[Callable[..., dict], str]] = {
    "percent_of": (percent_of, "X% of an amount: rate, base, [claimed], [cur]"),
    "discount": (discount, "price after discounts applied one after another: price, rates [list], [amount_off], "
                           "[claimed], [cur], [claim_is final|saving]"),
    "add_tax": (add_tax, "add GST / tax to a price: base, rate, [claimed], [cur], [tax_name], [claim_is total|tax]"),
    "remove_tax": (remove_tax, "price before tax from a tax-inclusive price: total, rate, [claimed], [cur], [tax_name],"
                               " [claim_is base|tax]"),
    "percent_change": (percent_change, "percentage increase or decrease: old, new, [claimed percent], [cur]"),
    "what_percent": (what_percent, "part as a percentage of a whole (marks): part, whole, [claimed percent]"),
    "change_by": (change_by, "increase or decrease an amount by X%: base, rate, up (true|false), [claimed], [cur]"),
    "tip": (tip, "tip and split: bill, rate, [people], [claimed], [cur]"),
    "split_equally": (split_equally, "split an amount equally: total, people, [claimed share], [cur]"),
    "settle_up": (settle_up, "who owes whom: paid {name: amount}, [people who paid nothing], [cur]"),
    "loan_emi": (loan_emi, "monthly EMI on a loan: principal, annual_rate, months, [claimed], [cur]"),
    "simple_interest": (simple_interest, "simple interest: principal, annual_rate, years, [claimed], [cur], "
                                         "[claim_is interest|amount]"),
    "compound_interest": (compound_interest, "compound interest or an FD: principal, annual_rate, years, [per_year 1|2|"
                                             "4|12|365], [claimed], [cur], [claim_is amount|interest]"),
    "sip": (sip, "value of a monthly SIP: monthly, annual_rate, years, [claimed], [cur]"),
    "days_between": (days_between, "days between two dates: start, end (YYYY-MM-DD), [claimed], [inclusive]"),
    "add_days": (add_days, "a date plus or minus a time: start, [days], [weeks], [months], [years], [claimed date]"),
    "weekday": (weekday, "day of the week of a date: date, [claimed weekday]"),
    "age": (age, "age from a birth date: born, [on], [claimed years]"),
    "time_between": (time_between, "time between two clock times: start, end, [claimed_minutes]"),
    "convert_time_zone": (convert_time_zone, "a clock time in another time zone: time, from_zone, to_zone, [on]"),
    "convert_units": (convert_units, "unit conversion (length, mass, volume, temperature, speed, area, time, data, "
                                     "fuel economy): value, from_unit, to_unit, [claimed]"),
    "trip": (trip, "travel time, speed or distance from the other two: [distance], [speed], [hours], [claimed], [unit]"),
    "fuel_cost": (fuel_cost, "fuel cost of a trip: distance, mileage (km/l), price per litre, [people], [claimed], "
                             "[cur]"),
    "gpa": (gpa, "credit-weighted SGPA / CGPA: credits [list], points [list], [claimed], [label]"),
    "scale_recipe": (scale_recipe, "scale a recipe: ingredients [list like '200 g flour'], from_servings, to_servings"),
}


def calculate(name: str, args: dict) -> dict:
    """Run one calculator with plain arguments (from the built-in reader, a model or another program)."""
    if name not in CALCULATORS:
        return unreadable(name, f"unknown calculator {name!r}")
    fn = CALCULATORS[name][0]
    import inspect
    params = inspect.signature(fn).parameters
    kwargs = {k: v for k, v in (args or {}).items() if k in params and v is not None}
    notes = args.get("notes") if isinstance(args, dict) else None
    if "notes" in params and notes:
        kwargs["notes"] = notes
    try:
        res = fn(**kwargs)
    except (ValueError, TypeError, ArithmeticError, KeyError) as e:
        return unreadable(name, f"I couldn't work that out ({e}).")
    except Exception as e:  # a time zone name the system doesn't know, and the like
        return unreadable(name, f"I couldn't work that out ({type(e).__name__}: {e}).")
    if notes and "notes" not in params:
        res["notes"] = list(notes) + res["notes"]
    res["calculator"] = name if res["status"] != "unreadable" else res.get("calculator")
    return res


def check_everyday(text: str) -> dict:
    """Understand an everyday request and work it out exactly, or say it isn't one the calculators know."""
    found = understand_everyday(text)
    if not found:
        return unreadable("everyday", "That isn't a request I can work out exactly. I can do percentages, discounts, "
                                      "GST, tips, splitting bills, EMIs and interest, dates and ages, unit "
                                      "conversions, travel time and fuel, SGPA and recipes.")
    name, args = found
    res = calculate(name, args)
    res["understood_as"] = {"calculator": name, "args": {k: v for k, v in args.items() if k != "notes"}}
    return res


def looks_like_everyday(text: str) -> bool:
    return understand_everyday(text) is not None
