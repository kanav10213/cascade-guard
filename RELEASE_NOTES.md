# Cascade Guard 1.3.0

New: **one assistant for everyday life, that checks itself.**

Open `/assistant` on your server (or run `cascade-guard ask`) and ask:

- "I bought a shirt for ₹1,500 at 20% off, they charged ₹1,250" → *Wrong: 20% off ₹1,500.00 is ₹1,200.00, not
  ₹1,250.00.*, with the working shown like a receipt.
- "Asha paid 600, Ravi paid 300 and I paid 0 for dinner" → *You pay Asha 300.00.*
- "EMI for 10 lakh at 10% for 3 years", "FD of 1 lakh at 7% for 5 years", "3 pm IST in New York", "5 feet 8 inches in
  cm", "SGPA with credits 4, 3, 3 and grades 9, 8, 10", "recipe for 4 needs 200 g flour, make it for 6", and more.

These are worked out exactly and need no AI model.

With an AI model connected (a **free** Groq key works), it also writes emails and messages, explains, translates and
answers questions, and **Cascade Guard checks every answer before you see it**: numbers against the exact tools, sums
recomputed, and an independent second pass for wrong or made-up facts. An answer that stays wrong is replaced or
clearly marked "not confirmed".

Free model setup: get a key at console.groq.com, then set `LLM_PROVIDER=auto`, `OPENAI_API_KEY=<your key>`,
`OPENAI_BASE_URL=https://api.groq.com/openai/v1`, `LLM_REASONING_EFFORT=low`, `LLM_PRICE_IN=0` and `LLM_PRICE_OUT=0`.

Also: `check_everyday` in the MCP server and the Claude connector, so Claude can use the calculators too.

Get it: `pip install --upgrade cascade-guard-ai`. Full list: CHANGELOG.md
