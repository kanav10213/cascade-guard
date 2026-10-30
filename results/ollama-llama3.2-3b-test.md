# Expense receipts with llama3.2:3b as the agents: without and with Cascade Guard

Agents: **openai:llama3.2:3b** · receipts: **326** real scanned receipts (ICDAR 2019 SROIE (train set, corrected annotations), [https://github.com/zzzDavid/ICDAR-2019-SROIE](https://github.com/zzzDavid/ICDAR-2019-SROIE) at `27be427`)

| | Without the guard | With Cascade Guard |
|---|---:|---:|
| Posted, right | 13 | 216 |
| **Posted, wrong (silent error)** | **312** | **16** |
| Stopped for a person, really wrong | 1 | 40 |
| Stopped for a person, actually right (false alarm) | 0 | 54 |
| Silent error rate (95% CI) |  95.7%  [92.9, 97.4]  (312/326) |   4.9%  [3.0, 7.8]  (16/326) |
| Agent calls per receipt | 3.0 | 4.34 |
| Seconds per receipt | 25.017 | 17.349 |

- Of the receipts the agents got wrong, the guard kept **296 of 312** out of the ledger (94.9%  [91.8, 96.8]  (296/312)); **206** of them were fixed automatically when the agent was told what was wrong.
- False alarms: 3 of 13 receipts the agents got right were stopped. Right answers the guard's retry turned wrong: 0.
- Silent errors reduced by **95%**.

What the guard caught (rejections, all steps):

- a ledger date in the wrong format or wrong: 251
- a policy decision that breaks the policy: 218
- a smaller amount taken as the total: 49
- total disagrees with cash paid minus change: 42
- the cash paid taken as the total: 33
- other: 21
- a tax amount taken as the total: 11
- the vendor: 6
- a total that is not on the receipt: 6
- the change taken as the total: 4
- no date found: 1

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
| 307 | 46.55 / 20/04/2018 | 46.55 / 20/04/2018 20:48 | the receipt has a larger amount, 53.45, that is not cash paid or change; the total is normally the largest | stopped_ok |

A result is right when the posted amount and date equal the dataset's answer. The guard never sees the answers: it checks the agents' work against the receipt text only.
