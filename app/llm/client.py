"""LLM clients.

AnthropicClient  : the real thing, using only the standard library.
FakeLLM          : a SIMULATED model for tests and free dry runs. Its error rates are
                   made-up parameters, so anything it produces is NOT a measurement of
                   a real model. Reports from it are always labelled "simulated".
"""
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request

from app.agents.pipeline import compute_stats, compute_trend

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-5-5"
RETRY_STATUS = (429, 500, 502, 503, 504, 529)


class LLMError(RuntimeError):
    pass


class Usage:
    def __init__(self):
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.errors = 0
        self._lock = threading.Lock()

    def add(self, inp=0, out=0, error=False):
        with self._lock:
            self.calls += 1
            self.input_tokens += inp
            self.output_tokens += out
            self.errors += int(error)


class LLMClient:
    name = "base"
    simulated = False

    def __init__(self):
        self.usage = Usage()

    def complete(self, system: str, user: str) -> str:
        raise NotImplementedError


class AnthropicClient(LLMClient):
    def __init__(self, model=None, api_key=None, max_tokens=400, timeout=60, max_attempts=5):
        super().__init__()
        self.model = model or os.getenv("CG_MODEL", DEFAULT_MODEL)
        self.name = self.model
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_attempts = max_attempts

    def complete(self, system: str, user: str) -> str:
        body = json.dumps({
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }).encode()
        headers = {"content-type": "application/json", "x-api-key": self.api_key,
                   "anthropic-version": API_VERSION}
        last = "unknown error"
        for attempt in range(self.max_attempts):
            try:
                req = urllib.request.Request(API_URL, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode())
                usage = data.get("usage", {})
                self.usage.add(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
                return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            except urllib.error.HTTPError as e:
                detail = e.read()[:300].decode("utf-8", "replace") if hasattr(e, "read") else ""
                last = f"HTTP {e.code}: {detail}"
                if e.code in RETRY_STATUS and attempt < self.max_attempts - 1:
                    try:
                        delay = float(e.headers.get("retry-after")) if e.headers else 2 ** attempt
                    except (TypeError, ValueError):
                        delay = 2 ** attempt
                    time.sleep(min(delay, 30))
                    continue
                break
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = f"network error: {e}"
                if attempt < self.max_attempts - 1:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                break
        self.usage.add(error=True)
        raise LLMError(last)


class FakeLLM(LLMClient):
    """Simulated model. Parameters are assumptions, not data."""
    name = "simulated"
    simulated = True

    def __init__(self, seed=0, analyst_error=0.08, trend_error=0.05, naive_fp=0.03,
                 recompute_fp=0.02, recompute_detect=0.93):
        super().__init__()
        self.seed = seed
        self.analyst_error = analyst_error
        self.trend_error = trend_error
        self.naive_fp = naive_fp
        self.recompute_fp = recompute_fp
        self.recompute_detect = recompute_detect
        self._seen = {}
        self._lock = threading.Lock()

    def _rng(self, task, user):
        with self._lock:
            n = self._seen.get((task, user), 0)
            self._seen[(task, user)] = n + 1
        return random.Random(f"{self.seed}:{task}:{user}:{n}")

    def complete(self, system: str, user: str) -> str:
        task = re.search(r"\[task=([a-z-]+)\]", system).group(1)
        nums = [int(x) for x in re.search(r"NUMBERS: (.*)", user).group(1).split(",")]
        rng = self._rng(task, user)
        if task == "analyst":
            out = self._analyst(nums, rng)
        elif task == "trend":
            out = self._trend(nums, rng)
        else:
            out = self._validator(task, nums, user, rng)
        text = json.dumps(out)
        self.usage.add((len(system) + len(user)) // 4, len(text) // 4)
        return text

    def _analyst(self, nums, rng):
        t = compute_stats(nums)
        if rng.random() >= self.analyst_error:
            return t
        kind = rng.choice(["sum", "max", "count"])
        d = rng.choice([-1, 1]) * rng.randint(1, max(2, t["sum"] // 10))
        if kind == "sum":
            t["sum"] += d
            t["mean"] = round(t["sum"] / t["count"], 2)
        elif kind == "max":
            t["max"] += d
        else:
            t["count"] += rng.choice([-1, 1])
        return t

    def _trend(self, nums, rng):
        t = compute_trend(nums)
        if rng.random() < self.trend_error:
            t = rng.choice([x for x in ("increasing", "decreasing", "mixed", "flat") if x != t])
        return {"trend": t}

    def _validator(self, task, nums, user, rng):
        truth, true_trend = compute_stats(nums), compute_trend(nums)
        try:
            figs = json.loads(re.search(r"FIGURES: (.*)", user).group(1))
            trend = re.search(r"TREND: (.*)", user).group(1).strip()
        except (AttributeError, ValueError):
            figs, trend = {}, ""
        figs_ok = figs == truth
        trend_ok = trend == true_trend
        try:
            internal_ok = abs(figs["mean"] - round(figs["sum"] / figs["count"], 2)) <= 0.01
        except (KeyError, TypeError, ZeroDivisionError):
            internal_ok = False
        if task == "validator-naive":
            if figs_ok and trend_ok:
                detect = rng.random() < self.naive_fp
            elif not figs_ok and not internal_ok:
                detect = rng.random() < 0.85
            elif not figs_ok:
                rel = abs(figs.get("sum", 0) - truth["sum"]) / max(1, truth["sum"])
                detect = rng.random() < 0.12 + min(0.4, rel)
            else:
                detect = rng.random() < 0.15
            return {"valid": not detect, "reason": "simulated"}
        wrong = not (figs_ok and trend_ok)
        detect = rng.random() < (self.recompute_detect if wrong else self.recompute_fp)
        return {"recomputed": {**truth, "trend": true_trend}, "valid": not detect, "reason": "simulated"}


def get_client(simulated=None, model=None, seed=0):
    """simulated=True -> FakeLLM; False -> real; None -> real if a key is set, else simulated."""
    if simulated is None:
        simulated = not os.getenv("ANTHROPIC_API_KEY")
    return FakeLLM(seed=seed) if simulated else AnthropicClient(model=model)
