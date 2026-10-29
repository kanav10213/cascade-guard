from cascade_guard.agents.pipeline import AGENTS, STAGES, expected_output
from cascade_guard.evaluation.error_injector import ErrorInjector
from cascade_guard.evaluation.runner import make_inputs, run_chain, run_experiment
from cascade_guard.guard.cascade_guard import CascadeGuard

RAW = "3, 7, 2, 9"


def test_clean_chain_is_correct():
    r = run_chain(RAW, 0, ErrorInjector(0.0))
    assert r["status"] == "correct"
    assert r["final_output"] == expected_output(RAW)


def test_clean_chain_with_guard_has_no_false_alarms():
    guard = CascadeGuard("full")
    for i, raw in enumerate(make_inputs(300, 1)):
        r = run_chain(raw, i, ErrorInjector(0.0), guard)
        assert r["status"] == "correct" and r["false_alarms"] == 0


def test_injector_is_deterministic():
    out = {"count": 4, "sum": 21, "mean": 5.25, "max": 9}
    a = ErrorInjector(1.0, seed=7).inject("analyze", out, 3, 0)
    b = ErrorInjector(1.0, seed=7).inject("analyze", out, 3, 0)
    assert a == b


def test_injector_never_mutates_input():
    out = {"numbers": [1, 2, 3]}
    ErrorInjector(1.0, seed=1).inject("extract", out, 0, 0)
    assert out == {"numbers": [1, 2, 3]}


def test_faults_hurt_baseline_and_guard_helps():
    r = run_experiment(trials=400, seed=5, probability=0.2)
    assert r["baseline_accuracy"] < 1.0
    assert r["protected_accuracy"] > r["baseline_accuracy"]
    assert r["protected"]["false_alarms"] == 0
    assert 0.0 <= r["cri"] <= 1.0


def test_source_check_matters():
    """Invariants-only cannot see errors injected at the first stage; the source check can."""
    full = run_experiment(trials=600, seed=9, probability=0.3, mode="full")
    inv = run_experiment(trials=600, seed=9, probability=0.3, mode="invariants")
    assert full["protected_accuracy"] > inv["protected_accuracy"]
    assert inv["protected"]["wrong"] > 0
    assert full["protected"]["wrong"] == 0


def test_experiment_is_reproducible():
    assert run_experiment(200, seed=3) == run_experiment(200, seed=3)


def test_stages_cover_agents():
    assert set(STAGES) == set(AGENTS)
