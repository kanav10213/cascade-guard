# Deploying the company assistant

There are three ways to run it. Pick the one that fits.

| | Who it's for | The AI | Data stays |
|---|---|---|---|
| [A. Your own server, local AI](#a-your-own-server-with-a-local-ai) | a company that wants nothing to leave its building | Ollama on the same server | on your server |
| [B. Your own server, cloud AI](#b-your-own-server-with-a-cloud-ai) | a company without a GPU that is fine with a cloud AI | Gemini, Groq, OpenAI, Claude, ... | documents on your server; the passages for each question go to the AI provider |
| [C. Selling it as a service](#c-selling-it-as-a-service) | you, hosting it for many paying customers | either | your server; each customer in their own workspace |

Every answer is checked against the company's documents, whichever AI writes it.

**Want to start earning from it?** Go to [Go live as a paid service](#go-live-as-a-paid-service).

---

## Go live as a paid service

The public service is a home page with free sign-up, a chat widget your customers paste into their websites, and
paid plans. It is built in. These steps turn it on for real customers. Do them in order.

| Step | What you do | Time | Cost |
|---|---|---|---|
| 1 | **A database that keeps data.** On Render, open your PostgreSQL database, copy its **Internal Database URL**, and add it to the web service's Environment as `CASCADE_DB`. (A free Render database lasts 30 days; after that it costs about $6 a month. [neon.tech](https://neon.tech) has a free one that doesn't expire: use its connection string instead.) Without this, the free Render disk forgets every customer when the server restarts. | 5 min | free to start |
| 2 | **An AI.** Add a free Groq key on Render as `OPENAI_API_KEY`. Without an AI the chat still works, but it quotes the matching lines instead of writing an answer. | 3 min | free |
| 3 | **Your details.** On Render set `CONTACT_EMAIL`, `BUSINESS_NAME` (your name or firm) and `BUSINESS_ADDRESS`. They appear on `/contact`, `/terms`, `/privacy` and `/refunds`. | 2 min | free |
| 4 | **Read the policy pages.** `/terms`, `/privacy` and `/refunds` are starting drafts, not legal advice. Read them and change what doesn't fit you (`cascade_guard/company/saas.py`). | 15 min | free |
| 5 | **Razorpay.** Open an account at razorpay.com and finish KYC (PAN and a bank account; an individual can register). Razorpay reviews your website, which is why steps 3 and 4 come first. | 1–3 days | free; about 2% per payment |
| 6 | **Payment links.** In Razorpay create one Payment Link (or subscription) per plan. Add the note `plan` = `starter` or `team`. Set the callback URL to `https://<your-site>/welcome`. Put the links on Render as `BILLING_LINKS={"starter": "https://rzp.io/...", "team": "https://rzp.io/..."}`. | 10 min | |
| 7 | **Webhook.** In Razorpay, Settings, Webhooks: add `https://<your-site>/billing/razorpay` with the events `payment_link.paid`, `subscription.charged` and `subscription.cancelled`. Put its secret on Render as `RAZORPAY_WEBHOOK_SECRET`. | 5 min | |
| 8 | **Test it.** Sign up on your own site with a second email, add a document, paste the widget into a test page, and pay for the cheapest plan yourself. Your workspace should move to that plan. | 15 min | one small payment |

**You can start earning before Razorpay is approved.** Take payment directly (UPI, bank transfer), then open
`/admin` with your `API_KEY`, find the company under **Customers**, and choose its plan. The same page makes a
password-reset link for a customer who is locked out. From the command line: `cascade-guard customers`,
`cascade-guard customers plan WORKSPACE starter`, `cascade-guard customers reset-link EMAIL`.

Useful to know:

- **Prices** are `PLAN_PRICES` and **limits** are `PLANS`. Change them on Render at any time.
- **Upgrades are matched by email.** A customer who pays with the email they signed up with is upgraded
  automatically. If they cancel, their workspace goes back to the free plan and keeps its documents.
- **A plan is per workspace.** The website widget, staff keys and API calls all count towards the same monthly
  answers.
- **Behind a proxy, tell the server how to find each visitor's address**, or everyone shares one set of limits
  (requests a minute, sign-in attempts, sign-ups a day). On Render: `CLIENT_IP_HEADER=cf-connecting-ip,x-forwarded-for`
  and `TRUSTED_PROXY_HOPS=2`. Behind your own Caddy or nginx: `CLIENT_IP_HEADER=x-forwarded-for` and
  `TRUSTED_PROXY_HOPS=1`.
- **Sign-up has no email verification yet**, and it is limited to 3 per address per day and `SIGNUPS_PER_DAY` in
  total.
- **Email:** set `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` and `SMTP_FROM` (any mail provider) so invitations and
  password resets are emailed. Without it, a manager copies the link and sends it.
- **Company login:** set `OIDC_ISSUER`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` to add "Continue with Google"
  (or Microsoft, Okta). Register `https://<your-site>/auth/sso/callback` as the redirect URL.
- **The free Render server sleeps** after 15 minutes without visitors, and the first visit then takes about a
  minute. Move to a paid Render plan (about $7 a month) once you have paying customers. A custom domain can be added
  on Render too.
- **Income is not automatic.** The site takes sign-ups and payments by itself, but customers have to hear about it.
  The quickest first customers are usually local businesses you can show it to in person: coaching classes,
  clinics, shops with a website. Set it up for them with their own documents.

---

## A. Your own server with a local AI

You need a computer or server with [Docker](https://docs.docker.com/get-docker/). Roughly 8 GB of free memory is
enough for the default model on the CPU, and an NVIDIA GPU makes answers much faster.

```bash
git clone https://github.com/kanav10213/cascade-guard && cd cascade-guard
cp .env.example .env          # then set COMPANY_NAME, and API_KEY to a long random secret
docker compose up -d          # the first start downloads the AI model (about 4.7 GB)
```

Open **http://localhost:8000/chat** on that machine and enter your `API_KEY`. For sign-in with your company's own
login instead of keys, set the `OIDC_...` settings and `OIDC_WORKSPACE` in `.env`; people then use
`http://<server>:8000/login`, and managers get the dashboard at `/app`. Then:

1. **Add documents.** Use "Add documents" in the left panel: PDF, Word (.docx), text, Markdown, CSV and HTML.
   Scanned PDFs need OCR first. Try "Load the sample company" to see how it works.
2. **Give people keys.** Open `/admin`, then **Access keys**. Members can chat. Managers can also add documents
   and keys. Every key can have a monthly answer limit.
3. **Share it on your network.** In `docker-compose.yml`, change `"127.0.0.1:8000:8000"` to `"8000:8000"` and
   remove the `ALLOW_OPEN_NETWORK` line. Then run `docker compose up -d` again. People open
   `http://<server-address>:8000/chat`.
4. **Connect other apps.** Any app that works with OpenAI can point at `http://<server>:8000/v1` with one of your
   keys and the model `cascade-guard`, for example Open WebUI, LibreChat, AnythingLLM, or your own code using the
   `openai` package. See `/admin`, **Connect other apps**.

**Choosing a model.** Set `MODEL=` before `docker compose up -d`, or put it in `.env`. These sizes are rough:

| Model | Memory | On a CPU | Notes |
|---|---|---|---|
| `qwen2.5:3b` | ~3 GB | fastest | fine for short policy questions |
| `qwen2.5:7b` (default) | ~6 GB | about 10–40 s an answer | a good balance |
| `qwen2.5:14b` | ~10 GB | slow without a GPU | better reading of long documents |

An answer takes two to four AI calls: the answer itself, the independent reading, and a correction when something
is caught. `CHAT_SECOND_CHECK=off` saves one call, at the cost of the independent reading.

**With an NVIDIA GPU**, uncomment the `deploy:` block under `ollama` in `docker-compose.yml`. It needs the NVIDIA
Container Toolkit.

## B. Your own server with a cloud AI

This needs no GPU. Put the AI and its key in `.env`:

```bash
CASCADE_AI=gemini:gemini-2.5-flash
GEMINI_API_KEY=...
# or: CASCADE_AI=groq with GROQ_API_KEY, openai:<model> with OPENAI_API_KEY, claude with ANTHROPIC_API_KEY
```

Then use either of these:

- `docker compose up -d cascade-guard`. This still downloads the local model once, though it isn't used.
- Without Ollama at all: `docker build -t cascade-guard . && docker run -d -p 8000:8000 --env-file .env -v cascade-data:/data cascade-guard`.

Your documents stay on your server. For each question, only the few passages it needs go to the AI provider.

## C. Selling it as a service

You run one server for many customers. Each paying customer gets their own **workspace**, with their own
documents and usage. They also get a **manager key**, and with it they add their documents and their people's
keys themselves.

### 1. A server and a domain

- **Server:** a cloud server (VPS) with Docker. Use a cloud AI (B) unless you rent a GPU.
- **Database:** use **PostgreSQL** (`CASCADE_DB=postgresql://user:pass@host:5432/db`). This could be a managed
  Postgres, or a `postgres` service added to the compose file. SQLite works for one company. For many customers,
  Postgres is easier to back up.
- **HTTPS:** put [Caddy](https://caddyserver.com) in front. It gets the certificate by itself. This `Caddyfile` is
  all you need:
  ```
  assistant.yourdomain.com {
      reverse_proxy localhost:8000
  }
  ```
- **Render:** the free Render setup in this repository is fine for a demo. Its disk is wiped on every restart,
  which would delete keys and documents. Before you sell anything, point `CASCADE_DB` at a PostgreSQL database.

### 2. Plans and prices

The plans are monthly answer limits. Change them in `.env`:

```bash
PLANS={"free": 50, "starter": 1000, "team": 10000, "unlimited": 0}
PLAN_PRICES={"starter": "₹999 a month", "team": "₹4,999 a month"}
BILLING_LINKS={"starter": "https://rzp.io/l/...", "team": "https://rzp.io/l/..."}
```

`/pricing` shows these plans with their payment links. Send customers there.

### 3. Payments that make keys by themselves

**Razorpay** (India):

1. Create a Payment Link (or a subscription plan) for each plan. Add a note `plan` = `starter` (or map its id in
   `BILLING_PLANS`).
2. Set the link's callback URL to `https://assistant.yourdomain.com/welcome`.
3. Under Settings, Webhooks, add `https://assistant.yourdomain.com/billing/razorpay` with the events
   `payment_link.paid`, `subscription.charged` and `subscription.cancelled`. Put its secret in
   `RAZORPAY_WEBHOOK_SECRET`.

**Stripe:**

1. Create Payment Links. Set the success page to
   `https://assistant.yourdomain.com/welcome?ref={CHECKOUT_SESSION_ID}`.
2. Add the webhook `https://assistant.yourdomain.com/billing/stripe` with the events
   `checkout.session.completed` and `customer.subscription.deleted`. Put the signing secret in
   `STRIPE_WEBHOOK_SECRET`. Map each link id to a plan in `BILLING_PLANS`.

When a customer pays, the webhook makes a manager key in a new workspace named after their company. After paying,
they land on `/welcome`, where their key is shown **once**. When a subscription is cancelled, their keys stop
working. Webhook signatures are verified, and a repeated webhook does not make a second key.

### 4. Before you charge anyone

This is a short checklist, not legal advice. Ask a professional about your situation.

- **Terms of service and a privacy policy.** You will hold your customers' documents. In India, the Digital
  Personal Data Protection Act applies to personal data in them.
- **Payment gateway KYC, and GST registration** once your turnover requires it.
- **Backups:** `pg_dump` (or copy the SQLite file in the `cascade-data` volume) every day, kept off the server.
- **The licence:** the code is MIT-licensed, so anyone may use it. What customers pay you for is the hosting,
  the setup on their documents, support and custom work. Decide on the licence before you accept outside
  contributions, if you ever want to change it.

**Ways to earn from it:**

- Set it up for local companies, on their own server or on yours, for a setup fee plus a monthly support fee.
- Hosted plans through `/pricing`.
- Custom integrations through the OpenAI-format API.

---

## Running it

| | |
|---|---|
| Upgrade | `git pull && docker compose up -d --build` (the data lives in the volumes) |
| Logs | `docker compose logs -f cascade-guard` |
| Health | `GET /health` (open), `GET /health/ready` (database and AI) |
| Metrics | `GET /metrics` (Prometheus) |
| Usage | `/admin`, or `cascade-guard keys usage` |
| Keys from the command line | `docker compose exec cascade-guard cascade-guard keys create "Acme" --workspace acme --plan team --role manager` |

**Security:**

- Customer keys are stored only as SHA-256 hashes.
- A workspace sees only its own documents and usage.
- Customer keys cannot open the older admin routes.
- Every request is rate-limited and every write is in the audit log (`/audit`).
- Set `API_KEY` before the server can be reached from anywhere else. Without it, the server answers only its own
  computer.
- A key made by a payment is kept until the customer has seen it once on `/welcome`, then forgotten.
