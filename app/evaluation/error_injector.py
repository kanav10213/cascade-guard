"""Deterministic fault injection into agent outputs.

Randomness is derived from (seed, trial, stage, attempt), so
  * runs are reproducible,
  * the guarded and unguarded arms see identical first-attempt faults (paired),
  * no process-wide random state is shared between concurrent requests.
"""
import copy
import random

from app.agents.pipeline import TRENDS
from app.evaluation.judgement import FAILURE_TYPES


def _delta(rng: random.Random) -> int:
    return rng.choice([-1, 1]) * rng.randint(1, 9)


def _corrupt(out: dict, rng: random.Random) -> dict:
    key = rng.choice(list(out))
    val = out[key]
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        out[key] = val + _delta(rng)
    elif isinstance(val, list) and val:
        i = rng.randrange(len(val))
        val[i] = val[i] + _delta(rng)
    elif isinstance(val, str):
        digits = [i for i, c in enumerate(val) if c.isdigit()]
        if digits:
            i = rng.choice(digits)
            new = rng.choice([d for d in "0123456789" if d != val[i]])
            out[key] = val[:i] + new + val[i + 1:]
        else:
            out[key] = val + "#"
    return out


def _missing(out: dict, rng: random.Random) -> dict:
    out[rng.choice(list(out))] = None
    return out


def _invalid(out: dict, rng: random.Random) -> dict:
    if isinstance(out.get("trend"), str) and out["trend"] in TRENDS:
        out["trend"] = rng.choice([t for t in TRENDS if t != out["trend"]])
    elif isinstance(out.get("final_output"), str):
        parts = out["final_output"].split("; ")
        out["final_output"] = "; ".join(reversed(parts))
    elif isinstance(out.get("numbers"), list):
        out["numbers"] = list(reversed(out["numbers"]))
    elif len(out) >= 2:
        k1, k2 = rng.sample(list(out), 2)
        out[k1], out[k2] = out[k2], out[k1]
    return out


def _truncated(out: dict, rng: random.Random) -> dict:
    for key, val in out.items():
        if isinstance(val, list) and len(val) > 1:
            out[key] = val[:-1]
            return out
        if isinstance(val, str) and len(val) > 1:
            out[key] = val[: max(1, int(len(val) * 0.6))]
            return out
    if len(out) > 1:
        out.pop(list(out)[-1])
    return out


_APPLY = {
    "CORRUPTED_OUTPUT": _corrupt,
    "MISSING_DATA": _missing,
    "INVALID_REASONING": _invalid,
    "TRUNCATED_OUTPUT": _truncated,
}


class ErrorInjector:
    ERROR_TYPES = FAILURE_TYPES

    def __init__(self, probability: float = 0.1, seed: int = 0):
        self.probability = probability
        self.seed = seed

    def inject(self, stage: str, output: dict, trial: int, attempt: int = 0):
        """Return (possibly corrupted copy, error_type or None)."""
        rng = random.Random(f"{self.seed}:{trial}:{stage}:{attempt}")
        if rng.random() >= self.probability:
            return output, None
        kind = rng.choice(self.ERROR_TYPES)
        corrupted = _APPLY[kind](copy.deepcopy(output), rng)
        if corrupted == output:  # fault had no effect; do not count it
            return output, None
        return corrupted, kind
