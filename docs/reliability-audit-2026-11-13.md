# StockRadar reliability and freshness audit — 13 November 2026

This change addresses reproducible reliability, calculation, latency and freshness defects. It is not a certification of all financial information or billing operations. Deployment measurements and outstanding production limitations are recorded separately in the delivered audit report.

## Evidence and corrections

- Production started on `2d0e9ebd03d95b5cf07e0a8c912c85258cd0bd32`. Anonymous MSFT and AAPL requests returned a missing chart with the internal Yahoo cooldown message. Render logs showed repeated metadata `YFRateLimitError` events. An authenticated MSFT chart subsequently recovered on unchanged production, demonstrating an intermittent failure.
- Metadata quote-summary throttling disabled chart requests through a shared circuit breaker. Charts now use Yahoo's direct chart endpoint, avoiding cookie/quote-summary bootstrap, with a separate circuit breaker and refresh coordination. A chart 429 blocks subsequent chart requests for 300 seconds. No alternate-host retry is used to bypass throttling. `YAHOO_HISTORY_TRANSPORT=yfinance` retains the legacy adapter for operational rollback.
- The direct transport uses `query2.finance.yahoo.com`, the same primary base URL as the pinned yfinance history adapter. The initial direct `query1` rollout was throttled from Render and did not establish chart restoration; changing the primary transport host requires its own production verification, not a success assumption.
- Successful histories were process-only. Bounded atomic JSON history files now allow reuse after process cache loss on the same filesystem. Entries retain their original retrieval times, reject corrupt/future/over-30-day cache records, and are limited to 256 files. Render Free filesystems are ephemeral: this does not provide durability across deployments or instance replacement.
- Cache failures preserve validated histories, mark them stale and defer retries. Cold concurrent requests wait at most 50 ms for a refresh slot; previously validated history is served if available, otherwise an explicit refresh-pending explanation is returned.
- Metadata refresh is deduplicated in one background worker per process. Browser requests return available cached evidence or an explicit pending/unavailable state. Slow library metadata calls no longer block page requests. A failed metadata refresh cannot erase validated fundamentals or restart their freshness clock.
- The one-hour chart previously displayed a full trading day. Intraday histories now select the requested one-hour/24-hour window ending at the latest available trading bar. Closed markets do not invent bars.
- History selection rejects a different symbol's MultiIndex column. Direct chart parsing validates symbol, response size, timestamps, positive finite observations and adjusted-series length. Sub-cent values are retained rather than rounded into zero.
- Lifetime calculations now request adjusted history, exclude nonpositive values, sort/deduplicate observations and withhold results with an extreme adjacent adjustment discontinuity. Adjusted change is labelled with its coverage dates and is not represented as an investor-specific return. Large legitimate cumulative returns are not capped.
- Short chart histories could contaminate the annual dividend cache. Price-series income evidence now requires at least 330 days of coverage; stale histories are excluded. Short zero-payment samples cannot establish annual income absence. Income-cache retrieval dates remain tied to the underlying history.
- The homepage called fallback/default signals current. It now displays the existing source and observation-time limitations. Heuristic Decision Scores remain heuristic; no fabricated provider evidence was added.
- Warm Render dashboard traces showed approximately 1.1 seconds in repeated template compilation. A bounded LRU caches compiled Jinja code, while rendering each request's context and sessions afresh. Controlled local homepage measurements (30 samples): median 19.738 → 0.716 ms; p95 20.310 → 0.784 ms. These are CPU measurements, not production latency.
- Free Opportunities previews no longer initiate provider metadata requests. Persisted and in-memory validated outlooks remain available; unavailable figures are omitted rather than invented. Empty previews avoid an unnecessary storage read.
- Market status accounted for Christmas but omitted other holidays/early closes. Published 2026–2028 NYSE and LSE schedules now cover those dates. Unscheduled closures are outside a static calendar's capabilities.
- The feedback page ignored the configured support address. It now renders the configured address with template escaping.
- Read-only authenticated health diagnostics now include chart/metadata cooldowns, cache freshness counts, pending refreshes and scheduled-job check status. Public liveness responses remain minimal. Scheduled checks produce actionable status logs; a successful check is not a claim of email delivery.

## Refresh and fallback policy

| Data | Refresh opportunity | Failure behaviour |
| --- | --- | --- |
| Intraday history | 60 seconds, on demand | Preserve validated history, label stale; 300-second failure cooldown |
| Daily history / index and ETF snapshots | 300 seconds, on demand | Same; latest bar and retrieval timestamps shown |
| Lifetime adjusted history | 6 hours, on demand | Preserve history, label stale; reject invalid/discontinuous calculations |
| Metadata and fundamentals | Existing 1-hour TTL; 5 minutes when unavailable | Deduplicated background refresh; preserve original successful data/time |
| Analyst outlook | Existing daily refresh; quote eligibility limited to 7 days | Preserve eligible validated pairs; unavailable when evidence expires |
| Recommendations / derived scores | Existing 300-second cache; scores composed from available inputs | Preserve provenance; generated/default prompts are not current market observations |
| News | Existing 300-second dashboard refresh | Retain verified headlines and original update time on failure |
| Weekly newsletter | Existing Friday London schedule and startup catch-up | Published artifact retained; scheduler checks observable, delivery separate |

These are refresh opportunities, not provider service-level guarantees. Provider source/reporting periods and target publication times cannot be inferred from cache retrieval times. No data is described as guaranteed real-time.

## Validation and operational boundaries

The full automated suite covers authentication, CSRF, signatures, subscription/entitlement gates, rate limits, persistence, malformed data, timeouts, cooldown recovery, concurrent requests, chart ranges, provenance, newsletter behaviour and calculations. The dedicated PostgreSQL integration test requires an explicitly configured test database and is skipped when absent. Regression fixtures now prevent route-render tests from leaving real background network threads that contaminate later tests.

Genuine Yahoo responses were verified locally for all six MSFT ranges, AAPL, LLOY.L, SPY and the S&P 500. Connected Chrome checks covered ten pages at 320, 390, 768 and 1440 px without document overflow, plus Microsoft search and chart rendering. The standalone Playwright runner cannot launch a browser inside this desktop sandbox; connected-browser checks are recorded separately and are not represented as a passing standalone smoke gate.

Four production dependency pins were security-updated: Werkzeug 3.1.9, certifi 2026.10.18, Soup Sieve 4.0 and urllib3 2.8.0. The production lock was regenerated with hashes, preserves other package versions, and explicitly locks psycopg-binary. The full locked production dependencies were installed for the final tests, including Flask-WTF. The revised lock has no known vulnerabilities in the audit scan. Soup Sieve's major-version selector changes were reviewed against the upstream changelog and the application's parsing tests. Bandit findings require contextual review; fixed HTTPS URLs, escaped Markup, fixed SQL fragments, XML escaping and the development bind are not evidence of a newly exploitable vulnerability.

Remaining dependencies include Yahoo availability, unverified source reporting periods/analyst publication times, heuristic/default signal inputs, Render Free cold starts and stopped in-process schedulers during sleep, ephemeral history storage, network round trips for durable security state, and third-party email/billing delivery. This audit does not execute a live charge, cancel a subscription, or send a newsletter to real subscribers.

## References

- [NYSE published calendar](https://www.nyse.com/trade/hours-calendars)
- [LSE published calendar](https://www.londonstockexchange.com/en/resources/markets-products/business-days)
- [yfinance caching scope](https://ranaroussi.github.io/yfinance/advanced/caching.html)
- [Werkzeug changes](https://werkzeug.palletsprojects.com/en/stable/changes/)
- [urllib3 security changes](https://urllib3.readthedocs.io/en/stable/changelog.html)
- [Soup Sieve changes](https://facelessuser.github.io/soupsieve/about/changelog/)
