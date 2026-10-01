# Login latency investigation after d6d172a

1 October 2026. This investigation separates observed public production behavior from controlled local measurements. **The reported six-second authenticated production delay has not been attributed to a specific server operation.** Production request logs and an authenticated browser waterfall were unavailable. No production login credentials were used. Nothing was committed, pushed or deployed.

## 1. Measured login POST

A local Flask-client reproduction uses the production durable rate-limit branch, the actual PostgreSQL storage adapter with a fake connector, and real Werkzeug test password-hash verification. The fake connector sleeps 30 ms, each SQL execution sleeps 5 ms, and commit sleeps 3 ms; observed durations include scheduler overhead. The database timings below are simulated, not Aiven measurements. Two requests were measured: one followed by a warm dashboard GET, one followed by a cold dashboard-cache GET.

| Login stage | Warm path (ms) | Cold dashboard path (ms) |
| --- | ---: | ---: |
| Complete POST through response/session generation | 102.344 | 102.068 |
| New PostgreSQL connection acquisition | 35.072 | 35.079 |
| Shared rate-limit advisory-lock SQL | 6.275 | 6.263 |
| State SELECT and UPSERT SQL combined | 12.540 | 12.530 |
| Database commit | 3.761 | 3.766 |
| Configured owner identity comparison | 0.002 | 0.001 |
| Password hash verification | 43.623 | 43.764 |
| Session clear and owner flag creation | 0.015 | 0.009 |
| Signed-cookie session serialization/save | 0.141 | 0.092 |
| Redirect construction | 0.045 | 0.034 |
| Flask response construction | 0.002 | 0.002 |

Other small costs include request parsing, CSRF/security hooks, local failed-login limiter checks and response headers. There is **no database user lookup** in this handler: owner credentials are configured values, compared locally. There is **no database session write**: Flask signs the existing client-side session cookie. Production hash parameters and hosting CPU speed have not been measured.

The production POST makes one new connection for the existing shared rate-limit transaction: acquire advisory lock, read state, update state, commit and close. That work is security-related and was retained. Login does not perform Premium or market-data lookups.

## 2. Measured redirected homepage GET

| Homepage stage | Warm dashboard (ms) | Cold dashboard cache (ms) |
| --- | ---: | ---: |
| Complete GET through response/session generation | 21.122 | 20.959 |
| Session cookie loading/verification | 0.062 | 0.054 |
| Dashboard cache reader (inclusive) | 0.200 | 1.085 |
| Local dashboard preparation inside cold read | — | 0.961 |
| Template compilation/rendering (summed exclusive time) | 20.132 | 19.159 |
| Flask response construction | 0.043 | 0.034 |
| Session serialization/save | 0.102 | 0.086 |
| Database connections/queries | 0 / 0 | 0 / 0 |
| Synchronous market/provider calls | 0 | 0 |

These are owner-session requests. The cold dashboard fixture intentionally holds refresh pending; it is not a production-worker or DNS cold start. Lookup caches may already be warm. A separate fresh-process local preview measured module initialization at 509.732 ms with disposable filesystem storage, then a cold homepage response at 25.788 ms while its mocked refresh ran for approximately two seconds. The browser initial navigation returned in 85 ms. The background trace recorded 2005.125 ms in refresh; the initial request did not wait for it. Two completion polls took 2.488 ms and 1.101 ms and displayed the controlled test headline without a page reload.

A separate Premium customer homepage test used one entitlement connection and one SELECT, with no duplicate entitlement read from the header. The existing request-local navigation result reuse remains intact. A legacy Premium cookie can trigger Stripe revalidation if its persisted entitlement cannot be found; that existing security path now has a separate timing span. Owner access bypasses it.

## 3. What is proved about the remaining latency

The current source and local tests prove that successful owner login waits for the shared database rate-limit transaction and password verification, but not dashboard providers. Its redirect is `/`. The owner homepage does not query the database for a user or entitlement. Dashboard refresh uses a daemon thread with no join; a held refresh does not delay the response.

The local reproduction's largest POST costs are deliberately simulated database time and real test password verification; the largest local homepage cost is template work. **Neither result assigns the production six seconds.** The previous 205.1-to-0.8 ms benchmark measured a cache reader, not login-to-paint latency.

## 4. Production observations and Aiven/hosting limits

Read-only public requests from this machine:

| Request | HTTP | DNS reported by curl (s) | TLS completion, cumulative (s) | TTFB, cumulative (s) | Total (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `https://stockradarhq.com/`, following www redirect | 200 | 3.133630 | 3.245372 | 4.401320 | 4.423562 |
| `https://www.stockradarhq.com/` subsequently | 200 | 0.002840 | 0.058885 | 0.755728 | 0.776709 |
| Canonical `/login` GET | 200 | 0.002923 | 0.054881 | 0.282651 | 0.285403 |
| Canonical `/newsletter/latest` GET | 200 | 0.002630 | 0.052164 | 1.043305 | 1.045565 |

These include network/edge/hosting time and are not authenticated requests or browser paint timings. The first sample demonstrates a resolver delay on this machine; it does not prove the user's browser or Aiven had the same delay. The in-app browser displayed the production homepage, including external, mostly lazy company-logo images. Their load durations were unavailable from the browser's restricted read-only interface; no browser Performance API or production HAR measurement is claimed.

The PostgreSQL adapter has no pool and opens/closes a connection per operation. Its existing connection timeout is configured as five seconds. Advisory lock/query/commit waits are not limited by that connection timeout. Actual Aiven connection latency, DNS/TLS breakdown, region placement and SQL lock contention are unmeasured. No existing pool was found to reuse and no new pooling dependency was added.

The Procfile is `gunicorn app:app`. Actual Render worker options, worker queue delay and restart behavior need production telemetry. Database schema initialization and legacy migration run synchronously during module startup; they are now separately instrumented. The local filesystem startup number does not predict production database startup. CPU competition with refresh work is possible in principle but was not demonstrated as the six-second cause.

## 5. Weekly unavailable sections

The [canonical production homepage](https://www.stockradarhq.com/) response did **not** contain the three reported weekly messages. They were present in the [published newsletter artifact](https://www.stockradarhq.com/newsletter/latest), titled Week 39 and marked **25 September 2026, 08:11 UTC**. Its market mood/pulse explicitly states verified movement prices were unavailable for comparison. This predates the performance commit.

Those strings belong to newsletter templates, not dashboard preparation. `/newsletter/latest` reads and serves finalized published HTML; it does not ask the dashboard cache for market data. A regression test demonstrates identical published HTML with warm and cold dashboard caches and forbids dashboard work in that route.

The newsletter's comparison code requires matching, available price points at both Friday cutoffs. Missing/unavailable price pairs produce no comparisons, so positive movers, negative movers and tracker lists can all be empty. Valid unchanged prices could produce empty positive/negative lists **but would still populate the tracker**. Consequently, all three empty sections describe insufficient verified comparison data, not proof that the market had no winners or losers.

The public artifact does not disclose whether its generation lacked a previous cutoff, a current cutoff, or failed provider validation. Production snapshot metadata/provider errors would be needed to distinguish those historical causes. No production data was regenerated or fabricated, and the published artifact was not rewritten.

A separate cold-dashboard problem was reproduced: it could label an in-flight first news refresh as unavailable and wait the normal five minutes to look again. That behavior is fixed locally, but it cannot explain the published weekly artifact.

## 6. Small safe local fixes and instrumentation

- A cold empty news feed explicitly says it is refreshing while work is pending. Its existing API returns a boolean pending flag. The page checks for completion after one second, then every three seconds for at most ten attempts, with a five-second deadline per cold fetch. The check starts after the DOM exists, independently of the window load event. Normal five-minute refresh behavior remains.
- Cached valid headlines remain visible. Existing failure/last-good timestamp preservation remains. A failed or empty Overview snapshot refresh now also preserves previously valid snapshot cards instead of replacing them with unavailable placeholders. No prices are inferred.
- Opt-in instrumentation uses `STOCKRADAR_TIMING_ENABLED=true`, disabled by default. It logs structured `stockradar_timing` records for login, homepage, market-news and published-newsletter requests; startup; and dashboard background refresh. A random `X-StockRadar-Trace` response header correlates request logs with the browser waterfall.
- Timed stages cover signed-cookie open/save, security hooks, database acquisition, advisory lock, SQL execution/fetch, commit/close, configured credential comparison, password verification, session creation, entitlement/legacy Stripe checks, cache reading, local preparation, template work and response construction. Counters identify connection/query counts and the fixed application stores being accessed.
- Logs contain no SQL, query arguments, query strings, request bodies, emails, passwords, hashes, cookie/session contents, DSNs, hostnames or exception messages. Timing scopes use fixed route labels. Startup logs include module initialization and database/migration durations; request logs include PID and timing-module age. With preloaded workers, module age is not OS process age; correlate PIDs and startup/Render logs.
- Authentication, cookie protections, entitlement decisions, database transaction/lock semantics, financial calculations and provider architecture are unchanged. There is no authentication decision cache.

## 7. Expected impact and next production measurement

The substantiated presentation fixes prevent a pending refresh from masquerading as failed data and avoid leaving the initial feed empty until the five-minute timer. They preserve actual last-good data. They do **not** establish a numerical improvement to production login latency.

After review, deploy the instrumentation and enable its environment flag, then repeat a warm login and one first request to a newly started worker. The user should enter credentials privately. Record only method, path without query, status, redirect destination, request/TTFB/download durations and the two random response trace IDs for POST and GET. Do not share an unredacted HAR, Cookie/Set-Cookie headers, POST body or credentials.

Match those IDs to the Render timing records. Use `ms` for inclusive stage timing and `self_ms` to avoid double-counting nested stages. A large `db.connect` identifies connection establishment; a large `db.advisory_lock` identifies rate-limit transaction lock wait; large query/commit spans identify database work; large password or template spans identify CPU work. Provider spans in the request scope reveal unexpected synchronous data work; separate background records prevent counting background refresh as request time.

Compare WSGI response duration with browser TTFB and complete navigation/asset timings. Time before WSGI entry and after response generation is excluded from the server metric: DNS/TLS, Render queue/cold-start time, socket transfer, assets and paint must be evaluated separately. Use the startup schema/migration record to determine whether database readiness delayed the worker. Avoid assuming every gap is a hosting cold start.

Only then choose a latency fix. Pooling/region/timeouts should be considered if acquisition/SQL measurements demonstrate a database bottleneck; worker settings if queue/startup evidence demonstrates one; template caching if measured template cost dominates. None of those speculative changes was made now.

## 8. Files and validation

Changed locally:

- `app.py`: timing spans, existing session/WSGI delegation, cold-feed state/completion and last-good snapshot preservation.
- `newsletter_storage.py`: timed PostgreSQL boundaries and fixed-store/query counters.
- `performance_timing.py`: opt-in context-local telemetry and transparent connection/cursor/session delegates.
- `tests/test_login_latency_trace.py`: controlled warm/cold request breakdown, disabled behavior, secret redaction, failures, nested accounting and one-read Premium behavior.
- `tests/test_site_performance_audit.py`: pending-feed and snapshot preservation tests, published newsletter independence.
- `LOGIN_LATENCY_INVESTIGATION.md`: this report and production measurement procedure.

Validation: focused tests **145 passed**; security/PostgreSQL/Premium tests with instrumentation explicitly enabled **101 passed**; final full runnable suite **784 passed, the same five pre-existing failures**, 9.20 seconds. No existing failure was repaired. The production-smoke test module remains excluded because its Python Playwright dependency is unavailable. Python compilation, diff whitespace checks and parsing both rendered inline homepage scripts passed. A local browser confirmed pending-to-ready feed completion with no console errors. No commit, push or deployment was performed.
