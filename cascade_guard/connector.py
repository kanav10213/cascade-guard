"""Use Cascade Guard from Claude on the web (a claude.ai custom connector) through a secret link.

Set MCP_CONNECTOR_TOKEN on the server to a long random value (at least 24 characters). The server then
answers MCP over HTTP at

    https://<your server>/mcp/<that value>

In Claude: Customize > Connectors > Add custom connector, paste that link, choose "No sign-in", Add. Then
turn it on in a chat with the "+" button > Connectors, and ask, for example: "Use Cascade Guard to check
this invoice: 3 x $4.00, 2 x $1.50, total $16.00".

Anyone who has the link can use the connector's tools, so keep it like a password; set a new value to
revoke the old link. The value never appears in the server's logs (the path is logged as /mcp/(connector)).

    MCP_CONNECTOR_ROLE      viewer (default): exact bill and maths checks, the demo, free chain runs on the
                            simulator, status. reviewer: also the review queue (read-only). admin: also
                            verify_output and chain runs on the configured model (these cost money).
    MCP_RATE_LIMIT_PER_MIN  requests per minute per caller address, default 120
"""
import contextlib
import hmac
import json
import logging
import os

log = logging.getLogger("cascade_guard")
PREFIX = "/mcp/"
MIN_TOKEN = 24
ROLES = ("viewer", "reviewer", "admin")
_state: dict = {"app": None, "role": None, "error": None}


def token() -> str | None:
    t = os.getenv("MCP_CONNECTOR_TOKEN", "").strip()
    return t if len(t) >= MIN_TOKEN else None


def role() -> str:
    r = os.getenv("MCP_CONNECTOR_ROLE", "viewer").strip().lower()
    return r if r in ROLES else "viewer"


def status() -> dict:
    """For the admin page: is the connector on, with which tools, and why not if it is off."""
    raw = os.getenv("MCP_CONNECTOR_TOKEN", "").strip()
    why: str | None
    if not raw:
        why = "MCP_CONNECTOR_TOKEN is not set"
    elif len(raw) < MIN_TOKEN:
        why = f"MCP_CONNECTOR_TOKEN is shorter than {MIN_TOKEN} characters"
    else:
        why = _state["error"] or (None if _state["app"] is not None else "the connector has not started yet")
    return {"enabled": _state["app"] is not None and token() is not None, "role": role(), "problem": why}


def _http_app(remote):
    """The MCP server's streamable-HTTP app, stateless, answering at its own root."""
    from mcp.server.transport_security import TransportSecuritySettings
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)  # the secret path is the check
    try:  # mcp 2.x
        return remote.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True,
                                          transport_security=security, max_request_body_size=1_000_000)
    except TypeError:  # mcp 1.x keeps these in settings
        remote.settings.streamable_http_path = "/"
        remote.settings.stateless_http = True
        remote.settings.json_response = True
        if hasattr(remote.settings, "transport_security"):
            remote.settings.transport_security = security
        return remote.streamable_http_app()


@contextlib.asynccontextmanager
async def lifespan():
    """Start the connector with the web server when MCP_CONNECTOR_TOKEN is set (a no-op otherwise)."""
    _state.update(app=None, role=None, error=None)
    if os.getenv("MCP_CONNECTOR_TOKEN", "").strip() and token() is None:
        log.warning("MCP_CONNECTOR_TOKEN is shorter than %d characters, so the Claude connector stays off", MIN_TOKEN)
    if token() is None:
        yield
        return
    try:
        from cascade_guard.mcp_server import build_remote_server
        remote = build_remote_server(role())
        if not hasattr(remote, "streamable_http_app"):
            raise RuntimeError("the installed mcp package is too old for the web connector (needs mcp 1.8 or newer)")
        app = _http_app(remote)
        manager = remote.session_manager
    except Exception as e:  # the connector must never stop the web server from starting
        _state["error"] = f"{type(e).__name__}: {e}"
        log.warning("the Claude connector could not start: %s", _state["error"])
        yield
        return
    async with manager.run():
        _state.update(app=app, role=role())
        log.info("Claude connector on at /mcp/(connector) as %s", role())
        try:
            yield
        finally:
            _state.update(app=None)


async def _reply(send, status_code: int, body: dict, headers: list | None = None):
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status_code,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode()),
                            (b"x-content-type-options", b"nosniff"), (b"cache-control", b"no-store")]
                + (headers or [])})
    await send({"type": "http.response.body", "body": data})


class ConnectorMiddleware:
    """Pure ASGI: requests to /mcp/<secret> go to the MCP app; a wrong or missing secret looks like any
    unknown page (404). Everything else goes on to the normal web app."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith(PREFIX):
            return await self.app(scope, receive, send)
        expected = token()
        given, _, tail = scope["path"][len(PREFIX):].partition("/")
        if expected is None or not hmac.compare_digest(given.encode("utf-8", "surrogateescape"),
                                                       expected.encode("utf-8", "surrogateescape")):
            return await _reply(send, 404, {"detail": "Not Found"})
        scope["path"] = PREFIX + "(connector)"  # the access log shows this, never the secret
        scope["raw_path"] = scope["path"].encode()
        from cascade_guard.ops import _limit, limiter
        client = (scope.get("client") or ("?", 0))[0]
        ok, wait = limiter.allow("mcp:" + str(client), _limit("MCP_RATE_LIMIT_PER_MIN", 120))
        if not ok:
            return await _reply(send, 429, {"detail": f"too many requests; try again in {wait} s"},
                                [(b"retry-after", str(wait).encode())])
        app = _state["app"]
        if app is None:
            return await _reply(send, 503, {"detail": "The Claude connector is starting; try again in a moment."})
        inner = dict(scope, path="/" + tail, raw_path=("/" + tail).encode(), root_path="")
        await app(inner, receive, send)
