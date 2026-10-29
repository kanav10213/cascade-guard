"""One-call connection check: is the key, the model name and the reply format right?

    cascade-guard eval check          (or POST /llm/check on the server)

It sends one tiny request (a fraction of a cent on a paid model) and says in plain words
what works and, if something does not, what to change.
"""
import random
import time

from cascade_guard.llm.client import BudgetExceeded, LLMCallError, NotConfigured
from cascade_guard.llm.prompts import AGENT_MAX_TOKENS, REQUIRED_KEYS, agent_messages, parse_last_json

SAMPLE = [3, 7, 2, 9]
EXPECTED = {"count": 4, "sum": 21, "mean": 5.25, "max": 9}


def _hint(err: str) -> str:
    e = err.lower()
    if any(s in e for s in ("401", "authentication", "invalid x-api-key", "invalid api key", "incorrect api key")):
        return "The provider rejected the API key. Copy it again into .env (no quotes or spaces) and restart."
    if any(s in e for s in ("404", "not_found", "model_not_found", "does not exist", "unknown model")):
        return "The model name was not found. Check LLM_MODEL against your provider's model list."
    if any(s in e for s in ("403", "permission", "forbidden")):
        return "The key works but is not allowed to use this model. Check the key's permissions or pick another model."
    if any(s in e for s in ("429", "rate limit", "quota", "credit", "billing", "insufficient")):
        return "Rate limit or credit problem at the provider. Check your plan and billing, or wait and try again."
    if any(s in e for s in ("connect", "timeout", "timed out", "name or service", "network", "ssl")):
        return "Could not reach the provider. Check the internet connection, a proxy, or OPENAI_BASE_URL."
    return "The provider returned an error; the message above says why."


def connection_check(client) -> dict:
    """Send one small agent request and judge the reply. Never raises for provider problems."""
    out = {"ok": False, "provider": getattr(client, "provider", "?"), "model": getattr(client, "model", "?"),
           "simulated": bool(getattr(client, "is_fake", False))}
    system, user = agent_messages("analyze", {"numbers": SAMPLE})
    before = client.usage.cost
    t0 = time.perf_counter()
    try:
        # a fresh sample number, so the check really calls the model instead of reading the cache
        resp = client.complete(system, user, max_tokens=AGENT_MAX_TOKENS, sample=random.randrange(1, 10**9),
                               purpose="check")
    except (LLMCallError, BudgetExceeded, NotConfigured) as e:
        out.update(stage="call", error=str(e), hint=_hint(str(e)) if isinstance(e, LLMCallError) else str(e))
        return out
    out["seconds"] = round(time.perf_counter() - t0, 2)
    out["cost_usd"] = round(client.usage.cost - before, 6)
    out["reply"] = resp.text[:300]
    data = parse_last_json(resp.text)
    if not isinstance(data, dict) or not set(REQUIRED_KEYS["analyze"]) <= data.keys():
        out.update(stage="format", hint="The model answered, but not with the JSON object the agents need. "
                                        "A small or local model may not follow the format; try a stronger one.")
        return out
    wrong = {k: data[k] for k, v in EXPECTED.items() if data.get(k) != v}
    out.update(ok=True, stage="done", answer_correct=not wrong,
               hint="Key, model and reply format all work." + (
                   "" if not wrong else f" (Its arithmetic was off on this sample: {wrong}; that is the kind of "
                                         "error the guard exists to catch.)"))
    return out


def print_check(r: dict) -> int:
    head = "OK" if r["ok"] else "PROBLEM"
    sim = " (simulated: nothing was sent to a provider)" if r.get("simulated") else ""
    print(f"{head}: {r['provider']} / {r['model']}{sim}")
    if r.get("error"):
        print(f"  error: {r['error']}")
    if "seconds" in r:
        print(f"  one call, {r['seconds']} s, ${r.get('cost_usd', 0):.5f}")
    print(f"  {r['hint']}")
    return 0 if r["ok"] else 1
