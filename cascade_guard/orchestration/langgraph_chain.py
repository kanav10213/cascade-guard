"""The guarded chain as a LangGraph StateGraph.

Same agents, same fault injector, same guards and the same result dict as
cascade_guard.evaluation.runner.run_chain; tests/test_langgraph_storage.py checks that both
engines give identical results on identical seeds.

Graph (one agent node and one guard node per stage):

    START -> extract -> guard_extract -> analyze -> guard_analyze -> ...
                ^             |
                +-- retry ----+          any guard --halt--> END
    ... -> report -> guard_report -> finish -> END
    an agent that crashes --> END

    graph = build_graph(injector, guard, agents)      # compiled once per config
    result = run_chain_langgraph(raw, trial, injector, guard, agents)
"""
import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from cascade_guard.agents.pipeline import AGENTS, STAGES, expected_output
from cascade_guard.evaluation.judgement import is_correct
from cascade_guard.evaluation.runner import _call_agent, _feedback, _is_infrastructure, _reference, stage_truth


class ChainState(TypedDict, total=False):
    raw: str
    trial: int
    truth: dict
    committed: dict                                  # accepted output of each stage
    trace: Annotated[list, operator.add]             # one entry per attempt, appended
    missed_types: Annotated[list, operator.add]
    attempt: int
    detected: int
    recovered: int
    false_alarms: int
    candidate: Any                                   # output waiting for the guard
    pending: dict                                    # flags about the candidate
    feedback: dict | None                         # why the last attempt was rejected (guided repair)
    rejected: bool                                   # the guard just rejected the candidate: retry
    caught_error: bool                               # a wrong output of this stage was rejected
    status: str | None                            # crashed | halted | correct | wrong
    final_output: str | None


def _agent_node(stage, agents, injector, repair="blind"):
    def node(s: ChainState) -> dict:
        committed, attempt = s["committed"], s["attempt"]
        truth = s["truth"]
        upstream_ok = all(committed[k] == truth[k] for k in committed)
        try:
            out = _call_agent(agents[stage], s["raw"], committed, attempt,
                              s.get("feedback") if repair == "guided" else None)
        except Exception as e:
            if _is_infrastructure(e):
                raise
            return {"status": "crashed",
                    "trace": [{"stage": stage, "attempt": attempt, "injected": None, "guard_issue": "AGENT_CRASH",
                               "agent_ok": False, "upstream_ok": upstream_ok,
                               "output_ok": False, "matches_truth": False}]}
        agent_ok = out == truth[stage]
        out, kind = injector.inject(stage, out, s["trial"], attempt, guided=repair == "guided" and attempt > 0)
        return {"candidate": out,
                "pending": {"kind": kind, "agent_ok": agent_ok, "upstream_ok": upstream_ok,
                            "output_ok": out == _reference(stage, s["raw"], committed),
                            "matches_truth": out == truth[stage]}}
    node.__name__ = stage
    return node


def _guard_node(stage, guard):
    def node(s: ChainState) -> dict:
        out, p, attempt = s["candidate"], s["pending"], s["attempt"]
        issue = guard.check(stage, out, s["raw"], s["committed"]) if guard else None
        entry = {"stage": stage, "attempt": attempt, "injected": p["kind"], "guard_issue": issue,
                 "agent_ok": p["agent_ok"], "upstream_ok": p["upstream_ok"], "output_ok": p["output_ok"],
                 "matches_truth": p["matches_truth"], "output": out}
        if issue is None:                                   # accept: commit, next stage
            missed = []
            if guard and p["kind"]:
                missed = [p["kind"]]
            elif guard and not p["output_ok"]:
                missed = ["AGENT_ERROR"]
            return {"trace": [entry], "missed_types": missed,
                    "committed": {**s["committed"], stage: out},
                    "recovered": s["recovered"] + int(s["caught_error"] and p["output_ok"]),
                    "attempt": 0, "caught_error": False, "rejected": False, "candidate": None, "feedback": None}
        new_catch = not p["output_ok"] and not s["caught_error"]
        update = {"trace": [entry], "rejected": True,
                  "detected": s["detected"] + int(new_catch),
                  "caught_error": s["caught_error"] or not p["output_ok"],
                  "false_alarms": s["false_alarms"] + int(p["output_ok"])}
        if attempt >= guard.max_retries:
            update["status"] = "halted"
        else:
            update["attempt"] = attempt + 1
            update["feedback"] = _feedback(guard, issue, out)
        return update
    node.__name__ = f"guard_{stage}"
    return node


def _finish(s: ChainState) -> dict:
    report = s["committed"].get("report")
    final = report.get("final_output") if isinstance(report, dict) else None
    return {"final_output": final,
            "status": "correct" if is_correct(final, expected_output(s["raw"])) else "wrong"}


def build_graph(injector, guard=None, agents: dict | None = None, repair: str = "blind"):
    """Compile the guarded chain for one (injector, guard, agents, repair) setup."""
    agents = agents or AGENTS
    g = StateGraph(ChainState)
    for stage in STAGES:
        g.add_node(stage, _agent_node(stage, agents, injector, repair))
        g.add_node(f"guard_{stage}", _guard_node(stage, guard))
    g.add_node("finish", _finish)  # type: ignore[call-overload]  # plain dict state; LangGraph accepts it at run time
    g.add_edge(START, STAGES[0])
    for i, stage in enumerate(STAGES):
        nxt = STAGES[i + 1] if i + 1 < len(STAGES) else "finish"
        g.add_conditional_edges(stage, lambda s: "end" if s.get("status") else "check",
                                {"end": END, "check": f"guard_{stage}"})
        g.add_conditional_edges(
            f"guard_{stage}",
            lambda s: "halt" if s.get("status") else ("retry" if s.get("rejected") else "next"),
            {"halt": END, "retry": stage, "next": nxt})
    g.add_edge("finish", END)
    return g.compile()


def run_with_graph(graph, raw: str, trial: int, max_retries: int = 2) -> dict:
    s = graph.invoke(
        {"raw": raw, "trial": trial, "truth": stage_truth(raw), "committed": {}, "trace": [],
         "missed_types": [], "attempt": 0, "caught_error": False, "rejected": False, "detected": 0, "recovered": 0,
         "false_alarms": 0, "candidate": None, "pending": {}, "status": None, "final_output": None,
         "feedback": None},
        {"recursion_limit": 2 * len(STAGES) * (max_retries + 2) + 10})
    return {
        "status": s["status"],
        "final_output": s.get("final_output"),
        "expected_output": expected_output(raw),
        "trace": s["trace"],
        "detected": s["detected"],
        "recovered": s["recovered"],
        "false_alarms": s["false_alarms"],
        "missed_types": s["missed_types"],
        "injected": any(t["injected"] for t in s["trace"]),
    }


def run_chain_langgraph(raw: str, trial: int, injector, guard=None, agents: dict | None = None,
                        repair: str = "blind") -> dict:
    """Drop-in replacement for run_chain that executes on LangGraph."""
    graph = build_graph(injector, guard, agents, repair)
    return run_with_graph(graph, raw, trial, guard.max_retries if guard else 0)


def mermaid(injector=None, guard=None) -> str:
    """The graph as a Mermaid diagram (for docs and slides)."""
    from cascade_guard.evaluation.error_injector import ErrorInjector
    return build_graph(injector or ErrorInjector(0.0), guard).get_graph().draw_mermaid()
