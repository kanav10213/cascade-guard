"""Use a running Cascade Guard server (for example your Render deployment) from your own agent.

    from cascade_guard.remote import RemoteGuard
    from cascade_guard import guard_step, GuardHalted

    cg = RemoteGuard("https://cascade-guard.onrender.com", api_key="...")   # or CASCADE_URL / CASCADE_API_KEY

    @guard_step(cg.check("Summarise the customer's problem in one sentence."))
    def my_agent(text, feedback=None):
        return call_my_llm(text, hint=feedback)      # your agent; feedback says why the last try was rejected

    cg.verify(task, source, output)                  # one check, returns {"passed", "verdict", "reason", ...}
    cg.run_chain("Support ticket triage", text)      # run a chain saved on the server

Only needs httpx (installed with cascade-guard-ai). The server's checker is the server's model.
"""
import os

import httpx


class RemoteGuardError(RuntimeError):
    pass


class RemoteGuard:
    def __init__(self, url: str | None = None, api_key: str | None = None, timeout: float = 120.0,
                 http_client: httpx.Client | None = None):
        self.url = (url or os.getenv("CASCADE_URL") or "http://localhost:8000").rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("CASCADE_API_KEY", "")
        self._http = http_client or httpx.Client(timeout=timeout)

    def _post(self, path: str, body: dict) -> dict:
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            r = self._http.post(self.url + path, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise RemoteGuardError(f"cannot reach Cascade Guard at {self.url}: {e}") from e
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:300]
            raise RemoteGuardError(f"Cascade Guard answered HTTP {r.status_code}: {detail}")
        return r.json()

    def _get(self, path: str):
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        r = self._http.get(self.url + path, headers=headers)
        if r.status_code >= 400:
            raise RemoteGuardError(f"Cascade Guard answered HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    def verify(self, task: str, source, output, prompt: str = "recompute") -> dict:
        """Ask the server's checker whether `output` is a correct result of `task` on `source`."""
        return self._post("/llm/verify", {"task": task, "source": source, "output": output, "prompt": prompt})

    def check(self, task: str, prompt: str = "recompute"):
        """A check function for guard_step / guarded_node that asks the server."""
        def remote_check(output, source):
            v = self.verify(task, source, output, prompt)
            return v["passed"], v.get("reason") or v.get("verdict")
        remote_check.__name__ = "remote_check"
        return remote_check

    def chains(self) -> list:
        return self._get("/chains")

    def run_chain(self, chain, input_text: str, simulated: bool = False, **options) -> dict:
        """Run a saved chain (its name or id) or a definition dict on the server."""
        if isinstance(chain, dict):
            return self._post("/chains/run", {"chain": chain, "input": input_text, "simulated": simulated, **options})
        if isinstance(chain, str) and not chain.isdigit():
            match = [c for c in self.chains() if c["name"].lower() == chain.lower()]
            if not match:
                raise RemoteGuardError(f"no saved chain called {chain!r} on {self.url}")
            chain = match[0]["id"]
        return self._post(f"/chains/{chain}/run", {"input": input_text, "simulated": simulated, **options})
