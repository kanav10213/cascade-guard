# Company chat with a real AI: qwen2.5:3b

30 questions about the sample company: 26 the documents answer, 4 they don't.

| | The AI's first answer (no guard) | What Cascade Guard shows |
|---|---:|---:|
| Right (all expected facts) | 23 of 26 | 19 of 26 |
| **Wrong answer shown** | **2** | **2** |
| Said "not in the documents" although they answer it | 1 | 5 |
| **Made-up answer** (the documents don't say) | **0** of 4 | **0** of 4 |
| Said "not in the documents" (right) | 4 | 4 |

AI calls per question: 2.77 · seconds per question: 20.1 · verdicts: checked 7, not_found 9, unconfirmed 14

## Answers that changed

**How many unused annual leave days can be carried forward to the next year?** (right → right, unconfirmed)

- first: 10 unused annual leave days can be carried forward to the next year. [1]
- shown: Up to 10 unused annual leave days can be carried forward to the next year. [1]
- caught: an independent reading found: a wrong or changed number

**How many sick days do we get per year?** (right → right, unconfirmed)

- first: 8 sick days per year. [1]
- shown: 8 sick days per year. [1]
- caught: an independent reading found: 8 sick days per year. [1]

**When do I need a doctor's certificate for sick leave?** (right → said so, not_found)

- first: You need a doctor's certificate for sick leave if you have more than 2 consecutive sick days. [2]
- shown: I couldn't find this in your company's documents. The closest passages are listed below; they may not answer it.

**How long is paternity leave, and by when must it be taken?** (right → right, unconfirmed)

- first: Paternity leave is 10 working days with full pay, to be taken within 3 months of the child's birth. [1]
- shown: Paternity leave is 10 working days with full pay, to be taken within 3 months of the child's birth. [1]
- caught: an independent reading found: the passages do not state by when must it be taken

**What is the notice period for a manager after confirmation?** (right → said so, not_found)

- first: 90 days [2]
- shown: I couldn't find this in your company's documents. The closest passages are listed below; they may not answer it.

**How long is probation, and what is the notice period during probation?** (right → right, unconfirmed)

- first: Probation is 6 months. During probation, the notice period is 15 days. [1]
- shown: Probation is 6 months. [1] During probation, the notice period is 15 days. [1]
- caught: an independent reading found: the passages do not specify the notice period during probation

**How many days a week can I work from home?** (right → right, unconfirmed)

- first: 2 days a week [1]
- shown: Up to 2 days a week [1]
- caught: an independent reading found: The passages state that employees may work from home up to 2 days a week with their manager's approval, but do not specify a limit of exactly 2 days.

**Who approves an expense of 20,000 rupees?** (wrong → wrong, unconfirmed)

- first: The expense of 20,000 rupees needs approval from the Director of Finance. [1]
- shown: The expense of 20,000 rupees needs approval from the Director of Finance. [1]
- caught: an independent reading found: The expense of 20,000 rupees needs approval from the Director of Finance. [1]
- caught: an independent reading found: The expense of 20,000 rupees needs approval from the Director of Finance. According to the passages, expenses from ₹5,001 to ₹50,000 need the department head's approval, and anything above ₹50,000 needs approval from the Director of Finance. The answer incorrectly states that only the Director of Fi

**Above what amount is an original receipt needed?** (right → right, unconfirmed)

- first: Above ₹500 an original receipt is needed. [1]
- shown: Above ₹500 an original receipt is needed. [1]
- caught: an independent reading found: [1] travel-and-expense-policy.md does not support the claim that an original receipt is needed above ₹500

**What is the daily allowance for travel in Pune?** (wrong → wrong, unconfirmed)

- first: [1] Pune is categorized as "elsewhere", so the daily allowance is ₹1,000.
- shown: [1] Pune is categorized as "elsewhere", so the daily allowance is ₹1,000.
- caught: an independent reading found: The answer does not correctly reflect the daily allowance for Pune based on the provided information.

**What is the hotel limit per night in a metro city for a manager?** (right → right, unconfirmed)

- first: [1] Manager and above may book up to ₹9,000 per night in metro cities.
- shown: [1] The hotel limit per night for managers in metro cities is ₹9,000.
- caught: an independent reading found: The answer does not directly state the exact amount of ₹9,000 per night for managers in metro cities, only that they may book up to this amount.

**What is the hotel limit per night in a non-metro city?** (right → said so, not_found)

- first: The hotel limit per night in a non-metro city is ₹4,500. [1]
- shown: I couldn't find this in your company's documents. The closest passages are listed below; they may not answer it.

**What train class can employees book?** (right → right, unconfirmed)

- first: Trains: AC 2-tier for all employees. [1]
- shown: Trains: AC 2-tier for all employees. [1]
- caught: an independent reading found: the answer does not fully address the question
- caught: an independent reading found: the answer includes a citation to a passage that does not say it

**How soon must a lost laptop be reported?** (right → said so, not_found)

- first: 24 hours [1]
- shown: I couldn't find this in your company's documents. The closest passages are listed below; they may not answer it.

**What is the warranty on the angle grinder?** (right → right, unconfirmed)

- first: The warranty on the angle grinder (KPT-AG-100) is 1 year. [2][3]
- shown: The warranty on the angle grinder (KPT-AG-100) is 1 year. [1][2]
- caught: an independent reading found: the warranty on the angle grinder (KPT-AG-100) is 1 year

**What is the price of the cordless drill, excluding GST?** (right → right, unconfirmed)

- first: 6499 [1]
- shown: 6499 [1]
- caught: an independent reading found: the answer does not match the information provided in the passages

**What is the GST rate on the vernier caliper?** (right → right, unconfirmed)

- first: [1] The GST rate on the vernier caliper 150 mm (Product code: KPT-VC-5) is 12%.
- shown: [1] The GST rate on the vernier caliper 150 mm (Product code: KPT-VC-5) is 12%.
- caught: an independent reading found: the GST rate on the vernier caliper 150 mm (Product code: KPT-VC-5) is 12%

**What are the phone support hours?** (right → right, unconfirmed)

- first: Phone support is available Monday to Saturday, 9:00 AM to 7:00 PM. [1]
- shown: Phone support is available Monday to Saturday, 9:00 AM to 7:00 PM. [1]
- caught: an independent reading found: wrong number of hours
- caught: an independent reading found: wrong day of week
