from unittest.mock import patch

import app


DASHBOARD_DATA = {
    "market_status": {
        "uk_status": "CLOSED",
        "uk_time": "00:00",
        "us_status": "CLOSED",
        "us_time": "00:00",
    }
}


def render_home(path="/", premium=False, owner=False):
    client = app.app.test_client()
    if premium or owner:
        with client.session_transaction() as current_session:
            if premium:
                current_session["premium_active"] = True
            if owner:
                current_session["owner_logged_in"] = True

    with (
        patch.object(app, "get_cached_dashboard_data", return_value=DASHBOARD_DATA),
        patch.object(app, "get_stock_universe", return_value=[]),
    ):
        return client.get(path)


def test_logged_out_homepage_uses_the_batch_one_public_order():
    response = render_home()
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    markers = (
        '<header class="public-header">',
        '<div class="card hero-card public-hero" id="investment-compass-card">',
        '<section class="hero-search-panel" id="stock-search"',
        '<section class="card product-steps" id="how-stockradar-works"',
        '<section class="card free-report-preview" id="free-report-preview"',
        '<div class="card premium-home-card" id="premium-decision-section">',
        '<div class="trust-strip" aria-label="How to use StockRadar">',
        '<div class="card newsletter-cta-card" id="newsletter-cta">',
        '<footer style=',
    )
    positions = [page.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_logged_out_homepage_has_compact_public_navigation_and_stock_search():
    page = render_home().get_data(as_text=True)

    assert '<body class="public-home" data-public-home="true">' in page
    assert 'aria-label="Primary navigation"' in page
    assert 'href="#stock-search">Search</a>' in page
    assert 'href="/how-it-works">How It Works</a>' in page
    assert 'href="/newsletter">Newsletter</a>' in page
    assert 'href="/upgrade">Premium</a>' in page
    assert "Search a stock or ETF" in page
    assert 'placeholder="Try Microsoft, Apple, SPY or MSFT"' in page
    assert "View free report" in page
    assert "Choose a company or fund you recognise." in page
    assert "See the current signal in plain English." in page
    assert "Learn what evidence and risks may matter next." in page

    assert "📊 AI Signals" not in page
    assert "🧠 Premium Watchlist" not in page
    assert "⚖️ Compare Stocks" not in page
    assert "Market News" not in page
    assert 'id="overview-section"' not in page
    assert 'aria-label="Current signal overview"' not in page
    assert "UK market status" not in page


def test_explicit_dashboard_tabs_keep_advanced_navigation_and_relocated_sections():
    page = render_home("/?tab=overview").get_data(as_text=True)

    assert '<body class="dashboard-view" data-public-home="false">' in page
    assert '<div class="sidebar">' in page
    assert "📊 AI Signals" in page
    assert "🌍 Impact Radar" in page
    assert "🧠 Premium Watchlist" in page
    assert "⚖️ Compare Stocks" in page
    assert "Market News" in page
    assert 'aria-label="Current signal overview"' in page
    assert 'id="overview-section"' in page
    assert "UK market status" in page
    assert 'id="signals-section"' in page
    assert 'id="radar-section"' in page


def test_premium_and_owner_root_sessions_keep_application_navigation():
    for response in (render_home(premium=True), render_home(owner=True)):
        page = response.get_data(as_text=True)
        assert response.status_code == 200
        assert '<body class="dashboard-view" data-public-home="false">' in page
        assert '<div class="sidebar">' in page
        assert '<header class="public-header">' not in page
        assert "Premium Active" in page


def test_non_entitled_session_can_still_open_the_public_homepage():
    client = app.app.test_client()
    with client.session_transaction() as current_session:
        current_session["premium_active"] = False
        current_session["premium_email"] = "free-reader@example.test"

    with (
        patch.object(app, "premium_entitlement_active", return_value=False),
        patch.object(app, "get_cached_dashboard_data", return_value=DASHBOARD_DATA),
        patch.object(app, "get_stock_universe", return_value=[]),
    ):
        response = client.get("/")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '<body class="public-home" data-public-home="true">' in page
    assert '<header class="public-header">' in page


def test_public_search_keeps_company_ticker_etf_and_index_resolution():
    page = render_home().get_data(as_text=True)

    assert "'MICROSOFT':'MSFT'" in page
    assert "'APPLE':'AAPL'" in page
    assert "'SPY':'SPY'" not in page  # SPY continues through the ticker fallback.
    assert "'S&P 500':'^GSPC'" in page
    assert "if(publicHome){if(/^[A-Z0-9.^-]{1,12}$/.test(query))" in page
    assert "No matching stock or ETF found." in page


def test_logged_out_homepage_only_renders_locked_premium_preview():
    page = render_home().get_data(as_text=True)

    assert "Locked Decision Brief preview" in page
    assert "A quick decision-support scan from the current StockRadar universe." not in page
    assert "Open Premium Watchlist</span>" not in page
    assert "Upgrade to Premium — £5/month" in page
