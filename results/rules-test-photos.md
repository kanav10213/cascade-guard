# Expense receipts with rule-based agents, read from the photos: without and with Cascade Guard

Agents: **rules, from the photos (OCR)** · receipts: **326** real scanned receipts (ICDAR 2019 SROIE (train set, corrected annotations), [https://github.com/zzzDavid/ICDAR-2019-SROIE](https://github.com/zzzDavid/ICDAR-2019-SROIE) at `27be427`)

| | Without the guard | With Cascade Guard |
|---|---:|---:|
| Posted, right | 166 | 194 |
| **Posted, wrong (silent error)** | **160** | **35** |
| Stopped for a person, really wrong | 0 | 93 |
| Stopped for a person, actually right (false alarm) | 0 | 4 |
| Silent error rate (95% CI) |  49.1%  [43.7, 54.5]  (160/326) |  10.7%  [7.8, 14.6]  (35/326) |
| Agent calls per receipt | 3.0 | 3.2 |
| Seconds per receipt | 0.001 | 0.006 |

- Of the receipts the agents got wrong, the guard kept **133 of 160** out of the ledger (83.1%  [76.6, 88.1]  (133/160)); **40** of them were fixed automatically when the agent was told what was wrong.
- False alarms: 4 of 166 receipts the agents got right were stopped. Right answers the guard's retry turned wrong: 8.
- Silent errors reduced by **78%**.

What the guard caught (rejections, all steps):

- no date found: 139
- no total found: 60
- other: 57
- total disagrees with cash paid minus change: 42
- a smaller amount taken as the total: 41
- a date in the future: 5
- the vendor: 4
- a tax amount taken as the total: 3
- the wrong date on the receipt: 3
- the cash paid taken as the total: 1
- a total that is not on the receipt: 1

Examples:

| Receipt | True total / date | Agent said | Guard's reason | Result |
|---|---|---|---|---|
| 300 | 1.75 / 18/04/2018 | 1.65 / 18/04/2018 | the cash paid (1.80) minus the change (0.05) is 1.75, not 1.65 | posted_ok |
| 301 | 10.20 / 22/04/2018 | 9.62 / 22/04/2018 | the cash paid (10.20) minus the change (0.00) is 10.20, not 9.62 | posted_ok |
| 302 | 98.35 / 22/04/2018 | 92.80 / 22/04/2018 | the cash paid (100.00) minus the change (1.65) is 98.35, not 92.80 | posted_ok |
| 307 | 46.55 / 20/04/2018 | 43.90 / 20/04/2018 | the cash paid (100.00) minus the change (53.45) is 46.55, not 43.90 | posted_ok |
| 308 | 4.40 / 12/04/2018 | 4.15 / 12/04/2018 | the cash paid (50.50) minus the change (46.10) is 4.40, not 4.15 | posted_ok |
| 311 | 122.80 / 01/04/2018 | 119.77 / 01/04/2018 | 119.77 is the tax summary on the receipt, not the total; the cash paid (200.00) minus the change (77.20) is 122.80, not 119.77 | posted_ok |
| 314 | 20.00 / 20180428 | 0.00 / 20180428 | there is no total amount | posted_ok |
| 315 | 11.25 / 18/04/2018 | 0.00 /  | there is no total amount; '' is not a date | stopped_wrong |

A result is right when the posted amount and date equal the dataset's answer. The guard never sees the answers: it checks the agents' work against the receipt text only.
