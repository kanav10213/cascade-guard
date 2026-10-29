"""An LLM validator with the same interface as CascadeGuard.

    guard = LLMGuard(client, prompt="recompute")      # or prompt="naive"
    run_chain(raw, trial, injector, guard, agents=agents)

For each stage in `llm_stages`:
  1. a cheap structural check (output is a dict, required keys present and not
     null). Failing it needs no model call.
  2. the LLM is shown the stage's source data and the output and asked for a
     verdict. A reject, or a reply with no parseable verdict, fails the stage.

Stages outside `llm_stages` use the rule-based CascadeGuard (`fallback_mode`),
or are accepted unchecked when fallback_mode is None.

Unlike CascadeGuard, the LLM validator is never given the expected answer, so
what it catches is a real measurement of the validator.
"""
import threading

from cascade_guard.guard.cascade_guard import CORRUPTED, MISSING, TRUNCATED, CascadeGuard
from cascade_guard.llm.prompts import (
    REQUIRED_KEYS,
    VALIDATOR_PROMPTS,
    generic_validator_messages,
    parse_last_json,
    validator_messages,
    validator_source,
)

REJECTED = "LLM_REJECTED"
UNPARSEABLE = "VALIDATOR_UNPARSEABLE"


def structural_issue(stage: str, out):
    if not isinstance(out, dict):
        return CORRUPTED
    keys = REQUIRED_KEYS[stage]
    if any(k not in out for k in keys):
        return TRUNCATED
    if any(out[k] is None for k in keys):
        return MISSING
    return None


class LLMGuard:
    FAILURE_TYPES = CascadeGuard.FAILURE_TYPES + [REJECTED, UNPARSEABLE]

    def __init__(self, client, prompt: str = "recompute", max_retries: int = 2,
                 llm_stages=("analyze", "reason"), fallback_mode="full", record: bool = False):
        if prompt not in VALIDATOR_PROMPTS:
            raise ValueError(f"prompt must be one of {sorted(VALIDATOR_PROMPTS)}")
        self.client = client
        self.prompt = prompt
        self.max_retries = max_retries
        self.llm_stages = tuple(llm_stages)
        self.fallback = CascadeGuard(fallback_mode, max_retries) if fallback_mode else None
        self.mode = f"llm-{prompt}"
        self._lock = threading.Lock()
        self._local = threading.local()
        self.stats = {"llm_checks": 0, "rejects": 0, "unparseable": 0, "structural": 0}
        self.log: list | None = [] if record else None  # with record=True: every verdict and its reason

    def judge(self, stage: str, out: dict, source: dict) -> dict:
        """Ask the model about one output. Returns {"verdict", "reason", "raw"}.
        verdict is "accept", "reject" or "unparseable"."""
        system, user, max_tokens = validator_messages(self.prompt, stage, source, out)
        resp = self.client.complete(system, user, max_tokens=max_tokens, temperature=0.0,
                                    purpose=f"validator:{self.prompt}")
        parsed = parse_last_json(resp.text) or {}
        verdict = str(parsed.get("verdict", "")).strip().lower()
        if verdict not in ("accept", "reject"):
            verdict = "unparseable"
        return {"verdict": verdict, "reason": parsed.get("reason"), "raw": resp.text}

    @property
    def last_reason(self):
        """The checker's words for its last rejection in this thread (for guided repair)."""
        return getattr(self._local, "reason", None)

    def check(self, stage: str, out, raw: str, state: dict):
        self._local.reason = None
        if stage not in self.llm_stages:
            return self.fallback.check(stage, out, raw, state) if self.fallback else None
        issue = structural_issue(stage, out)
        if issue:
            with self._lock:
                self.stats["structural"] += 1
            return issue
        j = self.judge(stage, out, validator_source(stage, raw, state))
        if j["verdict"] != "accept":
            self._local.reason = j["reason"] if j["verdict"] == "reject" else "no clear verdict from the checker"
        if self.log is not None:
            with self._lock:
                self.log.append({"stage": stage, "verdict": j["verdict"], "reason": j["reason"]})
        with self._lock:
            self.stats["llm_checks"] += 1
            if j["verdict"] == "reject":
                self.stats["rejects"] += 1
            elif j["verdict"] == "unparseable":
                self.stats["unparseable"] += 1
        if j["verdict"] == "accept":
            return None
        return REJECTED if j["verdict"] == "reject" else UNPARSEABLE


def verify(client, task: str, source, output, prompt: str = "recompute") -> dict:
    """Check any agent output against its source with the LLM validator.

    task    what the agent was asked to do, in plain words
    source  the agent's input (text or JSON)
    output  what the agent produced (text or JSON)
    Returns {"passed", "verdict", "reason"}; passed is True only on an explicit accept."""
    if prompt not in VALIDATOR_PROMPTS:
        raise ValueError(f"prompt must be one of {sorted(VALIDATOR_PROMPTS)}")
    system, user, max_tokens = generic_validator_messages(prompt, task, source, output)
    resp = client.complete(system, user, max_tokens=max_tokens, temperature=0.0,
                           purpose=f"verify:{prompt}")
    parsed = parse_last_json(resp.text) or {}
    verdict = str(parsed.get("verdict", "")).strip().lower()
    if verdict not in ("accept", "reject"):
        verdict = "unparseable"
    return {"passed": verdict == "accept", "verdict": verdict,
            "reason": parsed.get("reason") if verdict != "unparseable" else resp.text[:300]}
