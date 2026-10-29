"""Cascade Guard: stop errors from cascading through chains of AI agents.

Check every hand-off between agents against its own input, retry with the
reason when a check fails, and stop safely instead of passing a wrong answer on.
"""
__version__ = "1.2.0"

__all__ = ["GuardHalted", "__version__", "guard_step", "guarded_node", "llm_check", "rule_check"]


def __getattr__(name):  # `from cascade_guard import guard_step` without importing everything up front
    if name in ("GuardHalted", "guard_step", "guarded_node", "llm_check", "rule_check"):
        from cascade_guard.guard import wrapper
        return getattr(wrapper, name)
    raise AttributeError(f"module 'cascade_guard' has no attribute {name!r}")
