"""LLM-backed agents with the same interface as the rule-based ones.

    agents = build_agents(client)            # analyze + reason by the LLM, rest by rules
    run_chain(raw, trial, injector, guard, agents=agents)

An LLM agent is called as agent(raw, state, attempt=0). Each attempt is a
separate request (and a separate cache entry), so a retry is a fresh draw from
the model rather than a replay of the first answer. Temperature 0 for the first
attempt and `retry_temperature` for retries are requested, but only sent when
the installed SDK supports sampling parameters (recent versions do not; the
model's default sampling is then used for every attempt).

If the reply cannot be parsed, the agent returns {"_unparsed": "..."} instead
of raising. The guard then sees missing keys and can retry; without a guard the
next stage fails, which is exactly the cascade being studied.
"""
import contextlib

from cascade_guard.agents.pipeline import AGENTS, STAGES
from cascade_guard.llm.prompts import AGENT_MAX_TOKENS, REQUIRED_KEYS, agent_messages, parse_last_json

DEFAULT_LLM_STAGES = ("analyze", "reason")


def agent_input(stage: str, raw: str, state: dict) -> dict:
    if stage == "extract":
        return {"raw_input": raw}
    if stage in ("analyze", "reason"):
        return {"numbers": state["extract"]["numbers"]}
    return {"stats": state["analyze"], "trend": state["reason"]["trend"]}


def _as_int(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        try:
            f = float(v)
            return int(f) if f.is_integer() else f
        except ValueError:
            return v
    return v


def normalise(stage: str, out: dict) -> dict:
    """Keep only the output keys and coerce obvious type noise ("12" -> 12).

    For analyze, the mean is re-rounded from the agent's OWN sum and count when
    the agent's mean already matches sum/count within 0.0051. This removes
    rounding-convention noise (Python rounds 5.125 to 5.12, people say 5.13)
    without ever using the ground truth, so a wrong sum stays wrong."""
    out = {k: out.get(k) for k in REQUIRED_KEYS[stage]}
    if stage == "analyze":
        for k in ("count", "sum", "max"):
            out[k] = _as_int(out[k])
        m = out["mean"]
        if isinstance(m, str):
            with contextlib.suppress(ValueError):
                m = float(m)
        out["mean"] = m
        c, s = out["count"], out["sum"]
        numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (c, s, m))
        if numbers and c and abs(m - s / c) <= 0.0051:
            out["mean"] = round(s / c, 2)
    elif stage == "reason" and isinstance(out["trend"], str):
        out["trend"] = out["trend"].strip().lower()
    elif stage == "extract" and isinstance(out["numbers"], list):
        out["numbers"] = [_as_int(v) for v in out["numbers"]]
    return out


class LLMAgent:
    accepts_attempt = True
    accepts_feedback = True  # guided repair: a retry is told why the last answer was rejected

    def __init__(self, stage: str, client, retry_temperature: float = 1.0):
        self.stage = stage
        self.client = client
        self.retry_temperature = retry_temperature
        self.__name__ = f"llm_{stage}_agent"

    def __call__(self, raw: str, state: dict, attempt: int = 0, feedback=None) -> dict:
        system, user = agent_messages(self.stage, agent_input(self.stage, raw, state), feedback)
        resp = self.client.complete(
            system, user, max_tokens=AGENT_MAX_TOKENS,
            temperature=0.0 if attempt == 0 else self.retry_temperature,
            sample=attempt, purpose=f"agent:{self.stage}")
        parsed = parse_last_json(resp.text)
        if parsed is None:
            return {"_unparsed": resp.text[:300]}
        return normalise(self.stage, parsed)


def build_agents(client, llm_stages=DEFAULT_LLM_STAGES, retry_temperature: float = 1.0) -> dict:
    unknown = set(llm_stages) - set(STAGES)
    if unknown:
        raise ValueError(f"unknown stages: {sorted(unknown)}")
    agents = dict(AGENTS)
    for stage in llm_stages:
        agents[stage] = LLMAgent(stage, client, retry_temperature)
    return agents
