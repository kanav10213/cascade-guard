"""Prompts for the LLM agents and the LLM validator, plus reply parsing.

Every user message has the same machine-readable layout:

    TASK: <analyze | reason | validate>
    STAGE: <stage>              (validate only)
    DATA: <one line of JSON>

so that the offline simulator can answer it and so prompts stay comparable.
"""
import json
from typing import Any

STAGE_SPECS = {
    "extract": (
        "Parse the raw input text into a list of integers, in the order they appear. "
        "Output keys: numbers (list of integers)."
    ),
    "analyze": (
        "From the list of integers compute: count = how many numbers there are; "
        "sum = their total; mean = sum / count rounded to 2 decimal places; "
        "max = the largest number. Output keys: count, sum, mean, max."
    ),
    "reason": (
        "Classify the order of the list. trend = \"increasing\" if every number is strictly "
        "greater than the one before it; \"decreasing\" if every number is strictly smaller "
        "than the one before it; \"flat\" if the list has fewer than two numbers; otherwise "
        "\"mixed\" (this includes any list with two equal neighbours). Output keys: trend."
    ),
    "report": (
        "Render the statistics and trend as the single line "
        "\"count=C; sum=S; mean=M; max=X; trend=T\". Output keys: final_output (string)."
    ),
}

REQUIRED_KEYS = {
    "extract": ("numbers",),
    "analyze": ("count", "sum", "mean", "max"),
    "reason": ("trend",),
    "report": ("final_output",),
}

# ---------------------------------------------------------------- agents

AGENT_SYSTEM = (
    "You are the {stage} agent in a data-processing pipeline. {spec}\n"
    "Work it out yourself; you have no tools. Reply with only one JSON object "
    "containing exactly the output keys, and no other text.\n"
    "If PREVIOUS and FEEDBACK are given, a checker rejected your previous answer: work the task again "
    "from DATA, fix the problem FEEDBACK names, and reply with the corrected JSON object only."
)

REPAIR_LINES = "\nPREVIOUS: {previous}\nFEEDBACK: {reason}"


def repair_suffix(feedback) -> str:
    """Lines added to a retry's user message under guided repair."""
    if not feedback:
        return ""
    prev = json.dumps(feedback.get("previous_output"), default=str)[:2000]
    reason = " ".join(str(feedback.get("reason") or "rejected").split())[:500]
    return REPAIR_LINES.format(previous=prev, reason=reason)

AGENT_MAX_TOKENS = 200


def agent_messages(stage: str, data: dict, feedback=None):
    system = AGENT_SYSTEM.format(stage=stage, spec=STAGE_SPECS[stage])
    user = f"TASK: {stage}\nDATA: {json.dumps(data)}" + repair_suffix(feedback)
    return system, user


# ---------------------------------------------------------------- validator

VALIDATOR_PROMPTS: dict[str, dict[str, Any]] = {
    # The common default: ask the model whether the output is plausible.
    "naive": {
        "system": (
            "You are a quality checker in a data-processing pipeline. An upstream agent "
            "was given the task below and produced the output shown. Check whether the "
            "output looks right.\n"
            "Task the agent was given: {spec}\n"
            "Reply with only one JSON object: "
            "{{\"verdict\": \"accept\" or \"reject\", \"reason\": \"<one short sentence>\"}}"
        ),
        "max_tokens": 150,
    },
    # Independent recomputation before comparing.
    "recompute": {
        "system": (
            "You are a validator in a data-processing pipeline. An upstream agent was given "
            "the task below and produced the output shown. Do not trust the output.\n"
            "Task the agent was given: {spec}\n"
            "Step 1: ignore the agent's output and recompute every output value yourself "
            "from the source data. Show your working briefly; when adding, keep a running "
            "total one number at a time; when counting, number the items.\n"
            "Step 2: compare your values with the agent's values one by one.\n"
            "Step 3: on the last line, output only one JSON object: "
            "{{\"verdict\": \"accept\" or \"reject\", \"reason\": \"<one short sentence>\"}}. "
            "Reject if any value differs (a mean may differ only by rounding, at most 0.01)."
        ),
        "max_tokens": 1500,
    },
}


def validator_source(stage: str, raw: str, state: dict) -> dict:
    """What the validator is shown as the agent's input."""
    if stage == "extract":
        return {"raw_input": raw}
    if stage in ("analyze", "reason"):
        return {"numbers": (state.get("extract") or {}).get("numbers")}
    return {"stats": state.get("analyze"), "trend": (state.get("reason") or {}).get("trend")}


def validator_messages(prompt: str, stage: str, source: dict, output: dict):
    cfg = VALIDATOR_PROMPTS[prompt]
    system = cfg["system"].format(spec=STAGE_SPECS[stage])
    user = (f"TASK: validate\nSTAGE: {stage}\n"
            f"DATA: {json.dumps({'source': source, 'output': output})}")
    return system, user, cfg["max_tokens"]


def generic_validator_messages(prompt: str, task: str, source, output):
    """Validator prompt for any task, described in plain words by the caller."""
    cfg = VALIDATOR_PROMPTS[prompt]
    system = cfg["system"].format(spec=task.strip())
    user = (f"TASK: validate\nSTAGE: custom\n"
            f"DATA: {json.dumps({'source': source, 'output': output})}")
    return system, user, cfg["max_tokens"]


# ---------------------------------------------------------------- parsing

def parse_last_json(text: str):
    """Return the last JSON object in `text` (models sometimes add prose or a
    code fence around it), or None."""
    if not isinstance(text, str):
        return None
    dec = json.JSONDecoder()
    found = None
    i = text.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict):
            found = obj
        i = text.find("{", end)
    return found


def parse_request(user: str) -> dict:
    """Inverse of the user-message layout (used by the simulator)."""
    out = {}
    for line in user.splitlines():
        key, _, val = line.partition(": ")
        key = key.strip()
        if key in ("DATA", "PREVIOUS"):
            try:
                out[key] = json.loads(val)
            except ValueError:
                out[key] = val
        else:
            out[key] = val.strip()
    return out
