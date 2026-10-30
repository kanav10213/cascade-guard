# Expense receipts with qwen2.5:7b: without and with Cascade Guard

Agents: **openai:qwen2.5:7b** · receipts: **326** real scanned receipts (ICDAR 2019 SROIE (train set, corrected annotations), [https://github.com/zzzDavid/ICDAR-2019-SROIE](https://github.com/zzzDavid/ICDAR-2019-SROIE) at `27be427`)

| | Without the guard | With Cascade Guard |
|---|---:|---:|
| Posted, right | 181 | 226 |
| **Posted, wrong (silent error)** | **108** | **10** |
| Stopped for a person, really wrong | 37 | 58 |
| Stopped for a person, actually right (false alarm) | 0 | 32 |
| … with the wrong amount | 90 | 9 |
| … with the wrong or badly written date | 36 | 1 |
| … with a decision that breaks the policy | 96 | 0 |
| Silent error rate (95% CI) |  33.1%  [28.2, 38.4]  (108/326) |   3.1%  [1.7, 5.5]  (10/326) |
| Agent calls per receipt | 3.0 | 3.48 |
| Seconds per receipt | 69.539 | 24.567 |

- Of the receipts the agents got wrong, the guard kept **101 of 108** out of the ledger (93.5%  [87.2, 96.8]  (101/108)); **57** of them were fixed automatically when the agent was told what was wrong.
- False alarms: 22 of 181 receipts the agents got right were stopped. Right answers the guard's retry turned wrong: 3.
- Silent errors reduced by **91%**.

What the guard caught (rejections, all steps):

- a policy decision that breaks the policy: 245
- a smaller amount taken as the total: 70
- the vendor: 14
- the cash paid taken as the total: 12
- a tax amount taken as the total: 10
- a total that is not on the receipt: 5
- no total found: 3
- the change taken as the total: 3
- an amount not labelled as a total: 2
- total disagrees with cash paid minus change: 2
- the subtotal taken as the total: 1
- a ledger date in the wrong format or wrong: 1

Examples:

| Receipt | True total / date | Agent said | Guard's reason | Result |
|---|---|---|---|---|
| 321 | 3.70 / 20/04/2018 | 3.70 / 20/04/2018 | under the policy the decision for a total of 3.70 is 'approve' (within 200.00), not 'reject' | stopped_wrong |
| 322 | 18.80 / 16/04/2018 | 18.80 / 16/04/2018 | under the policy the decision for a total of 18.80 is 'approve' (within 200.00), not 'reject' | stopped_wrong |
| 325 | 234.40 / 13/10/2017 | 234.40 / 13/10/2017 | under the policy the decision for a total of 234.40 is 'needs_approval' (above 200.00), not 'reject' | posted_ok |
| 327 | 78.10 / 11/08/2017 | 78.10 / 11/08/2017 | under the policy the decision for a total of 78.10 is 'approve' (within 200.00), not 'reject' | stopped_wrong |
| 335 | 41.44 / 28/08/2017 | 41.44 / 28/08/2017 | under the policy the decision for a total of 41.44 is 'approve' (within 200.00), not 'reject' | stopped_wrong |
| 336 | 39.78 / 24/08/2017 | 39.78 / 24/08/2017 | under the policy the decision for a total of 39.78 is 'approve' (within 200.00), not 'reject' | stopped_wrong |
| 344 | 54.15 / 05/09/2017 | 54.15 / 05/09/2017 | under the policy the decision for a total of 54.15 is 'approve' (within 200.00), not 'reject' | stopped_wrong |
| 345 | 29.59 / 03/09/2017 | 29.59 / 03/09/2017 | under the policy the decision for a total of 29.59 is 'approve' (within 200.00), not 'reject' | posted_ok |

A result is right when the posted amount and date equal the dataset's answer and the policy decision is the one the true amount calls for. The guard never sees the answers: it checks the agents' work against the receipt text and the policy only.
