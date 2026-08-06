import inspect
from unittest.mock import patch

from flask import Response

import app


DASHBOARD_DATA = {
    "market_status": {
        "uk_status": "CLOSED",
        "uk_time": "00:00",
        "us_status": "CLOSED",
        "us_time": "00:00",
    }
}
STOCK_CHART_DATA = {
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
STOCK_LIFETIME_DATA = {
    "start_price": "—",
    "end_price": "—",
    "change_amount": "—",
    "change_percent": "—",
    "direction": "hold",
}
DIVIDEND_CONTEXT = {
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


def render_dashboard(owner=False, premium=False):
    client = app.app.test_client()
    if owner or premium:
        with client.session_transaction() as current_session:
            if owner:
                current_session["owner_logged_in"] = True
            if premium:
                current_session["stripe_subscription_id"] = "sub_test_active"

    with (
        patch.object(app, "get_cached_dashboard_data", return_value=DASHBOARD_DATA),
        patch.object(app, "get_stock_universe", return_value=[]),
        patch.object(
            app,
            "premium_entitlement_record",
            return_value=(
                {"premium_active": True, "entitlement_version": 1}
                if premium
                else None
            ),
        ),
    ):
        return client.get("/")


def render_stock_page():
    with (
        patch.object(app, "stock_history", return_value=STOCK_CHART_DATA),
        patch.object(app, "stock_lifetime_growth", return_value=STOCK_LIFETIME_DATA),
        patch.object(app, "get_dividend_context", return_value=DIVIDEND_CONTEXT),
        patch.object(app, "premium_entitlement_record", return_value=None),
    ):
        return app.app.test_client().get("/stock/AAPL")


def marker_count(response):
    return response.get_data(as_text=True).count(app.NEWSLETTER_SIDE_TAB_MARKER)


def assert_newsletter_tab(response, expected=True):
    page = response.get_data(as_text=True)
    assert marker_count(response) == (1 if expected else 0)
    if expected:
        assert 'href="/newsletter"' in page
        assert 'href="/newsletter/latest"' not in page
        assert (
            'aria-label="Read and subscribe to the StockRadar newsletter"'
            in page
        )


def test_shared_component_contains_accessible_desktop_and_mobile_styles():
    component = app.newsletter_side_tab()

    assert component == app.NEWSLETTER_SIDE_TAB_COMPONENT
    assert component.count(app.NEWSLETTER_SIDE_TAB_MARKER) == 1
    assert 'href="/newsletter"' in component
    assert 'href="/newsletter/latest"' not in component
    assert 'aria-label="Read and subscribe to the StockRadar newsletter"' in component
    assert "position: fixed" in component
    assert "z-index: 10000" in component
    assert "writing-mode: vertical-rl" in component
    assert ".stockradar-newsletter-tab:focus-visible" in component
    assert "@media (max-width: 700px)" in component
    assert "writing-mode: horizontal-tb" in component
    assert "env(safe-area-inset-right)" in component
    assert "env(safe-area-inset-bottom)" in component
    assert "border-radius: 999px" in component


def test_homepage_template_renders_side_tab_once_as_a_body_child():
    response = render_dashboard()
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert_newsletter_tab(response)
    assert page.index(app.NEWSLETTER_SIDE_TAB_MARKER) < page.index("</body>")
    assert "{{ newsletter_side_tab() | safe }}" in app.html


def test_stock_report_template_renders_side_tab_once():
    response = render_stock_page()

    assert response.status_code == 200
    assert_newsletter_tab(response)
    assert "{{ newsletter_side_tab() | safe }}" in app.stock_detail_html


def test_normal_public_templates_render_side_tab_once():
    client = app.app.test_client()

    for route in (
        "/universe",
        "/upgrade",
        "/how-it-works",
        "/privacy",
        "/terms",
        "/refund-policy",
        "/risk-disclaimer",
        "/feedback",
        "/contact",
    ):
        response = client.get(route)
        assert response.status_code == 200
        assert_newsletter_tab(response)


def test_equivalent_public_research_templates_render_side_tab_once():
    client = app.app.test_client()

    for route in (
        "/compare",
        "/premium-decision/AAPL",
        "/premium-watchlist",
        "/portfolio-fit",
        "/beginner",
        "/manage-subscription",
    ):
        response = client.get(route)
        assert response.status_code == 200
        assert_newsletter_tab(response)


def test_newsletter_templates_do_not_render_side_tab():
    client = app.app.test_client()

    assert_newsletter_tab(client.get("/newsletter"), expected=False)
    assert_newsletter_tab(client.get("/newsletter/rss"), expected=False)
    assert_newsletter_tab(client.get("/newsletter/archive"), expected=False)

    with (
        patch.object(app, "load_or_generate_latest_newsletter_issue", return_value={}),
        patch.object(
            app,
            "newsletter_issue_for_website_display",
            return_value={"draft": {}, "metadata": {}},
        ),
        patch.object(app, "render_template_string", return_value="<html><body>Newsletter</body></html>"),
    ):
        assert_newsletter_tab(client.get("/newsletter/latest"), expected=False)


def test_newsletter_landing_remains_the_read_and_subscribe_hub():
    response = app.app.test_client().get("/newsletter")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert '<form method="POST" action="/newsletter">' in page
    assert 'type="email" name="email"' in page
    assert ">Join Free</button>" in page
    assert 'href="/newsletter/latest">Read the latest issue</a>' in page


def test_login_logout_owner_and_checkout_templates_do_not_render_side_tab():
    client = app.app.test_client()

    assert_newsletter_tab(client.get("/login"), expected=False)
    assert_newsletter_tab(client.get("/logout"), expected=False)
    assert_newsletter_tab(client.get("/create-checkout-session"), expected=False)
    assert_newsletter_tab(client.get("/checkout-success"), expected=False)

    with client.session_transaction() as current_session:
        current_session["owner_logged_in"] = True
    assert_newsletter_tab(client.get("/owner"), expected=False)


def test_operational_json_and_error_routes_do_not_render_side_tab():
    client = app.app.test_client()

    for route in (
        "/admin/newsletter-preview",
        "/newsletter/cron/send",
        "/health",
        "/healthz",
        "/deploy-version",
        "/this-page-does-not-exist",
    ):
        assert_newsletter_tab(client.get(route), expected=False)

    assert_newsletter_tab(client.post("/stripe-webhook", data=b"{}"), expected=False)

    with (
        patch.object(app, "get_cached_dashboard_data", return_value=DASHBOARD_DATA),
        patch.object(app, "get_stock_universe", return_value=[]),
    ):
        api_response = client.get("/api/market-news")
    assert api_response.is_json
    assert_newsletter_tab(api_response, expected=False)


def test_security_header_hook_does_not_mutate_response_bodies():
    source = inspect.getsource(app.add_security_headers)
    assert "inject_newsletter" not in source
    assert not hasattr(app, "inject_newsletter_tab")
    assert not hasattr(app, "should_inject_newsletter_tab")

    with app.app.test_request_context("/test"):
        html_response = Response(
            "<html><head></head><body>Unchanged</body></html>",
            content_type="text/html",
        )
        original_html = html_response.get_data()
        result = app.add_security_headers(html_response)

    assert result.get_data() == original_html
    assert app.NEWSLETTER_SIDE_TAB_MARKER.encode() not in result.data
    assert result.headers["X-Content-Type-Options"] == "nosniff"


def test_non_html_and_streamed_responses_remain_unchanged():
    with app.app.test_request_context("/test"):
        text_response = Response("plain response", content_type="text/plain")
        original_text = text_response.get_data()
        app.add_security_headers(text_response)

        streamed_response = Response(
            (chunk for chunk in ("first", "second")),
            content_type="text/html",
        )
        assert streamed_response.is_streamed is True
        app.add_security_headers(streamed_response)

    assert text_response.get_data() == original_text
    assert streamed_response.get_data(as_text=True) == "firstsecond"


def test_shared_footer_omits_newsletter_and_manage_subscription_links():
    footer = app.disclaimer_footer()

    assert 'href="/newsletter"' not in footer
    assert 'href="/manage-subscription"' not in footer


def test_shared_footer_keeps_other_legal_and_support_links():
    footer = app.disclaimer_footer()

    for route in (
        "/how-it-works",
        "/privacy",
        "/terms",
        "/refund-policy",
        "/risk-disclaimer",
        "/feedback",
        "/contact",
    ):
        assert f'href="{route}"' in footer


def test_account_manage_subscription_is_not_rendered_for_anonymous_users():
    page = render_dashboard().get_data(as_text=True)

    assert 'data-account-manage-subscription="true"' not in page
    assert 'href="/manage-subscription"' not in page


def test_account_manage_subscription_follows_owner_account_control():
    page = render_dashboard(owner=True).get_data(as_text=True)
    account_control = '<a class="nav-link pro-button" href="/owner">✅ Premium Active</a>'
    manage_link = (
        '<a class="nav-link account-manage-subscription" '
        'data-account-manage-subscription="true" '
        'href="/manage-subscription">Manage Subscription</a>'
    )

    assert manage_link in page
    assert page.index(account_control) < page.index(manage_link) < page.index('action="/logout"')
    assert manage_link not in app.disclaimer_footer()


def test_account_manage_subscription_follows_premium_session_control():
    page = render_dashboard(premium=True).get_data(as_text=True)
    account_control = (
        '<a class="nav-link pro-button" '
        'href="/manage-subscription">✅ Premium Active</a>'
    )
    manage_link = (
        '<a class="nav-link account-manage-subscription" '
        'data-account-manage-subscription="true" '
        'href="/manage-subscription">Manage Subscription</a>'
    )

    assert manage_link in page
    assert page.index(account_control) < page.index(manage_link) < page.index('action="/logout"')
    assert manage_link not in app.disclaimer_footer()
