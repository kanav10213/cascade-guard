"""LLM-backed evaluation of the cascade chain (Project Brief, priority 1).

Chain: LLM analyst (count/sum/mean/max) + LLM trend labeler -> deterministic report.
Validator: an LLM that reviews the figures, with two prompts:
  naive      "do these look right?"
  recompute  "recompute independently first, then compare"
Experiments:
  agents     per-agent accuracy p_i on clean input, and the product p_1 * p_2
  validator  false-positive rate on correct figures; detection rate by error kind
             (internal = figures contradict each other; consistent = wrong but
             self-consistent, only detectable by recomputing from the numbers)
             and by error size
  e2e        end-to-end success with no guard, with each validator + bounded retry,
             and with a perfect "oracle" validator as the ceiling
"""
import random
from concurrent.futures import ThreadPoolExecutor

from app.agents.pipeline import compute_stats, compute_trend
from app.evaluation.error_injector import ErrorInjector
from app.llm.agents import figures_correct, run_analyst, run_trend, run_validator, trend_correct
from app.llm.stats import stat

STYLES = ("naive", "recompute")
KINDS = ("internal", "consistent")
MAGS = {"small": 0.02, "medium": 0.10, "large": 0.50}
PARTS = ("agents", "validator", "e2e")
# rough (input, output) tokens per call, used only for the pre-run estimate
TOKENS = {"analyst": (190, 45), "trend": (170, 12), "validator_naive": (190, 40), "validator_recompute": (210, 130)}
DEFAULTS = {"agent_trials": 40, "validator_trials": 15, "e2e_trials": 40, "hard": False,
            "retries": 2, "inject": 0.0, "seed": 42, "workers": 4, "repeats": 1, "parts": PARTS}


def norm(cfg):
    c = dict(DEFAULTS)
    c.update({k: v for k, v in (cfg or {}).items() if k in DEFAULTS and v is not None})
    c["parts"] = tuple(p for p in PARTS if p in c["parts"])
    return c


def plan(cfg):
    """Number of model calls and tokens, before anything is spent."""
    c = norm(cfg)
    mn, mx = dict.fromkeys(TOKENS, 0), dict.fromkeys(TOKENS, 0)
    both = (mn, mx)
    if "agents" in c["parts"]:
        for d in both:
            d["analyst"] += c["agent_trials"]
            d["trend"] += c["agent_trials"]
    if "validator" in c["parts"]:
        cells = 1 + len(KINDS) * len(MAGS)
        for d in both:
            for s in STYLES:
                d["validator_" + s] += cells * c["validator_trials"]
    if "e2e" in c["parts"]:
        n, r = c["e2e_trials"], c["retries"]
        for d in both:
            d["analyst"] += n
            d["trend"] += n
            for s in STYLES:
                d["validator_" + s] += n
        mx["analyst"] += n * r * 3   # retries in the oracle arm and both validator arms
        mx["trend"] += n * r * 3
        for s in STYLES:
            mx["validator_" + s] += n * r

    for d in both:
        for k in d:
            d[k] *= c["repeats"]

    def tokens(d):
        return {"input": sum(d[k] * TOKENS[k][0] for k in d), "output": sum(d[k] * TOKENS[k][1] for k in d)}

    out_cfg = dict(c)
    out_cfg["parts"] = list(c["parts"])
    return {"config": out_cfg, "calls_min": mn, "calls_max": mx, "total_min": sum(mn.values()),
            "total_max": sum(mx.values()), "tokens_min": tokens(mn), "tokens_max": tokens(mx)}


def make_numbers(rng, hard):
    n = rng.randint(8, 15) if hard else rng.randint(3, 8)
    lo, hi = (100, 9999) if hard else (1, 99)
    return [rng.randint(lo, hi) for _ in range(n)]


def _inputs(n, c, tag):
    rng = random.Random(f"{tag}:{c['seed']}")
    return [make_numbers(rng, c["hard"]) for _ in range(n)]


def _pmap(fn, items, workers):
    if workers <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


def exp_agents(client, c):
    inputs = _inputs(c["agent_trials"], c, "agents")

    def one(nums):
        a, t = run_analyst(client, nums), run_trend(client, nums)
        return (figures_correct(a, compute_stats(nums)), trend_correct(t, compute_trend(nums)),
                a is None, t is None)

    rows = _pmap(one, inputs, c["workers"])
    n = len(rows)
    a_ok, t_ok = sum(r[0] for r in rows), sum(r[1] for r in rows)
    both = sum(r[0] and r[1] for r in rows)
    pa, pt = (a_ok / n, t_ok / n) if n else (0, 0)
    return {"analyst": stat(a_ok, n), "trend": stat(t_ok, n),
            "product_predicted": round(pa * pt, 4), "joint_observed": stat(both, n),
            "unparseable": {"analyst": sum(r[2] for r in rows), "trend": sum(r[3] for r in rows)}}


def build_case(nums, kind, mag, rng):
    figs = dict(compute_stats(nums))
    delta = max(1, round(figs["sum"] * MAGS[mag])) * rng.choice([-1, 1])
    if kind == "internal":          # sum changes, mean no longer matches it
        figs["sum"] += delta
    else:                           # sum and mean both wrong, but consistent with each other
        figs["sum"] += delta
        figs["mean"] = round(figs["sum"] / figs["count"], 2)
    return figs


def exp_validator(client, c):
    n = c["validator_trials"]
    clean_inputs = _inputs(n, c, "val:clean")
    cell_inputs = {(k, m): _inputs(n, c, f"val:{k}:{m}") for k in KINDS for m in MAGS}
    out = {}
    for style in STYLES:
        def rejected_clean(nums):
            v = run_validator(client, nums, compute_stats(nums), compute_trend(nums), style)
            return v["valid"]

        verdicts = _pmap(rejected_clean, clean_inputs, c["workers"])
        unparse = sum(v is None for v in verdicts)
        res = {"false_positive": stat(sum(v is not True for v in verdicts), n), "detection": {}, "overall": {}}
        for k in KINDS:
            res["detection"][k] = {}
            pooled = [0, 0]
            for m in MAGS:
                def check(args, k=k, m=m):
                    i, nums = args
                    figs = build_case(nums, k, m, random.Random(f"case:{c['seed']}:{k}:{m}:{i}"))
                    return run_validator(client, nums, figs, compute_trend(nums), style)["valid"]

                vs = _pmap(check, list(enumerate(cell_inputs[(k, m)])), c["workers"])
                unparse += sum(v is None for v in vs)
                det = sum(v is not True for v in vs)
                res["detection"][k][m] = stat(det, n)
                pooled[0] += det
                pooled[1] += n
            res["overall"][k] = stat(*pooled)
        res["unparseable"] = unparse
        out[style] = res
    return out


def exp_e2e(client, c):
    inputs = _inputs(c["e2e_trials"], c, "e2e")
    retries = c["retries"]
    inj = ErrorInjector(c["inject"], c["seed"]) if c["inject"] > 0 else None

    def attempt(nums, trial, k):
        a, t = run_analyst(client, nums), run_trend(client, nums)
        if inj:
            if isinstance(a, dict):
                a, _ = inj.inject("analyze", a, trial, k)
            if isinstance(t, dict):
                t, _ = inj.inject("reason", t, trial, k)
        return a, t

    def one(args):
        i, nums = args
        truth, tt = compute_stats(nums), compute_trend(nums)

        def good(a, t):
            return figures_correct(a, truth) and trend_correct(t, tt)

        a0, t0 = attempt(nums, i, 0)
        first_ok = good(a0, t0)
        res = {"first_ok": first_ok, "unguarded": "correct" if first_ok else "wrong"}

        a, t, k = a0, t0, 0                       # oracle: a perfect validator
        while not good(a, t) and k < retries:
            k += 1
            a, t = attempt(nums, i, k)
        res["oracle"] = "correct" if good(a, t) else "halted"

        for style in STYLES:
            a, t, k, flagged_first = a0, t0, 0, False
            while True:
                v = run_validator(client, nums, a, t.get("trend") if isinstance(t, dict) else None, style)["valid"]
                if v is True:
                    res[style] = "correct" if good(a, t) else "wrong"
                    break
                if k == 0:
                    flagged_first = True
                if k >= retries:
                    res[style] = "halted"
                    break
                k += 1
                a, t = attempt(nums, i, k)
            res[style + "_flag_first"] = flagged_first
        return res

    rows = _pmap(one, list(enumerate(inputs)), c["workers"])
    n = len(rows)
    arms = {}
    for name, key in (("no_guard", "unguarded"), ("validator_naive", "naive"),
                      ("validator_recompute", "recompute"), ("oracle_reference", "oracle")):
        arms[name] = {o: stat(sum(r[key] == o for r in rows), n) for o in ("correct", "wrong", "halted")}
    wrong_first = [r for r in rows if not r["first_ok"]]
    right_first = [r for r in rows if r["first_ok"]]
    confusion = {}
    for style in STYLES:
        f = style + "_flag_first"
        confusion[style] = {"caught_wrong": stat(sum(r[f] for r in wrong_first), len(wrong_first)),
                            "false_alarm": stat(sum(r[f] for r in right_first), len(right_first))}
    return {"arms": arms, "first_attempt_correct": stat(len(right_first), n), "validator_first_pass": confusion}


def run_all(client, cfg):
    c = norm(cfg)
    report = {}
    if "agents" in c["parts"]:
        report["agents"] = exp_agents(client, c)
    if "validator" in c["parts"]:
        report["validator"] = exp_validator(client, c)
    if "e2e" in c["parts"]:
        report["end_to_end"] = exp_e2e(client, c)
    u = client.usage
    report["meta"] = {"llm": client.name, "simulated": client.simulated, "seed": c["seed"], "hard": c["hard"],
                      "retries": c["retries"], "inject": c["inject"], "calls": u.calls, "api_errors": u.errors,
                      "input_tokens": u.input_tokens, "output_tokens": u.output_tokens}
    return report


def _is_stat(x):
    return isinstance(x, dict) and {"k", "n", "rate", "ci"} <= set(x)


def merge(items):
    """Combine several identical-shaped reports: counts are pooled (so the Wilson interval
    uses all trials) and each rate keeps its per-run values so the spread is visible."""
    first = items[0]
    if _is_stat(first):
        s = stat(sum(i["k"] for i in items), sum(i["n"] for i in items))
        s["repeat_rates"] = [i["rate"] for i in items]
        return s
    if isinstance(first, dict):
        return {key: merge([i[key] for i in items]) for key in first}
    if isinstance(first, (bool, str)):
        return first
    if isinstance(first, int):
        return sum(items)
    if isinstance(first, float):
        return round(sum(items) / len(items), 4)
    return first


def run_repeated(client, cfg):
    """Run the evaluation `repeats` times with seeds seed, seed+1, ... and combine."""
    c = norm(cfg)
    n = c["repeats"]
    if n <= 1:
        return run_all(client, c)
    reports = [run_all(client, {**c, "seed": c["seed"] + i}) for i in range(n)]
    merged = merge([{k: v for k, v in r.items() if k != "meta"} for r in reports])
    meta = dict(reports[-1]["meta"])   # usage counters are cumulative on the client
    meta.update({"repeats": n, "seed": c["seed"], "seeds": [c["seed"] + i for i in range(n)]})
    merged["meta"] = meta
    return merged
