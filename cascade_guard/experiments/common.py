import argparse


def parse_args(argv=None, prog=None, description=None):
    p = argparse.ArgumentParser(prog=prog, description=description)
    p.add_argument("--trials", type=int, default=1000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--probability", type=float, default=0.1, help="fault probability per agent output")
    p.add_argument("--max-retries", type=int, default=2)
    p.add_argument("--mode", choices=["invariants", "full"], default="full")
    return p.parse_args(argv)
