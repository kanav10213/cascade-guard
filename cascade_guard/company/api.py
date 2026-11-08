"""HTTP routes of the company product: the chat, the documents, keys and usage, and an OpenAI-format API.

    GET  /chat                     the chat page          GET /admin   keys, usage, documents (owner, managers)
    GET  /chat/info                who you are, the AI, your documents and usage
    POST /chat/ask                 {question, history, mode} -> a checked answer with its sources
    GET  /kb/docs                  the workspace's documents
    POST /kb/docs                  add one: {name, text} or {name, file_base64} (manager)
    DELETE /kb/docs/{id}           remove one (manager)
    GET  /kb/search?q=...          the passages a question would use
    GET  /me                       your key, plan and this month's usage
    GET/POST/DELETE /admin/keys    customer keys (owner: any workspace; manager: member keys in their own)
    GET  /admin/usage              answers, AI calls and tokens by workspace and month

    GET  /v1/models                \\ OpenAI-format API: point any OpenAI-compatible app (Open WebUI, LibreChat, your
    POST /v1/chat/completions      / own code with the openai package) here and it gets checked answers.
                                     Models: cascade-guard (documents first, then general), cascade-guard-docs
                                     (documents only), cascade-guard-general. stream: true is supported.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from cascade_guard.company import keys as K
from cascade_guard.company.answer import answer, footer
from cascade_guard.company.keys import Principal, caller, need

router = APIRouter(tags=["company"])
STATIC = Path(__file__).resolve().parent.parent / "static"
MODELS = {"cascade-guard": "auto", "cascade-guard-docs": "documents", "cascade-guard-general": "general"}
SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "company"
SAMPLE_QUESTIONS = [
    "How many days of paid leave do we get, and how many can be carried forward?",
    "I'm a manager travelling to Pune for 3 nights. What is my hotel limit and daily allowance?",
    "Who has to approve an expense of ₹20,000?",
    "What is the warranty on the angle grinder, and how do I get it repaired?",
    "Can I paste a customer drawing into a public AI chat tool?",
    "What is the notice period for a manager after confirmation?",
]


def load_samples(workspace: str, by: str = "") -> list[dict]:
    from cascade_guard.company import kb
    return [kb.add_document(workspace, f.name, f.read_bytes(), by=by) for f in sorted(SAMPLES.iterdir())
            if f.is_file() and f.name != "README.md"]


def _client():
    from cascade_guard.llm_api import get_client
    c = get_client(required=False)
    return None if c is None or c.is_fake else c


# ------------------------------------------------------------------ pages

@router.api_route("/chat", methods=["GET", "HEAD"], include_in_schema=False)
def chat_page():
    return FileResponse(STATIC / "chat.html")


@router.api_route("/admin", methods=["GET", "HEAD"], include_in_schema=False)
def admin_page():
    return FileResponse(STATIC / "admin.html")


# ------------------------------------------------------------------ chat

class Turn(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field("", max_length=20_000)


class AskIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    history: list[Turn] = Field(default_factory=list, max_length=40)
    mode: Literal["auto", "documents", "general"] = "auto"


def _info(p: Principal) -> dict:
    from cascade_guard.company import kb
    c = _client()
    docs = kb.list_documents(p.workspace)
    return {"you": p.public(), "ai": {"provider": c.provider, "model": c.model} if c else None,
            "documents": len(docs), "passages": sum(int(d["passages"]) for d in docs),
            "used_this_month": K.used(p), "company": os.getenv("COMPANY_NAME", ""),
            "docs_only": os.getenv("CHAT_DOCS_ONLY", "").lower() in ("1", "on", "yes", "true"),
            "can_manage_documents": p.at_least("manager"), "can_manage_keys": p.at_least("manager"),
            "is_owner": p.role == "owner",
            "sample_questions": SAMPLE_QUESTIONS if any(d["name"] == "employee-handbook.md" for d in docs) else []}


@router.get("/chat/info")
def chat_info(p: Principal = Depends(caller)):
    return _info(p)


def _answer_for(p: Principal, question: str, history: list[dict], mode: str) -> dict:
    K.check_quota(p)
    res = answer(question, workspace=p.workspace, client=_client(), history=history, mode=mode)
    K.record(p, ai_calls=res.get("usage", {}).get("ai_calls", 0), tokens=res.get("usage", {}).get("tokens", 0))
    return res


@router.post("/chat/ask")
def chat_ask(req: AskIn, p: Principal = Depends(caller)):
    return _answer_for(p, req.question, [t.model_dump() for t in req.history if t.role != "system"], req.mode)


# ------------------------------------------------------------------ documents

class DocIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    text: str | None = Field(None, max_length=5_000_000)
    file_base64: str | None = Field(None, max_length=28_000_000)


@router.get("/kb/docs")
def kb_list(p: Principal = Depends(caller)):
    from cascade_guard.company import kb
    return {"workspace": p.workspace, "documents": kb.list_documents(p.workspace)}


@router.post("/kb/docs")
def kb_add(doc: DocIn, p: Principal = Depends(caller)):
    from cascade_guard.company import kb
    need(p, "manager")
    if doc.text is not None:
        data = doc.text.encode("utf-8")
    elif doc.file_base64:
        try:
            data = base64.b64decode(doc.file_base64.split(",", 1)[-1], validate=False)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=422, detail="file_base64 is not valid base64") from None
    else:
        raise HTTPException(status_code=422, detail="send text or file_base64")
    try:
        return kb.add_document(p.workspace, doc.name, data, by=p.name)
    except kb.DocumentError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


@router.post("/kb/samples")
def kb_samples(p: Principal = Depends(caller)):
    """Load the made-up sample company's documents into your workspace, to try the chat."""
    need(p, "manager")
    return {"added": load_samples(p.workspace, by=p.name)}


@router.delete("/kb/docs/{doc_id}")
def kb_delete(doc_id: int, p: Principal = Depends(caller)):
    from cascade_guard.company import kb
    need(p, "manager")
    if not kb.delete_document(p.workspace, doc_id):
        raise HTTPException(status_code=404, detail="no such document in your workspace")
    return {"deleted": doc_id}


@router.get("/kb/search")
def kb_search(q: str = Query(..., min_length=1, max_length=1000), k: int = Query(5, ge=1, le=20),
              p: Principal = Depends(caller)):
    from cascade_guard.company import kb
    hits = kb.search(p.workspace, q, k=k)
    return {"relevant": kb.relevant(hits), "passages": hits}


# ------------------------------------------------------------------ keys and usage

@router.get("/me")
def me(p: Principal = Depends(caller)):
    return {**p.public(), "used_this_month": K.used(p), "month": K.month(),
            "left_this_month": None if p.monthly_limit <= 0 or p.key_id == 0 else max(0, p.monthly_limit - K.used(p))}


class KeyIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    workspace: str | None = Field(None, max_length=63)
    role: Literal["member", "manager"] = "member"
    plan: str = Field("starter", max_length=40)
    monthly_limit: int | None = Field(None, ge=0, le=10_000_000)
    email: str | None = Field(None, max_length=200)


@router.get("/admin/keys")
def keys_list(workspace: str | None = None, p: Principal = Depends(caller)):
    need(p, "manager")
    return {"keys": K.list_keys(workspace if p.role == "owner" else p.workspace), "plans": K.plans()}


@router.post("/admin/keys")
def keys_create(req: KeyIn, p: Principal = Depends(caller)):
    need(p, "manager")
    if p.role == "owner":
        ws, role, plan, limit = req.workspace or "default", req.role, req.plan, req.monthly_limit
    else:  # a company's manager adds member keys for their own people, on their own plan
        if req.role != "member":
            raise HTTPException(status_code=403, detail="managers can add member keys; ask the owner for a manager key")
        ws, role, plan, limit = p.workspace, "member", p.plan, p.monthly_limit
    try:
        key, rec = K.create_key(req.name, ws, role=role, plan=plan, monthly_limit=limit, email=req.email)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return {"key": key, "record": rec, "note": "Copy the key now: it is shown only once."}


@router.delete("/admin/keys/{key_id}")
def keys_revoke(key_id: int, p: Principal = Depends(caller)):
    need(p, "manager")
    if not K.revoke_key(key_id, None if p.role == "owner" else p.workspace):
        raise HTTPException(status_code=404, detail="no such active key")
    return {"revoked": key_id}


@router.get("/admin/usage")
def usage(p: Principal = Depends(caller)):
    need(p, "manager")
    return {"usage": K.usage_report(None if p.role == "owner" else p.workspace)}


# ------------------------------------------------------------------ OpenAI-format API

def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # [{"type": "text", "text": ...}, {"type": "image_url", ...}]
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict)
                         and part.get("type") in ("text", "input_text"))
    return ""


@router.get("/v1/models")
def v1_models(p: Principal = Depends(caller)):
    now = int(time.time())
    return {"object": "list", "data": [{"id": m, "object": "model", "created": now, "owned_by": "cascade-guard"}
                                       for m in MODELS]}


@router.post("/v1/chat/completions")
async def v1_chat(request: Request, p: Principal = Depends(caller)):
    from fastapi.concurrency import run_in_threadpool
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="the body is not JSON") from None
    msgs = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(msgs, list) or not msgs:
        raise HTTPException(status_code=400, detail="messages is required")
    turns: list[dict[str, str]] = [{"role": str(m.get("role")), "content": _text_of(m.get("content"))[:20_000]}
                                   for m in msgs if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    if not turns or turns[-1]["role"] != "user" or not turns[-1]["content"].strip():
        raise HTTPException(status_code=400, detail="the last message must be the user's question")
    model = str(body.get("model") or "cascade-guard")
    mode = MODELS.get(model, "auto")
    res = await run_in_threadpool(_answer_for, p, turns[-1]["content"][:4000], turns[:-1][-12:], mode)
    show = os.getenv("CHAT_API_FOOTER", "on").strip().lower() not in ("off", "0", "no", "false")
    note = footer(res) if show else ""
    content = res["answer"] + (f"\n\n{note}" if note else "")
    cid, now = "chatcmpl-" + uuid.uuid4().hex[:24], int(time.time())
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": int(res.get("usage", {}).get("tokens", 0))}
    extra = {k: res.get(k) for k in ("verdict", "mode", "sources", "checks", "caught", "removed", "notice")}
    if not body.get("stream"):
        return {"id": cid, "object": "chat.completion", "created": now, "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}], "usage": usage, "cascade_guard": extra}

    def events():
        def chunk(delta: dict, finish=None) -> str:
            return "data: " + json.dumps({"id": cid, "object": "chat.completion.chunk", "created": now,
                                          "model": model, "choices": [{"index": 0, "delta": delta,
                                                                       "finish_reason": finish}]}) + "\n\n"
        yield chunk({"role": "assistant", "content": ""})
        for i in range(0, len(content), 48):
            yield chunk({"content": content[i:i + 48]})
        yield chunk({}, "stop")
        yield "data: [DONE]\n\n"
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def install(app) -> None:
    """Errors on /v1/... in OpenAI's shape, so OpenAI-format apps show the message."""
    from fastapi.exception_handlers import http_exception_handler
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def _errors(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/v1/"):
            kind = {401: "invalid_api_key", 403: "permission_denied", 429: "rate_limit_exceeded"}.get(exc.status_code,
                                                                                                       "invalid_request")
            return JSONResponse({"error": {"message": str(exc.detail), "type": kind, "code": exc.status_code}},
                                status_code=exc.status_code, headers=getattr(exc, "headers", None))
        return await http_exception_handler(request, exc)
