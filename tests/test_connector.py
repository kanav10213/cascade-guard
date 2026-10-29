"""The Claude web connector: MCP over HTTP at /mcp/<secret>, checked with a real MCP client on a real server."""
import asyncio
import contextlib
import json
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from cascade_guard.api import app

TOKEN = "t" * 20 + "-secret-connector-link-value"
pytest.importorskip("mcp")
try:  # the web connector needs an mcp package with the streamable-HTTP transport (1.8 or newer)
    import mcp.client.streamable_http  # noqa: F401
    HTTP = True
except ImportError:
    HTTP = False
needs_http = pytest.mark.skipif(not HTTP, reason="the installed mcp package has no streamable-HTTP transport")


@contextlib.contextmanager
def live_server():
    import uvicorn
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="on"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        t.join(5)


def _mcp(url, calls):
    try:
        from mcp.client.streamable_http import streamable_http_client as client
    except ImportError:  # mcp 1.x
        from mcp.client.streamable_http import streamablehttp_client as client  # type: ignore[no-redef]
    from mcp import ClientSession

    async def go():
        async with client(url) as streams:
            async with ClientSession(streams[0], streams[1]) as s:
                await s.initialize()
                names = sorted(t.name for t in (await s.list_tools()).tools)
                out = [json.loads((await s.call_tool(n, a)).content[0].text) for n, a in calls]
                return names, out
    return asyncio.run(go())


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("API_KEY", "adm")            # a shared server, like the one on Render
    monkeypatch.setenv("MCP_CONNECTOR_TOKEN", TOKEN)
    monkeypatch.setenv("LLM_PROVIDER", "fake")


@needs_http
def test_claude_can_check_a_bill_through_the_secret_link(keys):
    with live_server() as base:
        names, (bill, maths, demo) = _mcp(f"{base}/mcp/{TOKEN}", [
            ("check_bill", {"bill_text": "Use the cascade guard tool to check this invoice: 3 x $4.00, 2 x $1.50, "
                                         "total $16.00"}),
            ("check_math", {"claim": "the average of 3, 7, 2, 9 is 5.5"}),
            ("show_cascade_demo", {"example": 0}),
        ])
        import httpx
        wrong = httpx.post(f"{base}/mcp/not-the-secret", json={})
        info = httpx.get(f"{base}/connector", headers={"X-API-Key": "adm"}).json()
        viewer_info = httpx.get(f"{base}/connector").status_code
    assert names == ["check_bill", "check_everyday", "check_invoice", "check_math", "guard_status", "list_agent_chains",
                     "run_agent_chain", "show_cascade_demo"]          # viewer: nothing that costs or reads the queue
    assert bill["status"] == "wrong" and bill["total"] == 15.0
    assert maths["status"] == "wrong" and maths["value"] == "5.25"
    assert "$390.34" in demo["summary"]
    assert wrong.status_code == 404
    assert info["enabled"] and info["path"] == f"/mcp/{TOKEN}" and info["role"] == "viewer"
    assert viewer_info == 401


@needs_http
def test_admin_role_adds_the_paid_tools(keys, monkeypatch):
    monkeypatch.setenv("MCP_CONNECTOR_ROLE", "admin")
    with live_server() as base:
        names, _ = _mcp(f"{base}/mcp/{TOKEN}", [])
    assert {"verify_output", "list_review_queue"} <= set(names)


def test_off_without_a_long_enough_secret(monkeypatch):
    monkeypatch.setenv("API_KEY", "adm")
    for value in ("", "short-token"):
        monkeypatch.setenv("MCP_CONNECTOR_TOKEN", value)
        with TestClient(app) as c:  # runs the start-up, like the real server
            assert c.post("/mcp/short-token", json={}).status_code == 404
            st = c.get("/connector", headers={"X-API-Key": "adm"}).json()
            assert st["enabled"] is False and "MCP_CONNECTOR_TOKEN" in st["problem"]


def test_the_secret_never_reaches_the_logs_or_the_routes(keys):
    seen = {}

    async def spy(scope, receive, send):
        seen["path"] = scope["path"]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    from cascade_guard import connector
    from cascade_guard.connector import ConnectorMiddleware
    outer: dict = {}

    async def run():
        scope = {"type": "http", "path": f"/mcp/{TOKEN}", "raw_path": f"/mcp/{TOKEN}".encode(), "method": "POST",
                 "headers": [], "query_string": b"", "client": ("1.2.3.4", 5)}
        connector._state["app"] = spy
        try:
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(m):
                outer.setdefault("status", m.get("status"))
            await ConnectorMiddleware(None)(scope, receive, send)
        finally:
            connector._state["app"] = None
        return scope

    scope = asyncio.run(run())
    assert seen["path"] == "/" and TOKEN not in scope["path"] and TOKEN.encode() not in scope["raw_path"]
    assert outer["status"] == 200
