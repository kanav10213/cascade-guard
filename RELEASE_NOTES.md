# Cascade Guard 1.4.0

New: **a real workflow, tested on real data.** This is an expense-receipt pipeline of three agents: read the receipt,
apply the expense policy, write the ledger entry. Cascade Guard checks every hand-off against the receipt itself.

**Results on 326 real scanned receipts** (public ICDAR 2019 SROIE set, held out from tuning):

| Agents | Wrong amount reaching the books: without → with the guard | Correct receipts stopped |
|---|---|---|
| Rule-based agents | 30% → **8%** | 8 |
| Rule-based, read from the photos (free OCR) | 33% → **9%** | 3 |
| Llama 3.2 3B (free open model) | 19% → **6%** | 6 |
| Qwen 2.5 3B (free open model) | 18% → **5%** | 14 |
| Qwen 2.5 7B (free open model) | 27% → **3%** | 20 |

Wrong expense-policy decisions (e.g. a fine receipt called a "duplicate") went to **zero** in every run. Retries
cost 3.2 to 4.4 agent calls per receipt instead of 3. The method and every limit are in `benchmarks/README.md`.

- **Expense desk:** open `/receipts` on your server. Drop receipts as text, PDF or photos and see what each agent read
  and every check. Stopped receipts go to the review queue, and you can download a CSV. API:
  `POST /receipts/process`. In MCP and the Claude connector: `process_receipt`.
- **Photos:** `pip install "cascade-guard-ai[ocr]"` reads receipt photos with free offline OCR.
- **Run the benchmark yourself:** `cascade-guard receipts benchmark --data <SROIE>/data --split test`, or the
  `receipts-benchmark` GitHub workflow, which runs open models free on GitHub's machines.

Get it: `pip install --upgrade cascade-guard-ai`. Full list: CHANGELOG.md
