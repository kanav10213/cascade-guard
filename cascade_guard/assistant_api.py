"""HTTP routes for the assistant ("Ask Cascade Guard").

    GET  /assistant        the chat page
    GET  /assistant/info   what this key can use: built-in only, or the connected model too (viewer)
    POST /assistant/ask    answer one message (viewer); a PDF invoice can be attached as base64

The built-in assistant is free and open to any key. Letting the model read the request may cost money, so it
needs a configured (non-simulated) model and a key of at least ASSISTANT_MODEL_ROLE (admin by default; set it to
viewer for a free model), and shares the per-minute limit of the model routes.
"""
import base64
import binascii
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from cascade_guard.assistant import EXAMPLE_GROUPS, EXAMPLES, ask, model_role
from cascade_guard.auth import RANK, ensure, require

router = APIRouter(tags=["assistant"])
PAGE = Path(__file__).parent / "static" / "assistant.html"


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field("", max_length=4000)
    kind: str | None = Field(None, max_length=40)
    example: int | None = None


class AskRequest(BaseModel):
    message: str = Field("", max_length=20_000)
    history: list[HistoryItem] = Field(default_factory=list, max_length=12)
    use_model: bool | None = Field(None, description="None: use the model when one is connected and the key is admin")
    pdf_base64: str | None = Field(None, max_length=7_000_000, description="a PDF invoice, base64-encoded")
    pdf_name: str | None = Field(None, max_length=200)


@router.api_route("/assistant", methods=["GET", "HEAD"], include_in_schema=False)
def page():
    return FileResponse(PAGE)


def _model_client():
    from cascade_guard.llm_api import get_client
    client = get_client(required=False)
    return client if client is not None and not client.is_fake else None, client


@router.get("/assistant/info")
def info(role: str = Depends(require("viewer"))):
    real, client = _model_client()
    model = {"configured": client is not None, "simulated": bool(client and client.is_fake)}
    if client is not None:
        model.update(provider=client.provider, model=client.model)
    from cascade_guard import connector
    allowed = RANK[role] >= RANK[model_role()]
    return {"role": role, "examples": EXAMPLES, "example_groups": EXAMPLE_GROUPS, "model": model,
            "model_available": real is not None and allowed,
            "model_needs_admin": real is not None and not allowed, "model_role": model_role(),
            "connector": {"enabled": connector.status()["enabled"]}}


@router.post("/assistant/ask")
def ask_route(req: AskRequest, role: str = Depends(require("viewer")),
              req_key: str = Header(default="", alias="X-API-Key")):
    message = req.message.strip()
    extracted = None
    if req.pdf_base64:
        from cascade_guard.pilot.pdf import PDFError, pdf_to_text
        try:
            data = base64.b64decode(req.pdf_base64.split(",", 1)[-1], validate=False)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=422, detail="The attached file is not valid base64.") from None
        try:
            extracted = pdf_to_text(data)[:20_000]
        except PDFError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        message = (message + "\n\n" if message else "Check this bill:\n") + extracted
    if not message:
        raise HTTPException(status_code=422, detail="Type a message or attach a PDF.")
    real, _ = _model_client()
    allowed = RANK[role] >= RANK[model_role()]
    use_model = (real is not None and allowed) if req.use_model is None else req.use_model
    if use_model:
        if real is None:
            raise HTTPException(status_code=409, detail="No AI model is connected to this server, so only the "
                                                        "built-in assistant can answer. Ask without use_model.")
        ensure(role, model_role())
        from cascade_guard.pilot_api import _llm_rate_limit
        _llm_rate_limit(req_key)
    history = [h.model_dump() for h in req.history]
    result = ask(message, history, client=real, use_model=use_model)
    if extracted is not None:
        result["extracted_text"] = extracted
        result["pdf_name"] = req.pdf_name
    return result
