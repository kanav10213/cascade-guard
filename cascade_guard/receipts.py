"""Expense receipts: a real three-agent workflow, guarded at every hand-off.

Finance teams get piles of scanned shop receipts. A typical automated workflow:

    extract  read the receipt's text (usually from OCR): vendor, date, total, currency
    policy   apply the expense policy: approve, needs a manager's approval, duplicate, or reject
    ledger   write the ledger entry: vendor, ISO date, amount, currency, category, memo

Each step can be done by a rule-based agent (what a developer would write first) or an LLM agent. Real receipts
are messy: OCR noise, labels and values on different lines, cash, change, tax and rounding next to the total.
Agents get them wrong in ordinary ways (the cash paid read as the total, the subtotal before tax, a date in the
wrong format). Cascade Guard checks every hand-off against the receipt itself, not against an answer key:

  * the total must appear on the receipt, next to a total label, and agree with cash paid minus change and with
    a rounded total when the receipt has them; an amount that is the cash, the change or the tax is named as such
  * the date must be a real date that appears on the receipt and is not in the future
  * the vendor must be one of the receipt's first lines
  * the policy decision must follow the policy from the extracted values (recomputed exactly)
  * the ledger entry must carry the same vendor, date and amount, and a category from the chart of accounts

A failed check goes back to the agent with the reason (up to max_retries); a step that still fails stops the
receipt for a person instead of posting it.

    result = process_receipt(text)                       # rule agents
    result = process_receipt(text, client=make_client()) # LLM agents
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

STEPS = ("extract", "policy", "ledger")
APPROVAL_LIMIT = Decimal("200.00")      # above this, a manager approves
CATEGORIES = ("meals", "groceries", "stationery", "hardware", "fuel", "transport", "pharmacy", "electronics",
              "other")
POLICY = (f"Expense policy: reject a receipt with no positive total. A receipt already submitted (same vendor, date "
          f"and total) is a duplicate. A total above {APPROVAL_LIMIT} needs a manager's approval. Anything else is "
          f"approved.")

MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV",
                                      "DEC"], 1)}
MONTH_RX = r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*"
DATE_RX = re.compile(
    rf"(?<!\d)(?:(?P<d1>\d{{1,2}})[/\-.](?P<m1>\d{{1,2}})[/\-.](?P<y1>\d{{4}}|\d{{2}})"
    rf"|(?P<y2>\d{{4}})[/\-.](?P<m2>\d{{1,2}})[/\-.](?P<d2>\d{{1,2}})"
    rf"|(?P<d3>\d{{1,2}})[\s/\-.]*(?P<m3>{MONTH_RX})[\s/\-.,]*(?P<y3>\d{{4}}|\d{{2}})"
    rf"|(?P<m4>{MONTH_RX})[\s.]+(?P<d4>\d{{1,2}}),?\s*(?P<y4>\d{{4}})"
    rf"|(?P<y6>20\d{{2}})(?P<m6>[01]\d)(?P<d6>[0-3]\d)"
    rf"|(?P<d5>[0-3]\d)(?P<m5>[01]\d)(?P<y5>20\d{{2}}))(?!\d)", re.I)
MONEY_RX = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)[.,](\d{2})(?![\d])")
TOTAL_WORDS = re.compile(r"\b(?:TOTAL|TOTAL\s*\(RM\)|NETT|NET\s*TOTAL|GRAND\s*TOTAL|AMOUNT\s*DUE|AMT\s*DUE|TOTAL\s*AMOUNT|"
                         r"ROUND(?:ED|ING)?\s*TOTAL|TOTAL\s*ROUND(?:ED|ING)?|JUMLAH|TOTAL\s*SALES?|TOTAL\s*INCL\w*|"
                         r"TOTAL\s*PAYABLE|BALANCE\s*DUE|AMOUNT\s*PAYABLE|NET\s*AMOUNT)\b", re.I)
NOT_TOTAL = re.compile(r"\b(?:SUB\s*-?\s*TOTAL|TOTAL\s*(?:QTY|QUANTITY|ITEMS?|PCS|UNITS?|GST|TAX|SST|DISC\w*|SAV\w*|"
                       r"EXCL\w*|BEFORE)|ITEMS?\s*COUNT|NO\.?\s*OF\s*ITEMS?)\b", re.I)
KINDS = [("cash paid", re.compile(r"\b(?:CASH|TENDER\w*|PAID|PAYMENT|VISA|MASTER\w*|CARD|CREDIT|DEBIT|"
                                   r"E-?WALLET|BOOST|GRAB\s*PAY|TNG)\b", re.I)),
         ("change", re.compile(r"\bCHANGE\b|\bBAKI\b", re.I)),
         ("subtotal", re.compile(r"\bSUB\s*-?\s*TOTAL\b|\bTOTAL\s*(?:EXCL\w*|BEFORE)", re.I)),
         ("tax", re.compile(r"\b(?:GST|SST|TAX|SR\s*@|ZR\s*@|SERVICE\s*(?:CHARGE|TAX))\b", re.I)),
         ("discount", re.compile(r"\bDISC(?:OUNT)?\b|\bSAVING", re.I)),
         ("rounding adjustment", re.compile(r"\bROUND\w*\s*(?:ADJ\w*|OFF)\b|\bADJUSTMENT\b", re.I)),
         ("item count", re.compile(r"\b(?:QTY|QUANTITY|ITEMS?|PCS)\b", re.I))]
VENDOR_HINTS = re.compile(r"\b(?:SDN\.?\s*BHD|BHD|S/B|ENTERPRISE|TRADING|RESTAURANT|RESTORAN|STORE|MART|MARKET|"
                          r"BAKERY|CAFE|PHARMACY|HARDWARE|STATIONERY|BOOK|SHOP|KEDAI|PLT|CO\.|COMPANY|HOLDINGS|"
                          r"SUPERMARKET|HYPERMARKET|TRADERS?|STATION|MAMAK|KITCHEN|FOOD|LTD|LIMITED|INC)\b", re.I)
NOT_VENDOR = re.compile(r"\b(?:JALAN|JLN|TAMAN|NO\.|LOT|TEL|FAX|GST|REG|INVOICE|RECEIPT|TAX|CASHIER|DATE|"
                        r"WELCOME|THANK|PHONE|EMAIL|WWW|LORONG|BANDAR|SELANGOR|JOHOR|KUALA|PULAU)\b", re.I)
CATEGORY_WORDS = [
    ("meals", r"RESTAURANT|RESTORAN|CAFE|COFFEE|KOPI|FOOD|KITCHEN|BAKERY|BAKE|NASI|MAMAK|BISTRO|BURGER|PIZZA|"
              r"CHICKEN|TEA|DRINK|MEAL|DIM\s*SUM|CURRY|NOODLE|MEE"),
    ("groceries", r"MART|MARKET|SUPERMARKET|HYPERMARKET|GROCER|MINI\s*MARKET|FRESH|AEON|TESCO|GIANT|99\s*SPEED"),
    ("stationery", r"STATIONERY|BOOK|PAPER|PRINT|PHOTOSTAT|OFFICE"),
    ("hardware", r"HARDWARE|TOOLS?|BUILDING|ELECTRICAL|TRADING|ENGINEERING|PAINT|STEEL|BESI"),
    ("fuel", r"PETROL|PETRONAS|SHELL|CALTEX|BHP|PETRON|FUEL|STATION"),
    ("transport", r"TAXI|GRAB|PARKING|TOLL|TRANSPORT|BUS|TRAIN"),
    ("pharmacy", r"PHARMACY|FARMASI|CLINIC|KLINIK|HEALTH|GUARDIAN|WATSONS"),
    ("electronics", r"ELECTRONIC|COMPUTER|MOBILE|PHONE|GADGET"),
]


# ===================================================================== reading receipts

def lines_of(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").replace("\r", "").split("\n") if ln.strip()]


def money(value) -> Decimal | None:
    """'RM 1,234.50' / '9.00' / 9 -> Decimal with two places, else None."""
    if value is None or isinstance(value, bool):
        return None
    s = str(value).upper().replace("RM", "").replace("MYR", "").replace("$", "").replace(" ", "").strip()
    s = s.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", s) else s.replace(",", ".")
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    return d.quantize(Decimal("0.01"))


def amounts_in(line: str) -> list[Decimal]:
    out = []
    for m in MONEY_RX.finditer(line):
        try:
            out.append(Decimal(m.group(1).replace(",", "") + "." + m.group(2)))
        except InvalidOperation:
            continue
    return out


def _year(y: str) -> int:
    n = int(y)
    return n + 2000 if n < 100 else n


def parse_date(value) -> dt.date | None:
    """A receipt date (day first, as in Malaysia and India) -> date, else None."""
    if value is None:
        return None
    if isinstance(value, dt.date):
        return value
    m = DATE_RX.search(str(value).upper())
    return _date_of(m) if m else None


def _date_of(m) -> dt.date | None:
    g = m.groupdict()
    try:
        if g["d1"]:
            try:
                return dt.date(_year(g["y1"]), int(g["m1"]), int(g["d1"]))
            except ValueError:  # 12/28/2017: month first
                return dt.date(_year(g["y1"]), int(g["d1"]), int(g["m1"]))
        if g["y6"]:
            return dt.date(int(g["y6"]), int(g["m6"]), int(g["d6"]))
        if g["y2"]:
            return dt.date(int(g["y2"]), int(g["m2"]), int(g["d2"]))
        if g["d3"]:
            return dt.date(_year(g["y3"]), MONTHS[g["m3"][:3].upper()], int(g["d3"]))
        if g["m4"]:
            return dt.date(int(g["y4"]), MONTHS[g["m4"][:3].upper()], int(g["d4"]))
        if g["d5"]:
            return dt.date(int(g["y5"]), int(g["m5"]), int(g["d5"]))
    except (ValueError, KeyError):
        return None
    return None


def dates_in(text: str) -> list[tuple[dt.date, str]]:
    out = []
    for m in DATE_RX.finditer((text or "").upper()):
        d = _date_of(m)
        if d is not None:
            out.append((d, m.group(0)))
    return out


def norm_name(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def similar(a: str, b: str) -> float:
    a, b = norm_name(a), norm_name(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _has_words(ln: str) -> bool:
    return bool(re.search(r"[A-Za-z]{3,}", re.sub(r"\b(?:RM|MYR|SR|ZR)\b", "", ln, flags=re.I)))


SUMMARY = re.compile(r"\b(?:GST|TAX|SST)\s*SUMMARY\b", re.I)
UNIT_PRICE = re.compile(r"\d\s*(?:X|@)\s*(?:RM\s*)?\d", re.I)


def _labelled_amounts(lines: list[str]) -> list[dict]:
    """Every money amount with the label it most likely belongs to. OCR often prints a column of labels and then
    a column of values (TOTAL / CASH / CHANGE, then 18.00 / 20.00 / 2.00): a run of label lines followed by a run
    of the same number of value lines is paired in order. Otherwise the label is the same line, or the nearest
    line above with words. Amounts in a tax summary block are labelled as tax."""
    paired: dict[int, str] = {}
    i, n = 0, len(lines)
    while i < n:
        if _has_words(lines[i]) and not amounts_in(lines[i]):
            j = i
            while j < n and _has_words(lines[j]) and not amounts_in(lines[j]):
                j += 1
            k = j
            while k < n and amounts_in(lines[k]) and not _has_words(lines[k]):
                k += 1
            labels, values = lines[i:j], list(range(j, k))
            if len(labels) >= 2 and len(labels) == len(values):
                for lab, v in zip(labels, values, strict=True):
                    paired[v] = lab
            i = max(j, i + 1)
        else:
            i += 1
    out = []
    in_summary = False
    for i, ln in enumerate(lines):
        if SUMMARY.search(ln):
            in_summary = True
        for a in amounts_in(ln):
            label, sure = ln, True
            if i in paired:
                label = paired[i]
            elif not _has_words(ln):
                for j in range(i - 1, max(-1, i - 4), -1):  # nearest line above with words
                    if _has_words(lines[j]):
                        label = lines[j]
                        # under a block of several labels, which label a value belongs to is a guess
                        sure = not (j > 0 and _has_words(lines[j - 1]) and not amounts_in(lines[j - 1]))
                        break
            out.append({"amount": a, "line": i, "label": label, "summary": in_summary, "sure": sure})
    return out


def _k(x: dict) -> str | None:
    """The kind of a labelled amount, or None when its label is a guess."""
    if x["summary"]:
        return "tax summary"
    return _kind(x["label"]) if x["sure"] else None


def _kind(label: str, summary: bool = False) -> str | None:
    if summary:
        return "tax summary"
    if TOTAL_WORDS.search(label) and not NOT_TOTAL.search(label):
        return "total"
    for name, rx in KINDS:
        if rx.search(label):
            return name
    return None


def _cash_and_change(lines: list[str]) -> tuple[Decimal | None, Decimal | None]:
    cash = change = None
    for x in _labelled_amounts(lines):
        k = _k(x)
        if k == "cash paid" and re.search(r"\bCASH\b|\bTENDER", x["label"], re.I) and cash is None:
            cash = x["amount"]
        elif k == "change" and change is None:
            change = x["amount"]
    return cash, change


# ===================================================================== the agents

class AgentError(RuntimeError):
    pass


def rule_extract(text: str, avoid: list | None = None) -> dict:
    """A typical first-version extractor: the amount at the last TOTAL label, the first date, the first line that
    looks like a company name. On a retry it avoids values the guard rejected and tries the next candidate."""
    avoid = avoid or []
    lines = lines_of(text)

    def blamed(a: dict, field: str) -> bool:  # avoid only what the guard objected to
        why = str(a.get("_reason") or "")
        value = str(a.get(field) or "")
        return bool(not why or (field == "total" and ("total" in why or (value and value in why))) or
                    (field == "date" and "date" in why) or
                    (field == "vendor" and ("vendor" in why or (value and value in why))))
    bad_totals = {money(a.get("total")) for a in avoid if a.get("total") and blamed(a, "total")}
    candidates: list[Decimal] = []
    for i in range(len(lines) - 1, -1, -1):
        if TOTAL_WORDS.search(lines[i]) and not NOT_TOTAL.search(lines[i]):
            here = amounts_in(lines[i])
            if not here:
                for j in range(i + 1, min(len(lines), i + 4)):
                    here = amounts_in(lines[j])
                    if here:
                        break
            candidates.extend(here)
    if avoid:  # told it was wrong: try cash minus change, then the largest total-labelled amount, then the largest
        cash, change = _cash_and_change(lines)
        retry: list[Decimal] = []
        if cash is not None and change is not None and cash - change > 0:
            retry.append(cash - change)
        labelled = _labelled_amounts(lines)
        finals = [x["amount"] for x in labelled if _k(x) == "total"]
        retry += list(reversed(finals)) + sorted(set(finals), reverse=True)
        candidates = retry + candidates
    total = next((c for c in candidates if c not in bad_totals), None)
    found = dates_in(text)
    bad_dates = {str(a.get("date")) for a in avoid if a.get("date") and blamed(a, "date")}
    if avoid:  # the date next to a DATE label first
        near = [raw for k, ln in enumerate(lines) if re.search(r"\b(?:DATE|TARIKH|DT)\b", ln, re.I)
                for nxt in lines[k:k + 3] for _, raw in dates_in(nxt)]
        found = [(d, raw) for raw in near for d in [parse_date(raw)] if d is not None] + found
    date = next((raw for _, raw in found if raw not in bad_dates), found[0][1] if found else "")
    bad_vendors = {str(a.get("vendor")) for a in avoid if a.get("vendor") and blamed(a, "vendor")}
    vendor = ""
    for ln in lines[:8]:
        if VENDOR_HINTS.search(ln) and not NOT_VENDOR.search(ln) and ln not in bad_vendors and \
                (not avoid or len(re.sub(r"[^A-Z]", "", re.sub(r"\b(?:SDN|BHD|S/B|CO|M)\b", "", ln.upper()))) >= 3):
            vendor = ln
            break
    if not vendor:
        vendor = next((ln for ln in lines[:5] if re.search(r"[A-Za-z]{3,}", ln) and ln not in bad_vendors), "")
    return {"vendor": vendor, "date": date, "total": None if total is None else f"{total:.2f}", "currency": "RM"}


def decide(fields: dict, seen: set | None = None) -> dict:
    """The expense policy, exactly."""
    total = money(fields.get("total"))
    if total is None or total <= 0:
        return {"decision": "reject", "reason": "no positive total"}
    key = (norm_name(fields.get("vendor", "")), str(parse_date(fields.get("date"))), str(total))
    if seen is not None and key in seen:
        return {"decision": "duplicate", "reason": "already submitted"}
    if total > APPROVAL_LIMIT:
        return {"decision": "needs_approval", "reason": f"above {APPROVAL_LIMIT}"}
    return {"decision": "approve", "reason": f"within {APPROVAL_LIMIT}"}


def category_of(vendor: str, text: str = "") -> str:
    for name, words in CATEGORY_WORDS:
        if re.search(rf"\b(?:{words})", vendor or "", re.I):
            return name
    for name, words in CATEGORY_WORDS:
        if re.search(rf"\b(?:{words})", (text or "")[:600], re.I):
            return name
    return "other"


def rule_ledger(fields: dict, decision: dict, text: str = "") -> dict:
    d = parse_date(fields.get("date"))
    amount = money(fields.get("total"))
    return {"vendor": fields.get("vendor", ""), "date": d.isoformat() if d else "",
            "amount": None if amount is None else f"{amount:.2f}", "currency": fields.get("currency") or "RM",
            "category": category_of(fields.get("vendor", ""), text),
            "memo": f"{fields.get('vendor', '')} receipt, {decision.get('decision', '')}"}


EXTRACT_PROMPT = """You read shop receipts for a finance team. The receipt text comes from OCR and may have errors.
Return ONLY a JSON object: {"vendor": "<the shop or company name>", "date": "<the receipt date as printed>",
"total": "<the final amount the customer paid for the purchase, with 2 decimals, no currency sign>",
"currency": "<currency code, e.g. RM>"}"""
POLICY_PROMPT = f"""You apply a company expense policy to a receipt's fields. {POLICY}
Return ONLY a JSON object: {{"decision": "approve" | "needs_approval" | "duplicate" | "reject", "reason": "<short>"}}"""
LEDGER_PROMPT = f"""You write accounting ledger entries from approved receipt fields.
Return ONLY a JSON object: {{"vendor": "<as given>", "date": "<YYYY-MM-DD; receipt dates are day first>",
"amount": "<the total, 2 decimals>", "currency": "<as given>", "category": "<one of: {', '.join(CATEGORIES)}>",
"memo": "<a short description>"}}"""


def _ask(client, system: str, user: str, attempt: int, feedback: str | None) -> dict:
    from cascade_guard.llm.prompts import parse_last_json
    if feedback:
        user += ("\n\nYOUR PREVIOUS ANSWER WAS CHECKED AND REJECTED: " + feedback +
                 "\nRead the receipt again and answer again with the same JSON format.")
    resp = client.complete(system, user, max_tokens=400, temperature=0.0, sample=attempt, purpose="receipt-agent")
    data = parse_last_json(resp.text)
    if not isinstance(data, dict):
        raise AgentError("the reply was not a JSON object")
    return data


def llm_extract(client, text: str, attempt: int = 0, feedback: str | None = None) -> dict:
    d = _ask(client, EXTRACT_PROMPT, "RECEIPT TEXT:\n" + text[:6000], attempt, feedback)
    return {"vendor": str(d.get("vendor") or ""), "date": str(d.get("date") or ""),
            "total": None if d.get("total") in (None, "") else str(d.get("total")),
            "currency": str(d.get("currency") or "RM")}


def llm_policy(client, fields: dict, already: list, attempt: int = 0, feedback: str | None = None) -> dict:
    user = ("RECEIPT FIELDS: " + json.dumps(fields) + "\nALREADY SUBMITTED (vendor, date, total): " +
            json.dumps(already[-20:]))
    d = _ask(client, POLICY_PROMPT, user, attempt, feedback)
    return {"decision": str(d.get("decision") or "").strip().lower(), "reason": str(d.get("reason") or "")}


def llm_ledger(client, fields: dict, decision: dict, attempt: int = 0, feedback: str | None = None) -> dict:
    user = "RECEIPT FIELDS: " + json.dumps(fields) + "\nDECISION: " + json.dumps(decision)
    d = _ask(client, LEDGER_PROMPT, user, attempt, feedback)
    return {k: (None if d.get(k) is None else str(d.get(k))) for k in ("vendor", "date", "amount", "currency",
                                                                       "category", "memo")}


# ===================================================================== the guard's checks

def check_extract(out: dict, text: str, today: dt.date | None = None) -> tuple[bool, str | None]:
    """Is the extraction consistent with the receipt itself? (No answer key is used.)"""
    lines = lines_of(text)
    problems = []
    total = money(out.get("total"))
    labelled = _labelled_amounts(lines)
    if total is None or total <= 0:
        problems.append("there is no total amount")
    else:
        at = [x for x in labelled if x["amount"] == total]
        kinds = {_k(x) for x in at}
        totals = [x["amount"] for x in labelled if _k(x) == "total"]
        cash, change = _cash_and_change(lines)
        paid = cash - change if cash is not None and change is not None and cash - change > 0 else None
        if not at:
            problems.append(f"the total {total} does not appear on the receipt")
        elif "total" not in kinds and total != paid and all(x["sure"] for x in at):
            named = sorted(k for k in kinds if k and k != "total")
            if named:
                problems.append(f"{total} is the {named[0]} on the receipt, not the total")
            elif totals:
                problems.append(f"{total} is not next to a total label on the receipt")
        if paid is not None and abs(paid - total) > Decimal("0.05") and any(x["amount"] == paid for x in labelled):
            # (5-sen rounding aside; and only when the amount paid is itself printed, so the cash and change
            # were read right)
            problems.append(f"the cash paid ({cash}) minus the change ({change}) is {paid}, not {total}")
        payments = {x["amount"] for x in labelled if _k(x) in ("cash paid", "change")}
        rounding = any(re.search(r"\bROUND", ln, re.I) for ln in lines)
        bigger = sorted({x["amount"] for x in labelled if x["amount"] > total and x["amount"] not in payments
                         and not UNIT_PRICE.search(lines[x["line"]])
                         and not x["summary"] and _k(x) in ("total", None)
                         and not (cash is not None and x["amount"] >= cash)
                         and not (rounding and x["amount"] - total <= Decimal("0.05"))
                         and not (_k(x) is None and x["amount"] % 1 == 0)},   # a round amount: likely cash given
                        reverse=True)
        if bigger and (paid is None or not any(x["amount"] == paid for x in labelled)):
            problems.append(f"the receipt has a larger amount, {bigger[0]}, that is not cash paid or change; the "
                            f"total is normally the largest")
        rounded = [x["amount"] for x in labelled
                   if re.search(r"ROUND\w*\s*(?:ED\s*)?TOTAL|TOTAL\s*ROUND|NETT|NET\s*TOTAL|GRAND\s*TOTAL",
                                x["label"], re.I) and _k(x) == "total"]
        if rounded and total not in rounded and all(abs(r - total) <= Decimal("0.05") for r in rounded):
            problems.append(f"the receipt's final (rounded or net) total is {rounded[-1]}, not {total}")
    date = parse_date(out.get("date"))
    if date is None:
        problems.append(f"'{out.get('date')}' is not a date")
    else:
        on_receipt = {d for d, _ in dates_in(text)}
        labelled_dates = set()
        for k, ln in enumerate(lines):
            if re.search(r"\b(?:DATE|TARIKH|DT)\b", ln, re.I):
                for nxt in lines[k:k + 3]:
                    labelled_dates |= {d for d, _ in dates_in(nxt)}
        if on_receipt and date not in on_receipt:
            problems.append(f"the date {date.isoformat()} is not on the receipt")
        elif labelled_dates and date not in labelled_dates:
            problems.append(f"the date next to the receipt's 'Date' label is not {date.isoformat()}")
        if today and date > today:
            problems.append(f"the date {date.isoformat()} is in the future")
        if date.year < 2000:
            problems.append(f"the year {date.year} is not plausible")
    vendor = str(out.get("vendor") or "")
    if not vendor.strip():
        problems.append("there is no vendor name")
    elif lines and max((similar(vendor, ln) for ln in lines[:10]), default=0) < 0.7 and \
            norm_name(vendor) not in norm_name(" ".join(lines[:10])):
        problems.append(f"the vendor '{vendor}' is not among the receipt's first lines")
    elif len(re.sub(r"[^A-Z]", "", re.sub(r"\b(?:SDN|BHD|S/B|CO|M|PLT|LTD|CASH|SALES?|BILL|RECEIPT)\b", "",
                                             vendor.upper()))) < 3:
        problems.append(f"'{vendor}' is not a business name")
    elif NOT_VENDOR.search(vendor) and not VENDOR_HINTS.search(vendor):
        problems.append(f"'{vendor}' looks like an address or a label, not the vendor")
    return (not problems), ("; ".join(problems) or None)


def check_policy(out: dict, fields: dict, seen: set) -> tuple[bool, str | None]:
    want = decide(fields, seen)
    if out.get("decision") != want["decision"]:
        return False, (f"under the policy the decision for a total of {fields.get('total')} is "
                       f"'{want['decision']}' ({want['reason']}), not '{out.get('decision')}'")
    return True, None


def check_ledger(out: dict, fields: dict) -> tuple[bool, str | None]:
    problems = []
    if money(out.get("amount")) != money(fields.get("total")):
        problems.append(f"the amount must be the receipt total {fields.get('total')}, not {out.get('amount')}")
    want = parse_date(fields.get("date"))
    if not want or out.get("date") != want.isoformat():
        problems.append(f"the date must be {want.isoformat() if want else 'the receipt date'} (YYYY-MM-DD; the "
                        f"receipt's date {fields.get('date')} is day first), not {out.get('date')}")
    if norm_name(out.get("vendor") or "") != norm_name(fields.get("vendor") or ""):
        problems.append(f"the vendor must be '{fields.get('vendor')}'")
    if out.get("category") not in CATEGORIES:
        problems.append(f"the category must be one of {', '.join(CATEGORIES)}, not '{out.get('category')}'")
    memo_amounts = amounts_in(str(out.get("memo") or ""))
    if any(a != money(fields.get("total")) for a in memo_amounts):
        problems.append("the memo mentions an amount that is not the total")
    return (not problems), ("; ".join(problems) or None)


# ===================================================================== the workflow

@dataclass
class Batch:
    """State shared across the receipts of one submission (the duplicate check)."""
    seen: set = field(default_factory=set)
    submitted: list = field(default_factory=list)

    def remember(self, fields: dict):
        total = money(fields.get("total"))
        if total is not None:
            self.seen.add((norm_name(fields.get("vendor", "")), str(parse_date(fields.get("date"))), str(total)))
            self.submitted.append([fields.get("vendor"), fields.get("date"), f"{total:.2f}"])


def process_receipt(text: str, *, client=None, guard: bool = True, max_retries: int = 2, batch: Batch | None = None,
                    today: dt.date | None = None) -> dict:
    """Run extract -> policy -> ledger on one receipt. client=None: rule agents; a model client: LLM agents.
    guard=False runs the same agents with no checks (the baseline)."""
    batch = batch if batch is not None else Batch()
    today = today or dt.date.today()
    trace: list[dict] = []
    calls = 0
    started = time.monotonic()
    state: dict[str, Any] = {}

    def run(step, agent, check):
        nonlocal calls
        rejected: list = []
        feedback = None
        for attempt in range(max_retries + 1 if guard else 1):
            calls += 1
            try:
                out = agent(attempt, feedback, rejected)
            except Exception as e:  # an unusable reply is a failed attempt, like a wrong one
                from cascade_guard.evaluation.runner import _is_infrastructure
                if _is_infrastructure(e):
                    raise
                out, ok, why = None, False, f"the agent's reply could not be used ({e})"
            else:
                ok, why = check(out) if guard else (True, None)
            trace.append({"step": step, "attempt": attempt, "output": out, "passed": ok, "reason": why})
            if ok:
                return out
            rejected.append({**(out or {}), "_reason": why})
            feedback = why
        return None

    if client is None:
        steps = {
            "extract": lambda a, fb, rej: rule_extract(text, rej),
            "policy": lambda a, fb, rej: decide(state["fields"], batch.seen),
            "ledger": lambda a, fb, rej: rule_ledger(state["fields"], state["decision"], text),
        }
    else:
        steps = {
            "extract": lambda a, fb, rej: llm_extract(client, text, a, fb),
            "policy": lambda a, fb, rej: llm_policy(client, state["fields"], batch.submitted, a, fb),
            "ledger": lambda a, fb, rej: llm_ledger(client, state["fields"], state["decision"], a, fb),
        }
    halted_at = None
    fields = run("extract", steps["extract"], lambda o: check_extract(o, text, today))
    if fields is None:
        halted_at = "extract"
    else:
        state["fields"] = fields
        decision = run("policy", steps["policy"], lambda o: check_policy(o, fields, batch.seen))
        if decision is None:
            halted_at = "policy"
        else:
            state["decision"] = decision
            entry = run("ledger", steps["ledger"], lambda o: check_ledger(o, fields))
            if entry is None:
                halted_at = "ledger"
            else:
                state["entry"] = entry
        batch.remember(fields)
    return {"status": "halted" if halted_at else "posted", "halted_at": halted_at,
            "fields": state.get("fields"), "decision": state.get("decision"), "entry": state.get("entry"),
            "trace": trace, "agent_calls": calls, "seconds": round(time.monotonic() - started, 3),
            "caught": sum(1 for t in trace if not t["passed"]),
            "agents": "rules" if client is None else f"{client.provider}:{client.model}", "guarded": guard}


# ===================================================================== scoring against an answer key (benchmarks)

def score(result: dict, truth: dict) -> dict:
    """Compare what would be posted with the receipt's true vendor, date and total."""
    entry = result.get("entry") or {}
    fields = result.get("fields") or {}
    t_total, t_date = money(truth.get("total")), parse_date(truth.get("date"))
    total_ok = entry.get("amount") is not None and money(entry.get("amount")) == t_total
    date_ok = t_date is not None and entry.get("date") == t_date.isoformat()
    vendor_ok = similar(entry.get("vendor") or fields.get("vendor") or "", truth.get("company", "")) >= 0.8
    correct = total_ok and date_ok
    if result["status"] == "halted":
        # Was the stopped receipt really wrong? Judge the agents' first answers, which are what would have been
        # posted without the guard (the first extraction, and the first ledger entry if the run got that far).
        first = {t["step"]: t["output"] for t in reversed(result["trace"]) if t["attempt"] == 0}
        ext, led = first.get("extract") or {}, first.get("ledger")
        if led is not None:
            right = money(led.get("amount")) == t_total and t_date is not None and led.get("date") == t_date.isoformat()
        else:
            right = money(ext.get("total")) == t_total and parse_date(ext.get("date")) == t_date
        outcome = "stopped_ok" if right else "stopped_wrong"
    else:
        outcome = "posted_ok" if correct else "posted_wrong"
    return {"outcome": outcome, "total_ok": total_ok, "date_ok": date_ok, "vendor_ok": vendor_ok}


# ===================================================================== the public SROIE receipts

def load_sroie(folder: str, ids: range | list | None = None) -> list[dict]:
    """Receipts from the ICDAR 2019 SROIE dataset (data/box/*.csv OCR lines, data/key/*.json answers)."""
    from pathlib import Path
    root = Path(folder)
    box, key = (root / "box", root / "key") if (root / "box").is_dir() else (root / "data" / "box", root / "data" / "key")
    out = []
    for f in sorted(box.glob("*.csv")):
        rid = f.stem
        if ids is not None and int(rid) not in ids:
            continue
        kf = key / f"{rid}.json"
        if not kf.exists():
            continue
        lines = []
        for raw in f.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = raw.split(",", 8)
            if len(parts) == 9 and parts[8].strip():
                lines.append(parts[8].strip())
        img = (box.parent / "img" / f"{rid}.jpg")
        out.append({"id": rid, "text": "\n".join(lines), "truth": json.loads(kf.read_text(encoding="utf-8")),
                    "image": str(img) if img.exists() else None})
    return out


# ===================================================================== photos (optional OCR)

MAX_IMAGE_BYTES = 8 * 1024 * 1024
_ocr_engine = None


class OCRUnavailable(RuntimeError):
    pass


def ocr_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("rapidocr_onnxruntime") is not None


def text_from_image(data: bytes) -> str:
    """The text of a receipt photo (JPEG / PNG), one line per text box, top to bottom. Needs the optional OCR
    engine: pip install "cascade-guard-ai[ocr]" (about 400 MB, runs offline, free; needs a few GB of memory)."""
    global _ocr_engine
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("the image is larger than 8 MB")
    if not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a JPEG or PNG image")
    if not ocr_available():
        raise OCRUnavailable('reading photos needs the OCR add-on: pip install "cascade-guard-ai[ocr]"')
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    import io

    import numpy as np
    from PIL import Image
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if max(img.size) > 1600:  # big phone photos: OCR memory grows with the image, and 1600 px reads receipts fine
        img.thumbnail((1600, 1600))
    result, _ = _ocr_engine(np.asarray(img)[:, :, ::-1])  # RGB -> BGR, as the engine expects
    return _rows_to_text(result or [])


def _rows_to_text(boxes) -> str:
    """Text boxes -> receipt lines: boxes whose vertical middles are within half a line of each other are one
    printed row ("TOTAL" ... "19.05"), joined left to right."""
    items = []
    for pts, text, *_ in boxes:
        ys, xs = [p[1] for p in pts], [p[0] for p in pts]
        items.append({"top": min(ys), "mid": (min(ys) + max(ys)) / 2, "h": max(1.0, max(ys) - min(ys)), "x": min(xs),
                      "text": str(text)})
    items.sort(key=lambda b: b["mid"])
    rows: list[list[dict]] = []
    for b in items:
        if rows and abs(b["mid"] - rows[-1][-1]["mid"]) <= 0.5 * min(b["h"], rows[-1][-1]["h"]):
            rows[-1].append(b)
        else:
            rows.append([b])
    return "\n".join("  ".join(b["text"] for b in sorted(r, key=lambda b: b["x"])) for r in rows)
