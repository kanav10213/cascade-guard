from app.llm.client import LLMError
from app.llm.prompts import (VALIDATOR_SYSTEMS, ANALYST_SYSTEM, TREND_SYSTEM, analyst_user,
                             parse_json, trend_user, validator_user)


def _ask(client, system, user):
    try:
        return parse_json(client.complete(system, user))
    except LLMError:
        return None  # counted as an unparseable / failed answer; client.usage.errors tracks it


def run_analyst(client, nums):
    return _ask(client, ANALYST_SYSTEM, analyst_user(nums))


def run_trend(client, nums):
    return _ask(client, TREND_SYSTEM, trend_user(nums))


def run_validator(client, nums, figures, trend, style):
    """Returns {"valid": True | False | None}. None means no usable verdict; callers fail closed."""
    d = _ask(client, VALIDATOR_SYSTEMS[style], validator_user(nums, figures or {}, trend))
    valid = d.get("valid") if isinstance(d, dict) else None
    return {"valid": valid if isinstance(valid, bool) else None}


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def figures_correct(out, truth) -> bool:
    if not isinstance(out, dict):
        return False
    for k in ("count", "sum", "max"):
        if not _num(out.get(k)) or out[k] != truth[k]:
            return False
    return _num(out.get("mean")) and abs(out["mean"] - truth["mean"]) <= 0.01


def trend_correct(out, truth_trend) -> bool:
    return isinstance(out, dict) and isinstance(out.get("trend"), str) and out["trend"].strip().lower() == truth_trend
