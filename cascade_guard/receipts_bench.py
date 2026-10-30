"""Benchmark the expense-receipt workflow on real receipts, without and with Cascade Guard.

The receipts are the public ICDAR 2019 SROIE set: 626 scanned shop receipts with their OCR text and the true
vendor, date and total. The same agents run twice on every receipt: once with no checks (what most pipelines do
today) and once guarded. What would be posted to the ledger is compared with the true date and total.

    posted_ok      posted, date and total right
    posted_wrong   posted, but the date or the total is wrong: a silent error
    stopped_wrong  stopped for a person, and it really was wrong: a caught error
    stopped_ok     stopped for a person although it was right: a false alarm

Splits: "dev" (receipts 0-299) was used while writing the checks; "test" (300-625) was not, so its numbers are
the honest ones. "all" is both.

    cascade-guard receipts benchmark --data path/to/sroie/data --split test
    cascade-guard receipts benchmark --data ... --model --shard 1/4 --out part1.json   # LLM agents
    cascade-guard receipts merge part*.json --md report.md
"""
from __future__ import annotations

import datetime as dt
import json
import time
from collections import Counter
from pathlib import Path

from cascade_guard.evaluation.stats import fmt, rate
from cascade_guard.receipts import Batch, load_sroie, money, process_receipt, score

SPLITS = {"dev": range(0, 300), "test": range(300, 626), "all": range(0, 626)}
SROIE_REPO = "https://github.com/zzzDavid/ICDAR-2019-SROIE"
SROIE_COMMIT = "27be4271b251c256f695acbade9a801bffe85994"
TODAY = dt.date(2026, 10, 30)  # fixed, so reruns give the same answers


def _reason_kind(reason: str) -> str:
    r = reason or ""
    for key, name in (("cash paid (", "total disagrees with cash paid minus change"),
                      ("is the cash paid", "the cash paid taken as the total"),
                      ("is the change", "the change taken as the total"),
                      ("is the tax", "a tax amount taken as the total"),
                      ("is the subtotal", "the subtotal taken as the total"),
                      ("larger amount", "a smaller amount taken as the total"),
                      ("rounded or net", "the unrounded total taken"),
                      ("not next to a total", "an amount not labelled as a total"),
                      ("does not appear", "a total that is not on the receipt"),
                      ("no total", "no total found"),
                      ("not a date", "no date found"),
                      ("date next to", "the wrong date on the receipt"),
                      ("is not on the receipt", "a date that is not on the receipt"),
                      ("in the future", "a date in the future"),
                      ("vendor", "the vendor"), ("business name", "the vendor"),
                      ("under the policy", "a policy decision that breaks the policy"),
                      ("YYYY-MM-DD", "a ledger date in the wrong format or wrong"),
                      ("the amount must be", "a ledger amount that differs from the receipt"),
                      ("category", "a category outside the chart of accounts"),
                      ("could not be used", "an unusable reply")):
        if key in r:
            return name
    return "other"


def run_benchmark(receipts: list[dict], client=None, max_retries: int = 2, progress=None) -> dict:
    """Both arms on every receipt with a true total; returns the raw rows (merge them with summarize)."""
    rows = []
    plain_batch, guard_batch = Batch(), Batch()
    for n, r in enumerate(receipts, 1):
        if money(r["truth"].get("total")) is None:
            rows.append({"id": r["id"], "skipped": "no true total in the dataset"})
            continue
        t0 = time.monotonic()
        try:
            base = process_receipt(r["text"], client=client, guard=False, batch=plain_batch, today=TODAY)
            t1 = time.monotonic()
            guarded = process_receipt(r["text"], client=client, guard=True, max_retries=max_retries,
                                      batch=guard_batch, today=TODAY)
        except Exception as e:  # the model server failed (timeout, out of memory): count it, carry on
            rows.append({"id": r["id"], "skipped": f"model error: {type(e).__name__}: {str(e)[:200]}"})
            continue
        t2 = time.monotonic()
        rows.append({
            "id": r["id"], "truth": {"total": r["truth"].get("total"), "date": r["truth"].get("date"),
                                     "company": r["truth"].get("company")},
            "without": {**score(base, r["truth"]), "fields": base["fields"], "entry": base["entry"],
                        "calls": base["agent_calls"], "seconds": round(t1 - t0, 3)},
            "with": {**score(guarded, r["truth"]), "fields": guarded["fields"], "entry": guarded["entry"],
                     "calls": guarded["agent_calls"], "seconds": round(t2 - t1, 3), "halted_at": guarded["halted_at"],
                     "rejections": [{"step": t["step"], "attempt": t["attempt"], "reason": t["reason"]}
                                    for t in guarded["trace"] if not t["passed"]]},
        })
        if progress:
            progress(n, len(receipts))
    return {"agents": "rules" if client is None else f"{client.provider}:{client.model}",
            "dataset": {"name": "ICDAR 2019 SROIE (train set, corrected annotations)", "repo": SROIE_REPO,
                        "commit": SROIE_COMMIT},
            "max_retries": max_retries, "rows": rows,
            "usage": client.describe()["usage"] if client is not None else None}


def summarize(result: dict) -> dict:
    rows = [r for r in result["rows"] if "skipped" not in r]
    n = len(rows)
    out: dict = {"agents": result["agents"], "dataset": result["dataset"], "receipts": n,
                 "skipped": len(result["rows"]) - n, "max_retries": result.get("max_retries"),
                 "skip_reasons": dict(Counter(r["skipped"].split(":")[0] for r in result["rows"] if "skipped" in r))}
    for arm in ("without", "with"):
        c = Counter(r[arm]["outcome"] for r in rows)
        out[arm] = {"outcomes": {k: c.get(k, 0) for k in ("posted_ok", "posted_wrong", "stopped_wrong", "stopped_ok")},
                    "silent_errors": rate(c.get("posted_wrong", 0), n),
                    "posted_correct": rate(c.get("posted_ok", 0), n),
                    "agent_calls_per_receipt": round(sum(r[arm]["calls"] for r in rows) / n, 2) if n else None,
                    "seconds_per_receipt": round(sum(r[arm]["seconds"] for r in rows) / n, 3) if n else None}
    base_wrong = [r for r in rows if r["without"]["outcome"] == "posted_wrong"]
    base_ok = [r for r in rows if r["without"]["outcome"] == "posted_ok"]
    out["caught"] = rate(sum(1 for r in base_wrong if r["with"]["outcome"] != "posted_wrong"), len(base_wrong))
    out["fixed_by_retry"] = rate(sum(1 for r in base_wrong if r["with"]["outcome"] == "posted_ok"), len(base_wrong))
    out["false_alarms"] = rate(sum(1 for r in base_ok if r["with"]["outcome"] == "stopped_ok"), len(base_ok))
    out["made_worse"] = sum(1 for r in base_ok if r["with"]["outcome"] == "posted_wrong")
    w, wo = out["with"]["silent_errors"], out["without"]["silent_errors"]
    out["silent_error_reduction"] = round(1 - w["k"] / wo["k"], 4) if wo["k"] else None
    kinds = Counter(_reason_kind(x["reason"]) for r in rows for x in r["with"]["rejections"])
    out["what_the_guard_caught"] = kinds.most_common()
    out["examples"] = [{"id": r["id"], "true_total": r["truth"]["total"], "true_date": r["truth"]["date"],
                        "agent_said": {k: (r["without"]["fields"] or {}).get(k) for k in ("total", "date")},
                        "guard": r["with"]["rejections"][0]["reason"], "result": r["with"]["outcome"]}
                       for r in base_wrong if r["with"]["rejections"]][:8]
    if result.get("usage"):
        out["model_usage"] = result["usage"]
    return out


def merge(parts: list[dict]) -> dict:
    first = parts[0]
    rows = [row for p in parts for row in p["rows"]]
    usage = None
    if any(p.get("usage") for p in parts):
        usage = {"calls": sum((p.get("usage") or {}).get("calls", 0) for p in parts),
                 "input_tokens": sum((p.get("usage") or {}).get("input_tokens", 0) for p in parts),
                 "output_tokens": sum((p.get("usage") or {}).get("output_tokens", 0) for p in parts)}
    return {**first, "rows": sorted(rows, key=lambda r: r["id"]), "usage": usage}


def to_markdown(s: dict, title: str = "Expense receipts: real receipts, without and with Cascade Guard") -> str:
    wo, w = s["without"], s["with"]
    o1, o2 = wo["outcomes"], w["outcomes"]
    skipped = (" · skipped: " + ", ".join(f"{v} ({k})" for k, v in s["skip_reasons"].items())) if s["skipped"] else ""
    lines = [f"# {title}", "",
             f"Agents: **{s['agents']}** · receipts: **{s['receipts']}** real scanned receipts "
             f"({s['dataset']['name']}, [{s['dataset']['repo']}]({s['dataset']['repo']}) at "
             f"`{s['dataset']['commit'][:7]}`){skipped}",
             "",
             "| | Without the guard | With Cascade Guard |", "|---|---:|---:|",
             f"| Posted, right | {o1['posted_ok']} | {o2['posted_ok']} |",
             f"| **Posted, wrong (silent error)** | **{o1['posted_wrong']}** | **{o2['posted_wrong']}** |",
             f"| Stopped for a person, really wrong | {o1['stopped_wrong']} | {o2['stopped_wrong']} |",
             f"| Stopped for a person, actually right (false alarm) | {o1['stopped_ok']} | {o2['stopped_ok']} |",
             f"| Silent error rate (95% CI) | {fmt(wo['silent_errors'])} | {fmt(w['silent_errors'])} |",
             f"| Agent calls per receipt | {wo['agent_calls_per_receipt']} | {w['agent_calls_per_receipt']} |",
             f"| Seconds per receipt | {wo['seconds_per_receipt']} | {w['seconds_per_receipt']} |", "",
             f"- Of the receipts the agents got wrong, the guard kept **{s['caught']['k']} of {s['caught']['n']}** out of "
             f"the ledger ({fmt(s['caught']).strip()}); **{s['fixed_by_retry']['k']}** of them were fixed automatically "
             f"when the agent was told what was wrong.",
             f"- False alarms: {s['false_alarms']['k']} of {s['false_alarms']['n']} receipts the agents got right were "
             f"stopped. Right answers the guard's retry turned wrong: {s['made_worse']}.",
             f"- Silent errors reduced by **{round((s['silent_error_reduction'] or 0) * 100)}%**." if
             s["silent_error_reduction"] is not None else "- No silent errors without the guard.", "",
             "What the guard caught (rejections, all steps):", ""]
    lines += [f"- {k}: {v}" for k, v in s["what_the_guard_caught"]]
    if s["examples"]:
        lines += ["", "Examples:", "", "| Receipt | True total / date | Agent said | Guard's reason | Result |",
                  "|---|---|---|---|---|"]
        for e in s["examples"]:
            lines.append(f"| {e['id']} | {e['true_total']} / {e['true_date']} | {e['agent_said']['total']} / "
                         f"{e['agent_said']['date']} | {str(e['guard']).replace('|', '/')[:140]} | {e['result']} |")
    lines += ["", "A result is right when the posted amount and date equal the dataset's answer. The guard never sees "
                  "the answers: it checks the agents' work against the receipt text only."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="cascade-guard receipts", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="action", required=True)
    b = sub.add_parser("benchmark", help="run both arms on the SROIE receipts")
    b.add_argument("--data", required=True, help="the SROIE data folder (with box/ and key/)")
    b.add_argument("--split", choices=list(SPLITS), default="test")
    b.add_argument("--limit", type=int, help="only the first N receipts of the split")
    b.add_argument("--shard", help="i/n: this run's share of the split, e.g. 2/4")
    b.add_argument("--model", action="store_true", help="LLM agents from the configured model (LLM_PROVIDER, ...)")
    b.add_argument("--max-retries", type=int, default=2)
    b.add_argument("--out", help="write the raw results (JSON) here")
    b.add_argument("--md", help="write the report (Markdown) here")
    m = sub.add_parser("merge", help="merge shard results into one report")
    m.add_argument("parts", nargs="+")
    m.add_argument("--out")
    m.add_argument("--md")
    m.add_argument("--title")
    args = p.parse_args(argv)
    if args.action == "merge":
        result = merge([json.loads(Path(f).read_text(encoding="utf-8")) for f in args.parts])
    else:
        receipts = load_sroie(args.data, SPLITS[args.split])
        if args.limit:
            receipts = receipts[:args.limit]
        if args.shard:
            i, n = (int(x) for x in args.shard.split("/"))
            receipts = receipts[i - 1::n]
        client = None
        if args.model:
            import os

            from cascade_guard.llm.client import make_client
            cache = os.getenv("LLM_CACHE") or None
            if cache and cache != ":memory:":
                Path(cache).parent.mkdir(parents=True, exist_ok=True)
            client = make_client(strict_prices=False, cache_path=cache)
        print(f"{len(receipts)} receipts, agents: {'model' if client else 'rules'}", flush=True)
        result = run_benchmark(receipts, client, args.max_retries,
                               progress=lambda k, n: print(f"  {k}/{n}", flush=True) if k % 10 == 0 else None)
    summary = summarize(result)
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    md = to_markdown(summary, args.title) if getattr(args, "title", None) else to_markdown(summary)
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8")
    print(md)
    return 0
