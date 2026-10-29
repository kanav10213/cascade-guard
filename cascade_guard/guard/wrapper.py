"""Put check -> retry -> halt around ANY step of ANY Python pipeline.

Plain Python:

    from cascade_guard.guard.wrapper import guard_step, llm_check, GuardHalted

    @guard_step(llm_check("Add up the invoice line items and give the total."), max_retries=2)
    def total_agent(invoice_text):
        return my_llm_call(invoice_text)            # any agent, any framework

    try:
        total = total_agent(text)                   # checked; retried if rejected
    except GuardHalted as stop:
        send_to_review(stop.report)                 # nothing wrong goes further

A LangGraph node:

    graph.add_node("total", guarded_node(total_node, check, source_key="invoice_text",
                                         output_key="total"))
    # on halt the node writes state["halted"] = {...}; route on it with a conditional edge

A check is any function check(output, source) returning one of:
    True / False,  (passed, reason),  or a dict with "passed" (and optionally "reason").
`llm_check` builds one from the LLM validator; `rule_check` from a plain Python function.
"""
import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepReport:
    step: str
    status: str                       # passed | halted
    output: Any
    attempts: int
    verdicts: list = field(default_factory=list)   # [{"attempt", "passed", "reason"}]


class GuardHalted(Exception):
    """Raised when a step still fails its check after all retries."""

    def __init__(self, report: StepReport):
        self.report = report
        last = report.verdicts[-1]["reason"] if report.verdicts else ""
        super().__init__(f"step {report.step!r} halted after {report.attempts} attempts: {last}")


def _normalise(verdict):
    if isinstance(verdict, dict):
        return bool(verdict.get("passed")), verdict.get("reason")
    if isinstance(verdict, tuple):
        return bool(verdict[0]), (verdict[1] if len(verdict) > 1 else None)
    return bool(verdict), None


def _accepts(fn, name) -> bool:
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _accepts_attempt(fn) -> bool:
    return _accepts(fn, "attempt")


def run_guarded(fn: Callable, check: Callable, *args, max_retries: int = 2, name: str | None = None,
                source: Any | None = None, **kwargs) -> StepReport:
    """Call fn(*args, **kwargs), check the output against its source, retry up to
    max_retries times, and report. source defaults to the first argument.

    Guided repair: if fn has a `feedback` parameter, every retry gets
    feedback={"reason": <why the checker rejected it>, "previous_output": <it>}
    so the agent can fix the named problem instead of guessing again."""
    name = name or getattr(fn, "__name__", "step")
    src = source if source is not None else (args[0] if args else kwargs)
    pass_attempt, pass_feedback = _accepts_attempt(fn), _accepts(fn, "feedback")
    verdicts: list[dict[str, Any]] = []
    out = None
    for attempt in range(max_retries + 1):
        extra: dict[str, Any] = {}
        if pass_attempt:
            extra["attempt"] = attempt
        if pass_feedback:
            extra["feedback"] = None if attempt == 0 else {"reason": verdicts[-1]["reason"],
                                                           "previous_output": verdicts[-1]["output"]}
        out = fn(*args, **kwargs, **extra)
        passed, reason = _normalise(check(out, src)) if check is not None else (True, None)
        verdicts.append({"attempt": attempt, "passed": passed, "reason": reason, "output": out})
        if passed:
            return StepReport(str(name), "passed", out, attempt + 1, verdicts)
    return StepReport(str(name), "halted", out, max_retries + 1, verdicts)


def guard_step(check: Callable, max_retries: int = 2, name: str | None = None,
               source: Callable | None = None):
    """Decorator: the wrapped function returns its checked output, or raises
    GuardHalted. source(*args, **kwargs) picks what the output is checked
    against (default: the first argument)."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            src = source(*args, **kwargs) if source else None
            report = run_guarded(fn, check, *args, max_retries=max_retries, name=name or fn.__name__,
                                 source=src, **kwargs)
            wrapper.last_report = report  # convenience for single-threaded use
            if report.status == "halted":
                raise GuardHalted(report)
            return report.output
        return wrapper
    return deco


def llm_check(task: str, client=None, prompt: str = "recompute") -> Callable:
    """A check that asks the LLM validator: is `output` a correct result of `task` on `source`?
    Uses the configured model (.env) unless a client is given."""
    from cascade_guard.guard.llm_guard import verify

    def check(output, source):
        nonlocal client
        if client is None:
            from cascade_guard.llm.client import make_client
            client = make_client(strict_prices=False)
        r = verify(client, task, _jsonable(source), _jsonable(output), prompt)
        return r["passed"], r["reason"]
    return check


def rule_check(fn: Callable[[Any, Any], Any], reason: str = "rule check failed") -> Callable:
    """Wrap a plain predicate fn(output, source) -> bool as a check."""
    def check(output, source):
        try:
            ok = bool(fn(output, source))
        except Exception as e:  # a check that crashes must not wave the output through
            return False, f"{reason}: {type(e).__name__}: {e}"
        return ok, None if ok else reason
    return check


def guarded_node(node: Callable, check: Callable | None, *, output_key: str,
                 source_key: Any | None = None, max_retries: int = 2, name: str | None = None,
                 trace_key: str | None = None) -> Callable[[dict], dict]:
    """Wrap a LangGraph node (state -> update) with check, retry and halt.

    The update's `output_key` is checked against the source: state[source_key],
    or source_key(state) when it is a function. A node that takes an `attempt`
    argument gets 0, 1, 2... so a retry can differ. check=None runs the node
    unchecked (useful for a no-guard baseline). On halt the node returns
    {"halted": {...}} instead of its update, so a conditional edge can route the
    run to END or to a person. With trace_key, every attempt is appended to
    state[trace_key] (declare it with an operator.add reducer)."""
    step = name or getattr(node, "__name__", output_key)
    pass_attempt, pass_feedback = _accepts_attempt(node), _accepts(node, "feedback")

    def wrapped(state: dict) -> dict:
        updates = {}
        src = source_key(state) if callable(source_key) else (state.get(source_key) if source_key else None)

        def fn(_src, attempt=0, feedback=None):  # each attempt re-runs the node on the same state
            extra: dict[str, Any] = {}
            if pass_attempt:
                extra["attempt"] = attempt
            if pass_feedback:
                extra["feedback"] = feedback
            upd = node(state, **extra)
            updates["last"] = upd
            return upd.get(output_key)

        # check=None means unchecked: run once and record it
        report = run_guarded(fn, check, src, max_retries=max_retries if check else 0,  # type: ignore[arg-type]
                             name=step, source=src)
        entry = {"step": step, "status": report.status if check else "unchecked",
                 "attempts": report.verdicts}
        extra = {trace_key: [entry]} if trace_key else {}
        if report.status == "halted":
            return {"halted": {"step": step, "attempts": report.attempts,
                               "verdicts": [{k: v for k, v in x.items() if k != "output"} for x in report.verdicts]},
                    **extra}
        return {**updates["last"], **extra}
    wrapped.__name__ = f"guarded_{step}"
    return wrapped


def _jsonable(v):
    if isinstance(v, (str, int, float, bool, type(None), list, dict)):
        return v
    return str(v)
