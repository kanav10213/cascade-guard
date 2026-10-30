# Expense receipts: real receipts, without and with Cascade Guard

Agents: **rules** · receipts: **625** real scanned receipts (ICDAR 2019 SROIE (train set, corrected annotations), [https://github.com/zzzDavid/ICDAR-2019-SROIE](https://github.com/zzzDavid/ICDAR-2019-SROIE) at `27be427`) · skipped: 1 (no true total in the dataset)

| | Without the guard | With Cascade Guard |
|---|---:|---:|
| Posted, right | 395 | 508 |
| **Posted, wrong (silent error)** | **230** | **40** |
| Stopped for a person, really wrong | 0 | 63 |
| Stopped for a person, actually right (false alarm) | 0 | 14 |
| Silent error rate (95% CI) |  36.8%  [33.1, 40.6]  (230/625) |   6.4%  [4.7, 8.6]  (40/625) |
| Agent calls per receipt | 3.0 | 3.24 |
| Seconds per receipt | 0.0 | 0.002 |

- Of the receipts the agents got wrong, the guard kept **197 of 230** out of the ledger (85.7%  [80.5, 89.6]  (197/230)); **134** of them were fixed automatically when the agent was told what was wrong.
- False alarms: 14 of 395 receipts the agents got right were stopped. Right answers the guard's retry turned wrong: 7.
- Silent errors reduced by **83%**.

What the guard caught (rejections, all steps):

- no total found: 178
- total disagrees with cash paid minus change: 89
- a smaller amount taken as the total: 81
- a tax amount taken as the total: 18
- the vendor: 9
- a date in the future: 3
- the wrong date on the receipt: 3
- the subtotal taken as the total: 2

Examples:

| Receipt | True total / date | Agent said | Guard's reason | Result |
|---|---|---|---|---|
| 008 | 112.45 / 12/02/2018 | 106.10 / 12/02/2018 | the cash paid (112.45) minus the change (0.00) is 112.45, not 106.10 | posted_ok |
| 009 | 26.60 / 18/01/2018 | 11.00 / 18/01/2018 | the receipt has a larger amount, 73.40, that is not cash paid or change; the total is normally the largest | stopped_wrong |
| 011 | 15.00 / 15/06/2017 | 0.85 / 15/06/2017 | 0.85 is the tax summary on the receipt, not the total; the cash paid (15.00) minus the change (0.00) is 15.00, not 0.85 | posted_ok |
| 014 | 32.70 / 22/12/2017 | 31.03 / 22/12/2017 | the cash paid (40.00) minus the change (7.30) is 32.70, not 31.03 | posted_ok |
| 016 | 73.00 / 20/12/2017 | None / 20/12/2017 | there is no total amount | stopped_wrong |
| 018 | 73.00 / 20/12/2017 | None / 20/12/2017 | there is no total amount | stopped_wrong |
| 021 | 4.90 / 18/01/2018 | 4.60 / 18/01/2018 | the cash paid (4.90) minus the change (0.00) is 4.90, not 4.60 | posted_ok |
| 022 | 9.00 / 25/01/2018 | None / 25/01/2018 | there is no total amount | stopped_wrong |

A result is right when the posted amount and date equal the dataset's answer. The guard never sees the answers: it checks the agents' work against the receipt text only.
