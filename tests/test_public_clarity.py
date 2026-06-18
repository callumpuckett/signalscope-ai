from unittest.mock import patch

import app


def test_homepage_explains_product_and_has_primary_calls_to_action():
    dashboard_data = {
        "market_status": {
            "uk_status": "CLOSED",
            "uk_time": "00:00",
            "us_status": "CLOSED",
            "us_time": "00:00",
        }
    }

    with (
        patch.object(app, "get_cached_dashboard_data", return_value=dashboard_data),
        patch.object(app, "get_stock_universe", return_value=[]),
    ):
        response = app.app.test_client().get("/")

    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "AI-powered stock market research dashboard" in page
    assert "stock signals, risk summaries, chart context and portfolio-fit tools" in page
    assert "StockRadar is not financial advice" in page
    assert (
        "StockRadar is currently in early access. Premium features and support "
        "processes are still being improved."
    ) in page
    assert 'href="/universe">Explore Stocks</a>' in page
    assert 'href="/upgrade">Unlock Premium</a>' in page
    assert 'href="/feedback">Send Feedback</a>' in page


def test_upgrade_page_clearly_lists_price_and_premium_tools():
    response = app.app.test_client().get("/upgrade")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "£5" in page
    assert "Premium Decision Panels" in page
    assert "Premium Watchlist Intelligence" in page
    assert "Portfolio Fit Checker" in page
    assert "Educational only." in page
    assert (
        "StockRadar is currently in early access. Premium features and support "
        "processes are still being improved."
    ) in page
    assert "£5/month early access premium subscription." in page
    assert 'href="/manage-subscription">Manage Subscription</a>' in page
    assert 'href="/feedback">Send Feedback</a>' in page
    assert 'href="/risk-disclaimer"' in page
