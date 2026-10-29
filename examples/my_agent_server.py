"""Turn YOUR agent into a step Cascade Guard can call (a chain step done by "your agent").

Run it:      python examples/my_agent_server.py              (listens on http://127.0.0.1:9000/agent)
Use it:      in dashboard section 9, set a step's "Done by" to "your agent (URL)" and the URL to
             http://127.0.0.1:9000/agent  (or your server's public address)

The only thing to change is `my_agent()`. It gets the step's instruction, its input, the chain's original
input, and on a retry `feedback` = {"reason": why the guard rejected the last answer, "previous_output": ...}.
It must return text (text step) or a dict with the step's keys (JSON step).

Out of the box it calls any OpenAI-compatible model if OPENAI_API_KEY (or a local Ollama via OPENAI_BASE_URL)
is set, and otherwise answers with simple rules, so it runs with no key at all.
Optional: set MY_AGENT_TOKEN to require "Authorization: Bearer <token>" (put the same token in an environment
variable on the Cascade Guard server and name that variable in the step's "token variable" field).
"""
import json
import os
import re

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="My agent")


class StepRequest(BaseModel):
    step: str
    instruction: str
    output: str = "text"
    keys: list[str] = Field(default_factory=list)
    input: object = None
    original: str = ""
    attempt: int = 0
    feedback: dict | None = None


def my_agent(instruction: str, data, original: str, output: str, keys: list, feedback: dict | None):
    """YOUR agent. Replace the body with your own logic, framework or model."""
    text = data if isinstance(data, str) else json.dumps(data)
    if os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_BASE_URL"):
        ask = f"{instruction}\n\nINPUT:\n{text}\n\nORIGINAL INPUT:\n{original}"
        if feedback:
            ask += f"\n\nYour previous answer was rejected: {feedback.get('reason')}. Fix that."
        if output == "json":
            ask += f"\n\nReply with only a JSON object with the keys: {', '.join(keys)}."
        r = httpx.post(os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions",
                       headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', 'local')}"},
                       json={"model": os.getenv("LLM_MODEL", "gpt-4o-mini"), "messages": [{"role": "user", "content": ask}]},
                       timeout=60)
        r.raise_for_status()
        answer = r.json()["choices"][0]["message"]["content"].strip()
        if output == "json":
            m = re.search(r"\{.*\}", answer, re.S)
            return json.loads(m.group(0)) if m else {}
        return answer
    # no model configured: a simple rule-based answer (first two sentences)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if s]
    answer = " ".join(sentences[:2]) or text[:300]
    return {k: answer for k in keys} if output == "json" else answer


@app.post("/agent")
def agent(req: StepRequest, authorization: str = Header(default="")):
    token = os.getenv("MY_AGENT_TOKEN")
    if token and authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="wrong or missing token")
    out = my_agent(req.instruction, req.input, req.original, req.output, req.keys, req.feedback)
    return {"output": out}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "9000")))
