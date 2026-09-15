from unittest.mock import patch

import app


def test_methodology_explains_current_rules_and_limits():
    response = app.app.test_client().get("/how-it-works")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    for heading in (
        "From inputs to research prompts", "How to read conviction and risk",
        "What AI contributes", "Data freshness and limitations", "Check the evidence yourself",
    ):
        assert heading in page
    assert 'id="opportunity-score"' in page
    assert "not a probability of profit" in page
    assert "fixed ticker groups and list-position rules" in page
    assert "not recalculated from live financial statements" in page
    assert "not a fair-value estimate" in page
    assert "Prices are added after ranking" in page
    assert "one snapshot per London calendar day" in page
    assert "no guaranteed publication time" in page
    assert "Free Opportunities preview" in page
    assert "Yahoo Finance history accessed through yfinance" in page
    assert "does not establish how every imported research file was produced" in page
    assert "not this six-component sum" in page
    assert "What StockRadar is not" in page
    assert "Join StockRadar Weekly" in page
    for ticker_group in (app.OPPORTUNITY_QUALITY_TICKERS, app.OPPORTUNITY_FUND_TICKERS, app.OPPORTUNITY_VOLATILE_TICKERS):
        assert all(ticker in page for ticker in ticker_group)
    example = app.opportunity_score_components({"ticker": "MSFT", "signal": "BUY", "confidence": "80%"})
    assert example["opportunity_score"] == 86
    assert "25 + 20 + 15 + 15 + 8 + 3 = 86" in page


def test_homepage_methodology_link_stays_in_existing_trust_strip():
    with (
        patch.object(app, "get_cached_dashboard_data", return_value={
            "market_status": {"uk_status": "CLOSED", "uk_time": "00:00",
                              "us_status": "CLOSED", "us_time": "00:00"},
        }),
        patch.object(app, "get_stock_universe", return_value=[]),
    ):
        page = app.app.test_client().get("/").get_data(as_text=True)
    start = page.index('<div class="trust-strip"')
    end = page.index('<div class="trust-item"><strong>Prompts, not instructions', start)
    assert 'href="/how-it-works">Read our research methodology →</a>' in page[start:end]


def test_upgrade_links_to_methodology_without_replacing_checkout():
    with (
        patch.object(app, "premium_has_access", return_value=False),
        patch.object(app, "stripe_checkout_configured", return_value=True),
        patch.object(app, "build_premium_decision_brief", return_value={}),
    ):
        page = app.app.test_client().get("/upgrade").get_data(as_text=True)
    assert 'href="/how-it-works#opportunity-score">How the Opportunity Score is calculated →</a>' in page
    assert page.count('action="/create-checkout-session"') == 2
    assert page.count("Start Premium — £5/month") == 2
