# Cascade Guard
AI Output Validation and Reliability Framework: a four-agent chain with fault injection,
a stage-by-stage guard with bounded retry, and a paired experiment that measures the difference.

## The chain
`extract -> analyze -> reason -> report`. Each agent reads the raw input and earlier outputs.
Input: integers such as `3, 7, 2, 9`. Output: `count=4; sum=21; mean=5.25; max=9; trend=mixed`.
The correct answer is always known, so every failure can be scored exactly.

## Fault types (injected into an agent's output)
`CORRUPTED_OUTPUT` (a value is changed), `MISSING_DATA` (a field becomes null),
`INVALID_REASONING` (swapped or reordered values, wrong trend), `TRUNCATED_OUTPUT` (list, text or keys cut short).

## The guard
Every agent output is validated before the next agent runs. On failure the stage is re-run
(up to `max_retries`, default 2). If it still fails, the order is **halted** instead of passing bad data on.
- `invariants` mode: checks each stage against earlier validated stages only.
- `full` mode: adds a source check of the first stage against the raw input.

## Run it
Package versions are pinned in `requirements.txt` and `requirements-dev.txt`, and the tests pass with exactly those versions.

**Windows:** double-click `start.bat` (needs Python installed, "Add to PATH" ticked). **Mac/Linux:** `./start.sh`.
Your browser opens the dashboard at http://localhost:8000.

Or by hand:
```bash
py -m pip install -r requirements-dev.txt     # Windows (use python3 on Mac/Linux)
py -m pytest -q                                # 39 tests
py -m uvicorn app.api:app                      # dashboard + API on http://localhost:8000
py -m experiments.compare                      # optional: writes report.json from the command line
```

## The dashboard (home page `/`)
1. **Live demo:** one order, with and without the guard, with the stage-by-stage trace.
2. **Measured results:** hundreds of orders, three arms (no guard, consistency-only guard, full guard).
3. **Check any AI output:** paste text, get pass or fail.
4. **Use it from your own system:** ready-made `curl` commands. Interactive API docs are at `/docs`.

## LLM version (Project Brief, priority 1)
Real language models do the work instead of rules: an LLM **analyst** (count, sum, mean, max), an LLM
**trend labeler**, and an LLM **validator** that reviews their output. A deterministic step renders the report.

What it measures, each with a 95% Wilson confidence interval:
1. **Per-agent accuracy p1, p2** on clean input, and whether p1 x p2 predicts the chain's accuracy.
2. **Validator false alarms** on correct figures.
3. **Validator detection rate** by error kind (*internal*: figures contradict each other; *consistent*: wrong but
   self-consistent, catchable only by recomputing from the numbers) and by size (small 2%, medium 10%, large 50%).
4. **End to end:** no guard, naive validator + retry, recompute validator + retry, and a perfect validator as the ceiling.
5. **Prompt comparison:** naive "do these look right?" vs "recompute independently first".

Try it free first. The **simulated model** (made-up error rates) checks that everything works and is always labelled
SIMULATED. Its numbers are not measurements of a real LLM.
```bash
py -m experiments.llm_eval --simulated
```
**Step 1 for any real run: the one-call check.** It sends a single tiny request (a fraction of a cent) and tells you
in plain words whether your key, model name and the model's reply format all work, with a hint if not:
```powershell
$env:ANTHROPIC_API_KEY = "your-key"
py -m experiments.llm_eval --check
```
Real run (PowerShell). It prints the plan and number of calls, then asks you to type `yes` before spending anything:
```powershell
$env:ANTHROPIC_API_KEY = "your-key"
py -m experiments.llm_eval --price-in 1 --price-out 5 --hard     # use YOUR model's prices per million tokens
```
Options: `--repeats 3` (run everything 3 times with different seeds, pool the counts, and show the lowest and highest single run),
`--model` (or `CG_MODEL`, default `claude-haiku-5-5`), `--hard` (longer lists and bigger numbers, so real models
make more natural errors), `--inject 0.1` (add injected faults on top), `--retries`, `--seed`, `--parts agents validator e2e`.
Start small: the default plan is roughly 450 to 1100 calls. Results are written to `llm_report.json`.

In the dashboard this is section 3. API: `GET /llm/status`, `POST /llm/plan` (spends nothing), `POST /llm/run`
(background job), `GET /llm/jobs/{id}`. **Safeguards on real runs:** the server needs `ANTHROPIC_API_KEY`, `API_KEY`
auth must be on (so strangers cannot spend your money), the request must say `confirm: true`, the plan must fit
`LLM_MAX_CALLS` (default 300), and only one real run goes at a time.

LLM limits: model replies vary from run to run, so the same seed does not give identical real results. Repeat runs and
compare. Small trial counts give wide intervals; trust the interval, not the point estimate. A reply that cannot be parsed
counts as a failed answer, and an unparseable validator reply is treated as a rejection (fail closed). It is reported
separately as `unparseable`.

## API
| Route | Purpose |
|---|---|
| `GET /health` | Liveness check (always open) |
| `POST /validate` | `{"text": "..."}` text-level check for failure markers or empty output |
| `POST /pipeline` | Run one input through the chain, with or without the guard, and return the full trace |
| `POST /experiment` | Paired baseline vs protected run (`trials` capped by `MAX_TRIALS`) |

Environment variables: `PORT` (default 8000), `MAX_TRIALS` (default 2000),
`API_KEY` (optional; when set, all routes except `/health` need an `X-API-Key` header),
`ANTHROPIC_API_KEY` and `LLM_MAX_CALLS` (real LLM runs, see above), `CG_MODEL`.

## Deploy
```bash
docker build -t cascade-guard .
docker run -p 8000:8000 -e API_KEY=change-me cascade-guard
# or: docker compose up --build
```
The image runs as a non-root user, has a health check, and honours the `PORT` variable, so it works on
Render, Railway, Fly.io and similar platforms. Without Docker, the `Procfile` runs it directly.
Set `API_KEY` for any public deployment. There is no rate limiting or persistence.

## Metrics
- `accuracy` = correct outputs / trials.
- *Unsafe failure* = a wrong output delivered silently, or an uncontrolled crash. A halted order is a controlled stop and is not counted as unsafe.
- `cri` (Cascade Reduction Index) = (baseline unsafe - protected unsafe) / baseline unsafe.
- `crs` (Cascade Recovery Score) = stage failures repaired by retry / stage failures detected.

## Honest limits
- The agents are deterministic rules, not LLMs. The guard recomputes expected values, so it is an upper bound
  on how well a real validator would do. The next step is replacing agents and validator with LLM calls.
- Fault types and the 10% fault rate are assumptions, not measured failure rates.
- Zero false alarms is expected here because the guard and the agents share the same logic.
- Results use one seed and no confidence intervals. Compare several seeds before quoting a number.

## What changed from the first version
- `report.json` held hardcoded numbers and the experiment scripts only printed text. Results are now generated by real runs.
- The old guard "recovered" an error by overwriting the field with `RECOVERED_OUTPUT`, so 100% recovery was
  guaranteed by construction. Recovery now means re-running the agent, which can fail again.
- Added the missing `__init__.py` files, so the `app` package imports correctly.
