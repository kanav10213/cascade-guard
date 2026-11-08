"""Who is calling: customer keys, workspaces, plans and monthly limits.

Two kinds of key open the company product (the chat, the documents, the OpenAI-format API):

    the server's own keys (environment)    API_KEY = owner, REVIEWER_KEY = manager, VIEWER_KEY = member,
                                           all in the "default" workspace; the owner also manages customers
    customer keys (database, cg_...)       made with `cascade-guard keys create` or the admin page, or by a
                                           payment; each belongs to one workspace, with a plan and a limit

A workspace is one company (or team): its own documents and its own usage. Roles inside it:
    member    chat and use the API
    manager   also add and remove the workspace's documents and see its usage
    owner     everything, every workspace (only the server's API_KEY)

Keys are sent as X-API-Key or as "Authorization: Bearer <key>" (what OpenAI-format apps send). Only a SHA-256
hash of a customer key is stored. Customer keys open only the company product, never the older admin routes.

Plans (answers a month; 0 = no limit). Override with PLANS='{"starter": 1000, ...}' in the environment:
    free 50 · starter 1,000 · team 10,000 · unlimited 0
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Header, HTTPException

DEFAULT_PLANS = {"free": 50, "starter": 1000, "team": 10000, "unlimited": 0}
ROLES = ("member", "manager")
RANK = {"member": 1, "manager": 2, "owner": 3}
WORKSPACE_RX = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")


def plans() -> dict[str, int]:
    out = dict(DEFAULT_PLANS)
    raw = os.getenv("PLANS")
    if raw:
        try:
            extra = json.loads(raw)
            out.update({str(k): max(0, int(v)) for k, v in extra.items()})
        except (ValueError, TypeError, AttributeError):
            pass  # a broken PLANS setting must not lock everyone out; the defaults stay
    return out


@dataclass
class Principal:
    workspace: str
    role: str                 # member | manager | owner
    key_id: int = 0           # 0 for the server's own keys
    name: str = ""
    plan: str = "unlimited"
    monthly_limit: int = 0

    def at_least(self, role: str) -> bool:
        return RANK[self.role] >= RANK[role]

    def public(self) -> dict:
        return {"workspace": self.workspace, "role": self.role, "name": self.name, "plan": self.plan,
                "monthly_limit": self.monthly_limit}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8", "surrogateescape")).hexdigest()


def key_from(x_api_key: str | None, authorization: str | None) -> str:
    """The key a request carries: X-API-Key, else "Authorization: Bearer <key>"."""
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization[:7].lower() == "bearer ":
        return authorization[7:].strip()
    return ""


def norm_workspace(name: str) -> str:
    ws = re.sub(r"[^a-z0-9._-]+", "-", (name or "").strip().lower()).strip("-.")[:63]
    if not ws or not WORKSPACE_RX.match(ws):
        raise ValueError("a workspace name needs letters or digits, e.g. acme or sahyadri-tools")
    return ws


def _store():
    from cascade_guard.storage.db import get_store
    store = get_store()
    if store is None:
        raise HTTPException(status_code=503, detail="This needs the database (CASCADE_DB is off).")
    return store


# ---------------------------------------------------------------- key management

def create_key(name: str, workspace: str, *, role: str = "member", plan: str = "starter",
               monthly_limit: int | None = None, email: str | None = None, ref: str | None = None,
               customer: str | None = None, keep_for_reveal: bool = False, store=None) -> tuple[str, dict]:
    """A new customer key: (the key, shown once; its record)."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    all_plans = plans()
    if plan not in all_plans:
        raise ValueError(f"unknown plan {plan!r}; plans: {', '.join(all_plans)}")
    ws = norm_workspace(workspace)
    limit = all_plans[plan] if monthly_limit is None else max(0, int(monthly_limit))
    key = "cg_" + secrets.token_urlsafe(30)
    store = store or _store()
    with store._tx() as c:
        kid = c.insert("INSERT INTO api_keys (key_hash, prefix, name, workspace, role, plan, monthly_limit, email, ref,"
                       " customer, reveal, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (digest(key), key[:10], name.strip()[:100] or ws, ws, role, plan, limit, email, ref, customer,
                        key if keep_for_reveal else None, _now()))
    return key, get_key(kid, store=store) or {}


def get_key(key_id: int, store=None) -> dict | None:
    rows = (store or _store())._read("SELECT * FROM api_keys WHERE id = ?", (key_id,))
    return _public_row(rows[0]) if rows else None


def _public_row(r: dict) -> dict:
    r = dict(r)
    r.pop("key_hash", None)
    r["has_reveal"] = bool(r.pop("reveal", None))
    r["active"] = not r.get("revoked_at")
    return r


def list_keys(workspace: str | None = None, store=None) -> list[dict]:
    store = store or _store()
    m = month()
    q = ("SELECT k.*, COALESCE(u.requests, 0) AS used_this_month FROM api_keys k LEFT JOIN key_usage u"
         " ON u.key_id = k.id AND u.workspace = k.workspace AND u.month = ?")
    args: tuple = (m,)
    if workspace:
        q += " WHERE k.workspace = ?"
        args += (workspace,)
    return [_public_row(r) for r in store._read(q + " ORDER BY k.id DESC", args)]


def revoke_key(key_id: int, workspace: str | None = None, store=None) -> bool:
    store = store or _store()
    q, args = "UPDATE api_keys SET revoked_at = ?, reveal = NULL WHERE id = ? AND revoked_at IS NULL", [_now(), key_id]
    if workspace:
        q += " AND workspace = ?"
        args.append(workspace)
    with store._tx() as c:
        return c.execute(q, args).rowcount > 0


def revoke_by_customer(customer: str, store=None) -> int:
    store = store or _store()
    with store._tx() as c:
        return c.execute("UPDATE api_keys SET revoked_at = ?, reveal = NULL WHERE customer = ? AND revoked_at IS NULL",
                         (_now(), customer)).rowcount


def reveal_once(ref: str, store=None) -> dict | None:
    """The key made for a payment, returned once (then forgotten): for the page shown after paying."""
    store = store or _store()
    with store._tx() as c:
        rows = c.execute("SELECT id, reveal, name, workspace, plan, monthly_limit FROM api_keys WHERE ref = ? AND"
                         " reveal IS NOT NULL AND revoked_at IS NULL", (ref,)).fetchall()
        if not rows:
            return None
        row = dict(rows[0])
        c.execute("UPDATE api_keys SET reveal = NULL WHERE id = ?", (row["id"],))
    return {"key": row["reveal"], "name": row["name"], "workspace": row["workspace"], "plan": row["plan"],
            "monthly_limit": row["monthly_limit"]}


def find_by_ref(ref: str, store=None) -> dict | None:
    rows = (store or _store())._read("SELECT * FROM api_keys WHERE ref = ?", (ref,))
    return _public_row(rows[0]) if rows else None


# ---------------------------------------------------------------- who is calling

_ENV_ROLES = (("API_KEY", "owner"), ("REVIEWER_KEY", "manager"), ("VIEWER_KEY", "member"))


def principal_for(key: str, store=None) -> Principal | None:
    """The principal a key stands for, or None."""
    from cascade_guard.auth import auth_enabled
    given = (key or "").encode("utf-8", "surrogateescape")
    for env, role in _ENV_ROLES:
        k = os.getenv(env)
        if k and given and secrets.compare_digest(given, k.encode("utf-8", "surrogateescape")):
            return Principal("default", role, 0, {"owner": "owner", "manager": "manager", "member": "member"}[role])
    if key and key.startswith("cg_"):
        from cascade_guard.storage.db import get_store
        store = store or get_store()
        if store is not None:
            rows = store._read("SELECT id, name, workspace, role, plan, monthly_limit FROM api_keys WHERE key_hash = ?"
                               " AND revoked_at IS NULL", (digest(key),))
            if rows:
                r = rows[0]
                return Principal(r["workspace"], r["role"], int(r["id"]), r["name"], r["plan"],
                                 int(r["monthly_limit"]))
    if not auth_enabled():  # no keys set: only this computer can reach the server (ops.py), as its owner
        return Principal("default", "owner", 0, "this computer")
    return None


def caller(x_api_key: str | None = Header(default=None),
           authorization: str | None = Header(default=None)) -> Principal:
    """FastAPI dependency: the caller, from either header."""
    p = principal_for(key_from(x_api_key, authorization))
    if p is None:
        raise HTTPException(status_code=401, detail="invalid or missing key (send X-API-Key or Authorization: Bearer)")
    return p


def need(p: Principal, role: str) -> None:
    if not p.at_least(role):
        raise HTTPException(status_code=403, detail=f"this needs a {role} key; yours is a {p.role} key")


# ---------------------------------------------------------------- limits and usage

def used(p: Principal, store=None) -> int:
    rows = (store or _store())._read("SELECT requests FROM key_usage WHERE key_id = ? AND workspace = ? AND month = ?",
                                     (p.key_id, p.workspace, month()))
    return int(rows[0]["requests"]) if rows else 0


def check_quota(p: Principal, store=None) -> None:
    """Refuse when the key has used its answers for this month."""
    if p.monthly_limit <= 0 or p.key_id == 0:
        return
    n = used(p, store)
    if n >= p.monthly_limit:
        raise HTTPException(status_code=429, detail=f"This key has used its {p.monthly_limit} answers for {month()} "
                                                    f"(plan {p.plan}). It starts again next month, or upgrade the plan.")


def record(p: Principal, ai_calls: int = 0, tokens: int = 0, store=None) -> None:
    store = store or _store()
    with store._tx() as c:
        c.execute("INSERT INTO key_usage (key_id, workspace, month, requests, ai_calls, tokens) VALUES (?, ?, ?, 1, ?, ?)"
                  " ON CONFLICT (key_id, workspace, month) DO UPDATE SET requests = key_usage.requests + 1,"
                  " ai_calls = key_usage.ai_calls + excluded.ai_calls, tokens = key_usage.tokens + excluded.tokens",
                  (p.key_id, p.workspace, month(), int(ai_calls), int(tokens)))
        if p.key_id:
            c.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (_now(), p.key_id))


def usage_report(workspace: str | None = None, store=None) -> list[dict]:
    store = store or _store()
    q = "SELECT workspace, month, SUM(requests) AS answers, SUM(ai_calls) AS ai_calls, SUM(tokens) AS tokens FROM key_usage"
    args: tuple = ()
    if workspace:
        q += " WHERE workspace = ?"
        args = (workspace,)
    return [dict(r) for r in store._read(q + " GROUP BY workspace, month ORDER BY month DESC, workspace", args)]
