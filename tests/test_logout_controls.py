from unittest.mock import patch

from bs4 import BeautifulSoup
import pytest

import app


DASHBOARD_DATA = {
    "market_status": {
        "uk_status": "CLOSED",
        "uk_time": "00:00",
        "us_status": "CLOSED",
        "us_time": "00:00",
    }
}


def owner_client():
    client = app.app.test_client()
    with client.session_transaction() as current_session:
        current_session["owner_logged_in"] = True
    return client


def dashboard_response(client):
    with (
        patch.object(app, "get_cached_dashboard_data", return_value=DASHBOARD_DATA),
        patch.object(app, "get_stock_universe", return_value=[]),
    ):
        return client.get("/")


def logout_form(response, location):
    page = BeautifulSoup(response.get_data(as_text=True), "html.parser")
    form = page.select_one(
        f'form[data-stockradar-logout-control="{location}"]'
    )
    assert form is not None
    return page, form


def assert_valid_logout_form(page, form):
    assert form.get("method", "").lower() == "post"
    assert form.get("action") == "/logout"
    assert form.select_one('button[type="submit"]') is not None
    token = form.select_one('input[type="hidden"][name="csrf_token"]')
    assert token is not None
    assert token.get("value")
    assert page.select_one('a[href="/logout"]') is None
    assert form.get("onclick") is None
    return token["value"]


def test_logout_navigation_records_cannot_render_as_get_links():
    logout_items = [
        item
        for item in app.STOCKRADAR_NAVIGATION_ITEMS
        if item.get("logout") is True
    ]

    assert {item["id"] for item in logout_items} == {
        "logout-owner",
        "logout-premium",
    }
    assert all("href" not in item for item in logout_items)
    assert app.STOCKRADAR_LOGOUT_CONTROL_TEMPLATE.count("<form") == 1
    assert 'method="post"' in app.STOCKRADAR_LOGOUT_CONTROL_TEMPLATE
    assert "url_for('logout')" in app.STOCKRADAR_LOGOUT_CONTROL_TEMPLATE
    assert 'name="csrf_token"' in app.STOCKRADAR_LOGOUT_CONTROL_TEMPLATE


@pytest.mark.parametrize(
    ("route", "location"),
    (
        ("/", "app-header"),
        ("/upgrade", "app-header"),
    ),
)
def test_desktop_and_mobile_navigation_use_the_shared_post_control(route, location):
    app.app.config["WTF_CSRF_ENABLED"] = True
    client = owner_client()

    response = dashboard_response(client) if route == "/" else client.get(route)

    assert response.status_code == 200
    page, form = logout_form(response, location)
    token = assert_valid_logout_form(page, form)
    assert page.select_one("[data-stockradar-menu-toggle]") is not None
    assert form.find_parent(attrs={"data-stockradar-menu": True}) is not None

    submitted = client.post(form["action"], data={"csrf_token": token})
    assert submitted.status_code == 302
    assert submitted.headers["Location"] == "/"
    with client.session_transaction() as current_session:
        assert dict(current_session) == {}


def test_premium_navigation_uses_post_control_with_csrf():
    app.app.config["WTF_CSRF_ENABLED"] = True
    client = app.app.test_client()
    with client.session_transaction() as current_session:
        current_session.update(
            {
                "premium_active": True,
                "stripe_customer_id": "cus_logout_test",
                "stripe_subscription_id": "sub_logout_test",
                "premium_email": "reader@example.test",
            }
        )

    with patch.object(
        app,
        "premium_entitlement_record",
        return_value={"premium_active": True, "entitlement_version": 1},
    ):
        response = client.get("/upgrade")

    page, form = logout_form(response, "app-header")
    assert form.get_text(" ", strip=True) == "End Premium Session"
    assert_valid_logout_form(page, form)


def test_logout_get_and_invalid_csrf_are_rejected():
    app.app.config["WTF_CSRF_ENABLED"] = True
    client = owner_client()
    dashboard_response(client)

    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 400
    assert client.post(
        "/logout",
        data={"csrf_token": "invalid-token"},
    ).status_code == 400
    with client.session_transaction() as current_session:
        assert current_session["owner_logged_in"] is True


def test_valid_logout_clears_owner_premium_and_other_session_values():
    app.app.config["WTF_CSRF_ENABLED"] = True
    client = owner_client()
    response = dashboard_response(client)
    page, form = logout_form(response, "app-header")
    token = assert_valid_logout_form(page, form)

    with client.session_transaction() as current_session:
        current_session.update(
            {
                "premium_active": True,
                "stripe_customer_id": "cus_sensitive",
                "stripe_subscription_id": "sub_sensitive",
                "premium_email": "reader@example.test",
                "entitlement_version": 4,
                "unrelated_marker": "clear-me-too",
            }
        )

    response = client.post("/logout", data={"csrf_token": token})

    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    with client.session_transaction() as current_session:
        assert dict(current_session) == {}


def test_logout_control_keeps_responsive_navigation_styling():
    assert ".public-nav-logout-form" in app.STOCKRADAR_HEADER_NAVIGATION_TEMPLATE
    assert ".nav-logout-form" in app.html
    assert "@media(max-width:700px)" in app.STOCKRADAR_HEADER_NAVIGATION_TEMPLATE
    assert "@media(max-width:900px)" in app.html
    assert "data-stockradar-menu" in app.STOCKRADAR_HEADER_NAVIGATION_TEMPLATE
    assert "{{ stockradar_header_navigation('app') | safe }}" in app.html
