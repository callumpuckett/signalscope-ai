# Weekly news diagnostic and visual polish — 21 September 2026

## Finding and evidence limit

**The historical cause of the Week 38 missing-news result is not established.**
No news retrieval, verification, scheduling, caching or persistence correction
has been made. A provider outage cannot honestly be inferred from the UI copy.

Read-only inspection of https://www.stockradarhq.com/newsletter/latest confirmed
Week 38, last updated **18 September 2026, 08:03 UTC**, with both reported
missing-news messages and 16 comparable market-price observations. This confirms
that a completed issue was published; it does not reveal the provider responses.

The checkout contains only local storage lock files, no saved issues or provider
logs. No production credentials are configured in this session. Production
`/health` details require authorized diagnostics access. `/news-health` measures
the separate live market-news path and cannot establish weekly-news failure.
The deployed commit has not been verified against this checkout.

A read-only replay on 21 September of the existing GDELT request for
11 September 08:00 UTC through 18 September 08:00 UTC timed out after 12 seconds,
with no HTTP response. The application timeout is 8 seconds. This is evidence of
failure to reach GDELT from the diagnostic environment today, **not evidence of
GDELT failure, HTTP throttling, or any particular response on 18 September**.
NewsAPI could not be replayed because its production key is not available.

## Existing path traced in app.py

1. **Window:** `newsletter_weekly_window` selects the latest completed Friday
   at 09:00 Europe/London, then subtracts seven local calendar days. Before Friday
   09:00 it uses the previous Friday. Local endpoints are converted separately to
   UTC, preserving DST changes. The observed generation time gives the expected
   half-open window **[2026-09-11 08:00 UTC, 2026-09-18 08:00 UTC)**. The fixed
   weekly cutoff constants drive this path; the older auto-send hour/minute
   environment settings do not override it.
2. **Requests:** `fetch_weekly_news_articles(limit=12)` requests up to 48 candidates
   per provider. NewsAPI `/v2/everything` is called only when `NEWSAPI_KEY` exists;
   parameters include the shared market query, English language, newest first,
   page size, and exact ISO start/end. GDELT `/api/v2/doc/doc` is always called,
   using the same query, `artlist`, JSON, 48 records, newest first and formatted
   UTC boundaries. Calls are sequential, each with an 8-second timeout, and have
   no weekly retry or pagination. NewsAPI non-OK statuses raise an error;
   transport/HTTP/JSON errors from either provider are caught per provider.
3. **Normalization and verification:** title, canonical HTTP(S) URL and parseable
   timestamp are required. NewsAPI `publishedAt` and GDELT `seendate` are supported.
   Naive timestamps are interpreted as UTC. The start-inclusive/end-exclusive
   window is enforced locally even if a provider ignores the request dates.
   Existing quality checks reject unsuitable language, sensational wording,
   excessive capitals, opinion pieces, untrusted publishers and irrelevant news.
   Accepted candidates are sorted by source tier, original-source status and time.
   Story identity/event checks deduplicate within the batch and against persisted
   story history; meaningful updates have their existing separate rule. At most
   12 are selected, then the issue builder rechecks relevance and includes three.
   This task does not change the existing verification methodology.
4. **Result and fallback:** selected stories give `verified`; errors from every
   attempted provider give `unavailable`; otherwise an empty selection gives
   `verified_no_stories`. All empty outcomes produce the same reader-facing
   missing-news fallback, including successful requests whose candidates were
   rejected. No live-feed or fabricated story fills the gap.
5. **Cache/storage:** there is no separate weekly raw-response cache. The issue
   builder first returns the persisted finalized issue, then considers the
   in-process issue cache. An empty-news issue is still finalized and persisted
   through the configured newsletter storage adapter, with its content fingerprint,
   article list and exclusion counts. `finalize_issue_once` preserves immutability;
   even `force_refresh=True` does not replace an existing finalized issue.
   Story usage is recorded after finalization. HTML/JSON/RSS artifacts are published
   through the configured artifact store. `/newsletter/latest` reads the validated
   published artifact and does not fetch new stories. Therefore an initially empty
   result remains visible for that edition even if providers recover later.
6. **Scheduling:** production startup catch-up, the default five-minute background
   loop, and the authenticated cron route can initiate generation. Storage readiness
   and generation locks gate it. Existing finalized issues are reused. Beehiiv
   delivery follows content generation and is separate from news retrieval.
   The observed 08:03 UTC generation is consistent with the 09:00 BST cutoff and
   poll interval; actual scheduler execution and production configuration remain
   unverified without logs.
7. **Diagnostics gap:** provider errors are returned to the builder and stored in
   process-local `LAST_NEWSLETTER_NEWS_STATUS`, but not persisted with the issue.
   Persisted exclusion counters help distinguish filtering, but the logged
   selection summary does not include provider errors or raw per-provider counts.
   The health field `last_news_provider_error` can be empty after restart or refer
   to a different run/worker. It is not a durable historical audit record.

## Cause assessment

| Candidate | Evidence / conclusion |
| --- | --- |
| Provider failure or throttling | Historical responses unavailable. Current GDELT replay timed out without HTTP status. No evidence of a 429. |
| Date window, Friday cutoff, timezone | Expected window reproduced; existing BST/GMT/DST/boundary tests pass. No defect identified in this checkout. |
| Verification/filtering/deduplication | Can produce exactly the same UI fallback. Affected issue's exclusion counters and candidates are needed. |
| Stale/empty cache | No weekly raw-response cache. Finalized empty results are intentionally reused; this explains persistence of the symptom, not its initial cause. |
| Persistence/artifact delivery | A completed issue is publicly served. No evidence that storage removed news; database/artifact comparison remains unavailable. |
| Scheduler timing | Public generation timestamp is after cutoff. No demonstrated timing defect. |

To finish the historical diagnosis, obtain the affected issue's saved metadata
(especially exclusion counts), its `draft.news_coverage_status`, whether NewsAPI
was configured, and generation/provider logs around **18 September 08:03 UTC**.
Do not share API key values. If responses/errors were not retained, the original
failure may not be recoverable; a future controlled run would need diagnostic
capture before any behavioral fix is justified. No new provider, dependency,
retry policy, verification relaxation or storage redesign is introduced here.

## Visual changes

- A shared `static/typography.css` loads through existing site navigation outside
  newsletter routes. It connects existing paragraph, supporting-copy and metadata
  selectors, and the Premium report's existing `--text` / `--muted` tokens, to
  primary `#f1f5f9`, secondary `#cbd5e1` and muted `#a8b8c8`. Existing heading,
  navigation action and semantic accent rules retain their hierarchy.
- Footer copy/links use the shared muted token, retaining their original fallback
  when the stylesheet is absent. Search placeholder contrast is also improved.
- Opportunities signal markup adds the existing lowercase signal as a CSS class.
  BUY uses `#4ade80`, HOLD `#f4c95d`, SELL `#fb7185`, matching existing stock-page
  semantic colours, with corresponding translucent badge backgrounds.
- Only the six decision-breakdown labels gain a span with secondary text colour
  and weight 600. Scores remain near-white and weight 700. Grid/spacing rules are
  unchanged, including two desktop columns and the existing single mobile column.

## Validation

- 121 tests passed: `test_newsletter_phase21.py`,
  `test_newsletter_source_quality.py`, `test_opportunity_radar.py`,
  `test_methodology_presentation.py`, `test_persistent_navigation.py`.
- 203 tests passed, one pre-existing failure: `test_stock_chart.py`,
  `test_public_clarity.py`, `test_newsletter_latest_resilience.py`,
  `test_newsletter_published_artifacts.py`, `test_beginner_examples.py`.
  `test_homepage_explains_product_and_has_primary_calls_to_action` expects old
  homepage wording. The same assertion was reproduced against unmodified
  `355be16` in an isolated module; no copy or unrelated test changes were made.
- Read-only mocked diagnostic probes reproduced identical fallback copy for
  provider timeout, empty successful response and untrusted-source rejection;
  a valid in-window Reuters fixture was accepted. No new news regression test
  is added because no news behavior was changed.
- Browser inspection used an isolated local Flask preview and synthetic market
  fixtures, not production entitlements or data. Screenshots and DOM checks covered
  Home, Opportunities, AAPL stock, populated MSFT/AAPL Compare, MSFT Premium report,
  How It Works and Investment Compass at 1440x1000 and 375x812. No document-level
  horizontal overflow was found. Detailed Research was opened, BUY/HOLD/SELL
  computed colours verified, and navigation/footer checked. Opportunities was
  checked with both missing and populated Return Outlook fixtures.
- Against representative panel `#121e2b`, primary/secondary/muted contrast ratios
  are 15.38:1 / 11.35:1 / 8.31:1. This is a representative check, not a claim about
  every gradient, translucent surface or unaffected component.
- `git diff --check` passed.

Rankings, scores, signal calculations, Return Outlook behavior and persistence,
Free/Premium entitlements, universe, SEO/routes, providers, dependencies,
newsletter layout and architecture remain unchanged. No push or deployment.
