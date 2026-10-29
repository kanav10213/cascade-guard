"""Chain without the guard. Run: python -m cascade_guard.experiments.baseline"""
import json

from cascade_guard.evaluation.error_injector import ErrorInjector
from cascade_guard.evaluation.metrics import accuracy
from cascade_guard.evaluation.runner import make_inputs, run_arm
from cascade_guard.experiments.common import parse_args


def main(argv=None):
    a = parse_args(argv, prog="cascade-guard baseline", description="Chain without the guard")
    res = run_arm(make_inputs(a.trials, a.seed), ErrorInjector(a.probability, a.seed))
    res["accuracy"] = round(accuracy(res["correct"], res["trials"]), 4)
    print(json.dumps(res, indent=2))
    return res


if __name__ == "__main__":
    main()
