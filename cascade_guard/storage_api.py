"""HTTP routes for stored runs and the human review queue.

    GET  /runs                      latest runs (pipeline and verify), newest first
    GET  /runs/{id}                 one run with every step and every checker verdict
    GET  /review-queue              jobs the guard stopped, or verify checks that did not pass
    POST /review-queue/{id}/resolve a person records the outcome: approved | rejected | fixed
    GET  /usage                     calls and cost across server restarts, by day
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from cascade_guard.auth import require
from cascade_guard.storage.db import get_store

router = APIRouter(tags=["storage"])


def _store():
    s = get_store()
    if s is None:
        raise HTTPException(status_code=503, detail="Storage is off (CASCADE_DB=off).")
    return s


class Resolve(BaseModel):
    resolution: Literal["approved", "rejected", "fixed"]
    note: str = Field("", max_length=2000)
    by: str = Field("", max_length=200)


@router.get("/runs", dependencies=[Depends(require("viewer"))])
def runs(limit: int = Query(50, ge=1, le=500), kind: Literal["pipeline", "verify", "pilot"] | None = None):
    return _store().list_runs(limit, kind)


@router.get("/runs/{run_id}", dependencies=[Depends(require("viewer"))])
def run(run_id: int):
    r = _store().get_run(run_id)
    if r is None:
        raise HTTPException(status_code=404, detail="no such run")
    return r


@router.get("/review-queue", dependencies=[Depends(require("viewer"))])
def review_queue(state: Literal["open", "resolved"] = "open", limit: int = Query(50, ge=1, le=500)):
    return _store().reviews(state, limit)


@router.post("/review-queue/{item_id}/resolve")
def resolve(item_id: int, body: Resolve, role: str = Depends(require("reviewer"))):
    """Needs a reviewer or admin key."""
    try:
        # the role comes from the key; the free-text name is kept beside it, never instead of it
        who = f"{body.by} ({role})" if body.by else role
        return _store().resolve_review(item_id, body.resolution, body.note, who)
    except KeyError:
        raise HTTPException(status_code=404, detail="no such review item") from None
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/usage", dependencies=[Depends(require("viewer"))])
def usage():
    return _store().usage_summary()
