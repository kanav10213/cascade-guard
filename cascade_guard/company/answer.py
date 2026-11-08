"""Checked answers from the company's documents.

    result = answer("How many days of leave carry over?", workspace="default", client=client)
    result["answer"], result["verdict"], result["sources"], result["checks"]

1. The question is matched to the workspace's documents (kb.search).
2. If they hold the answer, the AI writes it from the numbered passages only, citing them like [1].
3. Cascade Guard checks the answer against those passages, without trusting the AI:
     every amount, number, date and percentage must be in the passages (or simple arithmetic on them), every
     quotation must be word for word, every citation must point to a real passage, and each sentence must
     match what its passage says.
   A failed check goes back to the AI with the reason (up to 2 times); a correction never replaces a better earlier
   answer, and an obvious citation slip ([2] when there is one passage) is fixed without asking. What still fails
   is removed and listed. Then an independent reading by a fresh AI call looks for claims the passages do not
   support; it can only warn (the answer is marked unconfirmed), never rewrite, because an AI's reading is not
   reliable enough to overrule an answer that passed the exact checks.
4. If the documents do not hold the answer: with CHAT_DOCS_ONLY=1 it says so; otherwise the general assistant
   answers (its numbers are checked by exact tools) and the answer is marked as not from the documents.
Without an AI (rules), it quotes the best passages word for word.

Verdicts: checked (passed first time) · fixed (caught and corrected) · trimmed (unsupported parts removed) ·
unconfirmed (the independent reading still has doubts) · not_found · quoted (no AI: exact passages) ·
general (not from the documents).
"""
from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from cascade_guard.company import kb

MAX_REPAIRS = 2
K = 5
NOT_FOUND = "NOT IN THE DOCUMENTS"

SYSTEM = """You are the assistant of COMPANY. You answer questions from employees and customers using ONLY the
numbered passages from the company's documents given below.
Rules:
- Use only facts stated in the passages. No outside knowledge, no guesses.
- After each sentence, cite the passage(s) it comes from, like [1] or [2][3].
- Copy every amount, number, date, percentage and name exactly as the passage writes it.
- If the passages do not answer the question, reply with exactly: NOT IN THE DOCUMENTS
- Be brief and clear: a few sentences, or short bullet points for lists. Answer in the language of the question.
Today is TODAY."""

CHECKER = """You check an answer against the passages it must be based on. Find every statement in the ANSWER that
the PASSAGES do not support: a wrong or changed number, date, name or condition; a fact the passages do not state;
a citation [n] to a passage that does not say it. Also say so if the answer does not answer the QUESTION that was
asked (while the passages do). Ignore style and wording.
Reply with ONLY a JSON object: {"verdict": "accept" | "revise", "problems": ["<short description>", ...]}"""


# ------------------------------------------------------------------ numbers

NUM = re.compile(r"(?<![\w.])(₹|rs\.?\s*|inr\s*|rm\s*|\$|usd\s*|€|£)?\s?(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
                 r"(\s*(?:%|per\s?cent|lakhs?|lacs?|crores?|cr\b|k\b))?", re.I)
_SMALL = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
         "seventeen eighteen nineteen twenty"
WORDS: dict[str, int | Decimal] = {w: i for i, w in enumerate(_SMALL.split())}
WORDS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
              "hundred": 100, "thousand": 1000, "dozen": 12, "half": Decimal("0.5"), "once": 1, "twice": 2})
CITE = re.compile(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]")
LIST_MARK = re.compile(r"(?m)^\s*(?:\d+[.)]|[-*•])\s+")


UNIT_AFTER = re.compile(r"\s*(?:-\s*)?(?:working\s+|calendar\s+|paid\s+|unused\s+|full\s+)?(?:days?|weeks?|months?|"
                        r"years?|hours?|hrs?|minutes?|mins?|nights?|times?|pieces?|characters?|chars?|employees?|"
                        r"people|persons?|units?|kg|km|mm|cm|inch(?:es)?|litres?|liters?|AM|PM|a\.m\.|p\.m\.|"
                        r"leaves?|holidays?|sick|casual|annual)\b", re.I)


def numbers_in(text: str) -> list[tuple[Decimal, str, bool]]:
    """(value, as written, strict) for each number. Strict: money, percentages, decimals, 13 or more, and any
    number with a unit (10 days, 2 years): these must come from the passages. A bare small count (3 options) may be
    the AI's own counting."""
    out = []
    for m in NUM.finditer(text or ""):
        cur, digits, unit = m.group(1), m.group(2), (m.group(3) or "").strip().lower()
        try:
            v = Decimal(digits.replace(",", ""))
        except InvalidOperation:
            continue
        if unit.startswith(("lakh", "lac")):
            v *= 100000
        elif unit.startswith(("crore", "cr")):
            v *= 10000000
        elif unit == "k":
            v *= 1000
        strict = bool(cur) or bool(unit) or "." in digits or v >= 13 or bool(UNIT_AFTER.match(text, m.end()))
        out.append((v, m.group(0).strip(), strict))
    for w in re.findall(r"\b[a-z]+(?:-[a-z]+)?\b", (text or "").lower()):
        parts = w.split("-")
        if all(p in WORDS for p in parts):
            out.append((Decimal(sum(Decimal(WORDS[p]) for p in parts)), w, False))
    return out


def _q(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"))


# Wording that shows the sentence works something out ("3 days × ₹1,500 = ₹4,500", "in total", "comes to").
CALC = re.compile(r"[=×÷+]|\d\s*[x*]\s*\d|\b(?:times|in total|a total of|totals?|comes? to|come to|comes? out|"
                  r"altogether|in all|sum|adds? up|works? out|multipl\w+|divid\w+|minus|plus|difference)\b", re.I)


def exact_numbers(texts: list[str]) -> set[Decimal]:
    return {_q(v) for t in texts for v, _, _ in numbers_in(t)}


def allowed_numbers(texts: list[str]) -> set[Decimal]:
    """Every number in the texts, plus simple arithmetic on them (sums, differences, products, ratios, a percentage
    of an amount): what a sentence that shows a calculation may honestly derive from its sources."""
    base = []
    for t in texts:
        base += [v for v, _, _ in numbers_in(t)]
    uniq = sorted(set(base))
    out = {_q(v) for v in uniq}
    few = uniq[:60]
    for i, a in enumerate(few):
        for b in few[i:]:
            out |= {_q(a + b), _q(abs(a - b)), _q(a * b)}
            if b:
                out.add(_q(a / b))
                out.add(_q(a * b / 100))
            if a:
                out.add(_q(b / a))
    return out


# ------------------------------------------------------------------ sentences

SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(*\[])")
LEAD_CITES = re.compile(r"^((?:\[\d+(?:\s*[,–-]\s*\d+)*\]\s*)+)(.*)$", re.S)


def _split_line(line: str) -> list[str]:
    """Sentences of one line, with a citation written after the full stop ("... month. [1] Next") kept with the
    sentence before it."""
    parts = [p.strip() for p in SPLIT.split(line) if p.strip()]
    out: list[str] = []
    for part in parts:
        m = LEAD_CITES.match(part)
        if m and out:
            out[-1] = out[-1] + " " + m.group(1).strip()
            part = m.group(2).strip()
        if part:
            out.append(part)
    return out


def sentences(text: str) -> list[str]:
    out = []
    for line in (text or "").splitlines():
        if line.strip():
            out += _split_line(line.strip())
    return out


def cited(sentence: str) -> set[int]:
    nums: set[int] = set()
    for group in CITE.findall(sentence):
        for part in re.split(r"\s*,\s*", group):
            if re.match(r"^\d+\s*[–-]\s*\d+$", part):
                a, b = (int(x) for x in re.split(r"\s*[–-]\s*", part))
                nums |= set(range(min(a, b), max(a, b) + 1)) if abs(b - a) < 20 else {a, b}
            elif part.strip().isdigit():
                nums.add(int(part))
    return nums


HEDGE = re.compile(r"\b(?:not (?:mention|say|state|specif|cover|includ)\w*|doesn'?t (?:say|mention|state|specify|cover)|"
                   r"do(?:es)? not (?:say|mention|state|specify|cover)|no information|could ?n[o']t find|"
                   r"not (?:in|found in) the (?:documents|passages|sources)|please (?:contact|check|ask)|"
                   r"for (?:more|further) (?:details|information))\b", re.I)


def is_not_found(text: str) -> bool:
    t = (text or "").strip()
    if NOT_FOUND.lower() in t.lower()[:200]:
        return True
    return len(t) < 220 and bool(re.search(r"\b(?:passages|documents|sources) (?:do not|don'?t) (?:contain|"
                                           r"answer|mention|say)|no information (?:about|on)", t, re.I))


# ------------------------------------------------------------------ the check

def check_answer(text: str, passages: list[dict], question: str, history_text: str = "") -> list[dict]:
    """Problems with an answer, judged only against its passages and the question. Each problem has `kind`,
    `text` (for the AI and the person), `sentence` and `hard` (hard problems are removed if the AI can't fix them;
    soft ones only mark the answer as unconfirmed)."""
    problems: list[dict] = []
    n = len(passages)
    body = text or ""
    if not body.strip():
        return [{"kind": "empty", "text": "the answer is empty", "sentence": "", "hard": True}]
    asked = [question, history_text]
    exact_all = exact_numbers([p["text"] for p in passages] + asked)
    derived_all = allowed_numbers([p["text"] for p in passages] + asked)
    passage_words = [set(kb.words(p["text"])) for p in passages]
    all_words = set().union(*passage_words) if passage_words else set()
    for s in sentences(body):
        refs = cited(s)
        bad = sorted(r for r in refs if r < 1 or r > n)
        if bad:
            problems.append({"kind": "citation", "hard": True, "sentence": s,
                             "text": f"[{bad[0]}] is not one of the passages (there are {n})"})
        plain = CITE.sub(" ", LIST_MARK.sub("", s))
        good = [r for r in refs if 1 <= r <= n]
        # A number must be in the passage the sentence cites (any passage if it cites none); a sentence that shows
        # a calculation may also use simple arithmetic on those numbers.
        if not good:  # no citation: judge it by the passages it matches best
            sw0 = set(kb.words(plain))
            overlap = [len(sw0 & passage_words[i]) for i in range(n)]
            top = max(overlap, default=0)
            near = [i + 1 for i in range(n) if top and overlap[i] >= 0.7 * top] or list(range(1, n + 1))
        else:
            near = good
        texts = [passages[r - 1]["text"] for r in near]
        calc = bool(CALC.search(plain))
        ok_here = allowed_numbers(texts + asked) if calc else exact_numbers(texts + asked)
        for v, raw, strict in numbers_in(plain):
            if strict and _q(v) not in ok_here:
                where = f"passage {', '.join(f'[{r}]' for r in good)}" if good else "the passages it matches"
                elsewhere = good and _q(v) in (derived_all if calc else exact_all)
                problems.append({"kind": "number", "hard": True, "sentence": s,
                                 "text": f"{raw} is not in {where}" + (" (it comes from a different passage)"
                                                                         if elsewhere else "")})
        for quote in re.findall(r"[\"“]([^\"”]{12,300})[\"”]", plain):
            norm = re.sub(r"\s+", " ", quote).strip().lower()
            if not any(norm in re.sub(r"\s+", " ", p["text"]).lower() for p in passages):
                problems.append({"kind": "quote", "hard": True, "sentence": s,
                                 "text": f"the quotation \"{quote[:80]}\" is not word for word in the passages"})
        if HEDGE.search(s):
            continue
        sw = [w for w in kb.words(plain) if not w.isdigit()]
        if len(set(sw)) >= 4 and not (refs and not good):
            pool = set().union(*(passage_words[r - 1] for r in refs if 1 <= r <= n)) if refs else all_words
            share = len(set(sw) & pool) / len(set(sw))
            if share < 0.34:
                where = f"passage{'s' if len(refs) > 1 else ''} {', '.join(f'[{r}]' for r in sorted(refs))}" \
                    if refs else "the passages"
                problems.append({"kind": "support", "hard": False, "sentence": s,
                                 "text": f"\"{s[:100]}\" does not match what {where} say"})
    return problems


def trim(text: str, problems: list[dict]) -> tuple[str, list[str]]:
    """The answer without the sentences that have hard problems: (kept text, removed sentences)."""
    bad = {p["sentence"] for p in problems if p["hard"] and p["sentence"]}
    kept_lines, removed = [], []
    for line in (text or "").splitlines():
        parts = _split_line(line.strip()) if line.strip() else []
        keep = []
        for part in parts:
            if part.strip() and part.strip() in bad:
                removed.append(part.strip())
            else:
                keep.append(part)
        joined = " ".join(keep).rstrip()
        if joined.strip() and not re.fullmatch(r"\s*(?:[-*•]|\d+[.)])\s*", joined):
            kept_lines.append(joined)
    return "\n".join(kept_lines).strip(), removed


# ------------------------------------------------------------------ prompts

def _company() -> str:
    return os.getenv("COMPANY_NAME", "the company")


def _today() -> str:
    from cascade_guard.assistant import _today as t
    return t()


def _passages_text(passages: list[dict]) -> str:
    out = []
    for i, p in enumerate(passages, 1):
        where = p["doc"] + (f", page {p['page']}" if p.get("page") else "")
        out.append(f"[{i}] ({where})\n{p['text']}")
    return "\n\n".join(out)


def _history_text(history: list[dict] | None, limit: int = 4) -> str:
    lines = []
    for h in (history or [])[-limit * 2:]:
        role = h.get("role")
        content = h.get("content") or h.get("text") or ""
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            lines.append(f"{'person' if role == 'user' else 'assistant'}: {content.strip()[:800]}")
    return "\n".join(lines)


def _prompt(question: str, passages: list[dict], history: str, previous: str | None = None,
            problems: list[str] | None = None) -> str:
    parts = ["PASSAGES:\n" + _passages_text(passages)]
    if history:
        parts.append("CONVERSATION SO FAR:\n" + history)
    parts.append("QUESTION:\n" + question)
    if previous is not None:
        parts.append("YOUR PREVIOUS ANSWER:\n" + previous[:3000])
        parts.append("IT WAS REJECTED BECAUSE:\n" + "\n".join(f"- {p}" for p in (problems or [])) +
                     "\nWrite a corrected answer that uses only what the passages say, with citations.")
    return "\n\n".join(parts)


def _ask(client, question, passages, history, n, previous=None, problems=None) -> str:
    system = SYSTEM.replace("COMPANY", _company()).replace("TODAY", _today())
    r = client.complete(system, _prompt(question, passages, history, previous, problems),
                        max_tokens=int(os.getenv("CHAT_MAX_TOKENS", "700")), temperature=0.0, sample=n,
                        purpose="company-answer")
    return (r.text or "").strip()


def review(client, question: str, passages: list[dict], text: str) -> dict:
    """The independent reading: a fresh call that sees only the passages, the question and the answer."""
    from cascade_guard.llm.prompts import parse_last_json
    user = f"PASSAGES:\n{_passages_text(passages)}\n\nQUESTION:\n{question}\n\nANSWER:\n{text[:4000]}"
    r = client.complete(CHECKER, user, max_tokens=400, temperature=0.0, purpose="company-check")
    data = parse_last_json(r.text)
    if not isinstance(data, dict) or data.get("verdict") not in ("accept", "revise"):
        return {"verdict": "unclear", "problems": []}
    problems = [str(p)[:300] for p in (data.get("problems") or []) if str(p).strip()][:4]
    if data["verdict"] == "revise" and not problems:
        return {"verdict": "accept", "problems": []}
    return {"verdict": data["verdict"], "problems": problems}


_reader: dict = {}


def reader_client(client):
    """The AI that does the independent reading: CHAT_READER_AI (any --ai value, e.g. a bigger local model or a
    cloud one) if set, else the AI that wrote the answer. A small model's reading warns too often."""
    spec = os.getenv("CHAT_READER_AI", "").strip()
    if not spec:
        return client
    if spec not in _reader:
        from cascade_guard.llm.presets import client_from_ai
        _reader[spec] = client_from_ai(spec, cache_path=None)
    return _reader[spec]


def _checker_on() -> bool:
    return os.getenv("CHAT_SECOND_CHECK", "on").strip().lower() not in ("off", "0", "no", "false")


def _docs_only() -> bool:
    return os.getenv("CHAT_DOCS_ONLY", "").strip().lower() in ("1", "on", "yes", "true")


# ------------------------------------------------------------------ the answer

def _source_list(passages: list[dict]) -> list[dict]:
    return [{"n": i, "doc": p["doc"], "doc_id": p.get("doc_id"), "page": p.get("page"),
             "text": re.sub(r"(?m)^#{1,6}\s*", "", p["text"])[:1200]} for i, p in enumerate(passages, 1)]


def _usage(client, before: dict | None) -> dict:
    if client is None or before is None:
        return {"ai_calls": 0, "tokens": 0}
    u = client.usage
    return {"ai_calls": u.calls - before["calls"],
            "tokens": (u.input_tokens + u.output_tokens) - before["tokens"]}


def answer(question: str, *, workspace: str = "default", client=None, history: list[dict] | None = None,
           mode: str = "auto", k: int = K, store=None) -> dict[str, Any]:
    """Answer one question. mode: auto (documents, else general) · documents (documents only) · general."""
    question = (question or "").strip()[:4000]
    real = client is not None and not getattr(client, "is_fake", False)
    before = {"calls": client.usage.calls, "tokens": client.usage.input_tokens + client.usage.output_tokens} \
        if real else None
    hist = _history_text(history)
    earlier = " ".join((h.get("content") or h.get("text") or "") for h in (history or [])[-4:]
                       if h.get("role") == "user" and isinstance(h.get("content") or h.get("text"), str))
    hits = [] if mode == "general" else kb.search(workspace, question, k=k, store=store, also=earlier)
    found = kb.relevant(hits)
    if not found and earlier and hits:  # a follow-up ("and for managers?") leans on the earlier question
        found = hits[0]["coverage"] >= 0.2 and hits[0]["score"] >= 2.0
    base: dict[str, Any] = {"question": question, "ai": {"provider": client.provider, "model": client.model}
                            if real else None}
    if not found:
        if mode == "documents" or (mode == "auto" and _docs_only()):
            return {**base, **_not_found(hits), "usage": _usage(client if real else None, before)}
        return {**base, **_general(question, history, client if real else None),
                "usage": _usage(client if real else None, before)}
    passages = [h for h in hits if h["score"] >= hits[0]["score"] * 0.25][:k]
    if not real:
        return {**base, **_quoted(passages), "usage": {"ai_calls": 0, "tokens": 0}}
    from cascade_guard.llm.client import BudgetExceeded, LLMCallError
    try:
        return {**base, **_grounded(question, passages, hist, client), "usage": _usage(client, before)}
    except (LLMCallError, BudgetExceeded) as e:  # the AI is down or busy: the exact passages are still useful
        busy = re.search(r"\b429\b|rate.?limit|too many", str(e), re.I)
        return {**base, **_quoted(passages), "usage": _usage(client, before),
                "notice": "The AI is busy right now, so these are the exact passages from your documents." if busy
                else f"The AI is not available right now ({str(e)[:160]}), so these are the exact passages."}


def _not_found(hits: list[dict]) -> dict:
    near = _source_list(hits[:2]) if hits else []
    text = "I couldn't find this in your company's documents."
    if near:
        text += " The closest passages are listed below; they may not answer it."
    return {"answer": text, "verdict": "not_found", "mode": "documents", "sources": near, "checks": [],
            "caught": [], "removed": [], "attempts": []}


def _quoted(passages: list[dict]) -> dict:
    best = passages[:2]
    lines = ["Here is what your documents say:"]
    for i, p in enumerate(best, 1):
        body = re.sub(r"\s*\n+\s*", " ", re.sub(r"(?m)^#{1,6}\s*", "", p["text"])).strip()
        lines.append(f"\n> {body[:700]}{'…' if len(body) > 700 else ''} [{i}]")
    return {"answer": "\n".join(lines), "verdict": "quoted", "mode": "documents", "sources": _source_list(best),
            "checks": ["word-for-word passages from your documents (no AI is connected, so nothing is reworded)"],
            "caught": [], "removed": [], "attempts": []}


def _general(question: str, history, client) -> dict:
    from cascade_guard.assistant import ask
    hist = [{"role": "assistant" if h.get("role") == "assistant" else "user", "text": h.get("content") or h.get("text")}
            for h in (history or []) if isinstance(h, dict)]
    res = ask(question, hist, client=client, use_model=client is not None)
    g = res.get("guard") or {}
    checks = list(g.get("checked") or [])
    caught = list(g.get("caught") or [])
    verdict = "general"
    if g.get("verdict") == "flagged":
        verdict = "unconfirmed"
    return {"answer": res.get("reply") or "", "verdict": verdict, "mode": "general", "sources": [],
            "checks": checks or ["not from your documents: a general answer"], "caught": caught, "removed": [],
            "attempts": [], "notice": res.get("notice") or "Not from your company's documents.",
            "answered_by": res.get("answered_by")}


def repair_citations(text: str, passages: list[dict]) -> tuple[str, list[str]]:
    """Fix a citation that points to no passage ("[2]" when there is one passage), when the sentence's numbers and
    words plainly come from one passage. Returns (text, what was fixed). A slip like this is not a wrong fact."""
    n = len(passages)
    if not n:
        return text, []
    fixed: list[str] = []
    words_of = [set(kb.words(p["text"])) for p in passages]
    nums_of = [exact_numbers([p["text"]]) for p in passages]

    def best_for(sentence: str) -> int | None:
        plain = CITE.sub(" ", sentence)
        strict = {_q(v) for v, _, st in numbers_in(plain) if st}
        sw = set(kb.words(plain))
        fits = [i for i in range(n) if strict <= nums_of[i]]
        if not fits:
            return None
        ranked = sorted(fits, key=lambda i: -len(sw & words_of[i]))
        if len(ranked) > 1 and len(sw & words_of[ranked[0]]) == len(sw & words_of[ranked[1]]):
            return None  # two passages fit equally well: don't guess
        return ranked[0] + 1

    out_lines = []
    for line in (text or "").splitlines():
        parts = _split_line(line.strip()) if line.strip() else []
        new_parts = []
        for sent in parts:
            bad = [r for r in cited(sent) if r < 1 or r > n]
            if bad:
                good = best_for(sent)
                if good is not None:
                    for b in bad:
                        sent = re.sub(rf"\[{b}\]", f"[{good}]", sent)
                    fixed.append(f"citation [{bad[0]}] corrected to [{good}]")
            new_parts.append(sent)
        out_lines.append(" ".join(new_parts) if parts else line)
    return "\n".join(out_lines), fixed


def _hard(problems: list[dict]) -> int:
    return sum(1 for p in problems if p["hard"])


def _grounded(question: str, passages: list[dict], hist: str, client) -> dict:
    """Write, check, correct. Only the exact checks (numbers, quotations, citations) can send an answer back: they
    are reliable. The independent AI reading can only warn, because a small model's reading is not reliable enough to
    rewrite an answer on its own say-so. A correction never replaces a better earlier answer."""
    from cascade_guard.llm.client import BudgetExceeded, LLMCallError
    attempts: list[dict] = []
    caught: list[str] = []
    repaired: list[str] = []
    best: dict | None = None          # the attempt with the fewest hard problems so far
    n = 0
    text = _ask(client, question, passages, hist, n)
    while True:
        if is_not_found(text):
            attempts.append({"answer": text, "problems": ["the AI said the passages don't answer it"]})
            if best is None:  # it said so straight away: an honest "not in the documents"
                res = _not_found(passages)
                res["attempts"] = attempts
                res["checks"] = ["the AI found no answer in the passages, and said so instead of guessing"]
                return res
            break  # it gave up while correcting: keep the earlier answer
        text, fixes = repair_citations(text, passages)
        repaired += fixes
        problems = check_answer(text, passages, question, hist)
        hard = [p for p in problems if p["hard"]]
        attempts.append({"answer": text, "problems": [p["text"] for p in problems]})
        if best is None or _hard(problems) < _hard(best["problems"]):
            best = {"text": text, "problems": problems, "n": len(attempts)}
        if not hard or n >= MAX_REPAIRS:
            break
        caught += [p["text"] for p in hard if p["text"] not in caught]
        n += 1
        text = _ask(client, question, passages, hist, n, previous=text, problems=[p["text"] for p in hard])
    assert best is not None
    text, final_problems = best["text"], best["problems"]
    removed: list[str] = []
    verdict = "checked" if best["n"] == 1 and not caught else "fixed"
    if _hard(final_problems):
        text, removed = trim(text, final_problems)
        verdict = "trimmed"
        if not CITE.search(text):
            res = _not_found(passages)
            res.update({"answer": "I couldn't give an answer I could check against your documents. The passages that "
                                  "come closest are listed below.", "attempts": attempts, "caught": caught,
                        "removed": removed, "verdict": "not_found",
                        "checks": ["every sentence the AI wrote had a claim the passages don't support, so none "
                                   "was shown"]})
            return res
    reviewed: dict = {"verdict": "off", "problems": []}
    if _checker_on():
        try:
            reviewed = review(reader_client(client), question, passages, text)
        except (LLMCallError, BudgetExceeded) as e:
            reviewed = {"verdict": "unavailable", "problems": [], "error": str(e)[:200]}
    soft = [p["text"] for p in final_problems if not p["hard"]]
    doubts = soft + (reviewed["problems"] if reviewed["verdict"] == "revise" else [])
    if doubts and verdict != "trimmed":
        verdict = "unconfirmed"
    checks = ["every amount, number, date and quotation was compared with the passages",
              "every citation points to a passage that exists"]
    checks.append({"accept": "read again by an independent AI call: no unsupported claims found",
                   "revise": "read again by an independent AI call: it has doubts (shown above)",
                   "unclear": "read again by an independent AI call: no clear verdict",
                   "unavailable": "the independent reading could not run (the AI was busy)",
                   "off": "the independent reading is turned off (CHAT_SECOND_CHECK=off)"}[reviewed["verdict"]])
    out = {"answer": text, "verdict": verdict, "mode": "documents", "sources": _source_list(passages),
           "checks": checks, "caught": caught, "removed": removed, "attempts": attempts, "repaired": repaired,
           "review": reviewed}
    if doubts:
        out["notice"] = "Check before relying on it: " + "; ".join(doubts[:3])
    if removed:
        out["notice"] = (f"{len(removed)} sentence{'s' * (len(removed) != 1)} with claims the documents don't "
                         "support were removed." + (" Also: " + "; ".join(doubts[:2]) if doubts else ""))
    return out


def footer(result: dict) -> str:
    """A short plain-text note for chat apps that only show text: sources and what was checked."""
    lines = []
    if result.get("sources") and result.get("mode") == "documents":
        lines.append("Sources: " + "; ".join(
            f"[{s['n']}] {s['doc']}" + (f" p.{s['page']}" if s.get("page") else "") for s in result["sources"]))
    label = {"checked": "✓ Checked against your documents by Cascade Guard",
             "fixed": "✓ Checked by Cascade Guard (a mistake was caught and fixed)",
             "trimmed": "✓ Checked by Cascade Guard (unsupported sentences were removed)",
             "unconfirmed": "⚠ Cascade Guard could not fully confirm this answer",
             "quoted": "✓ Quoted word for word from your documents",
             "not_found": "Cascade Guard: not found in your documents",
             "general": "Note: a general answer, not from your documents"}.get(result.get("verdict", ""), "")
    if label:
        lines.append(label)
    if result.get("notice") and result.get("verdict") in ("unconfirmed", "trimmed"):
        lines.append(result["notice"])
    return "\n".join(lines)
