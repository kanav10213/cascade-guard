"""A sequential chain of four deterministic agents.

Each agent reads the raw input plus the outputs of earlier agents and returns a
dict. The chain is deliberately simple so that the "correct" answer is known and
every failure can be scored exactly.
"""
import re

STAGES = ("extract", "analyze", "reason", "report")
TRENDS = ("increasing", "decreasing", "mixed", "flat")


def parse_numbers(raw: str) -> list:
    tokens = [t for t in re.split(r"[,\s]+", raw.strip()) if t]
    if not tokens:
        raise ValueError("input contains no numbers")
    return [int(t) for t in tokens]  # ValueError on non-integers


def compute_stats(nums: list) -> dict:
    total = sum(nums)
    return {
        "count": len(nums),
        "sum": total,
        "mean": round(total / len(nums), 2),
        "max": max(nums),
    }


def compute_trend(nums: list) -> str:
    pairs = list(zip(nums, nums[1:], strict=False))
    if not pairs:
        return "flat"
    if all(a < b for a, b in pairs):
        return "increasing"
    if all(a > b for a, b in pairs):
        return "decreasing"
    return "mixed"


def render_report(stats: dict, trend: str) -> str:
    return (
        f"count={stats['count']}; sum={stats['sum']}; "
        f"mean={stats['mean']}; max={stats['max']}; trend={trend}"
    )


def extract_agent(raw: str, state: dict) -> dict:
    return {"numbers": parse_numbers(raw)}


def analyze_agent(raw: str, state: dict) -> dict:
    return compute_stats(state["extract"]["numbers"])


def reason_agent(raw: str, state: dict) -> dict:
    return {"trend": compute_trend(state["extract"]["numbers"])}


def report_agent(raw: str, state: dict) -> dict:
    return {"final_output": render_report(state["analyze"], state["reason"]["trend"])}


AGENTS: dict = {
    "extract": extract_agent,
    "analyze": analyze_agent,
    "reason": reason_agent,
    "report": report_agent,
}


def expected_output(raw: str) -> str:
    """Ground truth for an input, computed independently of the chain."""
    nums = parse_numbers(raw)
    return render_report(compute_stats(nums), compute_trend(nums))
