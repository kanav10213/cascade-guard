"""Whose request is this? The visitor's address, also when the server sits behind a proxy (Render, Caddy, nginx).

Without settings it is the address of whoever opened the connection. Behind a proxy that is the proxy itself, for
every visitor, so every per-visitor limit (requests a minute, sign-in attempts, sign-ups a day) would be shared by
everyone. Tell the server which header the proxy writes:

    CLIENT_IP_HEADER     header names, first one present wins, e.g. "cf-connecting-ip,x-forwarded-for"
    TRUSTED_PROXY_HOPS   for X-Forwarded-For: how many proxies are in front (default 1). The visitor is that many
                         entries from the right; entries further left were sent by the visitor and can be faked.

Only set these when the server can be reached only through that proxy: otherwise anyone can write the header.
"""
from __future__ import annotations

import ipaddress
import os


def _valid(value: str) -> str | None:
    v = value.strip().strip('"')
    if v.startswith("[") and "]" in v:          # [2001:db8::1]:443
        v = v[1:v.index("]")]
    elif v.count(":") == 1:                     # 203.0.113.7:51234
        v = v.split(":")[0]
    try:
        return str(ipaddress.ip_address(v))
    except ValueError:
        return None


def client_address(request) -> str:
    peer = request.client.host if request.client else "?"
    names = [n.strip().lower() for n in os.getenv("CLIENT_IP_HEADER", "").split(",") if n.strip()]
    for name in names:
        raw = request.headers.get(name)
        if not raw:
            continue
        if name == "x-forwarded-for":
            parts = [p for p in (_valid(x) for x in raw.split(",")) if p]
            hops = max(1, int(os.getenv("TRUSTED_PROXY_HOPS", "1") or 1))
            if parts:
                return parts[-hops] if len(parts) >= hops else parts[0]
        else:
            ip = _valid(raw)
            if ip:
                return ip
    return peer
