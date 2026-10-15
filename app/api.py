import os
import secrets
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from app.agents.pipeline import parse_numbers
from app.evaluation.error_injector import ErrorInjector
from app.evaluation.judgement import get_failure_reason
from app.evaluation.runner import run_chain, run_experiment
from app.guard.cascade_guard import CascadeGuard
from app.llm import jobs as llm_jobs
from app.llm.client import DEFAULT_MODEL
from app.llm.experiments import PARTS, plan as llm_plan

app = FastAPI(
    title="Cascade Guard",
    version="1.0.0",
    description="Detect, halt and recover from error cascades in a multi-agent chain.",
)


def require_key(x_api_key: Optional[str] = Header(default=None)):
    """If the API_KEY environment variable is set, every route except /health needs it."""
    expected = os.getenv("API_KEY")
    if expected and not secrets.compare_digest(x_api_key or "", expected):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


class ValidationRequest(BaseModel):
    text: str = Field(max_length=100_000)


class PipelineRequest(BaseModel):
    numbers: str = Field("3, 7, 2, 9", min_length=1, max_length=2000,
                         description="Comma or space separated integers")
    protected: bool = True
    inject_probability: float = Field(0.3, ge=0, le=1)
    seed: int = 42
    max_retries: int = Field(2, ge=0, le=5)
    mode: Literal["invariants", "full"] = "full"

    @field_validator("numbers")
    @classmethod
    def _numbers_ok(cls, v):
        nums = parse_numbers(v)  # ValueError -> HTTP 422
        if len(nums) > 200:
            raise ValueError("at most 200 numbers")
        return v


class ExperimentRequest(BaseModel):
    trials: int = Field(500, ge=1)
    seed: int = 42
    inject_probability: float = Field(0.1, ge=0, le=1)
    max_retries: int = Field(2, ge=0, le=5)
    mode: Literal["invariants", "full"] = "full"


STATIC = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/validate", dependencies=[Depends(require_key)])
def validate(request: ValidationRequest):
    reason = get_failure_reason({"final_output": request.text})
    if reason == "NONE":
        return {"passed": True, "risk": "LOW", "issues": []}
    return {"passed": False, "risk": "HIGH", "issues": [reason]}


@app.post("/pipeline", dependencies=[Depends(require_key)])
def pipeline(req: PipelineRequest):
    """Run one input through the four-agent chain with fault injection."""
    injector = ErrorInjector(req.inject_probability, req.seed)
    guard = CascadeGuard(req.mode, req.max_retries) if req.protected else None
    return run_chain(req.numbers, 0, injector, guard)


@app.post("/experiment", dependencies=[Depends(require_key)])
def experiment(req: ExperimentRequest):
    """Paired baseline vs protected experiment on identical seeded trials."""
    max_trials = int(os.getenv("MAX_TRIALS", "2000"))
    if req.trials > max_trials:
        raise HTTPException(status_code=422, detail=f"trials must be <= {max_trials}")
    return run_experiment(req.trials, req.seed, req.inject_probability, req.max_retries, req.mode)


# ---------------------------------------------------------------- LLM evaluation
class LLMRunRequest(BaseModel):
    agent_trials: int = Field(40, ge=0, le=2000)
    validator_trials: int = Field(15, ge=0, le=500, description="per cell")
    e2e_trials: int = Field(40, ge=0, le=2000)
    hard: bool = False
    retries: int = Field(2, ge=0, le=5)
    inject: float = Field(0.0, ge=0, le=1)
    seed: int = 42
    workers: int = Field(4, ge=1, le=8)
    repeats: int = Field(1, ge=1, le=10, description="run the whole evaluation this many times and combine")
    parts: list[Literal["agents", "validator", "e2e"]] = list(PARTS)
    simulated: bool = True
    confirm: bool = False


def _llm_cfg(req: LLMRunRequest) -> dict:
    return req.model_dump(exclude={"simulated", "confirm"})


@app.get("/llm/status", dependencies=[Depends(require_key)])
def llm_status():
    return {
        "real_available": bool(os.getenv("ANTHROPIC_API_KEY")),
        "auth_enabled": bool(os.getenv("API_KEY")),
        "model": os.getenv("CG_MODEL", DEFAULT_MODEL),
        "max_real_calls": int(os.getenv("LLM_MAX_CALLS", "300")),
    }


@app.post("/llm/plan", dependencies=[Depends(require_key)])
def llm_plan_endpoint(req: LLMRunRequest):
    """How many model calls and tokens a run would use. Spends nothing."""
    return llm_plan(_llm_cfg(req))


@app.post("/llm/run", dependencies=[Depends(require_key)])
def llm_run(req: LLMRunRequest):
    """Start an evaluation in the background. Poll /llm/jobs/{id}.
    Real-model runs spend money, so they need: ANTHROPIC_API_KEY on the server, API_KEY auth
    switched on, confirm=true, and a plan within LLM_MAX_CALLS."""
    cfg = _llm_cfg(req)
    pl = llm_plan(cfg)
    if pl["total_max"] == 0:
        raise HTTPException(status_code=422, detail="nothing to run: all trial counts are zero")
    if req.simulated:
        if pl["total_max"] > 50_000:
            raise HTTPException(status_code=422, detail="simulated plan too large (max 50000 calls)")
    else:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise HTTPException(status_code=422, detail="real runs need ANTHROPIC_API_KEY set on the server")
        if not os.getenv("API_KEY"):
            raise HTTPException(status_code=403, detail="real runs need API_KEY auth switched on, so strangers cannot spend your money")
        if not req.confirm:
            raise HTTPException(status_code=422, detail=f"set confirm=true to spend up to {pl['total_max']} real API calls")
        limit = int(os.getenv("LLM_MAX_CALLS", "300"))
        if pl["total_max"] > limit:
            raise HTTPException(status_code=422, detail=f"plan has up to {pl['total_max']} calls; the server limit is {limit} (LLM_MAX_CALLS)")
        if llm_jobs.real_running():
            raise HTTPException(status_code=409, detail="a real run is already in progress")
    return {"job_id": llm_jobs.submit(cfg, req.simulated), "planned_max_calls": pl["total_max"]}


@app.get("/llm/jobs/{job_id}", dependencies=[Depends(require_key)])
def llm_job(job_id: str):
    job = llm_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return job
