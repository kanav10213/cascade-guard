# Deploying the company assistant

There are three ways to run it. Pick the one that fits.

| | Who it's for | The AI | Data stays |
|---|---|---|---|
| [A. Your own server, local AI](#a-your-own-server-with-a-local-ai) | a company that wants nothing to leave its building | Ollama on the same server | on your server |
| [B. Your own server, cloud AI](#b-your-own-server-with-a-cloud-ai) | a company without a GPU that is fine with a cloud AI | Gemini, Groq, OpenAI, Claude, ... | documents on your server; the passages for each question go to the AI provider |
| [C. Selling it as a service](#c-selling-it-as-a-service) | you, hosting it for many paying customers | either | your server; each customer in their own workspace |

Every answer is checked against the company's documents, whichever AI writes it.

---

## A. Your own server with a local AI

You need a computer or server with [Docker](https://docs.docker.com/get-docker/). Roughly 8 GB of free memory is
enough for the default model on the CPU, and an NVIDIA GPU makes answers much faster.

```bash
git clone https://github.com/kanav10213/cascade-guard && cd cascade-guard
cp .env.example .env          # then set COMPANY_NAME, and API_KEY to a long random secret
docker compose up -d          # the first start downloads the AI model (about 4.7 GB)
```

Open **http://localhost:8000/chat** on that machine and enter your `API_KEY`. Then:

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
