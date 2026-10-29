# Cascade Guard 1.0.0

Cascade Guard stops errors from cascading through chains of AI agents. It checks every hand-off against the step's
own input, retries with the reason when a check fails, and stops the job safely instead of passing a wrong answer on.

## Get it
- `pip install cascade-guard-ai`, then `cascade-guard init` and `cascade-guard serve`
- Docker: `docker compose up -d --build`
- Windows without a command line: download the source, double-click `start.bat`

## Highlights
- `guard_step` and `guarded_node`: check, retry with the reason, and halt around any Python step or LangGraph node
- `POST /llm/verify`: check any agent's output against its source, from any language
- MCP server: use it as a tool inside Claude Desktop, Claude Code and other AI assistants
- Works with Anthropic Claude, any OpenAI-compatible API, a local model through Ollama, or an offline simulator
- Review queue, audit log, access roles, rate limits, spend caps, Prometheus metrics, SQLite or PostgreSQL

## Results so far (simulation, best case)
1,000 orders with 10% injected faults per step: silent wrong answers 288 without the guard, 0 with it (99.6% correct,
4 stopped safely). Generated invoices: 177 silent wrong to 0. These use a rule checker that knows the right answer;
the real-model evaluation (`cascade-guard eval all`) is included and has not been run for this release.

Full list: [CHANGELOG.md](CHANGELOG.md).
