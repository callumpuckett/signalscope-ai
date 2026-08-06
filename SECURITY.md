# StockRadar security operations

## Required production configuration

Keep all values in Render environment variables. Never commit their values.

- `SIGNALSCOPE_SECRET_KEY`: at least 32 random characters.
- `STOCKRADAR_INTERNAL_SECRET`: a separate random secret accepted only in the `X-StockRadar-Internal-Secret` header for detailed diagnostics and internal forced refreshes.
- `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY`: Cloudflare Turnstile credentials.
- `TURNSTILE_EXPECTED_HOSTNAME`: `www.stockradarhq.com` in production.
- `TURNSTILE_EXPECTED_ACTION`: `newsletter_signup`.
- Existing Stripe, Beehiiv, owner and database variables remain required by their features.

The newsletter cron now accepts only `POST` with `X-Newsletter-Cron-Secret`; query-string secrets and `GET` triggers are rejected. Stripe webhooks remain CSRF-exempt and are authenticated by Stripe signatures.

## CSRF, rate limits and shared state

Flask-WTF provides production CSRF validation. The small compatibility fallback in `app.py` exists only so offline local environments can run; production installation is locked to Flask-WTF. Browser mutations require a session token. Stripe webhooks and the header-authenticated newsletter cron are the only exemptions.

Production rate-limit buckets and Turnstile replay hashes use the PostgreSQL-backed `stockradar_application_state` store. If durable storage fails, rate limiting falls back to a per-process in-memory limiter; this fallback is intentionally conservative but cannot coordinate multiple instances. Turnstile replay persistence fails closed in production when durable storage is unavailable.

## CSP rollout

The current application contains inline `<script>` blocks, inline `<style>` blocks, style attributes and inline event handlers. A strict enforced policy would therefore break navigation, charts and several interactive filters. The application sends a strict `Content-Security-Policy-Report-Only` policy without `unsafe-inline` or `unsafe-eval`.

Before enforcement:

1. Move inline JavaScript and CSS to versioned static files.
2. Replace inline event handlers with `addEventListener` bindings.
3. If small server-rendered blocks must remain, generate a per-request nonce and apply it only to those blocks.
4. Collect and review CSP reports in staging, including Turnstile and jsDelivr Chart.js resources.
5. Switch the same policy to `Content-Security-Policy` only after mobile, desktop, checkout, newsletter and chart regression checks have no violations.

HSTS is sent only for HTTPS production responses, without `includeSubDomains` or `preload` until every subdomain has been audited.

## Dependency updates and scanning

`requirements.in` contains reviewed direct production pins. `requirements.txt` locks direct and transitive production packages. Development scanners are isolated in `requirements-dev.txt`.

To update safely in a clean virtual environment:

1. Change only the intended package in `requirements.in`; major versions require explicit review.
2. Run `pip-compile --generate-hashes --resolver=backtracking requirements.in --output-file requirements.txt` on a networked trusted machine.
3. Regenerate the development lock from `requirements-dev.in` if tooling changed.
4. Run `python scripts/check_dependency_pins.py`, `pip-audit -r requirements.txt`, Bandit, secret scanning and the full test suite.
5. Review changelogs and the complete lock diff before committing.

Hashes were not generated in the August 2026 hardening change because the implementation environment had no package-index access. The workflow rejects unpinned or missing direct dependencies; hash generation is the next lock regeneration step on a trusted networked runner.
