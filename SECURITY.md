# Security policy

## Supported versions
| Version | Supported |
|---|---|
| 1.0.x | yes |

## Reporting a vulnerability
Please do not open a public issue. Use GitHub's private vulnerability reporting ("Report a vulnerability" on the
repository's Security tab: https://github.com/kanav10213/cascade-guard/security). Include what you found, how to reproduce it, and its impact.
You will get an answer within 7 days; fixes for confirmed issues are released as soon as they are ready, with credit
if you want it.

## Running it safely
- Set `API_KEY` (and `REVIEWER_KEY` / `VIEWER_KEY` for others) before any other computer can reach the server, and
  before putting it behind a proxy or tunnel. Without keys it answers only the computer it runs on, addressed as
  localhost; a tunnel that hides where requests come from (an SSH port forward) defeats that, so set keys first.
- Serve it over HTTPS through a reverse proxy (Caddy, nginx, a cloud load balancer) with keys set and
  `cascade-guard serve --proxy-headers`; it does not terminate TLS itself.
- Keep `.env` out of version control (it is in `.gitignore`); use long random keys (`python -c "import secrets;
  print(secrets.token_urlsafe(32))"`).
- Text checked with a cloud model is sent to that provider. For data that must stay inside, use a local model.
- Treat review-queue items and model output as untrusted input in anything you build on top.
