# Company chat with a real AI: qwen2.5:7b

30 questions about the sample company: 26 the documents answer, 4 they don't.

| | The AI's first answer (no guard) | What Cascade Guard shows |
|---|---:|---:|
| Right (all expected facts) | 25 of 26 | 25 of 26 (1 of them with a warning) |
| **Wrong answer shown as if right** | **0** | **0** |
| Wrong answer shown with a warning | – | 0 |
| Said "not in the documents" although they answer it | 1 | 1 |
| **Made-up answer** (the documents don't say) | **0** of 4 | **0** of 4 |
| Said "not in the documents" (right) | 4 | 4 |

AI calls per question: 1.7 · seconds per question: 20.2 · verdicts: checked 24, not_found 5, unconfirmed 1

## Answers that changed

**How many sick days do we get per year?** (right → right, unconfirmed)

- first: [1] Every permanent employee gets 8 days of sick leave per year. A doctor's certificate is needed for more than 2 sick days in a row.
- shown: [1] Every permanent employee gets 8 days of sick leave per year. A doctor's certificate is needed for more than 2 sick days in a row.
