"""HTTP routes for the expense desk: process receipts through the guarded three-agent workflow.

    GET  /receipts           the page: drop receipts, see what was read and checked, export a CSV
    GET  /receipts/info      what this server can do (photos, the AI model)
    POST /receipts/process   1 to 50 receipts (text, a PDF or a photo, base64); returns every result, a summary and
                             a CSV. A receipt the guard stops goes to the review queue for a person.

Rule agents are free and open to any key. LLM agents use the configured model and need a key of at least
ASSISTANT_MODEL_ROLE (as on /assistant); a model batch is limited to 10 receipts.
"""
from __future__ import annotations

import base64
import binascii
import csv
import io
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from cascade_guard.auth import RANK, ensure, require
from cascade_guard.receipts import APPROVAL_LIMIT, CATEGORIES, Batch, OCRUnavailable, ocr_available, process_receipt, text_from_image

router = APIRouter(tags=["receipts"])
PAGE = Path(__file__).parent / "static" / "receipts.html"
MAX_ITEMS, MAX_MODEL_ITEMS = 50, 10


class ReceiptIn(BaseModel):
    name: str = Field("receipt", max_length=200)
    text: str | None = Field(None, max_length=20_000)
    pdf_base64: str | None = Field(None, max_length=7_000_000)
    image_base64: str | None = Field(None, max_length=11_000_000)


class ProcessRequest(BaseModel):
    receipts: list[ReceiptIn] = Field(..., min_length=1, max_length=MAX_ITEMS)
    use_model: bool | None = Field(None, description="None: use the model when one is connected and the key may")
    save: bool = Field(True, description="save each run; stopped receipts go to the review queue")


@router.api_route("/receipts", methods=["GET", "HEAD"], include_in_schema=False)
def page():
    return FileResponse(PAGE)


def _model():
    from cascade_guard.llm_api import get_client
    client = get_client(required=False)
    return client if client is not None and not client.is_fake else None


@router.get("/receipts/info")
def info(role: str = Depends(require("viewer"))):
    from cascade_guard.assistant import model_role
    real = _model()
    return {"role": role, "photos": ocr_available(), "model": None if real is None else real.model,
            "model_available": real is not None and RANK[role] >= RANK[model_role()],
            "approval_limit": str(APPROVAL_LIMIT), "categories": list(CATEGORIES),
            "max_receipts": MAX_ITEMS, "max_model_receipts": MAX_MODEL_ITEMS}


def _decode(b64: str, what: str) -> bytes:
    try:
        return base64.b64decode(b64.split(",", 1)[-1], validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail=f"The {what} is not valid base64.") from None


def _text_of(item: ReceiptIn) -> tuple[str | None, str | None]:
    """(text, problem) for one uploaded receipt."""
    if item.text and item.text.strip():
        return item.text, None
    if item.pdf_base64:
        from cascade_guard.pilot.pdf import PDFError, pdf_to_text
        try:
            return pdf_to_text(_decode(item.pdf_base64, "PDF"))[:20_000], None
        except PDFError as e:
            return None, str(e)
    if item.image_base64:
        try:
            return text_from_image(_decode(item.image_base64, "image"))[:20_000], None
        except OCRUnavailable as e:
            return None, f"{e}. Paste the receipt's text or attach a PDF instead."
        except ValueError as e:
            return None, str(e)
    return None, "empty: paste the receipt's text, or attach a PDF or a photo"


def _issue(result: dict) -> str:
    bad = [t for t in result["trace"] if not t["passed"]]
    if result["status"] == "halted" and bad:
        return bad[-1]["reason"] or "did not pass the check"
    if bad:
        return "fixed after: " + (bad[0]["reason"] or "")
    return ""


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["file", "status", "vendor", "date", "amount", "currency", "category", "decision", "issue", "run_id"])
    for r in rows:
        e, f, d = r.get("entry") or {}, r.get("fields") or {}, r.get("decision") or {}
        w.writerow([r["name"], r["status"], e.get("vendor") or f.get("vendor") or "", e.get("date") or f.get("date") or "",
                    e.get("amount") or f.get("total") or "", e.get("currency") or f.get("currency") or "",
                    e.get("category") or "", d.get("decision") or "", r.get("issue") or r.get("problem") or "",
                    r.get("run_id") or ""])
    return buf.getvalue()


@router.post("/receipts/process")
def process(req: ProcessRequest, role: str = Depends(require("viewer")),
            req_key: str = Header(default="", alias="X-API-Key")):
    from cascade_guard.assistant import model_role
    real = _model()
    allowed = real is not None and RANK[role] >= RANK[model_role()]
    use_model = allowed if req.use_model is None else req.use_model
    if use_model:
        if real is None:
            raise HTTPException(status_code=409, detail="No AI model is connected; use the rule agents (use_model "
                                                        "false).")
        ensure(role, model_role())
        if len(req.receipts) > MAX_MODEL_ITEMS:
            raise HTTPException(status_code=422, detail=f"With the AI model, send at most {MAX_MODEL_ITEMS} receipts "
                                                        f"at a time.")
        from cascade_guard.pilot_api import _llm_rate_limit
        _llm_rate_limit(req_key)
    from cascade_guard.llm.client import BudgetExceeded, LLMCallError
    from cascade_guard.storage.db import get_store
    store = get_store() if req.save else None
    batch = Batch()
    rows: list[dict] = []
    for item in req.receipts:
        text, problem = _text_of(item)
        if text is None:
            rows.append({"name": item.name, "status": "unreadable", "problem": problem})
            continue
        try:
            res = process_receipt(text, client=real if use_model else None, batch=batch)
        except (LLMCallError, BudgetExceeded) as e:
            rows.append({"name": item.name, "status": "unreadable", "problem": f"the AI model is not available: {e}"})
            continue
        row = {"name": item.name, **res, "issue": _issue(res), "text": text[:4000]}
        if store is not None:
            row["run_id"] = _save(store, res, text, real if use_model else None)
        rows.append(row)
    summary: dict = {k: sum(1 for r in rows if r["status"] == k) for k in ("posted", "halted", "unreadable")}
    summary["caught"] = sum(r.get("caught", 0) for r in rows)
    summary["fixed"] = sum(1 for r in rows if r["status"] == "posted" and r.get("caught"))
    summary["total_posted"] = f"{sum(float((r.get('entry') or {}).get('amount') or 0) for r in rows if r['status'] == 'posted'):.2f}"
    return {"results": rows, "summary": summary, "csv": to_csv(rows),
            "agents": f"AI model {real.model}" if use_model and real is not None else "rule agents"}


def _save(store, res: dict, text: str, client) -> int | None:
    import json
    trace = [{"stage": t["step"], "attempt": t["attempt"], "guard_issue": None if t["passed"] else t["reason"],
              "agent_ok": None, "output_ok": t["passed"], "output": t["output"]} for t in res["trace"]]
    run = {"status": "completed" if res["status"] == "posted" else "halted",
           "final_output": json.dumps(res.get("entry"), default=str) if res.get("entry") else None,
           "detected": res["caught"], "recovered": int(res["status"] == "posted" and res["caught"] > 0),
           "trace": trace}
    model = {"provider": client.provider, "model": client.model} if client is not None else {"provider": "rules"}
    try:
        return store.save_pipeline_run(run, text, guard="receipts", engine="receipts", model=model, kind="receipt",
                                       usage_delta=None, prompt="receipt")
    except Exception:  # saving must never lose the person's results
        return None
