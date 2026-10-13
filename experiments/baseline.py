"""Chain without the guard. Run: python -m experiments.baseline"""
import json

from app.evaluation.error_injector import ErrorInjector
from app.evaluation.metrics import accuracy
from app.evaluation.runner import make_inputs, run_arm
from experiments.common import parse_args

if __name__ == "__main__":
    a = parse_args()
    res = run_arm(make_inputs(a.trials, a.seed), ErrorInjector(a.probability, a.seed))
    res["accuracy"] = round(accuracy(res["correct"], res["trials"]), 4)
    print(json.dumps(res, indent=2))
