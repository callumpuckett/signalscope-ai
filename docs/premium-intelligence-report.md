# StockRadar Intelligence Report

Implemented locally on 15 October 2026, building on prerequisite commit
`f9b86972744b4b0b806a0067b1cf90a36e5f8669`.

## Result and reused features

Premium stock research now presents one Intelligence Report rather than the repeated
Today’s Context, decision summary and supporting panels. The same shared template
serves `/premium-decision/<symbol>`. Free stock research retains its existing signal,
chart, fundamentals and income access, with an Intelligence Report upgrade preview.

The five report sections reuse:

1. Investment Overview: `opportunity_score_components`, existing overall signal,
   confidence and `signal_provenance`. The displayed score retains the existing
   calculation, including its fractional value, and explains all six components.
2. Opportunities: existing AI Reasoning and `build_business_education`, explicitly
   separating general growth-driver commentary from verified company evidence.
3. Risks: existing Risk Read, `weaker_evidence`, business risks and valuation limits.
4. Portfolio Intelligence: `get_premium_report` and its existing Portfolio Fit,
   portfolio builder, overlap, concentration and position-size education.
5. What to Watch Next: existing Watch-Next, strengthening evidence, business research
   question and available cached events/headlines.

The existing Before You Act checklist and investing lesson close the review. Compare,
What-If, Portfolio Fit and Premium Watchlist remain existing linked tools. The report
adds no new financial, forecasting or execution functionality.

## Access, data and performance

Both routes assemble report content only after `premium_has_access` succeeds, retaining
existing subscriber entitlement validation and owner administrative access. Free and
revoked accounts receive no restricted report object, page elements or serialized
report payload. Authenticated report responses use `Cache-Control: no-store` and
`Vary: Cookie`. No report API was added. The public news serializer remains separate.

Stock pages reuse the financial context already loaded for existing functionality;
they do not duplicate financial requests. The direct Premium URL reads the existing
financial cache only. Both views read completed local news cache entries without
refreshing the dashboard or fetching news. TTLs, negative caching, cooldowns and
provider fallback logic are unchanged. Stale financial values carry a stale label;
cache retrieval is distinguished from a financial reporting date. Earnings dates
require a matching, fresh cache and a date that has not passed. Invalid/missing
publication dates are unavailable. Company-name mentions and broader thematic news
associations have distinct labels, and neither claims independent verification.

A scoped responsive stylesheet preserves the navy, green and amber design. Desktop
shows opportunities and risks side by side; mobile stacks them. The score breakdown
and optional supporting education use native keyboard-accessible disclosures.
The floating Newsletter overlay is omitted on Premium stock pages because it covered
report text on mobile; the Free overlay and existing Newsletter routes remain intact.

The offline before/after comparison used the prerequisite commit, identical signal
inputs, stubbed provider calls, five warmups and 20 measured authenticated stock-page
requests per version. Median rendering time was 12.18ms before and 8.01ms after;
HTML size was 62,990 bytes before and 51,890 after. These are local composition/rendering
measurements, not production network latency or a service-level guarantee. Raw results
are in `screenshots/intelligence-performance-validation.json`.

## Validation and screenshots

New focused coverage in `tests/test_intelligence_report.py` verifies active, Free and
revoked entitlements; both report routes; restricted page-source content; no shared
HTML caching; existing provider call counts; cache-only direct access; score reuse;
generated/unknown/stale source states; stale/missing/mismatched financial data;
future cached event dates; symbol-specific news associations; missing/invalid cache
fields; escaping; all five sections and unavailable states; and separation from the
public news API. Existing presentation assertions were updated to the new report
hierarchy while preserving their content and entitlement checks.

Existing prerequisite, universe, business education, financial fundamentals, portfolio
builder, Premium, What-If, market resilience, security, entitlement revocation,
performance and persistent-navigation regressions also passed. The known unrelated
navigation-breakpoint failure was not changed. Final regression result: 263 passed in 4.89 seconds.
`git diff --check` passed.

Headless Chrome with the existing bundled Playwright tooling checked 320, 390, 900
and 1440px layouts. All widths passed document/report horizontal-overflow checks,
expected column counts, keyboard score-disclosure operation and expanded-score
layout checks. Screenshots were visually inspected. No dependencies were installed.

- Desktop: `screenshots/intelligence-report-desktop.png` (1440px viewport).
- Mobile: `screenshots/intelligence-report-mobile.png` (390px viewport).
- Browser check results: `screenshots/intelligence-layout-validation.json`.

Screenshots show the actual authenticated stock route using local recommendation
fallback data and deliberately unavailable financial/news data, with external network
requests blocked. They illustrate accurate missing-data states rather than live
market data. Score detail is expanded in both screenshots.

## Remaining limitations

- Undated recommendation inputs cannot establish current observations. Generated and
  fallback prompts remain educational heuristics, explicitly labelled.
- Financial reporting periods, verified company strengths/developments and complete
  event schedules are not supplied by the existing data. Missing fields stay unavailable.
- Cached headline mentions are attribution, not independent verification. Legacy
  headlines without association metadata are excluded; cache availability can differ
  between a stock page and the direct report.
- Actual portfolio weights and fund look-through holdings are not supplied, so the
  report cannot calculate personal exposure or concentration percentages.
- The direct report deliberately does not warm financial or news caches. Visiting it
  with empty caches produces unavailable states.

## Files changed

- `app.py`
- `templates/intelligence_report.html`
- `static/intelligence-report.css`
- `tests/test_intelligence_report.py`
- `tests/test_phase18_context_business.py`
- `tests/test_phase20_fundamentals.py`
- `tests/test_portfolio_builder_education.py`
- `docs/premium-intelligence-audit.md`
- `docs/premium-intelligence-report.md`
- `docs/screenshots/intelligence-report-desktop.png`
- `docs/screenshots/intelligence-report-mobile.png`
- `docs/screenshots/intelligence-layout-validation.json`
- `docs/screenshots/intelligence-performance-validation.json`

No push or deployment was performed.
