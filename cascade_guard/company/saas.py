"""The public service: a home page, free sign-up, the website chat widget, and the policy pages.

Turn it on with PUBLIC_SIGNUP=1 (off by default, so a company's private install stays private).

    GET  /                      the home page (the older dashboard moves to /dashboard)
    POST /signup                {company, email} -> a free workspace and its manager key, shown once
    GET  /plans                 the plans, prices and payment links (for the home page)
    GET  /widget.js             the chat bubble a business pastes into its website:
                                  <script src="https://<server>/widget.js" data-key="pk_..." async></script>
    GET  /widget/key            the workspace's public site key (manager; made on first use)
    POST /widget/ask            {key, question, history} -> a checked answer from that workspace's documents
    GET  /widget/demo           a demo site key (the made-up sample company), for the home page
    GET  /terms /privacy /refunds /contact      policy pages (payment gateways ask for these)

Settings: BRAND (name shown), CONTACT_EMAIL, BUSINESS_NAME, BUSINESS_ADDRESS, SIGNUPS_PER_DAY (default 200),
PLAN_PASSAGES ('{"free": 300}': how much a plan may upload). A site key is public by design: it can only ask
questions, only from its workspace's documents, and every answer counts against that workspace's plan.
"""
from __future__ import annotations

import contextlib
import html
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from cascade_guard.company import keys as K
from cascade_guard.company.keys import Principal, caller, need

router = APIRouter(tags=["service"])
STATIC = Path(__file__).resolve().parent.parent / "static"
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "86400"}


def public() -> bool:
    return os.getenv("PUBLIC_SIGNUP", "").strip().lower() in ("1", "on", "yes", "true")


def brand() -> str:
    return os.getenv("BRAND") or "Cascade Guard"


def passages_limit(plan: str) -> int | None:
    limits = {"free": 300}
    with contextlib.suppress(ValueError, TypeError, AttributeError):
        limits.update({str(k): int(v) for k, v in json.loads(os.getenv("PLAN_PASSAGES") or "{}").items()})
    return limits.get(plan)


# ------------------------------------------------------------------ sign-up

_signups: dict[str, list[float]] = {}
_lock = threading.Lock()


def _allow_signup(address: str) -> bool:
    now, day = time.time(), 86400
    with _lock:
        for k in list(_signups):
            _signups[k] = [t for t in _signups[k] if now - t < day]
            if not _signups[k]:
                del _signups[k]
        total = sum(len(v) for v in _signups.values())
        if len(_signups.get(address, [])) >= 3 or total >= int(os.getenv("SIGNUPS_PER_DAY", "200")):
            return False
        _signups.setdefault(address, []).append(now)
        return True


class SignupIn(BaseModel):
    company: str = Field(..., min_length=2, max_length=80)
    email: str = Field(..., min_length=5, max_length=255)
    agree: bool = False


@router.post("/signup")
def signup(req: SignupIn, request: Request):
    if not public():
        raise HTTPException(status_code=404, detail="sign-up is not open on this server")
    email = req.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(status_code=422, detail="That email address doesn't look right.")
    if not req.agree:
        raise HTTPException(status_code=422, detail="Please agree to the terms and the privacy policy.")
    if K.workspace_of_email(email):
        raise HTTPException(status_code=409, detail="This email already has a workspace. Use the key you were given, "
                                                    "or write to us for a new one.")
    if not _allow_signup(request.client.host if request.client else "?"):
        raise HTTPException(status_code=429, detail="Too many sign-ups from here today. Please try again tomorrow.")
    from cascade_guard.company.billing import _workspace_for
    try:
        key, rec = K.create_key(req.company.strip(), _workspace_for(req.company.strip(), email, email + str(time.time())),
                                role="manager", plan="free", email=email)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return {"key": key, "workspace": rec["workspace"], "plan": "free", "monthly_limit": rec["monthly_limit"],
            "note": "Copy the key now: it is shown only once."}


@router.get("/plans")
def plans():
    from cascade_guard.company.billing import _json_env
    prices, links = _json_env("PLAN_PRICES"), _json_env("BILLING_LINKS")
    out = [{"plan": name, "answers": limit, "price": prices.get(name) or ("Free" if name == "free" else None),
            "link": links.get(name)} for name, limit in K.plans().items()
           if name == "free" or name in prices or name in links]
    return {"brand": brand(), "signup": public(), "contact": os.getenv("CONTACT_EMAIL") or None, "plans": out}


# ------------------------------------------------------------------ the website widget

def site_key(workspace: str, plan: str = "free", limit: int | None = None) -> str:
    """The workspace's public site key; made the first time it is asked for."""
    store = K._store()
    rows = store._read("SELECT reveal FROM api_keys WHERE workspace = ? AND role = 'widget' AND revoked_at IS NULL"
                       " AND reveal IS NOT NULL ORDER BY id LIMIT 1", (workspace,))
    if rows:
        return rows[0]["reveal"]
    key, _ = K.create_key("website widget", workspace, role="widget", plan=plan, monthly_limit=limit,
                          keep_for_reveal=True)   # public by design, so it is kept to show again
    return key


@router.get("/widget/key")
def widget_key(request: Request, p: Principal = Depends(caller)):  # noqa: B008
    need(p, "manager")
    key = site_key(p.workspace, p.plan if p.key_id else "unlimited", p.monthly_limit if p.key_id else 0)
    base = os.getenv("PUBLIC_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
    return {"key": key, "snippet": f'<script src="{base}/widget.js" data-key="{key}" async></script>'}


@router.get("/widget/demo")
def widget_demo():
    """A site key for the made-up sample company, so the home page can show the real thing."""
    from cascade_guard.company import kb
    from cascade_guard.company.api import load_samples
    if not kb.list_documents("demo"):
        load_samples("demo", by="demo")
    return {"key": site_key("demo", "team")}


@router.get("/widget.js", include_in_schema=False)
def widget_js():
    return FileResponse(STATIC / "widget.js", media_type="application/javascript",
                        headers={"Cache-Control": "public, max-age=300", "Access-Control-Allow-Origin": "*",
                                 "Cross-Origin-Resource-Policy": "cross-origin"})


class WidgetTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field("", max_length=4000)


class WidgetAsk(BaseModel):
    key: str = Field(..., min_length=10, max_length=100)
    question: str = Field(..., min_length=1, max_length=1000)
    history: list[WidgetTurn] = Field(default_factory=list, max_length=12)


@router.options("/widget/ask", include_in_schema=False)
def widget_preflight():
    return Response(status_code=204, headers=CORS)


@router.post("/widget/ask")
def widget_ask(req: WidgetAsk):
    from cascade_guard.company.answer import answer
    from cascade_guard.company.api import _client
    p = K.principal_for(req.key)
    if p is None or p.role != "widget":
        return JSONResponse({"detail": "This chat is not set up correctly (unknown site key)."}, status_code=401,
                            headers=CORS)
    try:
        K.check_quota(p)
    except HTTPException:
        return JSONResponse({"detail": "This chat has reached its monthly limit. Please contact us directly."},
                            status_code=429, headers=CORS)
    res = answer(req.question, workspace=p.workspace, client=_client(),
                 history=[t.model_dump() for t in req.history], mode="documents")
    K.record(p, ai_calls=res.get("usage", {}).get("ai_calls", 0), tokens=res.get("usage", {}).get("tokens", 0))
    # Visitors see the answer and where it came from; the working stays in the owner's dashboard.
    return JSONResponse({"answer": res["answer"], "verdict": res["verdict"], "notice": res.get("notice"),
                         "sources": [{"n": s["n"], "doc": s["doc"], "page": s.get("page")}
                                     for s in res.get("sources", [])]}, headers=CORS)


# ------------------------------------------------------------------ pages

@router.api_route("/start", methods=["GET", "HEAD"], include_in_schema=False)
def landing():
    return FileResponse(STATIC / "landing.html")


@router.api_route("/dashboard", methods=["GET", "HEAD"], include_in_schema=False)
def dashboard():
    return FileResponse(STATIC / "index.html")


def _page(title: str, body: str) -> str:
    from cascade_guard.company.billing import _STYLE
    b = html.escape(brand())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>{_STYLE}<style>h2{{font-size:18px;
margin:22px 0 6px}}p,li{{max-width:70ch}}.nav{{font-size:14px;margin-bottom:18px}}</style></head><body><div class="w">
<p class="nav"><a href="/">{b}</a> · <a href="/terms">Terms</a> · <a href="/privacy">Privacy</a> ·
<a href="/refunds">Refunds</a> · <a href="/contact">Contact</a></p><h1>{html.escape(title)}</h1>{body}</div></body></html>"""


def _who() -> tuple[str, str, str]:
    name = html.escape(os.getenv("BUSINESS_NAME") or brand())
    mail = os.getenv("CONTACT_EMAIL", "")
    contact = f'<a href="mailto:{html.escape(mail, quote=True)}">{html.escape(mail)}</a>' if mail else \
        "the address on our Contact page"
    return name, contact, html.escape(os.getenv("BUSINESS_ADDRESS", ""))


@router.get("/terms", response_class=HTMLResponse, include_in_schema=False)
def terms():
    name, contact, _ = _who()
    return _page("Terms of Service", f"""
<p class="m">These terms apply when you use {html.escape(brand())}, a service run by {name}.</p>
<h2>The service</h2><p>You upload your documents. The service answers questions from them, in your dashboard, through
its API, and in a chat widget on your website. Answers are written by an AI model and checked automatically against
your documents. The checks reduce mistakes; they do not guarantee that every answer is correct. Do not rely on it
for medical, legal, financial or safety decisions without a person checking.</p>
<h2>Your account</h2><p>Keep your access keys secret (the website widget key is public by design). You are
responsible for what is done with your keys and for the documents you upload. Upload only documents you have the
right to use, and nothing illegal.</p>
<h2>Plans and payment</h2><p>Each plan includes a number of answers per month. When they are used up, the service
stops answering until the next month or until you upgrade. Paid plans are charged in advance through our payment
provider. Prices can change with notice for future periods.</p>
<h2>Cancelling</h2><p>You can cancel at any time; your workspace then moves to the free plan. We may suspend an
account that abuses the service or breaks these terms.</p>
<h2>Liability</h2><p>The service is provided as is. To the extent the law allows, our total liability is limited to
what you paid us in the three months before the claim.</p>
<h2>Changes and contact</h2><p>We may update these terms and will show the new date here. Questions: {contact}.</p>
<p class="m">Last updated: {time.strftime('%d %B %Y')}.</p>""")


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
def privacy():
    name, contact, _ = _who()
    return _page("Privacy Policy", f"""
<p class="m">{name} runs {html.escape(brand())}. This page says what we keep and why.</p>
<h2>What we keep</h2><ul><li>Your company name and email address, from sign-up.</li><li>The documents you upload,
stored as text so they can be searched.</li><li>Counts of how many answers your workspace used.</li><li>A log of
requests (time, address, route, result) for security.</li></ul>
<p>We do not store the questions your website visitors ask, and the widget sets no cookies.</p>
<h2>How it is used</h2><p>Only to provide the service to you. When a question is asked, the few passages of your
documents that relate to it are sent to the AI model that writes the answer. We do not sell your data or use your
documents to train models.</p>
<h2>Who else handles it</h2><p>Our hosting provider, our database provider, the AI model provider, and the payment
provider (for payments; we never see your card or bank details).</p>
<h2>Your choices</h2><p>You can delete your documents at any time in the dashboard. To delete your whole workspace,
or to see or correct your details, write to {contact}.</p>
<p class="m">Last updated: {time.strftime('%d %B %Y')}.</p>""")


@router.get("/refunds", response_class=HTMLResponse, include_in_schema=False)
def refunds():
    _, contact, _ = _who()
    return _page("Cancellation and Refunds", f"""
<h2>Cancelling</h2><p>You can cancel a paid plan at any time. It stays active until the end of the period you paid
for, then your workspace moves to the free plan. Your documents are kept.</p>
<h2>Refunds</h2><p>If the service did not work for you, write to {contact} within 7 days of a payment and we will
refund that payment. Refunds go back to the original payment method, usually within 5 to 7 working days.</p>
<h2>Delivery</h2><p>This is an online service. Access starts as soon as a payment is confirmed; nothing is
shipped.</p>""")


@router.get("/contact", response_class=HTMLResponse, include_in_schema=False)
def contact_page():
    name, contact, address = _who()
    mail = os.getenv("CONTACT_EMAIL")
    lines = f"<p><b>{name}</b></p>" + (f"<p>Email: {contact}</p>" if mail else
                                       "<p>Contact details are being set up.</p>")
    if address:
        lines += f"<p>{address}</p>"
    return _page("Contact", lines + "<p>We usually reply within 2 working days.</p>")
