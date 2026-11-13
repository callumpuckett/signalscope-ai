# Consented Premium funnel

Uses existing GTM-M45PZW6K and its single G-6TMSFYY19K tag. No second GA configuration
or manual page_view is installed. GTM version 2 publishes privacy settings that disable
automatic history/scroll/link/form/video/download detection and user-provided data
capabilities. The existing GA4 property and measurement ID are preserved.

## Configuration and reporting

- Render: ANALYTICS_ENABLED=true enables the consent interface and optional collection.
  Default is false. GA4_API_SECRET is server-only, configured in Render, never in source,
  HTML, logs or the data layer. False/absent analytics enablement loads no analytics assets.
- GA4: property StockRadar HQ Website (564320874). Realtime verifies page_view,
  premium_view, premium_upgrade_click and begin_checkout. Reports > Acquisition shows
  consented traffic sources. Explore > Funnel exploration can use those events followed
  by purchase; no additional dashboard or reporting infrastructure is installed.
- purchase remains the paid conversion. Do not count premium_view, a click, a checkout
  or checkout-success page as a sale. Standard processed reports can take 24–48 hours.
- Events are deliberately counts of initial paid subscriptions, not a revenue ledger:
  no payment amounts, names, emails, card information, Stripe customer/session/subscription
  identifiers, portfolio contents, raw incoming query strings or document titles are sent.
  Transaction IDs are keyed, opaque digests, not Stripe identifiers. Actual revenue,
  discounts, renewals, cancellations and refunds remain authoritative in Stripe.

## Consent and privacy

Basic Consent Mode: before acceptance there is no GTM, Google script, iframe, preconnect,
analytics cookie or Google consent ping from this integration. Accept queues denied
defaults then the permitted analytics update before loading the existing GTM container.
Advertising stays denied. Reject does not load GTM. Preferences is persistently available
on instrumented public pages; private login, billing/account and success pages are excluded.
Privacy remains accessible from existing page footers.

Withdrawal disables the loaded measurement ID immediately, stops the application event
dispatcher, removes accessible _ga cookies, revokes the durable consent record and reloads
without GTM. A local rejection blocks stale accepted state and is propagated to other tabs.
Saving failure keeps browser analytics off and displays a retry message; previously sent
data is not erased. Consent is signed, HttpOnly, SameSite=Lax and Secure over HTTPS,
expires after 180 days and is protected by existing CSRF controls. Declining never blocks
research, checkout or entitlement handling. Browser blocking or withdrawal causes gaps;
this measures consented visitors, not all visitors.

Only fixed page categories and generic titles are measured. Stock symbols, comparisons,
holdings, scenario amounts and search strings are omitted. Referrers are reduced to origin;
only existing known StockRadar/Beehiiv campaign conventions are forwarded. This reduces
traffic-source detail intentionally. Event/user retention was inspected read-only in GA4:
2 months / 14 months; aggregated reports are separate. No property retention changes made.

## Trusted purchases and recovery

The existing Stripe create call and its parameters are unchanged. After Stripe actually
creates a checkout, a consented association records a keyed digest of that exact Session,
its existing random checkout intent and pseudonymous GA client/session context, valid for
24 hours. begin_checkout is server-side, not a submit-click proxy.

Only the existing signed checkout.session.completed webhook, after existing flow, mode,
product context and entitlement validation, can enqueue purchase. It additionally requires
live mode, payment_status=paid, a positive integer amount, a matching exact checkout and
existing consent association, and a non-expired, non-withdrawn durable consent record.
Zero-value, test-mode, unmatched and late confirmations do not count. New browser consent
after checkout does not retrospectively authorize that checkout. Nothing is emitted from
the success URL, an entitlement grant alone, renewal or subscription update.

The existing PostgreSQL application-state table holds consent/association/delivery state;
no new database service or schema migration is required. Claims are atomic under its
existing transaction lock. One stable purchase transaction_id per subscription prevents
webhook replays and supports Google's purchase deduplication. Pending purchase payloads
survive worker replacement and receive at most three attempts with backoff, rechecking
consent before each attempt. The existing periodic worker loop drains at most five pending
purchases per pass (no new scheduled service). No blind retry for ambiguous begin_checkout
delivery, which has no purchase transaction deduplication guarantee.

Delivery is outside the billing response in bounded-time background work. Failures never
change checkout redirects, access rights or webhook billing responses. A Google 2xx is an
accepted transport response, not proof that a report processed the event. Analytics is
best-effort: blockers, outages, exhausted retries, missing return associations and Render
Free sleep can delay or omit events. Stripe remains the source of truth. Expired associations
and consent records are removed on later updates; deduplication records expire after 400
days and old (>7-day) payment webhooks cannot recreate them.

## Targeted validation

- Python consent, payload minimization, CSRF, exact-checkout association, durable claims,
  retry, withdrawal, invalid signature and trusted-webhook tests, plus existing relevant
  Premium/Stripe/checkout regressions. Full audit/suite intentionally not repeated.
- Client JavaScript VM checks: unknown/rejected/withdrawn state loads no GTM; acceptance
  loads once; no manual duplicate page_view; sanitized URL/referrer payloads; no events
  after withdrawal.
- Connected Chrome preview: 320px/390px, no overflow, equal-width accept/reject controls,
  rejection persists, acceptance loads GTM, withdrawal removes it after reload.
- GA4 Realtime received the explicit-consent preview page_view and premium_view, generic
  title Premium. Google Measurement Protocol validation-only endpoint accepted the purchase
  schema with no validation messages; that endpoint does not record a sale.
- No real charge, fabricated production purchase or subscriber payment was made. Live
  purchase receipt needs the next genuine consented paid subscription (or a separately
  authorized payment acceptance test). Automated signed-event fixtures establish the gate,
  not end-to-end receipt of a real paid transaction.
