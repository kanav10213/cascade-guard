"""HTTP routes for the LLM version.

    GET  /llm/status     is a model configured? provider, model, calls and spend so far
    POST /llm/pipeline   run one input through the chain with LLM agents, with or without a guard
    POST /llm/verify     check ANY agent output against its source with the LLM validator

The model client is created on first use from the environment / .env file, and
shared by all requests. Two limits protect the key while the server runs:
LLM_MAX_COST (USD, default 1.00; only when the model's price is known) and
LLM_MAX_CALLS (default 1000). Restart the server to reset them or to pick up a
changed .env.
"""
import json
import os
import threading
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from cascade_guard.agents.llm_agents import build_agents
from cascade_guard.agents.pipeline import parse_numbers
from cascade_guard.auth import require
from cascade_guard.evaluation.error_injector import ErrorInjector
from cascade_guard.evaluation.runner import run_chain
from cascade_guard.guard.cascade_guard import CascadeGuard
from cascade_guard.guard.llm_guard import LLMGuard, verify
from cascade_guard.llm.client import BudgetExceeded, LLMCallError, NotConfigured, make_client
from cascade_guard.storage.db import get_store

router = APIRouter(prefix="/llm", tags=["llm"])

_client = None
_client_error: str | None = None
_lock = threading.Lock()


def _settings():
    return {
        "max_cost": float(os.getenv("LLM_MAX_COST", "1.0")),
        "max_calls": int(os.getenv("LLM_MAX_CALLS", "1000")),
        "cache": os.getenv("LLM_CACHE", ".llm_cache/service.jsonl"),
    }


def get_client(required: bool = True):
    """The shared client, or HTTP 503 with a plain explanation of what is missing."""
    global _client, _client_error
    with _lock:
        if _client is None and _client_error is None:
            s = _settings()
            try:
                _client = make_client(max_cost=s["max_cost"], max_calls=s["max_calls"],
                                      cache_path=s["cache"] or None, strict_prices=False)
            except (NotConfigured, ValueError) as e:
                _client_error = str(e)
        if _client is None and required:
            raise HTTPException(status_code=503, detail=f"No LLM configured: {_client_error}")
        return _client


def reset_client(client=None):
    """Drop the shared client (tests use this to install a fake one)."""
    global _client, _client_error
    with _lock:
        _client, _client_error = client, None


def _call(fn):
    try:
        return fn()
    except BudgetExceeded as e:
        raise HTTPException(status_code=429, detail=f"LLM limit for this server run reached: {e}. "
                                                     "Raise LLM_MAX_COST / LLM_MAX_CALLS and restart.") from e
    except LLMCallError as e:
        raise HTTPException(status_code=502, detail=f"The model call failed: {e}") from e


class LLMPipelineRequest(BaseModel):
    numbers: str = Field("12, 45, 7, 88, 23, 61, 9, 34", min_length=1, max_length=1000,
                         description="Comma or space separated integers")
    guard: Literal["none", "recompute", "naive", "rules"] = Field(
        "recompute", description="none, LLM validator (recompute or naive prompt), or the rule-based guard")
    inject_probability: float = Field(0.0, ge=0, le=1, description="extra injected faults on top of the model's own")
    seed: int = 0
    max_retries: int = Field(2, ge=0, le=3)
    engine: Literal["langgraph", "custom"] = Field(
        "langgraph", description="Run the chain on LangGraph (default) or on the built-in orchestrator")
    repair: Literal["guided", "blind"] = Field(
        "guided", description="On a retry, tell the agent why it was rejected (guided) or just ask again (blind)")

    @field_validator("numbers")
    @classmethod
    def _numbers_ok(cls, v):
        nums = parse_numbers(v)
        if len(nums) > 60:
            raise ValueError("at most 60 numbers on the LLM route")
        return v


class VerifyRequest(BaseModel):
    task: str = Field(..., min_length=3, max_length=4000,
                      description="What the agent was asked to do, in plain words")
    source: Any = Field(..., description="The agent's input: text or JSON")
    output: Any = Field(..., description="What the agent produced: text or JSON")
    prompt: Literal["recompute", "naive"] = "recompute"

    @field_validator("source", "output")
    @classmethod
    def _size(cls, v):
        if len(json.dumps(v, default=str)) > 20_000:
            raise ValueError("at most 20,000 characters")
        return v


@router.get("/status", dependencies=[Depends(require("viewer"))])
def status():
    c = get_client(required=False)
    if c is None:
        return {"configured": False, "error": _client_error,
                "how_to_fix": "Copy .env.example to .env, put your API key in it, and restart the server."}
    return {"configured": True, **c.describe()}


@router.post("/pipeline", dependencies=[Depends(require("admin"))])
def llm_pipeline(req: LLMPipelineRequest):
    """One input through extract -> analyze (LLM) -> reason (LLM) -> report.
    Call it twice with guard="none" and a guard to compare: the agents' first
    answers are cached, so both runs start from the same model output."""
    client = get_client()
    agents = build_agents(client)
    guard: Any = None
    if req.guard == "rules":
        guard = CascadeGuard("full", req.max_retries)
    elif req.guard != "none":
        guard = LLMGuard(client, req.guard, req.max_retries, record=True)
    injector = ErrorInjector(req.inject_probability, req.seed)
    before = client.usage.as_dict()
    if req.engine == "langgraph":
        from cascade_guard.orchestration.langgraph_chain import run_chain_langgraph
        result = _call(lambda: run_chain_langgraph(req.numbers, req.seed, injector, guard, agents, req.repair))
    else:
        result = _call(lambda: run_chain(req.numbers, req.seed, injector, guard, agents, req.repair))
    result["repair"] = req.repair
    result["engine"] = req.engine
    result["guard"] = req.guard
    result["validator_log"] = guard.log if isinstance(guard, LLMGuard) else []
    result["model"] = {"provider": client.provider, "model": client.model, "simulated": client.is_fake}
    result["usage"] = client.usage.as_dict()
    store = get_store()
    if store is not None:
        result["run_id"] = store.save_pipeline_run(
            result, req.numbers, guard=req.guard, engine=req.engine, model=result["model"],
            usage_delta=_delta(before, result["usage"]), validator_log=result["validator_log"],
            prompt=req.guard if req.guard in ("naive", "recompute") else None)
    return result


@router.post("/verify", dependencies=[Depends(require("admin"))])
def llm_verify(req: VerifyRequest):
    """Use the LLM validator on any agent output, from any system."""
    client = get_client()
    before = client.usage.as_dict()
    out = _call(lambda: verify(client, req.task, req.source, req.output, req.prompt))
    out["prompt"] = req.prompt
    out["model"] = {"provider": client.provider, "model": client.model, "simulated": client.is_fake}
    store = get_store()
    if store is not None:
        out["run_id"] = store.save_verify(req.model_dump(), out, model=out["model"],
                                          usage_delta=_delta(before, client.usage.as_dict()))
    return out


def _delta(before: dict, after: dict) -> dict:
    """What one request used (approximate when requests overlap)."""
    return {k: round(after[k] - before[k], 6) if k == "cost_usd" else after[k] - before[k]
            for k in ("calls", "cached_calls", "input_tokens", "output_tokens", "cost_usd")}
