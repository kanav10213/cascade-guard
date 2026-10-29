"""Operations: request limits, audit log, metrics, readiness.

Settings (environment or .env):
    RATE_LIMIT_PER_MIN       requests per minute per valid key (else per address), default 120
    LLM_RATE_LIMIT_PER_MIN   per minute on the routes that call the model, default 20
    AUDIT                    "on" (default) | "off"; records every POST and every refused request
    ALLOW_OPEN_NETWORK       with NO keys set, only this computer may call the server; "1" lifts the
                             address check (used by docker-compose, where the port is bound to 127.0.0.1)
    ALLOWED_HOSTS            with NO keys set, extra Host names accepted besides localhost (comma list)
    MAX_BODY_BYTES           larger request bodies get 413, counted as they arrive (default 8,000,000)

With no keys, every caller is admin, so the server also refuses requests that came through a proxy
or tunnel (Forwarded / X-Forwarded-For / X-Real-IP headers) and requests whose Host header is not
localhost (this blocks DNS-rebinding pages in the browser).

Routes:
    GET /health/ready   database reachable? model configured?
    GET /metrics        Prometheus text format (viewer key when keys are on)
    GET /audit          latest audit rows (admin key)
"""
import hashlib
import ipaddress
import logging
import os
import threading
import time
from collections import Counter, defaultdict, deque

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse

from cascade_guard.auth import auth_enabled, require, role_for_key

EXEMPT = {"/", "/assistant", "/health", "/health/ready", "/favicon.ico"}
LLM_PATHS = ("/llm/pipeline", "/llm/verify", "/llm/check")  # chain runs on a real model use the same limit


def _limit(name, default):
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


class RateLimiter:
    """Sliding one-minute window per identity. Idle identities are dropped, so
    memory stays bounded."""

    MAX_IDENTITIES = 10_000

    def __init__(self):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, ident: str, limit: int, now: float | None = None):
        now = now or time.monotonic()
        with self._lock:
            if len(self._hits) > self.MAX_IDENTITIES:
                for k in [k for k, q in self._hits.items() if not q or now - q[-1] >= 60]:
                    del self._hits[k]
            q = self._hits[ident]
            while q and now - q[0] >= 60:
                q.popleft()
            if len(q) >= limit:
                return False, max(1, int(60 - (now - q[0])) + 1)
            q.append(now)
            return True, 0

    def reset(self):
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


class Metrics:
    def __init__(self):
        self.requests = Counter()       # (method, path, status) -> n
        self.seconds = Counter()        # path -> total seconds
        self.limited = 0
        self._lock = threading.Lock()

    def record(self, method, path, status, secs):
        with self._lock:
            self.requests[(method, path, status)] += 1
            self.seconds[path] += secs


metrics = Metrics()


def _role_of(key: str):
    """The role a key maps to, or None (no exception: used for logging and limits)."""
    return role_for_key(key)


def _is_local(host: str) -> bool:
    """True for loopback addresses, including IPv4-mapped IPv6 (::ffff:127.0.0.1)."""
    if host in ("localhost", "testclient"):  # testclient: Starlette's test client
        return True
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or bool(mapped and mapped.is_loopback)


PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-real-ip")


def _host_name(host_header: str) -> str:
    h = host_header.strip().lower()
    if h.startswith("["):  # [::1]:8000
        return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def _local_host_header(host_header: str) -> bool:
    name = _host_name(host_header)
    extra = {x.strip().lower() for x in os.getenv("ALLOWED_HOSTS", "").split(",") if x.strip()}
    return name in ("localhost", "127.0.0.1", "::1") or name.endswith(".localhost") or name in extra


def keyless_refusal(request: Request, client: str) -> str | None:
    """Why a request must be refused while no access keys are set, or None if it may go ahead."""
    if auth_enabled() or request.url.path == "/health":
        return None
    if os.getenv("ALLOW_OPEN_NETWORK") != "1" and not _is_local(client):
        return "it came from another computer"
    if any(h in request.headers for h in PROXY_HEADERS):
        return "it came through a proxy or tunnel"
    if not _local_host_header(request.headers.get("host", "")):
        return "it was addressed to a name other than localhost"
    return None


class BodyLimit:
    """Pure ASGI middleware: refuse bodies over MAX_BODY_BYTES while they arrive, before any route or
    key check reads them (Content-Length alone is not enough: chunked uploads have none)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        limit = _limit("MAX_BODY_BYTES", 8_000_000)
        length = dict(scope.get("headers") or []).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > limit):
            return await _too_large(scope, receive, send)
        chunks, total = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None
            body = message.get("body", b"")
            total += len(body)
            if total > limit:
                return await _too_large(scope, receive, send)
            chunks.append(body)
            if not message.get("more_body"):
                break
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)


async def _too_large(scope, receive, send):
    metrics.record(scope.get("method", "?"), scope.get("path", "?"), 413, 0.0)
    await _secure(JSONResponse({"detail": "request body too large"}, status_code=413))(scope, receive, send)


def _route_path(request: Request) -> str:
    """The route template (/runs/{run_id}) rather than the raw path, to keep metrics small."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path


def install(app):
    """Add the request checks; BodyLimit is added last so it runs first."""
    @app.middleware("http")
    async def ops_middleware(request: Request, call_next):
        path = request.url.path
        key = request.headers.get("x-api-key", "")
        client = request.client.host if request.client else "?"
        why = keyless_refusal(request, client)
        if why:
            await _audit(request, key, client, 403, 0)
            return _secure(JSONResponse({"detail": f"Refused because {why}. This server has no API_KEY set, so it "
                                                   "only answers its own computer at http://localhost. Set API_KEY "
                                                   "(and give others REVIEWER_KEY or VIEWER_KEY) to share it."},
                                        status_code=403))
        valid = bool(key) and _role_of(key) is not None
        # only valid keys get their own bucket; junk keys share the caller's address bucket
        ident = ("key:" + hashlib.sha256(key.encode("utf-8", "surrogateescape")).hexdigest()[:12]) if valid \
            else ("addr:" + client)
        if path not in EXEMPT and not path.startswith(("/docs", "/openapi")):
            ok, wait = limiter.allow(ident, _limit("RATE_LIMIT_PER_MIN", 120))
            if ok and path in LLM_PATHS:
                ok, wait = limiter.allow(ident + ":llm", _limit("LLM_RATE_LIMIT_PER_MIN", 20))
            if not ok:
                metrics.limited += 1
                await _audit(request, key, client, 429, 0)
                return _secure(JSONResponse({"detail": f"too many requests; try again in {wait} s"},
                                            status_code=429, headers={"Retry-After": str(wait)}))
        t0 = time.perf_counter()
        response = await call_next(request)
        secs = time.perf_counter() - t0
        metrics.record(request.method, _route_path(request), response.status_code, secs)
        if request.method != "GET" or response.status_code in (401, 403):
            await _audit(request, key, client, response.status_code, int(secs * 1000))
        return _secure(response, path)

    app.add_middleware(BodyLimit)


# The dashboard is one self-contained page (inline script and style); the API docs at /docs
# load their script from a CDN, so they get FastAPI's defaults instead of this policy.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def _secure(response, path: str = ""):
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "no-referrer")
    if not path.startswith(("/docs", "/redoc")):
        h.setdefault("Content-Security-Policy", CSP)
    return response


audit_limiter = RateLimiter()


async def _audit(request, key, client, status, ms):
    """Write one audit row off the event loop. Refused requests (4xx) are recorded at most
    AUDIT_REFUSED_PER_MIN times a minute per caller, so a flood cannot fill the table."""
    if os.getenv("AUDIT", "on").lower() == "off":
        return
    if status in (401, 403, 413, 429):
        ok, _ = audit_limiter.allow("refused:" + client, _limit("AUDIT_REFUSED_PER_MIN", 30))
        if not ok:
            return
    digest = hashlib.sha256(key.encode("utf-8", "surrogateescape")).hexdigest()[:8] if key else None
    role = _role_of(key) or "none"
    method, path = request.method, request.url.path

    def write():
        from cascade_guard.storage.db import get_store
        try:
            store = get_store()
            if store is not None:
                store.save_audit(role, digest, client, method, path, status, ms)
        except Exception as e:  # the audit log must never break a request
            log.warning("audit log write failed: %s", e)

    await run_in_threadpool(write)


log = logging.getLogger("cascade_guard")
router = APIRouter(tags=["ops"])


@router.get("/health/ready")
def ready():
    from cascade_guard.llm_api import get_client
    from cascade_guard.storage.db import get_store
    out = {"status": "ok"}
    try:  # no names, paths or hosts here: this route is open to load balancers
        store = get_store()
        out["database"] = ("ok" if store.ping() else "unreachable") if store else "off"
    except Exception as e:
        out["database"] = f"error: {type(e).__name__}"
        out["status"] = "degraded"
    out["model"] = "configured" if get_client(required=False) is not None else "not configured"
    return JSONResponse(out, status_code=200 if out["status"] == "ok" else 503)


@router.get("/metrics", response_class=PlainTextResponse, dependencies=[Depends(require("viewer"))])
def prometheus():
    from cascade_guard.llm_api import get_client
    from cascade_guard.storage.db import get_store
    lines = ["# HELP cascade_requests_total HTTP requests by route and status",
             "# TYPE cascade_requests_total counter"]
    with metrics._lock:
        reqs, secs, limited = dict(metrics.requests), dict(metrics.seconds), metrics.limited
    for (m, p, st), n in sorted(reqs.items()):
        lines.append(f'cascade_requests_total{{method="{m}",route="{p}",status="{st}"}} {n}')
    lines += ["# TYPE cascade_request_seconds_sum counter"]
    lines += [f'cascade_request_seconds_sum{{route="{p}"}} {v:.4f}' for p, v in sorted(secs.items())]
    lines += ["# TYPE cascade_rate_limited_total counter", f"cascade_rate_limited_total {limited}"]
    c = get_client(required=False)
    if c is not None:
        u = c.usage
        lines += ["# TYPE cascade_llm_calls_total counter", f"cascade_llm_calls_total {u.calls}",
                  "# TYPE cascade_llm_cached_calls_total counter", f"cascade_llm_cached_calls_total {u.cached_calls}",
                  "# TYPE cascade_llm_cost_usd_total counter", f"cascade_llm_cost_usd_total {u.cost:.6f}"]
    store = get_store()
    if store is not None:
        s = store.usage_summary()
        lines += ["# TYPE cascade_runs gauge"]
        lines += [f'cascade_runs{{status="{k}"}} {v}' for k, v in sorted(s["runs_by_status"].items())]
        lines += ["# TYPE cascade_open_reviews gauge", f"cascade_open_reviews {s['open_reviews']}"]
    return "\n".join(lines) + "\n"


@router.get("/audit", dependencies=[Depends(require("admin"))])
def audit(limit: int = Query(100, ge=1, le=1000)):
    from cascade_guard.storage.db import get_store
    store = get_store()
    return store.list_audit(limit) if store else []
