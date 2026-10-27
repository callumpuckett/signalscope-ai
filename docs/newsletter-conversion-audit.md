# Newsletter-to-website conversion audit

27 October 2026. Initial audit followed by approved Phase 2 verification and deployment.

## Existing functionality confirmed

- Fresh HTTP reads of the production site returned 200 for `/newsletter/latest` and `/upgrade`. The newsletter showed Week 43, updated 23 October 2026 at 08:03 UTC: the most recent Friday before this audit. This confirms the public issue is current, but does not independently establish whether Beehiiv has a newer published post.
- The latest route serves a validated published artifact, without generating a newsletter or fetching market data on a visit. Existing tests cover publication integrity, last-known-good behaviour and observable 503 responses when no valid artifact exists.
- Shared navigation already offers research tools. `/upgrade` shows £5/month, cancellation information, CSRF-protected Stripe checkout when enabled, and a disabled-checkout state when configuration is unavailable. The live upgrade page displayed the enabled checkout form; no payment was started.
- Newsletter signup is linked from navigation, stock detail pages and the upgrade page, and from the issue itself. The form validates email, requires CSRF and Turnstile, and calls Beehiiv subscriptions with welcome-email enabled. Focused tests confirm success and error handling with mocked integrations. Actual production subscription acceptance, confirmation and inbox delivery were not tested.

## Problems and changes

- Production `/premium` returned 404. Added a permanent redirect to the existing `/upgrade` page, preserving the complete raw query string, including all UTM parameters, repeated values and percent encoding. Pricing, page design, checkout and entitlement logic are unchanged.
- The issue offered no prominent contextual free-research next step; its closing links included a redundant link to itself. Added a compact panel below the introduction linking to Stock Universe, Compare Stocks, £5/month Premium and free newsletter signup. It uses existing colours, typography and section styling. Future issues also replace the redundant closing link with Stock Universe and state the Premium price.
- Existing published issues are immutable: the panel is added to their response HTML without changing stored artifacts. Newly published issues include the same panel once. No extra storage or market-data calls are introduced.
- Signup copy promised automatic delivery of the latest issue, while the signup path requests Beehiiv’s welcome email rather than sending that issue. Corrected the copy to describe confirmation and the normal Friday schedule.
- The signup email input lacked an accessible name and forced a 240px minimum width inside nested mobile padding. Added an accessible name and allowed the input to shrink to its container.

## Attribution findings

No application analytics SDK, pageview/event collector, campaign storage or conversion reporting was found in the repository. UTMs on newsletter landing URLs are accepted, but ordinary internal links do not retain them. Beehiiv signup metadata (`stockradar` / `website` / `stockradar_weekly`) describes new website subscriptions, not attribution of existing readers arriving from email. The new Premium redirect preserves UTMs but does not create attribution reporting.

The Procfile starts `gunicorn app:app` without explicit access-log configuration. Hosting logs might already retain query strings, but their configuration, retention and reporting access were not available to this audit. Do not assume an existing end-to-end Beehiiv-to-Premium funnel can be measured.

## Validation

- 63 focused pytest cases passed: newsletter conversion journey (3), latest resilience, published artifacts, Beehiiv, Turnstile, upgrade presentation and Premium readiness (60).
- Installed Chrome with Playwright checked `/newsletter/latest` with Beehiiv UTMs, `/newsletter` and `/premium` at 1440×900, 390×900 and 320×900. No horizontal overflow; one research panel; alias resolves to `/upgrade`; invalid email rejected by browser validation. Desktop/mobile panel screenshots visually reviewed.
- Browser preview used the freshly fetched production issue HTML through the local route to exercise compatibility with an already-published issue. Signup used local configuration; a live Turnstile challenge and external submission were not exercised.
- `git diff --check` passed. No dependencies added.

## Remaining recommendations

1. Use consistent Beehiiv destination tags, e.g. `utm_source=beehiiv&utm_medium=email&utm_campaign=stockradar_weekly_2026_10_23&utm_content=research`. Use distinct content tags for research and Premium links.
2. Inspect existing hosting access logs for query strings and retention before considering any attribution implementation. Reliable tracking across internal navigation and completed subscriptions would require a separately approved scope; no analytics service, tracking cookies or payment metadata changes were added here.
3. After deployment approval, perform one controlled newsletter signup with a designated test inbox and confirm Beehiiv acceptance, confirmation/welcome delivery and suppression behaviour. Current tests do not prove deliverability.
4. Measure link engagement and Premium starts before considering additional layout changes. These changes remove demonstrated friction; no conversion uplift is claimed.

## Approved Phase 2 verification

- Redirect now appends the raw incoming query string to the fixed `/upgrade` destination. All query parameters, repeated values, blank values and percent encoding are retained; no user-controlled redirect destination is used.
- 126 focused regression cases passed, including seven-UTM coverage, exact encoded/repeated query round trips, newsletter publication/navigation/signup, CSRF, Turnstile, authentication controls and existing performance paths. `git diff --check` passed.
- Function-level comparison against HEAD confirmed no changes to `upgrade`, `create_checkout_session`, `newsletter`, `login`, `premium_has_access` or `verify_turnstile_token`.
- Predeployment production reads returned 200 for newsletter latest, upgrade, signup, login and health. Upgrade retained the existing £5/month checkout form; signup and login retained CSRF fields; signup retained Turnstile configuration. Median of three requests: latest 1.230s, upgrade 0.328s, signup 0.366s, login 0.655s, health 0.362s. These small samples are a smoke baseline, not a load benchmark.
