# Cascade Guard

[![ci](https://github.com/kanav10213/cascade-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/kanav10213/cascade-guard/actions/workflows/ci.yml)
[![licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](https://github.com/kanav10213/cascade-guard/blob/main/LICENSE)

**Stop errors from cascading through chains of AI agents.**

When AI agents work in a chain, one agent's mistake becomes the next agent's input, and by the end the result is
confidently wrong. Cascade Guard checks every hand-off against the step's own input. If a check fails, it retries
and tells the agent *what* was wrong. If the step still fails, it stops the job safely and sends it to a person.
A wrong answer is never passed on silently.

- **An everyday assistant that checks itself:** open `/assistant` on the server (or run `cascade-guard ask`) and
  ask anything. Everyday sums are worked out exactly with the working shown, with no AI model and no key: bills and
  invoices (also PDFs, rupees and GST), discounts, splitting a bill and who owes whom, EMIs, interest, FDs and SIPs,
  dates, ages and time zones, units, travel time and fuel, SGPA and recipes. With an AI model connected (a free one
  works), it also writes emails and messages, explains, translates and answers questions, and Cascade Guard checks
  every answer before you see it: exact numbers against the tools, and an independent second pass.
- **Build your own agent, no code:** design a multi-step agent chain on the dashboard (instructions, output
  format, checks per step), save it, and run it with the guard on every step.
- **Plug in the agents you already have:** any agent with an HTTP endpoint, in any language, can be a step in a
  chain; Cascade Guard checks it, retries it with the reason and stops it safely. See [INTEGRATION.md](https://github.com/kanav10213/cascade-guard/blob/main/INTEGRATION.md).
- **Drop-in for your code:** wrap any Python step or LangGraph node with `guard_step` / `guarded_node`, checked
  locally or by your deployed server (`cascade_guard.remote.RemoteGuard`).
- **A service for everything else:** a REST API that checks any agent output against its source, plus a dashboard.
- **Inside AI assistants:** an MCP server for Claude Desktop, Claude Code and other MCP-capable tools, and a
  connector link for Claude on the web (claude.ai), so Claude itself checks bills and sums with Cascade Guard.
- **Measurable:** fault injection and paired experiments that report what the guard actually buys (CRI, CRS, Wilson
  95% intervals), and a cost-gated evaluation on real models.
- **Any model:** Anthropic Claude by default; any OpenAI-format API (OpenAI, Gemini, Groq, OpenRouter, a local model
  through Ollama), or a free offline simulator.

> **Status: v1.4.0.** Tested on **real data with real agents**: 326 held-out scanned shop receipts, run through a
> three-agent expense workflow with rule-based agents and with open AI models as the agents (see
> [Real-world results](#real-world-results)). Software tested on Python 3.12 to 3.14, SQLite and PostgreSQL, Docker.

## Real-world results
An expense-receipt workflow of three agents: **read** the receipt (vendor, date, total), apply the **expense policy**,
write the **ledger entry**. It was run on the public [ICDAR 2019 SROIE](https://rrc.cvc.uab.es/?ch=13) scanned
receipts, which come with the true date and total. Every receipt went through twice, without and with Cascade Guard.
The guard checks each hand-off against **the receipt itself** and never sees the answers. The checks were written on
receipts 0–299; the numbers below are on the other **326**, which were held out.

| Agents | Wrong amount posted: without → with | Wrong day posted | Policy broken | Any wrong entry posted | Fixed automatically | Stopped for a person (of them right) |
|---|---|---|---|---|---:|---|
| Rule-based agents | 97 (30%) → **26 (8%)** | 6 → 1 | 19 → 0 | 99 (30%) → **27 (8%)** | 58 | 29 (9%) (8) |
| Rule-based agents, read from the photos (OCR) | 107 (33%) → **29 (9%)** | 92 → 6 | 22 → 0 | 160 (49%) → **35 (11%)** | 40 | 96 (29%) (3) |
| Llama 3.2 3B (open model) | 63 (19%) → **18 (6%)** | 17 → 0 | 130 → 0 | 320 (98%) → **18 (6%)** | 210 | 93 (29%) (6) |
| Qwen 2.5 3B (open model) | 59 (18%) → **16 (5%)** | 54 → 1 | 229 → 0 | 286 (88%) → **17 (5%)** | 250 | 19 (6%) (14) |
| Qwen 2.5 7B (open model) | 88 (27%) → **9 (3%)** | 35 → 1 | 100 → 0 | 111 (34%) → **10 (3%)** | 59 | 86 (26%) (20) |

Each row is the same 326 held-out receipts.

- *Wrong amount* is the money that would reach the books; it is the headline.
- *Policy broken*: the policy step's decision was wrong for the true amount. The small models often called a fine
  receipt a "duplicate" or rejected it. The guard recomputes the decision, so none got through.
- *Any wrong entry* also counts dates in the wrong format (`18-04-2018` in an ISO date field) and wrong decisions. The
  small models made many of those, which is why their "without" numbers are so high. A format fix in code would
  remove part of them; the wrong amounts would remain.
- *Fixed automatically*: the guard told the agent what was wrong (for example "50.00 is the cash paid on the
  receipt, not the total") and the agent corrected itself.
- *Stopped for a person* is the cost. In brackets: how many of those the agents had read right, so a person
  confirms them for nothing. The rest were really wrong.
- Cost: retries took 3.2 to 4.4 agent calls per receipt instead of 3.
- AI agents: free open models run with Ollama on GitHub's machines (the `receipts-benchmark` workflow). Photos: the
  scans read with free offline OCR (`pip install "cascade-guard-ai[ocr]"`).
- One check was improved after the first test results; it was tuned on receipts 0–299 only, and all numbers here
  are from after the change. [benchmarks/README.md](benchmarks/README.md) says exactly what changed.

Method, raw results and how to reproduce: [benchmarks/README.md](benchmarks/README.md); full reports for each run are on
the [`benchmark-results`](https://github.com/kanav10213/cascade-guard/tree/benchmark-results/results) branch.

**Try it:** open `/receipts` (the expense desk) on a running server. Drop receipts (text, PDF, or photos with the OCR
add-on) and see what each agent read, every check, what was fixed and what was stopped. Stopped receipts go to the
review queue, and the results download as CSV. API: `POST /receipts/process`. MCP and the Claude connector:
`process_receipt`.

## Contents
[Real-world results](#real-world-results) · [Install](#install) · [Quick start](#quick-start) · [Ask in plain English](#ask-cascade-guard-in-plain-english) ·
[Docker](#docker) · [Use it in your code](#use-it-in-your-code) ·
[Use it from Claude (MCP)](#use-it-from-claude-and-other-ai-assistants-mcp) · [Models](#models-and-providers) ·
[Configuration](#configuration) · [API](#api) · [Security](#security) · [Results](#results) ·
[How it works](#how-it-works) · [Evaluate on a real model](#evaluate-on-a-real-model) · [Development](#development) ·
[Limits](#limits) · [License](#license)

## Install
Python 3.12 or newer.

```bash
pip install cascade-guard-ai                          # from PyPI, once published
pip install cascade_guard_ai-1.4.0-py3-none-any.whl   # from a release file
pip install .                                         # from this folder
pip install "cascade-guard-ai[postgres]"              # with PostgreSQL support
pip install "cascade-guard-ai[ocr]"                   # read receipt photos (offline OCR, ~400 MB)
```
The package is called `cascade-guard-ai` on PyPI (the name `cascade-guard` is too close to an existing project);
the command is `cascade-guard` and the import is `cascade_guard`. For the exact versions this release was tested with,
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

## Ask Cascade Guard in plain English
Open **`/assistant`** on a running server (for example `http://localhost:8000/assistant`), or use the command line:

```bash
cascade-guard ask "check this invoice: 3 x $4.00, 2 x $1.50, total $16.00"
# The total is wrong. It should be $15.00, but the bill says $16.00 ($1.00 too much).
cascade-guard ask --file invoice.pdf          # a PDF or text file
cascade-guard ask                             # a conversation; type exit to stop
```

| You type | It does |
|---|---|
| a bill, invoice or receipt, written any way (`3 x $4.00`, `2 pens at ₹15 each`, `Rice: 2 kg x ₹60 = ₹120`, GST / CGST + SGST / VAT, discounts, delivery, round-off), or a PDF | recomputes every line, the tax and the total exactly, compares them with the bill, and names the likely cause of a mistake (a line counted twice, the tax left out, a decimal slip) |
| `is 17 x 23 = 401?`, `what is 15% of 240` | checks or works out the sum exactly |
| `the average of 3, 7, 2, 9 is 5.5` | checks sums, averages, medians, maxima, minima, counts |
| `run support ticket triage on: …` | runs a saved chain or template with the guard on every step |
| `show me Cascade Guard catching a mistake` | a sample invoice through four agents with planted mistakes, without and with the guard |
| `list my agent chains`, `which agents are connected?`, `review queue`, `status` | what it says |
| `I bought a shirt for ₹1,500 at 20% off, they charged ₹1,250`, `₹1,180 including 18% GST, what is the base price?`, `my salary went from 40000 to 46000 what is the hike percentage` | discounts (also stacked), GST added or taken out, percentages and changes, checked against any figure you give |
| `split ₹2,400 among 3 people`, `Asha paid 600, Ravi paid 300 and I paid 0 for dinner`, `15% tip on $80 split 4 ways` | equal shares to the paisa, and the fewest payments to settle up |
| `EMI for 10 lakh at 10% for 3 years`, `FD of 1 lakh at 7% for 5 years`, `SIP of 5000 per month at 12% for 10 years` | EMIs, simple and compound interest, FDs, SIPs, with total paid and interest |
| `what day is 15 August 2027?`, `how many days till 25 December`, `3 pm IST in New York`, `born 12 May 2000, how old am I?` | dates, weekdays, ages, time between and time zones (daylight saving included) |
| `5 feet 8 inches in cm`, `100 F in C`, `fuel cost for 450 km at 15 km/l and ₹105 per litre`, `450 km at 60 km/h` | units (also tola, gaj, acre, cups), fuel cost, travel time, speed and distance |
| `SGPA with credits 4, 3, 3 and grades 9, 8, 10`, `I scored 432 out of 500`, `recipe for 4 needs 200 g flour, make it for 6` | SGPA / CGPA, marks, scaled recipes |
| anything else: `write a polite email to my landlord…`, `explain compound interest simply`, `translate … to Hindi` | needs an AI model (see below); the answer is checked before you see it |

Nothing is guessed: a line with an amount that cannot be read is reported, never dropped, and a request the exact
tools cannot read says so. These need no model and no key.

**With an AI model** (Claude, or a free one such as Groq: see [Models and providers](#models-and-providers)), the model
handles everything else and can call the same exact tools, and **Cascade Guard checks every answer** before you see it:

1. *Exact check:* after a calculation, every number in the answer must come from a tool result or your message, the
   verdict (correct / wrong) must match the tool's, and any sum written in the answer (`12 x 4 = 48`) is recomputed.
2. *Independent second pass:* a separate call that did not write the answer reads it against your request and the
   exact results, looking for wrong facts, made-up specifics and missed parts of the request.
3. A rejected answer goes back with the reason (up to twice). A number that is still wrong is replaced by the exact
   built-in answer; an answer the checker still doubts is shown marked "not confirmed", with the reason.

Requests the exact tools answer completely skip the model (free and instant). If the model is busy or over its free
limit, the built-in assistant answers and says so. By default only admin keys use the model; `ASSISTANT_MODEL_ROLE=viewer`
opens it to every key (sensible for a free model). API: `POST /assistant/ask` `{"message": "...", "pdf_base64": "..."}`.

## Deploy online (Render)
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/kanav10213/cascade-guard)

`render.yaml` sets up a free web service from the Dockerfile with a generated `API_KEY` (find it in the service's
Environment tab and enter it on the dashboard) and the free simulated model. Add `ANTHROPIC_API_KEY` and remove
`LLM_PROVIDER=fake` to use Claude. The free plan sleeps when idle and does not keep saved runs across restarts.

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

## Build your own agent chain
Dashboard section 9 (or the API and command line) lets anyone design a multi-step agent without writing code.
Each step has:

| Field | Meaning |
|---|---|
| name | unique within the chain |
| instruction | what this agent does, in plain words (it works on the previous step's output; the first step on your input) |
| returns | plain text, or JSON with the keys you name |
| check | `model` (format, then the model checker against the step's input), `format` (shape only) or `none` |
| extra acceptance criteria | optional rules for the model checker, e.g. "every owner must appear in the notes" |
| max characters | optional limit for text output |
| done by | the configured model, a **connected agent** by name, or any agent URL (with an optional token variable and timeout) |

A chain has up to 10 steps, a number of retries per step and the retry style (guided or blind). It runs on
LangGraph, built from the definition at run time: one guarded node per step. A rejected step is retried with the
checker's reason; if it stays wrong the chain stops and the run goes to the review queue. "Plant test mistakes"
puts a realistic error into a share of first answers (an empty or cut-off text, a missing or wrong key) so you can
watch the guard work even when the model makes none. Chains are saved in the database (`chains` table), and can be
exported and imported as JSON. Three starter templates are included: support ticket triage, meeting notes to
action items, invoice review.

```bash
cascade-guard chain templates                                   # the starter chains
cascade-guard chain run support-triage --input "Charged twice for INV-1007, please refund."   # simulated, free
cascade-guard chain run my_chain.json --input-file ticket.txt --real    # on the configured model
cascade-guard chain import my_chain.json                        # save it (then it shows on the dashboard)
cascade-guard chain list
```
Simulated runs are free and need any key; runs on the real model need an admin key; creating, editing and deleting
chains need an admin key.

## Integrate your own agent
Four ways, from no code to a single HTTP call; the full guide with request formats is
[INTEGRATION.md](https://github.com/kanav10213/cascade-guard/blob/main/INTEGRATION.md).

| Your agent is... | Use |
|---|---|
| anything with an HTTP endpoint (any language or platform) | a chain step **done by your agent (URL)**: Cascade Guard POSTs `{instruction, input, original, attempt, feedback}` and checks the `{"output": ...}` it gets back. `examples/my_agent_server.py` is a ready-made agent server; the built-in `/demo/agent` is a sample to try |
| Python code you own | `@guard_step(...)` around it (`examples/guard_inside_my_agent.py`) |
| a LangGraph graph | `guarded_node(...)` around each node (`examples/langgraph_agent.py`) |
| anything else | `POST /llm/verify` per answer, or `RemoteGuard` from Python |

**Connected agents** (dashboard section 10, `/agents`, `cascade-guard agent ...`): register an agent once with a
name, URL, token setting and timeout, press **Test** (it sends one probe and explains any problem: wrong path,
token, timeout, format), then pick it by name in any step. Agents listed in the `CASCADE_AGENTS` setting (a JSON
list) survive restarts, which matters on hosts that wipe the disk.

```bash
cascade-guard agent add support-bot https://support-bot.example.com/agent --token-var SUPPORT_BOT_TOKEN
cascade-guard agent test support-bot
cascade-guard agent list
```

Agent URLs are never link-local or cloud-metadata addresses; on a shared server (with keys) private and local
addresses need `AGENT_ALLOW_PRIVATE=1`. A token for your agent stays in a server environment variable; the
chain only names it.

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
**Claude on the web (claude.ai) and the Claude apps, through a connector link:** set `MCP_CONNECTOR_TOKEN` on your
server to a long random value (at least 24 characters). The server then answers MCP at
`https://<your server>/mcp/<that value>`; an admin finds the full link on the assistant page under **Use in Claude**.
In Claude: Customize > Connectors > Add custom connector, paste the link, choose No sign-in, Add. Then in a chat,
press + > Connectors, turn on Cascade Guard, and ask "Use Cascade Guard to check this invoice: 3 x $4.00, 2 x $1.50,
total $16.00". Anyone with the link can use the connector's tools, so keep it private (a new value revokes the old
link; the value never appears in the server's logs). `MCP_CONNECTOR_ROLE` decides the tools: `viewer` (default: the
exact checks, the demo, free chain runs, status), `reviewer` (also the review queue), `admin` (also `verify_output` and
chain runs on the model, which cost money).

**Claude Desktop and Claude Code on your computer:**
```bash
cascade-guard mcp --install-claude  # adds Cascade Guard to the Claude desktop app's settings (keeps everything else)
cascade-guard mcp --print-config    # prints the JSON for Claude Desktop and the `claude mcp add` command for Claude Code
```
Run it in the folder that holds your `.env`: the printed setup pins that folder as `CASCADE_HOME`. (Started without
it, the MCP server uses `~/.cascade-guard`, never the folder the AI app happens to start it in.)
Claude Desktop: paste the JSON into `claude_desktop_config.json` (Settings > Developer > Edit Config) and restart.
Claude Code: run the printed `claude mcp add ...` command.

| Tool | Does |
|---|---|
| `check_bill` | recompute any bill or invoice exactly and say what is wrong (no model needed) |
| `check_math` | check a sum or a claim about a list of numbers exactly (no model needed) |
| `process_receipt` | an expense receipt through read → policy → ledger, every hand-off checked (no model needed) |
| `check_everyday` | everyday sums in words: discounts, GST, splits, EMIs, interest, dates, time zones, units, travel, SGPA, recipes (no model needed) |
| `show_cascade_demo` | a sample invoice through four agents with planted mistakes, without and with the guard |
| `verify_output` | check any output against its source with the model checker |
| `check_invoice` | run an invoice through the guarded invoice workflow |
| `guard_status` | model, spend so far, open review items |
| `list_review_queue` | stopped jobs and failed checks (read-only; their text is marked as untrusted data) |
| `list_agent_chains` | the agent chains built on the dashboard, and the starter templates |
| `run_agent_chain` | run one of them on an input, with the guard on every step |

Deciding review items is deliberately **not** a tool: the queue holds outside text that could try to talk an assistant
into approving things, so people decide on the dashboard or through the API with a reviewer key.

## Models and providers
| Provider | In `.env` |
|---|---|
| Anthropic (default) | `ANTHROPIC_API_KEY=...` (model `claude-haiku-5-5` unless `LLM_MODEL` is set) |
| OpenAI | `LLM_PROVIDER=openai`, `OPENAI_API_KEY=...`, `LLM_MODEL=<model>` |
| Gemini / Groq / OpenRouter / Together | as OpenAI, plus `OPENAI_BASE_URL=` the provider's OpenAI-compatible URL |
| Groq (free tier, no card) | `LLM_PROVIDER=auto`, `OPENAI_API_KEY=<gsk_… key from console.groq.com>`, `OPENAI_BASE_URL=https://api.groq.com/openai/v1`, `LLM_REASONING_EFFORT=low`, `LLM_PRICE_IN=0`, `LLM_PRICE_OUT=0` (model `openai/gpt-oss-120b` unless `LLM_MODEL` is set) |
| Ollama (free, local) | `LLM_PROVIDER=openai`, `OPENAI_BASE_URL=http://localhost:11434/v1`, `LLM_MODEL=llama3.2` |
| No key, just try it | `LLM_PROVIDER=fake` (simulated answers, clearly labelled, no cost) |
| Whichever key is set | `LLM_PROVIDER=auto`: Anthropic if `ANTHROPIC_API_KEY` is set, else OpenAI-compatible if `OPENAI_API_KEY` is set, else the simulator |

Free tiers have limits (Groq's, in October 2026: 30 requests a minute and 1,000 a day for `openai/gpt-oss-120b`). An
assistant answer takes two or three model calls; when a limit is hit the built-in assistant answers instead.

The Anthropic and OpenAI wire formats are covered by the tests (against a local stand-in server, with the real client
libraries); other OpenAI-compatible providers use the same code but have not been tried live.

## Configuration
Settings come from the environment or from `.env` in the working folder (the real environment wins).
`cascade-guard init` writes a commented template.

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic`, `openai` (any OpenAI-compatible API), `auto` (whichever key is set) or `fake` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | | the provider key |
| `LLM_MODEL` | `claude-haiku-5-5` | model name |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | for other OpenAI-compatible providers |
| `LLM_REASONING_EFFORT` | | `low`, `medium` or `high` for reasoning models on OpenAI-compatible APIs (dropped if the model refuses it) |
| `ASSISTANT_MODEL_ROLE` | `admin` | the lowest key role that may use the model on `/assistant`: `viewer`, `reviewer` or `admin` |
| `ASSISTANT_MAX_TOKENS` | `1500` | longest answer the assistant's model may write |
| `CASCADE_TZ` | `Asia/Kolkata` | the time zone that decides "today" for date questions |
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
| `AGENT_ALLOW_PRIVATE` | off | `1` lets chain steps call agents on private or local addresses on a shared server |
| `CASCADE_AGENTS` | | connected agents as a JSON list of `{name, url, auth_env, timeout, description}`; survive restarts |
| `MCP_CONNECTOR_TOKEN` | off | turns on the Claude connector at `/mcp/<this value>` (at least 24 characters; keep it secret) |
| `MCP_CONNECTOR_ROLE` | `viewer` | the connector's tools: `viewer`, `reviewer` or `admin` (admin includes paid tools) |
| `MCP_RATE_LIMIT_PER_MIN` | `120` | connector requests per minute per caller address |
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
| `GET /assistant` | the assistant page (its requests need a key when keys are set) | none |
| `POST /assistant/ask` | `{"message", "history", "pdf_base64"}` answer one request; built-in, or the model with an admin key | viewer / admin |
| `GET /assistant/info` | what this key can use (built-in only, or the model too) | viewer |
| `GET /connector` | the Claude connector: on or off, its role and its link | admin |
| `POST /mcp/<secret>` | MCP over HTTP for the Claude connector (only when `MCP_CONNECTOR_TOKEN` is set) | the secret link |
| `GET /health/ready` | database reachable, model configured (for load balancers) | none |
| `POST /llm/verify` | `{"task", "source", "output", "prompt": "recompute"}` check any output against its source | admin |
| `POST /llm/pipeline` | one input through the chain with model agents and a model checker; full trace | admin |
| `POST /llm/check` | one tiny model call: do the key, model name and reply format work? (plain-language hint) | admin |
| `POST /llm/eval/plan` | calls and estimated cost of an evaluation; spends nothing | viewer |
| `POST /llm/eval/run` | start the evaluation in the background (`simulated`: viewer; real: admin + `confirm: true`) | viewer / admin |
| `GET /llm/eval/jobs`, `GET /llm/eval/jobs/{id}` | progress, then the full report and Markdown summary | viewer |
| `GET /chains`, `GET /chains/{id}`, `GET /chains/templates` | saved agent chains and the starter templates | viewer |
| `POST /chains`, `PUT /chains/{id}`, `DELETE /chains/{id}` | create, change or delete a chain | admin |
| `POST /chains/{id}/run`, `POST /chains/run` | run a saved chain, or a definition without saving it (`simulated`: viewer; real: admin) | viewer / admin |
| `GET /chains/{id}/graph` | the chain as a Mermaid diagram | viewer |
| `POST /demo/agent` | a sample outside agent that speaks the agent format (`?mistakes=0.5` to make it err) | any |
| `GET /agents` | connected agents (never their tokens) | viewer |
| `POST /agents`, `PUT /agents/{name}`, `DELETE /agents/{name}` | connect, change or remove an agent | admin |
| `POST /agents/{name}/test`, `POST /agents/test` | send one probe and explain the result | admin |
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
said and why), `review_queue`, `usage`, `audit`, `chains` (the agent chains people build), `agents` (connected
agents: URL and token setting name, never the token).

**Metrics.** *Unsafe* = a wrong output delivered silently, or a crash (a halt is a controlled stop).
CRI = (unsafe without guard - unsafe with guard) / unsafe without guard. CRS = caught step errors repaired by retry /
caught step errors.

**Dashboard** (`/`): live demo, the chain on your model (with a one-click connection check), the invoice pilot (text
or PDF), measured results, check any output, review queue and history, ready-made `curl` commands, and the model
evaluation with downloadable results, and the agent chain builder.

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
pytest -q                     # 205 tests, no API key needed; CASCADE_TEST_PG=postgresql://... adds the PostgreSQL ones
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
- **Two kinds of results.** The real-world results come from real receipts and real (small, open) models. The
  simulation results further down (the arithmetic chain, the generated invoices) use a rule checker that equals the
  ground truth, so they are an upper bound. The paid-model evaluation is built and cost-gated but has not been run.
- **Receipts:** one public dataset (Malaysian shop receipts, 2017–2018) and one workflow. Its checks were written for
  that kind of receipt: other countries, languages and document types need their own checks and their own measured
  run. The guard stops some receipts the agents read right (see the table): up to 20 of 326 with the AI agents.
  It rejects the true answer itself on 27 of the 326 test receipts, mostly when OCR separates an amount from its
  label. The open models tested are small (3B–7B) and run on CPU; larger models make fewer
  mistakes for the guard to catch.
- Fault types, the 10% fault rate and the 90% guided-repair success rate are assumptions, not measurements.
- The pilot invoices are generated; real invoices may use layouts the rule parser cannot read (model agents can).
- A model check adds one call and its delay per checked step; check only the high-risk steps if that matters.
- Spend caps, rate limits and the reply cache are per process; for several servers, put a shared limiter in front.
- No per-person accounts, single sign-on or built-in TLS.
- The demo chain is simple arithmetic; results for other tasks need their own evaluation.

## License
MIT; the full text is in the `LICENSE` file.
