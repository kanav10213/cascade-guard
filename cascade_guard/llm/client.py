"""LLM clients with usage tracking, spend and call caps, and a response cache.

    client = make_client()                      # provider from LLM_PROVIDER (default anthropic)
    client = make_client(provider="openai")     # any OpenAI-compatible API (OpenAI, Groq,
                                                # OpenRouter, Gemini, local Ollama, ...)
    client = make_client(fake=True)             # offline simulator, no key, no cost
    resp = client.complete(system, user, max_tokens=200)

Configuration (environment variables, or a .env file in the project folder):
    LLM_PROVIDER       anthropic | openai | auto | fake     (default anthropic)
                       auto: anthropic when ANTHROPIC_API_KEY is set, else openai when OPENAI_API_KEY is
                       set, else the offline simulator, so a server works before any key is added
    LLM_MODEL          model name   (default claude-haiku-5-5 for anthropic; required for openai,
                       except on Groq, where it defaults to openai/gpt-oss-120b)
    LLM_REASONING_EFFORT  low | medium | high, for reasoning models on OpenAI-compatible APIs (optional)
    ANTHROPIC_API_KEY  for anthropic
    OPENAI_API_KEY     for openai-compatible providers (not needed for a local Ollama)
    OPENAI_BASE_URL    default https://api.openai.com/v1
    LLM_PRICE_IN / LLM_PRICE_OUT   USD per million tokens, for models not in PRICES

Every call goes through BaseClient.complete(), which
  * returns a cached answer when the exact same request was made before,
  * refuses to start a new call once spend reaches max_cost or calls reach max_calls,
  * records calls, tokens and cost in self.usage.

The SDKs are imported only when a real client is created, so the tests and the
fake client run without them.
"""
import contextlib
import hashlib
import inspect
import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODEL = "claude-haiku-5-5"
PROVIDERS = ("anthropic", "openai", "auto", "fake")
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"

# USD per million tokens (input, output) for prompts under 100k tokens.
# Haiku 5.5 rates were taken from published rate cards in Oct 2026; check
# https://docs.claude.com before quoting a cost. For any other model, set the
# prices explicitly (--price-in / --price-out, or LLM_PRICE_IN / LLM_PRICE_OUT).
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
}


class LLMCallError(RuntimeError):
    """The request itself failed (network, auth, rate limit after retries, a bad
    request). This stops the run; it is never scored as an agent mistake."""
    infrastructure = True


class BudgetExceeded(RuntimeError):
    infrastructure = True


class UnknownPrice(ValueError):
    pass


class NotConfigured(RuntimeError):
    """A required setting (API key, model name, package) is missing."""


def resolve_prices(model: str, price_in: float | None = None, price_out: float | None = None,
                   strict: bool = True):
    """(price_in, price_out) in USD per million tokens. With strict=False an
    unknown price returns (None, None): calls still work, cost shows as unknown."""
    price_in = price_in if price_in is not None else _env_float("LLM_PRICE_IN")
    price_out = price_out if price_out is not None else _env_float("LLM_PRICE_OUT")
    if price_in is not None and price_out is not None:
        return price_in, price_out
    if model in PRICES:
        return PRICES[model]
    if not strict:
        return None, None
    raise UnknownPrice(
        f"no price known for model {model!r}; pass --price-in and --price-out "
        "(USD per million tokens; 0 and 0 for a free local model) so the cost can be estimated"
    )


def _env_float(name):
    v = os.getenv(name)
    return float(v) if v else None


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cached: bool = False


@dataclass
class Usage:
    calls: int = 0           # calls actually sent to the model (billed)
    cached_calls: int = 0    # calls answered from the cache (free)
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    by_purpose: dict = field(default_factory=dict)

    def as_dict(self):
        return {
            "calls": self.calls,
            "cached_calls": self.cached_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost, 6),
            "by_purpose": self.by_purpose,
        }


class ResponseCache:
    """Append-only JSONL cache keyed by a hash of the full request."""

    def __init__(self, path):
        """path=":memory:" keeps the cache in memory only."""
        self.path = None if path == ":memory:" else Path(path)
        self._data = {}
        self._lock = threading.Lock()
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path is not None and self.path.exists():
            with self.path.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                        self._data[row["key"]] = row
                    except (ValueError, KeyError):
                        continue  # a half-written last line after a crash

    @staticmethod
    def key(**request) -> str:
        return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()

    def get(self, key):
        return self._data.get(key)

    def put(self, key, resp: LLMResponse):
        row = {"key": key, "text": resp.text, "input_tokens": resp.input_tokens,
               "output_tokens": resp.output_tokens}
        with self._lock:
            self._data[key] = row
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")

    def __len__(self):
        return len(self._data)


class BaseClient:
    is_fake = False
    provider = "base"

    def __init__(self, model=DEFAULT_MODEL, max_cost: float | None = None,
                 cache_path=None, price_in=None, price_out=None, strict_prices: bool = True,
                 max_calls: int | None = None):
        self.model = model
        self.max_cost = max_cost
        self.max_calls = max_calls
        self.price_in, self.price_out = resolve_prices(model, price_in, price_out, strict_prices)
        self.cache = ResponseCache(cache_path) if cache_path else None
        self.usage = Usage()
        self._lock = threading.Lock()

    @property
    def price_known(self) -> bool:
        return self.price_in is not None

    def cost_of(self, input_tokens, output_tokens) -> float:
        if not self.price_known:
            return 0.0
        return (input_tokens * self.price_in + output_tokens * self.price_out) / 1e6

    def describe(self) -> dict:
        return {"provider": self.provider, "model": self.model, "simulated": self.is_fake,
                "price_per_million_tokens": None if not self.price_known
                else {"input": self.price_in, "output": self.price_out},
                "max_cost_usd": self.max_cost, "max_calls": self.max_calls,
                "usage": self.usage.as_dict()}

    def cache_key(self, system, user, max_tokens, temperature, sample) -> str:
        # The provider is part of the key, so simulated answers can never be
        # served as real ones (or one provider's answers as another's).
        return ResponseCache.key(provider=self.provider, model=self.model, system=system, user=user,
                                 max_tokens=max_tokens, temperature=temperature, sample=sample)

    def complete(self, system: str, user: str, *, max_tokens: int = 300,
                 temperature: float | None = 0.0, sample: int = 0,
                 purpose: str = "other") -> LLMResponse:
        """`sample` separates otherwise identical requests (e.g. a retry), so a
        retry is a new draw from the model rather than the cached first answer."""
        key = self.cache_key(system, user, max_tokens, temperature, sample)
        if self.cache is not None:
            row = self.cache.get(key)
            if row is not None:
                with self._lock:
                    self.usage.cached_calls += 1
                return LLMResponse(row["text"], row["input_tokens"], row["output_tokens"], cached=True)
        with self._lock:
            if self.max_cost is not None and self.price_known and self.usage.cost >= self.max_cost:
                raise BudgetExceeded(
                    f"spend reached ${self.usage.cost:.4f} (cap ${self.max_cost:.2f}); "
                    "completed calls are cached, so a re-run resumes from here")
            if self.max_calls is not None and self.usage.calls >= self.max_calls:
                raise BudgetExceeded(f"call limit reached ({self.max_calls} model calls)")
        try:
            resp = self._call(system, user, max_tokens, temperature, sample)
        except Exception as e:
            raise LLMCallError(f"{type(e).__name__}: {e}") from e
        with self._lock:
            u = self.usage
            u.calls += 1
            u.input_tokens += resp.input_tokens
            u.output_tokens += resp.output_tokens
            c = self.cost_of(resp.input_tokens, resp.output_tokens)
            u.cost += c
            p = u.by_purpose.setdefault(purpose, {"calls": 0, "cost_usd": 0.0})
            p["calls"] += 1
            p["cost_usd"] = round(p["cost_usd"] + c, 6)
        if self.cache is not None:
            self.cache.put(key, resp)
        return resp

    def _call(self, system, user, max_tokens, temperature, sample) -> LLMResponse:
        raise NotImplementedError


class AnthropicClient(BaseClient):
    """Real calls through the Anthropic SDK. Needs ANTHROPIC_API_KEY."""
    provider = "anthropic"

    def __init__(self, model=DEFAULT_MODEL, **kw):
        try:
            import anthropic
        except ImportError as e:
            raise NotConfigured("the anthropic package is missing: pip install anthropic") from e
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise NotConfigured("ANTHROPIC_API_KEY is not set. Put it in the .env file and restart.")
        super().__init__(model, **kw)
        self._anthropic = anthropic
        self._sdk = anthropic.Anthropic(max_retries=5)  # SDK backs off on 429 / 5xx
        # Recent SDK versions have no sampling parameters at all; only send
        # temperature where the installed SDK accepts it.
        try:
            params = inspect.signature(self._sdk.messages.create).parameters
            self._send_temperature = "temperature" in params
        except (TypeError, ValueError):
            self._send_temperature = False

    def _call(self, system, user, max_tokens, temperature, sample):
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": user}])
        if temperature is not None and self._send_temperature:
            kwargs["temperature"] = temperature
        try:
            msg = self._sdk.messages.create(**kwargs)
        except getattr(self._anthropic, "BadRequestError", ()) as e:
            # Some models reject sampling parameters. Drop temperature and carry on.
            if "temperature" in str(e).lower() and "temperature" in kwargs:
                self._send_temperature = False
                kwargs.pop("temperature")
                msg = self._sdk.messages.create(**kwargs)
            else:
                raise
        text = "".join(getattr(b, "text", "") for b in msg.content)
        return LLMResponse(text, msg.usage.input_tokens, msg.usage.output_tokens)


class OpenAICompatClient(BaseClient):
    """Any API that speaks the OpenAI chat-completions format: OpenAI, Groq,
    OpenRouter, Together, Gemini's OpenAI endpoint, or a local Ollama
    (OPENAI_BASE_URL=http://localhost:11434/v1, no key needed)."""
    provider = "openai"

    def __init__(self, model=None, base_url=None, api_key=None, http_client=None, **kw):
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        model = model or os.getenv("LLM_MODEL") or (GROQ_DEFAULT_MODEL if "groq.com" in self.base_url else None)
        if not model:
            raise NotConfigured("LLM_MODEL is not set. For an OpenAI-compatible provider, name the model in .env.")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        local = any(h in self.base_url for h in ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal"))  # noqa: S104
        if not self.api_key and not local:
            raise NotConfigured("OPENAI_API_KEY is not set. Put it in the .env file and restart.")
        super().__init__(model, **kw)
        if http_client is None:
            try:
                import httpx
            except ImportError as e:
                raise NotConfigured("the httpx package is missing: pip install httpx") from e
            http_client = httpx.Client(timeout=float(os.getenv("LLM_TIMEOUT", "120")))
        self._http = http_client
        self._send_temperature = True
        self._tokens_field = "max_tokens"
        effort = os.getenv("LLM_REASONING_EFFORT", "").strip().lower()
        self._effort = effort if effort in ("minimal", "low", "medium", "high") else None

    def _post(self, body):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for attempt in range(5):
            r = self._http.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                wait = float(min(30, 2 ** attempt))
                with contextlib.suppress(TypeError, ValueError):
                    wait = max(wait, float(r.headers.get("retry-after", 0)))
                if wait > 20:  # a daily or per-minute limit that won't clear soon: report it now
                    return r
                time.sleep(wait)
                continue
            return r
        return r

    def _call(self, system, user, max_tokens, temperature, sample):
        for _ in range(4):
            body = {"model": self.model,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    self._tokens_field: max_tokens}
            if temperature is not None and self._send_temperature:
                body["temperature"] = temperature
            if self._effort:
                body["reasoning_effort"] = self._effort
            r = self._post(body)
            if r.status_code == 400:
                msg = r.text.lower()
                if "reasoning" in msg and self._effort:  # this model has no reasoning setting
                    self._effort = None
                    continue
                # Newer models want max_completion_tokens and/or reject temperature.
                if "max_tokens" in msg and self._tokens_field == "max_tokens":
                    self._tokens_field = "max_completion_tokens"
                    continue
                if "temperature" in msg and self._send_temperature:
                    self._send_temperature = False
                    continue
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code} from {self.base_url}: {r.text[:300]}")
            data = r.json()
            text = (data["choices"][0]["message"].get("content") or "")
            usage = data.get("usage") or {}
            return LLMResponse(text, usage.get("prompt_tokens") or approx_tokens(system + user),
                               usage.get("completion_tokens") or approx_tokens(text))
        raise RuntimeError("request rejected after adjusting parameters")


class FakeLLMClient(BaseClient):
    """Offline stand-in. `responder(system, user, sample)` returns the reply text.

    Token counts are approximated from text length and priced like the real
    model, so cost estimates and budget logic can be tested for free. The
    reported cost is what the run *would* have cost; nothing is billed."""

    is_fake = True
    provider = "fake"

    def __init__(self, responder: Callable | None = None, model=DEFAULT_MODEL, **kw):
        kw.setdefault("price_in", PRICES.get(model, (0.0, 0.0))[0])
        kw.setdefault("price_out", PRICES.get(model, (0.0, 0.0))[1])
        super().__init__(model, **kw)
        if responder is None:
            from cascade_guard.llm.fake import SimulatedLLM
            responder = SimulatedLLM()
        self.responder = responder

    def _call(self, system, user, max_tokens, temperature, sample):
        text = self.responder(system, user, sample)
        return LLMResponse(text, approx_tokens(system + user), approx_tokens(text))


def approx_tokens(text: str) -> int:
    return max(1, round(len(text) / 3.5))


def make_client(fake=False, model=None, provider=None, **kw) -> BaseClient:
    provider = (provider or os.getenv("LLM_PROVIDER") or "anthropic").strip().lower()
    if provider == "auto":
        provider = ("anthropic" if os.getenv("ANTHROPIC_API_KEY") else
                    "openai" if os.getenv("OPENAI_API_KEY") else "fake")
    if fake or provider == "fake":
        return FakeLLMClient(model=model or os.getenv("LLM_MODEL") or DEFAULT_MODEL, **kw)
    if provider == "anthropic":
        return AnthropicClient(model=model or os.getenv("LLM_MODEL") or DEFAULT_MODEL, **kw)
    if provider in ("openai", "openai-compatible", "ollama", "groq", "openrouter", "gemini"):
        return OpenAICompatClient(model=model, **kw)
    raise NotConfigured(f"unknown LLM_PROVIDER {provider!r}; use one of {', '.join(PROVIDERS)}")
