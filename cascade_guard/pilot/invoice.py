"""Pilot workflow: invoice checking, guarded at every step.

A realistic four-step agent workflow, built the way another team would build
it, on LangGraph, with each node wrapped by `guarded_node`:

    extract  read the invoice text: invoice id, tax rate, line items      (rules or LLM)
    price    line totals, subtotal, tax, total                            (rules or LLM)
    approve  "auto-approve" up to $500.00, otherwise "needs sign-off"     (business rule)
    report   one-line summary                                             (template)

Guards: "none" (baseline), "rules" (deterministic recomputation), or "llm"
(the LLM validator checks extract and price, rules check approve and report).

The sample invoices are generated, so every answer can be scored exactly.
Real invoices in the same text layout can be pasted in unchanged.

    result = run_invoice(text, guard="rules")
    report = run_pilot_experiment(trials=500, probability=0.1)
"""
import hashlib
import json
import operator
import random
import re
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from cascade_guard.evaluation.metrics import cri, crs
from cascade_guard.evaluation.runner import _is_infrastructure
from cascade_guard.guard.wrapper import guarded_node, llm_check
from cascade_guard.llm.prompts import parse_last_json, repair_suffix

STEPS = ("extract", "price", "approve", "report")
APPROVAL_LIMIT = Decimal("500.00")

CATALOG = [
    ("Steel bolts (box of 50)", "2.50"), ("Claw hammer", "14.99"), ("Safety gloves (pair)", "6.75"),
    ("LED work light", "39.90"), ("Cable ties (pack)", "3.20"), ("Measuring tape 5 m", "8.45"),
    ("Cordless drill", "129.00"), ("Drill bit set", "24.60"), ("Wood screws (box)", "4.15"),
    ("Paint roller", "7.80"), ("Masking tape", "2.95"), ("Extension cord 10 m", "18.50"),
    ("Spirit level", "12.30"), ("Utility knife", "5.60"), ("Ladder 6 ft", "89.00"),
]
CUSTOMERS = ["Sharma Traders", "Northwind Supplies", "Patel Hardware", "Blue River Builders",
             "Kaveri Engineering", "Summit Facilities"]
TAX_RATES = [5, 8, 12, 18]


# ===================================================================== sample data

def make_invoice(seed: int) -> dict:
    rng = random.Random(f"invoice:{seed}")
    n = rng.randint(2, 7)
    picks = rng.sample(CATALOG, n)
    items = [{"description": d, "qty": rng.randint(1, 12), "unit_price": float(p)} for d, p in picks]
    inv_id = f"INV-{1000 + seed % 9000}"
    rate = rng.choice(TAX_RATES)
    lines = "\n".join(f"  {it['qty']} x {it['description']} @ ${it['unit_price']:.2f}" for it in items)
    text = (f"INVOICE {inv_id}\nCustomer: {rng.choice(CUSTOMERS)}\nDate: 2026-10-{rng.randint(1, 28):02d}\n"
            f"Items:\n{lines}\nTax: {rate}%\n")
    return {"id": inv_id, "text": text}


def make_invoices(n: int, seed: int) -> list:
    return [make_invoice(seed * 100_000 + i) for i in range(n)]


# ===================================================================== the business logic (ground truth)

LINE = re.compile(r"^\s*(\d+)\s*x\s*(.+?)\s*@\s*\$?\s*([\d,]+(?:\.\d{1,2})?)\s*$")


def _money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_invoice(text: str) -> dict:
    inv = re.search(r"INVOICE\s+(\S+)", text)
    tax = re.search(r"Tax:\s*([\d.]+)\s*%", text)
    items = []
    for line in text.splitlines():
        m = LINE.match(line)
        if m:
            items.append({"description": m.group(2), "qty": int(m.group(1)),
                          "unit_price": float(_money(m.group(3).replace(",", "")))})
    if not inv or not tax or not items:
        raise ValueError("not an invoice in the expected layout (INVOICE id, item lines 'Q x NAME @ $P', 'Tax: R%')")
    return {"invoice_id": inv.group(1), "tax_rate": float(tax.group(1)), "items": items}


def price_items(extracted: dict) -> dict:
    lines = [_money(Decimal(str(it["unit_price"])) * int(it["qty"])) for it in extracted["items"]]
    subtotal = sum(lines, Decimal("0"))
    tax = _money(subtotal * Decimal(str(extracted["tax_rate"])) / 100)
    return {"line_totals": [float(x) for x in lines], "subtotal": float(subtotal), "tax": float(tax),
            "total": float(subtotal + tax)}


def decide(priced: dict) -> dict:
    return {"decision": "auto-approve" if _money(priced["total"]) <= APPROVAL_LIMIT else "needs sign-off"}


def render(extracted: dict, priced: dict, decision: dict) -> dict:
    return {"summary": (f"{extracted['invoice_id']}: {len(extracted['items'])} items, "
                        f"subtotal ${priced['subtotal']:.2f}, tax ${priced['tax']:.2f}, "
                        f"total ${priced['total']:.2f}, {decision['decision']}")}


def truth(text: str) -> dict:
    e = parse_invoice(text)
    p = price_items(e)
    d = decide(p)
    return {"extract": e, "price": p, "approve": d, "report": render(e, p, d)}


def reference(step: str, state: dict) -> dict:
    """The right output for `step` given what it was actually fed."""
    if step == "extract":
        return parse_invoice(state["invoice_text"])
    if step == "price":
        return price_items(clean(state["extract"]))
    if step == "approve":
        return decide(clean(state["price"]))
    return render(clean(state["extract"]), clean(state["price"]), clean(state["approve"]))


def clean(out):
    """Drop bookkeeping keys (_fault, _agent_ok) before comparing or passing on."""
    return {k: v for k, v in out.items() if not k.startswith("_")} if isinstance(out, dict) else out


def same(a, b) -> bool:
    """Equal, with money compared to the cent and text compared exactly."""
    a, b = clean(a), clean(b)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) < 0.005
    return a == b


# ===================================================================== agents

def rule_agent(step):
    def agent(state, attempt=0, feedback=None):
        return reference(step, state)
    return agent


EXTRACT_SYSTEM = (
    "You are the extraction agent in an invoice workflow. Read the invoice and return only one JSON object: "
    '{"invoice_id": string, "tax_rate": number (the percent, e.g. 8 for 8%), '
    '"items": [{"description": string, "qty": integer, "unit_price": number}]} with items in the order they appear. '
    "No other text.")
PRICE_SYSTEM = (
    "You are the pricing agent in an invoice workflow. From the line items and tax rate compute: line_totals "
    "(qty x unit_price for each item, rounded to cents), subtotal (sum of line totals), tax (subtotal x tax_rate / 100, "
    "rounded to cents, half up) and total (subtotal + tax). Work it out yourself; you have no tools. Return only one "
    'JSON object: {"line_totals": [numbers], "subtotal": number, "tax": number, "total": number}. No other text.')


def llm_agent(step, client):
    system = (EXTRACT_SYSTEM if step == "extract" else PRICE_SYSTEM) + (
        " If PREVIOUS and FEEDBACK are given, a checker rejected your previous answer: work it again from DATA, "
        "fix the problem FEEDBACK names, and reply with the corrected JSON only.")

    def agent(state, attempt=0, feedback=None):
        data = {"invoice_text": state["invoice_text"]} if step == "extract" else clean(state["extract"])
        if feedback and isinstance(feedback.get("previous_output"), dict):
            feedback = {**feedback, "previous_output": clean(feedback["previous_output"])}
        user = f"TASK: pilot_{step}\nDATA: {json.dumps(data)}" + repair_suffix(feedback)
        resp = client.complete(system, user, max_tokens=800,
                               temperature=0.0 if attempt == 0 else 1.0, sample=attempt, purpose=f"pilot:{step}")
        out = parse_last_json(resp.text)
        if out is None:
            raise ValueError(f"no JSON in the model's reply: {resp.text[:120]}")
        return _normalise(step, out)
    return agent


def _normalise(step, out):
    try:
        if step == "extract":
            return {"invoice_id": str(out["invoice_id"]), "tax_rate": float(out["tax_rate"]),
                    "items": [{"description": str(i["description"]), "qty": int(i["qty"]),
                               "unit_price": round(float(i["unit_price"]), 2)} for i in out["items"]]}
        return {"line_totals": [round(float(x), 2) for x in out["line_totals"]],
                "subtotal": round(float(out["subtotal"]), 2), "tax": round(float(out["tax"]), 2),
                "total": round(float(out["total"]), 2)}
    except (KeyError, TypeError, ValueError):
        return out  # malformed: the check will reject it


# ===================================================================== fault injection (testing only)

class PilotFaults:
    """Deterministic, realistic invoice errors, by (seed, invoice, step, attempt)."""

    KINDS = {
        "extract": ["PRICE_DECIMAL_SHIFT", "QTY_CHANGED", "LINE_DROPPED", "LINE_DUPLICATED"],
        "price": ["TOTAL_OFF", "TAX_WRONG_RATE", "SUBTOTAL_OFF_CONSISTENT"],
        "approve": ["DECISION_FLIPPED"],
        "report": ["REPORT_TOTAL_WRONG"],
    }

    def __init__(self, probability: float = 0.0, seed: int = 0, sticky: float = 0.0, repair_success: float = 0.9):
        """sticky: chance a retry repeats the first attempt's mistake (systematic error);
        repair_success: chance a guided retry fixes it. Simulation assumptions."""
        self.probability, self.seed = probability, seed
        self.sticky, self.repair_success = sticky, repair_success

    def apply(self, step, out, invoice_id, attempt, guided=False):
        if not self.probability or not isinstance(out, dict):
            return out, None
        if attempt > 0 and self.sticky > 0:
            srng = random.Random(f"{self.seed}:{invoice_id}:{step}:sticky:{attempt}")
            if srng.random() < self.sticky:
                first = self._apply(step, out, invoice_id, 0)
                if first[1] is not None:
                    if guided and srng.random() < self.repair_success:
                        return out, None
                    return first
        return self._apply(step, out, invoice_id, attempt)

    def _apply(self, step, out, invoice_id, attempt):
        rng = random.Random(hashlib.sha256(f"{self.seed}:{invoice_id}:{step}:{attempt}".encode()).hexdigest())
        if rng.random() >= self.probability:
            return out, None
        kind = rng.choice(self.KINDS[step])
        o = json.loads(json.dumps(out))
        try:
            if kind == "PRICE_DECIMAL_SHIFT":
                it = rng.choice(o["items"])
                it["unit_price"] = round(it["unit_price"] * 10, 2)
            elif kind == "QTY_CHANGED":
                it = rng.choice(o["items"])
                it["qty"] += rng.choice([1, 2])
            elif kind == "LINE_DROPPED":
                o["items"].pop(rng.randrange(len(o["items"])))
            elif kind == "LINE_DUPLICATED":
                o["items"].append(dict(rng.choice(o["items"])))
            elif kind == "TOTAL_OFF":
                o["total"] = round(o["total"] + rng.choice([-1, 1]) * rng.choice([0.01, 1.00, 10.00]), 2)
            elif kind == "TAX_WRONG_RATE":
                o["tax"] = round(o["subtotal"] * rng.choice([0.05, 0.10, 0.15]), 2)
                o["total"] = round(o["subtotal"] + o["tax"], 2)
            elif kind == "SUBTOTAL_OFF_CONSISTENT":
                o["subtotal"] = round(o["subtotal"] + rng.choice([5.00, 20.00]), 2)
                o["total"] = round(o["subtotal"] + o["tax"], 2)
            elif kind == "DECISION_FLIPPED":
                o["decision"] = "needs sign-off" if o["decision"] == "auto-approve" else "auto-approve"
            elif kind == "REPORT_TOTAL_WRONG":
                o["summary"] = re.sub(r"total \$([\d.]+)", lambda m: f"total ${float(m.group(1)) + 1:.2f}", o["summary"])
        except (KeyError, IndexError, TypeError, ValueError):
            return out, None
        return (o, kind) if o != out else (out, None)


# ===================================================================== checks

TASKS = {
    "extract": "Read the invoice text and list the invoice id, the tax rate (percent) and every line item "
               "(description, quantity, unit price), in order.",
    "price": "From the line items and tax rate, compute each line total (qty x unit price), the subtotal, the tax "
             "(subtotal x rate / 100, rounded to cents) and the total (subtotal + tax).",
    "approve": f"Decide 'auto-approve' if the total is ${APPROVAL_LIMIT} or less, otherwise 'needs sign-off'.",
    "report": "Write the one-line summary 'ID: N items, subtotal $S, tax $T, total $X, DECISION' from the values.",
}


def source_for(step):
    def src(state):
        if step == "extract":
            return state["invoice_text"]
        if step == "price":
            return clean(state["extract"])
        if step == "approve":
            return {"total": clean(state["price"])["total"]}
        e, p, d = clean(state["extract"]), clean(state["price"]), clean(state["approve"])
        return {"invoice_id": e["invoice_id"], "n_items": len(e["items"]), "subtotal": p["subtotal"],
                "tax": p["tax"], "total": p["total"], "decision": d["decision"]}
    return src


def _explain(step, out, ref) -> str:
    out = clean(out)
    if not isinstance(out, dict):
        return "output is not a JSON object"
    for k, v in ref.items():
        if k not in out:
            return f"missing {k}"
        if not same(out[k], v):
            if isinstance(v, (int, float)):
                return f"{k} is {out[k]}, recomputed {v:.2f}"
            if isinstance(v, list) and k == "items":
                return _explain_items(out[k], v)
            if isinstance(v, list):
                return f"{k} do not match the recomputed values"
            return f"{k} is '{out[k]}', expected '{v}'"
    return "does not match"


def _explain_items(got, want) -> str:
    if not isinstance(got, list):
        return "items is not a list"
    if len(got) != len(want):
        return f"{len(got)} line items found, the invoice has {len(want)}"
    for i, (g, w) in enumerate(zip(got, want, strict=True), 1):
        if not isinstance(g, dict):
            return f"line {i} is malformed"
        if g.get("qty") != w["qty"]:
            return f"line {i} ({w['description']}): qty {g.get('qty')}, invoice says {w['qty']}"
        if not same(g.get("unit_price"), w["unit_price"]):
            return f"line {i} ({w['description']}): unit price {g.get('unit_price')}, invoice says {w['unit_price']:.2f}"
        if g.get("description") != w["description"]:
            return f"line {i}: description does not match the invoice"
    return "line items differ from the invoice"


# ===================================================================== the graph

class InvoiceState(TypedDict, total=False):
    invoice_text: str
    invoice_id: str
    extract: dict
    price: dict
    approve: dict
    report: dict
    trace: Annotated[list, operator.add]
    halted: dict
    crashed: str


def build_pilot_graph(guard: str = "rules", agents: str = "rules", client=None,
                      faults: PilotFaults | None = None, max_retries: int = 2, repair: str = "blind"):
    if guard not in ("none", "rules", "llm"):
        raise ValueError("guard must be none, rules or llm")
    if (guard == "llm" or agents == "llm") and client is None:
        raise ValueError("an LLM client is needed for guard='llm' or agents='llm'")
    faults = faults or PilotFaults(0.0)
    g = StateGraph(InvoiceState)
    prev = START
    for step in STEPS:
        agent = llm_agent(step, client) if (agents == "llm" and step in ("extract", "price")) else rule_agent(step)

        def node(state, attempt=0, feedback=None, step=step, agent=agent):
            guided = repair == "guided" and feedback is not None
            try:
                ref = reference(step, state)
            except Exception:
                ref = None
            try:
                out = agent(state, attempt, feedback if guided else None)
            except Exception as e:
                if _is_infrastructure(e):
                    raise
                return {step: {"_crash": f"{type(e).__name__}: {e}", "_fault": None, "_agent_ok": False}}
            agent_ok = ref is not None and same(out, ref)
            out, kind = faults.apply(step, out, state.get("invoice_id"), attempt, guided=guided)
            return {step: {**out, "_fault": kind, "_agent_ok": agent_ok}}

        node.__name__ = step
        check = None
        if guard == "rules" or (guard == "llm" and step in ("approve", "report")):
            def check(out, src, step=step):
                if isinstance(out, dict) and "_crash" in out:
                    return False, "agent crashed: " + out["_crash"]
                ref = _ref_from_source(step, src)
                return (True, None) if same(out, ref) else (False, _explain(step, out, ref))
        elif guard == "llm":
            inner = llm_check(TASKS[step], client=client)

            def check(out, src, inner=inner):
                if isinstance(out, dict) and "_crash" in out:
                    return False, "agent crashed: " + out["_crash"]
                return inner(clean(out), src)
        g.add_node(step, guarded_node(node, check, output_key=step, source_key=source_for(step),
                                      max_retries=max_retries, name=step, trace_key="trace"))
        if prev is START:
            g.add_edge(START, step)
        else:
            g.add_conditional_edges(prev, _route, {"stop": END, "go": step})
        prev = step
    g.add_conditional_edges(prev, _route, {"stop": END, "go": END})
    return g.compile()


def _route(state):
    if state.get("halted") or state.get("crashed"):
        return "stop"
    last = [s for s in STEPS if s in state][-1]
    return "stop" if "_crash" in (state.get(last) or {}) else "go"


def _ref_from_source(step, src):
    if step == "extract":
        return parse_invoice(src)
    if step == "price":
        return price_items(src)
    if step == "approve":
        return decide(src)
    return {"summary": (f"{src['invoice_id']}: {src['n_items']} items, subtotal ${src['subtotal']:.2f}, "
                        f"tax ${src['tax']:.2f}, total ${src['total']:.2f}, {src['decision']}")}


# ===================================================================== running

def run_with_pilot_graph(graph, text: str) -> dict:
    """Run one invoice. status: correct | wrong (silent) | halted | crashed."""
    expected = truth(text)  # ValueError for unreadable invoices goes to the caller
    inv_id = expected["extract"]["invoice_id"]
    try:
        s = graph.invoke({"invoice_text": text, "invoice_id": inv_id, "trace": []}, {"recursion_limit": 50})
        crashed = None
    except Exception as e:
        if _is_infrastructure(e):
            raise
        s, crashed = {"trace": []}, f"{type(e).__name__}: {e}"  # an unguarded step fed bad data fell over

    trace, detected, recovered, false_alarms, verdicts = [], 0, 0, 0, []
    upstream = {"invoice_text": text}
    for entry in s.get("trace", []):
        step = entry["step"]
        caught = False  # a wrong output of this step was rejected
        for a in entry["attempts"]:
            out = a["output"] or {}
            try:
                ref = reference(step, upstream)
            except Exception:
                ref = None
            out_ok = ref is not None and same(out, ref)
            checked = entry["status"] != "unchecked"
            issue = None if a["passed"] else (a["reason"] or "rejected")
            trace.append({"stage": step, "attempt": a["attempt"], "injected": out.get("_fault"),
                          "guard_issue": issue, "agent_ok": out.get("_agent_ok"), "output_ok": out_ok,
                          "checked": checked, "output": clean(out)})
            if checked:
                verdicts.append({"stage": step, "verdict": "accept" if a["passed"] else "reject",
                                 "reason": a["reason"]})
            if issue:
                if out_ok:
                    false_alarms += 1  # rejected a correct output: not a detection
                elif not caught:
                    detected += 1
                    caught = True
            elif caught and out_ok:
                recovered += 1
        if entry["status"] != "halted":
            upstream[step] = clean(entry["attempts"][-1]["output"])

    final = clean(s.get("report") or {}).get("summary") if not s.get("halted") else None
    if crashed or any("_crash" in (s.get(k) or {}) for k in STEPS if not s.get("halted")):
        status = "crashed"
        final = None
    elif s.get("halted"):
        status = "halted"
    else:
        status = "correct" if final == expected["report"]["summary"] else "wrong"
    return {"status": status, "final_output": final, "expected_output": expected["report"]["summary"],
            "invoice_id": inv_id, "trace": trace, "validator_log": verdicts, "detected": detected,
            "recovered": recovered, "false_alarms": false_alarms,
            "injected": any(t["injected"] for t in trace), "halted": s.get("halted"), "crash": crashed}


def run_invoice(text: str, guard: str = "rules", agents: str = "rules", client=None,
                probability: float = 0.0, seed: int = 0, max_retries: int = 2,
                repair: str = "blind", sticky: float = 0.0) -> dict:
    graph = build_pilot_graph(guard, agents, client, PilotFaults(probability, seed, sticky), max_retries, repair)
    return run_with_pilot_graph(graph, text)


def run_pilot_arm(invoices, guard, probability, seed, max_retries=2, agents="rules", client=None,
                  repair="blind", sticky=0.0) -> dict:
    graph = build_pilot_graph(guard, agents, client, PilotFaults(probability, seed, sticky), max_retries, repair)
    c: Counter[str] = Counter()
    missed: Counter[str] = Counter()
    for inv in invoices:
        r = run_with_pilot_graph(graph, inv["text"])
        c[r["status"]] += 1
        c["injected"] += int(r["injected"])
        for k in ("detected", "recovered", "false_alarms"):
            c[k] += r[k]
        if guard != "none":
            missed.update(t["injected"] for t in r["trace"] if t["injected"] and not t["guard_issue"])
    n = len(invoices)
    return {"trials": n, **{k: c[k] for k in ("correct", "wrong", "crashed", "halted", "injected",
                                               "detected", "recovered", "false_alarms")},
            "accuracy": round(c["correct"] / n, 4) if n else 0, "missed_by_type": dict(missed)}


def run_pilot_experiment(trials: int = 500, seed: int = 42, probability: float = 0.1, max_retries: int = 2,
                         sticky: float = 0.0) -> dict:
    """Paired run on generated invoices, same faults in every arm: no guard, rule
    guard with blind retry, and (when sticky > 0) rule guard with guided repair."""
    invoices = make_invoices(trials, seed)
    base = run_pilot_arm(invoices, "none", probability, seed, max_retries, sticky=sticky)
    prot = run_pilot_arm(invoices, "rules", probability, seed, max_retries, sticky=sticky)
    b_unsafe, p_unsafe = base["wrong"] + base["crashed"], prot["wrong"] + prot["crashed"]
    out = {"config": {"trials": trials, "seed": seed, "inject_probability": probability,
                      "max_retries": max_retries, "workflow": "invoice", "sticky": sticky},
           "baseline": base, "protected": prot,
           "cri": round(cri(b_unsafe, p_unsafe), 4), "crs": round(crs(prot["recovered"], prot["detected"]), 4)}
    if sticky > 0:
        g = run_pilot_arm(invoices, "rules", probability, seed, max_retries, repair="guided", sticky=sticky)
        out["guided"] = {**g, "cri": round(cri(b_unsafe, g["wrong"] + g["crashed"]), 4),
                         "crs": round(crs(g["recovered"], g["detected"]), 4)}
    return out
