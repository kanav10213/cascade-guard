"""Chain with the guard. Run: python -m cascade_guard.experiments.protected"""
import json

from cascade_guard.evaluation.error_injector import ErrorInjector
from cascade_guard.evaluation.metrics import accuracy
from cascade_guard.evaluation.runner import make_inputs, run_arm
from cascade_guard.experiments.common import parse_args
from cascade_guard.guard.cascade_guard import CascadeGuard


def main(argv=None):
    a = parse_args(argv, prog="cascade-guard protected", description="Chain with the guard")
    guard = CascadeGuard(a.mode, a.max_retries)
    res = run_arm(make_inputs(a.trials, a.seed), ErrorInjector(a.probability, a.seed), guard)
    res["accuracy"] = round(accuracy(res["correct"], res["trials"]), 4)
    print(json.dumps(res, indent=2))
    return res


if __name__ == "__main__":
    main()
