"""cascade-guard ai list | test: which AIs Cascade Guard can use, and a quick check that one answers.

    cascade-guard ai list                     every kind of AI, and the local AI servers running right now
    cascade-guard ai test --ai ollama         send one tiny request and show the answer and the time it took
"""
from __future__ import annotations

import argparse
import json
import os
import time


def cmd_list() -> int:
    from cascade_guard.llm.presets import catalogue, detect_local
    rows = catalogue()
    w = max(len(r["ai"]) for r in rows)
    print("Use any of these with --ai (or CASCADE_AI=... in .env):\n")
    print(f"  {'--ai':<{w}}  {'runs on':<13}  {'needs':<32}  note")
    for r in rows:
        print(f"  {r['ai']:<{w}}  {r['where']:<13}  {r['needs']:<32}  {r['note']}")
    print("\nIn Python, any function works: cascade_guard.llm.presets.function_client(fn).")
    found = detect_local()
    print("\nLocal AI servers answering now:" if found else "\nNo local AI server is answering right now "
          "(start Ollama, LM Studio, llama.cpp, vLLM, Jan or LocalAI to use one).")
    for name, url, models in found:
        more = f" (+{len(models) - 6} more)" if len(models) > 6 else ""
        print(f"  {name:<9} {url:<28} {', '.join(models[:6])}{more}")
    keys = [k for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY",
                        "OPENROUTER_API_KEY", "MISTRAL_API_KEY", "DEEPSEEK_API_KEY", "TOGETHER_API_KEY")
            if os.getenv(k)]
    if keys:
        print("\nCloud keys found: " + ", ".join(keys))
    current = os.getenv("CASCADE_AI")
    print(f"\nCASCADE_AI is {current!r}." if current else "\nCASCADE_AI is not set: the rule-based agents are used "
          "unless you pass --ai.")
    return 0


def cmd_test(ai: str | None) -> int:
    from cascade_guard.llm.client import LLMCallError, NotConfigured
    from cascade_guard.llm.presets import client_from_ai, parse_ai
    ai = ai or os.getenv("CASCADE_AI") or "auto"
    try:
        spec = parse_ai(ai)
        client = client_from_ai(spec, cache_path=None)
    except NotConfigured as e:
        print(f"Can't use {ai!r}: {e}")
        return 1
    if client.is_fake:
        print(f"{ai!r} means the rule-based agents: no AI to test. They are ready.")
        return 0
    where = getattr(client, "base_url", None) or ""
    print(f"Asking {client.provider} · {client.model}{' at ' + where if where else ''} ...", flush=True)
    t = time.monotonic()
    try:
        r = client.complete("You are a careful assistant. Reply with JSON only.",
                            'A receipt says: "TOTAL 497.00  CASH 500.00  CHANGE 3.00". '
                            'Return {"total": "<the total paid>"}', max_tokens=60, purpose="ai-test")
    except LLMCallError as e:
        print(f"It did not answer: {e}")
        return 1
    secs = time.monotonic() - t
    from cascade_guard.llm.prompts import parse_last_json
    data = parse_last_json(r.text)
    ok = isinstance(data, dict) and str(data.get("total", "")).replace("RM", "").strip() in ("497.00", "497", "497.0")
    print(f"Answer ({secs:.1f} s): {r.text.strip()[:200]}")
    print("It works, and it read the total right." if ok else
          "It answers. (It did not read this total right on its own; that is the kind of mistake the guard is "
          "for.)")
    print(f"Use it:  cascade-guard receipts run <folder> --ai {ai}")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="cascade-guard ai", description=__doc__.split("\n\n")[0])
    p.add_argument("action", choices=["list", "test"])
    p.add_argument("--ai", help="the AI to test, e.g. ollama:qwen2.5:7b (default: CASCADE_AI, else auto)")
    p.add_argument("--json", action="store_true", help="list: print the catalogue as JSON")
    a = p.parse_args(argv)
    if a.action == "list" and a.json:
        from cascade_guard.llm.presets import catalogue, detect_local
        print(json.dumps({"ais": catalogue(), "local_now": [{"ai": n, "url": u, "models": m}
                                                           for n, u, m in detect_local()]}, indent=2))
        return 0
    return cmd_list() if a.action == "list" else cmd_test(a.ai)
