# Benchmarks on real receipts

These benchmarks measure what Cascade Guard does for a real workflow on real data, not on generated examples.

## The workflow

An expense-receipt pipeline of three agents, the kind finance teams automate:

1. **extract**: read the receipt's vendor, date and total;
2. **policy**: apply the expense policy (above 200 needs a manager; duplicates and receipts with no total are refused);
3. **ledger**: write the ledger entry (vendor, ISO date, amount, currency, category, memo).

The agents are either rule-based (what a developer writes first) or an open AI model. The guard checks every hand-off
**against the receipt text only**. It never sees the answers.

- The total must be next to a total label.
- It must agree with cash paid minus change, and with a rounded total, when the receipt prints them.
- It must not be the cash, the change, the tax or the subtotal.
- It must not be smaller than another unexplained amount.
- The date must be a real date on the receipt, next to its "Date" label, and not in the future.
- The vendor must be one of the first lines.
- The policy decision and the ledger fields are recomputed exactly.

A failed check goes back to the agent with the reason, up to 2 retries. A receipt that still fails is stopped for a
person.

## The data

The [ICDAR 2019 SROIE](https://rrc.cvc.uab.es/?ch=13) receipts are 626 scanned shop receipts from Malaysia. Each has
its OCR text and the true company, date and total. We use the corrected copy at
[zzzDavid/ICDAR-2019-SROIE](https://github.com/zzzDavid/ICDAR-2019-SROIE), pinned to commit `27be427`. The data is
downloaded at run time and not stored in this repository.

- **dev** (receipts 0–299) was used while writing the checks.
- **test** (receipts 300–625, 326 receipts) was not used for writing the checks. Its numbers are the headline.

It was run a few times while finding two bugs: one in the scoring and one in the rule agent's retry. No check was
changed because of a test receipt.

## How results are counted

The same agents run twice on every receipt, without and with the guard. What would be posted is compared with the
dataset's true date and total, and the policy decision with the one the true amount calls for (above 200: a manager
approves; the same vendor, date and total as an earlier receipt: a duplicate):

| Outcome | Meaning |
|---|---|
| posted, right | the amount and date are correct |
| **posted, wrong** | a **silent error**: wrong data reached the ledger and nobody was told |
| stopped, really wrong | the guard kept a wrong receipt out of the ledger |
| stopped, actually right | a false alarm: a person checks a receipt that was fine |

## Results

See the `*-test.md` files here, and the `benchmark-results` branch for the runs with AI models (made by the
`receipts-benchmark` workflow).

## Reproduce

```bash
git clone https://github.com/zzzDavid/ICDAR-2019-SROIE sroie && git -C sroie checkout 27be4271b251c256f695acbade9a801bffe85994
pip install cascade-guard-ai
cascade-guard receipts benchmark --data sroie/data --split test --md report.md                   # rule agents, seconds
pip install "cascade-guard-ai[ocr]"
cascade-guard receipts benchmark --data sroie/data --split test --photos --md report-photos.md   # from the scans
# an open model as the agents (free, local): install Ollama, `ollama pull qwen2.5:3b`, then
LLM_PROVIDER=openai OPENAI_BASE_URL=http://localhost:11434/v1 LLM_MODEL=qwen2.5:3b LLM_PRICE_IN=0 LLM_PRICE_OUT=0 \
  cascade-guard receipts benchmark --data sroie/data --split test --model --md report-qwen.md
```

On GitHub, run the **receipts-benchmark** workflow (Actions tab → receipts-benchmark → Run workflow). It is free for
public repositories: the model runs on GitHub's machines.
