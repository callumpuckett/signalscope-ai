# StockRadar HQ technical SEO audit — 26 November 2026

## Release status

Validated code prepared on `codex/technical-seo`, based on production branch commit `3130d58748544fd7a85560347c80ecc326a96610` (rollback code reference). No push or deployment: the unchanged production browser gate failed twice on Cloudflare challenge CSP/console errors and aborted challenge requests. `PRODUCTION_SMOKE_TESTS.md` requires a passing browser gate. The gate was not weakened, and no consent, authentication, Stripe, infrastructure or service-plan settings were changed. The current database provider and Render Live commit could not be independently authenticated; public diagnostics intentionally hide these details. Existing uncommitted workspace changes were preserved by using an isolated checkout of the production branch.

## Evidence and findings

The supplied GSC counts are accepted as user-verified observations, not newly downloaded GSC evidence. Indexed URLs fell by 734 (37.9%). Neither aggregate exclusion totals nor this sample establish why Google excluded each URL. The 716 proper-canonical alternates are normally intentional consolidation, not 716 errors to reverse. The 148 potentially misdirected brand clicks are not qualified-investor demand and should be separated from conversion analysis.

A bounded, two-concurrent-request curl sample inspected 22 production URLs. All final responses were HTTP 200. Raw measurements are in `technical-seo-production-before.json`.

| Verified finding | Implication and action |
| --- | --- |
| AAPL base URL and all six chart ranges already had one canonical to `/stock/AAPL`; MSFT, KO, SPY, HLMA.L and the BA.L alias destination also had correct canonicals. | The 95 missing-canonical GSC reports were not reproduced today. Retain range functionality and canonical consolidation; do not index or robots-block range variants. |
| `/stock/aapl` returned 200. `/stock/BAE.L` temporarily redirected and added `?range=1mo`. | Add permanent canonical ticker redirects, preserving explicitly supplied query parameters without creating a default range URL. |
| Sitemap: 1,016 unique entries, no query URLs; included subscription-management and feedback utilities. Tickers were emitted without canonical normalisation. | Remove the two utilities and normalise/deduplicate canonical ticker entries. No invented lastmod dates. |
| `/premium-decision/MSFT` was a generic locked preview without indexing directives. | Explicit noindex/follow for all Premium Decision responses, including unlocked responses; no protected report fields or entitlement logic changed. Public stock pages and `/upgrade` remain the search acquisition destinations. |
| `/newsletter/latest` returned a published issue without canonical or description. | Add missing response-only head metadata to old/new artifacts without modifying stored issues or body content. Preserve existing metadata. Newsletter landing/latest remain eligible; artifact-unavailable responses remain 503. |
| Universe base and query pages shared the base canonical. Base rendered 250 stock links, despite 1,002 stock sitemap entries. | Keep the base universe eligible; noindex internal search results. Deep stock discovery still relies heavily on the sitemap. Visible pagination/navigation is deferred because layouts/functionality must remain unchanged. |
| Stock pages render public signal/provenance, confidence, research limits, chart summaries and income context server-side. AAPL, MSFT and SPY samples had explicitly labelled fallback prompts; every sampled stock HTML response reported unavailable chart source data. | Insufficient differentiated, evidence-backed public research and intermittent source availability are plausible indexing obstacles. Do not fabricate company analysis or expose Premium content. The browser subsequently rendered the MSFT chart, confirming variability rather than universal failure. |
| Home title emphasised AI signals and lacked the HQ brand; stock titles only said Stock Detail. | Clarify stock-market research/investment-analysis intent and company identity in title, description and social metadata. Add factual WebSite/WebPage JSON-LD only; no return, rating, price, recommendation or rich-result claims. |
| Stock TTFB was approximately 1.7–2.7s for direct sampled URLs; alias completion was 4.8s. Health was 0.29s, homepage 0.33s, latest newsletter 1.83s. | Small warm client-side sample, not a cold-start or DB benchmark. Existing October diagnostics identify durable rate-limit database work as a historical latency source; November reliability work already improves providers/templates/schedulers. No evidence supports buying infrastructure or weakening security. |

Robots.txt is valid, permits crawling and points to the production sitemap. It is intentionally unchanged so crawlers can read both canonical and noindex directives. Utility/auth/admin/API paths receive explicit noindex headers; authentication remains the actual access control. Unsupported arbitrary stock symbols retain existing functionality but receive noindex rather than promoting generated fallback pages into search.

## Validation

- 164 targeted tests passed, including public SEO, all six chart ranges plus invalid-range fallback, permanent aliases/query preservation, sitemap deduplication, utility/Premium noindex, unknown tickers, safe JSON-LD escaping, newsletter artifact immutability, security, consent analytics, Premium conversion/readiness, chart behaviour, stock timings, universe coverage and smoke-runner tests.
- Homepage and stock-template bodies are byte-identical to production source. Head metadata is the only template change. Newsletter tests verify identical body content and unchanged stored artifacts.
- `git diff --check` passed.
- Production browser gate twice passed health, desktop interaction/navigation, MSFT/Microsoft/AAPL search, Microsoft identity/report/chart and newsletter/Turnstile/latest/RSS UI checks. Both runs then exited 1 on third-party Cloudflare challenge errors; the mobile portion was not reached. This is a release blocker, not a passing smoke result.
- No production write, newsletter submission, charge, login or subscription change was performed.

## Outstanding work and expected benefit

A clean production browser gate and authenticated Render release details are required before deployment. After deployment, verify Live status, run the same read-only probe and browser gate, inspect representative US/UK/ETF URLs in GSC, and resubmit the canonical sitemap. No bulk indexing request or GSC action was performed.

Use URL Inspection and crawl statistics to distinguish old canonicals, rendering failures, soft 404 assessments and quality decisions for the 707 discovered and 41 crawled stock URLs. Inspect Render cold-start events, worker/provider diagnostics and database stage traces before attributing latency to any one component. No current Render logs, DB timings, Google-selected canonicals or live GSC inspection results were accessible in this task.

These fixes should consolidate ticker variants, remove utility/locked-preview search candidates and improve brand/query relevance. They cannot guarantee reindexing, ranking or subscriptions; meaningful unique public evidence remains the larger content opportunity. Expected excluded counts may rise appropriately as explicit noindex URLs are recrawled. Measure qualified non-brand research clicks, stock-to-upgrade visits and consented Premium conversions, not raw indexed totals.

References: [Google canonical consolidation](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls), [Google noindex guidance](https://developers.google.com/search/docs/crawling-indexing/block-indexing). Existing operational evidence: `docs/production-timeout-investigation.md`, `docs/reliability-audit-2026-11-13.md`, `PRODUCTION_SMOKE_TESTS.md`.
