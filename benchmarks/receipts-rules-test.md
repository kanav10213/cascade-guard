# Expense receipts: real receipts, without and with Cascade Guard

Agents: **rules** · receipts: **326** real scanned receipts (ICDAR 2019 SROIE (train set, corrected annotations), [https://github.com/zzzDavid/ICDAR-2019-SROIE](https://github.com/zzzDavid/ICDAR-2019-SROIE) at `27be427`)

| | Without the guard | With Cascade Guard |
|---|---:|---:|
| Posted, right | 227 | 267 |
| **Posted, wrong (silent error)** | **99** | **26** |
| Stopped for a person, really wrong | 0 | 23 |
| Stopped for a person, actually right (false alarm) | 0 | 10 |
| … with the wrong amount | 97 | 25 |
| … with the wrong or badly written date | 6 | 1 |
| … with a decision that breaks the policy | 19 | 0 |
| Silent error rate (95% CI) |  30.4%  [25.6, 35.6]  (99/326) |   8.0%  [5.5, 11.4]  (26/326) |
| Agent calls per receipt | 3.0 | 3.22 |
| Seconds per receipt | 0.0 | 0.002 |

- Of the receipts the agents got wrong, the guard kept **80 of 99** out of the ledger (80.8%  [72.0, 87.4]  (80/99)); **57** of them were fixed automatically when the agent was told what was wrong.
- False alarms: 10 of 227 receipts the agents got right were stopped. Right answers the guard's retry turned wrong: 7.
- Silent errors reduced by **74%**.

What the guard caught (rejections, all steps):

- no total found: 71
- a smaller amount taken as the total: 53
- total disagrees with cash paid minus change: 33
- a tax amount taken as the total: 7
- the vendor: 3
- the wrong date on the receipt: 3
- a date in the future: 1

Examples:

| Receipt | True total / date | Agent said | Guard's reason | Result |
|---|---|---|---|---|
| 300 | 1.75 / 18/04/2018 | 1.65 / 18/04/2018 | the receipt has a larger amount, 1.75, that is not cash paid or change; the total is normally the largest | posted_ok |
| 302 | 98.35 / 22/04/2018 | 92.80 / 22/04/2018 | the receipt has a larger amount, 98.37, that is not cash paid or change; the total is normally the largest | posted_ok |
| 307 | 46.55 / 20/04/2018 | 43.90 / 20/04/2018 | the receipt has a larger amount, 53.45, that is not cash paid or change; the total is normally the largest | stopped_wrong |
| 308 | 4.40 / 12/04/2018 | 4.15 / 12/04/2018 | the receipt has a larger amount, 46.10, that is not cash paid or change; the total is normally the largest | stopped_wrong |
| 311 | 122.80 / 01/04/2018 | 119.77 / 01/04/2018 | 119.77 is the tax summary on the receipt, not the total; the receipt has a larger amount, 122.80, that is not cash paid or change; the total | posted_ok |
| 314 | 20.00 / 20180428 | 0.00 / 20180428 | there is no total amount | posted_ok |
| 320 | RM20.40 / 04-05-2018 | None / 04-05-2018 | there is no total amount | stopped_wrong |
| 324 | 28.90 / 29/10/2017 | 1.64 / 29/10/2017 | 1.64 is the tax on the receipt, not the total; the cash paid (28.90) minus the change (0.00) is 28.90, not 1.64 | posted_ok |

A result is right when the posted amount and date equal the dataset's answer and the policy decision is the one the true amount calls for. The guard never sees the answers: it checks the agents' work against the receipt text and the policy only.
