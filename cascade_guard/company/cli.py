"""Command line for the company product.

    cascade-guard kb add FILES-OR-FOLDERS [--workspace W]      add documents (PDF, Word, text, CSV, HTML)
    cascade-guard kb add --samples                             the made-up sample company, to try it
    cascade-guard kb list | remove ID | search "question"
    cascade-guard chat "question" [--ai X] [--workspace W]     one checked answer
    cascade-guard chat                                         a conversation in the terminal
    cascade-guard keys create NAME --workspace acme [--role manager] [--plan starter] [--limit N]
    cascade-guard keys list [--workspace W] | revoke ID | usage
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _setup(args) -> None:
    from cascade_guard.config import load_env, use_home
    use_home(getattr(args, "home", None))
    load_env()
    if getattr(args, "ai", None):
        os.environ["CASCADE_AI"] = args.ai


def kb_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="cascade-guard kb", description="The company's documents.")
    p.add_argument("action", choices=["add", "list", "remove", "search"])
    p.add_argument("items", nargs="*", help="add: files or folders · remove: document id · search: the question")
    p.add_argument("--workspace", default="default")
    p.add_argument("--samples", action="store_true", help="add: the made-up sample company")
    p.add_argument("--home")
    a = p.parse_args(argv)
    _setup(a)
    from cascade_guard.company import kb
    from cascade_guard.company.keys import norm_workspace
    ws = norm_workspace(a.workspace)
    if a.action == "add":
        from cascade_guard.company.api import SAMPLES
        paths = [Path(x).expanduser() for x in a.items] + ([SAMPLES] if a.samples else [])
        if not paths:
            raise SystemExit("name files or folders to add, or use --samples")
        files: list[Path] = []
        for path in paths:
            if path.is_dir():
                files += sorted(f for f in path.rglob("*") if f.is_file() and f.suffix.lower() in kb.KINDS
                                and not f.name.startswith(".") and not f.stem.upper().startswith("README"))
            elif path.is_file():
                files.append(path)
            else:
                raise SystemExit(f"No such file or folder: {path}")
        added = failed = 0
        for f in files:
            try:
                d = kb.add_document(ws, f.name, f.read_bytes(), by="command line")
                print(f"  added  {f.name}: {d['passages']} parts" + (f", {d['pages']} pages" if d.get("pages") else ""))
                added += 1
            except (kb.DocumentError, OSError) as e:
                print(f"  SKIPPED {f.name}: {e}")
                failed += 1
        print(f"{added} added to workspace {ws}" + (f", {failed} skipped" if failed else "") + ".")
        return 1 if failed and not added else 0
    if a.action == "list":
        docs = kb.list_documents(ws)
        for d in docs:
            print(f"  {d['id']:>4}  {d['name']:<44} {d['passages']:>5} parts   added {d['created_at'][:10]}")
        print(f"{len(docs)} documents in workspace {ws}.")
        return 0
    if a.action == "remove":
        if not a.items or not a.items[0].isdigit():
            raise SystemExit("give the document id (see: cascade-guard kb list)")
        ok = kb.delete_document(ws, int(a.items[0]))
        print("Removed." if ok else "No such document.")
        return 0 if ok else 1
    hits = kb.search(ws, " ".join(a.items), k=5)
    print(f"Relevant: {'yes' if kb.relevant(hits) else 'no'}")
    for h in hits:
        print(f"\n[{h['doc']}{', page ' + str(h['page']) if h.get('page') else ''}]  score {h['score']}  "
              f"coverage {h['coverage']}\n{h['text'][:500]}")
    return 0


def _show(res: dict) -> None:
    label = {"checked": "✓ checked against your documents", "fixed": "✓ a mistake was caught and fixed",
             "trimmed": "! unsupported parts were removed", "unconfirmed": "! not fully confirmed",
             "not_found": "i not in your documents", "quoted": "✓ quoted word for word",
             "general": "i general answer, not from your documents"}.get(res["verdict"], res["verdict"])
    print(f"\n{res['answer']}\n\n[{label}]")
    for c in res.get("caught") or []:
        print(f"  caught: {c}")
    for r in res.get("removed") or []:
        print(f"  removed: {r}")
    if res.get("notice"):
        print(f"  note: {res['notice']}")
    for s in res.get("sources") or []:
        print(f"  [{s['n']}] {s['doc']}" + (f", page {s['page']}" if s.get("page") else ""))


def chat_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="cascade-guard chat", description="Ask the company's documents; every answer "
                                "is checked before you see it.")
    p.add_argument("question", nargs="*")
    p.add_argument("--ai", help="which AI writes the answers (see: cascade-guard ai list); default CASCADE_AI")
    p.add_argument("--workspace", default="default")
    p.add_argument("--mode", choices=["auto", "documents", "general"], default="auto")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    p.add_argument("--home")
    a = p.parse_args(argv)
    _setup(a)
    from cascade_guard.company.answer import answer
    from cascade_guard.company.keys import norm_workspace
    from cascade_guard.llm.client import NotConfigured, make_client
    ws = norm_workspace(a.workspace)
    client = None
    if os.getenv("CASCADE_AI", "rules").strip().lower() not in ("", "rules", "none", "off"):
        try:
            client = make_client(strict_prices=False, cache_path=None)
        except NotConfigured as e:
            raise SystemExit(f"Can't use that AI: {e}") from e
        if client.is_fake:
            client = None
    history: list[dict] = []
    if a.question:
        res = answer(" ".join(a.question), workspace=ws, client=client, mode=a.mode)
        print(json.dumps(res, indent=2, default=str, ensure_ascii=False)) if a.json else _show(res)
        return 0
    who = f"{client.provider} · {client.model}" if client else "no AI (answers quote your documents)"
    print(f"Ask about your documents (workspace {ws}, {who}). Empty line or Ctrl+C to stop.")
    while True:
        try:
            q = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            return 0
        res = answer(q, workspace=ws, client=client, history=history, mode=a.mode)
        _show(res)
        history += [{"role": "user", "content": q}, {"role": "assistant", "content": res["answer"]}]


def keys_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="cascade-guard keys", description="Access keys for people and customers.")
    p.add_argument("action", choices=["create", "list", "revoke", "usage", "plans"])
    p.add_argument("items", nargs="*", help="create: a name · revoke: the key id")
    p.add_argument("--workspace")
    p.add_argument("--role", choices=["member", "manager"], default="member")
    p.add_argument("--plan", default="starter")
    p.add_argument("--limit", type=int, help="answers a month (default: the plan's)")
    p.add_argument("--email")
    p.add_argument("--home")
    a = p.parse_args(argv)
    _setup(a)
    from cascade_guard.company import keys as K
    if a.action == "plans":
        for name, lim in K.plans().items():
            print(f"  {name:<12} {f'{lim:,} answers a month' if lim else 'no limit'}")
        return 0
    if a.action == "create":
        if not a.items:
            raise SystemExit("give a name, e.g. cascade-guard keys create \"Acme Pvt Ltd\" --workspace acme")
        try:
            key, rec = K.create_key(" ".join(a.items), a.workspace or "default", role=a.role, plan=a.plan,
                                    monthly_limit=a.limit, email=a.email)
        except ValueError as e:
            raise SystemExit(str(e)) from e
        limit = f"{rec['monthly_limit']:,} answers a month" if rec["monthly_limit"] else "no limit"
        print(f"Key for {rec['name']} (workspace {rec['workspace']}, {rec['role']}, plan {rec['plan']}: {limit}):\n\n"
              f"  {key}\n\nCopy it now: it is not stored and cannot be shown again.")
        return 0
    if a.action == "revoke":
        if not a.items or not a.items[0].isdigit():
            raise SystemExit("give the key id (see: cascade-guard keys list)")
        ok = K.revoke_key(int(a.items[0]))
        print("Revoked." if ok else "No such active key.")
        return 0 if ok else 1
    if a.action == "usage":
        for u in K.usage_report(a.workspace):
            print(f"  {u['month']}  {u['workspace']:<24} {u['answers']:>7} answers {u['ai_calls']:>8} AI calls "
                  f"{int(u['tokens'] or 0):>12,} tokens")
        return 0
    for k in K.list_keys(a.workspace):
        used = f"{k['used_this_month']}" + (f"/{k['monthly_limit']}" if k["monthly_limit"] else "")
        print(f"  {k['id']:>4}  {k['prefix']}…  {k['name'][:28]:<28} {k['workspace']:<18} {k['role']:<8} "
              f"{k['plan']:<10} {used:>11}  {'active' if k['active'] else 'revoked'}")
    return 0
