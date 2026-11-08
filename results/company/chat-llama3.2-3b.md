# Company chat with a real AI: llama3.2:3b

30 questions about the sample company: 26 the documents answer, 4 they don't.

| | The AI's first answer (no guard) | What Cascade Guard shows |
|---|---:|---:|
| Right (all expected facts) | 25 of 26 | 19 of 26 |
| **Wrong answer shown** | **1** | **4** |
| Said "not in the documents" although they answer it | 0 | 3 |
| **Made-up answer** (the documents don't say) | **0** of 4 | **0** of 4 |
| Said "not in the documents" (right) | 4 | 4 |

AI calls per question: 3.13 · seconds per question: 27.4 · verdicts: checked 5, fixed 1, not_found 7, unconfirmed 17

## Answers that changed

**How many days of paid annual leave does a permanent employee get each year?** (right → right, unconfirmed)

- first: A permanent employee gets 24 days of paid annual leave per calendar year, credited at 2 days per month. [1]
- shown: A permanent employee gets 24 days of paid annual leave per calendar year, credited at 2 days per month. [1]
- caught: an independent reading found: The number of days of paid annual leave per calendar year is stated as 24, but the passage also states that it is credited at 2 days per month, which implies that the total number of days is 24 * 2 = 48, not 24.
- caught: "This answer is based on the passage that states the total number of days is 24, without specifying t" does not match what the passages say

**How many unused annual leave days can be carried forward to the next year?** (right → right, unconfirmed)

- first: Up to 10 unused annual leave days can be carried forward to the next year. [1]
- shown: According to the employee handbook, up to 10 unused annual leave days can be carried forward to the next year. [1]
- caught: an independent reading found: The number of unused annual leave days that can be carried forward to the next year is stated as 10, but the passage does not explicitly state that it is 'up to' 10, only that it is 10.

**How many sick days do we get per year?** (right → right, unconfirmed)

- first: According to the employee handbook, permanent employees get 8 days of paid sick leave per year. [1]
- shown: According to the employee handbook, permanent employees get 8 days of paid sick leave per year. [1]
- caught: an independent reading found: The number of sick days is not specified as a fixed number, but rather as a range (more than 2 sick days in a row require a doctor's certificate), and the total amount of sick leave (8 days per year) is not explained.

**When do I need a doctor's certificate for sick leave?** (right → right, unconfirmed)

- first: You need a doctor's certificate for more than 2 sick days in a row. [1]
- shown: You need a doctor's certificate for more than 2 sick days in a row. [1]
- caught: an independent reading found: The number of sick days for which a doctor's certificate is needed is not specified in the passage.

**How many days in advance must leave be requested?** (right → right, unconfirmed)

- first: For leave requests, the policy states that leave requests go through the HR portal at least 7 days in advance, except for sick leave. [1]

Note that there is no specific mention of the advance notice period for sick leave, only that it should be reported to the IT helpdesk within 24 hours if lost or
- shown: According to the employee handbook, leave requests go through the HR portal at least 7 days in advance, except sick leave. [1]

However, the passage does not specify the advance notice period for sick leave.
- caught: an independent reading found: The answer does not answer the question that was asked (while the passages do). The answer should state the number of days in advance that leave must be requested, not just mention that the policy states so.

**What is the notice period for a manager after confirmation?** (right → right, unconfirmed)

- first: According to the employee handbook, the notice period for managers and above after confirmation is 90 days. [1]
- shown: The notice period for managers and above after confirmation is 90 days. [1]
- caught: an independent reading found: The notice period for managers and above after confirmation is 90 days, but the passage only states that it is 90 days for managers and above, without specifying the number of days.

**How long is probation, and what is the notice period during probation?** (right → wrong, fixed)

- first: New employees are on probation for 6 months. [1]
During probation, the notice period is 15 days. [1]
- shown: Here is the corrected answer:

New employees are on probation for 6 months. [1]
During probation, the notice period is 60 days for staff and 90 days for managers and above. [1]
- caught: an independent reading found: The notice period during probation is not 15 days, it is 60 days for staff and 90 days for managers and above.

**How many days a week can I work from home?** (right → right, unconfirmed)

- first: According to the employee handbook, employees may work from home up to 2 days a week with their manager's approval. [1]
- shown: According to the employee handbook, employees may work from home up to 2 days a week with their manager's approval. [1]
- caught: an independent reading found: The number of days a week that can be worked from home is not clearly stated as 2, but rather 'up to 2 days a week' which implies flexibility.

**Who approves an expense of 20,000 rupees?** (right → wrong, unconfirmed)

- first: According to the travel-and-expense-policy.md, expenses from ₹5,001 to ₹50,000 need the department head's approval. Since ₹20,000 falls within this range, the department head's approval is required. [1]
- shown: According to the travel-and-expense-policy.md, expenses above ₹50,000 need approval from the Director of Finance. Since ₹20,000 falls above ₹50,000, the Director of Finance's approval is required. [1][3]
- caught: an independent reading found: The department head's approval is required for expenses from ₹5,001 to ₹50,000, but the answer does not mention the Director of Finance's approval for expenses above ₹50,000.

**Up to what amount can my manager approve an expense?** (right → wrong, unconfirmed)

- first: According to the travel and expense policy, expenses from ₹5,001 to ₹50,000 need the department head's approval, but the passage does not specify the exact amount that the department head can approve. However, it does state that expenses up to ₹5,000 are approved by the employee's manager, and expen
- shown: According to the travel and expense policy, expenses from ₹5,001 to ₹50,000 need the department head's approval. However, the passage does not specify the exact amount that the department head can approve.
- caught: an independent reading found: The answer does not answer the question that was asked (while the passages do). The department head's approval limit is not specified in the passages.

**Within how many days must an expense claim be submitted?** (right → right, unconfirmed)

- first: According to passage [1], an expense claim must be submitted within 30 days of the expense.
- shown: According to passage [1], an expense claim must be submitted within 30 days of the expense. [1]
- caught: an independent reading found: The answer states 30 days, but the passage states 'within 30 days of the expense', which implies the submission deadline is after the expense date, not on the same day.
- caught: "However, the passage does not explicitly state the submission deadline as a date, but rather a timef" does not match what the passages say

**Above what amount is an original receipt needed?** (right → right, unconfirmed)

- first: An original receipt is needed for every expense above ₹500. [1]
- shown: According to passage [1], an original receipt is required for every expense above ₹500. [1]
- caught: an independent reading found: The answer does not answer the QUESTION that was asked (while the passages do). The answer should be above ₹500, but the passage only mentions that an original receipt is needed for every expense above ₹500, not just above ₹500.

**What is the daily allowance for travel in Pune?** (wrong → wrong, unconfirmed)

- first: The daily allowance for travel in Pune is ₹1,000 per day. [1]
- shown: The daily allowance for travel in Pune is ₹1,000 per day. [1]
- caught: an independent reading found: The daily allowance for travel in Pune is ₹1,000 per day.

**What is the daily allowance for travel in Indore?** (right → said so, not_found)

- first: The daily allowance for travel in Indore is ₹1,000 per day. [1]
- shown: I couldn't find this in your company's documents. The closest passages are listed below; they may not answer it.

**What is the hotel limit per night in a metro city for a manager?** (right → right, unconfirmed)

- first: The hotel limit per night in a metro city for a manager is ₹9,000. [1]
- shown: The hotel limit per night in a metro city for a manager is ₹9,000. [1]
- caught: an independent reading found: The hotel limit per night in a metro city for a manager is ₹9,000.

**What is the hotel limit per night in a non-metro city?** (right → said so, not_found)

- first: The hotel limit per night in a non-metro city is ₹4,500. [1]
- shown: I couldn't find this in your company's documents. The closest passages are listed below; they may not answer it.

**What train class can employees book?** (right → right, unconfirmed)

- first: Employees can book AC 2-tier trains. [1]
- shown: According to the travel and expense policy, employees can book AC 2-tier trains, but the passage does not explicitly state that it is the only option available. [1]

The passage only mentions that AC 2-tier is mentioned as an option for all employees, but it does not rule out other train classes.
- caught: an independent reading found: The train class employees can book is not specified as AC 2-tier for all employees, but only 'AC 2-tier for all employees' is mentioned, implying that other train classes may also be available.

**What is the price of the cordless drill, excluding GST?** (right → right, unconfirmed)

- first: The price of the cordless drill, excluding GST, is 6,499 INR. [1]
- shown: The price of the cordless drill, excluding GST, is 6,499 INR [1].
- caught: an independent reading found: The price of the cordless drill, excluding GST, is 6,499 INR.

**What is the GST rate on the vernier caliper?** (right → said so, not_found)

- first: The GST rate on the vernier caliper is 12%. [2]
- shown: I couldn't give an answer I could check against your documents. The passages that come closest are listed below.
- caught: [2] is not one of the passages (there are 1)
- caught: "The GST rate on the vernier caliper is 12%. [2]" does not match what passage [2] say
- caught: an independent reading found: The GST rate on the vernier caliper is 12%.

**Within how many days can an unused product be returned?** (right → right, unconfirmed)

- first: Unused products in their original packaging can be returned within 15 days of delivery for a full refund. [1]

Refunds reach the original payment method within 7 working days. [1]
- shown: According to the passage [1], an unused product can be returned within 15 days of delivery for a full refund.
- caught: an independent reading found: The time frame for returning an unused product is not specified as a range (e.g. 15 days or less), but rather as a specific number of days (15 days)

**What are the phone support hours?** (right → right, unconfirmed)

- first: The phone support hours are Monday to Saturday, 9:00 AM to 7:00 PM. [1]
- shown: The phone support hours are Monday to Saturday, 9:00 AM to 7:00 PM. [1]
- caught: an independent reading found: Incorrect day of the week for Saturday (should be 'Saturday' instead of 'Monday to Saturday')
- caught: an independent reading found: Incorrect end time for phone support hours (should be '7:00 PM' instead of '7:00 AM')
