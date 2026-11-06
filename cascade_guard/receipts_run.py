"""Check your own receipts with any AI: cascade-guard receipts run FOLDER-OR-FILES [--ai ...]

    cascade-guard receipts run my-receipts/                              rule-based agents (free, offline)
    cascade-guard receipts run my-receipts/ --ai ollama:qwen2.5:7b       a local model, free
    cascade-guard receipts run bills/*.pdf --ai gemini:gemini-2.5-flash --approval-limit 5000 --currency INR

Reads .txt / .md (receipt text), .pdf (text PDFs) and .jpg / .png / .webp photos (with the [ocr] extra).
Every receipt goes through the three agents (read it, apply the expense policy, write the ledger entry), with
Cascade Guard checking each hand-off against the receipt itself. Writes a CSV of everything (--out) and lists the
receipts a person has to look at. Nothing leaves your computer unless the AI you pick is a cloud one.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

TEXT = {".txt", ".text", ".md"}
PHOTO = {".jpg", ".jpeg", ".png", ".webp"}
KINDS = TEXT | PHOTO | {".pdf"}


def find_files(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in KINDS
                          and not f.name.startswith("."))
        elif p.is_file():
            out.append(p)
        else:
            raise SystemExit(f"No such file or folder: {raw}")
    return out


def read_receipt(path: Path) -> str:
    """The receipt's text. Raises ValueError with a plain reason when it can't be read."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        from cascade_guard.pilot.pdf import PDFError, pdf_to_text
        try:
            return pdf_to_text(path.read_bytes())
        except PDFError as e:
            raise ValueError(str(e)) from e
    if ext in PHOTO:
        from cascade_guard.receipts import OCRUnavailable, text_from_image
        data = path.read_bytes()
        if ext == ".webp":
            import io

            from PIL import Image
            buf = io.BytesIO()
            Image.open(io.BytesIO(data)).convert("RGB").save(buf, "PNG")
            data = buf.getvalue()
        try:
            return text_from_image(data)
        except OCRUnavailable as e:
            raise ValueError(f'{e}. Install it with: pip install "cascade-guard-ai[ocr]"') from e
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise ValueError("the file is empty")
    return text


def run_files(files: list[Path], client=None, *, max_retries: int = 2, today: dt.date | None = None,
              progress=None) -> list[dict]:
    from cascade_guard.llm.client import BudgetExceeded, LLMCallError
    from cascade_guard.receipts import Batch, process_receipt
    from cascade_guard.receipts_api import _issue
    batch, rows = Batch(), []
    for i, f in enumerate(files, 1):
        try:
            text = read_receipt(f)
        except (ValueError, OSError) as e:
            rows.append({"name": str(f), "status": "unreadable", "problem": str(e)})
        else:
            try:
                res = process_receipt(text, client=client, batch=batch, max_retries=max_retries, today=today)
            except (LLMCallError, BudgetExceeded) as e:
                rows.append({"name": str(f), "status": "unreadable", "problem": f"the AI did not answer: {e}"})
            else:
                rows.append({"name": str(f), **res, "issue": _issue(res)})
        if progress:
            progress(i, len(files), rows[-1])
    return rows


def summary(rows: list[dict]) -> dict:
    from decimal import Decimal
    s: dict = {k: sum(1 for r in rows if r["status"] == k) for k in ("posted", "halted", "unreadable")}
    s["fixed_by_guard"] = sum(1 for r in rows if r["status"] == "posted" and r.get("caught"))
    s["receipts"] = len(rows)
    by_cur: dict[str, Decimal] = {}
    for r in rows:
        e = r.get("entry") or {}
        if r["status"] == "posted" and e.get("amount"):
            by_cur[e.get("currency") or ""] = by_cur.get(e.get("currency") or "", Decimal(0)) + Decimal(e["amount"])
    s["total_posted"] = {k: f"{v:.2f}" for k, v in by_cur.items()}
    s["agent_calls"] = sum(r.get("agent_calls", 0) for r in rows)
    return s


def _line(r: dict, width: int) -> str:
    e, f, d = r.get("entry") or {}, r.get("fields") or {}, r.get("decision") or {}
    name = Path(r["name"]).name[:width].ljust(width)
    if r["status"] == "unreadable":
        return f"  UNREADABLE {name}  {r.get('problem', '')}"
    if r["status"] == "halted":
        return f"  STOPPED    {name}  needs a person: {r.get('issue', '')}"
    tag = "FIXED   " if r.get("caught") else "OK      "
    amt = f"{e.get('amount') or f.get('total') or ''} {e.get('currency') or ''}".strip()
    line = (f"  {tag}   {name}  {(e.get('vendor') or '')[:28]:<28} {e.get('date') or '':<10} {amt:>14}  "
            f"{e.get('category') or '':<11} {d.get('decision', '')}")
    if r.get("caught"):
        line += f"\n{'':15}{'':{width}}  (the guard caught: {r.get('issue', '')[len('fixed after: '):]})"
    return line


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="cascade-guard receipts run", description=__doc__.split("\n\n")[0])
    p.add_argument("paths", nargs="+", help="receipt files or folders (.txt .pdf .jpg .png .webp)")
    p.add_argument("--ai", help="which AI does the reading: rules (default), ollama:<model>, lmstudio, gemini:<model>,"
                                " a URL, cmd:<program>, ... (see: cascade-guard ai list)")
    p.add_argument("--out", default="receipts-checked.csv", help="CSV to write (default: receipts-checked.csv)")
    p.add_argument("--json", help="also write every result, with the guard's working, as JSON here")
    p.add_argument("--approval-limit", help="totals above this need a manager (default: RECEIPTS_APPROVAL_LIMIT, "
                                            "else 200)")
    p.add_argument("--currency", help="currency for receipts that print none, e.g. INR (default: RECEIPTS_CURRENCY,"
                                      " else RM)")
    p.add_argument("--max-retries", type=int, default=2, help="times the guard sends a step back (default 2)")
    p.add_argument("--no-guard", action="store_true", help="run the agents without Cascade Guard (to compare)")
    a = p.parse_args(argv)

    from cascade_guard import receipts
    from cascade_guard.llm.client import BaseClient, NotConfigured
    from cascade_guard.llm.presets import client_from_ai
    receipts.configure(approval_limit=a.approval_limit, currency=a.currency)
    files = find_files(a.paths)
    if not files:
        raise SystemExit("No receipts found (looked for .txt .md .pdf .jpg .png .webp).")
    import os
    ai = a.ai or os.getenv("CASCADE_AI") or "rules"
    client: BaseClient | None
    try:
        client = client_from_ai(ai, cache_path=None, max_cost=float(os.getenv("LLM_MAX_COST", "1.0")))
    except NotConfigured as e:
        raise SystemExit(f"Can't use that AI: {e}") from e
    if client.is_fake:
        client, who = None, "rule-based agents (no AI)"
    else:
        who = f"{client.provider} · {client.model}" + (f" at {getattr(client, 'base_url', '')}"
                                                       if getattr(client, "base_url", None) else "")
    print(f"Checking {len(files)} receipt{'s' * (len(files) != 1)} with {who}. Policy: above "
          f"{receipts.APPROVAL_LIMIT} needs a manager.", flush=True)
    width = min(28, max(len(f.name) for f in files))
    if a.no_guard:
        from cascade_guard.llm.client import BudgetExceeded, LLMCallError
        from cascade_guard.receipts import Batch, process_receipt
        b = Batch()
        rows = []
        for f in files:
            try:
                rows.append({"name": str(f), **process_receipt(read_receipt(f), client=client, guard=False,
                                                               batch=b), "issue": ""})
            except (ValueError, OSError, LLMCallError, BudgetExceeded) as e:
                rows.append({"name": str(f), "status": "unreadable", "problem": str(e)})
            print(_line(rows[-1], width), flush=True)
    else:
        rows = run_files(files, client, max_retries=a.max_retries,
                         progress=lambda i, n, r: print(_line(r, width), flush=True))
    from cascade_guard.receipts_api import to_csv
    Path(a.out).write_text(to_csv(rows), encoding="utf-8", newline="")
    if a.json:
        Path(a.json).write_text(json.dumps({"agents": who, "summary": summary(rows), "results": rows}, indent=2,
                                           default=str, ensure_ascii=False), encoding="utf-8")
    s = summary(rows)
    money = ", ".join(f"{v} {k}".strip() for k, v in s["total_posted"].items()) or "0"
    print(f"\n{s['posted']} posted ({s['fixed_by_guard']} of them fixed by the guard first), {s['halted']} stopped for a "
          f"person, {s['unreadable']} unreadable. Posted total: {money}.")
    if s["halted"] or s["unreadable"]:
        print("Look at: " + ", ".join(Path(r["name"]).name for r in rows if r["status"] in ("halted", "unreadable")))
    print(f"Wrote {a.out}" + (f" and {a.json}" if a.json else "") + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
