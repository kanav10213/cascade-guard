"""Check a bill or invoice written the way people really write them, and say what is wrong in plain words.

    check_bill("3 x $4.00, 2 x $1.50, total $16.00")
    -> status "wrong": the items add up to $15.00 but the bill says $16.00 ($1.00 too much)

Understands one item per line, or items separated by commas, semicolons or " + ":
    3 x $4.00 · 3 x Claw hammer @ $14.99 · 2 pens at ₹15 each · Pens 2 x ₹15 · Rice: 2 kg x ₹60 = ₹120
    $4.00 x 3 · 3 @ 4.00 · Hammer x 3 @ $14.99 · 3 hammers $14.99 each · 2 tickets for $30 · Hammer - ₹250
and the lines around them:
    Subtotal · Total / Grand total / Amount due · Tax 8% · GST 18% · CGST 9% SGST 9% · VAT 5% (incl.)
    Discount 10% / 10% off / Discount $5 · Delivery / Shipping / Service charge 10% / Round off
The pilot's invoice layout ("INVOICE INV-1007", "3 x Claw hammer @ $14.99", "Tax: 8%") works too.

Every amount is recomputed with exact decimal arithmetic (cents rounded half up). Nothing is guessed:
a line that has an amount but cannot be read is reported, never silently dropped.
"""
import re
from decimal import ROUND_HALF_UP, Decimal

MAX_TEXT = 20_000
MAX_SEGMENTS = 300
MAX_SEGMENT = 300
CENT = Decimal("0.01")

CURRENCIES = {"$": "$", "usd": "$", "₹": "₹", "rs": "₹", "rs.": "₹", "inr": "₹", "€": "€", "eur": "€",
              "£": "£", "gbp": "£", "¥": "¥"}
CUR = r"(?:\$|₹|€|£|¥|\brs\.?(?=\s|\d|$)|\binr\b|\busd\b|\beur\b|\bgbp\b)"
NUM = r"(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3}|\d+)(?:\.\d+)?"
QTY = r"\d+(?:\.\d+)?"
UNIT = (r"(?:kgs?|gms?|grams?|g|ltrs?|litres?|liters?|l|ml|mtrs?|m|pcs?|pieces?|nos?|units?|items?|pairs?|"
        r"boxes|box|packs?|packets?|dozens?|hrs?|hours?|days?|nights?|sets?|bags?|bottles?|kits?)\b")
EACH = r"(?:\s*(?:each|ea\.?|apiece|/\s*(?:pc|piece|unit|kg|l|hr|hour|item|day)|per\s+\w+))"
# "3x4", "3 x $4", "3 x Claw hammer", but not "3 xylophones"
SEP_X = r"(?:\s*[x*]\s*(?=[\d$₹€£¥]|rs\b|inr\b|usd\b)|\s*[x*]\s+)"


def _money(name: str) -> str:
    return (rf"(?P<{name}_neg>-\s*)?(?P<{name}_c1>{CUR})?\s*(?P<{name}_num>{NUM})(?:\s*(?P<{name}_c2>{CUR}))?"
            r"(?:\s*/-)?")


M1, M2 = _money("p"), _money("s")
STATED = rf"(?:\s*(?:=|->|→)\s*{M2})?"
DESC = r"[a-z][^@=→$₹€£¥]*?"

ITEM_PATTERNS = [
    re.compile(rf"^(?P<qty>{QTY})\s*(?P<unit>{UNIT})?{SEP_X}(?P<desc>[^$₹€£¥]*?[a-z][^$₹€£¥]*?)\s*(?:@|\bat\b)\s*{M1}"
               rf"{EACH}?{STATED}$"),
    re.compile(rf"^(?P<qty>{QTY})\s*(?P<unit>{UNIT})?{SEP_X}{M1}{EACH}?(?:\s+(?P<desc>[a-z][^=→]*?))?{STATED}$"),
    re.compile(rf"^(?P<desc>{DESC})\s*[-:]?\s*(?P<qty>{QTY})\s*(?P<unit>{UNIT})?{SEP_X}{M1}{EACH}?{STATED}$"),
    re.compile(rf"^(?P<desc>{DESC})\s*[x*]\s*(?P<qty>{QTY})\s*(?:@|\bat\b)\s*{M1}{EACH}?{STATED}$"),
    re.compile(rf"^(?P<desc>{DESC})\s+(?P<qty>{QTY})\s*(?P<unit>{UNIT})?\s*(?:@|\bat\b)\s*{M1}{EACH}?{STATED}$"),
    re.compile(rf"^(?P<qty>{QTY})\s+(?P<desc>{DESC})\s*(?:@|\bat\b)\s*{M1}{EACH}?{STATED}$"),
    re.compile(rf"^(?P<qty>{QTY})\s+(?P<desc>[a-z][^$₹€£¥]*?)\s+[x*]\s+{M1}{EACH}?{STATED}$"),
    re.compile(rf"^(?P<qty>{QTY})\s+(?P<desc>{DESC})\s+{M1}{EACH}{STATED}$"),
    re.compile(rf"^(?P<qty>{QTY})\s*(?:@|\bat\b)\s*{M1}{EACH}?(?:\s+(?P<desc>[a-z][^=→]*?))?{STATED}$"),
    # $4.00 x 3: a currency sign on the first number marks it as the price
    re.compile(rf"^(?P<p_c1>{CUR})\s*(?P<p_num>{NUM})\s*[x*]\s*(?P<qty>{QTY})(?:\s+(?P<desc>[a-z][^=→]*?))?{STATED}$"),
    re.compile(rf"^(?P<p_num>{NUM})\s*(?P<p_c2>{CUR})\s*[x*]\s*(?P<qty>{QTY})(?:\s+(?P<desc>[a-z][^=→]*?))?{STATED}$"),
    # Fish and chips @ $5 (one of it)
    re.compile(rf"^(?P<desc>{DESC})\s*(?:@|\bat\b)\s*{M1}{EACH}?{STATED}$"),
]
FOR_TOTAL = re.compile(rf"^(?P<qty>{QTY})\s*(?:{SEP_X})?\s*(?P<desc>[a-z].*?)\s+for\s+{M1}$")  # 2 tickets for $30
SINGLE = re.compile(rf"^(?P<desc>[a-z][a-z0-9 .,'&()/+#-]*?)\s*(?P<sep>[-:=])?\s*{M1}$")  # Hammer - ₹250

TAX_WORDS = r"(?:sales\s+tax|service\s+tax|tax|gst|vat|cgst|sgst|igst|utgst|cess)"
TAX_RATE = re.compile(rf"(?P<label>{TAX_WORDS})\s*(?:@|\(|:|-|at|of|rate)?\s*(?P<rate>\d+(?:\.\d+)?)\s*%\)?"
                      rf"|(?P<rate2>\d+(?:\.\d+)?)\s*%\s*(?P<label2>{TAX_WORDS})\b")
ACRONYMS = {"gst", "vat", "cgst", "sgst", "igst", "utgst"}
TOTAL_LABEL = re.compile(r"^(?:grand\s+total|total\s+(?:amount|due|payable|bill|price|cost|to\s+pay)|total|"
                         r"amount\s+(?:due|payable|to\s+pay)|amount|balance\s+due|net\s+(?:amount|payable|total)|"
                         r"to\s+pay|bill\s+amount|bill\s+total|invoice\s+total|you\s+pay|final\s+amount|=)$")
SUBTOTAL_LABEL = re.compile(r"^(?:sub[\s-]*total|items?\s+total|total\s+before\s+tax|before\s+tax|taxable\s+"
                            r"(?:value|amount))$")
TAX_LABEL = re.compile(rf"^(?:total\s+)?{TAX_WORDS}(?:\s+(?:amount|total))?$")
CHARGE_LABEL = re.compile(r"^(?:delivery|shipping|postage|courier|freight|packing|packaging|handling|service|"
                          r"convenience|platform|tip|gratuity|installation|round(?:ing)?[\s-]*off|rounding|"
                          r"adjustment)(?:\s+(?:charges?|fees?|cost))?$|^fees?$")
DISCOUNT_LABEL = re.compile(r"^(?:less\s+)?(?:discount|coupon|promo(?:\s+code)?|savings?|rebate|offer)\b|^less$")
META_LABEL = re.compile(r"^(?:invoice|inv|bill|receipt|order|ref|reference|po|date|dated|due\s+date|customer|"
                        r"client|bill\s+to|ship\s+to|sold\s+to|name|phone|tel|mobile|email|address|gstin|gst\s+no|"
                        r"pan|account|a/c|ifsc|pin|zip|table|seller|vendor|from|to|items?|description|qty|"
                        r"quantity|thank\s+you|notes?|terms|paid|cash|change|tendered|card|upi|payment|balance"
                        r"(?!\s+due)|total\s+(?:items|qty|quantity|units|pieces|count)|no\.?\s+of\s+items)\b")
INSTRUCTION = re.compile(r"\b(?:check|verify|validate|review|audit|calculate|compute|confirm|is|are|please|can|"
                         r"could|tell|use|look|help|correct|right|wrong|bill|invoice|receipt|tool)\b", re.I)
LEAD_REQUEST = re.compile(
    r"^\s*(?:hi|hey|hello)?[\s,!]*(?:please\s+|pls\s+|can\s+you\s+|could\s+you\s+|kindly\s+)?"
    r"(?:use\s+.{0,60}?\s+to\s+)?(?:check|verify|validate|review|audit|recheck|re-check)\s+"
    r"(?:if\s+|whether\s+)?(?:this|my|the|our|these)?\s*(?:invoice|bill|receipt|order|items|numbers)?\s*"
    r"(?:is\s+(?:correct|right)\s*)?(?=\d|[$₹€£]|rs\b)", re.I)


# --------------------------------------------------------------------------- small helpers

def _dec(num: str) -> Decimal:
    return Decimal(num.replace(",", ""))


def _cents(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def _cur(token: str | None) -> str | None:
    if not token:
        return None
    t = token.strip().lower()
    return CURRENCIES.get(t) or CURRENCIES.get(t.rstrip("."))


def fmt(amount, cur: str = "") -> str:
    """$1,234.50 · ₹1,23,456.00 (Indian grouping for rupees) · 15.00 when the bill has no currency."""
    d = _cents(Decimal(str(amount)))
    sign = "-" if d < 0 else ""
    whole, frac = f"{abs(d):.2f}".split(".")
    if cur == "₹" and len(whole) > 3:
        head, groups = whole[:-3], [whole[-3:]]
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + groups)
    else:
        whole = f"{int(whole):,}"
    return f"{sign}{cur}{whole}.{frac}"


def _pct(r) -> str:
    r = Decimal(str(r))
    return (str(int(r)) if r == r.to_integral_value() else str(r.normalize())) + "%"


def _qty_text(q: Decimal) -> str:
    return str(int(q)) if q == q.to_integral_value() else str(q.normalize())


def _money_from(m: re.Match, name: str = "p") -> tuple[Decimal, str | None]:
    g = m.groupdict()
    value = _dec(g[f"{name}_num"])
    if g.get(f"{name}_neg"):
        value = -value
    return value, _cur(g.get(f"{name}_c1")) or _cur(g.get(f"{name}_c2"))


PERCENT = re.compile(r"\d+(?:\.\d+)?\s*%")
MONEY_ANY = re.compile(rf"(?<![\d.,])(?P<p_neg>-\s*)?(?P<p_c1>{CUR})?\s*(?P<p_num>{NUM})(?![\d])(?!\.\d)"
                       rf"(?:\s*(?P<p_c2>{CUR}))?")


def _amounts(text: str) -> list[tuple[Decimal, str | None]]:
    """Money-looking numbers in a piece of text, percentages left out; currency-marked ones first."""
    found = [_money_from(m) for m in MONEY_ANY.finditer(PERCENT.sub(" ", text))]
    marked = [v for v in found if v[1]]
    return marked or found


def _label(low: str) -> str:
    """The words before the first number, sign or colon: "grand total: $16" -> "grand total"."""
    if low.lstrip().startswith("="):
        return "="
    m = re.match(rf"^\s*([a-z][a-z\s/&.'#-]*?)\s*(?=[:=(@]|\d|{CUR}|$)", low)
    if not m:
        return ""
    label = re.sub(r"[\s.#/&'-]+$", "", m.group(1)).strip()
    label = re.sub(r"^(?:the|my|our|your)\s+", "", label)
    return re.sub(r"\s+(?:is|was|comes\s+to|of|=)$", "", label)


# --------------------------------------------------------------------------- splitting into lines

def _normalise(text: str) -> str:
    text = text[:MAX_TEXT].replace(" ", " ").replace(" ", " ").replace(" ", " ")
    text = text.replace("×", " x ").replace("–", "-").replace("—", "-").replace("−", "-").replace("₨", "₹")
    text = re.sub(r"(?<=\d),(?=\d{2}(?![\d,]))", ".", text)  # decimal comma: 4,50 € -> 4.50 €
    return text


def strip_request(text: str) -> str:
    """Drop a leading request such as "Use the cascade guard tool to check this invoice:"."""
    m = re.match(r"^\s*([^\d\n:$₹€£]{3,160}?):\s*", text)
    if m and INSTRUCTION.search(m.group(1)) and not _field_words(m.group(1)):
        return text[m.end():]
    m = LEAD_REQUEST.match(text)
    return text[m.end():] if m else text


def _field_words(words: str) -> bool:
    w = _label(words.strip().lower() + ":")
    return bool(TOTAL_LABEL.match(w) or SUBTOTAL_LABEL.match(w) or TAX_LABEL.match(w) or CHARGE_LABEL.match(w)
                or DISCOUNT_LABEL.match(w) or re.match(r"^(?:customer|date|bill\s+to|invoice\s+(?:no|number|id))$", w))


THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d)|\d{2},\d)")
SPLIT_BEFORE_KEYWORD = re.compile(r"(?<=[\d)])\s+(?=(?:sub[\s-]*total|grand\s+total|total|tax|gst|vat|cgst|sgst|"
                                  r"igst|discount|delivery|shipping|service\s+charge)\b)"
                                  r"|(?<=%)\s+(?=(?:sub[\s-]*total|grand\s+total|total|discount|delivery|shipping|"
                                  r"service\s+charge)\b)", re.I)
LEAD_LABEL = re.compile(r"^(?:invoice|bill|receipt|items?|order)\s*:\s*(?=\d)", re.I)


def segments(text: str) -> list[str]:
    out: list[str] = []
    for line in _normalise(text).splitlines():
        protected = THOUSANDS.sub("\u0000", line)
        for part in re.split(r"[;|]|,|\s\+\s", protected):
            for piece in SPLIT_BEFORE_KEYWORD.split(part.replace("\u0000", ",")):
                piece = re.sub(r"^\s*(?:[-*•·>]|\d{1,2}[.)])\s+", "", piece).strip().rstrip(".").strip()
                piece = LEAD_LABEL.sub("", piece)
                if piece:
                    out.append(piece[:MAX_SEGMENT])
            if len(out) >= MAX_SEGMENTS:
                return out
    return out


# --------------------------------------------------------------------------- reading one line

def _orig(seg: str, low_part: str) -> str:
    i = seg.lower().find(low_part) if low_part else -1
    return seg[i:i + len(low_part)] if i >= 0 else low_part


def read_item(seg: str) -> dict | None:
    low = seg.lower()
    for rx in ITEM_PATTERNS:
        m = rx.match(low)
        if not m:
            continue
        qty = _dec(m.groupdict().get("qty") or "1")
        price, cur = _money_from(m, "p")
        if qty <= 0 or qty > 1_000_000 or price < 0:
            return None
        stated = None
        if m.groupdict().get("s_num"):
            stated, cur2 = _money_from(m, "s")
            cur = cur or cur2
        desc = (m.groupdict().get("desc") or "").strip(" -:,")
        return {"kind": "item", "qty": qty, "unit": m.groupdict().get("unit"), "unit_price": price,
                "description": _orig(seg, desc), "stated_line_total": stated, "currency": cur}
    m = FOR_TOTAL.match(low)
    if m and _dec(m.group("qty")) > 0:
        total, cur = _money_from(m, "p")
        return {"kind": "item", "qty": _dec(m.group("qty")), "unit": None, "unit_price": None,
                "line_total_given": total, "description": _orig(seg, m.group("desc").strip()),
                "stated_line_total": None, "currency": cur}
    return None


def _read_single(seg: str) -> dict | None:
    m = SINGLE.match(seg.lower())
    if not m:
        return None
    price, cur = _money_from(m, "p")
    if not (cur or m.group("sep") or "." in m.group("p_num")):  # "Table 2" is not an item priced 2
        return None
    return {"kind": "item", "qty": Decimal(1), "unit": None, "unit_price": price, "single": True,
            "description": _orig(seg, m.group("desc").strip(" -:")), "stated_line_total": None, "currency": cur}


def _tax(low: str, rates: list, label: str) -> dict:
    included = bool(re.search(r"\bincl(?:\.|uded|uding|usive)?\b|\binclusive\b", low))
    a = _amounts(TAX_RATE.sub(" ", low))
    return {"kind": "tax", "rates": rates, "stated": a[-1][0] if a and len(rates) == 1 else None,
            "currency": a[-1][1] if a else None, "included": included}


def _discount(low: str) -> dict | None:
    pct = re.search(r"(\d+(?:\.\d+)?)\s*%", low)
    a = _amounts(low)
    if not (pct or a):
        return None
    return {"kind": "discount", "rate": _dec(pct.group(1)) if pct else None,
            "amount": abs(a[-1][0]) if a else None, "currency": a[-1][1] if a else None}


def read_segment(seg: str) -> dict:
    low = seg.lower()
    label = _label(low)
    rates = [(m.group("label") or m.group("label2"), _dec(m.group("rate") or m.group("rate2")))
             for m in TAX_RATE.finditer(low)]
    if label == "":  # starts with a number: an item, "18% GST" or "10% off"
        item = read_item(seg)
        if item:
            return item
        if rates:
            return _tax(low, rates, label)
        if re.search(r"\boff\b|\bdiscount\b", low):
            d = _discount(low)
            if d:
                return d
        return {"kind": "unread"} if _amounts(low) else {"kind": "ignore"}
    if SUBTOTAL_LABEL.match(label):
        a = _amounts(low)
        return {"kind": "subtotal", "amount": a[-1][0], "currency": a[-1][1]} if a else {"kind": "ignore"}
    if TOTAL_LABEL.match(label):
        a = _amounts(TAX_RATE.sub(" ", low))
        if a:
            out = {"kind": "total", "amount": a[-1][0], "currency": a[-1][1]}
            if rates and re.search(r"\bincl", low):
                out["taxes_included"] = rates
            return out
        return {"kind": "ignore"}
    if rates and (TAX_LABEL.match(label) or re.match(rf"^(?:{TAX_WORDS}|incl\w*)\b", label)):
        return _tax(low, rates, label)
    if TAX_LABEL.match(label):
        a = _amounts(low)
        if a:
            return {"kind": "tax_amount", "label": label, "amount": a[-1][0], "currency": a[-1][1]}
    if CHARGE_LABEL.match(label):
        pct = re.search(r"(\d+(?:\.\d+)?)\s*%", low)
        a = _amounts(low)
        if a or pct:
            return {"kind": "charge", "label": _orig(seg, label).strip().capitalize() or label,
                    "amount": a[-1][0] if a else None, "rate": _dec(pct.group(1)) if pct and not a else None,
                    "currency": a[-1][1] if a else None}
    if DISCOUNT_LABEL.match(label):
        d = _discount(low)
        if d:
            return d
    item = read_item(seg)
    if item:
        return item
    if META_LABEL.match(label):
        m = re.match(r"^(?:invoice|inv|bill|receipt|order)\s*(?:no\.?|number|#|id)?\s*[:#-]?\s*([a-z0-9][\w/-]*)", low)
        if m and re.search(r"\d", m.group(1)):
            return {"kind": "meta", "invoice_id": _orig(seg, m.group(1)).upper()}
        return {"kind": "meta"}
    single = _read_single(seg)
    if single:
        return single
    return {"kind": "unread"} if _amounts(low) else {"kind": "ignore"}


JOINER = re.compile(r"\s(?:and|&|plus)\s", re.I)
NOTE = re.compile(r"^(?P<main>.*?\S)\s*\((?P<note>[^()]*)\)$")


def _read_all(body: str) -> list[tuple[str, dict]]:
    parts: list[tuple[str, dict]] = []
    for seg in segments(body):
        if JOINER.search(seg):  # "3 pens at $2 and 2 books at $5" is two items; "Fish and chips @ $5" is one
            pieces = [x.strip() for x in JOINER.split(seg) if x.strip()]
            read = [(x, read_segment(x)) for x in pieces]
            if sum(r["kind"] == "item" for _, r in read) >= 2 and all(r["kind"] not in ("unread", "ignore")
                                                                     for _, r in read):
                parts += read
                continue
        p = read_segment(seg)
        m = NOTE.match(seg)
        if p["kind"] in ("unread", "ignore", "tax") and m:  # "1 x Chair @ ₹1,180 (incl. GST 18%)"
            main = read_segment(m.group("main"))
            if main["kind"] == "item":
                parts.append((m.group("main"), main))
                note = read_segment(m.group("note"))
                if note["kind"] in ("tax", "discount", "charge"):
                    parts.append((m.group("note"), note))
                continue
        parts.append((seg, p))
    return parts


# --------------------------------------------------------------------------- the check

def check_bill(text: str) -> dict:
    """Read a bill, recompute every amount, and compare it with what the bill says."""
    parts = _read_all(strip_request(text or ""))
    items = [(s, p) for s, p in parts if p["kind"] == "item"]
    currencies = sorted({p["currency"] for _, p in parts if p.get("currency")})
    cur = currencies[0] if currencies else ""
    r: dict = {"status": "unreadable", "currency": cur, "invoice_id": None, "lines": [], "subtotal": None,
               "discount": None, "taxes": [], "charges": [], "total": None,
               "stated": {"subtotal": None, "total": None}, "difference": None,
               "checks": [], "problems": [], "hints": [], "assumptions": [],
               "unread": [s for s, p in parts if p["kind"] == "unread"], "uncertain": False}
    r["invoice_id"] = next((p["invoice_id"] for _, p in parts if p.get("invoice_id")), None)
    if len(currencies) > 1:
        r["hints"].append("The bill mixes currencies (" + ", ".join(currencies) + "); they are added as plain numbers.")
    if not items:
        r["summary"] = ("I couldn't find any items with a quantity and a price. Put one item on each line, or "
                        "separate them with commas, for example: 3 x $4.00, 2 x $1.50, total $16.00")
        return r

    subtotal = _lines(r, items, cur)
    discount = _apply_discount(r, parts, subtotal, cur)
    base = subtotal - discount
    added_tax = _apply_taxes(r, parts, base, cur)
    charges = _apply_charges(r, parts, base, cur)
    total = base + added_tax + charges
    r["total"] = _m(total)
    if discount and added_tax:
        r["assumptions"].append("Tax is charged after the discount.")
    if charges and added_tax:
        r["assumptions"].append("Tax is charged on the items, not on the extra charges.")

    stated_total = next((p["amount"] for _, p in reversed(parts) if p["kind"] == "total"), None)
    if stated_total is None and len(items) > 1:  # "3 x $4.00, 2 x $1.50 = $16.00": the last "= X" is the total
        last = r["lines"][-1]
        if last["stated_line_total"] is not None:
            st = Decimal(str(last["stated_line_total"]))
            if abs(st - total) < abs(st - Decimal(str(last["line_total"]))):
                stated_total = st
                last.update(stated_line_total=None, ok=True)
                r["checks"][len(items) - 1].update(stated=None, ok=True)
                r["problems"] = [x for x in r["problems"] if not x.startswith(f"Line {last['n']}")]

    stated_sub = next((p["amount"] for _, p in parts if p["kind"] == "subtotal"), None)
    if stated_sub is not None:
        ok = abs(_cents(stated_sub) - subtotal) < CENT
        r["stated"]["subtotal"] = _m(stated_sub)
        r["checks"].append({"what": "subtotal", "expected": _m(subtotal), "stated": _m(stated_sub), "ok": ok,
                            "working": _sum_text(r, cur, subtotal)})
        if not ok:
            r["problems"].append(f"The subtotal should be {fmt(subtotal, cur)} (the items added up), but the bill "
                                 f"says {fmt(stated_sub, cur)}.")

    working = _total_text(r, cur, subtotal, discount, charges, total)
    if stated_total is None:
        r["checks"].append({"what": "total", "expected": _m(total), "stated": None, "ok": True, "working": working})
        r["status"] = "wrong" if r["problems"] else "no_total"
        r["summary"] = f"The bill doesn't say a total. It should come to {fmt(total, cur)}." + (
            f" Also: {r['problems'][0]}" if r["problems"] else "")
        _caution(r)
        return r

    r["stated"]["total"] = _m(stated_total)
    diff = _cents(stated_total) - total
    r["difference"] = _m(diff)
    ok = abs(diff) < CENT
    alt = None if ok else _alternative(_cents(stated_total), subtotal, discount, base, r, charges)
    note = None
    if alt:
        note, total, replaced = alt
        ok, diff = True, Decimal(0)
        r["total"], r["difference"] = _m(total), 0.0
        r["assumptions"] = [a for a in r["assumptions"] if a != replaced] + [note]
        working = f"{working}; {note[0].lower() + note[1:-1]}, which gives {fmt(total, cur)}"
    r["checks"].append({"what": "total", "expected": _m(total), "stated": _m(stated_total), "ok": ok,
                        "working": working})
    if not ok:
        more = "too much" if diff > 0 else "too little"
        r["problems"].insert(0, f"The total is wrong: it should be {fmt(total, cur)}, but the bill says "
                                f"{fmt(stated_total, cur)} ({fmt(abs(diff), cur)} {more}).")
        r["hints"] += _diagnose(diff, _cents(stated_total), r, discount, added_tax, charges)
    r["status"] = "wrong" if r["problems"] else "correct"
    n = len(items)
    if r["status"] == "correct":
        r["summary"] = (f"The bill is correct: {n} item{'s come' if n != 1 else ' comes'} to {fmt(stated_total, cur)}."
                        + (f" {note}" if note else ""))
    elif not ok:
        r["summary"] = r["problems"][0].replace("The total is wrong: it should", "The total is wrong. It should") + (
            f" {r['hints'][0]}" if r["hints"] else "")
    else:
        r["summary"] = f"The total ({fmt(stated_total, cur)}) is right, but one amount is not. {r['problems'][0]}"
    _caution(r)
    return r


def _lines(r: dict, items: list, cur: str) -> Decimal:
    subtotal = Decimal(0)
    for n, (seg, p) in enumerate(items, 1):
        if p.get("unit_price") is None:  # "2 tickets for $30": the price is for all of them
            line_total = _cents(p["line_total_given"])
            unit_price = _cents(line_total / p["qty"])
            calc = f"{_qty_text(p['qty'])} for {fmt(line_total, cur)}"
        else:
            unit_price, line_total = p["unit_price"], _cents(p["qty"] * p["unit_price"])
            calc = f"{_qty_text(p['qty'])} × {fmt(unit_price, cur)}"
        stated = p.get("stated_line_total")
        ok = stated is None or abs(_cents(stated) - line_total) < CENT
        desc = p["description"]
        r["lines"].append({"n": n, "text": seg, "description": desc, "qty": _q(p["qty"]), "unit": p.get("unit"),
                           "unit_price": _m(unit_price), "line_total": _m(line_total),
                           "stated_line_total": _m(stated) if stated is not None else None, "ok": ok})
        r["checks"].append({"what": f"line {n}" + (f" ({desc})" if desc else ""), "expected": _m(line_total),
                            "stated": _m(stated) if stated is not None else None, "ok": ok,
                            "working": f"{calc} = {fmt(line_total, cur)}"})
        if not ok:
            r["problems"].append(f"Line {n}{' (' + desc + ')' if desc else ''}: {calc} = {fmt(line_total, cur)}, "
                                 f"but the bill says {fmt(stated, cur)}.")
        subtotal += line_total
    r["subtotal"] = _m(subtotal)
    return subtotal


def _apply_discount(r: dict, parts: list, subtotal: Decimal, cur: str) -> Decimal:
    d = next((p for _, p in parts if p["kind"] == "discount"), None)
    if d is None:
        return Decimal(0)
    if d["rate"] is not None:
        discount = _cents(subtotal * d["rate"] / 100)
        if d["amount"] is not None and abs(_cents(d["amount"]) - discount) >= CENT:
            r["problems"].append(f"A {_pct(d['rate'])} discount on {fmt(subtotal, cur)} is {fmt(discount, cur)}, but "
                                 f"the bill says {fmt(d['amount'], cur)}.")
        label = f"Discount {_pct(d['rate'])}"
    else:
        discount, label = _cents(d["amount"]), "Discount"
    r["discount"] = {"label": label, "rate": _m(d["rate"]) if d["rate"] is not None else None, "amount": _m(discount)}
    return discount


def _apply_taxes(r: dict, parts: list, base: Decimal, cur: str) -> Decimal:
    added, included = Decimal(0), []
    for _, p in parts:
        rates = p.get("rates") if p["kind"] == "tax" else p.get("taxes_included") if p["kind"] == "total" else None
        if rates:
            inside = p["kind"] == "total" or p["included"]
            for word, rate in rates:
                name = word.upper() if word in ACRONYMS else word.capitalize()
                entry = {"label": f"{name} {_pct(rate)}" + (" (included)" if inside else ""), "rate": _m(rate),
                         "amount": None, "stated": None, "included": inside}
                if inside:
                    included.append(entry)
                else:
                    amount = _cents(base * rate / 100)
                    entry["amount"] = _m(amount)
                    added += amount
                    if p.get("stated") is not None and len(rates) == 1:
                        entry["stated"] = _m(p["stated"])
                        if abs(_cents(p["stated"]) - amount) >= CENT:
                            r["problems"].append(f"{name} at {_pct(rate)} of {fmt(base, cur)} is {fmt(amount, cur)}, "
                                                 f"but the bill says {fmt(p['stated'], cur)}.")
                r["taxes"].append(entry)
        elif p["kind"] == "tax_amount":
            amount = _cents(p["amount"])
            added += amount
            r["taxes"].append({"label": p["label"].upper() if p["label"] in ACRONYMS else p["label"].capitalize(),
                               "rate": None, "amount": _m(amount), "stated": None, "included": False})
            r["assumptions"].append(f"The {p['label']} is given as an amount without a rate, so it is taken as written.")
    if included:
        total_rate = sum((Decimal(str(t["rate"])) for t in included), Decimal(0))
        inside_amount = _cents(base - base / (1 + total_rate / 100))
        for t in included:
            t["amount"] = _m(_cents(inside_amount * Decimal(str(t["rate"])) / total_rate))
        r["assumptions"].append("The prices already include the tax marked as included, so it is not added again.")
    return added


def _apply_charges(r: dict, parts: list, base: Decimal, cur: str) -> Decimal:
    total = Decimal(0)
    for _, p in parts:
        if p["kind"] == "charge":
            amount = _cents(base * p["rate"] / 100) if p.get("rate") is not None else _cents(p["amount"])
            label = p["label"] + (f" {_pct(p['rate'])}" if p.get("rate") is not None else "")
            r["charges"].append({"label": label, "amount": _m(amount)})
            total += amount
    return total


def _alternative(stated: Decimal, subtotal, discount, base, r, charges):
    """A stated total that matches another common way of doing the maths is not called wrong.
    Returns (note, that total, the default assumption it replaces) or None."""
    rates = [Decimal(str(t["rate"])) for t in r["taxes"] if t["rate"] is not None and not t["included"]]
    flat = sum((Decimal(str(t["amount"])) for t in r["taxes"] if t["rate"] is None), Decimal(0))
    if not rates:
        return None
    if charges:
        alt = _cents(base + sum((_cents((base + charges) * x / 100) for x in rates), Decimal(0)) + flat + charges)
        if alt == stated:
            return ("Here tax is also charged on the extra charges.", alt,
                    "Tax is charged on the items, not on the extra charges.")
    if discount:
        alt = _cents(subtotal + sum((_cents(subtotal * x / 100) for x in rates), Decimal(0)) + flat - discount
                     + charges)
        if alt == stated:
            return "Here the discount is taken off after tax.", alt, "Tax is charged after the discount."
    return None


def _diagnose(diff: Decimal, stated: Decimal, r: dict, discount, added_tax, charges) -> list[str]:
    total, d = Decimal(str(r["total"])), abs(diff)
    if added_tax and abs(stated - (total - added_tax)) < CENT:
        return ["The bill's total leaves out the tax (unless the prices already include it)."]
    if discount and abs(stated - (total + discount)) < CENT:
        return ["The discount was not taken off."]
    if charges and abs(stated - (total - charges)) < CENT:
        return ["The extra charges were not added."]
    if total and (abs(stated - total * 10) < CENT or abs(stated * 10 - total) < CENT):
        return ["It looks like a decimal point slip."]
    for line in r["lines"]:
        what = f"line {line['n']}" + (f" ({line['description']})" if line["description"] else "")
        if len(r["lines"]) > 1 and abs(Decimal(str(line["line_total"])) - d) < CENT:
            return [f"The difference is exactly the amount of {what}, so it looks "
                    + ("counted twice." if diff > 0 else "left out.")]
    for line in r["lines"]:
        what = f"line {line['n']}" + (f" ({line['description']})" if line["description"] else "")
        if abs(Decimal(str(line["unit_price"])) - d) < CENT:
            return [f"The difference is the price of one unit of {what}, so the quantity may be "
                    + ("one too many." if diff > 0 else "one too few.")]
    return []


def _caution(r: dict):
    r["uncertain"] = bool(r["unread"])
    if r["unread"]:
        n = len(r["unread"])
        lines = "; ".join(f"'{u}'" for u in r["unread"][:3]) + ("; ..." if n > 3 else "")
        r["hints"].append(f"I couldn't read {n} line{'s' if n != 1 else ''} ({lines}), so "
                          + ("it is" if n == 1 else "they are") + " not counted.")
        if r["status"] == "correct":
            r["summary"] = (f"The items I could read add up to the bill's total ({fmt(r['stated']['total'], r['currency'])}), "
                            f"but I couldn't read {n} line{'s' if n != 1 else ''}: {lines}. Check "
                            + ("it" if n == 1 else "them") + " yourself.")


def _sum_text(r: dict, cur: str, subtotal: Decimal) -> str:
    if len(r["lines"]) == 1:
        return fmt(subtotal, cur)
    return " + ".join(fmt(x["line_total"], cur) for x in r["lines"]) + f" = {fmt(subtotal, cur)}"


def _total_text(r: dict, cur: str, subtotal, discount, charges, total) -> str:
    steps = [_sum_text(r, cur, subtotal)]
    if discount:
        steps.append(f"minus {r['discount']['label'].lower()} {fmt(discount, cur)}")
    steps += [f"plus {t['label']} {fmt(t['amount'], cur)}" for t in r["taxes"] if not t["included"]]
    steps += [f"plus {c['label'].lower()} {fmt(c['amount'], cur)}" for c in r["charges"]]
    return ", ".join(steps) + (f" = {fmt(total, cur)}" if len(steps) > 1 else "")


def _m(x) -> float | None:
    return None if x is None else float(_cents(Decimal(str(x))))


def _q(q: Decimal) -> int | float:
    return int(q) if q == q.to_integral_value() else float(q)


def looks_like_bill(text: str) -> bool:
    """True when the text has line items a bill check can read (one 'qty x price' item, or two priced items)."""
    parts = _read_all(strip_request(text or ""))
    items = [p for _, p in parts if p["kind"] == "item"]
    return any(not p.get("single") for p in items) or len(items) >= 2 or (
        bool(items) and any(p["kind"] in ("total", "subtotal", "tax") for _, p in parts))
