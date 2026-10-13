import json
import re

ANALYST_SYSTEM = (
    "[task=analyst]\n"
    "You are a precise calculation agent. Given a list of integers, compute the count, "
    "the sum, the mean (rounded to 2 decimals) and the maximum.\n"
    'Reply with ONLY a JSON object: {"count": <int>, "sum": <int>, "mean": <number>, "max": <int>}'
)

TREND_SYSTEM = (
    "[task=trend]\n"
    "You are a precise analysis agent. Given a list of integers in order, label the trend.\n"
    "increasing = every value is strictly larger than the one before it.\n"
    "decreasing = every value is strictly smaller than the one before it.\n"
    "flat = the list has a single value.\n"
    "mixed = anything else.\n"
    'Reply with ONLY a JSON object: {"trend": "increasing" | "decreasing" | "mixed" | "flat"}'
)

VALIDATOR_NAIVE_SYSTEM = (
    "[task=validator-naive]\n"
    "You review the work of other agents. You are given a list of numbers, figures "
    "(count, sum, mean, max) and a trend label that another agent produced. "
    "Check whether the figures and trend look right for the numbers.\n"
    'Reply with ONLY a JSON object: {"valid": true | false, "reason": "<short reason>"}'
)

VALIDATOR_RECOMPUTE_SYSTEM = (
    "[task=validator-recompute]\n"
    "You are an independent verifier. Do NOT trust the figures you are given. "
    "First recompute count, sum, mean (rounded to 2 decimals), max and the trend yourself, "
    "directly from the numbers. Then compare your values with the given ones. "
    "(Trend: increasing = strictly rising, decreasing = strictly falling, flat = one value, mixed = otherwise.)\n"
    'Reply with ONLY a JSON object: {"recomputed": {"count": <int>, "sum": <int>, "mean": <number>, '
    '"max": <int>, "trend": "<label>"}, "valid": true | false, "reason": "<short reason>"}\n'
    '"valid" is true only if every given figure and the given trend equal your recomputed values.'
)

VALIDATOR_SYSTEMS = {"naive": VALIDATOR_NAIVE_SYSTEM, "recompute": VALIDATOR_RECOMPUTE_SYSTEM}


def numbers_line(nums) -> str:
    return "NUMBERS: " + ", ".join(str(n) for n in nums)


def analyst_user(nums) -> str:
    return numbers_line(nums)


def trend_user(nums) -> str:
    return numbers_line(nums)


def validator_user(nums, figures, trend) -> str:
    return f"{numbers_line(nums)}\nFIGURES: {json.dumps(figures)}\nTREND: {trend}"


def parse_json(text):
    """Pull the first JSON object out of a model reply (tolerates code fences and chatter)."""
    if not isinstance(text, str):
        return None
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None
