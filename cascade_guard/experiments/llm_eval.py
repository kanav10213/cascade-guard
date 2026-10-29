"""LLM evaluation (brief priority 1).

    cascade-guard eval agents   # 1. per-agent accuracy p_i on clean input
    cascade-guard eval detect   # 2. validator detection by error kind/size, false positives,
                                #    4. naive vs recompute prompt
    cascade-guard eval chain    # 3. end-to-end success vs prod p_i, with and without guards
    cascade-guard eval all      # all three, one cost estimate and one confirmation
    cascade-guard eval check    # one tiny call: do the key, model name and reply format work?

Before any model call the script prints the number of calls and the estimated
cost and asks for confirmation (skip with --yes). --dry-run prints the plan
only. --max-cost is a hard cap: the run stops when actual spend reaches it.
--fake uses the offline simulator: no key, no cost, numbers are SIMULATED.

Every request is cached in .llm_cache/, so re-running (or resuming after the
cap was hit) does not pay twice for the same call, and all chain arms see the
same first answer from each agent (a paired comparison).
"""
import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cascade_guard.agents.llm_agents import DEFAULT_LLM_STAGES, LLMAgent, agent_input, build_agents
from cascade_guard.agents.pipeline import AGENTS, STAGES, compute_stats, compute_trend
from cascade_guard.config import load_env
from cascade_guard.evaluation.error_injector import ErrorInjector
from cascade_guard.evaluation.runner import run_arm, stage_truth
from cascade_guard.evaluation.stats import bootstrap_product, fmt, rate
from cascade_guard.guard.cascade_guard import CascadeGuard
from cascade_guard.guard.llm_guard import LLMGuard
from cascade_guard.llm.client import (
    BudgetExceeded,
    LLMCallError,
    ResponseCache,
    UnknownPrice,
    approx_tokens,
    make_client,
    resolve_prices,
)
from cascade_guard.llm.prompts import (
    AGENT_MAX_TOKENS,
    VALIDATOR_PROMPTS,
    agent_messages,
    validator_messages,
    validator_source,
)

PROMPTS = ("naive", "recompute")
CHAIN_ARMS = ("rules_noguard", "llm_noguard", "llm_rule_guard", "llm_guard_naive", "llm_guard_recompute",
              "llm_guard_recompute_guided")


# ===================================================================== inputs

def make_llm_inputs(n: int, seed: int, min_len: int, max_len: int) -> list:
    """Integer lists with a mix of shapes, so the trend task is not trivial:
    random 40%, strictly increasing 20%, strictly decreasing 20%, and
    nearly sorted 20% (sorted with one adjacent swap or one repeated value,
    so the right answer is "mixed" although it looks sorted)."""
    rng = random.Random(f"llm-inputs:{seed}")
    out = []
    for _ in range(n):
        length = rng.randint(min_len, max_len)
        r = rng.random()
        if r < 0.4:
            shape, nums = "random", [rng.randint(1, 999) for _ in range(length)]
        elif r < 0.6:
            shape, nums = "increasing", sorted(rng.sample(range(1, 1000), length))
        elif r < 0.8:
            shape, nums = "decreasing", sorted(rng.sample(range(1, 1000), length), reverse=True)
        else:
            shape = "near_sorted"
            nums = sorted(rng.sample(range(1, 1000), length), reverse=rng.random() < 0.5)
            i = rng.randrange(length - 1)
            if rng.random() < 0.5:
                nums[i], nums[i + 1] = nums[i + 1], nums[i]
            else:
                nums[i + 1] = nums[i]
        out.append({"raw": ", ".join(map(str, nums)), "nums": nums, "len": length, "shape": shape})
    return out


def len_bucket(n: int) -> str:
    for hi, label in ((10, "<=10"), (20, "11-20"), (30, "21-30")):
        if n <= hi:
            return label
    return ">30"


# ===================================================================== planning

def _expected_out(purpose: str, length: int) -> int:
    if purpose.startswith("agent"):
        return 35
    if purpose == "validator:naive":
        return 45
    return 60 + 9 * length  # recompute: a line of working per number


def planned_call(purpose, system, user, max_tokens, length, temperature=0.0, sample=0):
    return {"purpose": purpose, "system": system, "user": user, "max_tokens": max_tokens,
            "temperature": temperature, "sample": sample,
            "in": approx_tokens(system + user), "out": _expected_out(purpose, length)}


def plan_agent_calls(item, stage, attempt=0):
    nums = item["nums"]
    data = agent_input(stage, item["raw"], {"extract": {"numbers": nums}, **stage_truth(item["raw"])})
    system, user = agent_messages(stage, data)
    return planned_call(f"agent:{stage}", system, user, AGENT_MAX_TOKENS, item["len"],
                        temperature=0.0 if attempt == 0 else 1.0, sample=attempt)


def plan_validator_call(prompt, stage, source, output, length):
    system, user, mt = validator_messages(prompt, stage, source, output)
    return planned_call(f"validator:{prompt}", system, user, mt, length)


def summarise_plan(plan: list, extra_worst: list, client, cache) -> dict:
    """plan: calls that will certainly be made. extra_worst: calls that may be
    made (retries). Calls already in the cache are free."""
    def is_cached(c):
        if cache is None:
            return False
        return cache.get(client.cache_key(c["system"], c["user"], c["max_tokens"],
                                          c["temperature"], c["sample"])) is not None

    seen, new = set(), []
    for c in plan:
        k = (c["system"], c["user"], c["max_tokens"], c["temperature"], c["sample"])
        if k not in seen:
            seen.add(k)
            if not is_cached(c):
                new.append(c)
    by_purpose = Counter(c["purpose"] for c in new)
    tin = sum(c["in"] for c in new)
    tout = sum(c["out"] for c in new)
    est = client.cost_of(tin, tout) * 1.3  # +30% margin for tokenizer differences
    worst = client.cost_of(tin + sum(c["in"] for c in extra_worst),
                           sum(c["max_tokens"] for c in new) + sum(c["max_tokens"] for c in extra_worst)) * 1.3
    return {"planned_calls": len(plan), "unique_new_calls": len(new), "cached_or_duplicate": len(plan) - len(new),
            "possible_retry_calls": len(extra_worst), "by_purpose": dict(by_purpose),
            "est_input_tokens": tin, "est_output_tokens": tout,
            "est_cost_usd": round(est, 4), "worst_case_cost_usd": round(worst, 4)}


def print_plan(summary, client, args):
    print("=" * 72)
    print(f"PLAN  model={client.model}  prices=${client.price_in}/${client.price_out} per M tokens (in/out)"
          + ("  [FAKE: nothing is billed]" if client.is_fake else ""))
    print(f"  seeds={args.seeds}  items per seed={args.n}  list length {args.min_len}-{args.max_len}")
    for p, k in sorted(summary["by_purpose"].items()):
        print(f"  {p:<24}{k:>7} calls")
    print(f"  new model calls: {summary['unique_new_calls']}  "
          f"(already cached or shared between arms: {summary['cached_or_duplicate']})")
    if summary["possible_retry_calls"]:
        print(f"  retries: up to {summary['possible_retry_calls']} more calls, depending on what the guards reject")
    print(f"  est. tokens: {summary['est_input_tokens']:,} in / {summary['est_output_tokens']:,} out")
    print(f"  ESTIMATED COST: ${summary['est_cost_usd']:.2f}   "
          f"worst case (every reply hits max_tokens, every retry used): ${summary['worst_case_cost_usd']:.2f}")
    print(f"  hard cap: ${args.max_cost:.2f} (the run stops when actual spend reaches it)")
    print("=" * 72)


# ===================================================================== 1. agents

def plan_agents(args):
    plan = []
    for seed in args.seeds:
        for item in make_llm_inputs(args.n, seed, args.min_len, args.max_len):
            for stage in args.llm_stages:
                plan.append(plan_agent_calls(item, stage))
    return plan, []


def run_agents(args, client):
    rows = []
    for seed in args.seeds:
        items = make_llm_inputs(args.n, seed, args.min_len, args.max_len)
        jobs = [(seed, item, stage) for item in items for stage in args.llm_stages]

        def work(job):
            seed, item, stage = job
            truth = stage_truth(item["raw"])
            out = LLMAgent(stage, client)(item["raw"], truth)  # upstream = correct outputs
            return {"seed": seed, "stage": stage, "len": item["len"], "shape": item["shape"],
                    "raw": item["raw"], "output": out, "expected": truth[stage], "ok": out == truth[stage],
                    "unparsed": "_unparsed" in out}

        rows += _pmap(work, jobs, args.workers)

    res = {"per_stage": {}, "examples_of_errors": []}
    for stage in args.llm_stages:
        sr = [r for r in rows if r["stage"] == stage]
        d = {"accuracy": rate(sum(r["ok"] for r in sr), len(sr)),
             "unparsed": sum(r["unparsed"] for r in sr),
             "per_seed": {s: rate(sum(r["ok"] for r in sr if r["seed"] == s),
                                  sum(1 for r in sr if r["seed"] == s)) for s in args.seeds},
             "by_length": _group_rate(sr, lambda r: len_bucket(r["len"]), "ok"),
             "by_shape": _group_rate(sr, lambda r: r["shape"], "ok")}
        if stage == "analyze":
            d["field_error_rate"] = {
                f: rate(sum(1 for r in sr if not r["unparsed"] and r["output"].get(f) != r["expected"][f]),
                        sum(1 for r in sr if not r["unparsed"]))
                for f in ("count", "sum", "mean", "max")}
        res["per_stage"][stage] = d
    res["examples_of_errors"] = [{k: r[k] for k in ("stage", "raw", "output", "expected")}
                                 for r in rows if not r["ok"]][:8]

    md = ["## 1. Per-agent accuracy on clean input (p_i)", "",
          "Each LLM agent gets the correct upstream output, so this is its own accuracy.", "",
          "| Stage | Accuracy p_i (95% CI) | Unparsed replies |", "|---|---|---|"]
    for stage, d in res["per_stage"].items():
        md.append(f"| {stage} | {fmt(d['accuracy'])} | {d['unparsed']} |")
    for stage, d in res["per_stage"].items():
        md += ["", f"**{stage}, by list length:** " + "; ".join(f"{k}: {fmt(v)}" for k, v in d["by_length"].items()),
               "", f"**{stage}, by list shape:** " + "; ".join(f"{k}: {fmt(v)}" for k, v in d["by_shape"].items())]
        if "field_error_rate" in d:
            md += ["", f"**{stage}, error rate per field:** "
                   + "; ".join(f"{k}: {fmt(v)}" for k, v in d["field_error_rate"].items())]
    return res, md


# ===================================================================== 2+4. detect

SIZES = ("small", "medium", "large")


def _shift(total: int, size: str, rng) -> int:
    if size == "small":
        return total + rng.choice([-1, 1])
    if size == "medium":
        return total + rng.choice([-1, 1]) * max(10, round(0.01 * total))
    return total * 10  # decimal-place slip


def make_cases(seed, items):
    """Validator test cases. analyze: clean, plus two kinds of wrong sum at
    three sizes:
      inconsistent  sum changed, mean left alone, so mean != sum / count
                    (visible without recomputing anything)
      consistent    sum changed and mean recomputed from it, so the output
                    is internally consistent and only recomputing the sum
                    from the numbers reveals it
    reason: clean, plus a plausible wrong label."""
    rng = random.Random(f"cases:{seed}")
    cases = []
    for idx, item in enumerate(items):
        nums = item["nums"]
        stats = compute_stats(nums)
        trend = compute_trend(nums)
        base = {"seed": seed, "item": idx, "len": item["len"], "shape": item["shape"],
                "source": {"numbers": nums}}
        cases.append({**base, "stage": "analyze", "kind": "clean", "size": "-", "output": stats, "wrong": False})
        for size in SIZES:
            new_sum = _shift(stats["sum"], size, rng)
            inc = dict(stats, sum=new_sum)
            con = dict(stats, sum=new_sum, mean=round(new_sum / stats["count"], 2))
            cases.append({**base, "stage": "analyze", "kind": "inconsistent", "size": size, "output": inc, "wrong": True})
            cases.append({**base, "stage": "analyze", "kind": "consistent", "size": size, "output": con, "wrong": True})
        if trend == "mixed":
            wrong = "increasing" if nums[-1] > nums[0] else "decreasing"
        else:
            wrong = "mixed"
        cases.append({**base, "stage": "reason", "kind": "clean", "size": "-", "output": {"trend": trend}, "wrong": False})
        cases.append({**base, "stage": "reason", "kind": "wrong_label", "size": "-", "output": {"trend": wrong}, "wrong": True})
    return cases


def _all_cases(args):
    out = []
    for seed in args.seeds:
        out += make_cases(seed, make_llm_inputs(args.n, seed, args.min_len, args.max_len))
    return out


def plan_detect(args):
    return [plan_validator_call(p, c["stage"], c["source"], c["output"], c["len"])
            for c in _all_cases(args) for p in args.prompts], []


def run_detect(args, client):
    cases = _all_cases(args)
    guards = {p: LLMGuard(client, p) for p in args.prompts}
    jobs = [(c, p) for c in cases for p in args.prompts]

    def work(job):
        c, p = job
        j = guards[p].judge(c["stage"], c["output"], c["source"])
        return {**c, "prompt": p, "verdict": j["verdict"], "flagged": j["verdict"] != "accept",
                "reason": (j["reason"] or "")[:200] if isinstance(j["reason"], str) else j["reason"]}

    rows = _pmap(work, jobs, args.workers)
    res = {"per_prompt": {}}
    for p in args.prompts:
        pr = [r for r in rows if r["prompt"] == p]
        clean = [r for r in pr if not r["wrong"]]
        wrong = [r for r in pr if r["wrong"]]
        d = {
            "false_positive_rate": {s: rate(sum(r["flagged"] for r in clean if r["stage"] == s),
                                            sum(1 for r in clean if r["stage"] == s)) for s in ("analyze", "reason")},
            "detection_by_kind": _group_rate(wrong, lambda r: f"{r['stage']}/{r['kind']}", "flagged"),
            "detection_by_kind_and_size": _group_rate(wrong, lambda r: f"{r['stage']}/{r['kind']}/{r['size']}", "flagged"),
            "detection_by_length": _group_rate(wrong, lambda r: len_bucket(r["len"]), "flagged"),
            "unparseable": sum(r["verdict"] == "unparseable" for r in pr),
            "per_seed_detection": {s: rate(sum(r["flagged"] for r in wrong if r["seed"] == s),
                                           sum(1 for r in wrong if r["seed"] == s)) for s in args.seeds},
        }
        res["per_prompt"][p] = d
    res["cases"] = [{k: r[k] for k in ("seed", "item", "stage", "kind", "size", "len", "prompt", "verdict", "reason")}
                    for r in rows]

    md = ["## 2. Validator detection, false positives and 4. prompt comparison", "",
          "\"Flagged\" = the validator did not accept (a reject, or no parseable verdict, both stop the stage).",
          "inconsistent = sum wrong but mean not updated (visible without recomputing); "
          "consistent = sum and mean wrong together (only recomputing from the numbers shows it). "
          "Sizes: small = +/-1, medium = +/-1% of the sum, large = sum x10 (decimal slip).", ""]
    keys = sorted({k for d in res["per_prompt"].values() for k in d["detection_by_kind_and_size"]})
    md += ["| Case | " + " | ".join(args.prompts) + " |", "|---" * (len(args.prompts) + 1) + "|"]
    for k in keys:
        md.append(f"| detect {k} | " + " | ".join(fmt(res["per_prompt"][p]["detection_by_kind_and_size"].get(k, rate(0, 0)))
                                               for p in args.prompts) + " |")
    for s in ("analyze", "reason"):
        md.append(f"| false positive {s} (clean) | " + " | ".join(
            fmt(res["per_prompt"][p]["false_positive_rate"][s]) for p in args.prompts) + " |")
    md.append("| unparseable replies | " + " | ".join(str(res["per_prompt"][p]["unparseable"]) for p in args.prompts) + " |")
    for p in args.prompts:
        md.append(f"\n**{p}, detection of wrong outputs by list length:** " + "; ".join(
            f"{k}: {fmt(v)}" for k, v in res["per_prompt"][p]["detection_by_length"].items()))
    return res, md


# ===================================================================== 3. chain

def plan_chain(args):
    plan, extra = [], []
    guard_arms = [a for a in args.arms if a.startswith("llm_guard") or a == "llm_rule_guard"]
    llm_guard_arms = [a.removesuffix("_guided") for a in args.arms if a.startswith("llm_guard_")]
    uses_llm = any(a.startswith("llm") for a in args.arms)
    r = args.max_retries
    for seed in args.seeds:
        for item in make_llm_inputs(args.n, seed, args.min_len, args.max_len):
            truth = stage_truth(item["raw"])
            for stage in args.llm_stages:
                if uses_llm:
                    plan.append(plan_agent_calls(item, stage))
                for arm in llm_guard_arms:
                    p = arm.rsplit("_", 1)[1]
                    plan.append(plan_validator_call(p, stage, validator_source(stage, item["raw"], truth),
                                                    truth[stage], item["len"]))
                    for _ in range(r):
                        extra.append(plan_validator_call(p, stage, validator_source(stage, item["raw"], truth),
                                                         truth[stage], item["len"]))
                for _ in guard_arms:
                    for a in range(1, r + 1):
                        extra.append(plan_agent_calls(item, stage, attempt=a))
    return plan, extra


def arm_repair(name: str) -> str:
    return "guided" if name.endswith("_guided") else "blind"


def make_arm(name, client, args):
    """Returns (agents, guard) for an arm. An arm ending in _guided uses the same
    guard, but a retried agent is told why its last answer was rejected."""
    name = name.removesuffix("_guided")
    llm = build_agents(client, args.llm_stages)
    if name == "rules_noguard":
        return AGENTS, None
    if name == "llm_noguard":
        return llm, None
    if name == "llm_rule_guard":
        return llm, CascadeGuard("full", args.max_retries)
    prompt = name.rsplit("_", 1)[1]
    return llm, LLMGuard(client, prompt, args.max_retries, args.llm_stages)


# What each stage reads. A stage "got correct input" when all of these were right.
DEPENDS_ON = {"extract": (), "analyze": ("extract",), "reason": ("extract",), "report": ("analyze", "reason")}


def per_stage_rows(results):
    """No-guard arm: one row per trial, {stage: was the stage's output correct},
    or None when the stage did not get correct input (or was not reached).
    Each p_i is then the stage's accuracy given correct input, and prod p_i is
    what the chain would score if the stages failed independently."""
    rows = []
    for r in results:
        ok = {t["stage"]: t.get("matches_truth", False) for t in r["trace"] if t["attempt"] == 0}
        row = {}
        for stage in STAGES:
            if stage in ok and all(ok.get(d) for d in DEPENDS_ON[stage]):
                row[stage] = bool(ok[stage])
            else:
                row[stage] = None
        rows.append(row)
    return rows


def run_chain_eval(args, client):
    arms = {a: {"results": [], "guard_stats": Counter()} for a in args.arms}
    per_seed = defaultdict(dict)
    for seed in args.seeds:
        items = make_llm_inputs(args.n, seed, args.min_len, args.max_len)
        inputs = [it["raw"] for it in items]
        injector = ErrorInjector(args.probability, seed)
        for a in args.arms:
            agents, guard = make_arm(a, client, args)
            out = run_arm(inputs, injector, guard, agents, workers=args.workers, return_results=True,
                          repair=arm_repair(a))
            arms[a]["results"] += out["results"]
            per_seed[a][seed] = rate(out["correct"], out["trials"])
            if isinstance(guard, LLMGuard):
                arms[a]["guard_stats"].update(guard.stats)

    res = {"arms": {}, "per_seed_accuracy": dict(per_seed)}
    for a, d in arms.items():
        rs = d["results"]
        n = len(rs)
        st = Counter(r["status"] for r in rs)
        missed = Counter()
        for r in rs:
            missed.update(r["missed_types"])
        res["arms"][a] = {
            "trials": n,
            "correct": rate(st["correct"], n),
            "silent_wrong": rate(st["wrong"], n),
            "crashed": rate(st["crashed"], n),
            "halted": rate(st["halted"], n),
            "unsafe": rate(st["wrong"] + st["crashed"], n),
            "detected": sum(r["detected"] for r in rs),
            "recovered": sum(r["recovered"] for r in rs),
            "false_alarms": sum(r["false_alarms"] for r in rs),
            "missed_by_type": dict(missed),
            "llm_validator": dict(d["guard_stats"]),
        }
    if "llm_noguard" in arms:
        rows = per_stage_rows(arms["llm_noguard"]["results"])
        p_i = {s: rate(sum(1 for r in rows if r.get(s)), sum(1 for r in rows if r.get(s) is not None))
               for s in STAGES}
        point, (lo, hi) = bootstrap_product(rows, list(STAGES))
        llm = [s for s in args.llm_stages if s in ("analyze", "reason")]
        both_wrong = None
        if len(llm) == 2:
            rr = [r for r in rows if r[llm[0]] is not None and r[llm[1]] is not None]
            both_wrong = {"stages": llm,
                          "p_both_wrong_observed": rate(sum(1 for r in rr if not r[llm[0]] and not r[llm[1]]), len(rr)),
                          "p_both_wrong_if_independent": round(
                              (1 - p_i[llm[0]]["rate"]) * (1 - p_i[llm[1]]["rate"]), 4)
                          if p_i[llm[0]]["n"] and p_i[llm[1]]["n"] else None}
        res["multiplication"] = {
            "both_wrong": both_wrong,
            "p_i": p_i,
            "predicted_prod_p_i": round(point, 4) if point is not None else None,
            "predicted_ci95": [round(lo, 4), round(hi, 4)] if lo is not None else None,
            "observed_no_guard": res["arms"]["llm_noguard"]["correct"],
        }

    md = ["## 3. End-to-end chain", "",
          f"Fault injection probability per agent output: {args.probability}. "
          f"Retries per stage: {args.max_retries}. LLM stages: {', '.join(args.llm_stages)}.", "",
          "| Arm | Correct | Silent wrong | Halted (safe stop) | Crashed | False alarms | Recovered |",
          "|---|---|---|---|---|---|---|"]
    for a, d in res["arms"].items():
        md.append(f"| {a} | {fmt(d['correct'])} | {fmt(d['silent_wrong'])} | {fmt(d['halted'])} | "
                  f"{fmt(d['crashed'])} | {d['false_alarms']} | {d['recovered']} |")
    if "multiplication" in res:
        m = res["multiplication"]
        md += ["", "**Does multiplication predict the chain?** (no-guard arm)", ""]
        md += [f"- p_{s} = {fmt(v)}" for s, v in m["p_i"].items()]
        if m["predicted_prod_p_i"] is not None:
            md.append(f"- predicted prod p_i = {m['predicted_prod_p_i'] * 100:.1f}% "
                      f"(bootstrap 95% CI {m['predicted_ci95'][0] * 100:.1f}-{m['predicted_ci95'][1] * 100:.1f}%)")
        md.append(f"- observed end-to-end = {fmt(m['observed_no_guard'])}")
        bw = m.get("both_wrong")
        if bw and bw["p_both_wrong_if_independent"] is not None:
            md.append(f"- {bw['stages'][0]} and {bw['stages'][1]} both wrong on the same input: observed "
                      f"{fmt(bw['p_both_wrong_observed'])} vs {bw['p_both_wrong_if_independent'] * 100:.1f}% "
                      "if independent (higher observed = errors cluster on the same hard inputs, so the chain "
                      "does better than the product predicts)")
        md += ["", "p_i is each stage's accuracy on trials where it got correct input. "
               "Observed above predicted means errors are correlated (they hit the same inputs); "
               "below means they compound."]
    md += ["", "**Accuracy per seed:** " + "; ".join(
        f"{a}: " + ", ".join(f"{v['rate'] * 100:.1f}%" for v in res["per_seed_accuracy"][a].values())
        for a in res["per_seed_accuracy"])]
    return res, md


# ===================================================================== helpers

def _pmap(fn, jobs, workers):
    if workers > 1:
        with ThreadPoolExecutor(workers) as pool:
            return list(pool.map(fn, jobs))
    return [fn(j) for j in jobs]


def _group_rate(rows, keyfn, field):
    groups = defaultdict(list)
    for r in rows:
        groups[keyfn(r)].append(r)
    return {k: rate(sum(bool(r[field]) for r in v), len(v)) for k, v in sorted(groups.items())}


COMMANDS = {
    "agents": (plan_agents, run_agents),
    "detect": (plan_detect, run_detect),
    "chain": (plan_chain, run_chain_eval),
}


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="cascade-guard eval", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=list(COMMANDS) + ["all", "check"])
    p.add_argument("--fake", action="store_true", help="offline simulator; no key, no cost, SIMULATED numbers")
    p.add_argument("--provider", default=None, choices=["anthropic", "openai"],
                   help="default: LLM_PROVIDER env var, else anthropic. openai = any OpenAI-compatible API")
    p.add_argument("--model", default=None, help="default: LLM_MODEL env var, else claude-haiku-5-5")
    p.add_argument("--price-in", type=float, help="USD per million input tokens (needed for unlisted models)")
    p.add_argument("--price-out", type=float, help="USD per million output tokens")
    p.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    p.add_argument("--n", type=int, default=50, help="items per seed")
    p.add_argument("--min-len", type=int, default=5)
    p.add_argument("--max-len", type=int, default=30)
    p.add_argument("--llm-stages", nargs="+", default=list(DEFAULT_LLM_STAGES), choices=list(STAGES))
    p.add_argument("--prompts", nargs="+", default=list(PROMPTS), choices=list(VALIDATOR_PROMPTS))
    p.add_argument("--arms", nargs="+", default=list(CHAIN_ARMS), choices=list(CHAIN_ARMS))
    p.add_argument("--probability", type=float, default=0.0,
                   help="chain: injected fault rate per agent output (0 = the LLM's own errors only)")
    p.add_argument("--max-retries", type=int, default=2)
    p.add_argument("--workers", type=int, default=4, help="parallel requests")
    p.add_argument("--max-cost", type=float, default=5.0, help="hard spend cap in USD")
    p.add_argument("--cache", default=".llm_cache/responses.jsonl")
    p.add_argument("--out", default="results")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    a = p.parse_args(argv)
    if a.min_len < 2 or a.max_len < a.min_len:
        p.error("need 2 <= --min-len <= --max-len")
    return a


def eval_client(args, plan_only: bool = False):
    """The client for an evaluation run (or, for a plan without a key, a prices-only stand-in)."""
    fake = args.fake or (args.provider or os.getenv("LLM_PROVIDER", "")).lower() == "fake"
    if plan_only and not fake:
        return _PlanOnlyClient(args)
    return make_client(fake=args.fake, model=args.model, provider=args.provider, max_cost=args.max_cost,
                       cache_path=":memory:" if fake else args.cache,
                       price_in=args.price_in, price_out=args.price_out)


def commands_of(args) -> list:
    return list(COMMANDS) if args.command == "all" else [args.command]


def make_plan(args, client) -> dict:
    plan: list = []
    extra: list = []
    for c in commands_of(args):
        p, more = COMMANDS[c][0](args)
        plan += p
        extra += more
    return summarise_plan(plan, extra, client, client.cache)


def main(argv=None):
    load_env()
    args = parse_args(argv)
    if args.command == "check":
        from cascade_guard.experiments.check import connection_check, print_check
        try:
            client = eval_client(args)
        except (RuntimeError, UnknownPrice) as e:
            print(f"PROBLEM: {e}", file=sys.stderr)
            return 2
        return print_check(connection_check(client))
    try:
        client = eval_client(args, plan_only=args.dry_run)
    except (RuntimeError, UnknownPrice) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    summary = make_plan(args, client)
    print_plan(summary, client, args)
    if args.dry_run:
        return 0
    if not client.is_fake:
        if summary["est_cost_usd"] > args.max_cost:
            print(f"Estimated cost is above the cap (${args.max_cost:.2f}). Lower --n / --seeds or raise --max-cost.")
            return 1
        if not args.yes and input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Cancelled; no calls made.")
            return 1
    report, md, status = run_evaluation(args, client, summary)
    stem_json, stem_md = write_results(args, client, report, md)
    print("\n".join(md))
    print(f"\nwrote {stem_json} and {stem_md}")
    return status


def run_evaluation(args, client, summary, progress=None):
    """Run the planned commands; returns (report dict, markdown lines, status code).
    progress(command_name) is called as each part starts."""
    commands = commands_of(args)
    report = {"label": "SIMULATED (fake LLM; not a measurement of any model)" if client.is_fake else "MEASURED",
              "model": client.model, "config": {k: v for k, v in vars(args).items()}, "plan": summary,
              "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    md = [f"# LLM evaluation: {report['label']}", "",
          f"Model `{client.model}`; seeds {args.seeds}; {args.n} items per seed; list length "
          f"{args.min_len}-{args.max_len}. Rates are pooled over seeds, with Wilson 95% intervals.", ""]
    status = 0
    try:
        for c in commands:
            if progress:
                progress(c)
            t0 = time.time()
            res, lines = COMMANDS[c][1](args, client)
            res["seconds"] = round(time.time() - t0, 1)
            report[c] = res
            md += lines + [""]
    except BudgetExceeded as e:
        print(f"\nSTOPPED: {e}")
        md += [f"**Run stopped early: {e}**", ""]
        status = 3
    except LLMCallError as e:
        print(f"\nSTOPPED: a model call failed: {e}")
        md += [f"**Run stopped early: a model call failed ({e}).** Completed calls are cached.", ""]
        status = 4
    except KeyboardInterrupt:
        print("\nInterrupted; partial results saved. Completed calls are cached.")
        md += ["**Run interrupted; results are partial.**", ""]
        status = 130
    report["usage"] = client.usage.as_dict()
    md += ["## Cost", "",
           f"Estimated before the run: ${summary['est_cost_usd']:.2f}. "
           f"Actual: {client.usage.calls} billed calls, {client.usage.cached_calls} answered from cache, "
           f"{client.usage.input_tokens:,} input / {client.usage.output_tokens:,} output tokens, "
           f"${client.usage.cost:.4f}" + (" (simulated; nothing billed)" if client.is_fake else "") + "."]
    report["status"] = {0: "complete", 3: "stopped at the spend cap", 4: "stopped: a model call failed",
                        130: "interrupted"}.get(status, str(status))
    return report, md, status


def write_results(args, client, report, md):
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_model = "".join(ch if ch.isalnum() or ch in "-." else "-" for ch in client.model)
    stem = f"llm_{args.command}_{'fake' if client.is_fake else safe_model}_{time.strftime('%Y%m%d-%H%M%S')}"
    (out_dir / f"{stem}.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (out_dir / f"{stem}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return out_dir / f"{stem}.json", out_dir / f"{stem}.md"


class _PlanOnlyClient:
    """For --dry-run without a key: prices and cache lookups only."""
    is_fake = False

    def __init__(self, args):
        import os

        from cascade_guard.llm.client import DEFAULT_MODEL
        self.provider = (args.provider or os.getenv("LLM_PROVIDER") or "anthropic").strip().lower()
        if self.provider not in ("anthropic", "fake"):
            self.provider = "openai"
        self.model = args.model or os.getenv("LLM_MODEL") or DEFAULT_MODEL
        self.price_in, self.price_out = resolve_prices(self.model, args.price_in, args.price_out)
        self.cache = ResponseCache(args.cache) if Path(args.cache).exists() else None

    def cache_key(self, system, user, max_tokens, temperature, sample):
        return ResponseCache.key(provider=self.provider, model=self.model, system=system, user=user,
                                 max_tokens=max_tokens, temperature=temperature, sample=sample)

    def cost_of(self, i, o):
        return (i * self.price_in + o * self.price_out) / 1e6


if __name__ == "__main__":
    sys.exit(main())
