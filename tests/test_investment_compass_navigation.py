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


def render_dashboard(path="/", owner=False, premium=False):
    client = app.app.test_client()
    if owner or premium:
        with client.session_transaction() as current_session:
            if owner:
                current_session["owner_logged_in"] = True
            if premium:
                current_session["premium_active"] = True

    with (
        patch.object(app, "get_cached_dashboard_data", return_value=DASHBOARD_DATA),
        patch.object(app, "get_stock_universe", return_value=[]),
        patch.object(app, "premium_entitlement_active", return_value=False),
    ):
        return client.get(path)


def compass_navigation_count(page):
    return page.count('data-nav-id="investment-compass"')


def test_shared_navigation_definition_contains_one_investment_compass_item():
    compass_items = [
        item
        for item in app.STOCKRADAR_NAVIGATION_ITEMS
        if item["id"] == "investment-compass"
    ]

    assert compass_items == [
        {
            "id": "investment-compass",
            "section": "Main Menu",
            "label": "Investment Compass",
            "href": "/beginner",
            "icon": "🌱",
            "locations": ("public", "dashboard", "app"),
            "access": "all",
        }
    ]


def test_signed_out_desktop_and_mobile_navigation_share_one_compass_link():
    page = render_dashboard().get_data(as_text=True)

    assert compass_navigation_count(page) == 1
    assert (
        'data-nav-id="investment-compass" href="/beginner">'
        "Investment Compass</a>"
    ) in page
    assert 'data-stockradar-primary-nav="true"' in page
    assert 'data-stockradar-menu-toggle' in page
    assert 'aria-controls="stockradar-primary-menu"' in page
    assert 'aria-expanded="false"' in page


def test_entitled_dashboard_navigation_contains_compass_once_and_all_tools():
    for response in (
        render_dashboard(owner=True),
        render_dashboard(premium=True),
    ):
        page = response.get_data(as_text=True)

        assert compass_navigation_count(page) == 1
        assert 'href="/beginner">🌱 Investment Compass</a>' in page
        assert 'href="/?tab=overview">🏠 Overview</a>' in page
        assert 'href="/?tab=signals">📊 AI Signals</a>' in page
        assert 'href="/?tab=watchlist">📋 AI Watchlist</a>' in page
        assert 'href="/premium-watchlist">🧠 Premium Watchlist' in page
        assert 'href="/compare">⚖️ Compare Stocks' in page
        assert 'href="/portfolio-fit">🧩 Portfolio Builder' in page
        assert 'href="/universe">🌍 Stock Universe</a>' in page
        assert 'href="/manage-subscription">Manage Subscription</a>' in page
        assert 'aria-controls="dashboard-primary-menu"' in page


def test_signed_out_free_and_premium_compass_access_remains_public():
    anonymous = app.app.test_client()
    assert anonymous.get("/beginner").status_code == 200

    free_client = app.app.test_client()
    with free_client.session_transaction() as current_session:
        current_session["premium_active"] = False
        current_session["premium_email"] = "free-reader@example.test"
    with patch.object(app, "premium_entitlement_active", return_value=False):
        free_page = free_client.get("/beginner")
        locked_portfolio = free_client.get("/portfolio-fit")
        locked_compare = free_client.get("/compare?symbol_a=AAPL&symbol_b=MSFT")

    assert free_page.status_code == 200
    assert "Investment Compass — StockRadar" in free_page.get_data(as_text=True)
    assert "Upgrade to unlock portfolio fit reviews" in locked_portfolio.get_data(as_text=True)
    assert "Locked Premium teaser" in locked_compare.get_data(as_text=True)

    premium_client = app.app.test_client()
    with premium_client.session_transaction() as current_session:
        current_session["premium_active"] = True
    assert premium_client.get("/beginner").status_code == 200
    assert "Upgrade to unlock portfolio fit reviews" not in premium_client.get(
        "/portfolio-fit"
    ).get_data(as_text=True)


def test_relevant_standalone_pages_use_the_same_app_navigation():
    client = app.app.test_client()

    for route in (
        "/beginner",
        "/upgrade",
        "/compare",
        "/premium-watchlist",
        "/portfolio-fit",
    ):
        response = client.get(route)
        page = response.get_data(as_text=True)

        assert response.status_code == 200
        assert compass_navigation_count(page) == 1
        assert 'href="/beginner">Investment Compass</a>' in page


def test_stock_report_uses_the_same_app_navigation():
    chart_data = {
        "ok": False,
        "labels": [],
        "prices": [],
        "start_price": "—",
        "end_price": "—",
        "change_amount": "—",
        "change_percent": "—",
        "direction": "hold",
        "error": "Test data unavailable",
    }
    dividend_context = {
        "income_status": app.INCOME_STATUS_UNAVAILABLE,
        "is_etf": False,
        "dividend_label": "Dividend",
        "dividend_yield": "Not available",
        "annual_dividend": "Not available",
        "ex_dividend_date": "Not available",
        "payout_ratio": "Not available",
        "fundamentals": [],
        "no_data_message": "Dividend data is temporarily unavailable.",
        "beginner_explanation": "Dividend data is temporarily unavailable.",
        "dividend_frequency_note": "Confirm payment details with the company.",
        "risk_note": "Dividend data is educational only.",
        "source_note": "Source data is currently unavailable.",
    }

    with (
        patch.object(app, "stock_history", return_value=chart_data),
        patch.object(app, "stock_lifetime_growth", return_value=chart_data),
        patch.object(app, "get_dividend_context", return_value=dividend_context),
        patch.object(app, "premium_entitlement_active", return_value=False),
    ):
        response = app.app.test_client().get("/stock/AAPL")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert compass_navigation_count(page) == 1
    assert 'href="/beginner">Investment Compass</a>' in page


def test_mobile_menu_is_keyboard_touch_and_viewport_safe():
    header_component = app.STOCKRADAR_HEADER_NAVIGATION_TEMPLATE
    dashboard_template = app.html
    navigation_script = app.stockradar_navigation_script()

    for source in (header_component, dashboard_template):
        assert "min-height:44px" in source
        assert "100dvh" in source
        assert "overflow-x:hidden" in source
        assert "overflow-y:auto" in source
        assert "env(safe-area-inset-top)" in source
        assert "env(safe-area-inset-bottom)" in source

    assert "@media(display-mode:standalone)" in header_component
    assert "@media(display-mode:standalone)" in dashboard_template
    assert "event.key==='Escape'" in navigation_script
    assert "link.addEventListener('click'" in navigation_script
    assert "button.focus()" in navigation_script


def test_navigation_ids_are_unique_and_existing_routes_resolve():
    navigation_ids = [item["id"] for item in app.STOCKRADAR_NAVIGATION_ITEMS]
    assert len(navigation_ids) == len(set(navigation_ids))

    client = app.app.test_client()
    for route in (
        "/beginner",
        "/portfolio-fit",
        "/compare",
        "/premium-watchlist",
        "/universe",
        "/upgrade",
        "/login",
    ):
        assert client.get(route).status_code == 200
