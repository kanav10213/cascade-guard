def accuracy(s, t):
    """Share of trials whose delivered output was correct."""
    return s / t if t else 0


def cri(b, p):
    """Cascade Reduction Index: share of baseline unsafe failures removed.

    b = unsafe failures without the guard, p = unsafe failures with the guard.
    "Unsafe" means a wrong output delivered silently, or an uncontrolled crash.
    """
    return 0 if b == 0 else (b - p) / b


def crs(r, d):
    """Cascade Recovery Score: share of detected stage failures that were
    repaired by retry (as opposed to halting the order)."""
    return 0 if d == 0 else r / d
