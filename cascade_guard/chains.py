"""User-defined agent chains: build your own multi-step agent, with the guard on every step.

A chain is data, not code. Each step has a name, an instruction, who does it (the configured
model, or your own agent at a URL: see agent_call.py), an output format (plain text, or JSON with
named keys) and a check:

    model    the reply's format, then the LLM checker: is this output a correct result of the
             instruction on this input? (plus any extra acceptance criteria you write)
    format   only the format (non-empty text within max_chars, or every JSON key present and filled)
    none     no check: the output is passed on as it is

Each step works on the previous step's output (the first step on the chain's input) and can also
see the chain's original input. The chain runs on LangGraph: one guarded node per step, built from
the definition at run time. A rejected step is retried (with the checker's reason under guided
repair); if it is still rejected the chain stops and the run goes to the review queue.

    chain = ChainDef.model_validate(json_definition)
    result = run_chain_def(chain, "the input text", client)

`inject` plants a test mistake in the first answer of a step with that probability (retries are
not affected), so you can watch the guard catch errors even when the model makes none.
"""
import json
import operator
import random
import re
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field, field_validator, model_validator

from cascade_guard.guard.wrapper import guarded_node
from cascade_guard.llm.prompts import parse_last_json, repair_suffix

MAX_STEPS = 10
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,59}$")
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
AGENT_MAX_TOKENS = 700

CHAIN_SYSTEM = (
    "You are the step \"{name}\" in a chain of AI agents.\n"
    "Your instruction:\n{instruction}\n\n"
    "DATA holds INPUT (what you work on: the previous step's output, or the chain's input for the "
    "first step) and ORIGINAL (the chain's input). Work only from DATA; do not invent facts.\n"
    "{format}\n"
    "If PREVIOUS and FEEDBACK are given, a checker rejected your previous answer: do the task again "
    "from DATA, fix the problem FEEDBACK names, and reply in the same format."
)
FORMAT_TEXT = 'Reply with only one JSON object, {"output": "<your answer as plain text>"}, and nothing else.'
FORMAT_JSON = "Reply with only one JSON object with exactly these keys: {keys}. Fill every key."


# ---------------------------------------------------------------- the definition

class StepDef(BaseModel):
    name: str = Field(min_length=1, max_length=60, description="unique within the chain")
    instruction: str = Field(min_length=1, max_length=4000, description="what this agent must do, in plain words")
    output: Literal["text", "json"] = "text"
    keys: list[str] = Field(default_factory=list, max_length=12, description="output keys (json only)")
    check: Literal["model", "format", "none"] = "model"
    criteria: str = Field("", max_length=1000, description="extra acceptance criteria for the model checker")
    max_chars: int | None = Field(None, ge=1, le=20_000, description="longest acceptable text output")
    agent: Literal["model", "url", "connected"] = Field(
        "model", description="model: the configured model; connected: a connected agent by name; url: any agent URL")
    agent_name: str = Field("", max_length=40, description="the connected agent's name (agent=connected)")
    url: str = Field("", max_length=2000, description="your agent's HTTP endpoint (agent=url)")
    auth_env: str = Field("", max_length=64, description="server environment variable that holds the agent's bearer token")
    timeout: float | None = Field(None, gt=0, le=300, description="seconds to wait for your agent (default 90)")

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        v = " ".join(v.split())
        if not NAME_RE.match(v):
            raise ValueError("step names use letters, digits, spaces, '-', '_' or '.', and start with a letter or digit")
        return v

    @field_validator("instruction", "criteria")
    @classmethod
    def _strip(cls, v):
        return v.strip()

    @field_validator("keys")
    @classmethod
    def _keys(cls, v):
        v = [k.strip() for k in v if k.strip()]
        bad = [k for k in v if not KEY_RE.match(k)]
        if bad:
            raise ValueError(f"keys must be simple names like action_items (not {bad})")
        if len(set(v)) != len(v):
            raise ValueError("keys must be different from each other")
        return v

    @model_validator(mode="after")
    def _format(self):
        if not self.instruction:
            raise ValueError(f"step {self.name!r} needs an instruction")
        if self.output == "json" and not self.keys:
            raise ValueError(f"step {self.name!r} returns JSON, so give at least one key")
        if self.output == "text":
            self.keys = []
        if self.agent == "url":
            if not self.url.strip().lower().startswith(("http://", "https://")):
                raise ValueError(f"step {self.name!r} is done by your agent, so give its URL (http:// or https://)")
            self.url = self.url.strip()
            if self.auth_env and not re.match(r"^[A-Z_][A-Z0-9_]{0,63}$", self.auth_env):
                raise ValueError("auth_env is the NAME of an environment variable, like MY_AGENT_TOKEN, not the token")
            self.agent_name = ""
        elif self.agent == "connected":
            self.agent_name = self.agent_name.strip().lower()
            if not self.agent_name:
                raise ValueError(f"step {self.name!r} is done by a connected agent, so name which one")
            self.url, self.auth_env, self.timeout = "", "", None
        else:
            self.url, self.auth_env, self.timeout, self.agent_name = "", "", None, ""
        return self


class ChainDef(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field("", max_length=500)
    steps: list[StepDef] = Field(min_length=1, max_length=MAX_STEPS)
    max_retries: int = Field(2, ge=0, le=5)
    repair: Literal["guided", "blind"] = "guided"

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        v = " ".join(v.split())
        if not v:
            raise ValueError("the chain needs a name")
        return v

    @model_validator(mode="after")
    def _unique(self):
        names = [s.name.lower() for s in self.steps]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"step names must be unique (repeated: {', '.join(dup)})")
        return self


# ---------------------------------------------------------------- one step

def step_messages(step: StepDef, step_input, original: str, feedback=None):
    fmt = FORMAT_TEXT if step.output == "text" else FORMAT_JSON.format(keys=", ".join(step.keys))
    system = CHAIN_SYSTEM.format(name=step.name, instruction=step.instruction, format=fmt)
    user = (f"TASK: chain_step\nSTEP: {step.name}\nOUTPUT: {step.output}\nKEYS: {','.join(step.keys)}\n"
            f"DATA: {json.dumps({'input': step_input, 'original': original}, default=str)}"
            + repair_suffix(feedback))
    return system, user


def parse_step_reply(step: StepDef, text: str):
    """The step's output from the model's reply, or None when it is not the requested JSON."""
    data = parse_last_json(text)
    if not isinstance(data, dict):
        return None
    if step.output == "text":
        out = data.get("output")
        return out.strip() if isinstance(out, str) else None
    return {k: data.get(k) for k in step.keys} if any(k in data for k in step.keys) else None


def _filled(v) -> bool:
    return v not in (None, "", [], {}) and not (isinstance(v, str) and not v.strip())


def format_problem(step: StepDef, out) -> str | None:
    """Why the output does not have the requested shape, or None."""
    if out is None:
        return "the reply was not the requested JSON object"
    if step.output == "text":
        if not isinstance(out, str) or not out.strip():
            return "the output is empty"
        if step.max_chars and len(out) > step.max_chars:
            return f"the output has {len(out)} characters, more than the limit of {step.max_chars}"
        return None
    missing = [k for k in step.keys if not _filled(out.get(k))]
    return f"missing or empty: {', '.join(missing)}" if missing else None


def checker_task(step: StepDef) -> str:
    t = f"Step \"{step.name}\"{' (external agent)' if step.agent != 'model' else ''}: {step.instruction}"
    if step.criteria:
        t += f"\nAlso check these acceptance criteria: {step.criteria}"
    if step.output == "json":
        t += f"\nThe output is JSON with the keys {', '.join(step.keys)}."
    return t


def inject_fault(step: StepDef, out, rng: random.Random):
    """A realistic test mistake: an empty or cut-off text, a missing or blanked key."""
    if step.output == "text":
        if not isinstance(out, str) or len(out) < 8 or rng.random() < 0.3:
            return "", "EMPTY_OUTPUT"
        return out[: max(1, len(out) // 3)], "TRUNCATED_OUTPUT"
    out = dict(out or {})
    key = rng.choice(step.keys)
    if rng.random() < 0.5:
        out.pop(key, None)
        return out, "MISSING_KEY"
    out[key] = "N/A"
    return out, "WRONG_VALUE"


# ---------------------------------------------------------------- the whole chain on LangGraph

class ChainState(TypedDict, total=False):
    input: str
    outputs: Annotated[dict, lambda a, b: {**(a or {}), **(b or {})}]
    current: Any
    trace: Annotated[list, operator.add]
    halted: dict


def build_chain_graph(chain: ChainDef, client, *, inject: float = 0.0, seed: int = 0,
                      max_retries: int | None = None, repair: str | None = None, log: list | None = None,
                      http_client=None):
    """A LangGraph StateGraph built from the definition: one guarded node per step."""
    from langgraph.graph import END, START, StateGraph

    retries = chain.max_retries if max_retries is None else max_retries
    resolved = resolve_connected(chain)
    guided = (repair or chain.repair) == "guided"
    log = [] if log is None else log
    g = StateGraph(ChainState)
    names = [s.name for s in chain.steps]

    def source_of(i):
        def src(state):
            return state["input"] if i == 0 else state["outputs"][names[i - 1]]
        return src

    for i, step in enumerate(chain.steps):
        get_source = source_of(i)

        def node(state, attempt=0, feedback=None, step=step, get_source=get_source):
            src = get_source(state)
            if step.agent != "model":
                from cascade_guard.agent_call import call_agent
                out = call_agent(resolved.get(step.name, step), src, state["input"], attempt,
                                 feedback if guided else None, http_client)
                reply = json.dumps(out, default=str) if out is not None else "(no usable output)"
            else:
                system, user = step_messages(step, src, state["input"], feedback if guided else None)
                resp = client.complete(system, user, max_tokens=AGENT_MAX_TOKENS, sample=attempt,
                                       temperature=0.0 if attempt == 0 else 1.0, purpose=f"chain:{step.name}")
                out, reply = parse_step_reply(step, resp.text), resp.text
            injected = None
            if attempt == 0 and inject > 0:
                rng = random.Random(f"{seed}|{step.name}|{json.dumps(src, default=str)}")
                if rng.random() < inject:
                    out, injected = inject_fault(step, out, rng)
            log.append({"step": step.name, "attempt": attempt, "injected": injected, "reply": reply[:2000]})
            return {"outputs": {step.name: out}, "current": out}

        def check(out, src, step=step):
            problem = format_problem(step, out)
            if problem:
                log.append({"step": step.name, "check": "format", "verdict": "reject", "reason": problem})
                return False, problem
            if step.check != "model":
                return True, None
            from cascade_guard.guard.llm_guard import verify
            v = verify(client, checker_task(step), src, out, "recompute")
            log.append({"step": step.name, "check": "model", "verdict": v["verdict"], "reason": v["reason"]})
            return v["passed"], v["reason"] or v["verdict"]

        g.add_node(f"step_{i}", guarded_node(  # type: ignore[call-overload]
            node, None if step.check == "none" else check, output_key="current", source_key=get_source,
            max_retries=retries, name=step.name, trace_key="trace"))

    g.add_edge(START, "step_0")
    for i in range(len(chain.steps)):
        nxt = f"step_{i + 1}" if i + 1 < len(chain.steps) else END
        g.add_conditional_edges(f"step_{i}", lambda s, nxt=nxt: END if s.get("halted") else nxt, [nxt, END]
                                if nxt != END else [END])
    return g.compile(), log


def resolve_connected(chain: ChainDef) -> dict:
    """Steps done by a connected agent, with that agent's URL, token variable and timeout filled in.
    Raises AgentCallError for an unknown agent, before anything runs."""
    from cascade_guard.agent_call import AgentCallError
    from cascade_guard.agents_registry import find_agent
    out = {}
    for s in chain.steps:
        if s.agent == "connected":
            a = find_agent(s.agent_name)
            if a is None:
                raise AgentCallError(f"step {s.name!r} uses the connected agent {s.agent_name!r}, which does not exist "
                                     "(see Connected agents)")
            out[s.name] = s.model_copy(update={"url": a.url, "auth_env": a.auth_env, "timeout": a.timeout})
    return out


def mermaid(chain: ChainDef) -> str:
    lines = ["flowchart LR", "  start([input])"]
    prev = "start"
    for i, s in enumerate(chain.steps):
        label = s.name.replace('"', "'")
        who = {"url": "your agent", "connected": f"agent {s.agent_name}"}.get(s.agent, "model")
        lines.append(f'  s{i}["{label}<br/><small>{who} · check: {s.check}</small>"]')
        lines.append(f"  {prev} --> s{i}")
        if s.check != "none":
            lines.append(f"  s{i} -. rejected, retry .-> s{i}")
            lines.append(f"  s{i} -. still wrong .-> review([review queue])")
        prev = f"s{i}"
    lines.append(f"  {prev} --> done([output])")
    return "\n".join(lines)


def run_chain_def(chain: ChainDef, input_text: str, client, *, inject: float = 0.0, seed: int = 0,
                  max_retries: int | None = None, repair: str | None = None, http_client=None) -> dict:
    """Run a chain on one input. status: completed | halted. Includes every attempt and verdict.
    http_client: an httpx.Client for steps done by your own agent (tests pass a stand-in)."""
    before = client.usage.as_dict()
    graph, log = build_chain_graph(chain, client, inject=inject, seed=seed, max_retries=max_retries, repair=repair,
                                   http_client=http_client)
    state = graph.invoke({"input": input_text, "outputs": {}, "trace": []},
                         {"recursion_limit": 4 * len(chain.steps) + 10})
    after = client.usage.as_dict()
    halted = state.get("halted")
    steps, flat = [], []
    replies = [x for x in log if "reply" in x]
    checks = [x for x in log if "check" in x]
    detected = recovered = 0
    for entry in state.get("trace", []):
        attempts = []
        for a in entry["attempts"]:
            r: dict = next((x for x in replies if x["step"] == entry["step"] and x["attempt"] == a["attempt"]), {})
            attempts.append({"attempt": a["attempt"], "passed": a["passed"], "reason": a["reason"],
                             "output": a["output"], "injected": r.get("injected")})
            flat.append({"stage": entry["step"], "attempt": a["attempt"], "injected": r.get("injected"),
                         "guard_issue": None if a["passed"] else a["reason"], "output": a["output"]})
        rejected = sum(not a["passed"] for a in attempts)
        detected += 1 if rejected else 0
        recovered += 1 if rejected and entry["status"] == "passed" else 0
        steps.append({"step": entry["step"], "status": entry["status"], "attempts": attempts,
                      "output": attempts[-1]["output"] if attempts else None})
    final = None
    if not halted and steps:
        final = steps[-1]["output"]
    return {
        "chain": chain.name, "status": "halted" if halted else "completed",
        "halted_at": halted["step"] if halted else None,
        "final_output": final, "steps": steps, "trace": flat,
        "detected": detected, "recovered": recovered, "false_alarms": 0,
        "checks": checks,
        "usage": {k: round(after[k] - before[k], 6) if k == "cost_usd" else after[k] - before[k]
                  for k in ("calls", "cached_calls", "input_tokens", "output_tokens", "cost_usd")},
    }


def save_chain_run(store, result: dict, chain_name: str, input_text: str, model: dict,
                   chain_id: int | None = None) -> int:
    """Save a chain run with its steps and verdicts; a stopped run goes to the review queue."""
    final = result["final_output"]
    saved = dict(result, final_output=final if isinstance(final, str) or final is None
                 else json.dumps(final, default=str), chain_id=chain_id)
    return store.save_pipeline_run(
        saved, input_text, guard="chain", engine="langgraph", model=model, kind="chain",
        usage_delta=result["usage"],
        validator_log=[{"stage": c["step"], "verdict": c["verdict"], "reason": c["reason"]} for c in result["checks"]],
        prompt=f"chain:{chain_name}"[:100])
