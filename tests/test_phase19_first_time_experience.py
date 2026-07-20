from copy import deepcopy
from unittest.mock import patch

import app


PREMIUM_SENTINEL = "PREMIUM_ONLY_REASON_MUST_NOT_RENDER"


def dashboard_data():
    return {
        "market_status": {
            "uk_status": "CLOSED",
            "uk_time": "00:00",
            "us_status": "CLOSED",
            "us_time": "00:00",
        },
        "recommendations": [
            {
                "ticker": "MSFT",
                "signal": "HOLD",
                "confidence": "65%",
                "reason": PREMIUM_SENTINEL,
                "premium_analysis": PREMIUM_SENTINEL,
            }
        ],
        "premium_decision_brief": {
            "strongest": {
                "label": PREMIUM_SENTINEL,
                "signal": "BUY",
                "confidence": "99%",
            }
        },
    }


def render_public_home(data=None):
    data = deepcopy(data or dashboard_data())
    with (
        patch.object(app, "get_cached_dashboard_data", return_value=data),
        patch.object(app, "get_stock_universe", return_value=[]),
    ):
        return app.app.test_client().get("/")


def test_free_report_preview_reuses_only_existing_free_fields():
    preview = app.build_homepage_free_report_preview(dashboard_data()["recommendations"])

    assert preview == {
        "company_name": "Microsoft",
        "ticker": "MSFT",
        "signal": "HOLD",
        "confidence": "65%",
        "strength": "Moderate",
        "is_current": True,
        "explanation": (
            "The current HOLD signal is StockRadar's latest free research prompt for Microsoft."
        ),
        "research_next": (
            "Open the live report to review the current signal, strength and chart context."
        ),
    }
    assert PREMIUM_SENTINEL not in str(preview)


def test_free_report_preview_has_a_claim_free_fallback():
    preview = app.build_homepage_free_report_preview([])

    assert preview["is_current"] is False
    assert preview["signal"] == ""
    assert preview["confidence"] == ""
    assert preview["strength"] == ""
    assert preview["explanation"] == (
        "Example preview — open the live report for the current signal."
    )
    assert preview["research_next"] == (
        "The live Microsoft report is the authoritative current view."
    )


def test_public_hero_integrates_the_single_stock_search_journey():
    response = render_public_home()
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "<h1>Learn to think like an investor.</h1>" in page
    assert (
        "Search any stock or ETF and get a clear, plain-English research summary in seconds."
        in page
    )
    assert "Start with a company you already know" in page
    assert "Search any stock or ETF." in page
    assert '<label for="smartSearchInput">Search a stock or ETF</label>' in page
    assert 'placeholder="Try Microsoft, Apple, SPY or MSFT"' in page
    assert "View free report" in page
    assert page.count('<form class="smart-search"') == 1
    assert '<div class="hero-actions">' not in page
    assert "AI-assisted market research" not in page
    assert "We help people become better investors" not in page
    assert "Start free with StockRadar Weekly" not in page


def test_suggested_examples_open_real_existing_reports():
    page = render_public_home().get_data(as_text=True)

    expected_links = {
        "Microsoft — MSFT": "/stock/MSFT",
        "Apple — AAPL": "/stock/AAPL",
        "Amazon — AMZN": "/stock/AMZN",
        "S&amp;P 500 ETF — SPY": "/stock/SPY",
    }
    for label, href in expected_links.items():
        assert f'href="{href}">{label}</a>' in page

    assert (
        "New to investing? Start with a company or fund you already recognise."
        in page
    )


def test_three_steps_and_free_preview_use_the_required_compact_copy():
    page = render_public_home().get_data(as_text=True)

    assert "<strong>Search</strong><span>Choose a company or fund you recognise.</span>" in page
    assert "<strong>Understand</strong><span>See the current signal in plain English.</span>" in page
    assert (
        "<strong>Research</strong><span>Learn what evidence and risks may matter next.</span>"
        in page
    )
    assert "See a free report in action" in page
    assert '<strong class="free-report-preview-name">Microsoft</strong>' in page
    assert '<span class="free-report-preview-ticker">MSFT</span>' in page
    assert '<span class="free-report-signal hold">HOLD</span>' in page
    assert "Signal strength: Moderate" in page
    assert "65% confidence" not in page
    assert (
        "The current HOLD signal is StockRadar&#39;s latest free research prompt for Microsoft."
        in page
    )
    assert "Research next:" in page
    assert 'href="/stock/MSFT">View Microsoft’s free report</a>' in page


def test_logged_out_source_does_not_leak_premium_only_preview_data():
    page = render_public_home().get_data(as_text=True)

    assert PREMIUM_SENTINEL not in page
    assert "A quick decision-support scan from the current StockRadar universe." not in page
    assert "Open Premium Watchlist</span>" not in page
    assert "Locked Decision Brief preview" in page


def test_premium_and_newsletter_sections_keep_their_batch_one_copy():
    page = render_public_home().get_data(as_text=True)

    assert "Trading apps show you the market. Premium helps you understand the signal." in page
    assert (
        "Free tells you what the scanner is flagging. Premium is the calm "
        "decision-support layer: why it matters, what risk to check, where it may fit "
        "and what to research next."
    ) in page
    assert "Microsoft signal preview" in page
    assert "Upgrade to Premium — £5/month" in page

    assert "StockRadar Weekly" in page
    assert (
        "Get the 5-minute market signal every week — what’s strengthening, what’s "
        "weakening, and what may matter next. Free market context, signal highlights "
        "and risk prompts for everyday investors."
    ) in page
    assert 'href="/newsletter">Join Free</a>' in page


def test_batch_two_order_places_free_preview_before_unchanged_premium():
    page = render_public_home().get_data(as_text=True)
    markers = (
        '<header class="public-header">',
        '<div class="card hero-card public-hero" id="investment-compass-card">',
        '<div class="suggested-searches"',
        '<section class="card product-steps" id="how-stockradar-works"',
        '<section class="card free-report-preview" id="free-report-preview"',
        '<div class="card premium-home-card" id="premium-decision-section">',
        '<div class="trust-strip" aria-label="How to use StockRadar">',
        '<div class="card newsletter-cta-card" id="newsletter-cta">',
        '<footer style=',
    )

    positions = [page.index(marker) for marker in markers]
    assert positions == sorted(positions)
