# Cascade Guard 1.1.0

New: **build your own agent chain, without code.**

On the dashboard (section 9), design a multi-step agent: give each step an instruction for the model, choose
whether it returns text or JSON with named keys, and choose how it is checked (the model checker with your own
acceptance criteria, format only, or none). Save it, export it as JSON, and run it on any input. Every step is
checked before the next one uses it, retried with the checker's reason when it is wrong, and the chain stops
safely and goes to the review queue if a step stays wrong. Chains run on LangGraph, built from your definition.

- Free with the simulated model; on the configured model with an admin key
- "Plant test mistakes" shows the guard catching and repairing errors
- Three starter templates: support ticket triage, meeting notes to action items, invoice review
- Also from the command line (`cascade-guard chain run ...`), the API (`/chains`) and AI assistants (MCP
  `run_agent_chain`)

Also new: **integrate the agents you already have.** A chain step can be done by your own agent at a URL, in any
language or framework; Cascade Guard sends it the instruction and input, checks its answer, retries it with the
reason, and stops the chain safely if it stays wrong. Ready-to-run examples (an agent server, guarding an agent
inside Python, guarding a LangGraph agent) and a step-by-step guide: INTEGRATION.md.

Get it: `pip install --upgrade cascade-guard-ai`. Full list: CHANGELOG.md
