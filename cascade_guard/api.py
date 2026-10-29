import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from cascade_guard import __version__
from cascade_guard.config import load_env

load_env()  # settings from .env (API keys, model) before anything reads them

from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from pydantic import BaseModel, Field, field_validator  # noqa: E402

from cascade_guard.agents.pipeline import parse_numbers  # noqa: E402
from cascade_guard.evaluation.error_injector import ErrorInjector  # noqa: E402
from cascade_guard.evaluation.judgement import get_failure_reason  # noqa: E402
from cascade_guard.evaluation.runner import run_chain, run_experiment  # noqa: E402
from cascade_guard.guard.cascade_guard import CascadeGuard  # noqa: E402


@asynccontextmanager
async def lifespan(_app):
    from cascade_guard import connector
    async with connector.lifespan():  # the Claude web connector, when MCP_CONNECTOR_TOKEN is set
        yield


app = FastAPI(
    title="Cascade Guard",
    version=__version__,
    description="Detect, halt and recover from error cascades in a multi-agent chain.",
    lifespan=lifespan,
)


from cascade_guard.auth import auth_enabled, current_role, require  # noqa: E402

# Every route except /health and /health/ready needs a valid key when keys are set (see cascade_guard/auth.py).
require_key = require("viewer")


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
    engine: Literal["custom", "langgraph"] = "custom"
    repair: Literal["blind", "guided"] = "blind"
    sticky: float = Field(0.0, ge=0, le=1, description="share of faults that repeat on a blind retry")

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
    engine: Literal["custom", "langgraph"] = "custom"
    sticky: float = Field(0.0, ge=0, le=1, description="> 0: some faults repeat on retry; adds a guided-repair arm")


STATIC = Path(__file__).parent / "static"


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def dashboard():
    return FileResponse(STATIC / "index.html")


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok", "version": __version__}


@app.post("/validate", dependencies=[Depends(require_key)])
def validate(request: ValidationRequest):
    reason = get_failure_reason({"final_output": request.text})
    if reason == "NONE":
        return {"passed": True, "risk": "LOW", "issues": []}
    return {"passed": False, "risk": "HIGH", "issues": [reason]}


@app.post("/pipeline", dependencies=[Depends(require_key)])
def pipeline(req: PipelineRequest):
    """Run one input through the four-agent chain with fault injection."""
    injector = ErrorInjector(req.inject_probability, req.seed, sticky=req.sticky)
    guard = CascadeGuard(req.mode, req.max_retries) if req.protected else None
    if req.engine == "langgraph":
        from cascade_guard.orchestration.langgraph_chain import run_chain_langgraph
        return run_chain_langgraph(req.numbers, 0, injector, guard, repair=req.repair)
    return run_chain(req.numbers, 0, injector, guard, repair=req.repair)


@app.post("/experiment", dependencies=[Depends(require_key)])
def experiment(req: ExperimentRequest):
    """Paired baseline vs protected experiment on identical seeded trials."""
    max_trials = int(os.getenv("MAX_TRIALS", "2000"))
    if req.trials > max_trials:
        raise HTTPException(status_code=422, detail=f"trials must be <= {max_trials}")
    return run_experiment(req.trials, req.seed, req.inject_probability, req.max_retries, req.mode, req.engine,
                          req.sticky)


from cascade_guard.llm_api import router as llm_router  # noqa: E402

app.include_router(llm_router)

from cascade_guard.eval_api import router as eval_router  # noqa: E402

app.include_router(eval_router)

from cascade_guard.chains_api import router as chains_router  # noqa: E402

app.include_router(chains_router)

from cascade_guard.storage_api import router as storage_router  # noqa: E402

app.include_router(storage_router)

from cascade_guard.pilot_api import router as pilot_router  # noqa: E402

app.include_router(pilot_router)

from cascade_guard.assistant_api import router as assistant_router  # noqa: E402

app.include_router(assistant_router)


from cascade_guard import ops  # noqa: E402

ops.install(app)
app.include_router(ops.router)

from cascade_guard.connector import ConnectorMiddleware  # noqa: E402

app.add_middleware(ConnectorMiddleware)  # added last, so it runs first: /mcp/<secret> never reaches the routes


@app.get("/connector", dependencies=[Depends(require("admin"))])
def connector_info():
    """The Claude web connector: on or off, its role, and the link to paste into Claude (admin only)."""
    from cascade_guard import connector
    out = connector.status()
    out["path"] = connector.PREFIX + connector.token() if out["enabled"] else None
    return out


@app.get("/whoami")
def whoami(role: str = Depends(current_role)):
    """The caller's role (admin, reviewer or viewer) and whether keys are switched on. 401 for a wrong key."""
    return {"role": role, "auth_enabled": auth_enabled()}
