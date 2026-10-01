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

## 9. Pooling-first implementation following production telemetry

The subsequent production `/api/market-news` trace measured 2,898.138 ms server response, including 2,896.630 ms durable rate limiting, 1,033.054 ms connection establishment, 1,356.161 ms across the two ordinary SQL executions, 336.933 ms in the advisory-lock execution and 168.062 ms commit. Dashboard cache reading took 0.088 ms. These identify a database-backed rate-limit bottleneck for that request. SQL spans include network transport and execution; the advisory span can also include lock contention and implicit transaction startup. They do not independently establish database CPU time or network RTT. A production login POST trace is still required: login uses the same limiter, but its duration has not been measured here.

The approved patch changes connection lifecycle only, with a fail-closed capacity safeguard:

- `postgres_connection_pool.py` provides a synchronous standard-library pool: maximum **two transaction connections per worker**, **zero eager connections**, **five seconds maximum waiting for capacity**, and the existing **five-second new-connection timeout**. Waiting followed by new establishment can therefore take up to approximately ten seconds, before SQL execution; this is not a SQL/transaction deadline. There are no pool background threads, eager prewarming or health-check SQL round trips.
- The application has one durable storage backend per process. Its normal PostgreSQL storage operations borrow a connection exclusively. Slot reservation happens before connecting outside the pool condition, so concurrent cold acquisitions cannot exceed the limit. Returned connections are reused by later requests or background storage operations in that same worker. Total transaction connection capacity is two times the worker count; existing dedicated newsletter send-lock connections remain additional to that bound.
- Successful transactions retain the original explicit commit. Failed transactions retain the original rollback. On pool return, rollback runs again defensively; psycopg sends no SQL for rollback when already idle. Reuse requires an open, non-broken connection and idle transaction status. Reset failures or unusable connections are discarded and closed. Connection/write failures are not automatically retried because commit outcome could be ambiguous.
- Known closed/broken idle connections are replaced before borrowing. An undetected network failure discovered during an operation follows the existing storage-error contract, and the broken connection is discarded on return; the next acquisition can create a replacement.
- `register_at_fork` resets the child pool's condition, idle list and capacity accounting before child threads start. A PID guard also prevents inherited leases from executing operations. Inherited idle socket objects are retained without protocol use/close in the child, because closing a shared inherited PostgreSQL socket there could affect the parent. They never enter the child's pool; child acquisitions establish independent connections. The master can retain its startup connections under preload; do not treat this as two connections shared among workers.
- Session-level newsletter advisory locks continue to use dedicated connections that are closed after release, including failed unlocks. They never enter the transaction pool. Existing transaction-level advisory locks and their whole-store read–modify–write sequence remain unchanged.
- Injected legacy test connectors retain their original lifecycle by default; pool-aware doubles explicitly enable pooling. The ordinary production driver path enables pooling without a new environment setting.
- Pool exhaustion is a distinct typed failure. For a durable rate-limit check it rejects with the existing 429 response shape and `Retry-After: 1`, allowing a later retry without consuming a bucket. It does **not** fall back to a fresh process-local counter. Ordinary threshold/window rejection outcomes are unchanged. Other database failures retain the previous outage handling. Other storage operations retain their existing unsuccessful-operation contract on exhaustion.

No database functions, SQL consolidation, UPSERT redesign, rate-limit policy changes, template optimizations, authentication/session/CSRF changes, entitlement decisions or financial calculation changes were introduced. No dependency was added: the pool uses the standard library and the existing production psycopg driver. No commit, push or deployment has been performed for this patch.

### Timing interpretation

The existing opt-in `STOCKRADAR_TIMING_ENABLED=true` flag enables all these fixed-label spans and counters:

| Marker | Meaning |
|---|---|
| `db.pool.acquire` | Total time obtaining a transaction connection, including waiting/new establishment if necessary |
| `db.pool.wait` | Time waiting because both slots were borrowed |
| `db.pool.created` | Count of successfully created pooled connections in this trace |
| `db.pool.reused` | Count of reused pooled connections in this trace |
| `db.connect`, `db.connections` | Actual new driver connection duration and attempt count, not warm pool acquisition |
| `db.pool.release`, `db.pool.reset` | Returning a connection and ensuring transaction cleanliness |
| `db.pool.discarded` | Connection removed from reuse |
| `db.pool.exhausted`, `security.rate_limit.pool_exhausted` | Capacity timeout and protective rate-limit rejection |

These are context-local to the request/startup/background operation that does the work, since connection creation is synchronous. A warm request can have `db.pool.reused: 1` and no `db.connect` span at all. A new worker's first request may already reuse a connection created during startup; use PID and startup traces to interpret it. Dedicated send-lock connections still report actual `db.connect` creation without pool acquisition. Inclusive stages nest: do not add `db.pool.acquire.ms` to its nested `db.connect.ms` when attributing total time.

Timing logs contain only fixed operation labels, counts, durations, error counts, status, PID, worker age and independently generated diagnostic correlation IDs. They contain no passwords, cookies, session IDs, authentication tokens, connection strings, SQL/parameters, user identities or exception text.

### Exact production verification procedure after review and authorized deployment

1. Keep Render worker count and other settings unchanged. Enable `STOCKRADAR_TIMING_ENABLED=true`. Do not change database credentials, limits or transaction policy. Compare the same canonical host, client and flow with the previous measurements.
2. In Render, filter records on `stockradar_timing`. Record PID and scope. Check startup's actual `db.connect` spans separately; startup may populate the pool before the first HTTP request.
3. Enter credentials privately and perform one login. Record `POST /login`, its redirect and the redirected homepage `GET /`, with their `X-StockRadar-Trace` diagnostic IDs. Match each ID to its request log. Also capture the following `/api/market-news` request. Do not share cookies, request bodies, credentials or unredacted HAR files.
4. For POST and API traces, compare `server_response_ms`, `security.durable_rate_limit`, `db.pool.acquire`, `db.pool.wait`, `db.connect`, pool counters, `db.advisory_lock`, `db.query` and `db.commit`. A normal accepted durable check must still execute **three explicit SQL calls** (lock, read, upsert) and commit. A denied threshold check omits the upsert as before.
5. Repeat the API call and another login within the existing limits, comparing requests handled by the **same PID**. Warm reuse should show `db.pool.reused`, no new `db.connections` attempt, and no `db.connect` span. A different PID or discarded socket can legitimately show a new connection. Do not issue bursts or alter security limits to force a result.
6. Observe a newly started worker when available. Match its startup/new-connection trace to later reuse; cold establishment and cold hosting time must not be confused with warm acquisition. Test deliberate pool exhaustion, broken connections and advisory-lock recovery in an isolated test database, not by interrupting production DB sessions.
7. Measure production **POST /login** before making any login-latency claim. Compare browser TTFB/navigation time to WSGI duration; queueing, DNS/TLS, assets and browser rendering remain outside WSGI timing. Do not optimize the already identified template cost in this patch.

**Estimated impact, not a measured result:** removing this sample's 1,033 ms connection establishment would reduce its roughly 2.9-second warm API response to approximately **1.87 seconds** if every other cost stayed unchanged. Cold establishment remains. Query, commit and whole-store lock contention remain, and improvement depends on worker reuse, pool demand and socket health. This patch does not establish that the six-second production login delay is fixed.

### Regression coverage and limitations

`tests/test_postgres_connection_pool.py` covers cold/warm acquisition, bounded parallel creation/exclusive borrowing, exhaustion/wakeup/recovery, fail-closed 429 on capacity exhaustion without local fallback, broken/closed/reset-failed connections, creation/commit failure, commit/rollback isolation, transaction advisory-lock release, dedicated session-lock cleanup, actual process forks with idle and active leases, two worker-like pools sharing durable rate-limit state, expiry/scope/429/Retry-After, login failure blocking, CSRF and unauthorized forced refresh. It exercises the real storage and rate-limit code with a transactional database double; it is not a live Aiven benchmark.

`tests/test_postgres_pool_integration.py` additionally provides an opt-in **real PostgreSQL** test for concurrent limiting across two pools, rollback, advisory-lock release and clean reuse. Set `STOCKRADAR_TEST_DATABASE_URL` only to an explicitly designated disposable test database with schema creation permission, install the existing psycopg production dependency, and run `python3 -m pytest tests/test_postgres_pool_integration.py -q`. The test creates and removes a unique isolated schema; it never reads the production `DATABASE_URL`. This integration test was skipped locally because no test PostgreSQL service was configured; the local interpreter also lacks psycopg. Actual psycopg/Aiven behaviour remains a production/test-service verification step, rather than a claimed local measurement.

Final patch validation:

- Focused pool/storage/login/security/Turnstile/performance tests with `STOCKRADAR_TIMING_ENABLED=true`: **167 passed, 1 skipped**, 5.18 seconds.
- Normal full command, `python3 -m pytest tests -q`: collection blocked by the existing missing Python `playwright` dependency in `tests/test_production_smoke_test.py`.
- Full runnable suite, `python3 -m pytest tests --ignore=tests/test_production_smoke_test.py -q`: **805 passed, 1 skipped, the same 5 pre-existing failures**, 9.28 seconds. The skip is the explicit test-database integration test described above.
- Unchanged failures: `test_refund_policy_uses_support_email_when_configured`, `test_feedback_uses_support_email_and_mail_subject`, `test_header_reserves_logo_column_and_mobile_overrides_dense_layout`, `test_homepage_explains_product_and_has_primary_calls_to_action`, and `test_opportunities_public_preview_metadata`. No unrelated fixes were made.
- Python compilation and `git diff --check` passed.

Patch files: `app.py` (capacity-failure rejection only), `newsletter_storage.py` (connection lifecycle), `postgres_connection_pool.py` (pool and opt-in timing spans), `tests/test_postgres_connection_pool.py`, `tests/test_postgres_pool_integration.py`, and this report. Existing `performance_timing.py` is unchanged; the new pool uses its existing opt-in timing API.
