"""HTTP routes for the invoice pilot.

    GET  /pilot/sample?seed=7    a generated sample invoice
    GET  /pilot/sample.pdf       the same sample as a PDF
    POST /pilot/invoice          run one invoice through the guarded workflow (LangGraph)
    POST /pilot/invoice-pdf      the same, for a PDF (base64); returns the extracted text too
    POST /pilot/experiment       paired run on many generated invoices, no guard vs rule guard (free)
"""
import base64
import binascii
import hashlib
import os
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from cascade_guard.auth import ensure, require
from cascade_guard.llm_api import _call, _delta, get_client
from cascade_guard.pilot.invoice import make_invoice, run_invoice, run_pilot_experiment
from cascade_guard.pilot.pdf import PDFError, pdf_to_text, text_to_pdf
from cascade_guard.storage.db import get_store

router = APIRouter(prefix="/pilot", tags=["pilot"])


class InvoiceRequest(BaseModel):
    text: str = Field(..., min_length=10, max_length=20_000,
                      description="Invoice text: 'INVOICE <id>', lines 'Q x NAME @ $PRICE', 'Tax: R%'")
    guard: Literal["none", "rules", "llm"] = "rules"
    agents: Literal["rules", "llm"] = "rules"
    inject_probability: float = Field(0.0, ge=0, le=1)
    seed: int = 0
    max_retries: int = Field(2, ge=0, le=3)
    repair: Literal["blind", "guided"] = Field(
        "guided", description="On a retry, tell the agent why it was rejected (guided) or just ask again (blind)")
    sticky: float = Field(0.0, ge=0, le=1, description="share of injected errors that repeat on a blind retry")


class InvoicePDFRequest(BaseModel):
    pdf_base64: str = Field(..., min_length=10, max_length=7_000_000, description="The PDF file, base64-encoded")
    guard: Literal["none", "rules", "llm"] = "rules"
    agents: Literal["rules", "llm"] = "rules"
    inject_probability: float = Field(0.0, ge=0, le=1)
    seed: int = 0
    max_retries: int = Field(2, ge=0, le=3)
    repair: Literal["blind", "guided"] = "guided"
    sticky: float = Field(0.0, ge=0, le=1)


class PilotExperimentRequest(BaseModel):
    trials: int = Field(500, ge=1)
    seed: int = 42
    inject_probability: float = Field(0.1, ge=0, le=1)
    max_retries: int = Field(2, ge=0, le=5)
    sticky: float = Field(0.0, ge=0, le=1, description="> 0 adds a guided-repair arm")


@router.get("/sample", dependencies=[Depends(require("viewer"))])
def sample(seed: int = Query(7, ge=0, le=10_000_000)):
    return make_invoice(seed)


@router.get("/sample.pdf", dependencies=[Depends(require("viewer"))])
def sample_pdf(seed: int = Query(7, ge=0, le=10_000_000)):
    inv = make_invoice(seed)
    return Response(text_to_pdf(inv["text"], inv["id"]), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{inv["id"]}.pdf"'})


@router.post("/invoice-pdf")
def invoice_pdf(req: InvoicePDFRequest, role: str = Depends(require("viewer")),
                req_key: str = Header(default="", alias="X-API-Key")):
    """Read the text out of a PDF invoice, then run it like /pilot/invoice."""
    try:
        data = base64.b64decode(req.pdf_base64.split(",", 1)[-1], validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="pdf_base64 is not valid base64") from None
    try:
        text = pdf_to_text(data)
    except PDFError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    body = InvoiceRequest(text=text[:20_000], **req.model_dump(exclude={"pdf_base64"}))
    result = invoice(body, role, req_key)
    result["extracted_text"] = text
    return result


def _llm_rate_limit(key: str):
    """Routes that call the model share the stricter per-minute limit of /llm/*."""
    from cascade_guard.ops import _limit, limiter
    ok, wait = limiter.allow("key:" + hashlib.sha256((key or "").encode("utf-8", "surrogateescape")).hexdigest()[:12]
                             + ":llm", _limit("LLM_RATE_LIMIT_PER_MIN", 20))
    if not ok:
        raise HTTPException(status_code=429, detail=f"too many requests; try again in {wait} s",
                            headers={"Retry-After": str(wait)})


@router.post("/invoice")
def invoice(req: InvoiceRequest, role: str = Depends(require("viewer")),
            req_key: str = Header(default="", alias="X-API-Key")):
    """Rule-based runs are free and open to any key; anything that calls the model needs an admin key."""
    uses_llm = req.guard == "llm" or req.agents == "llm"
    client = None
    if uses_llm:
        ensure(role, "admin")
        _llm_rate_limit(req_key)
        client = get_client()
    before = client.usage.as_dict() if client else None
    try:
        result = _call(lambda: run_invoice(req.text, req.guard, req.agents, client, req.inject_probability,
                                           req.seed, req.max_retries, req.repair, req.sticky))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    model = ({"provider": client.provider, "model": client.model, "simulated": client.is_fake}
             if client else {"provider": "rules", "model": None, "simulated": False})
    result.update(guard=req.guard, agents=req.agents, engine="langgraph", model=model, repair=req.repair)
    if client:
        result["usage"] = client.usage.as_dict()
    store = get_store()
    if store is not None:
        result["run_id"] = store.save_pipeline_run(
            result, req.text, guard=req.guard, engine="langgraph", model=model, kind="pilot",
            usage_delta=_delta(before, client.usage.as_dict()) if client and before else None,
            validator_log=result["validator_log"], prompt="recompute" if req.guard == "llm" else req.guard)
    return result


@router.post("/experiment", dependencies=[Depends(require("viewer"))])
def experiment(req: PilotExperimentRequest):
    max_trials = int(os.getenv("MAX_TRIALS", "2000"))
    if req.trials > max_trials:
        raise HTTPException(status_code=422, detail=f"trials must be <= {max_trials}")
    return run_pilot_experiment(req.trials, req.seed, req.inject_probability, req.max_retries, req.sticky)
