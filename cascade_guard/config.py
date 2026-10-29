"""Settings and the working folder.

The working folder holds .env, the SQLite database (data/) and the model-reply
cache (.llm_cache/). It is CASCADE_HOME if set, otherwise the current folder.

Each .env line is KEY=value (quotes optional, # starts a comment). Values already
set in the real environment win, so a deployment platform's settings override
the file.
"""
import os
from pathlib import Path


def home() -> Path:
    """The working folder: CASCADE_HOME, else the current folder."""
    return Path(os.getenv("CASCADE_HOME") or os.getcwd()).expanduser().resolve()


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".cascade-write-test"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


def use_home(path=None, fallback: bool = False) -> Path:
    """Make `path` (or home()) the working folder: create it, chdir into it, set CASCADE_HOME.

    fallback=True: if the folder cannot be written (an AI app may start the MCP server
    in a system folder), use ~/.cascade-guard instead."""
    h = Path(path).expanduser().resolve() if path else home()
    if not _writable(h):
        if not fallback:
            raise SystemExit(f"Cannot write to {h}. Set CASCADE_HOME to a folder you own.")
        h = Path.home() / ".cascade-guard"
        h.mkdir(parents=True, exist_ok=True)
    os.chdir(h)
    os.environ["CASCADE_HOME"] = str(h)
    return h


def load_env(path=None) -> dict:
    loaded: dict = {}
    if os.getenv("CASCADE_NO_DOTENV") == "1":  # set by the test suite
        return loaded
    path = Path(path) if path else home() / ".env"
    if not path.is_file():
        return loaded
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        elif " #" in val:
            val = val.split(" #", 1)[0].rstrip()
        if key and val and key not in os.environ:
            os.environ[key] = val
            loaded[key] = val
    return loaded
