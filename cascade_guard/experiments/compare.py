"""Paired comparison of the chain with and without the guard; writes report.json.

Run: cascade-guard compare   (or python -m cascade_guard.experiments.compare)
"""
import json

from cascade_guard.evaluation.runner import run_experiment
from cascade_guard.experiments.common import parse_args
from cascade_guard.reporting.report_generator import generate


def main(argv=None, out="report.json"):
    a = parse_args(argv, prog="cascade-guard compare", description="Paired rule-based comparison; writes report.json")
    full = run_experiment(a.trials, a.seed, a.probability, a.max_retries, "full")
    inv = run_experiment(a.trials, a.seed, a.probability, a.max_retries, "invariants")
    report = dict(full)
    report["invariants_only"] = {k: inv[k] for k in
                                 ("protected_accuracy", "detected_errors", "recovered_errors", "cri", "crs", "protected")}
    generate(report, out)
    print(json.dumps({k: v for k, v in report.items() if k not in ("baseline", "protected", "invariants_only")}, indent=2))
    print("invariants-only guard:", {k: v for k, v in report["invariants_only"].items() if k != "protected"})
    print(f"wrote {out}")
    return report


if __name__ == "__main__":
    main()
