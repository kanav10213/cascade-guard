# Expense receipts with llama3.2:3b: without and with Cascade Guard

Agents: **openai:llama3.2:3b** · receipts: **326** real scanned receipts (ICDAR 2019 SROIE (train set, corrected annotations), [https://github.com/zzzDavid/ICDAR-2019-SROIE](https://github.com/zzzDavid/ICDAR-2019-SROIE) at `27be427`)

| | Without the guard | With Cascade Guard |
|---|---:|---:|
| Posted, right | 6 | 215 |
| **Posted, wrong (silent error)** | **320** | **18** |
| Stopped for a person, really wrong | 0 | 87 |
| Stopped for a person, actually right (false alarm) | 0 | 6 |
| … with the wrong amount | 63 | 18 |
| … with the wrong or badly written date | 308 | 0 |
| … with a decision that breaks the policy | 130 | 0 |
| Silent error rate (95% CI) |  98.2%  [96.0, 99.2]  (320/326) |   5.5%  [3.5, 8.6]  (18/326) |
| Agent calls per receipt | 3.0 | 4.41 |
| Seconds per receipt | 37.185 | 24.807 |

- Of the receipts the agents got wrong, the guard kept **302 of 320** out of the ledger (94.4%  [91.3, 96.4]  (302/320)); **210** of them were fixed automatically when the agent was told what was wrong.
- False alarms: 0 of 6 receipts the agents got right were stopped. Right answers the guard's retry turned wrong: 0.
- Silent errors reduced by **94%**.

What the guard caught (rejections, all steps):

- a ledger date in the wrong format or wrong: 257
- a policy decision that breaks the policy: 251
- total disagrees with cash paid minus change: 44
- a smaller amount taken as the total: 32
- the cash paid taken as the total: 24
- other: 23
- a total that is not on the receipt: 6
- the vendor: 5
- the change taken as the total: 4
- an amount not labelled as a total: 1
- no date found: 1
- a tax amount taken as the total: 1

Examples:

| Receipt | True total / date | Agent said | Guard's reason | Result |
|---|---|---|---|---|
| 300 | 1.75 / 18/04/2018 | 1.75 / 18/04/2018 | the date must be 2018-04-18 (YYYY-MM-DD; the receipt's date 18/04/2018 is day first), not 18/04/2018 | posted_ok |
| 301 | 10.20 / 22/04/2018 | 10.20 / 22/04/2018 19:29 | the date must be 2018-04-22 (YYYY-MM-DD; the receipt's date 22/04/2018 19:29 is day first), not 22/04/2018 | posted_ok |
| 302 | 98.35 / 22/04/2018 | 98.35 / 22/04/2018 19:26 | the date must be 2018-04-22 (YYYY-MM-DD; the receipt's date 22/04/2018 19:26 is day first), not 22/04/2018 | posted_ok |
| 303 | 30.90 / 28/04/18 | 30.90 / 28/04/18 | the receipt has a larger amount, 45.70, that is not cash paid or change; the total is normally the largest; the vendor 'DOMMAL FOOD SERVICES | stopped_ok |
| 304 | 39.60 / 28/04/18 | 39.60 / 28/04/18 | the vendor 'DOMMAL FOOD SERVICES SDN BHD' is not among the receipt's first lines | posted_ok |
| 305 | 81.80 / 17/04/2018 | 81.80 / 17/04/2018 | the date must be 2018-04-17 (YYYY-MM-DD; the receipt's date 17/04/2018 is day first), not 17/04/2018 | posted_ok |
| 306 | 18.90 / 08/04/2018 | 50.00 / 08/04/2018 | 50.00 is the cash paid on the receipt, not the total | posted_ok |
| 307 | 46.55 / 20/04/2018 | 46.55 / 20/04/2018 20:48 | the date must be 2018-04-20 (YYYY-MM-DD; the receipt's date 20/04/2018 20:48 is day first), not 20/04/2018 | posted_ok |

A result is right when the posted amount and date equal the dataset's answer and the policy decision is the one the true amount calls for. The guard never sees the answers: it checks the agents' work against the receipt text and the policy only.
