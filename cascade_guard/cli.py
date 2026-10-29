"""The cascade-guard command.

    cascade-guard init                 create .env in this folder from the template
    cascade-guard serve                dashboard + API on http://localhost:8000
    cascade-guard mcp                  MCP server for Claude Desktop, Claude Code and other AI tools
    cascade-guard compare              rule-based paired comparison, writes report.json
    cascade-guard eval all --dry-run   real-model evaluation (shows the cost first)
    cascade-guard chain run support-triage --input "..."   run your own agent chain
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


def cmd_chain(args) -> int:
    """Agent chains from the command line: templates, list, import, export, run."""
    import json as _json

    from pydantic import ValidationError

    from cascade_guard.chains import ChainDef, run_chain_def
    from cascade_guard.config import load_env, use_home
    use_home(args.home)
    load_env()

    def store():
        from cascade_guard.storage.db import get_store
        s = get_store()
        if s is None:
            raise SystemExit("Storage is off (CASCADE_DB=off), so chains cannot be saved or listed.")
        return s

    def load(ref: str) -> ChainDef:
        p = Path(ref)
        try:
            if p.suffix == ".json" or p.exists():
                return ChainDef.model_validate(_json.loads(p.read_text(encoding="utf-8")))
            for t in sorted((Path(__file__).with_name("templates")).glob("*.json")):
                d = _json.loads(t.read_text(encoding="utf-8"))
                if d["name"].lower() == ref.lower() or t.stem == ref:
                    return ChainDef.model_validate(d)
            row = store().get_chain(int(ref)) if ref.isdigit() else store().find_chain(ref)
        except ValidationError as e:
            raise SystemExit(f"Not a valid chain definition:\n{e}") from e
        if row is None:
            raise SystemExit(f"No chain file, template or saved chain called {ref!r}.")
        return ChainDef.model_validate(row["definition"])

    if args.action == "templates":
        for t in sorted((Path(__file__).with_name("templates")).glob("*.json")):
            d = _json.loads(t.read_text(encoding="utf-8"))
            print(f"{t.stem:<20} {d['name']}: {len(d['steps'])} steps. {d.get('description', '')}")
        return 0
    if args.action == "list":
        rows = store().list_chains()
        for r in rows:
            print(f"#{r['id']:<4} {r['name']}  ({r['steps']} steps, updated {r['updated_at']})")
        if not rows:
            print("No saved chains yet. Import one: cascade-guard chain import my_chain.json")
        return 0
    if args.action == "export":
        print(_json.dumps(load(args.ref).model_dump(), indent=2))
        return 0
    if args.action == "import":
        chain = load(args.ref)
        existing = store().find_chain(chain.name)
        chain_id = store().save_chain(chain.model_dump(), chain_id=existing["id"] if existing else None, by="cli")
        print(f"{'Updated' if existing else 'Saved'} chain #{chain_id}: {chain.name}")
        return 0
    # run
    chain = load(args.ref)
    text = Path(args.input_file).read_text(encoding="utf-8") if args.input_file else args.input
    if not text:
        raise SystemExit("Give the input with --input TEXT or --input-file FILE.")
    from cascade_guard.llm.client import FakeLLMClient, make_client
    client = make_client(max_cost=float(os.getenv("LLM_MAX_COST", "1.0")), cache_path=None,
                         strict_prices=False) if args.real else FakeLLMClient()
    from cascade_guard.agent_call import AgentCallError
    try:
        result = run_chain_def(chain, text, client, inject=args.inject, seed=args.seed)
    except AgentCallError as e:
        raise SystemExit(f"Could not use your agent: {e}") from e
    if args.json:
        print(_json.dumps(result, indent=2, default=str))
    else:
        print(f"{chain.name}: {result['status'].upper()} ({client.provider} / {client.model}"
              f"{', simulated' if client.is_fake else ''}; {result['usage']['calls']} calls, "
              f"${result['usage']['cost_usd']:.4f})")
        for s in result["steps"]:
            tries = " -> ".join(("ok" if a["passed"] else f"rejected ({a['reason']})") for a in s["attempts"])
            print(f"  {s['step']}: {s['status']}  [{tries}]")
        if result["status"] == "completed":
            out = result["final_output"]
            print("\n" + (out if isinstance(out, str) else _json.dumps(out, indent=2)))
        else:
            print(f"\nStopped at {result['halted_at']}: nothing wrong was passed on.")
    return 0 if result["status"] == "completed" else 1


def cmd_agent(args) -> int:
    """Connected agents from the command line: list, add, test, remove."""
    from pydantic import ValidationError

    from cascade_guard.agents_registry import AgentDef, all_agents, find_agent, test_agent
    from cascade_guard.config import load_env, use_home
    use_home(args.home)
    load_env()
    from cascade_guard.storage.db import get_store
    store = get_store()
    if args.action == "list":
        agents = all_agents()
        for a in agents:
            token = f", token in {a['auth_env']}" + ("" if a["token_set"] else " (NOT SET)") if a["auth_env"] else ""
            print(f"{a['name']:<20} {a['url']}  [{a['source']}{token}]  {a['description']}")
        if not agents:
            print("No connected agents yet. Add one: cascade-guard agent add my-agent https://.../agent")
        return 0
    if not args.name:
        raise SystemExit("Name the agent, e.g. cascade-guard agent test my-agent")
    if args.action == "add":
        if store is None:
            raise SystemExit("Storage is off; list agents in the CASCADE_AGENTS setting instead.")
        try:
            agent = AgentDef(name=args.name, url=args.url or "", auth_env=args.token_var or "", timeout=args.timeout,
                             description=args.description or "")
        except ValidationError as e:
            raise SystemExit(f"Not a valid agent:\n{e}") from e
        store.save_agent(agent.model_dump())
        print(f"Connected {agent.name} -> {agent.url}. Test it: cascade-guard agent test {agent.name}")
        return 0
    if args.action == "remove":
        if store is None or not store.delete_agent(args.name.lower()):
            raise SystemExit(f"No agent called {args.name!r} in the database.")
        print(f"Removed {args.name.lower()}")
        return 0
    found = find_agent(args.name)
    if found is None:
        raise SystemExit(f"No connected agent called {args.name!r}.")
    r = test_agent(found)
    print(("OK" if r["ok"] else "PROBLEM") + f": {found.name} ({found.url})" + (f", {r['seconds']} s" if "seconds" in r else ""))
    if r.get("reply"):
        print(f"  reply: {str(r['reply'])[:200]}")
    print(f"  {r['hint']}")
    return 0 if r["ok"] else 1


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

    s = add("chain", cmd_chain, "your own agent chains: templates, list, import, export, run")
    s.add_argument("action", choices=["templates", "list", "import", "export", "run"])
    s.add_argument("ref", nargs="?", default="", help="a chain .json file, a template, or a saved chain's name or id")
    s.add_argument("--input", default="", help="input text for run")
    s.add_argument("--input-file", help="read the input for run from this file")
    s.add_argument("--real", action="store_true", help="run on the configured model (costs money); default: simulated")
    s.add_argument("--inject", type=float, default=0.0, help="chance of a planted test mistake per step (0 to 1)")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--json", action="store_true", help="print the full result as JSON")

    s = add("agent", cmd_agent, "connected agents: list, add, test, remove")
    s.add_argument("action", choices=["list", "add", "test", "remove"])
    s.add_argument("name", nargs="?", default="")
    s.add_argument("url", nargs="?", default="", help="for add: the agent's URL")
    s.add_argument("--token-var", help="for add: the server environment variable that holds the agent's token")
    s.add_argument("--timeout", type=float, help="for add: seconds to wait (default 90)")
    s.add_argument("--description", help="for add: one line about the agent")

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
