"""The company's documents: read them, cut them into passages, and find the passages that answer a question.

    pages = read_document("Handbook.pdf", data)        # [(page or None, text), ...]
    doc = add_document(workspace, "Handbook.pdf", data) # stored, ready to search
    hits = search(workspace, "how many days of leave?")  # best passages first, with a relevance score

Reads PDF (with page numbers), Word (.docx), text, Markdown, HTML, CSV and JSON. Search is BM25 over words, all in
this process: no embedding model, no extra service, the same results every time, and nothing leaves the server.
"""
from __future__ import annotations

import html
import io
import math
import re
import threading
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from xml.etree import ElementTree

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 2000
CHUNK_CHARS = 900
TEXT_KINDS = {".txt", ".text", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".rst"}
KINDS = TEXT_KINDS | {".pdf", ".docx", ".html", ".htm"}


class DocumentError(ValueError):
    pass


def _ext(name: str) -> str:
    m = re.search(r"(\.[A-Za-z0-9]+)$", name or "")
    return m.group(1).lower() if m else ""


def read_document(name: str, data: bytes) -> list[tuple[int | None, str]]:
    """The document's text as (page, text) parts. Raises DocumentError with a plain reason."""
    if len(data) > MAX_FILE_BYTES:
        raise DocumentError(f"{name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    ext = _ext(name)
    if ext == ".pdf" or data[:4] == b"%PDF":
        return _pdf(data)
    if ext == ".docx":
        return [(None, _docx(data))]
    if ext in (".doc", ".xls", ".xlsx", ".ppt", ".pptx"):
        raise DocumentError(f"{ext} files are not read yet: save it as PDF, .docx or text")
    text = _decode(data)
    if ext in (".csv", ".tsv"):
        return [(None, _table_text(text, "\t" if ext == ".tsv" else ","))]
    if ext in (".html", ".htm") or re.match(r"\s*<(!doctype|html)", text[:200], re.I):
        text = _html_text(text)
    if not text.strip():
        raise DocumentError(f"{name} has no text")
    return [(None, text)]


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            text = data.decode(enc)
            if enc == "utf-16" and data[:2] not in (b"\xff\xfe", b"\xfe\xff"):
                continue
            return text
        except UnicodeDecodeError:
            continue
    return data.decode("cp1252", errors="replace")


def _table_text(text: str, delimiter: str) -> str:
    """A spreadsheet as one readable line per row ("Product: Angle grinder; Price: 3,299; Warranty: 1 year"),
    so each row can be found and quoted on its own."""
    import csv
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter) if any(c.strip() for c in r)]
    if len(rows) < 2:
        return text
    head = [c.strip() or f"column {i + 1}" for i, c in enumerate(rows[0])]
    lines = []
    for r in rows[1:]:
        cells = [f"{head[i] if i < len(head) else f'column {i + 1}'}: {c.strip()}" for i, c in enumerate(r) if c.strip()]
        lines.append("; ".join(cells))
    return "\n\n".join(lines)


def _pdf(data: bytes) -> list[tuple[int | None, str]]:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("the PDF is password-protected")
        out: list[tuple[int | None, str]] = []
        for i, page in enumerate(reader.pages[:MAX_PDF_PAGES], 1):
            text = page.extract_text() or ""
            text = "\n".join(re.sub(r"[ \t]+", " ", ln).rstrip() for ln in text.splitlines())
            if text.strip():
                out.append((i, text))
    except DocumentError:
        raise
    except Exception as e:
        raise DocumentError(f"could not read the PDF ({type(e).__name__})") from e
    if not out:
        raise DocumentError("the PDF has no text (a scan?): run it through OCR first, or upload the text")
    return out


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            root = ElementTree.fromstring(z.read("word/document.xml"))  # noqa: S314 - stdlib parser, no entities
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        raise DocumentError("not a readable .docx file") from e
    lines: list[str] = []
    body = root.find(f"{_W}body")
    for block in (body if body is not None else []):
        if block.tag == f"{_W}p":
            lines.append(_para_text(block))
        elif block.tag == f"{_W}tbl":
            for row in block.iter(f"{_W}tr"):
                cells = [" ".join(_para_text(p) for p in cell.iter(f"{_W}p")).strip() for cell in row.iter(f"{_W}tc")]
                lines.append(" | ".join(c for c in cells if c))
            lines.append("")
    return "\n".join(lines)


def _para_text(p) -> str:
    parts = []
    for node in p.iter():
        if node.tag == f"{_W}t" and node.text:
            parts.append(node.text)
        elif node.tag in (f"{_W}tab",):
            parts.append("\t")
        elif node.tag in (f"{_W}br", f"{_W}cr"):
            parts.append("\n")
    style = p.find(f"{_W}pPr/{_W}pStyle")
    text = "".join(parts)
    if style is not None and "heading" in (style.get(f"{_W}val") or "").lower() and text.strip():
        return "\n# " + text.strip()
    return text


def _html_text(text: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6]|section|article)>", "\n", text)
    text = re.sub(r"(?i)<h([1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return "\n".join(re.sub(r"[ \t]+", " ", html.unescape(ln)).strip() for ln in text.splitlines())


# ------------------------------------------------------------------ passages

HEADING = re.compile(r"^\s*(#{1,6}\s+.+|\d+(\.\d+)*\.?\s+[A-Z][^.!?]{2,80}|[A-Z][A-Z0-9 &/,'()-]{3,80}:?)\s*$")


def chunk(parts: list[tuple[int | None, str]], size: int = CHUNK_CHARS) -> list[tuple[int | None, str]]:
    """Passages of about `size` characters, cut at paragraph or sentence ends, each starting with its section
    heading so a passage still makes sense on its own."""
    out: list[tuple[int | None, str]] = []
    heading = ""
    for page, text in parts:
        paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=\s*(?:#{1,6}\s|[-*•]\s|\d+[.)]\s))", text) if p.strip()]
        buf: list[str] = []
        size_now = 0

        def flush(page: int | None, heading_now: str) -> None:
            nonlocal buf, size_now
            if buf:
                body = "\n".join(buf).strip()
                first_line = body.splitlines()[0].lstrip("#").strip()
                prefix = f"{heading_now}\n" if heading_now and first_line != heading_now else ""
                out.append((page, (prefix + body)[: size * 3]))
            buf, size_now = [], 0

        for para in paras:
            first = para.splitlines()[0]
            if HEADING.match(first) and len(first) < 90:
                flush(page, heading)
                heading = first.lstrip("#").strip()
            pieces = [para] if len(para) <= size else _sentences(para, size)
            for piece in pieces:
                if size_now + len(piece) > size and buf:
                    flush(page, heading)
                buf.append(piece)
                size_now += len(piece) + 1
        flush(page, heading)
    return [(p, t) for p, t in out if len(t.strip()) > 1]


def _sentences(text: str, size: int) -> list[str]:
    sents = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", text)
    out, cur = [], ""
    for s in sents:
        if len(cur) + len(s) > size and cur:
            out.append(cur.strip())
            cur = ""
        while len(s) > size * 2:  # a run-on line (tables, lists without full stops)
            out.append(s[:size])
            s = s[size:]
        cur += " " + s
    if cur.strip():
        out.append(cur.strip())
    return out


# ------------------------------------------------------------------ storage

def _store():
    from cascade_guard.company.keys import _store as s
    return s()


def add_document(workspace: str, name: str, data: bytes, *, by: str = "", store=None,
                 max_chunks: int | None = None) -> dict:
    import os

    from cascade_guard.storage.db import _now
    name = (name or "document").strip()[:200]
    parts = read_document(name, data)
    passages = chunk(parts)
    if not passages:
        raise DocumentError(f"{name} has no text")
    store = store or _store()
    limit = max_chunks if max_chunks is not None else int(os.getenv("KB_MAX_PASSAGES", "50000"))
    have = store._read("SELECT COUNT(*) AS n FROM kb_chunks WHERE workspace = ?", (workspace,))[0]["n"]
    if int(have) + len(passages) > limit:
        raise DocumentError(f"the workspace would have more than {limit} passages; remove old documents first")
    chars = sum(len(t) for _, t in parts)
    with store._tx() as c:
        c.execute("DELETE FROM kb_docs WHERE workspace = ? AND name = ?", (workspace, name))  # re-upload replaces
        doc_id = c.insert("INSERT INTO kb_docs (workspace, name, kind, chars, chunks, created_at, created_by)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?)", (workspace, name, _ext(name).lstrip(".") or "text", chars,
                                                            len(passages), _now(), by[:100]))
        c.executemany("INSERT INTO kb_chunks (doc_id, workspace, ord, page, text) VALUES (?, ?, ?, ?, ?)",
                      [(doc_id, workspace, i, page, text) for i, (page, text) in enumerate(passages)])
    _cache.pop(workspace, None)
    return {"id": doc_id, "name": name, "passages": len(passages), "chars": chars,
            "pages": len([p for p, _ in parts if p]) or None}


def list_documents(workspace: str, store=None) -> list[dict]:
    return (store or _store())._read("SELECT id, name, kind, chars, chunks AS passages, created_at, created_by"
                                     " FROM kb_docs WHERE workspace = ? ORDER BY name", (workspace,))


def delete_document(workspace: str, doc_id: int, store=None) -> bool:
    store = store or _store()
    with store._tx() as c:
        c.execute("DELETE FROM kb_chunks WHERE doc_id = ? AND workspace = ?", (doc_id, workspace))
        gone = c.execute("DELETE FROM kb_docs WHERE id = ? AND workspace = ?", (doc_id, workspace)).rowcount > 0
    _cache.pop(workspace, None)
    return gone


# ------------------------------------------------------------------ search

STOP = set("""a about above after again all also am an and any are as at be been before being below between both but
by can could did do does doing down during each few for from further had has have having he her here hers him his how
i if in into is it its itself just me more most my no nor not now of off on once only or other our ours out over own
please same she should so some such than that the their them then there these they this those through to too under
until up very was we were what when where which while who whom why will with would you your yours tell give show
know want need get got let us can't cannot does doesn't don't isn't""".split())  # noqa: C405, SIM905


def stem(w: str) -> str:
    """A light stemmer: approve, approves, approved, approving -> approv; policies -> policy."""
    if len(w) > 5 and w.endswith("ies"):
        return w[:-3] + "y"
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf) and not w.endswith("ss"):
            w = w[: -len(suf)]
            break
    if len(w) > 4 and w.endswith("e") and not w.endswith("ee"):
        w = w[:-1]
    return w


SAME = {"rs": "rupee", "inr": "rupee", "₹": "rupee", "rupe": "rupee", "₨": "rupee", "wfh": "home", "pto": "leave",
        "vacation": "leave", "holiday": "holiday", "salary": "salary", "pay": "salary", "per": "per", "diem": "allowance",
        "reimburse": "reimbursement", "reimbursed": "reimbursement", "refund": "refund", "mfa": "authentication",
        "2fa": "authentication", "laptop": "laptop", "notebook": "laptop"}


def words(text: str) -> list[str]:
    text = (text or "").lower().replace("₹", " rupee ")
    out = []
    for w in re.findall(r"[^\W_]+", text):
        if w in STOP:
            continue
        w = stem(w)
        out.append(SAME.get(w, w))
    return out


@dataclass
class _Index:
    version: tuple
    ids: list[int] = field(default_factory=list)
    meta: list[dict] = field(default_factory=list)
    tf: list[Counter] = field(default_factory=list)
    lens: list[int] = field(default_factory=list)
    df: Counter = field(default_factory=Counter)

    @property
    def avg(self) -> float:
        return (sum(self.lens) / len(self.lens)) if self.lens else 1.0

    def idf(self, term: str) -> float:
        n, d = len(self.ids), self.df.get(term, 0)
        return math.log(1 + (n - d + 0.5) / (d + 0.5))


_cache: dict[str, _Index] = {}
_cache_lock = threading.Lock()


def _index(workspace: str, store=None) -> _Index:
    store = store or _store()
    v = store._read("SELECT COUNT(*) AS n, MAX(id) AS m FROM kb_chunks WHERE workspace = ?", (workspace,))[0]
    version = (int(v["n"] or 0), int(v["m"] or 0))
    with _cache_lock:
        idx = _cache.get(workspace)
        if idx is not None and idx.version == version:
            return idx
    rows = store._read("SELECT c.id, c.doc_id, c.page, c.ord, c.text, d.name FROM kb_chunks c JOIN kb_docs d"
                       " ON d.id = c.doc_id WHERE c.workspace = ? ORDER BY c.id", (workspace,))
    idx = _Index(version)
    for r in rows:
        toks = words(r["text"])
        tf = Counter(toks)
        idx.ids.append(r["id"])
        idx.meta.append({"id": r["id"], "doc_id": r["doc_id"], "doc": r["name"], "page": r["page"], "ord": r["ord"],
                         "text": r["text"]})
        idx.tf.append(tf)
        idx.lens.append(len(toks))
        idx.df.update(tf.keys())
    with _cache_lock:
        _cache[workspace] = idx
    return idx


def search(workspace: str, query: str, k: int = 5, store=None, also: str = "") -> list[dict]:
    """The k best passages for the query, best first. `also` adds context words (earlier questions) at half weight.
    Each hit has `score` (BM25) and `coverage` (share of the question's words found in it)."""
    idx = _index(workspace, store)
    q = Counter(words(query))
    extra = Counter(words(also))
    if not idx.ids or not q:
        return []
    k1, b, avg = 1.4, 0.75, idx.avg
    scores = []
    for i, tf in enumerate(idx.tf):
        s = 0.0
        for term, weight in list(q.items()) + [(t, 0.5 * w) for t, w in extra.items() if t not in q]:
            f = tf.get(term)
            if f:
                s += weight * idx.idf(term) * f * (k1 + 1) / (f + k1 * (1 - b + b * idx.lens[i] / avg))
        if s > 0:
            scores.append((s, i))
    scores.sort(reverse=True)
    out = []
    terms = {t for t in q if not t.isdigit()}   # amounts in a question rarely appear in the documents
    for s, i in scores[:k]:
        found = {t for t in terms if t in idx.tf[i]}
        weight_all = sum(idx.idf(t) for t in terms)
        cover = sum(idx.idf(t) for t in found) / weight_all if weight_all else 0.0  # only numbers: not about the docs
        out.append({**idx.meta[i], "score": round(s, 3), "coverage": round(cover, 3)})
    return out


def relevant(hits: list[dict], min_coverage: float = 0.34) -> bool:
    """Do the passages plausibly answer the question (rather than share a word or two by chance)?"""
    return bool(hits) and hits[0]["coverage"] >= min_coverage
