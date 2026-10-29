"""Call an outside agent over HTTP: the "your agent" kind of chain step.

Any agent, in any language or framework, can be a step if it answers one POST request:

    request  {"step": "summarise", "instruction": "...", "output": "text" | "json", "keys": [...],
              "input": <previous step's output or the chain input>,
              "original": "<the chain's input>", "attempt": 0,
              "feedback": null | {"reason": "<why the checker rejected the last answer>", "previous_output": ...}}
    reply    {"output": "<text>"}            for a text step
             {"output": {"key": ...}}  or  {"key": ...}   for a JSON step

Cascade Guard checks the reply like any other step, retries the agent with the feedback, and stops the
chain if it stays wrong. A bearer token for the agent is never stored in the chain: the step names an
environment variable on the server (auth_env) and the token is read from there at run time.

Safety: the server refuses agent URLs that point at link-local or cloud-metadata addresses, and on a
server with access keys (a shared server) also private and loopback addresses, unless
AGENT_ALLOW_PRIVATE=1. Without keys (only this computer can use it) local agents are allowed.
"""
import ipaddress
import os
import socket
from urllib.parse import urlparse

TIMEOUT_DEFAULT = 90.0  # free hosts can take ~60 s to wake a sleeping agent
MAX_REPLY = 2_000_000


class AgentCallError(RuntimeError):
    """The agent could not be called or did not answer usably (an infrastructure problem, not a wrong answer)."""
    infrastructure = True


def _private_allowed() -> bool:
    from cascade_guard.auth import auth_enabled
    return os.getenv("AGENT_ALLOW_PRIVATE") == "1" or not auth_enabled()


def check_url(url: str) -> str | None:
    """Why this agent URL is refused, or None. Resolves the host name, so a name that points
    at an internal address is refused too."""
    try:
        u = urlparse(url)
    except ValueError:
        return "not a valid URL"
    if u.scheme not in ("http", "https") or not u.hostname:
        return "the agent URL must start with http:// or https:// and name a host"
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError:
        return f"cannot find the host {u.hostname}"
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        mapped = getattr(ip, "ipv4_mapped", None)
        ip = mapped or ip
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            return f"the agent URL points at a blocked address ({ip})"
        if (ip.is_private or ip.is_loopback) and not _private_allowed():
            return (f"the agent URL points at a private address ({ip}); on a shared server set "
                    "AGENT_ALLOW_PRIVATE=1 to allow agents inside your own network")
    return None


def call_agent(step, step_input, original: str, attempt: int, feedback, http_client=None):
    """POST the request to the step's agent and return its output (text, a dict, or None when the
    reply has no usable output). Raises AgentCallError when the call itself fails."""
    import httpx
    problem = check_url(step.url)
    if problem:
        raise AgentCallError(f"agent for step {step.name!r}: {problem}")
    headers = {"Content-Type": "application/json", "User-Agent": "cascade-guard"}
    if step.auth_env:
        token = os.getenv(step.auth_env)
        if not token:
            raise AgentCallError(f"agent for step {step.name!r}: the server has no {step.auth_env} set")
        headers["Authorization"] = f"Bearer {token}"
    body = {"step": step.name, "instruction": step.instruction, "output": step.output, "keys": step.keys,
            "input": step_input, "original": original, "attempt": attempt, "feedback": feedback}
    client = http_client or httpx.Client(timeout=step.timeout or TIMEOUT_DEFAULT, follow_redirects=False)
    try:
        r = client.post(step.url, json=body, headers=headers)
    except httpx.TimeoutException as e:
        raise AgentCallError(f"agent for step {step.name!r} did not answer within {step.timeout or TIMEOUT_DEFAULT:.0f} s "
                             "(a free host may be waking up; try again in a minute or raise the timeout)") from e
    except httpx.HTTPError as e:
        raise AgentCallError(f"agent for step {step.name!r} could not be reached: {type(e).__name__}: {e}") from e
    finally:
        if http_client is None:
            client.close()
    if r.status_code >= 400:
        raise AgentCallError(f"agent for step {step.name!r} answered HTTP {r.status_code}: {r.text[:200]}")
    if len(r.content) > MAX_REPLY:
        raise AgentCallError(f"agent for step {step.name!r} sent more than {MAX_REPLY} bytes")
    try:
        data = r.json()
    except ValueError:
        text = r.text.strip()
        return text if step.output == "text" and text else None
    return extract_output(step, data)


def extract_output(step, data):
    """Accept {"output": ...} or, for JSON steps, the object itself."""
    if isinstance(data, dict) and "output" in data:
        out = data["output"]
    else:
        out = data
    if step.output == "text":
        if isinstance(out, str):
            return out.strip()
        return None
    if isinstance(out, dict):
        return {k: out.get(k) for k in step.keys} if any(k in out for k in step.keys) else None
    return None
