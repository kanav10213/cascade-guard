import random
from collections import Counter

from app.agents.pipeline import AGENTS, STAGES, expected_output
from app.evaluation.error_injector import ErrorInjector
from app.evaluation.judgement import is_correct
from app.evaluation.metrics import accuracy, cri, crs
from app.guard.cascade_guard import CascadeGuard


def make_inputs(trials: int, seed: int) -> list:
    rng = random.Random(f"inputs:{seed}")
    out = []
    for _ in range(trials):
        nums = [rng.randint(1, 99) for _ in range(rng.randint(3, 8))]
        out.append(", ".join(map(str, nums)))
    return out


def run_chain(raw: str, trial: int, injector: ErrorInjector, guard: CascadeGuard = None) -> dict:
    """Run one order through the chain. status is one of:
    correct | wrong (silent) | crashed | halted (guard stopped it)."""
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
        incident = False
        while True:
            try:
                out = AGENTS[stage](raw, state)
            except Exception:
                trace.append({"stage": stage, "attempt": attempt, "injected": None, "guard_issue": "AGENT_CRASH"})
                return result("crashed")
            out, kind = injector.inject(stage, out, trial, attempt)
            issue = guard.check(stage, out, raw, state) if guard else None
            trace.append({"stage": stage, "attempt": attempt, "injected": kind, "guard_issue": issue})
            if issue is None:
                if guard and kind:
                    missed_types.append(kind)
                if incident and kind is None:
                    recovered += 1
                state[stage] = out
                break
            if not incident:
                detected += 1
                incident = True
            if kind is None:
                false_alarms += 1
            if attempt >= guard.max_retries:
                return result("halted")
            attempt += 1

    final = state["report"].get("final_output") if isinstance(state.get("report"), dict) else None
    return result("correct" if is_correct(final, expected_output(raw)) else "wrong", final)


def run_arm(inputs: list, injector: ErrorInjector, guard: CascadeGuard = None) -> dict:
    c = Counter()
    missed = Counter()
    for i, raw in enumerate(inputs):
        r = run_chain(raw, i, injector, guard)
        c[r["status"]] += 1
        c["injected_trials"] += int(r["injected"])
        c["detected"] += r["detected"]
        c["recovered"] += r["recovered"]
        c["false_alarms"] += r["false_alarms"]
        missed.update(r["missed_types"])
    return {
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


def run_experiment(trials=500, seed=42, probability=0.1, max_retries=2, mode="full") -> dict:
    inputs = make_inputs(trials, seed)
    injector = ErrorInjector(probability, seed)
    base = run_arm(inputs, injector)
    prot = run_arm(inputs, injector, CascadeGuard(mode, max_retries))
    b_unsafe = base["wrong"] + base["crashed"]
    p_unsafe = prot["wrong"] + prot["crashed"]
    return {
        "config": {"trials": trials, "seed": seed, "inject_probability": probability,
                   "max_retries": max_retries, "guard_mode": mode},
        "baseline_accuracy": round(accuracy(base["correct"], trials), 4),
        "protected_accuracy": round(accuracy(prot["correct"], trials), 4),
        "detected_errors": prot["detected"],
        "recovered_errors": prot["recovered"],
        "cri": round(cri(b_unsafe, p_unsafe), 4),
        "crs": round(crs(prot["recovered"], prot["detected"]), 4),
        "baseline": base,
        "protected": prot,
    }
