"""The signed-in app: accounts, the dashboard's data, conversations, leads, team, settings, data export.

    GET  /login  /app  /security                 pages
    POST /auth/signup  /auth/login  /auth/logout   accounts with a password (a session cookie)
    GET  /auth/me      /auth/options               who is signed in; which sign-in methods are on
    POST /auth/password  /auth/reset-request  /auth/reset  /auth/accept     passwords and invites
    GET  /auth/sso/start  /auth/sso/callback       single sign-on (OpenID Connect)
    GET  /app/overview                             numbers for the last 30 days, setup steps, unanswered questions
    GET  /app/conversations[.csv]                  what was asked and answered (manager)
    GET  /app/leads   POST /app/leads/{id}/done    contact details visitors left
    GET/PUT /app/settings                          widget look, allowed domains, logging, lead capture
    GET  /app/team   POST /app/team/invite   PATCH/DELETE /app/team/{id}   POST /app/team/{id}/reset-link
    GET  /app/activity                             who did what
    POST /kb/url                                   add pages from a website
    GET  /app/export   POST /app/delete-workspace  take everything out; delete everything
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from cascade_guard.company import accounts as A
from cascade_guard.company import keys as K
from cascade_guard.company.keys import Principal, caller, need

router = APIRouter(tags=["app"])
STATIC = Path(__file__).resolve().parent.parent / "static"


def _base(request: Request) -> str:
    return os.getenv("PUBLIC_URL", "").rstrip("/") or str(request.base_url).rstrip("/")


def _https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def _set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(A.COOKIE, token, max_age=A.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=_https(request), path="/")


def _address(request: Request) -> str:
    from cascade_guard.clientip import client_address
    return client_address(request)


def _actor(p: Principal) -> str:
    return p.name or p.role


# ------------------------------------------------------------------ pages

@router.api_route("/login", methods=["GET", "HEAD"], include_in_schema=False)
def login_page():
    return FileResponse(STATIC / "login.html")


@router.api_route("/app", methods=["GET", "HEAD"], include_in_schema=False)
def app_page():
    return FileResponse(STATIC / "app.html")


# ------------------------------------------------------------------ accounts

class SignupIn(BaseModel):
    company: str = Field(..., min_length=2, max_length=80)
    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=1, max_length=200)
    name: str = Field("", max_length=100)
    agree: bool = False


class LoginIn(BaseModel):
    email: str = Field(..., max_length=255)
    password: str = Field(..., max_length=200)


@router.get("/auth/options")
def auth_options():
    from cascade_guard.company.saas import brand, public
    return {"brand": brand(), "signup": public(), "sso": A.sso_name() if A.sso_on() else None, "email": A.email_on()}


@router.post("/auth/signup")
def auth_signup(req: SignupIn, request: Request, response: Response):
    from cascade_guard.company.saas import _allow_signup, public
    if not public():
        raise HTTPException(status_code=404, detail="Sign-up is not open on this server. Ask for an invite.")
    if not req.agree:
        raise HTTPException(status_code=422, detail="Please agree to the terms and the privacy policy.")
    if not A.EMAIL.match(req.email.strip().lower()):
        raise HTTPException(status_code=422, detail="That email address doesn't look right.")
    A.good_password(req.password)
    if not _allow_signup(_address(request)):
        raise HTTPException(status_code=429, detail="Too many sign-ups from here today. Please try again tomorrow.")
    user = A.signup(req.company, req.email, req.password, req.name)
    _set_cookie(response, request, A.start_session(user["id"]))
    return {"user": user}


@router.post("/auth/login")
def auth_login(req: LoginIn, request: Request, response: Response):
    token, user = A.login(req.email, req.password, _address(request))
    _set_cookie(response, request, token)
    return {"user": user}


@router.post("/auth/logout")
def auth_logout(request: Request, response: Response):
    token = request.cookies.get(A.COOKIE)
    if token:
        A.end_session(token)
    response.delete_cookie(A.COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def auth_me(request: Request):
    user = A.session_user(request.cookies.get(A.COOKIE) or "")
    if user is None:
        raise HTTPException(status_code=401, detail="not signed in")
    w = A.get_workspace(user["workspace"]) or {}
    return {"user": A._user_public(user), "workspace": {"name": user["workspace"], "title": w.get("title"),
                                                        "plan": w.get("plan"), "monthly_limit": w.get("monthly_limit")}}


class PasswordIn(BaseModel):
    old: str = Field("", max_length=200)
    new: str = Field(..., max_length=200)


@router.post("/auth/password")
def auth_password(req: PasswordIn, request: Request, response: Response):
    user = A.session_user(request.cookies.get(A.COOKIE) or "")
    if user is None or request.headers.get("x-cg") != "1":
        raise HTTPException(status_code=401, detail="not signed in")
    if user.get("password_hash") and not A.check_password(req.old, user["password_hash"]):
        raise HTTPException(status_code=403, detail="The current password is not right.")
    A.set_password(user["id"], req.new)
    _set_cookie(response, request, A.start_session(user["id"]))   # this browser stays signed in
    A.event(user["workspace"], user["email"], "password changed")
    return {"ok": True}


class DeleteMeIn(BaseModel):
    password: str = Field(default="", max_length=200)


@router.post("/auth/delete-account")
def auth_delete_account(req: DeleteMeIn, request: Request, response: Response):
    """A person deletes their own account (the app stores require this). The last manager deletes the whole
    workspace instead (Settings), so a company is never left without anyone who can manage it."""
    user = A.session_user(request.cookies.get(A.COOKIE) or "")
    if user is None or request.headers.get("x-cg") != "1":
        raise HTTPException(status_code=401, detail="not signed in")
    if user.get("password_hash") and not A.check_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=403, detail="The password is not right.")
    others = [u for u in A.list_users(user["workspace"]) if u["id"] != user["id"]]
    if user["role"] == "manager" and not any(u["role"] == "manager" and u["active"] for u in others):
        raise HTTPException(status_code=409, detail="You are the only manager. To delete your account, delete the "
                                                    "whole workspace under Settings, or make someone else a manager "
                                                    "first.")
    with K._store()._tx() as c:
        c.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        c.execute("DELETE FROM push_devices WHERE user_id = ?", (user["id"],))
        c.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    A.event(user["workspace"], user["email"], "deleted their account")
    response.delete_cookie(A.COOKIE, path="/")
    return {"deleted": True}


class ResetRequestIn(BaseModel):
    email: str = Field(..., max_length=255)


@router.post("/auth/reset-request")
def auth_reset_request(req: ResetRequestIn, request: Request):
    A.throttle(f"reset:{_address(request)}", limit=5)
    user = A.find_user(req.email)
    sent = False
    if user and not user.get("disabled_at") and A.email_on():
        link = f"{_base(request)}/login?reset={A.make_link('reset', user_id=user['id'], hours=2)}"
        sent = A.send_email(user["email"], "Reset your password",
                            f"Open this link within 2 hours to choose a new password:\n\n{link}\n\n"
                            "If you didn't ask for this, ignore this email.")
    # the same answer whether or not the account exists
    del sent
    return {"ok": True, "email": A.email_on(),
            "note": "If that account exists, a reset link is on its way." if A.email_on() else
                    "Ask a manager of your workspace for a reset link" + (
                        f", or write to {os.getenv('CONTACT_EMAIL')}." if os.getenv("CONTACT_EMAIL") else
                        ", or contact the people who run this service.")}


class ResetIn(BaseModel):
    token: str = Field(..., max_length=200)
    password: str = Field(..., max_length=200)


@router.post("/auth/reset")
def auth_reset(req: ResetIn):
    A.reset_password(req.token, req.password)
    return {"ok": True}


class AcceptIn(BaseModel):
    token: str = Field(..., max_length=200)
    name: str = Field("", max_length=100)
    password: str = Field(..., max_length=200)


@router.get("/auth/invite")
def auth_invite(token: str = Query(..., max_length=200)):
    inv = A.read_link(token, "invite")
    w = A.get_workspace(inv["workspace"]) or {}
    return {"email": inv["email"], "workspace": w.get("title") or inv["workspace"], "role": inv["role"]}


@router.post("/auth/accept")
def auth_accept(req: AcceptIn, request: Request, response: Response):
    user = A.accept_invite(req.token, req.name, req.password)
    _set_cookie(response, request, A.start_session(user["id"]))
    return {"user": user}


@router.get("/auth/sso/start", include_in_schema=False)
def sso_start(request: Request):
    if not A.sso_on():
        raise HTTPException(status_code=404, detail="single sign-on is not set up")
    return RedirectResponse(A.sso_start(f"{_base(request)}/auth/sso/callback"), status_code=302)


@router.get("/auth/sso/callback", include_in_schema=False)
def sso_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if not A.sso_on():
        raise HTTPException(status_code=404, detail="single sign-on is not set up")
    if error or not code:
        return RedirectResponse("/login?problem=sso", status_code=302)
    try:
        token, user = A.sso_finish(code, state, f"{_base(request)}/auth/sso/callback")
    except HTTPException as e:
        return HTMLResponse(f"<p>{e.detail}</p><p><a href='/login'>Back to sign in</a></p>", status_code=e.status_code)
    resp = RedirectResponse("/app" if user["role"] == "manager" else "/chat", status_code=302)
    _set_cookie(resp, request, token)
    return resp


# ------------------------------------------------------------------ conversations

def log_answer(workspace: str, channel: str, question: str, res: dict) -> str | None:
    """Keep a question and its answer for the workspace's owner (unless they turned logging off). Returns a
    token the asker can use to rate that one answer."""
    try:
        if not A.settings(workspace).get("log_questions", True):
            return None
        token = secrets.token_urlsafe(12)
        with K._store()._tx() as c:
            c.execute("INSERT INTO chat_log (workspace, at, channel, question, answer, verdict, sources, token)"
                      " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (workspace, A._now(), channel, question[:2000], (res.get("answer") or "")[:6000],
                       res.get("verdict"), json.dumps(sorted({s["doc"] for s in res.get("sources") or []})), token))
        return token
    except Exception:  # keeping the log must never cost someone their answer
        return None


def _since(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


def prune_logs(workspace: str) -> None:
    days = int(os.getenv("LOG_RETENTION_DAYS", "180"))
    with K._store()._tx() as c:
        c.execute("DELETE FROM chat_log WHERE workspace = ? AND at < ?", (workspace, _since(days)))


@router.get("/app/overview")
def overview(request: Request, p: Principal = Depends(caller)):  # noqa: B008
    from cascade_guard.company import kb
    from cascade_guard.company.api import _client
    store = K._store()
    prune_logs(p.workspace)
    since = _since(30)
    rows = store._read("SELECT SUBSTR(at, 1, 10) AS day, verdict, COUNT(*) AS n FROM chat_log WHERE workspace = ? AND"
                       " at >= ? GROUP BY SUBSTR(at, 1, 10), verdict", (p.workspace, since))
    by_day: dict[str, int] = {}
    verdicts: dict[str, int] = {}
    for r in rows:
        by_day[r["day"]] = by_day.get(r["day"], 0) + int(r["n"])
        verdicts[r["verdict"] or "?"] = verdicts.get(r["verdict"] or "?", 0) + int(r["n"])
    days = [(datetime.now(UTC) - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(29, -1, -1)]
    total = sum(verdicts.values())
    answered = total - verdicts.get("not_found", 0)
    missing = store._read("SELECT question, COUNT(*) AS n, MAX(at) AS last FROM chat_log WHERE workspace = ? AND"
                          " verdict = 'not_found' AND at >= ? GROUP BY question ORDER BY n DESC, last DESC LIMIT 15",
                          (p.workspace, since))
    fb = store._read("SELECT SUM(CASE WHEN feedback = 1 THEN 1 ELSE 0 END) AS up, SUM(CASE WHEN feedback = -1 THEN 1"
                     " ELSE 0 END) AS down FROM chat_log WHERE workspace = ? AND at >= ?", (p.workspace, since))[0]
    docs = kb.list_documents(p.workspace)
    widget_used = bool(store._read("SELECT 1 FROM chat_log WHERE workspace = ? AND channel = 'widget' LIMIT 1",
                                   (p.workspace,)))
    leads = store._read("SELECT COUNT(*) AS n FROM leads WHERE workspace = ? AND handled_at IS NULL", (p.workspace,))
    w = A.get_workspace(p.workspace) or {}
    ai = _client()
    return {
        "workspace": {"name": p.workspace, "title": w.get("title") or p.workspace, "plan": p.plan,
                      "monthly_limit": p.monthly_limit, "used_this_month": K.used(p)},
        "you": {"name": p.name, "role": p.role},
        "ai": {"provider": ai.provider, "model": ai.model} if ai else None,
        "last_30_days": {"questions": total, "answered": answered, "not_found": verdicts.get("not_found", 0),
                         "checked": sum(verdicts.get(v, 0) for v in ("checked", "fixed", "quoted")),
                         "warned": sum(verdicts.get(v, 0) for v in ("unconfirmed", "trimmed")),
                         "helpful": int(fb["up"] or 0), "not_helpful": int(fb["down"] or 0),
                         "by_day": [{"day": d, "n": by_day.get(d, 0)} for d in days]},
        "unanswered": [dict(m) for m in missing], "open_leads": int(leads[0]["n"]),
        "setup": {"documents": len(docs) > 0, "tried": total > 0, "widget": widget_used,
                  "team": len(A.list_users(p.workspace)) > 1},
        "logging": A.settings(p.workspace).get("log_questions", True),
    }


def _conversations(workspace: str, verdict: str | None, q: str | None, limit: int, before: int | None) -> list[dict]:
    sql = "SELECT id, at, channel, question, answer, verdict, sources, feedback FROM chat_log WHERE workspace = ?"
    args: list = [workspace]
    if verdict:
        sql += " AND verdict = ?"
        args.append(verdict)
    if q:
        sql += " AND LOWER(question) LIKE ?"
        args.append("%" + q.lower().replace("%", "").replace("_", " ")[:100] + "%")
    if before:
        sql += " AND id < ?"
        args.append(before)
    rows = K._store()._read(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))
    out = []
    for r in rows:
        r = dict(r)
        try:
            r["sources"] = json.loads(r["sources"] or "[]")
        except ValueError:
            r["sources"] = []
        out.append(r)
    return out


@router.get("/app/conversations")
def conversations(verdict: str | None = Query(None, max_length=20), q: str | None = Query(None, max_length=100),
                  limit: int = Query(50, ge=1, le=200), before: int | None = None,
                  p: Principal = Depends(caller)):  # noqa: B008
    need(p, "manager")
    return {"conversations": _conversations(p.workspace, verdict, q, limit, before)}


def _csv_safe(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s   # spreadsheets must not run a visitor's text


@router.get("/app/conversations.csv")
def conversations_csv(p: Principal = Depends(caller)):  # noqa: B008
    need(p, "manager")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time (UTC)", "where", "question", "answer", "verdict", "sources", "feedback"])
    for r in _conversations(p.workspace, None, None, 5000, None):
        w.writerow([r["at"], r["channel"], _csv_safe(r["question"]), _csv_safe(r["answer"]), r["verdict"],
                    "; ".join(r["sources"]), {1: "helpful", -1: "not helpful"}.get(r["feedback"], "")])
    A.event(p.workspace, _actor(p), "conversations exported")
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="conversations.csv"'})


@router.get("/app/leads")
def leads(p: Principal = Depends(caller)):  # noqa: B008
    need(p, "manager")
    return {"leads": K._store()._read("SELECT id, at, question, contact, handled_at FROM leads WHERE workspace = ?"
                                      " ORDER BY id DESC LIMIT 500", (p.workspace,))}


@router.post("/app/leads/{lead_id}/done")
def lead_done(lead_id: int, p: Principal = Depends(caller)):  # noqa: B008
    need(p, "manager")
    with K._store()._tx() as c:
        c.execute("UPDATE leads SET handled_at = ? WHERE id = ? AND workspace = ?", (A._now(), lead_id, p.workspace))
    return {"ok": True}


# ------------------------------------------------------------------ settings

class WidgetLook(BaseModel):
    title: str = Field("Ask us", max_length=60)
    color: str = Field("#23527c", pattern=r"^#[0-9a-fA-F]{6}$")
    greeting: str = Field("", max_length=300)
    contact: str = Field("", max_length=200)


class SettingsIn(BaseModel):
    widget: WidgetLook | None = None
    domains: list[str] | None = Field(None, max_length=20)
    log_questions: bool | None = None
    collect_leads: bool | None = None
    title: str | None = Field(None, min_length=2, max_length=80)


def clean_domain(d: str) -> str:
    d = re.sub(r"^https?://", "", d.strip().lower()).split("/")[0].split(":")[0]
    d = d[4:] if d.startswith("www.") else d
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+|localhost", d):
        raise HTTPException(status_code=422, detail=f"'{d}' is not a website address like example.com")
    return d


@router.get("/app/settings")
def settings_get(p: Principal = Depends(caller)):  # noqa: B008
    need(p, "manager")
    w = A.ensure_workspace(p.workspace, p.workspace, p.plan if p.key_id else "unlimited")
    return {"title": w["title"], **w["settings"]}


@router.put("/app/settings")
def settings_put(req: SettingsIn, p: Principal = Depends(caller)):  # noqa: B008
    need(p, "manager")
    A.ensure_workspace(p.workspace, p.workspace, p.plan if p.key_id else "unlimited")
    changes: dict = {}
    if req.widget is not None:
        changes["widget"] = req.widget.model_dump()
    if req.domains is not None:
        changes["domains"] = sorted({clean_domain(d) for d in req.domains if d.strip()})
    if req.log_questions is not None:
        changes["log_questions"] = req.log_questions
    if req.collect_leads is not None:
        changes["collect_leads"] = req.collect_leads
    out = A.save_settings(p.workspace, changes)
    if req.title:
        with K._store()._tx() as c:
            c.execute("UPDATE workspaces SET title = ? WHERE name = ?", (req.title.strip(), p.workspace))
    A.event(p.workspace, _actor(p), "settings changed", ", ".join(sorted(changes)) or "name")
    return {"title": (A.get_workspace(p.workspace) or {}).get("title"), **out}


# ------------------------------------------------------------------ team

class InviteIn(BaseModel):
    email: str = Field(..., max_length=255)
    role: Literal["member", "manager"] = "member"


class RoleIn(BaseModel):
    role: Literal["member", "manager"]


def _teammate(p: Principal, user_id: int) -> dict:
    rows = K._store()._read("SELECT * FROM users WHERE id = ? AND workspace = ?", (user_id, p.workspace))
    if not rows:
        raise HTTPException(status_code=404, detail="no such person in your workspace")
    return dict(rows[0])


@router.get("/app/team")
def team(p: Principal = Depends(caller)):  # noqa: B008
    need(p, "manager")
    pending = K._store()._read("SELECT email, role, expires_at FROM invites WHERE workspace = ? AND kind = 'invite' AND"
                               " used_at IS NULL AND expires_at > ? ORDER BY created_at DESC", (p.workspace, A._now()))
    return {"people": A.list_users(p.workspace), "pending": pending, "you": -p.key_id if p.key_id < 0 else None,
            "email": A.email_on()}


@router.post("/app/team/invite")
def team_invite(req: InviteIn, request: Request, p: Principal = Depends(caller)):  # noqa: B008
    need(p, "manager")
    email = req.email.strip().lower()
    if not A.EMAIL.match(email):
        raise HTTPException(status_code=422, detail="That email address doesn't look right.")
    if A.find_user(email):
        raise HTTPException(status_code=409, detail="That email already has an account.")
    w = A.ensure_workspace(p.workspace, p.workspace, p.plan if p.key_id else "unlimited")
    link = f"{_base(request)}/login?invite={A.make_link('invite', workspace=p.workspace, role=req.role, email=email)}"
    sent = A.send_email(email, f"You're invited to {w['title']}'s assistant",
                        f"{_actor(p)} invited you to {w['title']}'s assistant.\n\nOpen this link within 3 days to "
                        f"choose your password:\n\n{link}\n")
    A.event(p.workspace, _actor(p), "invited", f"{email} as {req.role}")
    return {"link": link, "emailed": sent,
            "note": "The invitation was emailed." if sent else "Send this link to them. It works once, for 3 days."}


@router.patch("/app/team/{user_id}")
def team_role(user_id: int, req: RoleIn, p: Principal = Depends(caller)):  # noqa: B008
    need(p, "manager")
    u = _teammate(p, user_id)
    if u["role"] == "manager" and req.role != "manager":
        _keep_a_manager(p, user_id)
    with K._store()._tx() as c:
        c.execute("UPDATE users SET role = ? WHERE id = ?", (req.role, user_id))
    A.event(p.workspace, _actor(p), "role changed", f"{u['email']} is now a {req.role}")
    return {"ok": True}


def _keep_a_manager(p: Principal, leaving: int) -> None:
    others = [u for u in A.list_users(p.workspace) if u["role"] == "manager" and u["active"] and u["id"] != leaving]
    if not others:
        raise HTTPException(status_code=409, detail="A workspace needs at least one manager.")


@router.delete("/app/team/{user_id}")
def team_remove(user_id: int, p: Principal = Depends(caller)):  # noqa: B008
    need(p, "manager")
    u = _teammate(p, user_id)
    if u["role"] == "manager":
        _keep_a_manager(p, user_id)
    with K._store()._tx() as c:
        c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM push_devices WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    A.event(p.workspace, _actor(p), "removed", u["email"])
    return {"ok": True}


@router.post("/app/team/{user_id}/reset-link")
def team_reset_link(user_id: int, request: Request, p: Principal = Depends(caller)):  # noqa: B008
    need(p, "manager")
    u = _teammate(p, user_id)
    link = f"{_base(request)}/login?reset={A.make_link('reset', user_id=u['id'], hours=24)}"
    A.event(p.workspace, _actor(p), "password reset link made", u["email"])
    return {"link": link, "note": "Send this link to them. It works once, for 24 hours."}


@router.get("/app/activity")
def activity(limit: int = Query(100, ge=1, le=500), p: Principal = Depends(caller)):  # noqa: B008
    need(p, "manager")
    return {"events": K._store()._read("SELECT at, actor, action, detail FROM events WHERE workspace = ? ORDER BY id"
                                       " DESC LIMIT ?", (p.workspace, limit))}


# ------------------------------------------------------------------ the operator (the server's owner key)

def all_workspaces() -> list[dict]:
    """Every company on this server, for the person who runs it."""
    s = K._store()
    month = K.month()
    names = {r["name"]: dict(r) for r in s._read("SELECT name, title, plan, monthly_limit, created_at FROM workspaces")}
    for r in s._read("SELECT workspace, MIN(plan) AS plan, MAX(monthly_limit) AS monthly_limit, MIN(created_at) AS"
                     " created_at FROM api_keys WHERE revoked_at IS NULL AND role <> 'widget' GROUP BY workspace"):
        names.setdefault(r["workspace"], {"name": r["workspace"], "title": r["workspace"], **dict(r)})
    people = {r["workspace"]: r for r in s._read("SELECT workspace, COUNT(*) AS n, MIN(email) AS email, MAX(last_login_at)"
                                                 " AS seen FROM users GROUP BY workspace")}
    docs = {r["workspace"]: r["n"] for r in s._read("SELECT workspace, COUNT(*) AS n FROM kb_docs GROUP BY workspace")}
    used = {r["workspace"]: r["n"] for r in s._read("SELECT workspace, SUM(requests) AS n FROM key_usage WHERE month = ?"
                                                    " GROUP BY workspace", (month,))}
    out = []
    for name, w in names.items():
        p = people.get(name) or {}
        out.append({"name": name, "title": w.get("title") or name, "plan": w["plan"],
                    "monthly_limit": int(w["monthly_limit"] or 0), "created_at": w["created_at"],
                    "people": int(p.get("n") or 0), "contact": p.get("email"), "last_sign_in": p.get("seen"),
                    "documents": int(docs.get(name) or 0), "used_this_month": int(used.get(name) or 0)})
    return sorted(out, key=lambda w: w["created_at"] or "", reverse=True)


class PlanIn(BaseModel):
    plan: str = Field(..., max_length=40)


class EmailIn(BaseModel):
    email: str = Field(..., max_length=255)


@router.get("/admin/workspaces")
def admin_workspaces(p: Principal = Depends(caller)):  # noqa: B008
    need(p, "owner")
    return {"workspaces": all_workspaces(), "plans": K.plans(), "month": K.month()}


@router.post("/admin/workspaces/{name}/plan")
def admin_set_plan(name: str, req: PlanIn, p: Principal = Depends(caller)):  # noqa: B008
    """Put a company on a plan by hand: for a customer who paid you directly (UPI, bank transfer, cash)."""
    need(p, "owner")
    if req.plan not in K.plans():
        raise HTTPException(status_code=422, detail=f"unknown plan; plans: {', '.join(K.plans())}")
    if not any(w["name"] == name for w in all_workspaces()):
        raise HTTPException(status_code=404, detail="no such workspace")
    A.ensure_workspace(name, name, req.plan)
    K.set_plan(name, req.plan)
    A.event(name, "the service", "plan changed", f"{req.plan} (set by the operator)")
    return {"workspace": name, "plan": req.plan, "monthly_limit": K.plans()[req.plan]}


@router.post("/admin/users/reset-link")
def admin_reset_link(req: EmailIn, request: Request, p: Principal = Depends(caller)):  # noqa: B008
    """A one-time link for a customer who forgot their password (send it to the email they signed up with)."""
    need(p, "owner")
    u = A.find_user(req.email)
    if not u:
        raise HTTPException(status_code=404, detail="no account with that email")
    link = f"{_base(request)}/login?reset={A.make_link('reset', user_id=u['id'], hours=24)}"
    A.event(u["workspace"], "the service", "password reset link made", u["email"])
    return {"link": link, "note": "Send this to that email address only. It works once, for 24 hours."}


# ------------------------------------------------------------------ documents from a website

class UrlIn(BaseModel):
    url: str = Field(..., min_length=4, max_length=500)
    pages: int = Field(1, ge=1, le=50)


@router.post("/kb/url")
def kb_url(req: UrlIn, p: Principal = Depends(caller)):  # noqa: B008
    from cascade_guard.company import web
    from cascade_guard.company.saas import passages_limit
    need(p, "manager")
    url = req.url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    cap = passages_limit(p.plan) if p.key_id else None
    pages = min(req.pages, 5) if p.plan == "free" and p.key_id else req.pages
    try:
        out = web.import_site(p.workspace, url, max_pages=pages, by=_actor(p), max_chunks=cap)
    except web.FetchError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    if not out["added"]:
        raise HTTPException(status_code=422, detail=out["skipped"][0]["why"] if out["skipped"] else "Nothing was added.")
    A.event(p.workspace, _actor(p), "website added", f"{url} ({len(out['added'])} pages)")
    return out


# ------------------------------------------------------------------ your data

@router.get("/app/export")
def export(p: Principal = Depends(caller)):  # noqa: B008
    """Everything the workspace holds, as one JSON file."""
    need(p, "manager")
    s = K._store()
    ws = p.workspace
    docs = s._read("SELECT id, name, kind, created_at, created_by FROM kb_docs WHERE workspace = ? ORDER BY id", (ws,))
    for d in docs:
        d["text"] = "\n\n".join(r["text"] for r in s._read(
            "SELECT text FROM kb_chunks WHERE doc_id = ? ORDER BY ord", (d["id"],)))
    w = A.get_workspace(ws) or {}
    data = {"exported_at": A._now(), "workspace": {"name": ws, "title": w.get("title"), "plan": p.plan,
                                                   "settings": w.get("settings")},
            "people": A.list_users(ws), "keys": K.list_keys(ws), "documents": docs,
            "conversations": _conversations(ws, None, None, 100000, None),
            "leads": s._read("SELECT at, question, contact, handled_at FROM leads WHERE workspace = ? ORDER BY id", (ws,)),
            "usage": K.usage_report(ws),
            "activity": s._read("SELECT at, actor, action, detail FROM events WHERE workspace = ? ORDER BY id", (ws,))}
    A.event(ws, _actor(p), "data exported")
    return Response(json.dumps(data, indent=1, default=str, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{ws}-export.json"'})


class DeleteIn(BaseModel):
    confirm: str = Field(..., max_length=80)


@router.post("/app/delete-workspace")
def delete_workspace(req: DeleteIn, response: Response, p: Principal = Depends(caller)):  # noqa: B008
    """Delete the workspace and everything in it. Not for the server's own "default" workspace."""
    from cascade_guard.company import kb
    need(p, "manager")
    if p.key_id >= 0 or p.workspace == "default":
        raise HTTPException(status_code=403, detail="Sign in as a manager of the workspace to delete it.")
    if req.confirm.strip() != p.workspace:
        raise HTTPException(status_code=422, detail=f"Type the workspace's name ({p.workspace}) to confirm.")
    ws = p.workspace
    with K._store()._tx() as c:
        c.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE workspace = ?)", (ws,))
        for table in ("kb_chunks", "kb_docs", "chat_log", "leads", "events", "invites", "users", "api_keys",
                      "key_usage", "push_devices"):
            c.execute(f"DELETE FROM {table} WHERE workspace = ?", (ws,))  # noqa: S608 - fixed table names
        c.execute("DELETE FROM workspaces WHERE name = ?", (ws,))
    kb._cache.pop(ws, None)
    response.delete_cookie(A.COOKIE, path="/")
    return {"deleted": ws}


# ------------------------------------------------------------------ security page

@router.get("/security", response_class=HTMLResponse, include_in_schema=False)
def security_page():
    from cascade_guard.company.saas import _page, brand
    b = brand()
    return _page("Security", f"""
<p class="m">How {b} protects your company's data. This page describes what the software does; it is not a
certification.</p>
<h2>Your data stays yours</h2><ul>
<li>Each company has its own workspace. Documents, conversations, people and keys are never visible to another
workspace.</li>
<li>Your documents are not used to train AI models.</li>
<li>You can export everything as one file, and delete the whole workspace yourself, at any time.</li>
<li>For full control, the same software runs on your own server with a local AI model, so nothing leaves your
network.</li></ul>
<h2>Sign-in and access</h2><ul>
<li>Passwords are stored only as salted scrypt hashes. Sessions are random tokens in HttpOnly cookies, stored
hashed, and expire after 30 days.</li>
<li>Single sign-on with your company's identity provider (OpenID Connect: Google, Microsoft Entra, Okta).</li>
<li>Roles: members can ask questions; managers manage documents, people, keys and settings.</li>
<li>API keys are stored only as SHA-256 hashes and can be revoked at any time.</li>
<li>Sign-in attempts and API requests are rate-limited.</li></ul>
<h2>The website widget</h2><ul>
<li>Its key can only ask questions, only from your documents.</li>
<li>You can restrict it to your own domains.</li>
<li>It sets no cookies on your visitors.</li></ul>
<h2>Answers you can trust</h2><ul>
<li>Every answer is checked against the passages it came from: amounts, numbers, dates, quotations and
citations.</li>
<li>Answers that could not be fully confirmed are marked, and the passages used are shown.</li></ul>
<h2>Records</h2><ul>
<li>An activity log shows who signed in, added or removed documents, invited people or changed settings.</li>
<li>Conversation logging can be turned off; logs are deleted after {os.getenv("LOG_RETENTION_DAYS", "180")}
days.</li></ul>
<h2>Limits to know</h2><ul>
<li>The checks reduce wrong answers; they do not make an AI system perfect.</li>
<li>The hosted service has no third-party security certification (such as SOC 2 or ISO 27001).</li></ul>""")
