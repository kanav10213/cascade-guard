"""Put Cascade Guard INSIDE your own agent's code: check, retry with the reason, stop safely.

Run it:   python examples/guard_inside_my_agent.py
          (works with no key: LLM_PROVIDER=fake uses the free simulator for the checker)

Two ways to check, pick one:
  * local:  the checker runs in this process with the model in .env (llm_check)
  * remote: ask a running Cascade Guard server, e.g. your Render site (RemoteGuard), with
            CASCADE_URL=https://cascade-guard.onrender.com CASCADE_API_KEY=... python examples/guard_inside_my_agent.py
"""
import os

from cascade_guard import GuardHalted, guard_step, llm_check, rule_check
from cascade_guard.llm.client import make_client
from cascade_guard.remote import RemoteGuard

os.environ.setdefault("LLM_PROVIDER", "fake")   # no key needed for the demo


# ---- 1. your agents (replace these with your own code, framework or model calls) --------------------
def summarise_agent(ticket: str, feedback=None) -> str:
    """Your agent. `feedback` is set on a retry: why the guard rejected the last answer."""
    first = ticket.split(".")[0].strip()
    return first + "." if first else ""


def total_agent(items: list, feedback=None) -> float:
    return round(sum(q * p for q, p in items), 2)


# ---- 2. choose the checker ----------------------------------------------------------------------------
if os.getenv("CASCADE_URL"):
    check_summary = RemoteGuard().check("Summarise the customer's problem in one sentence; keep invoice numbers.")
else:
    check_summary = llm_check("Summarise the customer's problem in one sentence; keep invoice numbers.",
                              client=make_client())

guarded_summarise = guard_step(check_summary, max_retries=2)(summarise_agent)
guarded_total = guard_step(rule_check(lambda out, items: abs(out - sum(q * p for q, p in items)) < 0.005,
                                      "the total does not match the line items"))(total_agent)

# ---- 3. use the guarded agents exactly like the originals -------------------------------------------------
if __name__ == "__main__":
    ticket = "I was charged twice for invoice INV-1007. Please refund the duplicate before Friday."
    try:
        print("summary:", guarded_summarise(ticket))
        print("total:  ", guarded_total([(3, 4.0), (2, 1.5)]))
    except GuardHalted as stop:
        print("stopped safely, nothing wrong passed on:", stop.report)
