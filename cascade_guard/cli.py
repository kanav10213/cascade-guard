"""The cascade-guard command.

    cascade-guard init                 create .env in this folder from the template
    cascade-guard serve                dashboard + API on http://localhost:8000
    cascade-guard mcp                  MCP server for Claude Desktop, Claude Code and other AI tools
    cascade-guard compare              rule-based paired comparison, writes report.json
    cascade-guard eval all --dry-run   real-model evaluation (shows the cost first)
    cascade-guard --version

Every command works in the working folder: --home, else CASCADE_HOME, else the
current folder. It holds .env, data/ (the SQLite database) and .llm_cache/.
"""
import argparse
import ipaddress
import os
import shutil
import sys
from pathlib import Path

from cascade_guard import __version__

TEMPLATE = Path(__file__).with_name("env.example")


def _is_local(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def cmd_init(args) -> int:
    from cascade_guard.config import use_home
    h = use_home(args.home)
    env = h / ".env"
    if env.exists() and not args.force:
        print(f"{env} already exists (use --force to replace it).")
        return 0
    shutil.copyfile(TEMPLATE, env)
    print(f"Created {env}")
    print("Next: put your API key in it (or set LLM_PROVIDER=fake to try it free), then run:")
    print("    cascade-guard serve")
    return 0


def _in_container() -> bool:
    return Path("/.dockerenv").exists() or os.getenv("container") is not None  # noqa: SIM112 (podman sets lowercase)


def cmd_serve(args) -> int:
    from cascade_guard.config import load_env, use_home
    h = use_home(args.home)
    load_env()  # before reading HOST, PORT and LOG_LEVEL, so .env can set them
    host = args.host or os.getenv("HOST") or "127.0.0.1"
    port = args.port or int(os.getenv("PORT") or 8000)
    log_level = args.log_level or os.getenv("LOG_LEVEL") or "info"
    import uvicorn
    keys = any(os.getenv(k) for k in ("API_KEY", "REVIEWER_KEY", "VIEWER_KEY"))
    if args.proxy_headers and not keys:
        print("Refusing --proxy-headers without access keys: behind a proxy every visitor would look like this "
              "computer and get admin rights. Set API_KEY in .env first.", file=sys.stderr)
        return 2
    if not keys and os.getenv("ALLOW_OPEN_NETWORK") == "1":
        print("WARNING: ALLOW_OPEN_NETWORK=1 and no access keys: any program that can reach this port and says "
              "'Host: localhost' is admin. Keep the port bound to 127.0.0.1, or set API_KEY.", file=sys.stderr)
    elif not keys and not _is_local(host):
        where = "from inside this container" if _in_container() else "from this computer"
        print(f"Note: no access keys are set, so only requests {where} are answered. "
              "Set API_KEY in .env before letting others connect.", file=sys.stderr)
    if not (h / ".env").exists() and not os.getenv("LLM_PROVIDER"):
        print("No .env here yet; model features stay off until you run `cascade-guard init` "
              "and add a key.", file=sys.stderr)
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0", "::") else host  # noqa: S104
    print(f"Cascade Guard {__version__} on http://{shown}:{port}  (working folder: {h}; Ctrl+C to stop)")
    uvicorn.run("cascade_guard.api:app", host=host, port=port, reload=args.reload,
                workers=None if args.reload else args.workers, proxy_headers=args.proxy_headers,
                forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"), log_level=log_level)
    return 0


def cmd_mcp(args) -> int:
    if args.home:
        os.environ["CASCADE_HOME"] = str(Path(args.home).expanduser().resolve())
    from cascade_guard import mcp_server
    return mcp_server.main(["--print-config"] if args.print_config else [])


def _experiment(module: str):
    def run(args) -> int:
        from cascade_guard.config import load_env, use_home
        use_home(args.home)
        load_env()
        mod = __import__(f"cascade_guard.experiments.{module}", fromlist=["main"])
        result = mod.main(args.rest)
        return result if isinstance(result, int) else 0
    return run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cascade-guard", description="Stop errors from cascading through "
                                "chains of AI agents.", epilog="Docs: README.md, and /docs on a running server.")
    p.add_argument("--version", action="version", version=f"cascade-guard {__version__}")
    p.add_argument("--home", dest="top_home", metavar="HOME", help="working folder (also accepted after the command)")
    sub = p.add_subparsers(dest="command", metavar="command")

    def add(name, func, help_):
        s = sub.add_parser(name, help=help_, description=help_)
        s.add_argument("--home", help="working folder (default: CASCADE_HOME, else the current folder)")
        s.set_defaults(func=func)
        return s

    s = add("init", cmd_init, "create .env in the working folder from the template")
    s.add_argument("--force", action="store_true", help="replace an existing .env")

    s = add("serve", cmd_serve, "run the dashboard and API")
    s.add_argument("--host", default=None,
                   help="address to listen on (default: HOST, else 127.0.0.1; the Docker image uses 0.0.0.0)")
    s.add_argument("--port", type=int, default=None, help="port (default: PORT, else 8000)")
    s.add_argument("--workers", type=int, default=1,
                   help="worker processes (spend caps and rate limits are per process)")
    s.add_argument("--reload", action="store_true", help="restart on code changes (development)")
    s.add_argument("--proxy-headers", action="store_true",
                   help="trust X-Forwarded-For from FORWARDED_ALLOW_IPS (behind a reverse proxy)")
    s.add_argument("--log-level", default=None, choices=["critical", "error", "warning", "info", "debug"],
                   help="default: LOG_LEVEL, else info")

    s = add("mcp", cmd_mcp, "run the MCP server over stdio (for Claude Desktop, Claude Code and others)")
    s.add_argument("--print-config", action="store_true", help="print the setup to paste into your AI tool")

    for name, module, help_ in (("compare", "compare", "rule-based paired comparison; writes report.json"),
                                ("baseline", "baseline", "rule-based chain without the guard"),
                                ("protected", "protected", "rule-based chain with the guard"),
                                ("eval", "llm_eval", "real-model evaluation; shows the cost and asks first")):
        s = add(name, _experiment(module), help_)
        s.add_argument("rest", nargs=argparse.REMAINDER, help=f"options for {name} (see: {name} -- --help)")
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    if not getattr(args, "func", None):
        if unknown:
            parser.error("unrecognized arguments: " + " ".join(unknown))
        parser.print_help()
        return 0
    if hasattr(args, "rest"):  # experiment commands pass their own options through, except --home
        rest, i, passed = unknown + args.rest, 0, []
        while i < len(rest):
            a = rest[i]
            if a == "--home" and i + 1 < len(rest):
                args.home, i = rest[i + 1], i + 2
                continue
            if a.startswith("--home="):
                args.home = a.split("=", 1)[1]
            elif a != "--":
                passed.append(a)
            i += 1
        args.rest = passed
    elif unknown:
        parser.error("unrecognized arguments: " + " ".join(unknown))
    args.home = args.home or args.top_home
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
