# Company chat with a real AI: qwen2.5:3b

30 questions about the sample company: 26 the documents answer, 4 they don't.

| | The AI's first answer (no guard) | What Cascade Guard shows |
|---|---:|---:|
| Right (all expected facts) | 22 of 26 | 22 of 26 (15 of them with a warning) |
| **Wrong answer shown as if right** | **3** | **0** |
| Wrong answer shown with a warning | – | 3 |
| Said "not in the documents" although they answer it | 1 | 1 |
| **Made-up answer** (the documents don't say) | **0** of 4 | **0** of 4 |
| Said "not in the documents" (right) | 4 | 4 |

AI calls per question: 1.7 · seconds per question: 14.5 · verdicts: checked 7, not_found 5, unconfirmed 18

## Answers that changed

**How many unused annual leave days can be carried forward to the next year?** (right → right, unconfirmed)

- first: 10 unused annual leave days can be carried forward to the next year. [1]
- shown: 10 unused annual leave days can be carried forward to the next year. [1]

**How many sick days do we get per year?** (right → right, unconfirmed)

- first: 8 sick days per year. [1]
- shown: 8 sick days per year. [1]

**When do I need a doctor's certificate for sick leave?** (right → right, unconfirmed)

- first: You need a doctor's certificate for sick leave if you are taking more than 2 consecutive sick days. [1]
- shown: You need a doctor's certificate for sick leave if you are taking more than 2 consecutive sick days. [1]

**How long is paternity leave, and by when must it be taken?** (right → right, unconfirmed)

- first: Paternity leave is 10 working days with full pay, to be taken within 3 months of the child's birth. [1]
- shown: Paternity leave is 10 working days with full pay, to be taken within 3 months of the child's birth. [1]

**How many days in advance must leave be requested?** (wrong → wrong, warned, unconfirmed)

- first: 2 [1]
- shown: 2 [1]

**What is the notice period for a manager after confirmation?** (right → right, unconfirmed)

- first: 90 days [1]
- shown: 90 days [1]

**How long is probation, and what is the notice period during probation?** (right → right, unconfirmed)

- first: Probation is for 6 months. During probation, the notice period is 15 days. [1]
- shown: Probation is for 6 months. During probation, the notice period is 15 days. [1]

**How many days a week can I work from home?** (right → right, unconfirmed)

- first: 2 days a week [1]
- shown: 2 days a week [1]

**Who approves an expense of 20,000 rupees?** (wrong → wrong, warned, unconfirmed)

- first: The expense of 20,000 rupees needs approval from the Director of Finance. [1]
- shown: The expense of 20,000 rupees needs approval from the Director of Finance. [1]

**Above what amount is an original receipt needed?** (right → right, unconfirmed)

- first: Above ₹500 an original receipt is needed. [1]
- shown: Above ₹500 an original receipt is needed. [1]

**What is the daily allowance for travel in Pune?** (wrong → wrong, warned, unconfirmed)

- first: [1] Pune is categorized as "elsewhere", so the daily allowance is ₹1,000.
- shown: [1] Pune is categorized as "elsewhere", so the daily allowance is ₹1,000.

**What is the hotel limit per night in a metro city for a manager?** (right → right, unconfirmed)

- first: [1] Manager and above may book up to ₹9,000 per night in metro cities.
- shown: [1] Manager and above may book up to ₹9,000 per night in metro cities.

**What is the hotel limit per night in a non-metro city?** (right → right, unconfirmed)

- first: [1] The hotel limit per night in a non-metro city is ₹4,500.
- shown: [1] The hotel limit per night in a non-metro city is ₹4,500.

**What train class can employees book?** (right → right, unconfirmed)

- first: AC 2-tier [1]
- shown: AC 2-tier [1]

**How soon must a lost laptop be reported?** (right → right, unconfirmed)

- first: 24 hours [1]
- shown: 24 hours [1]

**What is the warranty on the angle grinder?** (right → right, unconfirmed)

- first: The warranty on the angle grinder (KPT-AG-100) is 1 year. [2][3]
- shown: The warranty on the angle grinder (KPT-AG-100) is 1 year. [2][3]

**What is the price of the cordless drill, excluding GST?** (right → right, unconfirmed)

- first: 6499 [1]
- shown: 6499 [1]

**What are the phone support hours?** (right → right, unconfirmed)

- first: Phone support is available Monday to Saturday, 9:00 AM to 7:00 PM. [1]
- shown: Phone support is available Monday to Saturday, 9:00 AM to 7:00 PM. [1]
