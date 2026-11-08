# Cascade Guard 1.5.0: the company assistant

**An AI assistant for your company's own documents that checks every answer before anyone sees it.** Upload
policies, handbooks, price lists and FAQs (PDF, Word, text, CSV, HTML) and ask questions. Answers cite the passages
they used. Cascade Guard then checks every amount, number, date and quotation against the passage cited. A wrong
one goes back to the AI with the reason, and what still fails is removed and listed. An independent AI reading
flags anything else it doubts. If the documents don't say, the assistant says so instead of guessing.

- **Private:** `docker compose up` runs a local AI (Ollama) next to it, so nothing leaves your server. Any cloud AI
  works too (`CASCADE_AI`).
- **Use it anywhere:** the `/chat` page (desktop and phone), any OpenAI-compatible app (`/v1/chat/completions`,
  streaming included; tested with the official `openai` package), or the command line (`cascade-guard chat`).
- **Run it as a business:**
  - workspaces, one per company, and keys per person (member, manager, owner);
  - plans with monthly limits, usage, and the `/admin` page;
  - Stripe and Razorpay payments that issue and revoke keys by themselves, and a `/pricing` page.

  See DEPLOY.md.

**Tested with real local AIs** (30 questions; GitHub's machines; Ollama):

| AI | Right: plain → checked | Wrong, shown as if right | Wrong, shown with a warning |
|---|---|---|---|
| Qwen 2.5 7B | 25 → 25 of 26 | 0 → 0 | 0 |
| Qwen 2.5 3B | 22 → 22 of 26 | 3 → 0 | 3 |
| Llama 3.2 3B | 25 → 25 of 26 | 1 → 0 | 1 |

None of them made up an answer to the 4 questions the documents don't answer. With 3B models, the AI reading also
warns about many right answers: use 7B or larger, or set `CHAT_READER_AI` to a stronger AI. The full results are in
the README.

Get it: `pip install --upgrade cascade-guard-ai`, or `git clone` and `docker compose up -d`. Full list: CHANGELOG.md
