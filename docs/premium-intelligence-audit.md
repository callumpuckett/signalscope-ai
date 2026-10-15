# StockRadar Intelligence Report — prerequisite audit

Date: 15 October 2026

Status: implementation stopped at the requested reliability boundary. No application,
pricing, billing, provider or entitlement changes have been made.

## Material dependencies

1. **Signal provenance is lost.** `get_recommendations` loads CSV rows or defaults,
   then `expand_recommendations` inserts generated rows. `generated_signal_for_ticker`
   assigns fixed signals to selected tickers and otherwise uses universe index
   divisibility. These rows have no machine-readable source flag or observation time.
   `get_stock_ai_context` generally finds them in the resulting list, bypassing its
   separate generated-universe branch. Reusing its confident momentum/risk wording
   as current evidence would misrepresent the inputs. Cache insertion time is not
   the time the underlying signal was observed.
2. **Decision Score is a heuristic ranking.** `opportunity_score_components` combines
   signal and confidence with fixed ticker categories. Momentum derives from the
   signal; fundamentals and valuation components are category constants. It is
   distinct from the stock page's confidence percentage and is not a measured
   business-quality, valuation or return estimate. Preserve the calculation, but
   disclose these rules explicitly before using it in the unified report.
3. **Business education is not company evidence.** `build_business_education` provides
   general sector/fund explanations. It cannot establish actual business strengths,
   recent positive developments or verified growth outcomes. These need accurate
   unavailable states unless already supported by validated cached evidence.
4. **News association is thematic.** `match_news_to_stocks` uses substring matching,
   shared sector words and short tickers, and falls back to SPY/QQQ when no match
   exists. A chips headline can match multiple chip companies without naming them.
   Do not describe those matches as company developments. The stock route currently
   calls `build_today_context` without company news.
5. **Freshness is incomplete.** Recommendation context has no observation timestamp.
   Dividend metadata has cache retrieval timing and return-outlook eligibility
   checks, but its general fundamentals/source note does not establish each metric's
   financial period. Do not label cache retrieval as a financial reporting date.

Correcting source metadata and its propagation through shared recommendation
consumers is broader than report presentation. This audit stops here under the
instruction to report a required broader reliability fix before expanding scope.

## Reuse inventory

| Feature | Existing implementation | Report use and limits |
| --- | --- | --- |
| Decision Score | `opportunity_score_components` | Reuse exact formula with heuristic explanation; never substitute confidence. |
| AI Reasoning | `get_stock_ai_context`, `get_premium_report` | Reuse after preserving source provenance; no new AI service. |
| Risk Read | `risk_view`, `weaker_evidence`, `risk_level` | Existing signal-based prompts; distinguish from verified business risks. |
| Portfolio Fit | `classify_portfolio_role`, `build_portfolio_builder_education`, `/portfolio-fit` | Reuse role and overlap education. No personal weights are supplied on the stock report, so actual concentration percentages are unavailable. |
| Watch-Next | `watch_next`, `stronger_evidence`, `next_move` | Reuse monitoring prompts; no verified upcoming event calendar is supplied. |
| Before You Act | `get_premium_report` checklist | Reuse as the report's closing research checklist. |
| Compare Stocks | `build_compare_stock_context`, `/compare` | Link to existing comparison; avoid fetching both stocks while rendering a report. |
| What-If | `what_if_return_outlook`, existing illustration functions | Link to existing tool. The outlook getter can fetch on cache miss; do not call it from a zero-additional-call report. |
| Premium Watchlist | `/premium-watchlist`, `build_premium_decision_brief` | Link to existing review; generated-source limitation also applies. |
| Entitlements | `premium_has_access`, server entitlement records | Reuse active entitlement check, including existing owner access. Existing decision route gates rendered detail but computes report data before checking access; gate before assembly in the future report. |
| Financial data | `get_dividend_context`, `_cached_dividend_context` | Reuse validated data already obtained by stock detail. Getter may call provider on cache miss, so a separate report must use cache-only reads or accurate unavailable states. Preserve negative TTL, cooldown and outlook eligibility. |

## Bounded prerequisite proposal

Preserve imported/generated/default/unavailable provenance and available source
observation timestamps through recommendation loading and stock context. Never
manufacture dates for existing CSV rows. Label heuristic scores and generated
prompts explicitly. Keep general education separate from company evidence and
show company-news unavailable unless a reliable association is already present.
This does not require new providers, API requests, dependencies or a new scoring model.

After that prerequisite is authorized, assemble the five requested sections from
existing objects, gate assembly on entitlement, and reuse stock-detail financial
data. Add cache-only behavior for a direct report URL. Validate Free/direct access,
active and revoked entitlement, generated/default/imported provenance, stale and
missing data, score consistency, zero additional provider calls, and desktop/mobile
layout before committing implementation.

## Validation and delivery scope

Baseline focused tests: `tests/test_premium_readiness.py`,
`tests/test_opportunity_radar.py`, `tests/test_what_if.py`.
Result: 106 passed, 1 failed in 1.54 seconds. The existing failure is
`test_header_reserves_logo_column_and_mobile_overrides_dense_layout`, which expects
`@media(max-width:1100px)` in the navigation HTML. Application code was unchanged;
this baseline layout assertion is not a report regression. `git diff --check` passed.

No report implementation or desktop/mobile screenshots exist at this stop point.
No report accuracy, responsive layout or performance claim is made. A passing
baseline suite does not establish the data provenance guarantees identified above.

## Prerequisite implementation — 15 October 2026

The subsequently authorized corrections are now implemented without architectural,
provider, billing or scoring changes:

- Recommendation rows retain `signal_source` (`calculated`, `supplied`, `generated`,
  `fallback`, `unavailable`) and optional `observed_at`. CSVs may supply these columns;
  existing unclassified CSV rows are identified as supplied, not assumed calculated.
  Defaults are fallback; deterministic expansion is generated. Unknown legacy
  snapshot provenance is unavailable rather than inferred.
- Observation timestamps must include a timezone and cannot be in the future.
  Missing/invalid dates stay unavailable. Generated/default prompts have no market
  observation date. Cache retrieval is explicitly separate from source observation.
  Cache reads recompute age on copies, preserving existing TTL and refresh behavior.
  Current means within the existing recommendation refresh window; stale means
  older than that window. Neither classification verifies input quality.
- Stock detail, the Premium decision page, recommendation confidence tables and
  Opportunity cards expose provenance/freshness. Cached snapshots recompute the
  displayed age. Existing scores and access checks are preserved.
- Decision Score breakdown labels fundamentals categories and valuation allowances
  as heuristic. The accompanying explanation states their fixed-category basis
  and distinguishes signal-derived momentum from measured financial analysis.
- Existing thematic news matching is retained. An additional conservative local
  classifier identifies company-name mentions or explicitly marked tickers; all
  other associations remain sector/market context. The initial feed and its public
  polling payload preserve that distinction. A direct mention is not independent
  verification of the article's claims; no story is marked verified company news.
- Business education is explicitly described as general sector/fund context rather
  than verified company strengths, financial results or recent developments.

Remaining limits: existing undated CSVs cannot establish current observations;
provenance on old persisted snapshots cannot be reconstructed; local headline
classification can miss aliases and does not validate claims. The original thematic
matcher still permits broad associations, now labelled as related context. No new
financial data, reporting dates, verified developments or upcoming event calendar
have been invented. The unified Intelligence Report remains a separate implementation.

Validation: new prerequisite tests cover CSV and fallback loading, generation,
invalid/future/missing dates, cache ageing without cache mutation, unchanged scores,
news classification and public serialization, and locked/unlocked report content.
Existing universe, business context, Premium, What-If, Opportunity, market resilience,
responsive feed, security and performance-path regressions were also run. The known
navigation breakpoint assertion reproduced before final validation and remains
unrelated; navigation styles were not changed. Final validation: 209 passed, 1 known unrelated breakpoint assertion deselected
in 1.87 seconds. `git diff --check` passed.
