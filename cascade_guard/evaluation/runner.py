import random
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from cascade_guard.agents.pipeline import AGENTS, STAGES, expected_output
from cascade_guard.evaluation.error_injector import ErrorInjector
from cascade_guard.evaluation.judgement import is_correct
from cascade_guard.evaluation.metrics import accuracy, cri, crs
from cascade_guard.guard.cascade_guard import CascadeGuard


def make_inputs(trials: int, seed: int) -> list:
    rng = random.Random(f"inputs:{seed}")
    out = []
    for _ in range(trials):
        nums = [rng.randint(1, 99) for _ in range(rng.randint(3, 8))]
        out.append(", ".join(map(str, nums)))
    return out


def stage_truth(raw: str) -> dict:
    """Correct output of every stage, from the rule-based agents."""
    truth: dict = {}
    for stage in STAGES:
        truth[stage] = AGENTS[stage](raw, truth)
    return truth


def _is_infrastructure(e: Exception) -> bool:
    """API outages and the budget cap stop the run; they are not agent errors."""
    return getattr(e, "infrastructure", False) or type(e).__module__.startswith("anthropic")


def _reference(stage, raw, state):
    """The rule-based answer for this stage given the state it actually received."""
    try:
        return AGENTS[stage](raw, state)
    except Exception:
        return None


def _call_agent(fn, raw, state, attempt, feedback=None):
    if getattr(fn, "accepts_attempt", False):
        if feedback is not None and getattr(fn, "accepts_feedback", False):
            return fn(raw, state, attempt=attempt, feedback=feedback)
        return fn(raw, state, attempt=attempt)
    return fn(raw, state)


REPAIRS = ("blind", "guided")


def rejection_reason(guard, issue):
    """The checker's own words when it has them (LLM validator), else the issue code."""
    return getattr(guard, "last_reason", None) or issue


def _feedback(guard, issue, out):
    return {"reason": rejection_reason(guard, issue), "previous_output": out}


def run_chain(raw: str, trial: int, injector: ErrorInjector, guard=None, agents: dict | None = None,
              repair: str = "blind") -> dict:
    """Run one order through the chain. status is one of:
    correct | wrong (silent) | crashed | halted (guard stopped it).

    `agents` defaults to the rule-based AGENTS; pass build_agents(client) for
    the LLM version. `guard` is a CascadeGuard, an LLMGuard or None.

    Each trace entry also records, for measuring per-agent accuracy:
      agent_ok       the agent's own output (before any injection) was correct
      upstream_ok    every stage accepted so far was correct
      output_ok      the output (after injection) is right for the input it was given
      matches_truth  the output (after injection) equals the ground truth

    repair="guided" tells the agent, on a retry, why its last answer was rejected
    (agents that accept `feedback`); "blind" simply asks again."""
    agents = agents or AGENTS
    truth = stage_truth(raw)
    state: dict = {}
    trace: list = []
    detected = recovered = false_alarms = 0
    missed_types: list = []

    def result(status, final=None):
        return {
            "status": status,
            "final_output": final,
            "expected_output": expected_output(raw),
            "trace": trace,
            "detected": detected,
            "recovered": recovered,
            "false_alarms": false_alarms,
            "missed_types": missed_types,
            "injected": any(t["injected"] for t in trace),
        }

    for stage in STAGES:
        attempt = 0
        caught_error = False  # a WRONG output of this stage was rejected (a true detection)
        feedback = None
        while True:
            upstream_ok = all(state[s] == truth[s] for s in state)
            try:
                out = _call_agent(agents[stage], raw, state, attempt,
                                  feedback if repair == "guided" else None)
            except Exception as e:
                if _is_infrastructure(e):
                    raise
                trace.append({"stage": stage, "attempt": attempt, "injected": None, "guard_issue": "AGENT_CRASH",
                              "agent_ok": False, "upstream_ok": upstream_ok,
                              "output_ok": False, "matches_truth": False})
                return result("crashed")
            agent_ok = out == truth[stage]
            out, kind = injector.inject(stage, out, trial, attempt, guided=repair == "guided" and attempt > 0)
            out_ok = out == _reference(stage, raw, state)  # correct given what this stage was fed
            issue = guard.check(stage, out, raw, state) if guard else None
            trace.append({"stage": stage, "attempt": attempt, "injected": kind, "guard_issue": issue,
                          "agent_ok": agent_ok, "upstream_ok": upstream_ok, "output_ok": out_ok,
                          "matches_truth": out == truth[stage], "output": out})
            if issue is None:
                if guard and kind:
                    missed_types.append(kind)
                elif guard and not out_ok:
                    missed_types.append("AGENT_ERROR")  # the agent's own mistake got through
                if caught_error and out_ok:
                    recovered += 1  # a caught error was repaired by a retry
                state[stage] = out
                break
            if out_ok:
                false_alarms += 1  # the guard rejected a correct output: not a detection
            elif not caught_error:
                detected += 1
                caught_error = True
            if attempt >= guard.max_retries:
                return result("halted")
            feedback = _feedback(guard, issue, out)
            attempt += 1

    final = state["report"].get("final_output") if isinstance(state.get("report"), dict) else None
    return result("correct" if is_correct(final, expected_output(raw)) else "wrong", final)


ENGINES = ("custom", "langgraph")


def _chain_runner(engine, injector, guard, agents, repair="blind"):
    """One function (raw, trial) -> result for the chosen orchestration engine."""
    if engine == "custom":
        return lambda raw, trial: run_chain(raw, trial, injector, guard, agents, repair)
    if engine == "langgraph":
        from cascade_guard.orchestration.langgraph_chain import build_graph, run_with_graph
        graph = build_graph(injector, guard, agents, repair)  # compiled once, reused for every order
        retries = guard.max_retries if guard else 0
        return lambda raw, trial: run_with_graph(graph, raw, trial, retries)
    raise ValueError(f"engine must be one of {ENGINES}")


def run_arm(inputs: list, injector: ErrorInjector, guard=None, agents: dict | None = None,
            workers: int = 1, return_results: bool = False, engine: str = "custom",
            repair: str = "blind") -> dict:
    run = _chain_runner(engine, injector, guard, agents, repair)
    if workers > 1:
        with ThreadPoolExecutor(workers) as pool:
            results = list(pool.map(lambda a: run(a[1], a[0]), enumerate(inputs)))
    else:
        results = [run(raw, i) for i, raw in enumerate(inputs)]
    c: Counter[str] = Counter()
    missed: Counter[str] = Counter()
    for r in results:
        c[r["status"]] += 1
        c["injected_trials"] += int(r["injected"])
        c["detected"] += r["detected"]
        c["recovered"] += r["recovered"]
        c["false_alarms"] += r["false_alarms"]
        missed.update(r["missed_types"])
    out = {
        "trials": len(inputs),
        "correct": c["correct"],
        "wrong": c["wrong"],
        "crashed": c["crashed"],
        "halted": c["halted"],
        "injected_trials": c["injected_trials"],
        "detected": c["detected"],
        "recovered": c["recovered"],
        "false_alarms": c["false_alarms"],
        "missed_by_type": dict(missed),
    }
    if return_results:
        out["results"] = results
    return out


def run_experiment(trials=500, seed=42, probability=0.1, max_retries=2, mode="full",
                   engine: str = "custom", sticky: float = 0.0, repair: str = "blind") -> dict:
    """sticky > 0 makes some faults repeat on a blind retry (systematic mistakes);
    the result then also carries a `guided` arm: same guard, but the agent is told
    why it was rejected (simulated with ErrorInjector.repair_success)."""
    inputs = make_inputs(trials, seed)
    injector = ErrorInjector(probability, seed, sticky=sticky)
    base = run_arm(inputs, injector, engine=engine)
    prot = run_arm(inputs, injector, CascadeGuard(mode, max_retries), engine=engine, repair=repair)
    b_unsafe = base["wrong"] + base["crashed"]
    p_unsafe = prot["wrong"] + prot["crashed"]
    return {
        "config": {"trials": trials, "seed": seed, "inject_probability": probability,
                   "max_retries": max_retries, "guard_mode": mode, "engine": engine,
                   "sticky": sticky, "repair": repair},
        "baseline_accuracy": round(accuracy(base["correct"], trials), 4),
        "protected_accuracy": round(accuracy(prot["correct"], trials), 4),
        "detected_errors": prot["detected"],
        "recovered_errors": prot["recovered"],
        "cri": round(cri(b_unsafe, p_unsafe), 4),
        "crs": round(crs(prot["recovered"], prot["detected"]), 4),
        "baseline": base,
        "protected": prot,
        **({"guided": _guided_summary(inputs, injector, mode, max_retries, engine, b_unsafe)}
           if sticky > 0 and repair == "blind" else {}),
    }


def _guided_summary(inputs, injector, mode, max_retries, engine, b_unsafe) -> dict:
    g = run_arm(inputs, injector, CascadeGuard(mode, max_retries), engine=engine, repair="guided")
    return {"protected_accuracy": round(accuracy(g["correct"], len(inputs)), 4),
            "cri": round(cri(b_unsafe, g["wrong"] + g["crashed"]), 4),
            "crs": round(crs(g["recovered"], g["detected"]), 4), "protected": g}
