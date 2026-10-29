# Integrating Cascade Guard with your agent

Cascade Guard checks each answer your agent gives before anything else uses it. If an answer is wrong, the
agent is asked again and told why. If it stays wrong, the job stops and goes to a person in the review queue.

There are four ways to connect an agent. Pick the one that matches how your agent is built.

| Your agent is... | Use | You change |
|---|---|---|
| anything with an HTTP endpoint (any language: Python, Node.js, Java, n8n, a LangChain or CrewAI service) | **A. Plug it into a chain** | nothing in Cascade Guard; your agent answers one POST format |
| Python code you own | **B. Guard it inside your code** | add one decorator line |
| a LangGraph graph | **C. Guard its nodes** | wrap each node |
| anything else, or you only want a yes/no check | **D. Call the check API** | send one HTTP request per answer |

All four work with the free simulated checker (no key) and with a real model.

---

## A. Plug your agent into a chain (no code in Cascade Guard)

**The quick way: connect it once.** Dashboard section 10, **Connected agents**: give the agent a name, its URL and
(if it needs one) the name of the server setting that holds its token, press **Test**, then **Connect**. From then
on every chain step can pick it under **Done by**, and **Use in a new chain** builds a two-step chain around it.
The test sends one probe request and tells you in plain words what is wrong if it fails (wrong path, missing or
refused token, a sleeping free host, a reply in the wrong format).

To keep connected agents across restarts (Render's free plan wipes its disk), list them in the server setting
`CASCADE_AGENTS` instead, for example:
`[{"name": "support-bot", "url": "https://support-bot.example.com/agent", "auth_env": "SUPPORT_BOT_TOKEN"}]`.
From the command line: `cascade-guard agent add NAME URL --token-var VAR`, `cascade-guard agent test NAME`.

You can also type a URL directly in a step (**Done by: another agent (URL)**).
Cascade Guard then sends your agent this request for that step:

```json
POST https://your-agent.example.com/agent
{
  "step": "summarise",
  "instruction": "Summarise the customer's problem in one or two sentences.",
  "output": "text",
  "keys": [],
  "input": "the previous step's output (or the chain's input for the first step)",
  "original": "the chain's input",
  "attempt": 0,
  "feedback": null
}
```

Your agent answers:

```json
{"output": "The customer was charged twice for INV-1007 and wants a refund before Friday."}
```

For a step that returns JSON (`"output": "json"`, `"keys": ["category", "urgency"]`), answer
`{"output": {"category": "billing", "urgency": "high"}}` (or just the object).

On a retry, `attempt` is 1, 2... and `feedback` says why the last answer was rejected:
`{"reason": "the summary leaves out the invoice number", "previous_output": "..."}`. Use it to fix the answer.

**Try it without writing anything:** click **Use the sample agent** in the step. It fills in this server's
built-in sample agent (`/demo/agent?mistakes=0.5`), which gives an empty answer half of the time on purpose,
so you can watch Cascade Guard catch it and get a correct answer on the retry.

**Make your own agent server in two minutes:** copy `examples/my_agent_server.py`, put your logic in
`my_agent()`, and run `python examples/my_agent_server.py` (it listens on `http://127.0.0.1:9000/agent`).
It already calls any OpenAI-compatible model (Groq, OpenAI, Ollama) when `OPENAI_API_KEY` or
`OPENAI_BASE_URL` is set, and answers with simple rules otherwise.

**Protecting your agent with a token:** set `MY_AGENT_TOKEN` on your agent (the example checks it), put the
same token in an environment variable on the Cascade Guard server (for example `MY_AGENT_TOKEN` in Render's
Environment tab), and type that variable's **name** in the step's **Token variable** field. Cascade Guard sends
`Authorization: Bearer <token>`. The token itself is never stored in the chain.

**Safety rules for agent URLs:** the server never calls link-local or cloud-metadata addresses. On a server
with access keys (a shared server) it also refuses private and local addresses (`localhost`, `10.x`,
`192.168.x`) unless `AGENT_ALLOW_PRIVATE=1` is set; without keys (your own computer) local agents are allowed.
Running an unsaved chain that calls outside agents needs an admin key.

The same chain also runs from the command line (`cascade-guard chain run my_chain.json --input "..."`), from
the API (`POST /chains/{id}/run`) and from Claude through MCP (`run_agent_chain`).

---

## B. Guard your agent inside your own Python code

```python
from cascade_guard import GuardHalted, guard_step, llm_check

@guard_step(llm_check("Summarise the customer's problem in one sentence; keep invoice numbers."), max_retries=2)
def summarise_agent(ticket, feedback=None):        # your agent, unchanged except for `feedback`
    return my_llm(ticket, hint=feedback)           # feedback: why the last answer was rejected

try:
    summary = summarise_agent(ticket_text)
except GuardHalted as stop:
    send_to_review(stop.report)                    # nothing wrong goes further
```

To use the checker of your deployed server instead of a local one:

```python
from cascade_guard.remote import RemoteGuard
cg = RemoteGuard("https://cascade-guard.onrender.com", api_key="...")   # or CASCADE_URL / CASCADE_API_KEY
@guard_step(cg.check("Summarise the customer's problem in one sentence."))
def summarise_agent(ticket, feedback=None): ...

cg.run_chain("Support ticket triage", ticket_text)   # run a chain saved on the server
```

Full runnable example: `examples/guard_inside_my_agent.py`.

---

## C. Guard the nodes of a LangGraph agent

```python
from cascade_guard import guarded_node, llm_check
g.add_node("summarise", guarded_node(summarise, llm_check("Summarise the ticket."),
                                     source_key="ticket", output_key="summary", trace_key="trace"))
g.add_conditional_edges("summarise", lambda s: END if s.get("halted") else "next_step", ["next_step", END])
```

Full runnable example: `examples/langgraph_agent.py`.

---

## D. Call the check API from any language

```bash
curl -X POST https://cascade-guard.onrender.com/llm/verify \
  -H "Content-Type: application/json" -H "X-API-Key: YOUR_ADMIN_KEY" \
  -d '{"task": "Add up the line items", "source": "3 x $4.00, 2 x $1.50", "output": "total $16.00"}'
# {"passed": false, "verdict": "reject", "reason": "...", ...}
```

```javascript
const r = await fetch("https://cascade-guard.onrender.com/llm/verify", {
  method: "POST",
  headers: {"Content-Type": "application/json", "X-API-Key": process.env.CASCADE_API_KEY},
  body: JSON.stringify({task, source: input, output: agentAnswer}),
});
const {passed, reason} = await r.json();
if (!passed) { /* ask your agent again with `reason`, or stop and send it to a person */ }
```

Your code decides what to do with a rejection; in A, B and C Cascade Guard does the retry and the stop for you.

---

## Using Cascade Guard yourself, in plain English or inside Claude

You don't need an agent of your own to use the checks. Open `/assistant` on the server and type a request ("check
this invoice: 3 x $4.00, 2 x $1.50, total $16.00"), or run `cascade-guard ask "..."`. To let Claude use the same
checks, add the connector link shown under **Use in Claude** on that page as a custom connector in Claude (see the
README section "Use it from Claude").
