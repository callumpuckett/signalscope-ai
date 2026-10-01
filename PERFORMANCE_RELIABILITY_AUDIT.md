# StockRadarHQ performance and reliability audit

Date: 1 October 2026. Changes are local and uncommitted; nothing was pushed or deployed. No production credentials, payments, newsletter submissions or production database modifications were used.

## Findings and implementation

1. **Login:** `login()` authenticates locally, checks the existing password/hash, clears the session, sets owner access and redirects. It does not fetch market data. The production before-request rate limiter can require a durable database transaction; this security operation was retained. The major code-level latency risk was the redirected homepage: an empty/expired dashboard cache synchronously fetched news, and explicit dashboard tabs additionally fetched six market snapshots. A global dashboard lock serialized that provider work and blocked other dashboard/news readers. These are demonstrated code bottlenecks, not a claim that production telemetry proved the cause of the reported incident.

2. **Navigation:** every explicit dashboard tab previously fetched Overview's six snapshots, even when their section was hidden. Signals, Impact Radar and Watchlist now omit those calls. Homepage, dashboard and market-news HTTP requests read available cache data immediately; a cold cache renders existing local recommendation/scoring data without market providers. A single background refresh is scheduled per process. Existing provider serialization and Yahoo deduplication remain intact. Other main routes do not call dashboard preparation.

3. **Cache reliability:** the existing synchronous refresh implementation remains the refresh worker. HTTP readers no longer wait on its network lock. Exceptions or structurally invalid refresh results retain last-good data and its original timestamp. Failed background refreshes retry after the existing dashboard TTL (default 300 seconds). Existing last-good headline merge and history preservation remain intact. Market status/time is calculated locally on each response, independently of cached news. An unavailable feed receives no invented current update timestamp.

4. **Cold Overview:** the existing six-card component is filled after the background refresh using a bounded same-route poll (5-second fetch deadline, at most 10 attempts). This only runs for an active Overview with missing snapshots. It does not prepare other tabs or add a new feature. If the provider remains unavailable, existing unavailable-data cards render. A later visit can retry after cooldown.

5. **Data wording:** stock/chart copy now describes price history and cached/delayed provider data instead of claiming a live chart or live stock page. The homepage report link no longer claims that the report is live. No calculations, scoring inputs or outputs were changed.

## Measurements

Controlled comparison uses the original `HEAD` cache function and the working-tree nonblocking cache reader, identical expired cached dashboard data, and a mocked provider that sleeps 200 ms. Five samples per implementation; no real provider timing is included.

| Measurement | Before | After |
| --- | ---: | ---: |
| Median dashboard cache response | 205.103 ms | 0.781 ms |
| Six snapshot calls on cold Signals/Radar/Watchlist preparation | 6 | 0 |
| Snapshot calls on homepage/news endpoint | 0 | 0 |
| Market-provider calls in login handler | 0 | 0 |
| Provider refresh attempts during held refresh plus four other route requests | 1 | 1 |
| Database calls removed | 0 | 0 |

The background refresh still takes the provider's time; the improvement is removal of that wait from navigation. These are not production end-to-end login measurements. Reproduce the controlled timing from the repository root with `python3 scripts/performance_audit_benchmark.py` before committing (its baseline is HEAD).

## Route and freshness review

| Surface/data | Opening/refresh work and outcome |
| --- | --- |
| Homepage, four dashboard tabs | Local CSV recommendations, existing calculations and cache state; market refresh is asynchronous. Snapshots requested only by Overview. |
| Market News | Existing news serialization and last-good headlines; dashboard/news TTL defaults to 300 seconds. Existing browser refresh interval is five minutes. |
| Login/logout | Existing session, credential, CSRF, origin and rate-limit controls. No market dependencies. |
| What-If | Initial form does not fetch prices; selecting a stock may retrieve its outlook. Existing cached/persisted validated pairs and hypothetical fallback remain. |
| Compare | Initial form does not prepare stock data; Premium pair submission loads the two selected contexts. Free teaser access remains unchanged. |
| Stock reports/charts | Selected range, lifetime history and income/outlook context only for the selected stock. History cache/retry is 300 seconds; metadata success cache is one hour, unavailable cache five minutes. Existing timeouts are retained. |
| Opportunities | Daily London-date snapshot and outlook refresh logic remains intact. Premium cold/due ranking/enrichment can still perform required synchronous provider/storage work. Free requests can read persisted outlook state. |
| Premium Watchlist/Portfolio Builder | Recommendation/local education work; portfolio computation occurs after submitted holdings. |
| Universe/search | Local CSV with five-minute cache and existing display lookup reuse. No remote universe download. |
| Investment Compass | Existing local form/profile calculations. |
| Upgrade/account/subscription/legal/help | Existing templates and entitlement checks. Payment and subscription operations were not exercised. |
| Newsletter | Existing artifact/last-good publication architecture retained. Signup protection configuration is required; signup was not submitted. |
| Market status | Local exchange-time calculation refreshed each HTTP response; existing partial holiday calendar unchanged. |

Dashboard expiry and history/metadata TTLs depend on request-time clocks, not deployment time. Tests move the clock to later days and verify subsequent refreshes. Opportunity snapshots already key by London date; validated outlook reuse retains its original quote/retrieval dates and existing seven-day eligibility limit. CSV recommendations are static unless an external producer updates their source: repeatedly reading the same CSV does not produce new signals. No scoring methodology was altered to disguise that constraint.

## Validation

- Focused performance, history/news resilience, security and Premium-readiness command: **65 passed** (`python3 -m pytest tests/test_site_performance_audit.py tests/test_performance_paths.py tests/test_market_resilience_corrections.py tests/test_security.py tests/test_premium_readiness.py -q`).
- Final full runnable test suite: **774 passed, 5 failed**, 8.34 seconds (`python3 -m pytest tests --ignore=tests/test_production_smoke_test.py -q`).
- All five full-suite failures reproduced against an isolated copy of original HEAD: refund support-email copy, feedback support-email copy, header breakpoint expectation, homepage copy expectation, and Opportunities public-preview storage-read expectation. These unrelated failures were left unchanged.
- Expanded focused run including first-time-experience, medium security hardening and emergency security remediation: **132 passed**, 4.26 seconds. New audit tests isolate local display/logo caches to avoid leaking rendered-route state into subsequent copy tests.
- Unrestricted pytest collection could not run the separate production-smoke script/tests because the installed Python runtime lacks Playwright. Browser-plugin checks were performed separately; no dependencies were installed.
- `git diff --check` and Python compilation passed.
- Real local browser checks at desktop (1440×900) and mobile (390×844): main dashboard tabs, returning to tabs, Opportunities, both watchlists, Compare, Portfolio Builder, Universe and Manage Subscription rendered. Mobile checks found no horizontal overflow on those routes. Desktop and mobile test login/logout succeeded; stock search/switching (MSFT/AAPL), What-If search/selection and MSFT/GOOGL comparison worked. Investment Compass and newsletter landing rendered. The local preview used disposable data, a test owner identity and mocked provider outages; production credentials were never entered. Newsletter landing correctly displayed its unavailable signup-protection state in this isolated environment.

## Remaining limits and hosting checks

Production incident attribution still requires request/redirect timings, worker queue time and Aiven latency metrics. The configured Procfile starts Gunicorn with its defaults; hosting worker count, cold starts and CPU/password-hash cost need measurement. Startup storage initialization/migration is synchronous and can delay app readiness during database problems.

The existing PostgreSQL adapter opens/closes a connection per operation with a five-second connection timeout. It has no existing pool; adding a new pooling dependency was deliberately avoided. Connect timeout does not bound SQL execution. Evaluate Aiven statement/lock timeout configuration, app/database region placement and a hosting-supported pooling endpoint before a separate database change. Authentication/rate-limit operations must not be bypassed or cached for speed.

Uncached stock histories/outlooks, requested Premium comparisons and cold/due Opportunities can still wait for their required provider calls. This patch does not promise instantaneous cold rendering of those data-heavy components. Existing Yahoo calls use bounded history timeouts and serialized refreshes; metadata/fallback paths and external service latency still need production observation. Dashboard refresh deduplication is per process, consistent with the existing memory caches; multiple Gunicorn workers/instances can still independently refresh. This patch adds no cross-instance guarantee.

Security/authentication behavior, Premium entitlements, subscription handling, financial calculations and score outputs remain unchanged. No site redesign, new user feature, unrelated legal-copy repair, commit, push or deployment was performed.

## Files changed

- `app.py`: nonblocking dashboard/news reads, serialized refresh scheduling, failure preservation/retry, Overview-only snapshot loading and completion, current local market status, truthful data wording.
- `tests/test_performance_paths.py`: update route expectations for asynchronous rendering and Overview-only snapshots; retain existing cache-completion and Yahoo concurrency coverage.
- `tests/test_site_performance_audit.py`: login provider independence, concurrent outage navigation, daily expiry, unrelated-route isolation, cold local rendering and invalid-refresh preservation.
- `tests/test_phase19_first_time_experience.py`: update the expected report sentence to match truthful wording.
- `scripts/performance_audit_benchmark.py`: reproducible controlled before/after cache measurement.
- `PERFORMANCE_RELIABILITY_AUDIT.md`: this report.
