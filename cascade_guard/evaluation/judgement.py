FAILURE_TYPES = [
    "CORRUPTED_OUTPUT",
    "MISSING_DATA",
    "INVALID_REASONING",
    "TRUNCATED_OUTPUT",
]


def get_failure_reason(result: dict) -> str:
    """Text-level check used by the /validate endpoint."""
    txt = str(result.get("final_output", ""))
    if not txt.strip():
        return "MISSING_DATA"
    for marker in FAILURE_TYPES:
        if marker in txt:
            return marker
    return "NONE"


def is_successful(result: dict) -> bool:
    return get_failure_reason(result) == "NONE"


def is_correct(final_output, expected: str) -> bool:
    """Experiment-level check: the delivered output equals the ground truth."""
    return final_output == expected
