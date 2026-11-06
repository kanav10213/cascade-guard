"""One setting for any AI: local servers, cloud APIs, any URL, any command, or a Python function.

    --ai ollama:qwen2.5:7b          (or CASCADE_AI=ollama:qwen2.5:7b in the environment or .env)

    local, free, no key       ollama[:model]  lmstudio[:model]  llamacpp  vllm[:model]  jan[:model]  localai[:model]
                              (no model named: the server's first model is used)
    cloud, with your key      openai:<model>  anthropic[:model] / claude[:model]  gemini:<model>  groq[:model]
                              openrouter:<model>  mistral:<model>  deepseek[:model]  together:<model>
    any OpenAI-format server  http://host:port/v1            or  http://host:port/v1#model
    any program               cmd:<program and arguments>    (gets the prompt on stdin, prints the answer)
    no AI at all              rules                          (the rule-based agents; free and offline)
    pick for me               auto    (a cloud key in the environment, else a local server that answers, else rules)

In Python, any function works:  function_client(lambda system, user: my_model(system + "\\n\\n" + user))

Keys come from the environment (or .env), never from the --ai text: OPENAI_API_KEY, ANTHROPIC_API_KEY,
GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY, MISTRAL_API_KEY, DEEPSEEK_API_KEY, TOGETHER_API_KEY.
A local server needs no key; set OLLAMA_HOST or <NAME>_BASE_URL (e.g. LMSTUDIO_BASE_URL) if it is not on the
usual port.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from cascade_guard.llm.client import (
    DEFAULT_MODEL,
    BaseClient,
    FakeLLMClient,
    LLMResponse,
    NotConfigured,
    approx_tokens,
)


@dataclass(frozen=True)
class Preset:
    name: str
    base_url: str
    key_env: tuple[str, ...] = ()
    default_model: str | None = None
    local: bool = False
    note: str = ""


PRESETS: dict[str, Preset] = {p.name: p for p in (
    Preset("ollama", "http://localhost:11434/v1", local=True, note="ollama.com; `ollama pull qwen2.5:7b`"),
    Preset("lmstudio", "http://localhost:1234/v1", local=True, note="LM Studio: Developer tab, Start server"),
    Preset("llamacpp", "http://localhost:8080/v1", local=True, note="llama.cpp: `llama-server -m model.gguf`"),
    Preset("vllm", "http://localhost:8000/v1", local=True, note="vLLM: `vllm serve <model>`"),
    Preset("jan", "http://localhost:1337/v1", local=True, note="Jan: Settings, Local API Server"),
    Preset("localai", "http://localhost:8080/v1", local=True, note="LocalAI"),
    Preset("openai", "https://api.openai.com/v1", ("OPENAI_API_KEY",)),
    Preset("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
           note="free tier at aistudio.google.com"),
    Preset("groq", "https://api.groq.com/openai/v1", ("GROQ_API_KEY", "OPENAI_API_KEY"), "openai/gpt-oss-120b",
           note="free tier at console.groq.com"),
    Preset("openrouter", "https://openrouter.ai/api/v1", ("OPENROUTER_API_KEY",), note="has free models"),
    Preset("mistral", "https://api.mistral.ai/v1", ("MISTRAL_API_KEY",)),
    Preset("deepseek", "https://api.deepseek.com/v1", ("DEEPSEEK_API_KEY",), "deepseek-chat"),
    Preset("together", "https://api.together.xyz/v1", ("TOGETHER_API_KEY",)),
)}
ALIASES = {"claude": "anthropic", "lm-studio": "lmstudio", "llama.cpp": "llamacpp", "llama-cpp": "llamacpp",
           "google": "gemini", "none": "rules", "off": "rules", "no": "rules"}
CLOUD_ORDER = ("anthropic", "openai", "gemini", "groq", "openrouter", "mistral", "deepseek", "together")


@dataclass
class AISpec:
    kind: str                 # "preset" | "url" | "anthropic" | "command" | "rules" | "auto"
    name: str = ""
    model: str | None = None
    base_url: str | None = None
    command: list[str] | None = None

    def label(self) -> str:
        if self.kind == "rules":
            return "rule-based agents (no AI)"
        if self.kind == "command":
            return "command: " + " ".join(self.command or [])
        where = self.name or self.base_url or ""
        return f"{where}:{self.model}" if self.model else where


def parse_ai(spec: str) -> AISpec:
    """Read an --ai value. Raises NotConfigured with a plain explanation when it can't."""
    s = (spec or "").strip()
    if not s:
        raise NotConfigured("empty --ai value; try `cascade-guard ai list`")
    if s.lower().startswith(("cmd:", "command:")):
        cmd = shlex.split(s.split(":", 1)[1], posix=os.name != "nt")
        if not cmd:
            raise NotConfigured("cmd: needs a program, e.g. cmd:ollama run qwen2.5:7b")
        return AISpec("command", command=cmd)
    if re.match(r"https?://", s, re.I):
        url, _, model = s.partition("#")
        return AISpec("url", base_url=url.rstrip("/"), model=model or None)
    name, _, model = s.partition(":")
    name = ALIASES.get(name.lower(), name.lower())
    if name == "rules":
        return AISpec("rules", "rules")
    if name == "auto":
        return AISpec("auto", "auto")
    if name == "anthropic":
        return AISpec("anthropic", "anthropic", model or None)
    if name in PRESETS:
        return AISpec("preset", name, model or None)
    raise NotConfigured(f"unknown AI {name!r}. Use one of: {', '.join(['rules', 'auto', 'anthropic', *PRESETS])}, "
                        "a URL (http://host:port/v1#model) or cmd:<program>")


def _base_url(p: Preset) -> str:
    env = os.getenv(f"{p.name.upper()}_BASE_URL")
    if env:
        return env.rstrip("/")
    if p.name == "ollama" and os.getenv("OLLAMA_HOST"):
        host = os.environ["OLLAMA_HOST"].strip().rstrip("/")
        if not re.match(r"https?://", host):
            host = "http://" + host
        if host.startswith(("http://0.0.0.0", "https://0.0.0.0")):  # the server's listen address
            host = host.replace("0.0.0.0", "localhost", 1)  # noqa: S104
        if ":" not in host.split("//", 1)[1]:
            host += ":11434"
        return host + "/v1"
    return p.base_url


def _key(p: Preset) -> str | None:
    return next((os.environ[k] for k in p.key_env if os.getenv(k)), None)


def list_models(base_url: str, api_key: str | None = None, timeout: float = 3.0) -> list[str]:
    """The model names an OpenAI-format server reports (GET /models), or [] when it doesn't answer."""
    import httpx
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=timeout)
        if r.status_code != 200:
            return []
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return []
    rows = data.get("data") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        rows = (data or {}).get("models") or []
    names = [str(m.get("id") or m.get("name")) for m in rows if isinstance(m, dict) and (m.get("id") or m.get("name"))]
    # Embedding-only models can't chat; keep them last.
    return sorted(names, key=lambda n: ("embed" in n.lower(), names.index(n)))


def detect_local(timeout: float = 0.6) -> list[tuple[str, str, list[str]]]:
    """Local AI servers that answer right now: (name, base_url, models)."""
    found, seen = [], set()
    for p in PRESETS.values():
        if not p.local:
            continue
        url = _base_url(p)
        if url in seen:
            continue
        seen.add(url)
        models = list_models(url, timeout=timeout)
        if models:
            found.append((p.name, url, models))
    return found


class CommandClient(BaseClient):
    """Any program as the AI: the prompt goes in on stdin, the answer is what it prints."""
    provider = "command"

    def __init__(self, command: list[str], timeout: float | None = None, **kw):
        kw.setdefault("price_in", 0.0)
        kw.setdefault("price_out", 0.0)
        super().__init__(" ".join(command), **kw)
        self.command = command
        self.timeout = timeout or float(os.getenv("LLM_TIMEOUT", "120"))

    def _call(self, system, user, max_tokens, temperature, sample):
        prompt = f"{system}\n\n{user}" if system else user
        try:
            out = subprocess.run(self.command, input=prompt, capture_output=True, text=True,  # noqa: S603
                                 timeout=self.timeout, encoding="utf-8", errors="replace")
        except FileNotFoundError as e:
            raise RuntimeError(f"program not found: {self.command[0]}") from e
        if out.returncode != 0:
            raise RuntimeError(f"{self.command[0]} exited with {out.returncode}: {out.stderr.strip()[:300]}")
        text = strip_thinking(_ANSI.sub("", out.stdout)).strip()
        return LLMResponse(text, approx_tokens(prompt), approx_tokens(text))


class FunctionClient(BaseClient):
    """Any Python function as the AI: fn(system, user) -> the answer text."""
    provider = "function"

    def __init__(self, fn: Callable[[str, str], str], model: str = "function", **kw):
        kw.setdefault("price_in", 0.0)
        kw.setdefault("price_out", 0.0)
        super().__init__(model, **kw)
        self.fn = fn

    def _call(self, system, user, max_tokens, temperature, sample):
        text = strip_thinking(str(self.fn(system, user) or "")).strip()
        return LLMResponse(text, approx_tokens(system + user), approx_tokens(text))


def function_client(fn: Callable[[str, str], str], **kw) -> FunctionClient:
    """Use any model you can call from Python: a LangChain chat model, Hugging Face transformers, your own API.

        client = function_client(lambda system, user: llm.invoke(system + "\\n\\n" + user).content)
        process_receipt(text, client=client)
    """
    return FunctionClient(fn, **kw)


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|[⠀-⣿]")  # terminal colours and spinners
_THINK = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.S | re.I)


def strip_thinking(text: str) -> str:
    """Drop a reasoning model's <think>...</think> part, keeping the answer."""
    out = _THINK.sub("", text)
    if re.match(r"\s*<think>", out, re.I):  # cut off mid-thought: nothing usable after it
        return ""
    return out


def client_from_ai(spec: str | AISpec, **kw) -> BaseClient:
    """A client for an --ai value. `rules` gives the offline simulator (callers treat it as 'no model')."""
    from cascade_guard.llm.client import AnthropicClient, OpenAICompatClient
    s = parse_ai(spec) if isinstance(spec, str) else spec
    kw.setdefault("strict_prices", False)
    if s.kind == "auto":
        s = auto_spec()
    if s.kind == "rules":
        return FakeLLMClient(model=DEFAULT_MODEL, **kw)
    if s.kind == "command":
        return CommandClient(s.command or [], **kw)
    if s.kind == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise NotConfigured("ANTHROPIC_API_KEY is not set. Add it to the environment or .env.")
        return AnthropicClient(model=s.model or DEFAULT_MODEL, **kw)
    if s.kind == "url":
        local = bool(re.match(r"https?://(localhost|127\.|0\.0\.0\.0|host\.docker\.internal|\[::1\])", s.base_url or ""))
        key = os.getenv("CASCADE_AI_KEY") or (None if local else os.getenv("OPENAI_API_KEY"))
        model = s.model or _first_model(s.base_url or "", key, "the server at " + (s.base_url or ""))
        if local:
            kw.setdefault("price_in", 0.0)
            kw.setdefault("price_out", 0.0)
        return OpenAICompatClient(model=model, base_url=s.base_url, api_key=key or ("local" if local else None), **kw)
    p = PRESETS[s.name]
    url, key = _base_url(p), _key(p)
    if not p.local and not key:
        raise NotConfigured(f"{p.name} needs a key: set {' or '.join(p.key_env)} in the environment or .env"
                            + (f" ({p.note})" if p.note else ""))
    model = s.model or p.default_model or _first_model(url, key, p.name, local=p.local)
    if p.local:
        kw.setdefault("price_in", 0.0)
        kw.setdefault("price_out", 0.0)
    return OpenAICompatClient(model=model, base_url=url, api_key=key or "local", **kw)


def _first_model(url: str, key: str | None, who: str, local: bool = True) -> str:
    models = list_models(url, key)
    if local and models:
        return models[0]
    if not models:
        hint = " Is it running?" if local else ""
        raise NotConfigured(f"name a model for {who}, e.g. --ai {who.split()[0]}:<model>.{hint}")
    raise NotConfigured(f"name a model for {who}, e.g. --ai {who}:{models[0]} (it offers: {', '.join(models[:8])})")


def auto_spec() -> AISpec:
    """A cloud key in the environment, else a local server that answers, else the rule-based agents."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return AISpec("anthropic", "anthropic", os.getenv("LLM_MODEL") or None)
    for name in CLOUD_ORDER[1:]:
        p = PRESETS[name]
        if _key(p) and (p.default_model or os.getenv("LLM_MODEL")):
            return AISpec("preset", name, os.getenv("LLM_MODEL") or None)
    if os.getenv("OPENAI_API_KEY") and os.getenv("LLM_MODEL"):
        return AISpec("url", "openai", os.getenv("LLM_MODEL"), (os.getenv("OPENAI_BASE_URL") or
                                                                 PRESETS["openai"].base_url).rstrip("/"))
    for name, _url, _models in detect_local():
        return AISpec("preset", name)
    return AISpec("rules", "rules")


def catalogue() -> list[dict]:
    """Every AI the --ai setting knows, for `cascade-guard ai list`."""
    rows = [{"ai": "rules", "where": "built in", "needs": "nothing", "note": "rule-based agents, free and offline"},
            {"ai": "anthropic[:model] / claude", "where": "cloud", "needs": "ANTHROPIC_API_KEY",
             "note": f"default model {DEFAULT_MODEL}"}]
    for p in PRESETS.values():
        rows.append({"ai": f"{p.name}[:model]" if p.local or p.default_model else f"{p.name}:<model>",
                     "where": "this computer" if p.local else "cloud",
                     "needs": "nothing (free)" if p.local else " or ".join(p.key_env), "note": p.note})
    rows += [{"ai": "http://host:port/v1#model", "where": "anywhere", "needs": "CASCADE_AI_KEY if it asks",
              "note": "any OpenAI-format server"},
             {"ai": "cmd:<program>", "where": "this computer", "needs": "the program",
              "note": "prompt on stdin, answer on stdout"}]
    return rows
