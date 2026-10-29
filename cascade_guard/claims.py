"""Check a claim about numbers exactly: arithmetic ("17 x 23 = 391") and list statistics
("the average of 3, 7, 2, 9 is 5.5"). Exact rational arithmetic, so 0.1 + 0.2 = 0.3.

    check_math("17 x 23 = 401")   -> status "wrong", value "391"
    check_math("what is 15% of 240")  -> status "computed", value "36"
    check_stats("the average of 3, 7, 2, 9 is 5.5")  -> status "wrong", value "5.25"
"""
import ast
import re
from fractions import Fraction

MAX_LEN = 500
MAX_POWER = 64
MAX_DIGITS = 200

LEAD = re.compile(r"^\s*(?:hey|hi|ok|so)?[\s,]*(?:please\s+|can\s+you\s+|could\s+you\s+)?"
                  r"(?:check|verify|confirm|tell\s+me|calculate|compute|work\s+out|is\s+it\s+true\s+that|is\s+it\s+right"
                  r"\s+that|is|does|what\s+is|what's|whats|how\s+much\s+is)?\s*(?:if|whether|that)?\s*(?:[:,]|-(?!\s*\d))?\s*",
                  re.I)
TRAIL = re.compile(r"[\s?.!,]*(?:(?:is\s+(?:it|this|that)\s+)?(?:correct|right|true|ok|okay)|really)?[\s?.!]*$",
                   re.I)
WORDS = [(r"\bmultiplied\s+by\b|\btimes\b", "*"), (r"\bdivided\s+by\b|\bover\b", "/"), (r"\bplus\b", "+"),
         (r"\bminus\b", "-"), (r"\bis\s+equal\s+to\b|\bequals\b|\bequal\s+to\b|==", "="),
         (r"\bto\s+the\s+power\s+of\b", "^")]


class ClaimError(ValueError):
    pass


def _clean(text: str) -> str:
    t = (text or "")[:MAX_LEN].strip()
    t = TRAIL.sub("", LEAD.sub("", t, count=1))
    t = t.replace("×", "*").replace("÷", "/").replace("−", "-").replace("–", "-").replace(" ", " ")
    for rx, op in WORDS:
        t = re.sub(rx, op, t, flags=re.I)
    t = re.sub(r"(?<=[\d)])\s*[xX]\s*(?=[\d(])", "*", t)          # 3x4, 3 x 4
    t = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", t)                   # 1,299 -> 1299
    t = re.sub(r"[$₹€£¥]|\brs\.?|\binr\b|\busd\b", "", t, flags=re.I)
    t = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*of\s*", r"(\1/100)*", t, flags=re.I)
    t = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", t)
    return t.replace("^", "**").strip()


def _eval(expr: str) -> Fraction:
    if not re.fullmatch(r"[\d\s.+\-*/()%]+", expr) or not re.search(r"\d", expr):
        raise ClaimError("not an arithmetic expression")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ClaimError("not an arithmetic expression") from e
    return _node(tree.body)


def _node(n) -> Fraction:
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
        if isinstance(n.value, int) and abs(n.value) > 10 ** MAX_DIGITS:
            raise ClaimError("number too large")
        return Fraction(str(n.value)) if isinstance(n.value, float) else Fraction(n.value)
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
        v = _node(n.operand)
        return -v if isinstance(n.op, ast.USub) else v
    if isinstance(n, ast.BinOp):
        a, b = _node(n.left), _node(n.right)
        if isinstance(n.op, ast.Add):
            out = a + b
        elif isinstance(n.op, ast.Sub):
            out = a - b
        elif isinstance(n.op, ast.Mult):
            out = a * b
        elif isinstance(n.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            if b == 0:
                raise ClaimError("division by zero")
            out = a / b if isinstance(n.op, ast.Div) else (Fraction(a // b) if isinstance(n.op, ast.FloorDiv)
                                                            else a % b)
        elif isinstance(n.op, ast.Pow):
            if b.denominator != 1 or abs(b) > MAX_POWER:
                raise ClaimError(f"only whole-number powers up to {MAX_POWER} are supported")
            if a == 0 and b < 0:
                raise ClaimError("division by zero")
            out = a ** int(b)
        else:
            raise ClaimError("unsupported operation")
        if abs(out.numerator) > 10 ** MAX_DIGITS or out.denominator > 10 ** MAX_DIGITS:
            raise ClaimError("the result is too large")
        return out
    raise ClaimError("not an arithmetic expression")


def show(v: Fraction) -> str:
    """391 · 5.25 · 0.333333 (6 decimals for repeating decimals)."""
    if v.denominator == 1:
        return f"{v.numerator:,}" if abs(v.numerator) >= 10_000 else str(v.numerator)
    d = v.denominator
    while d % 2 == 0:
        d //= 2
    while d % 5 == 0:
        d //= 5
    if d == 1:  # terminates
        s = f"{float(v):.12f}".rstrip("0").rstrip(".")
        return s
    return f"{float(v):.6f}"


def _pretty(expr: str) -> str:
    e = expr.replace("**", "^").replace("*", " × ").replace("/", " ÷ ").replace("+", " + ")
    e = re.sub(r"(?<=[\d)])-", " - ", e)
    e = re.sub(r"\(\s*(\d+(?:\.\d+)?)\s*÷\s*100\s*\)", r"\1%", e)
    return re.sub(r"\s+", " ", e).strip()


def _matches(value: Fraction, claimed: Fraction, claimed_text: str):
    """(ok, note): exact, or right when rounded to the claim's decimal places."""
    if value == claimed:
        return True, None
    decimals = len(claimed_text.split(".", 1)[1]) if "." in claimed_text else 0
    scale = Fraction(10) ** decimals
    rounded = Fraction(int((abs(value) * scale + Fraction(1, 2)) // 1), 1) / scale * (1 if value >= 0 else -1)
    if decimals and rounded == claimed:
        return True, f"correct to {decimals} decimal place{'s' if decimals != 1 else ''}"
    if not decimals and value.denominator != 1 and rounded == claimed:
        return True, "correct when rounded to a whole number"
    return False, None


def check_math(text: str) -> dict:
    """Check (or work out) an arithmetic claim."""
    t = _clean(text)
    left, right = t, None
    if t.count("=") == 1:
        left, right = (x.strip() for x in t.split("="))
    elif re.search(r"\s(?:is|was|makes|gives|comes\s+to)\s", t, re.I):
        left, right = (x.strip() for x in re.split(r"\s(?:is|was|makes|gives|comes\s+to)\s", t, maxsplit=1,
                                                   flags=re.I))
    try:
        value = _eval(left)
    except ClaimError as e:
        if str(e) == "division by zero":
            return {"status": "unreadable", "summary": "That divides by zero, so it has no answer."}
        return {"status": "unreadable", "summary": f"I couldn't read that as a sum ({e}). Try something like "
                                                    "'17 x 23 = 391' or 'what is 15% of 240'."}
    expression = _pretty(left)
    out = {"expression": expression, "value": show(value), "claimed": None}
    if right is None or right == "":
        return {**out, "status": "computed", "summary": f"{expression} = {show(value)}."}
    try:
        claimed = _eval(right)
    except ClaimError:
        return {**out, "status": "computed", "summary": f"{expression} = {show(value)}. (I couldn't read the "
                                                       f"answer you gave, '{right}', as a number.)"}
    claimed_text = right.strip()
    out["claimed"] = show(claimed)
    ok, note = _matches(value, claimed, claimed_text)
    if ok:
        return {**out, "status": "correct", "note": note,
                "summary": f"Correct: {expression} = {show(value)}" + (f" ({note})." if note else ".")}
    return {**out, "status": "wrong",
            "summary": f"Wrong: {expression} = {show(value)}, not {show(claimed)}."}


OPS = {"sum": "sum", "total": "sum", "add up to": "sum", "adds up to": "sum", "sum to": "sum",
       "average": "average", "mean": "average", "median": "median",
       "max": "maximum", "maximum": "maximum", "highest": "maximum", "largest": "maximum", "biggest": "maximum",
       "min": "minimum", "minimum": "minimum", "lowest": "minimum", "smallest": "minimum",
       "count": "count", "number": "count"}
STAT = re.compile(r"\b(?P<op>sum|total|average|mean|median|max(?:imum)?|highest|largest|biggest|min(?:imum)?|lowest|"
                  r"smallest|count|number)\s+(?:value\s+)?of\s+(?:the\s+)?(?:numbers\s+|values\s+|list\s+)?:?\s*"
                  r"(?P<nums>[-\d.,\s]+?(?:\s+and\s+-?[\d.]+)?)\s*(?:is|=|equals|was|comes\s+to|are)\s+"
                  r"(?P<val>-?[\d.,]+)", re.I)
ADDS = re.compile(r"(?P<nums>-?[\d.]+(?:\s*,\s*-?[\d.]+|\s+and\s+-?[\d.]+|\s+-?[\d.]+)+)\s+(?P<op>add\s+up\s+to|"
                  r"adds\s+up\s+to|sum\s+to|total)\s+(?P<val>-?[\d.,]+)", re.I)


def _numbers(text: str) -> list[Fraction]:
    return [Fraction(x) for x in re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", " ").replace(" and ", " "))]


def check_stats(text: str) -> dict:
    """Check a claim about a list of numbers: sum, average, median, maximum, minimum or count."""
    t = (text or "")[:MAX_LEN]
    m = STAT.search(t) or ADDS.search(t)
    if not m:
        return {"status": "unreadable", "summary": "Try something like 'the average of 3, 7, 2, 9 is 5.25'."}
    op = OPS[re.sub(r"\s+", " ", m.group("op").lower())]
    nums = _numbers(m.group("nums"))
    if not nums or len(nums) > 1000:
        return {"status": "unreadable", "summary": "I couldn't find the list of numbers."}
    if op == "sum":
        value = sum(nums, Fraction(0))
    elif op == "average":
        value = sum(nums, Fraction(0)) / len(nums)
    elif op == "median":
        s = sorted(nums)
        k = len(s) // 2
        value = s[k] if len(s) % 2 else (s[k - 1] + s[k]) / 2
    elif op == "maximum":
        value = max(nums)
    elif op == "minimum":
        value = min(nums)
    else:
        value = Fraction(len(nums))
    claimed_text = m.group("val").replace(",", "").rstrip(".")
    claimed = Fraction(claimed_text)
    ok, note = _matches(value, claimed, claimed_text)
    listed = ", ".join(show(x) for x in nums)
    out = {"operation": op, "numbers": [show(x) for x in nums], "value": show(value), "claimed": show(claimed)}
    if ok:
        return {**out, "status": "correct", "note": note,
                "summary": f"Correct: the {op} of {listed} is {show(value)}" + (f" ({note})." if note else ".")}
    return {**out, "status": "wrong", "summary": f"Wrong: the {op} of {listed} is {show(value)}, not {show(claimed)}."}


def looks_like_math(text: str) -> bool:
    t = _clean(text)
    left = t.split("=")[0] if t.count("=") == 1 else re.split(r"\s(?:is|was|makes|gives|comes\s+to)\s", t, maxsplit=1,
                                                               flags=re.I)[0]
    return bool(re.fullmatch(r"[\d\s.+\-*/()%]+", left.strip() or "x")) and bool(re.search(r"\d\s*[-+*/]", left))


def looks_like_stats(text: str) -> bool:
    return bool(STAT.search((text or "")[:MAX_LEN]) or ADDS.search((text or "")[:MAX_LEN]))
