# Expense receipts with qwen2.5:7b as the agents: without and with Cascade Guard

Agents: **openai:qwen2.5:7b** · receipts: **326** real scanned receipts (ICDAR 2019 SROIE (train set, corrected annotations), [https://github.com/zzzDavid/ICDAR-2019-SROIE](https://github.com/zzzDavid/ICDAR-2019-SROIE) at `27be427`)

| | Without the guard | With Cascade Guard |
|---|---:|---:|
| Posted, right | 184 | 215 |
| **Posted, wrong (silent error)** | **80** | **11** |
| Stopped for a person, really wrong | 3 | 3 |
| Stopped for a person, actually right (false alarm) | 59 | 97 |
| Silent error rate (95% CI) |  24.5%  [20.2, 29.5]  (80/326) |   3.4%  [1.9, 5.9]  (11/326) |
| Agent calls per receipt | 3.0 | 3.6 |
| Seconds per receipt | 67.834 | 30.641 |

- Of the receipts the agents got wrong, the guard kept **74 of 80** out of the ledger (92.5%  [84.6, 96.5]  (74/80)); **43** of them were fixed automatically when the agent was told what was wrong.
- False alarms: 44 of 184 receipts the agents got right were stopped. Right answers the guard's retry turned wrong: 4.
- Silent errors reduced by **86%**.

What the guard caught (rejections, all steps):

- a policy decision that breaks the policy: 308
- a smaller amount taken as the total: 68
- the vendor: 15
- the cash paid taken as the total: 12
- a tax amount taken as the total: 10
- a total that is not on the receipt: 4
- no total found: 3
- the change taken as the total: 3
- an amount not labelled as a total: 2
- total disagrees with cash paid minus change: 2
- the subtotal taken as the total: 1
- a ledger date in the wrong format or wrong: 1

Examples:

| Receipt | True total / date | Agent said | Guard's reason | Result |
|---|---|---|---|---|
| 317 | RM5.00 / 16-04-2018 | 5.00 / 16-04-2018 | the vendor 'WESTERN EASTERN STATIONERY SDN. BHD' is not among the receipt's first lines | stopped_ok |
| 318 | 25.00 / 13/04/2018 | 25.00 / 13/04/2018 | 25.00 is the subtotal on the receipt, not the total; the receipt has a larger amount, 50.00, that is not cash paid or change; the total is n | stopped_ok |
| 321 | 3.70 / 20/04/2018 | 3.70 / 20/04/2018 | under the policy the decision for a total of 3.70 is 'approve' (within 200.00), not 'reject' | stopped_ok |
| 324 | 28.90 / 29/10/2017 | 28.90 / 29/10/2017 | under the policy the decision for a total of 28.90 is 'approve' (within 200.00), not 'reject' | stopped_ok |
| 325 | 234.40 / 13/10/2017 | 234.40 / 13/10/2017 | under the policy the decision for a total of 234.40 is 'needs_approval' (above 200.00), not 'reject' | posted_ok |
| 330 | 20.21 / 30/07/2017 | 20.21 / 30/07/2017 | the receipt has a larger amount, 39.01, that is not cash paid or change; the total is normally the largest | stopped_ok |
| 341 | RM40.00 / 05-AUG-2017 | 40.00 / 05-AUG-2017 | under the policy the decision for a total of 40.00 is 'approve' (within 200.00), not 'reject' | posted_ok |
| 342 | 19.31 / 05/08/2017 | 19.31 / 05/08/2017 | under the policy the decision for a total of 19.31 is 'approve' (within 200.00), not 'reject' | stopped_ok |

A result is right when the posted amount and date equal the dataset's answer. The guard never sees the answers: it checks the agents' work against the receipt text only.
