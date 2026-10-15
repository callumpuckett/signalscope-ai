# Production timeout investigation

Date: 15 October 2026 (UTC timestamps below)
Deployed application: `488718656b970120725ee204dbbb205bedc4b3bf`.

## Confirmed request-path findings

The common blocking operation is the durable PostgreSQL rate-limit transaction,
executed by `security.endpoint_limit` before the stock/news route body. It is not
Premium report composition or the news provider fetch in the observed failures.

Evidence read directly from the Render application logs:

| UTC time | Evidence | Measured duration |
| --- | --- | --- |
| 21:52:48 | `/stock/MSFT` worker-abort stack: `consume_rate_limit` → `PostgresNewsletterStorage.update_state('rate_limits')` → `_load_state_with_cursor` → PostgreSQL cursor `execute` waiting in psycopg. Request recorded as HTTP 500 and worker exited. | Client had already exceeded its 30-second read deadline. |
| 21:53:05 | API trace `e3b00de2930d50ef` | 15,594.508ms request; 15,546.641ms durable limiter; 2,561.066ms DB connect; 12,400.382ms combined state-query stages; 382.687ms advisory lock; 43.676ms dashboard cache path. |
| 21:55:52 | API trace `7fbb86c4444036b3`, pooled connection reused | 4,384.683ms request; 4,382.450ms durable limiter; 3,874.913ms combined state-query stages; 0.353ms dashboard cache. |
| 21:57:56 | API trace `5a5e6235e882992e`, matched to this investigation's bounded HTTP sample | 2,200.508ms request; 2,199.435ms durable limiter; 0.079ms dashboard cache. |
| 22:01:06 | API trace `ccd075325da7f4cd`, no extra probe issued | 1,204.120ms request; 1,201.611ms durable limiter; 1.244ms dashboard cache. |

The database pool acquisition itself was 0.019–0.020ms on the warm traces, so pool
exhaustion is not supported by those samples. New-connection setup contributed to
one cold request, but reused connections also incurred slow state queries. The
shared advisory lock was roughly 334–383ms on these samples; it did not account for
the majority of the 15.59-second request.

The stock-abort stack had not reached stock providers, report rendering or Premium
entitlement checks. The public news API has no Premium entitlement check. In the
API traces, session verification was below 1ms and background news refresh occurred
separately. Yahoo rate-limit warnings exist for other stocks, but they do not explain
the observed database-blocked MSFT abort or the API's slow security hook.

## Scope and history

`/stock/*` and `/api/market-news` both run the same durable rate limiter. The health
route and direct Premium-preview route do not run that endpoint limiter, explaining
why they can remain responsive while these requests wait. Public health is a
liveness response and does not establish database responsiveness.

The rate-limit rules and database transaction/pool implementations are unchanged
between the pre-report base `fcf8af1` and the deployed report commit. Their most
recent storage/pool change is `944ed40`. This establishes that the blocking code path
predates the report; it does not establish the first historical occurrence of the
production incident.

A single bounded concurrent HTTP sample returned `/stock/KO` HTTP 200 in 4,992.1ms,
`/api/market-news` HTTP 200 in 2,791.7ms and `/health` HTTP 200 in 2,793.2ms. Client
elapsed time includes networking/queueing; the matching API server trace above is
2,200.508ms. Existing access logs also show other stock pages returning 200. The
symptoms are intermittent and the shared limiter makes other stock pages susceptible.

## Local diagnostic coverage

The existing middleware omitted `/stock/<symbol>` altogether. The local diagnostic
change adds a static `stock_detail` route label and route/history/lifetime/report
stages. State reads and writes gain separate stages instead of remaining combined
inside `db.query`. Existing timing enablement and random trace IDs are retained;
no ticker, query string, credential, SQL statement or payload is logged. When timing
is disabled, no trace header is added.

New tests reproduce a slow database security gate while stock data is supplied
locally, and distinguish that delay from route/provider/render work. They cover
MSFT, KO and SPY, the cache-only public news body, timing-disabled behaviour and
unchanged fail-closed handling of database pool exhaustion.

## Database-side confirmation

Aiven query statistics showed the application's normalized state SELECT at
2,541 calls, 35.2ms mean and 13,492.2ms maximum. Those statistics aggregate multiple
state keys and are not a per-request breakdown. The advisory-lock query's maximum
server execution time was 1.5ms, supporting the conclusion that lock contention is
not the dominant observed cost.

SELECT-only diagnostics in the existing `stockradar_production` database returned:

| Measurement | Result |
| --- | ---: |
| Stored rate-limit JSONB size | 118,216 bytes |
| Serialized JSON size read/written by the application | 173,515 bytes |
| Total buckets | 1,525 |
| Buckets older than the existing one-hour retention cutoff | 1,510 |
| Serialized size after a SELECT-only preview of the existing cleanup rule | 1,721 bytes |

The size/count query completed in the console in 0.110s; the expiry aggregate in
0.111s; the cleanup preview in 0.078s. None modified the production row. No counters,
identities, passwords or connection strings were retrieved into this report.

The application was retaining 99% obsolete buckets because cleanup ran only when
`len(buckets) > 5000`. Every allowed rate-limited request read and wrote this shared
JSON document. This is a confirmed, avoidable source of payload growth on the
already-confirmed blocking DB path. These observations do not prove that every
historical database latency spike has this sole cause.

## Small local correction

Remove only the 5,000-bucket trigger and run the **existing** expiration loop on each
update. The cutoff remains `now - max(window_seconds, 3600)`. Current, boundary and
recently expired buckets within that retention period stay intact. Active counts,
window reset rules, denial decisions, Retry-After and shared transaction locking are
unchanged. A denied request still does not commit a state update.

The first successful allowed update reclaims the obsolete entries; subsequent reads
and writes carry the smaller document. This uses the existing schema, pool, queries
and storage contract. No new store, SQL mutation, API, dependency, retry mechanism or
architecture is introduced. The local fallback counter uses the same unchanged
retention rule. Pool exhaustion still fails closed before route execution.

The correction does not change entitlement validation, market-provider requests,
cache TTLs, fallback selection, signal provenance, freshness or presentation.
No database settings, service plans, production counters or deployment settings
were changed. Render shell access was unavailable on the free compute plan;
Aiven's existing console provided the needed read-only metadata instead.

## Validation and timing limits

New pruning tests use 1,525 synthetic buckets with 1,510 beyond the retention cutoff.
They cover durable and local storage, retained counters, boundary values, denied
requests, window reset/Retry-After, longer windows and exact shared counts under
concurrent requests. New request-trace tests distinguish database/security time from
stock providers and rendering, verify static/redacted labels and trace-disabled
behaviour, and retain pool-exhaustion protection.

Focused regressions: **221 passed in 5.43s**, including the new pruning and timing
tests, PostgreSQL state/pool tests, existing timing, market/cache/fallback tests,
Premium Intelligence/provenance tests, authentication and security hardening.
`git diff --check` and Python compilation passed.

A controlled local JSON persistence benchmark compared the deployed function with
the local correction. It used synthetic production-shaped counts, five warmups and
20 measured calls per version, with no provider/database network access:

| Local JSON round-trip measurement | Before | After |
| --- | ---: | ---: |
| Median | 0.7234ms | 0.0127ms |
| P95 | 0.7471ms | 0.0130ms |
| Persisted buckets | 1,526 | 16 |
| Serialized bytes | 158,734 | 1,694 |

This measures local serialization/cleanup work, not production endpoint latency.
The production-data SELECT-only preview (173,515 → 1,721 bytes) is a preview of the
payload reduction, not evidence that production was modified.

One final bounded check of unchanged production returned `/stock/MSFT` HTTP 200 in
3,636.7ms at 22:14:36 UTC, preserving its Free preview and withholding restricted
report content. Together with KO/API samples, this establishes current successful
responses but does not establish that the intermittent incident is resolved.

There is **no post-fix production latency measurement**, because the correction is
local-only and has not been pushed or deployed. Faster later production samples are
recovery on unchanged code, not evidence of this fix. After review and any approved
deployment, verify state-size reduction, MSFT/API trace timings and worker health.

## Remaining limitations

- The confirmed bottleneck is the synchronous durable rate-limit state transaction;
  retained expired counters are a confirmed correctable contributor. Intermittent
  database/transport latency may still occur after reducing the payload.
- A shared database round trip remains on every rate-limited request. The existing
  database connection/query behaviour is unchanged; a genuinely unresponsive DB can
  still block. General deadline/retry or storage redesign is outside this correction.
- The first reclamation requires a successful database transaction. No manual
  production cleanup has been performed.
- The stock trace gap is repaired locally, not in production yet. Existing historical
  stock logs do not give a complete provider-versus-DB breakdown for successful calls.
- Free Render instances can incur platform cold-start delays separately. The observed
  worker-abort stack and API timings specifically establish in-request DB blocking;
  a liveness HTTP 200 alone is not a database performance guarantee.
- Yahoo rate limits remain handled by the existing cache/fallback logic. No new
  market data or freshness assumptions have been introduced.

## Files changed

- `app.py`: same expiry cleanup on every update; stock/news timing stages.
- `newsletter_storage.py`: separate read/write timing stages only.
- `performance_timing.py`: static, redacted stock-request trace coverage.
- `tests/test_rate_limit_pruning.py`: expiry/counter/concurrency regressions.
- `tests/test_stock_request_timing.py`: database-stage attribution and privacy tests.
- `docs/production-timeout-investigation.md`: evidence, correction and validation.

Commit locally only. No push or deployment is authorized for this correction.
