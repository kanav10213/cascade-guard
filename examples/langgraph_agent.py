"""Guard the nodes of an existing LangGraph agent with guarded_node.

Run it:   python examples/langgraph_agent.py        (no key needed: the checker uses the simulator)
"""
import operator
import os
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from cascade_guard import guarded_node, llm_check, rule_check
from cascade_guard.llm.client import make_client

os.environ.setdefault("LLM_PROVIDER", "fake")


class State(TypedDict, total=False):
    ticket: str
    summary: str
    category: str
    trace: Annotated[list, operator.add]
    halted: dict


def summarise(state):            # your existing node
    return {"summary": state["ticket"].split(".")[0] + "."}


def categorise(state):           # your existing node
    return {"category": "billing" if "invoice" in state["summary"].lower() else "other"}


client = make_client()
g = StateGraph(State)
g.add_node("summarise", guarded_node(summarise, llm_check("Summarise the ticket in one sentence.", client=client),
                                     source_key="ticket", output_key="summary", trace_key="trace"))
g.add_node("categorise", guarded_node(categorise, rule_check(lambda out, s: out in ("billing", "delivery", "other"),
                                                             "unknown category"),
                                      source_key="summary", output_key="category", trace_key="trace"))
g.add_edge(START, "summarise")
g.add_conditional_edges("summarise", lambda s: END if s.get("halted") else "categorise", ["categorise", END])
g.add_edge("categorise", END)
graph = g.compile()

if __name__ == "__main__":
    out = graph.invoke({"ticket": "My invoice INV-1007 was charged twice. Please refund it.", "trace": []})
    print("halted" if out.get("halted") else f"summary: {out['summary']}\ncategory: {out['category']}")
    for t in out["trace"]:
        print(" ", t["step"], t["status"], [a["reason"] for a in t["attempts"]])
