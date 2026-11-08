"""Selling it: payments create customer keys by themselves.

    GET  /pricing                    your plans, prices and payment links (a page to send customers)
    POST /billing/stripe             Stripe webhook: a paid checkout makes a key; a cancelled subscription revokes it
    POST /billing/razorpay           Razorpay webhook: a paid payment link or payment makes a key; a cancelled
                                     subscription revokes it
    GET  /welcome?ref=...            after paying, the customer sees their key once (then it is forgotten)

Settings (environment or .env):
    STRIPE_WEBHOOK_SECRET      whsec_... from the Stripe dashboard (Developers > Webhooks)
    RAZORPAY_WEBHOOK_SECRET    the secret you typed when adding the Razorpay webhook
    BILLING_PLANS              which payment gives which plan, e.g. '{"plink_ABC": "starter", "plink_XYZ": "team"}'
                               (Stripe payment link or price ids, Razorpay payment link ids, or an amount in the
                               smallest unit like "99900"); a "plan" in the payment's metadata/notes also works
    BILLING_DEFAULT_PLAN       when nothing matches (default: starter)
    PLAN_PRICES                for the pricing page, e.g. '{"starter": "₹999 a month", "team": "₹4,999 a month"}'
    BILLING_LINKS              payment links for the pricing page, e.g. '{"starter": "https://rzp.io/l/..."}'

Set the payment's success page to  https://<your server>/welcome?ref={CHECKOUT_SESSION_ID}  (Stripe) or the
payment link's callback URL to  https://<your server>/welcome  (Razorpay adds the payment id itself).
Each paying customer gets their own workspace (their company or email name) and a manager key: they add their
documents and their people's keys themselves.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from cascade_guard.company import keys as K

router = APIRouter(tags=["billing"])


def _json_env(name: str) -> dict:
    try:
        v = json.loads(os.getenv(name) or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def plan_for(*refs, notes: dict | None = None, amount=None) -> str:
    mapping = _json_env("BILLING_PLANS")
    for r in refs:
        if r and str(r) in mapping:
            return str(mapping[str(r)])
    if notes and str(notes.get("plan") or "") in K.plans():
        return str(notes["plan"])
    if amount is not None and str(amount) in mapping:
        return str(mapping[str(amount)])
    return os.getenv("BILLING_DEFAULT_PLAN", "starter")


def _workspace_for(company: str | None, email: str | None, ref: str) -> str:
    base = company or (email.split("@")[0] + "-" + email.split("@")[1].split(".")[0] if email and "@" in email
                       else "") or "customer"
    try:
        ws = K.norm_workspace(base)
    except ValueError:
        ws = "customer"
    from cascade_guard.storage.db import get_store
    store = get_store()
    if store is not None and store._read("SELECT 1 FROM api_keys WHERE workspace = ? LIMIT 1", (ws,)):
        ws = K.norm_workspace(f"{ws[:50]}-{hashlib.sha256(ref.encode()).hexdigest()[:6]}")  # never join a stranger's
    return ws


def _issue(ref: str, *, email: str | None, company: str | None, plan: str, customer: str | None) -> dict:
    """A manager key for a new paying customer, once per payment (webhooks are retried)."""
    existing = K.find_by_ref(ref)
    if existing:
        return {"status": "already issued", "key_id": existing["id"]}
    if plan not in K.plans():
        plan = os.getenv("BILLING_DEFAULT_PLAN", "starter")
    _, rec = K.create_key(company or email or "customer", _workspace_for(company, email, ref), role="manager",
                          plan=plan, email=email, ref=ref, customer=customer, keep_for_reveal=True)
    return {"status": "issued", "key_id": rec["id"], "workspace": rec["workspace"], "plan": plan}


# ------------------------------------------------------------------ Stripe

def verify_stripe(payload: bytes, header: str, secret: str, tolerance: int = 300, now: float | None = None) -> bool:
    """Stripe's scheme: HMAC-SHA256 of "<t>.<payload>" with the endpoint secret, sent as v1=...; t must be recent."""
    parts: dict[str, list[str]] = {}
    for item in (header or "").split(","):
        k, _, v = item.strip().partition("=")
        parts.setdefault(k, []).append(v)
    try:
        t = int(parts.get("t", [""])[0])
    except ValueError:
        return False
    if abs((now or time.time()) - t) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", []))


@router.post("/billing/stripe", include_in_schema=False)
async def stripe_webhook(request: Request):
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=404, detail="Stripe is not set up (STRIPE_WEBHOOK_SECRET)")
    body = await request.body()
    if not verify_stripe(body, request.headers.get("stripe-signature", ""), secret):
        raise HTTPException(status_code=400, detail="bad signature")
    event = json.loads(body)
    obj = (event.get("data") or {}).get("object") or {}
    kind = event.get("type")
    if kind == "checkout.session.completed" and obj.get("payment_status") in ("paid", "no_payment_required", None):
        details = obj.get("customer_details") or {}
        meta = obj.get("metadata") or {}
        plan = plan_for(obj.get("payment_link"), meta.get("price"), notes=meta, amount=obj.get("amount_total"))
        return _issue(obj["id"], email=details.get("email"), company=meta.get("company") or details.get("name"),
                      plan=plan, customer=obj.get("subscription") or obj.get("customer"))
    if kind in ("customer.subscription.deleted",):
        return {"status": "revoked", "keys": K.revoke_by_customer(obj.get("id", ""))}
    return {"status": "ignored", "type": kind}


# ------------------------------------------------------------------ Razorpay

def verify_razorpay(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


@router.post("/billing/razorpay", include_in_schema=False)
async def razorpay_webhook(request: Request):
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=404, detail="Razorpay is not set up (RAZORPAY_WEBHOOK_SECRET)")
    body = await request.body()
    if not verify_razorpay(body, request.headers.get("x-razorpay-signature", ""), secret):
        raise HTTPException(status_code=400, detail="bad signature")
    event = json.loads(body)
    kind = event.get("event")
    p = event.get("payload") or {}
    pay = (p.get("payment") or {}).get("entity") or {}
    if kind == "payment_link.paid":
        link = (p.get("payment_link") or {}).get("entity") or {}
        cust = link.get("customer") or {}
        notes = link.get("notes") or pay.get("notes") or {}
        if not pay.get("id"):
            raise HTTPException(status_code=422, detail="no payment in the event")
        return _issue(pay["id"], email=cust.get("email") or pay.get("email"),
                      company=notes.get("company") or cust.get("name"),
                      plan=plan_for(link.get("id"), notes=notes, amount=link.get("amount") or pay.get("amount")),
                      customer=pay.get("subscription_id") or pay.get("customer_id"))
    if kind == "subscription.charged" and pay.get("id"):
        sub = (p.get("subscription") or {}).get("entity") or {}
        notes = sub.get("notes") or pay.get("notes") or {}
        if K.find_by_ref("sub:" + str(sub.get("id"))):
            return {"status": "renewed"}
        return _issue("sub:" + str(sub.get("id")), email=pay.get("email"), company=notes.get("company"),
                      plan=plan_for(sub.get("plan_id"), notes=notes, amount=pay.get("amount")),
                      customer=sub.get("id"))
    if kind in ("subscription.cancelled", "subscription.halted", "subscription.completed"):
        sub = (p.get("subscription") or {}).get("entity") or {}
        return {"status": "revoked", "keys": K.revoke_by_customer(str(sub.get("id", "")))}
    return {"status": "ignored", "type": kind}


# ------------------------------------------------------------------ pages

_STYLE = """<style>
:root{--paper:#f2f5f1;--sheet:#fff;--ink:#1d2528;--muted:#5d6b6f;--rule:#d3dddf;--blue:#23527c;--ok:#1d7a50;--oks:#e3f2ea}
@media (prefers-color-scheme:dark){:root{--paper:#13181a;--sheet:#1b2225;--ink:#e5ebe9;--muted:#9aa8ab;--rule:#2f3a3e;
--blue:#8db8e8;--ok:#5ccf96;--oks:#163326}}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 "Segoe UI",system-ui,
-apple-system,Arial,sans-serif}.w{max-width:880px;margin:0 auto;padding:28px 16px 60px}h1{font-size:28px;margin:0 0 6px;
letter-spacing:-.02em}p.m{color:var(--muted);margin:0 0 20px}.plans{display:grid;grid-template-columns:repeat(auto-fit,
minmax(200px,1fr));gap:14px}.c{background:var(--sheet);border:1px solid var(--rule);border-radius:12px;padding:18px}
.c h2{margin:0 0 4px;font-size:18px;text-transform:capitalize}.price{font-size:22px;font-weight:650;margin:6px 0}
.c ul{padding-left:18px;color:var(--muted);font-size:14.5px}.b{display:inline-block;background:var(--blue);color:#fff;
border-radius:9px;padding:9px 16px;text-decoration:none;font-weight:600}.key{background:var(--oks);border:1px solid
var(--ok);border-radius:10px;padding:14px;font:15px ui-monospace,Menlo,Consolas,monospace;word-break:break-all}
a{color:var(--blue)}</style>"""


def _buy(link: str | None, plan: str) -> str:
    return f'<a class="b" href="{html.escape(link, quote=True)}">Choose {html.escape(plan)}</a>' if link else ""


@router.get("/pricing", response_class=HTMLResponse, include_in_schema=False)
def pricing():
    prices, links = _json_env("PLAN_PRICES"), _json_env("BILLING_LINKS")
    name = html.escape(os.getenv("COMPANY_NAME", "") or "Checked company assistant")
    cards = []
    for plan, limit in K.plans().items():
        if plan not in prices and plan not in links:
            continue
        lim = f"{limit:,} checked answers a month" if limit else "No monthly limit"
        buy = links.get(plan)
        cards.append(f"""<div class="c"><h2>{html.escape(plan)}</h2><div class="price">{html.escape(str(prices.get(plan, '')))}
</div><ul><li>{lim}</li><li>Your own private workspace for your documents</li><li>Keys for your whole team</li>
<li>Works in the browser and with any OpenAI-compatible app</li></ul>{_buy(buy, plan)}</div>""")
    body = "".join(cards) or "<p>Plans are not published yet (set PLAN_PRICES and BILLING_LINKS).</p>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,
initial-scale=1"><title>Pricing</title>{_STYLE}</head><body><div class="w"><h1>{name}</h1><p class="m">An AI assistant
that answers from your company's documents, and checks every answer against them before anyone sees it: every number,
date and quotation, with the passages it used. If your documents don't say, it tells you instead of guessing.</p>
<div class="plans">{body}</div></div></body></html>"""


@router.get("/welcome", response_class=HTMLResponse, include_in_schema=False)
def welcome(ref: str | None = None, razorpay_payment_id: str | None = None):
    ref = ref or razorpay_payment_id
    if not ref or not re.fullmatch(r"[A-Za-z0-9_:\-]{6,200}", ref):
        raise HTTPException(status_code=404, detail="no payment reference")
    got = K.reveal_once(ref)
    if got is None:
        rec = K.find_by_ref(ref)
        msg = ("Your key was already shown once, so it is not shown again. If you did not save it, write to us and "
               "we will make a new one.") if rec else ("We have not received the payment confirmation yet. It "
                                                       "usually takes a few seconds: refresh this page in a minute.")
        inner = f"<h1>Thank you</h1><p class='m'>{msg}</p>"
    else:
        lim = f"{got['monthly_limit']:,} answers a month" if got["monthly_limit"] else "no monthly limit"
        inner = f"""<h1>Welcome! Here is your key</h1><p class="m">Plan {html.escape(got['plan'])} ({lim}), workspace
<b>{html.escape(got['workspace'])}</b>. Copy the key now and keep it safe: it is shown only once.</p>
<div class="key">{html.escape(got['key'])}</div><p>Next: open <a href="/chat">the assistant</a>, paste the key, and add your
company's documents (PDF, Word, text). From <a href="/admin">the admin page</a> you can give each of your people a key
of their own and connect other apps.</p>"""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,
initial-scale=1"><meta name="referrer" content="no-referrer"><title>Welcome</title>{_STYLE}</head><body><div class="w">
{inner}</div></body></html>"""
