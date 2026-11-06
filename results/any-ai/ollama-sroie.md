# Expense receipts: real receipts, without and with Cascade Guard

Agents: **openai:qwen2.5:3b** · receipts: **30** real scanned receipts (ICDAR 2019 SROIE (train set, corrected annotations), [https://github.com/zzzDavid/ICDAR-2019-SROIE](https://github.com/zzzDavid/ICDAR-2019-SROIE) at `27be427`)

| | Without the guard | With Cascade Guard |
|---|---:|---:|
| Posted, right | 5 | 26 |
| **Posted, wrong (silent error)** | **25** | **2** |
| Stopped for a person, really wrong | 0 | 1 |
| Stopped for a person, actually right (false alarm) | 0 | 1 |
| … with the wrong amount | 6 | 2 |
| … with the wrong or badly written date | 21 | 0 |
| … with a decision that breaks the policy | 15 | 0 |
| Silent error rate (95% CI) |  83.3%  [66.4, 92.7]  (25/30) |   6.7%  [1.8, 21.3]  (2/30) |
| Agent calls per receipt | 3.0 | 4.07 |
| Seconds per receipt | 42.288 | 29.854 |

- Of the receipts the agents got wrong, the guard kept **23 of 25** out of the ledger (92.0%  [75.0, 97.8]  (23/25)); **21** of them were fixed automatically when the agent was told what was wrong.
- False alarms: 0 of 5 receipts the agents got right were stopped. Right answers the guard's retry turned wrong: 0.
- Silent errors reduced by **92%**.

What the guard caught (rejections, all steps):

- a ledger date in the wrong format or wrong: 18
- a policy decision that breaks the policy: 12
- the cash paid taken as the total: 3
- a smaller amount taken as the total: 2
- the subtotal taken as the total: 1
- the change taken as the total: 1
- total disagrees with cash paid minus change: 1

Examples:

| Receipt | True total / date | Agent said | Guard's reason | Result |
|---|---|---|---|---|
| 300 | 1.75 / 18/04/2018 | 1.75 / 18/04/2018 | under the policy the decision for a total of 1.75 is 'approve' (within 200.00), not 'reject' | posted_ok |
| 301 | 10.20 / 22/04/2018 | 10.20 / 22/04/2018 | the date must be 2018-04-22 (YYYY-MM-DD; the receipt's date 22/04/2018 is day first), not 22-04-2018 | posted_ok |
| 302 | 98.35 / 22/04/2018 | 98.35 / 22/04/2018 | the date must be 2018-04-22 (YYYY-MM-DD; the receipt's date 22/04/2018 is day first), not 22-04-2018 | posted_ok |
| 303 | 30.90 / 28/04/18 | 30.90 / 28/04/18 | the receipt has a larger amount, 45.70, that is not cash paid or change; the total is normally the largest | posted_wrong |
| 304 | 39.60 / 28/04/18 | 39.60 / 28/04/18 | under the policy the decision for a total of 39.60 is 'approve' (within 200.00), not 'needs_approval' | posted_ok |
| 305 | 81.80 / 17/04/2018 | 81.80 / 17/04/2018 | the date must be 2018-04-17 (YYYY-MM-DD; the receipt's date 17/04/2018 is day first), not 17-04-2018 | posted_ok |
| 308 | 4.40 / 12/04/2018 | 4.40 / 12/04/2018 | under the policy the decision for a total of 4.40 is 'approve' (within 200.00), not 'reject' | posted_ok |
| 309 | 6.40 / 20/04/2018 | 6.40 / 20/04/2018 | under the policy the decision for a total of 6.40 is 'approve' (within 200.00), not 'reject' | posted_ok |

A result is right when the posted amount and date equal the dataset's answer and the policy decision is the one the true amount calls for. The guard never sees the answers: it checks the agents' work against the receipt text and the policy only.
