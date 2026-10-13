"""Stage-by-stage validation with bounded retry.

Two modes:
  invariants  cross-agent consistency only: each stage is checked against the
              earlier validated stages. It cannot see an error that entered at
              the first stage and was reproduced faithfully by every later one.
  full        invariants plus a source check of the first stage against the
              raw input.
"""
from app.agents.pipeline import (
    TRENDS,
    compute_stats,
    compute_trend,
    parse_numbers,
    render_report,
)

CORRUPTED = "CORRUPTED_OUTPUT"
MISSING = "MISSING_DATA"
INVALID = "INVALID_REASONING"
TRUNCATED = "TRUNCATED_OUTPUT"


def _compare_list(actual: list, expected: list):
    if actual == expected:
        return None
    if len(actual) < len(expected) and actual == expected[: len(actual)]:
        return TRUNCATED
    if sorted(actual) == sorted(expected):
        return INVALID
    return CORRUPTED


class CascadeGuard:
    FAILURE_TYPES = [CORRUPTED, MISSING, INVALID, TRUNCATED]
    MODES = ("invariants", "full")

    def __init__(self, mode: str = "full", max_retries: int = 2):
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {self.MODES}")
        self.mode = mode
        self.max_retries = max_retries

    def check(self, stage: str, out, raw: str, state: dict):
        """Return a failure type, or None if the stage output is acceptable."""
        if not isinstance(out, dict):
            return CORRUPTED
        return getattr(self, f"_check_{stage}")(out, raw, state)

    def _check_extract(self, out, raw, state):
        nums = out.get("numbers")
        if not nums:
            return MISSING
        if not isinstance(nums, list) or not all(
            isinstance(n, int) and not isinstance(n, bool) for n in nums
        ):
            return CORRUPTED
        if self.mode == "full":
            return _compare_list(nums, parse_numbers(raw))
        return None

    def _check_analyze(self, out, raw, state):
        required = ("count", "sum", "mean", "max")
        if any(k not in out for k in required):
            return TRUNCATED
        if any(out[k] is None for k in required):
            return MISSING
        expected = compute_stats(state["extract"]["numbers"])
        diffs = [k for k in required if out[k] != expected[k]]
        if not diffs:
            return None
        if len(diffs) == 2 and out[diffs[0]] == expected[diffs[1]] and out[diffs[1]] == expected[diffs[0]]:
            return INVALID
        return CORRUPTED

    def _check_reason(self, out, raw, state):
        trend = out.get("trend")
        if trend is None:
            return MISSING
        if not isinstance(trend, str):
            return CORRUPTED
        expected = compute_trend(state["extract"]["numbers"])
        if trend == expected:
            return None
        if expected.startswith(trend):
            return TRUNCATED
        return INVALID if trend in TRENDS else CORRUPTED

    def _check_report(self, out, raw, state):
        text = out.get("final_output")
        if text is None or text == "":
            return MISSING
        if not isinstance(text, str):
            return CORRUPTED
        expected = render_report(state["analyze"], state["reason"]["trend"])
        if text == expected:
            return None
        if expected.startswith(text):
            return TRUNCATED
        if sorted(text.split("; ")) == sorted(expected.split("; ")):
            return INVALID
        return CORRUPTED
