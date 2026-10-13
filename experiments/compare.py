"""Paired comparison; writes report.json. Run: python -m experiments.compare"""
import json

from app.evaluation.runner import run_experiment
from app.reporting.report_generator import generate
from experiments.common import parse_args

if __name__ == "__main__":
    a = parse_args()
    full = run_experiment(a.trials, a.seed, a.probability, a.max_retries, "full")
    inv = run_experiment(a.trials, a.seed, a.probability, a.max_retries, "invariants")
    report = dict(full)
    report["invariants_only"] = {k: inv[k] for k in
                                 ("protected_accuracy", "detected_errors", "recovered_errors", "cri", "crs", "protected")}
    generate(report, "report.json")
    print(json.dumps({k: v for k, v in report.items() if k not in ("baseline", "protected", "invariants_only")}, indent=2))
    print("invariants-only guard:", {k: v for k, v in report["invariants_only"].items() if k != "protected"})
    print("wrote report.json")
