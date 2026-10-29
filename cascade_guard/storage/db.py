"""Storage: runs, steps, verdicts, review_queue, usage, audit, chains. SQLite or PostgreSQL.

    CASCADE_DB=data/cascade.db                          SQLite file (default; built into Python)
    CASCADE_DB=postgresql://user:pass@host:5432/dbname  PostgreSQL (needs the psycopg package)
    CASCADE_DB=off                                      no storage

The same seven tables and the same queries run on both; only placeholders,
the id column type and a few start-up commands differ.

    store = get_store()
    run_id = store.save_pipeline_run(result, input_text, guard="recompute", engine="langgraph", model={...})
    store.reviews("open")
    store.resolve_review(item_id, "approved", note="checked by hand", by="kanav")
"""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              {ID},
    created_at      TEXT NOT NULL,
    kind            TEXT NOT NULL,          -- pipeline | verify | pilot
    engine          TEXT,                   -- custom | langgraph
    provider        TEXT,
    model           TEXT,
    guard           TEXT,                   -- none | recompute | naive | rules | llm (or the verify prompt)
    input           TEXT NOT NULL,          -- the numbers, the invoice, or the verify request as JSON
    status          TEXT NOT NULL,          -- correct | wrong | crashed | halted | accepted | rejected | unparseable
    final_output    TEXT,
    expected_output TEXT,
    detected        INTEGER DEFAULT 0,
    recovered       INTEGER DEFAULT 0,
    false_alarms    INTEGER DEFAULT 0,
    cost_usd        {REAL} DEFAULT 0
);
CREATE TABLE IF NOT EXISTS steps (
    id          {ID},
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    stage       TEXT NOT NULL,
    attempt     INTEGER NOT NULL,
    injected    TEXT,
    guard_issue TEXT,
    agent_ok    INTEGER,
    output_ok   INTEGER,
    output_json TEXT
);
CREATE TABLE IF NOT EXISTS verdicts (
    id       {ID},
    run_id   INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq      INTEGER NOT NULL,
    stage    TEXT,
    prompt   TEXT,
    verdict  TEXT NOT NULL,             -- accept | reject | unparseable
    reason   TEXT
);
CREATE TABLE IF NOT EXISTS review_queue (
    id          {ID},
    run_id      INTEGER NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    reason      TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'open',   -- open | resolved
    resolution  TEXT,                           -- approved | rejected | fixed
    note        TEXT,
    resolved_by TEXT,
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS usage (
    id            {ID},
    at            TEXT NOT NULL,
    run_id        INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    provider      TEXT,
    model         TEXT,
    calls         INTEGER NOT NULL,
    cached_calls  INTEGER NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd      {REAL} NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id         {ID},
    at         TEXT NOT NULL,
    role       TEXT,                    -- admin | reviewer | viewer | none (rejected key)
    key_id     TEXT,                    -- short hash of the key, never the key itself
    client     TEXT,
    method     TEXT NOT NULL,
    path       TEXT NOT NULL,
    status     INTEGER NOT NULL,
    ms         INTEGER
);
CREATE TABLE IF NOT EXISTS chains (
    id          {ID},
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    definition  TEXT NOT NULL,          -- the chain as JSON (steps, instructions, checks)
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    created_by  TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit(at);
CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id);
CREATE INDEX IF NOT EXISTS idx_verdicts_run ON verdicts(run_id);
CREATE INDEX IF NOT EXISTS idx_review_state ON review_queue(state);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at)
"""

DIALECT = {
    "sqlite": {"ID": "INTEGER PRIMARY KEY AUTOINCREMENT", "REAL": "REAL"},
    "postgres": {"ID": "SERIAL PRIMARY KEY", "REAL": "DOUBLE PRECISION"},
}

RESOLUTIONS = ("approved", "rejected", "fixed")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def is_postgres_url(target: str) -> bool:
    return str(target).startswith(("postgresql://", "postgres://"))


class Store:
    def __init__(self, target):
        self.target = str(target)
        self.kind = "postgres" if is_postgres_url(self.target) else "sqlite"
        self._lock = threading.Lock()
        if self.kind == "postgres":
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as e:
                raise RuntimeError("PostgreSQL needs the psycopg package: pip install \"cascade-guard-ai[postgres]\"") from e
            self._conn = psycopg.connect(self.target, row_factory=dict_row)
        else:
            if self.target != ":memory:":
                Path(self.target).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.target, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            if self.target != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
        with self._tx() as c:
            for stmt in SCHEMA.format(**DIALECT[self.kind]).split(";"):
                stmt = "\n".join(ln.split("--")[0] for ln in stmt.splitlines()).strip()
                if stmt:
                    c.execute(stmt)

    @property
    def label(self) -> str:
        """Where the data is, without any password."""
        if self.kind == "postgres":
            return "PostgreSQL " + self.target.split("@")[-1]
        return "SQLite " + self.target

    # ------------------------------------------------------------ plumbing
    def _sql(self, q: str) -> str:
        return q.replace("?", "%s") if self.kind == "postgres" else q

    @contextmanager
    def _tx(self):
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield _Cursor(self, cur)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    def _read(self, q: str, args=()) -> list:
        with self._tx() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]

    # ------------------------------------------------------------ writes
    def save_pipeline_run(self, result: dict, input_text: str, *, guard: str, engine: str,
                          model: dict, usage_delta: dict | None = None,
                          validator_log: list | None = None, prompt: str | None = None,
                          kind: str = "pipeline") -> int:
        with self._tx() as c:
            run_id = c.insert(
                "INSERT INTO runs (created_at, kind, engine, provider, model, guard, input, status, final_output,"
                " expected_output, detected, recovered, false_alarms, cost_usd)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), kind, engine, model.get("provider"), model.get("model"), guard, input_text, result["status"],
                 result.get("final_output"), result.get("expected_output"), result.get("detected", 0),
                 result.get("recovered", 0), result.get("false_alarms", 0),
                 (usage_delta or {}).get("cost_usd", 0.0)))
            c.executemany(
                "INSERT INTO steps (run_id, seq, stage, attempt, injected, guard_issue, agent_ok, output_ok, output_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(run_id, i, t["stage"], t["attempt"], t.get("injected"), _s(t.get("guard_issue")),
                  _b(t.get("agent_ok")), _b(t.get("output_ok")), json.dumps(t.get("output"), default=str))
                 for i, t in enumerate(result.get("trace", []))])
            c.executemany(
                "INSERT INTO verdicts (run_id, seq, stage, prompt, verdict, reason) VALUES (?, ?, ?, ?, ?, ?)",
                [(run_id, i, v.get("stage"), prompt, v["verdict"], _s(v.get("reason")))
                 for i, v in enumerate(validator_log or [])])
            if result["status"] == "halted":
                last: dict = next((t for t in reversed(result.get("trace", [])) if t.get("guard_issue")), {})
                c.execute("INSERT INTO review_queue (run_id, created_at, reason) VALUES (?, ?, ?)",
                          (run_id, _now(), f"halted at {last.get('stage')}: {last.get('guard_issue')}"[:500]))
            self._usage(c, run_id, model, usage_delta)
            return run_id

    def save_verify(self, request: dict, result: dict, *, model: dict, usage_delta: dict | None = None) -> int:
        status = {"accept": "accepted", "reject": "rejected"}.get(result["verdict"], "unparseable")
        with self._tx() as c:
            run_id = c.insert(
                "INSERT INTO runs (created_at, kind, provider, model, guard, input, status, cost_usd)"
                " VALUES (?, 'verify', ?, ?, ?, ?, ?, ?)",
                (_now(), model.get("provider"), model.get("model"), request.get("prompt"),
                 json.dumps(request, default=str), status, (usage_delta or {}).get("cost_usd", 0.0)))
            c.execute("INSERT INTO verdicts (run_id, seq, stage, prompt, verdict, reason) VALUES (?, 0, 'custom', ?, ?, ?)",
                      (run_id, request.get("prompt"), result["verdict"], _s(result.get("reason"))))
            if status != "accepted":
                c.execute("INSERT INTO review_queue (run_id, created_at, reason) VALUES (?, ?, ?)",
                          (run_id, _now(), f"verify {status}: {_s(result.get('reason')) or ''}"[:500]))
            self._usage(c, run_id, model, usage_delta)
            return run_id

    def _usage(self, c, run_id, model, d):
        if not d or not (d.get("calls") or d.get("cached_calls")):
            return
        c.execute("INSERT INTO usage (at, run_id, provider, model, calls, cached_calls, input_tokens, output_tokens,"
                  " cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (_now(), run_id, model.get("provider"), model.get("model"), d.get("calls", 0),
                   d.get("cached_calls", 0), d.get("input_tokens", 0), d.get("output_tokens", 0),
                   d.get("cost_usd", 0.0)))

    def resolve_review(self, item_id: int, resolution: str, note: str = "", by: str = "") -> dict:
        if resolution not in RESOLUTIONS:
            raise ValueError(f"resolution must be one of {RESOLUTIONS}")
        with self._tx() as c:
            row = c.execute("SELECT state FROM review_queue WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                raise KeyError(item_id)
            if row["state"] != "open":
                raise ValueError("already resolved")
            c.execute("UPDATE review_queue SET state='resolved', resolution=?, note=?, resolved_by=?, resolved_at=?"
                      " WHERE id = ?", (resolution, note, by, _now(), item_id))
        return self.review_item(item_id)

    def purge_older_than(self, days: int) -> int:
        """Delete runs (with their steps, verdicts and review items) and audit rows older than `days`."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        with self._tx() as c:
            c.execute("DELETE FROM audit WHERE at < ?", (cutoff,))
            return c.execute("DELETE FROM runs WHERE created_at < ?", (cutoff,)).rowcount

    def save_audit(self, role, key_id, client, method, path, status, ms):
        with self._tx() as c:
            c.execute("INSERT INTO audit (at, role, key_id, client, method, path, status, ms)"
                      " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (_now(), role, key_id, client, method, path[:300], status, ms))

    def list_audit(self, limit: int = 100) -> list:
        return self._read("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------ user-defined agent chains
    def save_chain(self, definition: dict, *, chain_id: int | None = None, by: str | None = None) -> int:
        """Create a chain, or replace chain_id's definition. Raises KeyError for an unknown id and
        ValueError when the name is already used by another chain."""
        name = definition["name"]
        body = json.dumps(definition)
        with self._tx() as c:
            clash = c.execute("SELECT id FROM chains WHERE name = ?", (name,)).fetchone()
            if clash is not None and dict(clash)["id"] != chain_id:
                raise ValueError(f"a chain called {name!r} already exists")
            if chain_id is None:
                return c.insert("INSERT INTO chains (name, description, definition, created_at, updated_at, created_by)"
                                " VALUES (?, ?, ?, ?, ?, ?)",
                                (name, definition.get("description"), body, _now(), _now(), by))
            if c.execute("SELECT id FROM chains WHERE id = ?", (chain_id,)).fetchone() is None:
                raise KeyError(chain_id)
            c.execute("UPDATE chains SET name = ?, description = ?, definition = ?, updated_at = ? WHERE id = ?",
                      (name, definition.get("description"), body, _now(), chain_id))
            return chain_id

    def list_chains(self) -> list:
        rows = self._read("SELECT id, name, description, definition, created_at, updated_at, created_by"
                          " FROM chains ORDER BY name")
        for r in rows:
            r["steps"] = len(json.loads(r.pop("definition")).get("steps", []))
        return rows

    def get_chain(self, chain_id: int) -> dict | None:
        rows = self._read("SELECT * FROM chains WHERE id = ?", (chain_id,))
        if not rows:
            return None
        r = rows[0]
        r["definition"] = json.loads(r["definition"])
        return r

    def find_chain(self, name: str) -> dict | None:
        rows = self._read("SELECT id FROM chains WHERE name = ?", (name,))
        return self.get_chain(rows[0]["id"]) if rows else None

    def delete_chain(self, chain_id: int) -> bool:
        with self._tx() as c:
            found = c.execute("SELECT id FROM chains WHERE id = ?", (chain_id,)).fetchone() is not None
            c.execute("DELETE FROM chains WHERE id = ?", (chain_id,))
            return found

    def ping(self) -> bool:
        return bool(self._read("SELECT 1 AS ok"))

    def clear(self):
        """Delete everything (tests only)."""
        with self._tx() as c:
            for t in ("chains", "audit", "usage", "review_queue", "verdicts", "steps", "runs"):
                c.execute(f"DELETE FROM {t}")  # noqa: S608 - fixed table names, no user input

    # ------------------------------------------------------------ reads
    def list_runs(self, limit: int = 50, kind: str | None = None) -> list:
        q = ("SELECT id, created_at, kind, engine, provider, model, guard, input, status, final_output,"
             " detected, recovered, false_alarms, cost_usd FROM runs")
        args: list = []
        if kind:
            q += " WHERE kind = ?"
            args.append(kind)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return self._read(q, args)

    def get_run(self, run_id: int) -> dict | None:
        runs = self._read("SELECT * FROM runs WHERE id = ?", (run_id,))
        if not runs:
            return None
        steps = self._read("SELECT seq, stage, attempt, injected, guard_issue, agent_ok, output_ok, output_json"
                           " FROM steps WHERE run_id = ? ORDER BY seq", (run_id,))
        verdicts = self._read("SELECT seq, stage, prompt, verdict, reason FROM verdicts WHERE run_id = ? ORDER BY seq",
                              (run_id,))
        review = self._read("SELECT * FROM review_queue WHERE run_id = ?", (run_id,))
        for s in steps:
            s["output"] = json.loads(s.pop("output_json")) if s.get("output_json") else None
        return {**runs[0], "steps": steps, "verdicts": verdicts, "review": review[0] if review else None}

    _REVIEW = ("SELECT q.*, r.kind, r.input, r.status AS run_status, r.final_output, r.expected_output,"
               " r.guard, r.model FROM review_queue q JOIN runs r ON r.id = q.run_id")

    def review_item(self, item_id: int) -> dict:
        rows = self._read(self._REVIEW + " WHERE q.id = ?", (item_id,))
        if not rows:
            raise KeyError(item_id)
        return rows[0]

    def reviews(self, state: str = "open", limit: int = 50) -> list:
        return self._read(self._REVIEW + " WHERE q.state = ? ORDER BY q.id DESC LIMIT ?", (state, limit))

    def usage_summary(self) -> dict:
        total = self._read(
            "SELECT COUNT(*) AS requests, COALESCE(SUM(calls),0) AS calls,"
            " COALESCE(SUM(cached_calls),0) AS cached_calls, COALESCE(SUM(input_tokens),0) AS input_tokens,"
            " COALESCE(SUM(output_tokens),0) AS output_tokens, COALESCE(SUM(cost_usd),0) AS cost_usd FROM usage")[0]
        by_day = self._read(
            "SELECT substr(at, 1, 10) AS day, SUM(calls) AS calls, SUM(cost_usd) AS cost_usd"
            " FROM usage GROUP BY substr(at, 1, 10) ORDER BY day DESC LIMIT 30")
        counts = {r["status"]: int(r["n"]) for r in self._read("SELECT status, COUNT(*) AS n FROM runs GROUP BY status")}
        open_reviews = self._read("SELECT COUNT(*) AS n FROM review_queue WHERE state = 'open'")[0]["n"]
        total = {k: (round(float(v), 6) if k == "cost_usd" else int(v)) for k, v in total.items()}
        for d in by_day:
            d["calls"], d["cost_usd"] = int(d["calls"] or 0), round(float(d["cost_usd"] or 0), 6)
        return {"all_time": total, "by_day": by_day, "runs_by_status": counts, "open_reviews": int(open_reviews),
                "database": self.label}


class _Cursor:
    """Hides the placeholder and new-id differences between SQLite and PostgreSQL."""

    def __init__(self, store: Store, cur):
        self._store, self._cur = store, cur

    def execute(self, q, args=()):
        self._cur.execute(self._store._sql(q), tuple(args))
        return self._cur

    def executemany(self, q, rows):
        if rows:
            self._cur.executemany(self._store._sql(q), rows)

    def insert(self, q, args) -> int:
        if self._store.kind == "postgres":
            self._cur.execute(self._store._sql(q) + " RETURNING id", tuple(args))
            return self._cur.fetchone()["id"]
        self._cur.execute(q, tuple(args))
        return self._cur.lastrowid


def _b(v):
    return None if v is None else int(bool(v))


def _s(v):
    return None if v is None else str(v)


_store: Store | None = None
_store_lock = threading.Lock()


def get_store() -> Store | None:
    """The shared store, or None when CASCADE_DB=off."""
    global _store
    with _store_lock:
        if _store is None:
            target = os.getenv("CASCADE_DB", "data/cascade.db")
            if target.strip().lower() in ("off", "none", "0", ""):
                return None
            _store = Store(target)
            days = os.getenv("RETENTION_DAYS")
            if days and days.isdigit():
                _store.purge_older_than(int(days))
        return _store


def reset_store(store: Store | None = None):
    global _store
    with _store_lock:
        _store = store
