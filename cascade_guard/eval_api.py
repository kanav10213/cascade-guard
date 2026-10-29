"""Connection check and the model evaluation, from the API and the dashboard.

    POST /llm/check             one tiny model call: do the key, model name and reply format work? (admin)
    POST /llm/eval/plan         calls and estimated cost of an evaluation; spends nothing (viewer)
    POST /llm/eval/run          start an evaluation in the background; returns a job id
                                (simulated: viewer; real model: admin, confirm=true, within the cost cap)
    GET  /llm/eval/jobs         recent jobs (viewer)
    GET  /llm/eval/jobs/{id}    status, and the full report and Markdown summary when done (viewer)

The same code as `cascade-guard eval` runs the job, so the results are identical, and they are
also written to results/ in the working folder. Jobs live in this server process.
"""
import os
import threading
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from cascade_guard.auth import ensure, require

router = APIRouter(prefix="/llm", tags=["llm"])

_JOBS: dict = {}
_LOCK = threading.Lock()
MAX_KEPT = 20
MAX_SIMULATED_RUNNING = 2


def _max_cost() -> float:
    return float(os.getenv("LLM_EVAL_MAX_COST", "5.0"))


class EvalRequest(BaseModel):
    command: Literal["agents", "detect", "chain", "all"] = "all"
    seeds: list[int] = Field(default_factory=lambda: [1, 2, 3], min_length=1, max_length=10,
                             description="each seed is one independent repeat; results are pooled")
    n: int = Field(50, ge=1, le=500, description="items per seed")
    min_len: int = Field(5, ge=2, le=100)
    max_len: int = Field(30, ge=2, le=200)
    simulated: bool = Field(True, description="true: offline simulator, no cost; false: the configured model")
    max_cost: float = Field(5.0, gt=0, description="hard spend cap for this run, USD (capped by LLM_EVAL_MAX_COST)")
    confirm: bool = Field(False, description="must be true to spend money on a real-model run")


def _args(req: EvalRequest):
    from cascade_guard.experiments.llm_eval import parse_args
    if req.max_len < req.min_len:
        raise HTTPException(status_code=422, detail="max_len must be at least min_len")
    argv = [req.command, "--seeds", *map(str, req.seeds), "--n", str(req.n), "--min-len", str(req.min_len),
            "--max-len", str(req.max_len), "--max-cost", str(min(req.max_cost, _max_cost())), "--yes"]
    return parse_args(argv + (["--fake"] if req.simulated else []))


def _plan(req: EvalRequest, plan_only: bool):
    from cascade_guard.experiments.llm_eval import eval_client, make_plan
    from cascade_guard.llm.client import NotConfigured, UnknownPrice
    args = _args(req)
    try:
        client = eval_client(args, plan_only=plan_only)
    except (NotConfigured, UnknownPrice, RuntimeError) as e:
        raise HTTPException(status_code=503, detail=f"No model configured for a real run: {e}") from e
    return args, client, make_plan(args, client)


def _public(job: dict, full: bool) -> dict:
    out = {k: job[k] for k in ("id", "status", "part", "simulated", "model", "command", "started", "finished",
                               "planned", "error")}
    if full and job["status"] == "done":
        out["report"], out["markdown"] = job["report"], job["markdown"]
    if job.get("client") is not None:
        out["usage"] = job["client"].usage.as_dict()
    return out


@router.post("/check", dependencies=[Depends(require("admin"))])
def check():
    """One tiny call to the configured model (a fraction of a cent): key, model name and reply format."""
    from cascade_guard.experiments.check import connection_check
    from cascade_guard.llm_api import get_client
    client = get_client(required=False)
    if client is None:
        from cascade_guard import llm_api
        return {"ok": False, "stage": "settings", "hint": f"No model configured: {llm_api._client_error}. "
                "Put the key in .env (cascade-guard init creates it) and restart."}
    return connection_check(client)


@router.post("/eval/plan", dependencies=[Depends(require("viewer"))])
def plan(req: EvalRequest):
    """How many calls, how many tokens and what it would cost. Spends nothing and needs no key."""
    _, client, summary = _plan(req, plan_only=True)
    return {"model": client.model, "simulated": bool(client.is_fake), "plan": summary,
            "max_cost_usd": min(req.max_cost, _max_cost())}


@router.post("/eval/run")
def run(req: EvalRequest, role: str = Depends(require("viewer")),
        req_key: str = Header(default="", alias="X-API-Key")):
    """Start an evaluation in the background. Poll GET /llm/eval/jobs/{id}."""
    if not req.simulated:
        ensure(role, "admin")
        from cascade_guard.pilot_api import _llm_rate_limit
        _llm_rate_limit(req_key)
    args, client, summary = _plan(req, plan_only=False)
    cap = min(req.max_cost, _max_cost())
    if not client.is_fake:
        if not req.confirm:
            raise HTTPException(status_code=422, detail=f"This run calls {client.model} and is estimated at "
                                f"${summary['est_cost_usd']:.2f} (worst case ${summary['worst_case_cost_usd']:.2f}). "
                                "Send confirm=true to start it.")
        if summary["est_cost_usd"] > cap:
            raise HTTPException(status_code=422, detail=f"Estimated ${summary['est_cost_usd']:.2f} is above the cap "
                                f"${cap:.2f}. Use fewer seeds or items, or raise max_cost / LLM_EVAL_MAX_COST.")
    with _LOCK:
        running = [j for j in _JOBS.values() if j["status"] == "running"]
        if not client.is_fake and any(not j["simulated"] for j in running):
            raise HTTPException(status_code=409, detail="a real-model evaluation is already running")
        if client.is_fake and sum(j["simulated"] for j in running) >= MAX_SIMULATED_RUNNING:
            raise HTTPException(status_code=409, detail="two simulated evaluations are already running; wait for one")
        job = {"id": uuid.uuid4().hex[:12], "status": "running", "part": None, "simulated": bool(client.is_fake),
               "model": client.model, "command": req.command, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
               "finished": None, "planned": summary, "error": None, "client": client,
               "report": None, "markdown": None}
        _JOBS[job["id"]] = job
        for old in sorted(_JOBS.values(), key=lambda j: j["started"])[:-MAX_KEPT]:
            if old["status"] != "running":
                _JOBS.pop(old["id"], None)

    def work():
        from cascade_guard.experiments.llm_eval import run_evaluation, write_results
        try:
            report, md, status = run_evaluation(args, client, summary, progress=lambda c: job.update(part=c))
            write_results(args, client, report, md)
            job.update(report=report, markdown="\n".join(md), status="done" if status in (0, 3, 4) else "error")
            if status in (3, 4):
                job["error"] = report.get("status")
        except Exception as e:  # report any failure to the poller
            job.update(status="error", error=f"{type(e).__name__}: {e}")
        finally:
            job["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")

    threading.Thread(target=work, daemon=True, name=f"eval-{job['id']}").start()
    return {"job_id": job["id"], "simulated": job["simulated"], "model": job["model"], "plan": summary}


@router.get("/eval/jobs", dependencies=[Depends(require("viewer"))])
def jobs():
    with _LOCK:
        return [_public(j, full=False) for j in sorted(_JOBS.values(), key=lambda j: j["started"], reverse=True)]


@router.get("/eval/jobs/{job_id}", dependencies=[Depends(require("viewer"))])
def job(job_id: str):
    with _LOCK:
        j = _JOBS.get(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="no such job (jobs are kept in memory by this server process)")
    return _public(j, full=True)
