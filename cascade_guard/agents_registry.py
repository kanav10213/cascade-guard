"""Connected agents: register an outside agent once, test it, and use it by name in any chain step.

Two places hold connected agents:

  settings   the CASCADE_AGENTS environment variable, a JSON list, e.g.
             [{"name": "support-bot", "url": "https://support-bot.example.com/agent",
               "auth_env": "SUPPORT_BOT_TOKEN", "description": "our ticket agent"}]
             These survive restarts and redeploys (useful on hosts whose disk is wiped on restart) and
             cannot be changed from the dashboard.
  dashboard  added on the dashboard, the API or `cascade-guard agent add`; stored in the database.

A name in settings wins over the same name in the database. Tokens are never stored: an agent names
the server environment variable that holds its bearer token (auth_env).
"""
import json
import logging
import os
import re
import time

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

log = logging.getLogger("cascade_guard")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
PROBE = {"step": "connection test", "output": "text", "keys": [], "input": "ping", "original": "ping",
         "attempt": 0, "feedback": None,
         "instruction": "This is a connection test from Cascade Guard. Reply with any short text."}


class AgentDef(BaseModel):
    name: str = Field(min_length=1, max_length=40, description="lower-case letters, digits, '-' or '_'")
    url: str = Field(min_length=8, max_length=2000)
    auth_env: str = Field("", max_length=64, description="server environment variable holding the bearer token")
    timeout: float | None = Field(None, gt=0, le=300)
    description: str = Field("", max_length=300)

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        v = v.strip().lower().replace(" ", "-")
        if not NAME_RE.match(v):
            raise ValueError("agent names use lower-case letters, digits, '-' or '_' (like support-bot)")
        return v

    @model_validator(mode="after")
    def _check(self):
        self.url = self.url.strip()
        if not self.url.lower().startswith(("http://", "https://")):
            raise ValueError("the agent URL must start with http:// or https://")
        if self.auth_env and not re.match(r"^[A-Z_][A-Z0-9_]{0,63}$", self.auth_env):
            raise ValueError("auth_env is the NAME of an environment variable, like MY_AGENT_TOKEN, not the token")
        return self


def settings_agents() -> list[AgentDef]:
    raw = os.getenv("CASCADE_AGENTS", "").strip()
    if not raw:
        return []
    try:
        items = json.loads(raw)
        if not isinstance(items, list):
            raise ValueError("CASCADE_AGENTS must be a JSON list")
    except ValueError as e:
        log.warning("CASCADE_AGENTS is not valid JSON: %s", e)
        return []
    out = []
    for item in items:
        try:
            out.append(AgentDef.model_validate(item))
        except ValidationError as e:
            log.warning("skipping an agent in CASCADE_AGENTS: %s", e)
    return out


def _store():
    from cascade_guard.storage.db import get_store
    return get_store()


def all_agents() -> list[dict]:
    """Every connected agent, settings first. Never includes a token."""
    seen, out = set(), []
    for a in settings_agents():
        if a.name not in seen:
            seen.add(a.name)
            out.append({**a.model_dump(), "source": "settings", "token_set": bool(a.auth_env and os.getenv(a.auth_env))})
    store = _store()
    for row in (store.list_agents() if store is not None else []):
        a = AgentDef.model_validate(row["definition"])
        if a.name not in seen:
            seen.add(a.name)
            out.append({**a.model_dump(), "source": "dashboard", "updated_at": row["updated_at"],
                        "token_set": bool(a.auth_env and os.getenv(a.auth_env))})
    return out


def find_agent(name: str) -> AgentDef | None:
    name = name.strip().lower()
    for a in settings_agents():
        if a.name == name:
            return a
    store = _store()
    row = store.get_agent(name) if store is not None else None
    return AgentDef.model_validate(row["definition"]) if row else None


def test_agent(agent: AgentDef, http_client=None) -> dict:
    """Send one probe request and say in plain words whether the agent works."""
    import httpx

    from cascade_guard.agent_call import TIMEOUT_DEFAULT, check_url
    result: dict = {"agent": agent.name, "url": agent.url, "ok": False}
    problem = check_url(agent.url)
    if problem:
        return {**result, "stage": "address", "hint": f"Cascade Guard will not call this address: {problem}."}
    headers = {"Content-Type": "application/json", "User-Agent": "cascade-guard"}
    if agent.auth_env:
        token = os.getenv(agent.auth_env)
        if not token:
            return {**result, "stage": "token",
                    "hint": f"This agent needs the token in {agent.auth_env}, but this server has no {agent.auth_env} "
                            "setting. Add it to the server's environment (for example Render > Environment)."}
        headers["Authorization"] = f"Bearer {token}"
    client = http_client or httpx.Client(timeout=agent.timeout or TIMEOUT_DEFAULT, follow_redirects=False)
    t0 = time.perf_counter()
    try:
        r = client.post(agent.url, json=PROBE, headers=headers)
    except httpx.TimeoutException:
        return {**result, "stage": "timeout", "seconds": round(time.perf_counter() - t0, 1),
                "hint": "The agent did not answer in time. A free host may be waking up; try again in a minute, "
                        "or raise the timeout."}
    except httpx.HTTPError as e:
        return {**result, "stage": "connect", "hint": f"Could not reach the agent ({type(e).__name__}: {e}). "
                                                      "Check the URL and that the agent is running."}
    finally:
        if http_client is None:
            client.close()
    result["seconds"] = round(time.perf_counter() - t0, 2)
    result["status"] = r.status_code
    if r.status_code in (401, 403):
        return {**result, "stage": "auth", "hint": "The agent refused the token. Check that both sides have the same "
                                                   "token and that the token variable name is right."}
    if r.status_code == 404:
        return {**result, "stage": "path", "hint": "The agent's server answered 'not found'. Check the path at the end "
                                                   "of the URL (the example agent uses /agent)."}
    if r.status_code >= 400:
        return {**result, "stage": "http", "hint": f"The agent answered HTTP {r.status_code}: {r.text[:200]}"}
    try:
        data = r.json()
    except ValueError:
        return {**result, "ok": bool(r.text.strip()), "stage": "format", "reply": r.text[:300],
                "hint": "The agent answered with plain text instead of JSON. That works for text steps; "
                        'for JSON steps it should reply {"output": {...}}.'}
    reply = data.get("output") if isinstance(data, dict) and "output" in data else data
    result["reply"] = reply if isinstance(reply, str) else json.dumps(reply)[:300]
    if isinstance(data, dict) and "output" in data:
        if reply in (None, "", [], {}):
            return {**result, "ok": True, "stage": "empty",
                    "hint": "Connected and in the right format, but this answer was empty. In a chain the guard "
                            "rejects empty answers and asks again with the reason."}
        return {**result, "ok": True, "stage": "done", "hint": "The agent answered in the right format. It is ready to use."}
    return {**result, "ok": True, "stage": "format",
            "hint": 'The agent answered, but not as {"output": ...}. Text steps will still work; '
                    "wrap the answer in an output field to be safe."}
