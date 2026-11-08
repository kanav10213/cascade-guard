"""How well does the company chat answer, with a real AI? Questions about the sample company, each with the facts a
right answer must contain, and questions the documents do not answer at all.

    python -m cascade_guard.company.bench --ai ollama:qwen2.5:7b --out chat-bench.json --md chat-bench.md

For each question it compares the AI's first answer (what a plain document chatbot would show) with what Cascade
Guard shows after checking:
  right       every expected fact is in the answer
  wrong       an answer was shown, but a fact is missing or different
  made up     the documents don't answer it, but an answer was shown anyway
  said so     it said the documents don't answer it (right for those questions; for the others, a refusal)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from decimal import Decimal, InvalidOperation

QUESTIONS: list[dict] = [
    {"q": "How many days of paid annual leave does a permanent employee get each year?", "expect": ["24"]},
    {"q": "How many unused annual leave days can be carried forward to the next year?", "expect": ["10"]},
    {"q": "How many sick days do we get per year?", "expect": ["8"]},
    {"q": "When do I need a doctor's certificate for sick leave?", "expect": ["2"]},
    {"q": "How long is maternity leave?", "expect": ["26"]},
    {"q": "How long is paternity leave, and by when must it be taken?", "expect": ["10", "3"]},
    {"q": "How many days in advance must leave be requested?", "expect": ["7"]},
    {"q": "What is the notice period for a manager after confirmation?", "expect": ["90"]},
    {"q": "How long is probation, and what is the notice period during probation?", "expect": ["6", "15"]},
    {"q": "How many days a week can I work from home?", "expect": ["2"]},
    {"q": "Who approves an expense of 20,000 rupees?", "expect": ["department head"]},
    {"q": "Up to what amount can my manager approve an expense?", "expect": ["5,000"]},
    {"q": "Within how many days must an expense claim be submitted?", "expect": ["30"]},
    {"q": "Above what amount is an original receipt needed?", "expect": ["500"]},
    {"q": "What is the daily allowance for travel in Pune?", "expect": ["1,500"]},
    {"q": "What is the daily allowance for travel in Indore?", "expect": ["1,000"]},
    {"q": "What is the hotel limit per night in a metro city for a manager?", "expect": ["9,000"]},
    {"q": "What is the hotel limit per night in a non-metro city?", "expect": ["4,500"]},
    {"q": "What train class can employees book?", "expect": ["2-tier"]},
    {"q": "How long must passwords be?", "expect": ["12"]},
    {"q": "How soon must a lost laptop be reported?", "expect": ["24"]},
    {"q": "What is the warranty on the angle grinder?", "expect": ["1 year"]},
    {"q": "What is the price of the cordless drill, excluding GST?", "expect": ["6,499"]},
    {"q": "What is the GST rate on the vernier caliper?", "expect": ["12"]},
    {"q": "Within how many days can an unused product be returned?", "expect": ["15"]},
    {"q": "What are the phone support hours?", "expect": ["9", "7"]},
    # The documents don't answer these: a made-up answer is the failure.
    {"q": "What is the company's dress code?", "absent": True},
    {"q": "How much is the annual performance bonus?", "absent": True},
    {"q": "Is there a gym allowance, and how much is it?", "absent": True},
    {"q": "What is the price of the KPT-CS-200 circular saw?", "absent": True},
]

SHOWN = ("checked", "fixed", "trimmed", "unconfirmed", "quoted")


def _nums(text: str) -> set[Decimal]:
    out = set()
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            out.add(Decimal(m.group(0).replace(",", "")).normalize())
        except InvalidOperation:
            continue
    return out


def has_fact(text: str, fact: str) -> bool:
    if re.fullmatch(r"[\d,.]+", fact):
        return Decimal(fact.replace(",", "")).normalize() in _nums(text)
    m = re.fullmatch(r"(\d[\d,]*(?:\.\d+)?)\s+([A-Za-z]+)", fact)  # "1 year": the number and the word
    if m:
        return Decimal(m.group(1).replace(",", "")).normalize() in _nums(text) and \
            m.group(2).lower().rstrip("s") in text.lower()
    return fact.lower() in (text or "").lower()


def judge(item: dict, text: str, shown: bool) -> str:
    if not shown:
        return "said so"
    if item.get("absent"):
        return "made up"
    return "right" if all(has_fact(text, f) for f in item["expect"]) else "wrong"


def run(client, workspace: str = "bench", progress=print) -> dict:
    from cascade_guard.company import answer as A
    from cascade_guard.company.answer import is_not_found
    from cascade_guard.company.api import load_samples
    from cascade_guard.company.kb import list_documents
    if not list_documents(workspace):
        load_samples(workspace, by="benchmark")
    rows = []
    for i, item in enumerate(QUESTIONS, 1):
        t0 = time.monotonic()
        res = A.answer(item["q"], workspace=workspace, client=client, mode="documents")
        secs = time.monotonic() - t0
        first = (res.get("attempts") or [{}])[0].get("answer")
        if first is None:
            # Cascade Guard found no passage that answers it, so it never asked the AI. A plain document chatbot
            # sends the closest passages to the AI anyway: ask the same way, for a fair "without" column.
            from cascade_guard.company.kb import search
            hits = search(workspace, item["q"], k=A.K)
            first = A._ask(client, item["q"], hits, "", 0) if hits else A.NOT_FOUND
        first_text, first_shown = first, not is_not_found(first)
        row = {"q": item["q"], "absent": bool(item.get("absent")), "expect": item.get("expect", []),
               "first": first_text, "first_outcome": judge(item, first_text, first_shown),
               "final": res["answer"], "verdict": res["verdict"],
               "final_outcome": judge(item, res["answer"], res["verdict"] in SHOWN),
               "caught": res.get("caught", []), "removed": res.get("removed", []),
               "ai_calls": res.get("usage", {}).get("ai_calls", 0), "seconds": round(secs, 1)}
        rows.append(row)
        if progress:
            progress(f"{i:>2}/{len(QUESTIONS)} {row['first_outcome']:>8} -> {row['final_outcome']:<8} "
                     f"({row['verdict']}, {row['ai_calls']} calls, {secs:.0f} s)  {item['q'][:60]}")
    return {"rows": rows, "summary": summarize(rows)}


def summarize(rows: list[dict]) -> dict:
    def count(key, outcome, absent=None):
        return sum(1 for r in rows if r[key] == outcome and (absent is None or r["absent"] == absent))
    answerable = [r for r in rows if not r["absent"]]
    return {
        "questions": len(rows), "answerable": len(answerable), "not_in_documents": len(rows) - len(answerable),
        "first": {"right": count("first_outcome", "right"), "wrong": count("first_outcome", "wrong"),
                  "refused": count("first_outcome", "said so", False), "made_up": count("first_outcome", "made up"),
                  "said_not_found": count("first_outcome", "said so", True)},
        "final": {"right": count("final_outcome", "right"), "wrong": count("final_outcome", "wrong"),
                  "refused": count("final_outcome", "said so", False), "made_up": count("final_outcome", "made up"),
                  "said_not_found": count("final_outcome", "said so", True)},
        "verdicts": {v: sum(1 for r in rows if r["verdict"] == v) for v in sorted({r["verdict"] for r in rows})},
        "ai_calls_per_question": round(sum(r["ai_calls"] for r in rows) / max(1, len(rows)), 2),
        "seconds_per_question": round(sum(r["seconds"] for r in rows) / max(1, len(rows)), 1),
    }


def to_markdown(result: dict, title: str) -> str:
    s = result["summary"]
    f, g = s["first"], s["final"]
    lines = [f"# {title}", "",
             f"{s['questions']} questions about the sample company: {s['answerable']} the documents answer, "
             f"{s['not_in_documents']} they don't.", "",
             "| | The AI's first answer (no guard) | What Cascade Guard shows |", "|---|---:|---:|",
             f"| Right (all expected facts) | {f['right']} of {s['answerable']} | {g['right']} of {s['answerable']} |",
             f"| **Wrong answer shown** | **{f['wrong']}** | **{g['wrong']}** |",
             f"| Said \"not in the documents\" although they answer it | {f['refused']} | {g['refused']} |",
             f"| **Made-up answer** (the documents don't say) | **{f['made_up']}** of {s['not_in_documents']} | "
             f"**{g['made_up']}** of {s['not_in_documents']} |",
             f"| Said \"not in the documents\" (right) | {f['said_not_found']} | {g['said_not_found']} |", "",
             f"AI calls per question: {s['ai_calls_per_question']} · seconds per question: "
             f"{s['seconds_per_question']} · verdicts: " + ", ".join(f"{k} {v}" for k, v in s["verdicts"].items()),
             "", "## Answers that changed", ""]
    for r in result["rows"]:
        if r["first_outcome"] != r["final_outcome"] or r["caught"]:
            lines += [f"**{r['q']}** ({r['first_outcome']} → {r['final_outcome']}, {r['verdict']})", "",
                      f"- first: {r['first'][:300]}", f"- shown: {r['final'][:300]}"]
            lines += [f"- caught: {c}" for c in r["caught"][:3]]
            lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m cascade_guard.company.bench")
    p.add_argument("--ai", help="the AI to test (default: CASCADE_AI)")
    p.add_argument("--out")
    p.add_argument("--md")
    p.add_argument("--title", default="Company chat with a real AI")
    a = p.parse_args(argv)
    from cascade_guard.config import load_env
    load_env()
    if a.ai:
        os.environ["CASCADE_AI"] = a.ai
    from cascade_guard.llm.client import make_client
    client = make_client(strict_prices=False, cache_path=None)
    if client.is_fake:
        raise SystemExit("name a real AI with --ai (this measures an AI's answers)")
    os.environ.setdefault("CASCADE_DB", ":memory:")
    result = run(client)
    result["ai"] = {"provider": client.provider, "model": client.model}
    print(json.dumps(result["summary"], indent=2))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)
    if a.md:
        with open(a.md, "w", encoding="utf-8") as fh:
            fh.write(to_markdown(result, f"{a.title}: {client.model}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
