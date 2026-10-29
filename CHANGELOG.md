# Changelog
All notable changes to this project. Versions follow [Semantic Versioning](https://semver.org/).

## [1.2.0] - 2026-10-29

### Added
- Ask Cascade Guard in plain English: an assistant page at `/assistant`, the `POST /assistant/ask` API and the
  `cascade-guard ask` command (one request, a file, or a conversation). It checks bills and invoices, sums and
  claims about lists of numbers, runs chains, shows a demo of planted mistakes being caught, and answers questions
  about chains, agents, the review queue and status. The built-in assistant is free and needs no model or key;
  PDF invoices can be attached.
- An exact bill checker (`cascade_guard.bills`) for bills written the way people write them: `3 x $4.00`,
  `2 pens at ₹15 each`, `Rice: 2 kg x ₹60 = ₹120`, one item per line or comma-separated, with subtotals, tax,
  GST / CGST + SGST / VAT (also included in prices), discounts, delivery and service charges, round-off, thousands
  separators (also Indian grouping) and decimal commas. Exact decimal arithmetic; mistakes are explained (a line
  counted twice, the tax left out, a discount not taken off, a decimal slip); unreadable lines are reported, never
  dropped; a total that matches another common convention (tax on charges, discount after tax) is not called wrong.
- Exact checks of arithmetic and of list statistics (`cascade_guard.claims`), with rational arithmetic.
- With a real model and an admin key, the model reads the request and calls the same tools, and Cascade Guard
  checks the model's own answer (every number must come from a tool result, the verdict must match); a rejected
  answer is sent back with the reason, and the built-in answer replaces one that stays wrong.
- Claude on the web: a connector link (`MCP_CONNECTOR_TOKEN`) serves MCP over HTTP at `/mcp/<secret>` for a
  claude.ai custom connector, with the tool set chosen by `MCP_CONNECTOR_ROLE`; the secret never appears in logs.
  The assistant page shows admins the link and the steps ("Use in Claude").
- MCP tools `check_bill`, `check_math` and `show_cascade_demo`; `cascade-guard mcp --install-claude` adds Cascade
  Guard to the Claude desktop app's settings without touching anything else in them.

### Fixed
- The MCP server no longer prints a long error when stopped with Ctrl+C, and says it is waiting when started by hand.

## [1.1.0] - 2026-10-29

### Added
- Build your own agent chain, without code: dashboard section 9, `/chains` API, `cascade-guard chain` command and
  the MCP tools `list_agent_chains` and `run_agent_chain`. Each step has an instruction, an output format (text or
  JSON keys), a check (model, format or none), optional acceptance criteria and a length limit. Chains run on a
  LangGraph graph built from the definition, with check, guided retry and safe stop on every step; they are saved in
  a new `chains` table (SQLite and PostgreSQL), exported and imported as JSON, and can plant test mistakes to show
  the guard at work. Three starter templates.
- Integrate your own agents: a chain step can be done by your agent at a URL (any language), with the same
  check, guided retry and safe stop; an optional bearer token read from a server environment variable; URL
  safety rules (no link-local or metadata addresses; private addresses only on your own computer or with
  `AGENT_ALLOW_PRIVATE=1`). A sample agent at `/demo/agent`, `cascade_guard.remote.RemoteGuard` to use a
  deployed server from Python, runnable examples in `examples/`, and the guide `INTEGRATION.md`.
- Connected agents: register an outside agent once (dashboard section 10, `/agents`, `cascade-guard agent`, or
  the `CASCADE_AGENTS` setting, which survives restarts), test it with one probe that explains problems in
  plain words, and choose it by name in any chain step; a new `agents` table. Agent calls wait up to 90 s by
  default, so a sleeping free host can wake up.
- `HEAD` on `/` and `/health` for uptime monitors.

## [1.0.0] - 2026-10-29
First public release.

### Added
- Installable package `cascade-guard-ai` (`pip install cascade-guard-ai`; import `cascade_guard`), with the
  `cascade-guard` command: `init`, `serve`, `mcp`,
  `compare`, `baseline`, `protected`, `eval`, `--version`.
- Working folder (`--home` / `CASCADE_HOME` / current folder) for `.env`, the database and the reply cache.
- Guard for any Python step (`guard_step`) or LangGraph node (`guarded_node`), with rule or model checks.
- Model checker with `naive` and `recompute` prompts; Anthropic, OpenAI-compatible (OpenAI, Gemini, Groq,
  OpenRouter, Ollama) and an offline simulator.
- Guided repair: retries receive the checker's reason and the previous answer.
- Two engines with the same results: the built-in orchestrator and LangGraph.
- Invoice pilot workflow on LangGraph, from text or PDF.
- REST API and dashboard; MCP server for Claude Desktop, Claude Code and other MCP clients (read-only review queue).
- Storage in SQLite or PostgreSQL: runs, steps, verdicts, review queue, usage, audit.
- Access keys with admin, reviewer and viewer roles; this-computer-only mode when no keys are set.
- Request rate limits, a streaming request-size limit, spend and call caps, audit log (refused requests throttled),
  Prometheus `/metrics`, `/health/ready`, security headers.
- Without access keys: only loopback callers, no proxied or tunnelled requests, and only `localhost` Host headers
  (blocks DNS rebinding); `serve --proxy-headers` requires keys.
- Fault injection, paired experiments, CRI and CRS, Wilson intervals, and a cost-gated real-model evaluation, also
  runnable from the dashboard and API in the background with JSON and Markdown downloads.
- One-call connection check (`cascade-guard eval check`, `POST /llm/check`) with plain-language hints.
- Pinned, tested versions in `constraints.txt`; Docker image (non-root, health check, `/data` volume, tested pins)
  and Docker Compose; CI for Linux, Windows and macOS,
  Python 3.12 to 3.14, PostgreSQL, lowest dependency versions, wheel install and Docker; release workflow for
  PyPI, GitHub Releases and the GitHub container registry.

### Changed (from the earlier versions in this repository)
- The earlier LLM layer (`app/llm/`: agents, client, experiments, jobs, prompts, stats) is replaced by
  `cascade_guard.llm`, `cascade_guard.agents.llm_agents`, `cascade_guard.guard.llm_guard` and
  `cascade_guard.experiments`. Its features carry over: the connection check, repeats (now `--seeds`), background
  evaluation jobs with downloads, and pinned versions (now `constraints.txt`).
- The package is `cascade_guard` (was `app`); experiments moved to `cascade_guard.experiments`.
- Results are produced by real runs; recovery means re-running the agent, which can fail again.
- CRS counts only real errors the guard caught (false alarms are counted separately).
