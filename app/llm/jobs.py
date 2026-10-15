"""Background runner so long (real) evaluations do not block an HTTP request."""
import threading
import time
import uuid

from app.llm.client import get_client
from app.llm.experiments import plan, run_repeated

_JOBS: dict = {}
_LOCK = threading.Lock()
MAX_KEPT = 20


def real_running() -> bool:
    with _LOCK:
        return any(j["status"] == "running" and not j["simulated"] for j in _JOBS.values())


def submit(cfg: dict, simulated: bool) -> str:
    client = get_client(simulated=simulated, seed=cfg.get("seed", 0))
    p = plan(cfg)
    job = {"id": uuid.uuid4().hex[:12], "status": "running", "simulated": simulated, "started": time.time(),
           "planned_max": p["total_max"], "client": client, "report": None, "error": None}

    def work():
        try:
            job["report"] = run_repeated(client, cfg)
            job["status"] = "done"
        except Exception as e:  # report any failure to the poller
            job["error"] = f"{type(e).__name__}: {e}"
            job["status"] = "error"

    with _LOCK:
        _JOBS[job["id"]] = job
        for old in sorted(_JOBS.values(), key=lambda j: j["started"])[:-MAX_KEPT]:
            _JOBS.pop(old["id"], None)
    threading.Thread(target=work, daemon=True).start()
    return job["id"]


def get(job_id: str):
    with _LOCK:
        job = _JOBS.get(job_id)
    if not job:
        return None
    return {"id": job["id"], "status": job["status"], "simulated": job["simulated"],
            "calls_done": job["client"].usage.calls, "planned_max": job["planned_max"],
            "elapsed_s": round(time.time() - job["started"], 1), "error": job["error"], "report": job["report"]}
