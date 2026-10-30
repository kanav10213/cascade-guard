# Expense receipts with qwen2.5:3b as the agents: without and with Cascade Guard

Agents: **openai:qwen2.5:3b** · receipts: **326** real scanned receipts (ICDAR 2019 SROIE (train set, corrected annotations), [https://github.com/zzzDavid/ICDAR-2019-SROIE](https://github.com/zzzDavid/ICDAR-2019-SROIE) at `27be427`)

| | Without the guard | With Cascade Guard |
|---|---:|---:|
| Posted, right | 64 | 280 |
| **Posted, wrong (silent error)** | **262** | **18** |
| Stopped for a person, really wrong | 0 | 3 |
| Stopped for a person, actually right (false alarm) | 0 | 25 |
| Silent error rate (95% CI) |  80.4%  [75.7, 84.3]  (262/326) |   5.5%  [3.5, 8.6]  (18/326) |
| Agent calls per receipt | 3.0 | 4.29 |
| Seconds per receipt | 32.219 | 21.714 |

- Of the receipts the agents got wrong, the guard kept **245 of 262** out of the ledger (93.5%  [89.9, 95.9]  (245/262)); **221** of them were fixed automatically when the agent was told what was wrong.
- False alarms: 4 of 64 receipts the agents got right were stopped. Right answers the guard's retry turned wrong: 1.
- Silent errors reduced by **93%**.

What the guard caught (rejections, all steps):

- a policy decision that breaks the policy: 199
- a ledger date in the wrong format or wrong: 194
- a smaller amount taken as the total: 59
- the cash paid taken as the total: 18
- a tax amount taken as the total: 10
- the change taken as the total: 7
- total disagrees with cash paid minus change: 6
- a total that is not on the receipt: 4
- no total found: 3
- an amount not labelled as a total: 2
- the subtotal taken as the total: 1
- a date that is not on the receipt: 1

Examples:

| Receipt | True total / date | Agent said | Guard's reason | Result |
|---|---|---|---|---|
| 300 | 1.75 / 18/04/2018 | 1.75 / 18/04/2018 | under the policy the decision for a total of 1.75 is 'approve' (within 200.00), not 'reject' | posted_ok |
| 301 | 10.20 / 22/04/2018 | 10.20 / 22/04/2018 | the date must be 2018-04-22 (YYYY-MM-DD; the receipt's date 22/04/2018 is day first), not 22-04-2018 | posted_ok |
| 302 | 98.35 / 22/04/2018 | 98.35 / 22/04/2018 | the date must be 2018-04-22 (YYYY-MM-DD; the receipt's date 22/04/2018 is day first), not 22-04-2018 | posted_ok |
| 305 | 81.80 / 17/04/2018 | 81.80 / 17/04/2018 | the date must be 2018-04-17 (YYYY-MM-DD; the receipt's date 17/04/2018 is day first), not 17-04-2018 | posted_ok |
| 308 | 4.40 / 12/04/2018 | 4.40 / 12/04/2018 | the receipt has a larger amount, 46.10, that is not cash paid or change; the total is normally the largest | posted_wrong |
| 309 | 6.40 / 20/04/2018 | 6.40 / 20/04/2018 | under the policy the decision for a total of 6.40 is 'approve' (within 200.00), not 'reject' | posted_ok |
| 310 | 7.20 / 27/04/18 | 7.20 / 27/04/18 | the receipt has a larger amount, 15.60, that is not cash paid or change; the total is normally the largest | stopped_ok |
| 312 | 38.37 / 19/05/18 | 38.37 / 19/05/18 | under the policy the decision for a total of 38.37 is 'approve' (within 200.00), not 'reject' | posted_ok |

A result is right when the posted amount and date equal the dataset's answer. The guard never sees the answers: it checks the agents' work against the receipt text only.
