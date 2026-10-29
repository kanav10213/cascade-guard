"""Access control: three keys, three roles.

    API_KEY        admin     everything, including routes that call the model (cost money)
    REVIEWER_KEY   reviewer  read everything, run the free demos, resolve review-queue items
    VIEWER_KEY     viewer    read everything and run the free demos (nothing that costs or decides)

Send the key in the X-API-Key header. With no keys set at all, every caller counts
as admin, but only requests from this computer are accepted (see cascade_guard/ops.py); set
API_KEY before anyone else can reach the server. /health is always open.
"""
import os
import secrets

from fastapi import Depends, Header, HTTPException

RANK = {"viewer": 1, "reviewer": 2, "admin": 3}
_A = {"viewer": "a viewer", "reviewer": "a reviewer", "admin": "an admin"}
_ENV = {"admin": "API_KEY", "reviewer": "REVIEWER_KEY", "viewer": "VIEWER_KEY"}


def auth_enabled() -> bool:
    return any(os.getenv(v) for v in _ENV.values())


def role_for_key(given: str) -> str | None:
    """The role a key maps to, or None. Compares bytes, so any header text is safe."""
    if not auth_enabled():
        return "admin"
    given_b = (given or "").encode("utf-8", "surrogateescape")
    for role in ("admin", "reviewer", "viewer"):
        key = os.getenv(_ENV[role])
        if key and secrets.compare_digest(given_b, key.encode("utf-8", "surrogateescape")):
            return role
    return None


def current_role(x_api_key: str | None = Header(default=None)) -> str:
    role = role_for_key(x_api_key or "")
    if role:
        return role
    raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


def require(min_role: str):
    """FastAPI dependency: the caller's role must be at least `min_role`."""
    def dep(role: str = Depends(current_role)) -> str:
        if RANK[role] < RANK[min_role]:
            raise HTTPException(status_code=403,
                                detail=f"your key is {_A[role]} key; this needs {_A[min_role]} key")
        return role
    return dep


def ensure(role: str, min_role: str):
    """The same check inside a route, for permissions that depend on the request body."""
    if RANK[role] < RANK[min_role]:
        raise HTTPException(status_code=403, detail=f"your key is {_A[role]} key; this needs {_A[min_role]} key")
