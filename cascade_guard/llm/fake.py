"""SimulatedLLM: an offline stand-in that answers the pipeline's prompts.

It exists so the whole LLM path (prompts -> parsing -> guard -> runner ->
metrics -> report) can be tested and demonstrated without a key. Its error
rates are INVENTED; numbers produced with it say nothing about real models and
every report made with it is labelled SIMULATED.

Behaviour (deterministic per request):
  agents     correct, except an error rate that grows with list length
             (analyze: sum off by a few, mean kept consistent; reason: wrong
             label, mostly on nearly sorted lists)
  naive      checks only what is visible without recomputing: missing values,
             mean != sum / count, max smaller than mean, unknown labels
  recompute  recomputes from the source; misses a small share of errors
"""
import hashlib
import json
import random

from cascade_guard.agents.pipeline import TRENDS, compute_stats, compute_trend, parse_numbers, render_report
from cascade_guard.llm.prompts import parse_request


class SimulatedLLM:
    def __init__(self, agent_error_per_item=0.004, naive_fp=0.03, recompute_fp=0.01,
                 recompute_miss=0.04, sticky=0.7, repair_success=0.9):
        """sticky: chance a blind retry repeats the first attempt's mistake (systematic
        errors); repair_success: chance a retry told what was wrong gets it right.
        Both INVENTED, like every rate here."""
        self.sticky = sticky
        self.repair_success = repair_success
        self.agent_error_per_item = agent_error_per_item
        self.naive_fp = naive_fp
        self.recompute_fp = recompute_fp
        self.recompute_miss = recompute_miss

    def __call__(self, system: str, user: str, sample: int) -> str:
        req = parse_request(user)
        task = req.get("TASK")
        rng = random.Random(hashlib.sha256(f"{system}|{user}|{sample}".encode()).hexdigest())
        if task != "validate":
            # the first attempt's luck depends only on the task and its data, so a
            # blind retry can repeat exactly the same mistake (a systematic error)
            base = random.Random(hashlib.sha256(f"{system}|{task}|{json.dumps(req.get('DATA'))}".encode()).hexdigest())
            if sample == 0:
                rng = base
            elif "FEEDBACK" in req:
                if rng.random() < self.repair_success:
                    rng = _NoErrors()
            elif rng.random() < self.sticky:
                rng = base
        try:
            if task == "validate":
                recompute = "recompute every output value" in system
                if req["STAGE"] == "custom":
                    return self._validate_custom(req["DATA"], recompute, rng)
                return self._validate(req["STAGE"], req["DATA"], recompute, rng)
            if task in ("pilot_extract", "pilot_price"):
                return json.dumps(self._pilot(task, req["DATA"], rng))
            return json.dumps(self._agent(task, req["DATA"], rng))
        except Exception:
            # Malformed input (e.g. a corrupted upstream value): reply like a model
            # that cannot do the task, i.e. without the requested JSON.
            return "I can't complete this: the input is missing or malformed."

    # ------------------------------------------------------------ agents
    def _agent(self, stage, data, rng):
        if stage == "extract":
            return {"numbers": parse_numbers(data["raw_input"])}
        if stage == "report":
            return {"final_output": render_report(data["stats"], data["trend"])}
        nums = data["numbers"]
        p_err = min(0.5, self.agent_error_per_item * len(nums))
        if stage == "analyze":
            out = compute_stats(nums)
            if rng.random() < p_err:
                out["sum"] += rng.choice([-1, 1]) * rng.randint(1, 10)
                out["mean"] = round(out["sum"] / out["count"], 2)
            return out
        trend = compute_trend(nums)
        near_sorted = trend == "mixed" and _near_sorted(nums)
        if rng.random() < (0.3 if near_sorted else p_err / 2):
            trend = "increasing" if nums[-1] > nums[0] else "decreasing"
            if trend == compute_trend(nums):
                trend = "mixed"
        return {"trend": trend}

    # ------------------------------------------------------------ invoice pilot
    def _pilot(self, task, data, rng):
        from cascade_guard.pilot.invoice import parse_invoice, price_items
        if task == "pilot_extract":
            out = parse_invoice(data["invoice_text"])
            if rng.random() < 0.06:
                it = rng.choice(out["items"])
                it["unit_price"] = round(it["unit_price"] * 10, 2)
            return out
        out = price_items(data)
        if rng.random() < 0.004 * sum(i["qty"] for i in data["items"]):
            out["total"] = round(out["total"] + rng.choice([-1, 1]) * rng.choice([0.01, 0.10, 1.00]), 2)
        return out

    def _validate_custom(self, data, recompute, rng):
        """Generic checks; recognises the invoice pilot's steps and recomputes them."""
        from cascade_guard.pilot.invoice import parse_invoice, price_items, same
        src, out = data["source"], data["output"]
        problem = None
        try:
            if isinstance(out, dict) and {"subtotal", "tax", "total"} <= out.keys():
                if abs(out["subtotal"] + out["tax"] - out["total"]) > 0.005:
                    problem = "total is not subtotal + tax"
                elif recompute and not same(out, price_items(src)) and rng.random() >= self.recompute_miss:
                    problem = "recomputed total differs"
            elif isinstance(out, dict) and "items" in out and isinstance(src, str):  # noqa: SIM102
                if recompute and not same(out, parse_invoice(src)) and rng.random() >= self.recompute_miss:
                    problem = "line items differ from the invoice"
        except Exception:
            problem = "could not check the output"
        if problem is None and rng.random() < (self.recompute_fp if recompute else self.naive_fp):
            problem = "spurious doubt"
        verdict = {"verdict": "reject" if problem else "accept", "reason": problem or "values check out"}
        return ("Recomputing...\n" if recompute else "") + json.dumps(verdict)

    # ------------------------------------------------------------ validator
    def _validate(self, stage, data, recompute, rng):
        src, out = data["source"], data["output"]
        problem = _visible_problem(stage, out)
        if problem is None and recompute:
            truth = _truth(stage, src)
            if truth is not None and _differs(stage, out, truth) and rng.random() >= self.recompute_miss:
                problem = "recomputed value differs"
        if problem is None and rng.random() < (self.recompute_fp if recompute else self.naive_fp):
            problem = "spurious doubt"
        verdict = {"verdict": "reject" if problem else "accept", "reason": problem or "values check out"}
        if recompute:
            return "Recomputing from the source...\nComparing...\n" + json.dumps(verdict)
        return json.dumps(verdict)


class _NoErrors(random.Random):
    """An rng whose random() never triggers an error branch (a successful repair)."""

    def random(self):
        return 0.999999


def _near_sorted(nums):
    ups = sum(a < b for a, b in zip(nums, nums[1:], strict=False))
    return len(nums) > 2 and min(ups, len(nums) - 1 - ups) <= 1


def _visible_problem(stage, out):
    if stage == "analyze":
        try:
            c, s, m, x = out["count"], out["sum"], out["mean"], out["max"]
            if abs(m - s / c) > 0.011:
                return "mean is not sum / count"
            if x < m:
                return "max below mean"
        except (KeyError, TypeError, ZeroDivisionError):
            return "missing or malformed values"
    if stage == "reason" and out.get("trend") not in TRENDS:
        return "unknown trend label"
    return None


def _truth(stage, src):
    try:
        if stage == "extract":
            return {"numbers": parse_numbers(src["raw_input"])}
        if stage == "analyze":
            return compute_stats(src["numbers"])
        if stage == "reason":
            return {"trend": compute_trend(src["numbers"])}
        return {"final_output": render_report(src["stats"], src["trend"])}
    except Exception:
        return None


def _differs(stage, out, truth):
    for k, v in truth.items():
        if k == "mean":
            if not isinstance(out.get(k), (int, float)) or abs(out[k] - v) > 0.011:
                return True
        elif out.get(k) != v:
            return True
    return False
