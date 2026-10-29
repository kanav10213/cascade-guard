# Cascade Guard

[![ci](https://github.com/kanav10213/cascade-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/kanav10213/cascade-guard/actions/workflows/ci.yml)
[![licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](https://github.com/kanav10213/cascade-guard/blob/main/LICENSE)

**Stop errors from cascading through chains of AI agents.**

When AI agents work in a chain, one agent's mistake becomes the next agent's input, and by the end the result is
confidently wrong. Cascade Guard checks every hand-off against the step's own input. If a check fails, it retries
and tells the agent *what* was wrong. If the step still fails, it stops the job safely and sends it to a person.
A wrong answer is never passed on silently.

- **Drop-in for your code:** wrap any Python step or LangGraph node with `guard_step` / `guarded_node`.
- **A service for everything else:** a REST API that checks any agent output against its source, plus a dashboard.
- **Inside AI assistants:** an MCP server for Claude Desktop, Claude Code and other MCP-capable tools.
- **Measurable:** fault injection and paired experiments that report what the guard actually buys (CRI, CRS, Wilson
  95% intervals), and a cost-gated evaluation on real models.
- **Any model:** Anthropic Claude by default; any OpenAI-format API (OpenAI, Gemini, Groq, OpenRouter, a local model
  through Ollama), or a free offline simulator.

> **Status: v1.0.0.** The software is complete, tested (Python 3.12 to 3.14, SQLite and PostgreSQL, Docker) and
> documented. The headline numbers below are from **simulations**; the evaluation on a real paid model is built and
> waiting to be run. See [Limits](#limits).

## Contents
[Install](#install) · [Quick start](#quick-start) · [Docker](#docker) · [Use it in your code](#use-it-in-your-code) ·
[Use it from Claude (MCP)](#use-it-from-claude-and-other-ai-assistants-mcp) · [Models](#models-and-providers) ·
[Configuration](#configuration) · [API](#api) · [Security](#security) · [Results](#results) ·
[How it works](#how-it-works) · [Evaluate on a real model](#evaluate-on-a-real-model) · [Development](#development) ·
[Limits](#limits) · [License](#license)

## Install
Python 3.12 or newer.

```bash
pip install cascade-guard-ai                          # from PyPI, once published
pip install cascade_guard_ai-1.0.0-py3-none-any.whl   # from a release file
pip install .                                         # from this folder
pip install "cascade-guard-ai[postgres]"              # with PostgreSQL support
```
The package is called `cascade-guard-ai` on PyPI (the name `cascade-guard` is too close to an existing project);
the command is `cascade-guard` and the import is `cascade_guard`. For the exact versions v1.0.0 was tested with,
from this folder: `pip install -r requirements.txt` (pins in `constraints.txt`).

**Windows, no command line:** double-click `start.bat`. It sets everything up on the first run, opens the settings in
Notepad for your API key, and starts the dashboard. Mac/Linux: `./start.sh`.

## Quick start
```bash
mkdir my-guard && cd my-guard
cascade-guard init        # creates .env here; put your API key in it (or LLM_PROVIDER=fake to try it free)
cascade-guard serve       # dashboard + API on http://localhost:8000, interactive API docs at /docs
```

Check an agent's output against its input:
```bash
curl -s localhost:8000/llm/verify -H 'content-type: application/json' \
  -d '{"task": "Add up the line items", "source": "3 x $4.00, 2 x $1.50", "output": "total $16.00"}'
# -> {"passed": false, "verdict": "reject", "reason": "<why, in the checker's words>", ...}
```

Other commands: `cascade-guard mcp` (MCP server), `cascade-guard compare` (rule-based experiment, writes
`report.json`), `cascade-guard eval all --dry-run` (real-model evaluation plan and cost), `cascade-guard --help`.

Every command works in a **working folder**: `--home` (before or after the command), else `CASCADE_HOME`, else the
current folder. It holds `.env`,
`data/` (the SQLite database) and `.llm_cache/` (cached model replies, so nothing is paid for twice).

## Docker
```bash
docker compose up -d --build                  # http://localhost:8000, reads ./.env if present, data in a volume
# or
docker build -t cascade-guard .
docker run -p 8000:8000 --env-file .env -e API_KEY=change-me -v cascade-data:/data cascade-guard
```
The image runs as a non-root user, keeps its data in `/data`, has a health check, and honours `PORT`, so it also runs
on Render, Railway, Fly.io and similar platforms (the `Procfile` covers platforms without Docker). The image installs
the tested versions from `constraints.txt`. Plain `docker run` without `API_KEY` answers nothing but `/health`. Compose
binds the port to `127.0.0.1` and sets `ALLOW_OPEN_NETWORK=1`, so your browser on this computer can use it without
keys (other containers and computers are refused by the Host-header check); to share it, set `API_KEY` first and
follow the comment in `docker-compose.yml`.

## Use it in your code
`guard_step` puts check, retry-with-reason and halt around any Python step, in any framework:

```python
from cascade_guard import GuardHalted, guard_step, guarded_node, llm_check, rule_check
from cascade_guard.llm.client import make_client

checker = llm_check("Add up the invoice line items and give the total.", client=make_client())

@guard_step(checker, max_retries=2)
def total_agent(invoice_text, feedback=None):        # feedback: why the last try was rejected (guided repair)
    return my_agent(invoice_text, hint=feedback)      # your agent, any model

try:
    total = total_agent(text)                         # checked against `text`; retried with the reason if rejected
except GuardHalted as stop:
    send_to_review(stop.report)                       # nothing wrong goes further

# deterministic checks need no model call
@guard_step(rule_check(lambda out, items: out == sum(items), "total does not match"))
def add(items): ...

# inside a LangGraph graph: on halt the node writes state["halted"]
graph.add_node("total", guarded_node(total_node, checker, source_key="invoice_text", output_key="total"))
```

Any other language or framework: call `POST /llm/verify` and act on `passed`.

## Use it from Claude and other AI assistants (MCP)
```bash
cascade-guard mcp --print-config    # prints the JSON for Claude Desktop and the `claude mcp add` command for Claude Code
```
Run it in the folder that holds your `.env`: the printed setup pins that folder as `CASCADE_HOME`. (Started without
it, the MCP server uses `~/.cascade-guard`, never the folder the AI app happens to start it in.)
Claude Desktop: paste the JSON into `claude_desktop_config.json` (Settings > Developer > Edit Config) and restart.
Claude Code: run the printed `claude mcp add ...` command.

| Tool | Does |
|---|---|
| `verify_output` | check any output against its source with the model checker |
| `check_invoice` | run an invoice through the guarded invoice workflow |
| `guard_status` | model, spend so far, open review items |
| `list_review_queue` | stopped jobs and failed checks (read-only; their text is marked as untrusted data) |

Deciding review items is deliberately **not** a tool: the queue holds outside text that could try to talk an assistant
into approving things, so people decide on the dashboard or through the API with a reviewer key.

## Models and providers
| Provider | In `.env` |
|---|---|
| Anthropic (default) | `ANTHROPIC_API_KEY=...` (model `claude-haiku-5-5` unless `LLM_MODEL` is set) |
| OpenAI | `LLM_PROVIDER=openai`, `OPENAI_API_KEY=...`, `LLM_MODEL=<model>` |
| Gemini / Groq / OpenRouter / Together | as OpenAI, plus `OPENAI_BASE_URL=` the provider's OpenAI-compatible URL |
| Ollama (free, local) | `LLM_PROVIDER=openai`, `OPENAI_BASE_URL=http://localhost:11434/v1`, `LLM_MODEL=llama3.2` |
| No key, just try it | `LLM_PROVIDER=fake` (simulated answers, clearly labelled, no cost) |

The Anthropic and OpenAI wire formats are covered by the tests (against a local stand-in server, with the real client
libraries); other OpenAI-compatible providers use the same code but have not been tried live.

## Configuration
Settings come from the environment or from `.env` in the working folder (the real environment wins).
`cascade-guard init` writes a commented template.

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic`, `openai` (any OpenAI-compatible API) or `fake` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | | the provider key |
| `LLM_MODEL` | `claude-haiku-5-5` | model name |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | for other OpenAI-compatible providers |
| `LLM_PRICE_IN`, `LLM_PRICE_OUT` | built in for Haiku 5.5 | USD per million tokens, for cost tracking and the spend cap |
| `LLM_MAX_COST`, `LLM_MAX_CALLS` | `1.00`, `1000` | per server process; the server stops calling the model after either |
| `API_KEY`, `REVIEWER_KEY`, `VIEWER_KEY` | none | access keys (see [Security](#security)) |
| `RATE_LIMIT_PER_MIN`, `LLM_RATE_LIMIT_PER_MIN` | `120`, `20` | requests per minute per key (or address) |
| `MAX_BODY_BYTES` | `8000000` | larger requests get 413, counted as they arrive (chunked uploads too) |
| `AUDIT` | `on` | record every POST and every refused request |
| `CASCADE_DB` | `data/cascade.db` | SQLite file, a `postgresql://` URL, or `off` |
| `RETENTION_DAYS` | keep all | delete saved runs older than this at start-up |
| `CASCADE_HOME` | current folder | working folder for `.env`, `data/`, `.llm_cache/` |
| `LLM_CACHE` | `.llm_cache/service.jsonl` | reply cache file (`:memory:` keeps it in memory only) |
| `HOST`, `PORT` | `127.0.0.1`, `8000` | where `serve` listens (`--host`/`--port` win; the Docker image uses `0.0.0.0`) |
| `MAX_TRIALS` | `2000` | cap on `/experiment` size |
| `LLM_EVAL_MAX_COST` | `5.0` | highest cost cap a dashboard or API evaluation may use, USD |
| `ALLOW_OPEN_NETWORK` | off | `1` drops the address check while no keys are set (Docker Compose uses it; the Host check stays) |
| `ALLOWED_HOSTS` | | extra Host names accepted while no keys are set, comma-separated |
| `AUDIT_REFUSED_PER_MIN` | `30` | refused requests recorded per caller per minute (a flood cannot fill the table) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | proxies trusted for `X-Forwarded-For` with `serve --proxy-headers` (needs keys) |
| `LOG_LEVEL` | `info` | server log level |

The spend cap needs a known price (built in for `claude-haiku-5-5`; set `LLM_PRICE_IN`/`LLM_PRICE_OUT` for other
models); the call cap always works. Both are checked before each call, so parallel calls can overshoot slightly, and
they count per process (`serve --workers N` multiplies them).

## API
Interactive documentation: `/docs` on a running server.

| Route | Purpose | Role |
|---|---|---|
| `GET /health` | liveness and version (always open) | none |
| `GET /health/ready` | database reachable, model configured (for load balancers) | none |
| `POST /llm/verify` | `{"task", "source", "output", "prompt": "recompute"}` check any output against its source | admin |
| `POST /llm/pipeline` | one input through the chain with model agents and a model checker; full trace | admin |
| `POST /llm/check` | one tiny model call: do the key, model name and reply format work? (plain-language hint) | admin |
| `POST /llm/eval/plan` | calls and estimated cost of an evaluation; spends nothing | viewer |
| `POST /llm/eval/run` | start the evaluation in the background (`simulated`: viewer; real: admin + `confirm: true`) | viewer / admin |
| `GET /llm/eval/jobs`, `GET /llm/eval/jobs/{id}` | progress, then the full report and Markdown summary | viewer |
| `GET /llm/status` | model, calls and spend, or what is missing | viewer |
| `POST /pilot/invoice` | one invoice through the guarded workflow (`guard`, `agents`: `rules` free, `llm` paid) | viewer / admin |
| `POST /pilot/invoice-pdf` | the same for a PDF (`pdf_base64`) | viewer / admin |
| `GET /pilot/sample`, `GET /pilot/sample.pdf` | a generated sample invoice | viewer |
| `POST /pilot/experiment` | paired run on generated invoices (free) | viewer |
| `POST /pipeline`, `POST /experiment`, `POST /validate` | rule-based demo chain, paired experiment, text check (free) | viewer |
| `GET /runs`, `GET /runs/{id}` | saved runs with every step and verdict | viewer |
| `GET /review-queue` | stopped jobs and failed checks (`?state=resolved` for done ones) | viewer |
| `POST /review-queue/{id}/resolve` | `{"resolution": "approved" \| "rejected" \| "fixed", "note", "by"}` | reviewer |
| `GET /usage` | calls and cost by day, across restarts | viewer |
| `GET /audit` | audit log | admin |
| `GET /metrics` | Prometheus metrics | viewer |
| `GET /whoami` | the caller's role (401 for a wrong key) | any valid key |

Errors: 401 missing or wrong key, 403 key without enough rights (the message names the role needed), 413 body too
large, 422 invalid input, 429 rate limit or spend cap (`Retry-After` on rate limits), 502 the model provider failed,
503 no model configured (the message says what is missing).

## Security
- **Keys and roles** (sent as the `X-API-Key` header; the dashboard asks for it): `API_KEY` = admin (everything,
  including routes that cost money), `REVIEWER_KEY` = reviewer (read, free demos, decide review items),
  `VIEWER_KEY` = viewer (read and free demos). Keys are compared in constant time and never logged.
- **No keys = this computer only.** Without keys every caller is admin, so the server refuses (except `/health`):
  requests from other computers; requests that came through a proxy or tunnel (`Forwarded`, `X-Forwarded-For`,
  `X-Real-IP`); and requests whose Host header is not `localhost`, which stops DNS-rebinding web pages. `serve
  --proxy-headers` refuses to start without keys. A tunnel that hides all of this (an SSH port forward) makes its
  users look local: set keys before sharing the server in any way.
- **Limits:** request rate per key or address, a stricter rate on paid routes, request body size (enforced while the
  body arrives, before any other check), spend and call caps.
- **Audit log:** time, role, a short hash of the key, address, route, status and duration of every change and of
  refused requests, at most `AUDIT_REFUSED_PER_MIN` a minute per caller (`GET /audit`). `/health/ready` reports only
  "ok" or "unreachable", never database names or hosts.
- **HTTP headers:** `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and a Content-Security-Policy
  on the dashboard.
- **Untrusted text:** review-queue items and model replies are shown as data; the MCP server has no tool that makes
  decisions.
- **Data:** inputs and outputs are stored in your database so there is an audit trail; set `RETENTION_DAYS` or
  `CASCADE_DB=off`. With a cloud model, checked text is sent to that provider; use a local model for data that must
  not leave your machines.
- Not included: per-person accounts or single sign-on, TLS (put a reverse proxy such as Caddy or nginx in front for
  HTTPS; then set keys and use `serve --proxy-headers`). To report a vulnerability, see
[SECURITY.md](https://github.com/kanav10213/cascade-guard/blob/main/SECURITY.md).

## Results
**Simulation, best case.** Rule-based agents, injected faults, and a rule checker that recomputes the true answer, so
it catches every injected fault by construction; each retry is an independent new try. This measures what the check,
retry and stop wiring buys, not how good a model checker is (that is what [the evaluation](#evaluate-on-a-real-model)
measures). Reproduce: `cascade-guard compare`.

1,000 orders, seed 42, 10% fault chance per step, 2 retries:

| | No guard | Consistency-only guard | Full guard |
|---|---|---|---|
| Correct | 65.7% | 93.4% | 99.6% |
| Silent wrong answers | 288 | 62 | **0** |
| Crashes | 55 | | 0 |
| Stopped safely | | | 4 |

CRI (unsafe failures removed) 100%, CRS (caught errors repaired) 99.0%. Six seeds (6,000 orders): 99.3% to 99.8%.

**Invoice pilot** (500 generated invoices, seed 42, 10% realistic errors per step, rule checks): 64.6% to 99.4%
correct, 177 silent wrong invoices to 0, 3 held for review; seeds 1 to 5 with 1,000 invoices each: 98.9% to 99.6%,
zero silent wrong.

**Guided repair** (half the faults repeat on a blind retry; a told agent assumed to fix 90%): chain 88.8% to 99.3%
correct, stopped orders 112 to 7; invoices 86.6% to 99.8%, held 67 to 1; zero silent wrong in every run.

## How it works
**The demo chain.** `extract -> analyze -> reason -> report` on integers (`3, 7, 2, 9` to
`count=4; sum=21; mean=5.25; max=9; trend=mixed`). The right answer is always known, so every failure is scored exactly.

**Fault injection.** `CORRUPTED_OUTPUT`, `MISSING_DATA`, `INVALID_REASONING`, `TRUNCATED_OUTPUT`, seeded and
paired: the same order gets the same faults with and without the guard. `sticky` makes a share of faults repeat on
retry (systematic mistakes).

**The guard.** Every output is checked before the next agent runs. On failure the step is re-run (up to
`max_retries`, default 2); if it still fails, the job is **halted** and goes to the review queue. Checkers:
`invariants` (consistent with earlier validated steps), `full` (adds a source check), or the **model checker** with a
`naive` or `recompute` prompt (recompute every value from the source, then compare; it never sees the right answer).

**Guided repair.** A retry gets the checker's reason and the previous answer
(`FEEDBACK: line 4 (Cordless drill): qty 13, invoice says 12`) instead of a blind "try again". Default on the model
and pilot routes; `repair="blind"` turns it off.

**Engines.** The built-in orchestrator, or a LangGraph `StateGraph` (one agent node and one guard node per step,
retry and halt edges) with the same results on every test. `engine="langgraph"`;
`cascade_guard.orchestration.langgraph_chain.mermaid()` draws the graph.

**Invoice pilot.** A realistic four-step LangGraph workflow (read, price, approve with auto-approval up to $500,
summary), every node wrapped with `guarded_node`; rule or model agents, rule or model checks; text or PDF invoices
(PDFs need a text layer; scanned images need OCR first).

**Storage.** SQLite by default or PostgreSQL, same tables: `runs`, `steps` (every attempt), `verdicts` (what the checker
said and why), `review_queue`, `usage`, `audit`.

**Metrics.** *Unsafe* = a wrong output delivered silently, or a crash (a halt is a controlled stop).
CRI = (unsafe without guard - unsafe with guard) / unsafe without guard. CRS = caught step errors repaired by retry /
caught step errors.

**Dashboard** (`/`): live demo, the chain on your model (with a one-click connection check), the invoice pilot (text
or PDF), measured results, check any output, review queue and history, ready-made `curl` commands, and the model
evaluation with downloadable results.

## Evaluate on a real model
```bash
cascade-guard eval check                   # step 1: one tiny call checks the key, model name and reply format
cascade-guard eval all --dry-run           # the plan and the estimated cost; no calls, no key needed
cascade-guard eval all --fake              # the whole pipeline offline; numbers are SIMULATED
cascade-guard eval all --n 10 --seeds 1    # a small real run (a few cents)
cascade-guard eval all                     # default: 3 seeds x 50 items, hard cap $5
```
It prints the number of calls and the estimated cost and asks before the first call; `--max-cost` is a hard cap.
`--seeds 1 2 3` repeats everything once per seed and pools the counts (per-seed values are in the JSON). The same
evaluation runs from the dashboard (section 8: plan and cost, run in the background, download JSON or Markdown) or
the API (`/llm/eval/*`), where a real-model run needs an admin key, `confirm: true`, and an estimate within
`LLM_EVAL_MAX_COST` (default $5). With
Claude Haiku 5.5: about 3,000 calls plus up to 4,200 retries, estimated $0.34, worst case $3.26. Results go to
`results/*.json` and `results/*.md`, every rate with a Wilson 95% interval.

| Command | Measures |
|---|---|
| `agents` | each agent's accuracy on clean input, by list length and shape |
| `detect` | checker detection rate by error kind and size, false alarms on correct outputs, naive vs recompute prompt |
| `chain` | end-to-end success for six arms (rules; model without guard; with rule guard; naive checker; recompute checker; recompute checker with guided repair), and whether errors cluster |

Other models: `--provider openai --model <name> --price-in X --price-out Y`. Notes: every request is cached (re-runs
and resumes do not pay twice, and all arms share each agent's first answer, so the comparison is paired); retries are
new requests; a reply with no parseable JSON counts as an agent error; a failed API call stops the run instead of
being scored; seeds control inputs and faults, not the model.

## Development
```bash
pip install -r requirements-dev.txt     # tested versions, this folder in editable mode, dev tools
pytest -q                     # 150 tests, no API key needed; CASCADE_TEST_PG=postgresql://... adds the PostgreSQL ones
ruff check . && mypy cascade_guard
python -m build               # wheel and source package in dist/
```
CI (`.github/workflows/ci.yml`) runs lint and types, the tests on Python 3.12/3.13/3.14 (Linux) plus Windows and
macOS, PostgreSQL, the oldest allowed dependency versions, a clean install of the built wheel, and the Docker image.
Releases: bump `__version__` in `cascade_guard/__init__.py`, update `CHANGELOG.md` and `RELEASE_NOTES.md`, then
either publish a GitHub release with a new tag `vX.Y.Z` or push that tag; `release.yml` publishes to PyPI, GitHub
Releases and the GitHub container registry. See
`CONTRIBUTING.md`. Repository: https://github.com/kanav10213/cascade-guard

## Limits
- **Simulated headline numbers.** The rule checker equals the ground truth, so the results above are an upper bound.
  The real-model evaluation is built and cost-gated but has not been run yet.
- Fault types, the 10% fault rate and the 90% guided-repair success rate are assumptions, not measurements.
- The pilot invoices are generated; real invoices may use layouts the rule parser cannot read (model agents can).
- A model check adds one call and its delay per checked step; check only the high-risk steps if that matters.
- Spend caps, rate limits and the reply cache are per process; for several servers, put a shared limiter in front.
- No per-person accounts, single sign-on or built-in TLS.
- The demo chain is simple arithmetic; results for other tasks need their own evaluation.

## License
MIT; the full text is in the `LICENSE` file.
