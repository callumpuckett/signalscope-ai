# Premium conversion review

## Scope

Local-only conversion improvements built on `74f66635e66f873218287a12869dc447695e5fec`.
No push or deployment is authorised for this task.

- `/upgrade` leads with “Stop Guessing. Start Understanding.”, the supplied supporting message and the existing £5/month plan.
- The Intelligence Report guide reuses the real report header macro and stylesheet. It describes the five report sections without building, fetching or exposing a private report. No invented stock example, financial metric, testimonial or return claim is used.
- Free vs Premium comparison reflects existing route permissions below.
- Two existing checkout forms keep their POST destination, CSRF field and enabled/disabled conditions. Stripe configuration, session creation and entitlement logic are unchanged.
- Active Premium/owner sessions see an already-active message and report/tool links, without purchase forms or the marketing guide.
- Removed the unused Decision Brief calculation from `/upgrade`, the repeated Decision Brief marketing block, the future-feature promotion and the landing-page newsletter side tab. The inline Free newsletter alternative remains.
- Stock research now has one concise contextual report upsell instead of a teaser-card CTA plus a large repeated six-item preview. Free signals, confidence and provenance remain intact.
- Locked comparison and Portfolio Fit previews explain the value of the same subscription. Portfolio wording accurately describes ticker-role groups and concentration by holding count, not measured portfolio allocation.

## Permission audit and published comparison

| Feature | Free | Premium | Existing enforcement inspected |
| --- | --- | --- | --- |
| Signals and charts | Headline signal, confidence, available history | Also deeper report context | `stock_detail` |
| Decision Score | Heuristic score preview in Opportunities | Report score components and full ranked research | `opportunities_html` public preview; report gate |
| Intelligence Report | Locked guide | Complete report | `stock_detail`, `premium_decision`; builder runs only after access check |
| Portfolio Intelligence | Locked Portfolio Fit preview | Stock role guidance and ticker-entry Portfolio Fit Checker, concentration by count | `portfolio_fit`, report gate |
| Compare Stocks | Signal/confidence teaser for entered pair | Full research comparison | `render_compare_page` uses separate teaser and full builders |
| What-If | £1,000 illustration; hypothetical-return mode when target data unavailable | Also £250, £500 and custom amounts | `what_if` ignores Free amount overrides; existing templates gate controls |
| Watchlists | Full AI Watchlist | Also Premium Watchlist Intelligence | Public dashboard/watchlist and `premium_watchlist` gate |
| Daily Opportunity Radar | Limited preview | Complete ranked Top 5 with available supporting research | `opportunities` Premium branch |

Existing availability limits apply. Fundamentals and valuation score components remain heuristic. Nothing claims verified company developments, portfolio position weights, guaranteed outcomes, email/push alerts or unavailable features.

## Contextual prompt locations

- `/stock/<symbol>`: one report banner with price, `/upgrade` link and locked report preview link; omitted for Premium.
- `/premium-decision/<symbol>`: existing locked report page, CTA now includes £5/month; authorised reports unchanged.
- `/compare/<a>/<b>`: existing locked comparison teaser explains that the same subscription includes the report; no extra prompt added.
- `/portfolio-fit`: existing locked preview describes count-based role review and the report benefit.
- Existing What-If amount-control prompt is retained, rather than adding another.

## Validation

- Final combined focused run: **275 passed, 5 deselected in 4.91s**. Suites: conversion, upgrade presentation, report accuracy/access, What-If, Premium readiness, portfolio education, security/CSRF/entitlements, rate-limit pruning, request timing, market resilience, provenance, methodology, Opportunity Radar, public-copy/legal links and SEO.
- The initial run exposed two security tests relying on the old Portfolio Fit sentence; that phrase was preserved and both tests then passed. The existing stock-preview marker was also preserved for the portfolio-access regression. Enforcement logic was never changed.
- Five unrelated tests also fail against the deployed baseline: homepage old-copy assertion, refund-policy old-copy assertion, feedback email assertion the existing header 1100px-breakpoint assertion, and the Opportunities preview metadata test’s pre-existing storage-read expectation. No navigation CSS or unrelated copy was changed to address them.
- New tests ensure the landing page does not call report/brief/data/provider builders; verify accurate comparison boundaries, active-account presentation, one contextual stock banner, private-report exclusion, CSRF rejection, enabled mocked checkout redirect and the existing line-item price reference.
- Desktop 1440×1000; phone 360×800 and 390×844; tablet 768×1024: no page or comparison horizontal overflow. Manual mobile review corrected cramped table labels into stacked feature rows with Free/Premium columns.
- Browser verified `/upgrade#report-guide` navigation and `/premium-decision/MSFT` locked preview, with no restricted report section and a correct return path to `/upgrade`.
- Screenshots: `screenshots/premium-conversion/desktop.jpg`, `mobile.jpg`; complete pages in `desktop-full.jpg`, `mobile-full.jpg`.
- Screenshots use the local app with the checkout-enabled presentation stubbed. Stripe session creation is blocked in that local preview. No live checkout or payment was started.
- Controlled local Flask render benchmark: five warmups and 20 measured requests per version, identical Free/payment state, baseline unused brief stubbed. Median 2.928ms before, 3.099ms after; HTML 29,233 vs 30,448 bytes. This is template/render overhead, not production latency or a conversion-rate prediction. One existing static report stylesheet is reused; no external marketing assets or data calls added.
- Python compilation and `git diff --check` pass.

## Remaining limitations

- No production or live Stripe verification: this task explicitly requires local-only delivery. Existing checkout routing is tested with Stripe mocked.
- Conversion improvement has not been measured in live traffic; no uplift is claimed.
- Existing data/provider gaps, count-based portfolio limitations and support-based cancellation remain accurately described.
- The unrelated baseline test failures listed above remain open.
