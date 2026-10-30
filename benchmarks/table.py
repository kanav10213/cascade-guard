"""One results table from several benchmark result files (JSON from `cascade-guard receipts benchmark --out`).

    python benchmarks/table.py results/*.json
"""
import json
import sys
from collections import Counter

from cascade_guard.receipts import money, parse_date


def breakdown(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    rows = [r for r in d["rows"] if "skipped" not in r]
    out = {"agents": d["agents"], "n": len(rows)}
    for arm in ("without", "with"):
        c: Counter = Counter()
        for r in rows:
            a, e = r[arm], r[arm]["entry"] or {}
            t_total, t_date = money(r["truth"]["total"]), parse_date(r["truth"]["date"])
            if a["outcome"].startswith("posted"):
                d_val = parse_date(e.get("date"))
                c["amount"] += money(e.get("amount")) != t_total
                c["day"] += d_val != t_date
                c["format"] += d_val == t_date and e.get("date") != (t_date.isoformat() if t_date else None)
                c["any"] += a["outcome"] == "posted_wrong"
            else:
                c["stopped"] += 1
                f = r["without"]["fields"] or {}
                c["stopped_but_read_right"] += (money(f.get("total")) == t_total and parse_date(f.get("date")) == t_date)
        out[arm] = dict(c)
    out["fixed"] = sum(1 for r in rows if r["without"]["outcome"] == "posted_wrong" and r["with"]["outcome"] == "posted_ok")
    return out


def pct(k, n):
    return f"{k} ({100 * k / n:.0f}%)" if n else str(k)


if __name__ == "__main__":
    print("| Agents | Receipts | Wrong amount posted: without → with | Wrong day posted | Any wrong entry posted | "
          "Fixed automatically | Stopped for a person (of them read right) |")
    print("|---|---:|---|---|---|---:|---|")
    for p in sys.argv[1:]:
        b = breakdown(p)
        n, wo, w = b["n"], b["without"], b["with"]
        print(f"| {b['agents']} | {n} | {pct(wo.get('amount', 0), n)} → **{pct(w.get('amount', 0), n)}** | "
              f"{wo.get('day', 0)} → {w.get('day', 0)} | {pct(wo.get('any', 0), n)} → **{pct(w.get('any', 0), n)}** | "
              f"{b['fixed']} | {pct(w.get('stopped', 0), n)} ({w.get('stopped_but_read_right', 0)}) |")
