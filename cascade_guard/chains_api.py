"""Build-your-own agent chains over HTTP.

    GET    /chains                 saved chains (viewer)
    GET    /chains/templates       starter chains to copy and change (viewer)
    POST   /chains                 save a new chain (admin)
    GET    /chains/{id}            one chain with its full definition (viewer)
    PUT    /chains/{id}            replace a chain's definition (admin)
    DELETE /chains/{id}            delete a chain (admin)
    GET    /chains/{id}/graph      the chain as a Mermaid diagram (viewer)
    POST   /chains/{id}/run        run a saved chain on an input
    POST   /chains/run             run a definition without saving it (try before you save)
    POST   /demo/agent             a tiny sample "outside agent" to try steps done by your own agent

A step can be done by the configured model or by your own agent at a URL (see agent_call.py).

Runs on the simulated model are free and open to any key; runs on the configured model need an
admin key, and so does running an unsaved definition that calls outside agents (a saved chain's agent
URLs were set by an admin) and share the per-minute limit of the other model routes. Every run is saved with its
steps and verdicts, and a stopped run goes to the review queue.
"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, ValidationError

from cascade_guard.auth import ensure, require
from cascade_guard.chains import ChainDef, mermaid, run_chain_def

router = APIRouter(tags=["chains"])
TEMPLATES = Path(__file__).with_name("templates")
MAX_INPUT = 20_000


class RunOptions(BaseModel):
    input: str = Field(min_length=1, max_length=MAX_INPUT)
    simulated: bool = Field(True, description="true: offline simulator, free; false: the configured model")
    inject: float = Field(0.0, ge=0, le=1, description="chance of a planted test mistake in each step's first answer")
    seed: int = 0
    max_retries: int | None = Field(None, ge=0, le=5, description="default: the chain's own setting")
    repair: str | None = Field(None, pattern="^(guided|blind)$")


class AdhocRun(RunOptions):
    chain: ChainDef


def _store(required=True):
    from cascade_guard.storage.db import get_store
    store = get_store()
    if store is None and required:
        raise HTTPException(status_code=503, detail="Storage is off (CASCADE_DB=off), so chains cannot be saved. "
                                                    "You can still run a chain without saving it.")
    return store


def _chain_or_404(chain_id: int) -> dict:
    row = _store().get_chain(chain_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such chain")
    return row


@router.get("/chains", dependencies=[Depends(require("viewer"))])
def list_chains():
    store = _store(required=False)
    return [] if store is None else store.list_chains()


@router.get("/chains/templates", dependencies=[Depends(require("viewer"))])
def templates():
    out = []
    for f in sorted(TEMPLATES.glob("*.json")):
        out.append(ChainDef.model_validate(json.loads(f.read_text(encoding="utf-8"))).model_dump())
    return out


@router.post("/chains", status_code=201)
def create_chain(chain: ChainDef, role: str = Depends(require("admin"))):
    try:
        chain_id = _store().save_chain(chain.model_dump(), by=role)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _store().get_chain(chain_id)


@router.get("/chains/{chain_id}", dependencies=[Depends(require("viewer"))])
def get_chain(chain_id: int):
    return _chain_or_404(chain_id)


@router.put("/chains/{chain_id}", dependencies=[Depends(require("admin"))])
def update_chain(chain_id: int, chain: ChainDef):
    try:
        _store().save_chain(chain.model_dump(), chain_id=chain_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="no such chain") from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _store().get_chain(chain_id)


@router.delete("/chains/{chain_id}", dependencies=[Depends(require("admin"))])
def delete_chain(chain_id: int):
    if not _store().delete_chain(chain_id):
        raise HTTPException(status_code=404, detail="no such chain")
    return {"deleted": chain_id}


@router.get("/chains/{chain_id}/graph", dependencies=[Depends(require("viewer"))])
def graph(chain_id: int):
    chain = ChainDef.model_validate(_chain_or_404(chain_id)["definition"])
    return {"mermaid": mermaid(chain)}


def _client(opts: RunOptions, role: str, key: str):
    if opts.simulated:
        from cascade_guard.llm.client import FakeLLMClient
        return FakeLLMClient(cache_path=None)
    ensure(role, "admin")
    from cascade_guard.llm_api import get_client
    from cascade_guard.pilot_api import _llm_rate_limit
    _llm_rate_limit(key)
    return get_client()


def _run(chain: ChainDef, opts: RunOptions, role: str, key: str, chain_id: int | None = None):
    from cascade_guard.agent_call import AgentCallError
    from cascade_guard.llm.client import BudgetExceeded, LLMCallError
    client = _client(opts, role, key)
    try:
        result = run_chain_def(chain, opts.input, client, inject=opts.inject, seed=opts.seed,
                               max_retries=opts.max_retries, repair=opts.repair)
    except AgentCallError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except BudgetExceeded as e:
        raise HTTPException(status_code=429, detail=f"LLM limit for this server run reached: {e}") from e
    except LLMCallError as e:
        raise HTTPException(status_code=502, detail=f"The model call failed: {e}") from e
    model = {"provider": client.provider, "model": client.model, "simulated": client.is_fake}
    result["model"], result["chain_id"] = model, chain_id
    store = _store(required=False)
    if store is not None:
        final = result["final_output"]
        saved = dict(result, final_output=final if isinstance(final, str) or final is None
                     else json.dumps(final, default=str))
        result["run_id"] = store.save_pipeline_run(
            saved, opts.input, guard="chain", engine="langgraph", model=model, kind="chain",
            usage_delta=result["usage"],
            validator_log=[{"stage": c["step"], "verdict": c["verdict"], "reason": c["reason"]}
                           for c in result["checks"]], prompt=f"chain:{chain.name}"[:100])
    return result


@router.post("/chains/{chain_id}/run")
def run_saved(chain_id: int, opts: RunOptions, role: str = Depends(require("viewer")),
              req_key: str = Header(default="", alias="X-API-Key")):
    row = _chain_or_404(chain_id)
    try:
        chain = ChainDef.model_validate(row["definition"])
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"the saved chain is no longer valid: {e}") from e
    return _run(chain, opts, role, req_key, chain_id)


@router.post("/chains/run")
def run_adhoc(body: AdhocRun, role: str = Depends(require("viewer")),
              req_key: str = Header(default="", alias="X-API-Key")):
    if any(s.agent == "url" for s in body.chain.steps):
        ensure(role, "admin")  # an unsaved definition must not make the server call any URL a viewer picks
    return _run(body.chain, body, role, req_key)


# ---------------------------------------------------------------- a sample outside agent

class AgentRequest(BaseModel):
    step: str = ""
    instruction: str = ""
    output: str = "text"
    keys: list[str] = Field(default_factory=list, max_length=12)
    input: object = None
    original: str = ""
    attempt: int = 0
    feedback: dict | None = None


@router.post("/demo/agent", tags=["demo"])
def demo_agent(req: AgentRequest, mistakes: float = 0.0):
    """A deliberately simple outside agent that speaks the agent protocol, so you can try a step done by
    "your agent" right away (URL: this server's /demo/agent). It answers with the first two sentences of
    its input. With ?mistakes=0.5 its first answer is empty half of the time, so you can watch the guard
    catch it and retry; told what was wrong (feedback), it always answers properly."""
    import random as _random
    import re as _re
    text = req.input if isinstance(req.input, str) else json.dumps(req.input, default=str)
    sentences = [x.strip() for x in _re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if x.strip()]
    answer = " ".join(sentences[:2]) or text[:300]
    rng = _random.Random(f"{req.step}|{text}|{req.attempt}")
    if req.attempt == 0 and not req.feedback and rng.random() < max(0.0, min(1.0, mistakes)):
        answer = ""
    if req.output == "json":
        return {"output": {k: answer for k in req.keys}}
    return {"output": answer}
