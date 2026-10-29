# Changelog
All notable changes to this project. Versions follow [Semantic Versioning](https://semver.org/).

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
