"""LLM evaluation from the command line.

  py -m experiments.llm_eval --simulated                     free dry run (simulated model)
  py -m experiments.llm_eval                                 real model; prints the plan, asks to confirm
  py -m experiments.llm_eval --yes --price-in 1 --price-out 5   skip the prompt, show a cost estimate

The real run needs the ANTHROPIC_API_KEY environment variable. Model: CG_MODEL or --model.
"""
import argparse
import json
import sys

from app.llm.client import get_client
from app.llm.experiments import PARTS, plan, run_all


def pct(s):
    return "n/a" if not s or s["rate"] is None else f"{s['rate']*100:.1f}% [{s['ci'][0]*100:.1f}-{s['ci'][1]*100:.1f}] ({s['k']}/{s['n']})"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--parts", nargs="+", choices=PARTS, default=list(PARTS))
    p.add_argument("--agent-trials", type=int, default=40)
    p.add_argument("--validator-trials", type=int, default=15, help="per cell")
    p.add_argument("--e2e-trials", type=int, default=40)
    p.add_argument("--hard", action="store_true", help="longer lists, bigger numbers (more natural errors)")
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--inject", type=float, default=0.0, help="extra injected-fault chance per agent output")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--model", default=None)
    p.add_argument("--simulated", action="store_true", help="use the simulated model (free, NOT real results)")
    p.add_argument("--yes", action="store_true", help="do not ask before spending")
    p.add_argument("--price-in", type=float, help="USD per million input tokens (for the estimate)")
    p.add_argument("--price-out", type=float, help="USD per million output tokens")
    p.add_argument("--out", default="llm_report.json")
    a = p.parse_args()

    cfg = {"parts": a.parts, "agent_trials": a.agent_trials, "validator_trials": a.validator_trials,
           "e2e_trials": a.e2e_trials, "hard": a.hard, "retries": a.retries, "inject": a.inject,
           "seed": a.seed, "workers": a.workers}
    pl = plan(cfg)
    print("PLAN")
    print(f"  model calls: {pl['total_min']} to {pl['total_max']} (the upper figure assumes every retry happens)")
    for k, v in pl["calls_max"].items():
        print(f"    {k:22s} {pl['calls_min'][k]} to {v}")
    print(f"  tokens (rough): input {pl['tokens_min']['input']}-{pl['tokens_max']['input']}, "
          f"output {pl['tokens_min']['output']}-{pl['tokens_max']['output']}")
    if a.price_in is not None and a.price_out is not None:
        lo = pl["tokens_min"]["input"] / 1e6 * a.price_in + pl["tokens_min"]["output"] / 1e6 * a.price_out
        hi = pl["tokens_max"]["input"] / 1e6 * a.price_in + pl["tokens_max"]["output"] / 1e6 * a.price_out
        print(f"  estimated cost: ${lo:.2f} to ${hi:.2f} at your prices")
    elif not a.simulated:
        print("  cost: pass --price-in and --price-out (USD per million tokens) to see an estimate")

    if not a.simulated and not a.yes:
        if input("Type 'yes' to spend this on real API calls: ").strip().lower() != "yes":
            print("Cancelled, nothing was spent.")
            return 1
    client = get_client(simulated=a.simulated, model=a.model, seed=a.seed)
    print(f"\nRunning with {client.name}{' (SIMULATED, not real results)' if client.simulated else ''} ...")
    rep = run_all(client, cfg)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"plan": pl, **rep}, f, indent=2)

    if "agents" in rep:
        g = rep["agents"]
        print("\nAGENT ACCURACY (clean input)")
        print("  analyst p1          ", pct(g["analyst"]))
        print("  trend   p2          ", pct(g["trend"]))
        print(f"  predicted p1*p2      {g['product_predicted']*100:.1f}%")
        print("  observed both right ", pct(g["joint_observed"]))
    if "validator" in rep:
        print("\nVALIDATOR (rejects = flags as invalid)")
        for s, r in rep["validator"].items():
            print(f"  [{s}] false positives:", pct(r["false_positive"]), f"| unparseable: {r['unparseable']}")
            for k, cells in r["detection"].items():
                print(f"       detects {k:10s}", "  ".join(f"{m}: {pct(v)}" for m, v in cells.items()))
    if "end_to_end" in rep:
        e = rep["end_to_end"]
        print("\nEND TO END (correct / wrong delivered / halted)")
        for arm, v in e["arms"].items():
            print(f"  {arm:20s}", pct(v["correct"]), "|", f"wrong {v['wrong']['k']}", f"halted {v['halted']['k']}")
    m = rep["meta"]
    print(f"\nCalls made: {m['calls']} (API errors: {m['api_errors']}), tokens in/out: {m['input_tokens']}/{m['output_tokens']}")
    print("Saved", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
