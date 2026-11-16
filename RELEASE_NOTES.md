# Cascade Guard 1.7.0: a complete web app

Cascade Guard is an AI assistant that answers from a company's own documents and checks every answer before
anyone sees it. This release turns it into a web app a business can sign up for and run without touching code.

- **Accounts:** sign in with an email and password, or with the company's own login (single sign-on: Google,
  Microsoft Entra, Okta). Team members join by invite link. Members ask questions; managers run the workspace.
- **One dashboard (`/app`):**
  - an overview with setup steps and the last 30 days;
  - documents: upload files, or type a website address and it reads the pages;
  - a test chat that shows the full working;
  - conversations: every question, its answer and verdict, with search and CSV download;
  - the questions the documents could not answer, so you know what to add;
  - leads: when the chat can't answer, a visitor can leave a phone number or email;
  - the website chat widget: one line of code, with its look, greeting and allowed websites set in the dashboard;
  - team, API keys, plan, settings, data export, workspace deletion and an activity log.
- **For selling it:** a home page with a live demo, free sign-up, plans with monthly limits, Stripe and Razorpay
  payments that upgrade the workspace, and policy pages. See DEPLOY.md, "Go live as a paid service".
- **For a company's own server:** `docker compose up` runs it with a local AI, so nothing leaves the network.
  A `/security` page lists what the software does to protect data.

Tested on SQLite and PostgreSQL, and in a browser on desktop and phone as an owner, a team member and a website
visitor. Earlier results with real AI models (receipts and company questions) are in the README.

Get it: `pip install --upgrade cascade-guard-ai`, or `git clone` and `docker compose up -d`. Full list: CHANGELOG.md
