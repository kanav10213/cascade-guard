# Cascade Guard 1.2.0

New: **ask Cascade Guard in plain English.**

Open `/assistant` on your server (or run `cascade-guard ask`) and type what you want checked:

- "Check this invoice: 3 x $4.00, 2 x $1.50, total $16.00" → *The total is wrong. It should be $15.00, but the bill
  says $16.00 ($1.00 too much).* Every line, the tax and the total are recomputed exactly and shown like a receipt,
  with the wrong figure struck through. Rupees, GST (also CGST + SGST), discounts, delivery and PDF invoices work.
- "Is 17 x 23 = 401?" or "the average of 3, 7, 2, 9 is 5.5" → checked exactly.
- "Run support ticket triage on: …" → your agent chain, with the guard on every step.
- "Show me Cascade Guard catching a mistake" → a sample invoice through four agents with planted mistakes:
  the wrong total that would have been delivered, and how the guard caught and fixed each mistake.

The built-in assistant is free and needs no AI model. With a model connected, the model understands any request,
and Cascade Guard checks the model's own answer before you see it.

Also new: **use Cascade Guard inside Claude.** Set `MCP_CONNECTOR_TOKEN` on your server and add the link shown under
"Use in Claude" as a custom connector in Claude; then ask Claude to check a bill and it uses Cascade Guard. For the
Claude desktop app, `cascade-guard mcp --install-claude` does the setup.

Earlier in 1.1: build your own agent chains without code, and plug in the agents you already have (INTEGRATION.md).

Get it: `pip install --upgrade cascade-guard-ai`. Full list: CHANGELOG.md
