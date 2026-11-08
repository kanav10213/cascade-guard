import os

# Tests must not pick up a developer's real .env (API keys, API_KEY, provider).
os.environ["CASCADE_NO_DOTENV"] = "1"
for _k in ("API_KEY", "REVIEWER_KEY", "VIEWER_KEY", "LLM_PROVIDER", "LLM_MODEL", "LLM_PRICE_IN", "LLM_PRICE_OUT",
           "ALLOW_OPEN_NETWORK", "CASCADE_HOME", "HOST", "PORT", "CASCADE_AI", "COMPANY_NAME", "CHAT_DOCS_ONLY",
           "CHAT_SECOND_CHECK", "CHAT_API_FOOTER", "PLANS", "STRIPE_WEBHOOK_SECRET", "RAZORPAY_WEBHOOK_SECRET",
           "BILLING_PLANS", "BILLING_DEFAULT_PLAN", "PLAN_PRICES", "BILLING_LINKS"):
    os.environ.pop(_k, None)

# Tests never write the real database; storage tests install an in-memory one.
os.environ["CASCADE_DB"] = "off"
os.environ["ALLOWED_HOSTS"] = "testserver"  # the Host name Starlette's TestClient sends

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    from cascade_guard.ops import audit_limiter, limiter
    limiter.reset()
    audit_limiter.reset()
    yield
