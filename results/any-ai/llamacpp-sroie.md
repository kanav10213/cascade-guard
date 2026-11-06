# Expense receipts: real receipts, without and with Cascade Guard

Agents: **openai:local-gguf** · receipts: **30** real scanned receipts (ICDAR 2019 SROIE (train set, corrected annotations), [https://github.com/zzzDavid/ICDAR-2019-SROIE](https://github.com/zzzDavid/ICDAR-2019-SROIE) at `27be427`)

| | Without the guard | With Cascade Guard |
|---|---:|---:|
| Posted, right | 1 | 22 |
| **Posted, wrong (silent error)** | **29** | **1** |
| Stopped for a person, really wrong | 0 | 5 |
| Stopped for a person, actually right (false alarm) | 0 | 2 |
| … with the wrong amount | 8 | 1 |
| … with the wrong or badly written date | 14 | 0 |
| … with a decision that breaks the policy | 29 | 0 |
| Silent error rate (95% CI) |  96.7%  [83.3, 99.4]  (29/30) |   3.3%  [0.6, 16.7]  (1/30) |
| Agent calls per receipt | 3.0 | 4.27 |
| Seconds per receipt | 30.616 | 33.664 |

- Of the receipts the agents got wrong, the guard kept **28 of 29** out of the ledger (96.5%  [82.8, 99.4]  (28/29)); **21** of them were fixed automatically when the agent was told what was wrong.
- False alarms: 0 of 1 receipts the agents got right were stopped. Right answers the guard's retry turned wrong: 0.
- Silent errors reduced by **97%**.

What the guard caught (rejections, all steps):

- a policy decision that breaks the policy: 22
- a ledger date in the wrong format or wrong: 11
- the cash paid taken as the total: 9
- a smaller amount taken as the total: 5
- the change taken as the total: 3
- total disagrees with cash paid minus change: 3
- no date found: 2
- the subtotal taken as the total: 1
- a date that is not on the receipt: 1
- the vendor: 1
- other: 1

Examples:

| Receipt | True total / date | Agent said | Guard's reason | Result |
|---|---|---|---|---|
| 301 | 10.20 / 22/04/2018 | 10.20 / 22/04/2018 | under the policy the decision for a total of 10.20 is 'approve' (within 200.00), not 'reject' | posted_ok |
| 302 | 98.35 / 22/04/2018 | 98.35 / 22/04/2018 | under the policy the decision for a total of 98.35 is 'approve' (within 200.00), not 'reject' | posted_ok |
| 303 | 30.90 / 28/04/18 | 30.90 / 28/04/18 | the receipt has a larger amount, 45.70, that is not cash paid or change; the total is normally the largest | stopped_ok |
| 304 | 39.60 / 28/04/18 | 39.60 / 28/04/18 | under the policy the decision for a total of 39.60 is 'approve' (within 200.00), not 'reject' | posted_ok |
| 305 | 81.80 / 17/04/2018 | 81.80 / 17/04/2018 | under the policy the decision for a total of 81.80 is 'approve' (within 200.00), not 'reject' | posted_ok |
| 306 | 18.90 / 08/04/2018 | 18.90 / 08/04/2018 | under the policy the decision for a total of 18.90 is 'approve' (within 200.00), not 'reject' | posted_ok |
| 307 | 46.55 / 20/04/2018 | 46.55 / 20/04/2018 | under the policy the decision for a total of 46.55 is 'approve' (within 200.00), not 'reject' | posted_ok |
| 308 | 4.40 / 12/04/2018 | 50.50 / 12/04/2018 | 50.50 is the cash paid on the receipt, not the total | stopped_wrong |

A result is right when the posted amount and date equal the dataset's answer and the policy decision is the one the true amount calls for. The guard never sees the answers: it checks the agents' work against the receipt text and the policy only.
