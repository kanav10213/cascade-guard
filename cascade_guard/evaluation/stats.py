"""Confidence intervals for the LLM evaluation."""
import math
import random


def wilson(k: int, n: int, z: float = 1.96):
    """Wilson score interval for k successes out of n (95% by default).
    Returns (low, high); (0.0, 1.0) when n == 0."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def rate(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None,
            "ci95": [round(lo, 4), round(hi, 4)]}


def fmt(r: dict) -> str:
    if not r["n"]:
        return "n/a (n=0)"
    return f"{r['rate'] * 100:5.1f}%  [{r['ci95'][0] * 100:.1f}, {r['ci95'][1] * 100:.1f}]  ({r['k']}/{r['n']})"


def bootstrap_product(rows: list, stages: list, iters: int = 2000, seed: int = 0):
    """95% percentile interval for prod_i p_i, resampling trials.

    rows: one dict per trial, {stage: True/False/None}, where None means the
    stage was not reached with correct upstream input (excluded from that p_i)."""
    def product(sample):
        prod = 1.0
        for s in stages:
            vals = [r[s] for r in sample if r.get(s) is not None]
            if not vals:
                return None
            prod *= sum(vals) / len(vals)
        return prod

    point = product(rows)
    if point is None or not rows:
        return point, (None, None)
    rng = random.Random(seed)
    draws = []
    for _ in range(iters):
        v = product([rows[rng.randrange(len(rows))] for _ in rows])
        if v is not None:
            draws.append(v)
    draws.sort()
    lo = draws[int(0.025 * (len(draws) - 1))]
    hi = draws[int(0.975 * (len(draws) - 1))]
    return point, (lo, hi)
