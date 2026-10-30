# Expense receipts with qwen2.5:7b: without and with Cascade Guard

Agents: **openai:qwen2.5:7b** · receipts: **326** real scanned receipts (ICDAR 2019 SROIE (train set, corrected annotations), [https://github.com/zzzDavid/ICDAR-2019-SROIE](https://github.com/zzzDavid/ICDAR-2019-SROIE) at `27be427`)

| | Without the guard | With Cascade Guard |
|---|---:|---:|
| Posted, right | 180 | 230 |
| **Posted, wrong (silent error)** | **111** | **10** |
| Stopped for a person, really wrong | 35 | 66 |
| Stopped for a person, actually right (false alarm) | 0 | 20 |
| … with the wrong amount | 88 | 9 |
| … with the wrong or badly written date | 35 | 1 |
| … with a decision that breaks the policy | 100 | 0 |
| Silent error rate (95% CI) |  34.1%  [29.1, 39.4]  (111/326) |   3.1%  [1.7, 5.5]  (10/326) |
| Agent calls per receipt | 3.0 | 3.48 |
| Seconds per receipt | 72.411 | 23.865 |

- Of the receipts the agents got wrong, the guard kept **106 of 111** out of the ledger (95.5%  [89.9, 98.1]  (106/111)); **59** of them were fixed automatically when the agent was told what was wrong.
- False alarms: 13 of 180 receipts the agents got right were stopped. Right answers the guard's retry turned wrong: 4.
- Silent errors reduced by **91%**.

What the guard caught (rejections, all steps):

- a policy decision that breaks the policy: 263
- a smaller amount taken as the total: 51
- the vendor: 15
- the cash paid taken as the total: 6
- a total that is not on the receipt: 6
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
| 324 | 28.90 / 29/10/2017 | 28.90 / 29/10/2017 | under the policy the decision for a total of 28.90 is 'approve' (within 200.00), not 'reject' | stopped_wrong |
| 325 | 234.40 / 13/10/2017 | 234.40 / 13/10/2017 | under the policy the decision for a total of 234.40 is 'needs_approval' (above 200.00), not 'reject' | posted_ok |
| 327 | 78.10 / 11/08/2017 | 78.10 / 11/08/2017 | under the policy the decision for a total of 78.10 is 'approve' (within 200.00), not 'reject' | stopped_wrong |
| 335 | 41.44 / 28/08/2017 | 41.44 / 28/08/2017 | under the policy the decision for a total of 41.44 is 'approve' (within 200.00), not 'reject' | stopped_wrong |
| 336 | 39.78 / 24/08/2017 | 39.78 / 24/08/2017 | under the policy the decision for a total of 39.78 is 'approve' (within 200.00), not 'reject' | posted_ok |
| 338 | 50.60 / 19/08/2017 | 50.60 / 19/08/2017 | under the policy the decision for a total of 50.60 is 'approve' (within 200.00), not 'duplicate' | stopped_wrong |

A result is right when the posted amount and date equal the dataset's answer and the policy decision is the one the true amount calls for. The guard never sees the answers: it checks the agents' work against the receipt text and the policy only.
