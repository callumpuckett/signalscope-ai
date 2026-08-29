# Production smoke-test gate

StockRadar deployments are not successful until both `/healthz` and the browser smoke gate pass. The smoke test is read-only: it does not log in, purchase Premium, submit newsletter signup, modify portfolios, or send Beehiiv campaigns.

## Setup and commands

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m playwright install chromium
python3 scripts/production_smoke_test.py --self-test
```

Run against a local server:

```bash
STOCKRADAR_SMOKE_BASE_URL=http://127.0.0.1:8080 python3 scripts/production_smoke_test.py
```

Run after Render reports the production deployment is **Live**:

```bash
python3 scripts/production_smoke_test.py --base-url https://www.stockradarhq.com
```

The same production command can be run from GitHub Actions with **Actions → StockRadar production smoke gate → Run workflow**. Leave the default production URL unless an explicitly approved deployment uses another public base URL.

The gate verifies the homepage, pointer interaction, approved navigation, MSFT/Microsoft/AAPL search, Microsoft identity/logo/chart, Login, Premium, Investment Compass, Newsletter, Turnstile, `/newsletter/latest`, `/newsletter/rss`, desktop, 390×844 mobile, browser console errors, and failed critical requests. `--self-test` proves that a page with a full-screen pointer-blocking overlay fails even if its health endpoint would be 200.

## Release and rollback rule

Before a production change, record the active Render commit, database provider, and intended rollback commit/configuration. After every Render deployment:

1. Wait for Render to report **Live**.
2. Verify `/healthz` returns 200.
3. Run the production smoke command above.
4. Declare success only when the command exits 0.

Any failed route, unusable search/navigation control, blocking overlay, missing Microsoft logo/chart, unexpected critical 4xx/5xx, stalled critical request, redirect loop, or blocking JavaScript error is release-blocking. Stop further production work. Repair immediately if the cause is known and low-risk; otherwise restore the recorded last-known-good commit/configuration and rerun the smoke gate.
