# Company chat with a real AI: llama3.2:3b

30 questions about the sample company: 26 the documents answer, 4 they don't.

| | The AI's first answer (no guard) | What Cascade Guard shows |
|---|---:|---:|
| Right (all expected facts) | 25 of 26 | 25 of 26 (20 of them with a warning) |
| **Wrong answer shown as if right** | **1** | **0** |
| Wrong answer shown with a warning | – | 1 |
| Said "not in the documents" although they answer it | 0 | 0 |
| **Made-up answer** (the documents don't say) | **0** of 4 | **0** of 4 |
| Said "not in the documents" (right) | 4 | 4 |

AI calls per question: 1.73 · seconds per question: 7.3 · verdicts: checked 5, not_found 4, unconfirmed 21

## Answers that changed

**How many days of paid annual leave does a permanent employee get each year?** (right → right, unconfirmed)

- first: A permanent employee gets 24 days of paid annual leave per calendar year, credited at 2 days per month. [1]
- shown: A permanent employee gets 24 days of paid annual leave per calendar year, credited at 2 days per month. [1]

**How many unused annual leave days can be carried forward to the next year?** (right → right, unconfirmed)

- first: Up to 10 unused annual leave days can be carried forward to the next year. [1]
- shown: Up to 10 unused annual leave days can be carried forward to the next year. [1]

**How many sick days do we get per year?** (right → right, unconfirmed)

- first: According to the employee handbook, permanent employees get 8 days of paid sick leave per year. [1]
- shown: According to the employee handbook, permanent employees get 8 days of paid sick leave per year. [1]

**When do I need a doctor's certificate for sick leave?** (right → right, unconfirmed)

- first: You need a doctor's certificate for more than 2 sick days in a row. [1]
- shown: You need a doctor's certificate for more than 2 sick days in a row. [1]

**How many days in advance must leave be requested?** (right → right, unconfirmed)

- first: For leave requests, the following applies:

* Leave requests go through the HR portal at least 7 days in advance, except for sick leave.
[1]
- shown: For leave requests, the following applies:

* Leave requests go through the HR portal at least 7 days in advance, except for sick leave.
[1]

**What is the notice period for a manager after confirmation?** (right → right, unconfirmed)

- first: According to the employee handbook, the notice period for managers and above after confirmation is 90 days. [1]
- shown: According to the employee handbook, the notice period for managers and above after confirmation is 90 days. [1]

**How long is probation, and what is the notice period during probation?** (right → right, unconfirmed)

- first: New employees are on probation for 6 months. [1]
During probation, the notice period is 15 days. [1]
- shown: New employees are on probation for 6 months. [1]
During probation, the notice period is 15 days. [1]

**How many days a week can I work from home?** (right → right, unconfirmed)

- first: According to the employee handbook, employees may work from home up to 2 days a week with their manager's approval. [1]
- shown: According to the employee handbook, employees may work from home up to 2 days a week with their manager's approval. [1]

**Who approves an expense of 20,000 rupees?** (right → right, unconfirmed)

- first: According to the travel-and-expense-policy.md, expenses from ₹5,001 to ₹50,000 need the department head's approval. Since ₹20,000 falls within this range, the department head's approval is required. [1]
- shown: According to the travel-and-expense-policy.md, expenses from ₹5,001 to ₹50,000 need the department head's approval. Since ₹20,000 falls within this range, the department head's approval is required. [1]

**Up to what amount can my manager approve an expense?** (right → right, unconfirmed)

- first: According to the travel and expense policy, expenses from ₹5,001 to ₹50,000 need the department head's approval, but the passage does not specify the exact amount that the department head can approve. However, it does state that expenses up to ₹5,000 are approved by the employee's manager, and expen
- shown: According to the travel and expense policy, expenses from ₹5,001 to ₹50,000 need the department head's approval, but the passage does not specify the exact amount that the department head can approve. However, it does state that expenses up to ₹5,000 are approved by the employee's manager, and expen

**Within how many days must an expense claim be submitted?** (right → right, unconfirmed)

- first: According to passage [1], an expense claim must be submitted within 30 days of the expense.
- shown: According to passage [1], an expense claim must be submitted within 30 days of the expense.

**Above what amount is an original receipt needed?** (right → right, unconfirmed)

- first: An original receipt is needed for every expense above ₹500. [1]
- shown: An original receipt is needed for every expense above ₹500. [1]

**What is the daily allowance for travel in Pune?** (wrong → wrong, warned, unconfirmed)

- first: The daily allowance for travel in Pune is ₹1,000 per day. [1]
- shown: The daily allowance for travel in Pune is ₹1,000 per day. [1]

**What is the daily allowance for travel in Indore?** (right → right, unconfirmed)

- first: The daily allowance for travel in Indore is ₹1,000 per day. [1]
- shown: The daily allowance for travel in Indore is ₹1,000 per day. [1]

**What is the hotel limit per night in a metro city for a manager?** (right → right, unconfirmed)

- first: The hotel limit per night in a metro city for a manager is ₹9,000. [1]
- shown: The hotel limit per night in a metro city for a manager is ₹9,000. [1]

**What is the hotel limit per night in a non-metro city?** (right → right, unconfirmed)

- first: The hotel limit per night in a non-metro city is ₹4,500. [1]
- shown: The hotel limit per night in a non-metro city is ₹4,500. [1]

**What train class can employees book?** (right → right, unconfirmed)

- first: Employees can book AC 2-tier trains. [1]
- shown: Employees can book AC 2-tier trains. [1]

**What is the price of the cordless drill, excluding GST?** (right → right, unconfirmed)

- first: The price of the cordless drill, excluding GST, is 6,499 INR. [1]
- shown: The price of the cordless drill, excluding GST, is 6,499 INR. [1]

**What is the GST rate on the vernier caliper?** (right → right, unconfirmed)

- first: The GST rate on the vernier caliper is 12%. [1]
- shown: The GST rate on the vernier caliper is 12%. [1]

**Within how many days can an unused product be returned?** (right → right, unconfirmed)

- first: Unused products in their original packaging can be returned within 15 days of delivery for a full refund. [1]

Refunds reach the original payment method within 7 working days. [1]
- shown: Unused products in their original packaging can be returned within 15 days of delivery for a full refund. [1]

Refunds reach the original payment method within 7 working days. [1]

**What are the phone support hours?** (right → right, unconfirmed)

- first: The phone support hours are Monday to Saturday, 9:00 AM to 7:00 PM. [1]
- shown: The phone support hours are Monday to Saturday, 9:00 AM to 7:00 PM. [1]
