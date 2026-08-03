from unittest.mock import patch

from flask import Response

import app


HTML_PAGE = "<!doctype html><html><head><title>Page</title></head><body><main>Content</main></body></html>"
DASHBOARD_DATA = {
    "market_status": {
        "uk_status": "CLOSED",
        "uk_time": "00:00",
        "us_status": "CLOSED",
        "us_time": "00:00",
    }
}


def render_dashboard(owner=False, premium=False):
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
        return client.get("/")


def assert_newsletter_tab_absent(response):
    assert app.NEWSLETTER_TAB_MARKER not in response.get_data(as_text=True)


def test_newsletter_tab_appears_once_on_homepage_with_accessible_latest_link():
    response = render_dashboard()
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert page.count(app.NEWSLETTER_TAB_MARKER) == 1
    assert 'href="/newsletter/latest"' in page
    assert 'aria-label="Read the latest StockRadar newsletter"' in page
    assert page.index('id="stockradar-newsletter-tab-styles"') < page.index("</head>")
    assert page.index(app.NEWSLETTER_TAB_MARKER) < page.index("</body>")


def test_newsletter_tab_appears_on_a_normal_stock_route():
    with patch.dict(app.app.view_functions, {"stock_detail": lambda symbol: HTML_PAGE}):
        response = app.app.test_client().get("/stock/AAPL")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert page.count(app.NEWSLETTER_TAB_MARKER) == 1
    assert 'href="/newsletter/latest"' in page


def test_newsletter_tab_has_desktop_focus_and_mobile_safe_area_styles():
    page = render_dashboard().get_data(as_text=True)

    assert "position: fixed" in page
    assert "writing-mode: vertical-rl" in page
    assert ".stockradar-newsletter-tab:focus-visible" in page
    assert "@media (max-width: 700px)" in page
    assert "writing-mode: horizontal-tb" in page
    assert "env(safe-area-inset-right)" in page
    assert "env(safe-area-inset-bottom)" in page
    assert "border-radius: 999px" in page


def test_newsletter_tab_is_excluded_from_newsletter_and_login_pages():
    client = app.app.test_client()
    assert_newsletter_tab_absent(client.get("/newsletter"))
    assert_newsletter_tab_absent(client.get("/login"))

    with (
        patch.object(app, "load_or_generate_latest_newsletter_issue", return_value={}),
        patch.object(
            app,
            "newsletter_issue_for_website_display",
            return_value={"draft": {}, "metadata": {}},
        ),
        patch.object(app, "render_template_string", return_value=HTML_PAGE),
    ):
        assert_newsletter_tab_absent(client.get("/newsletter/latest"))


def test_newsletter_tab_is_excluded_from_checkout_error_and_operational_routes():
    client = app.app.test_client()

    for path in (
        "/create-checkout-session",
        "/checkout-success",
        "/admin/newsletter-preview",
        "/newsletter/cron/send",
        "/health",
        "/deploy-version",
        "/this-page-does-not-exist",
    ):
        assert_newsletter_tab_absent(client.get(path))

    assert_newsletter_tab_absent(client.post("/stripe-webhook", data=b"{}"))


def test_newsletter_tab_does_not_modify_non_html_responses():
    with app.app.test_request_context("/robots.txt"):
        response = Response("plain response", content_type="text/plain")
        original_data = response.get_data()
        original_length = response.content_length
        result = app.inject_newsletter_tab(response)

    assert result.get_data() == original_data
    assert result.content_length == original_length
    assert app.NEWSLETTER_TAB_MARKER.encode() not in result.data


def test_newsletter_tab_injection_is_idempotent():
    with app.app.test_request_context("/privacy"):
        response = Response(HTML_PAGE, content_type="text/html")
        app.inject_newsletter_tab(response)
        app.inject_newsletter_tab(response)
        page = response.get_data(as_text=True)

    assert page.count(app.NEWSLETTER_TAB_MARKER) == 1
    assert page.count('id="stockradar-newsletter-tab-styles"') == 1


def test_account_manage_subscription_is_not_rendered_for_logged_out_users():
    page = render_dashboard().get_data(as_text=True)

    assert 'data-account-manage-subscription="true"' not in page
    assert 'href="/manage-subscription"' in page  # Existing footer link remains.


def test_account_manage_subscription_follows_owner_account_control():
    page = render_dashboard(owner=True).get_data(as_text=True)
    account_control = '<a class="nav-link pro-button" href="/owner">✅ Premium Active</a>'
    manage_link = (
        '<a class="nav-link account-manage-subscription" '
        'data-account-manage-subscription="true" '
        'href="/manage-subscription">Manage Subscription</a>'
    )

    assert manage_link in page
    assert page.index(account_control) < page.index(manage_link) < page.index('href="/logout"')


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
    assert page.index(account_control) < page.index(manage_link) < page.index('href="/logout"')


def test_public_stock_visitor_does_not_receive_account_manage_subscription():
    with patch.dict(app.app.view_functions, {"stock_detail": lambda symbol: HTML_PAGE}):
        page = app.app.test_client().get("/stock/AAPL").get_data(as_text=True)

    assert 'data-account-manage-subscription="true"' not in page
