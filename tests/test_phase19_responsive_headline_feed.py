from unittest.mock import patch

import app


DASHBOARD_DATA = {
    "market_status": {
        "uk_status": "CLOSED",
        "uk_time": "00:00",
        "us_status": "CLOSED",
        "us_time": "00:00",
    },
    "live_headlines": [
        {
            "label": "LIVE NEWS",
            "article_url": "https://example.test/markets-live",
            "headline": "Markets assess a live test headline",
            "source": "Test Wire",
            "published_label": "Now",
            "impact_score": "72",
            "direction": "Watch technology",
            "stock_links": [
                {
                    "ticker": "MSFT",
                    "url": "/stock/MSFT",
                    "display_label": "Microsoft (MSFT)",
                    "signal_class": "hold",
                    "action_text": "HOLD",
                }
            ],
            "stock_links_total": 1,
        }
    ],
}


def render_home(data=DASHBOARD_DATA, path="/"):
    with (
        patch.object(app, "get_cached_dashboard_data", return_value=data),
        patch.object(app, "get_stock_universe", return_value=[]),
    ):
        return app.app.test_client().get(path)


def test_public_home_reuses_one_live_headline_feed_before_the_hero():
    response = render_home()
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert page.count('class="live-alert-strip"') == 1
    assert page.index('aria-label="Live market headlines"') < page.index(
        '<div class="card hero-card public-hero" id="investment-compass-card">'
    )
    assert 'id="marketNewsTrack"' in page
    assert 'class="live-alert-loop"' in page
    assert 'data-refresh-interval="300000"' in page
    assert "Markets assess a live test headline" in page
    assert 'href="https://example.test/markets-live"' in page
    assert 'href="/stock/MSFT"' in page
    assert 'id="overview-section"' not in page
    assert 'aria-label="Current signal overview"' not in page


def test_public_home_keeps_existing_empty_feed_fallback():
    data = {**DASHBOARD_DATA, "live_headlines": []}
    response = render_home(data)
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert page.count('class="live-alert-strip"') == 1
    assert (
        '<div class="live-news-empty" id="marketNewsEmpty">Market headlines temporarily '
        "unavailable. StockRadar will refresh when the feed reconnects.</div>"
    ) in page


def test_dashboard_keeps_the_same_shared_feed_and_quick_search():
    page = render_home(path="/?tab=overview").get_data(as_text=True)

    assert page.count('class="live-alert-strip"') == 1
    assert 'aria-label="Quick search and navigation"' in page
    assert '<label for="smartSearchInput">Quick Search</label>' in page


def test_mobile_css_keeps_the_shared_feed_visible_and_clipped():
    assert app.html.count('<div class="live-alert-strip"') == 1
    assert ".live-alert-strip{position:relative" in app.html
    assert ".live-alert-strip{width:100%;}" in app.html
    assert ".live-alert-track{overflow:hidden;white-space:nowrap" in app.html
    assert "@media(prefers-reduced-motion:reduce)" in app.html

    mobile_css = app.html.split("@media(max-width:900px)", 1)[1].split("</style>", 1)[0]
    assert ".live-alert-strip{display:none" not in mobile_css
    assert ".live-alert-track{display:none" not in mobile_css


def test_public_headline_correction_leaves_phase_19_content_intact():
    page = render_home().get_data(as_text=True)

    assert "Learn to think like an investor." in page
    assert 'placeholder="Try Microsoft, Apple, SPY or MSFT"' in page
    assert "Choose a company or fund you recognise." in page
    assert "See a free report in action" in page
    assert "Trading apps show you the market. Premium helps you understand the signal." in page
    assert "StockRadar Weekly" in page
    assert 'id="market-news-section"' not in page
