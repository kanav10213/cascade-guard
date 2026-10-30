# Expense receipts with qwen2.5:3b as the agents: without and with Cascade Guard

Agents: **openai:qwen2.5:3b** · receipts: **6** real scanned receipts (ICDAR 2019 SROIE (train set, corrected annotations), [https://github.com/zzzDavid/ICDAR-2019-SROIE](https://github.com/zzzDavid/ICDAR-2019-SROIE) at `27be427`)

| | Without the guard | With Cascade Guard |
|---|---:|---:|
| Posted, right | 2 | 5 |
| **Posted, wrong (silent error)** | **4** | **1** |
| Stopped for a person, really wrong | 0 | 0 |
| Stopped for a person, actually right (false alarm) | 0 | 0 |
| Silent error rate (95% CI) |  66.7%  [30.0, 90.3]  (4/6) |  16.7%  [3.0, 56.4]  (1/6) |
| Agent calls per receipt | 3.0 | 4.33 |
| Seconds per receipt | 30.663 | 11.133 |

- Of the receipts the agents got wrong, the guard kept **4 of 4** out of the ledger (100.0%  [51.0, 100.0]  (4/4)); **4** of them were fixed automatically when the agent was told what was wrong.
- False alarms: 0 of 2 receipts the agents got right were stopped. Right answers the guard's retry turned wrong: 1.
- Silent errors reduced by **75%**.

What the guard caught (rejections, all steps):

- a ledger date in the wrong format or wrong: 4
- a policy decision that breaks the policy: 3
- a smaller amount taken as the total: 1

Examples:

| Receipt | True total / date | Agent said | Guard's reason | Result |
|---|---|---|---|---|
| 300 | 1.75 / 18/04/2018 | 1.75 / 18/04/2018 | under the policy the decision for a total of 1.75 is 'approve' (within 200.00), not 'reject' | posted_ok |
| 301 | 10.20 / 22/04/2018 | 10.20 / 22/04/2018 | the date must be 2018-04-22 (YYYY-MM-DD; the receipt's date 22/04/2018 is day first), not 22-04-2018 | posted_ok |
| 302 | 98.35 / 22/04/2018 | 98.35 / 22/04/2018 | the date must be 2018-04-22 (YYYY-MM-DD; the receipt's date 22/04/2018 is day first), not 22-04-2018 | posted_ok |
| 305 | 81.80 / 17/04/2018 | 81.80 / 17/04/2018 | the date must be 2018-04-17 (YYYY-MM-DD; the receipt's date 17/04/2018 is day first), not 17-04-2018 | posted_ok |

A result is right when the posted amount and date equal the dataset's answer. The guard never sees the answers: it checks the agents' work against the receipt text only.
