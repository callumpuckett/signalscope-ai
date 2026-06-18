from unittest.mock import patch

import app


LEGAL_ROUTES = [
    "/privacy",
    "/terms",
    "/refund-policy",
    "/risk-disclaimer",
    "/manage-subscription",
    "/contact",
]


def test_legal_and_support_routes_return_200():
    client = app.app.test_client()

    for route in LEGAL_ROUTES:
        response = client.get(route)
        assert response.status_code == 200


def test_shared_footer_contains_legal_and_support_links():
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
    for route in LEGAL_ROUTES:
        assert f'href="{route}"' in page


def test_risk_disclaimer_contains_required_wording():
    response = app.app.test_client().get("/risk-disclaimer")

    assert (
        b"StockRadar provides educational and informational market research only. "
        b"It does not provide regulated financial advice, personalised investment "
        b"recommendations, brokerage services, or trade execution. Users are "
        b"responsible for their own investment decisions."
    ) in response.data


def test_upgrade_contains_short_research_disclaimer():
    response = app.app.test_client().get("/upgrade")

    assert (
        b"Premium access provides research tools and analysis only. "
        b"StockRadar is not financial advice."
    ) in response.data


def test_contact_uses_support_email_when_configured(monkeypatch):
    monkeypatch.setattr(app, "SUPPORT_EMAIL", "support@example.test")

    response = app.app.test_client().get("/contact")

    assert b"support@example.test" in response.data


def test_manage_subscription_uses_support_email_and_cancellation_subject(monkeypatch):
    monkeypatch.setattr(app, "SUPPORT_EMAIL", "support@example.test")

    response = app.app.test_client().get("/manage-subscription")

    assert response.status_code == 200
    assert b"support@example.test" in response.data
    assert b"Cancel StockRadar Premium" in response.data


def test_upgrade_links_to_manage_subscription():
    response = app.app.test_client().get("/upgrade")

    assert response.status_code == 200
    assert b'href="/manage-subscription"' in response.data
    assert b"Early access cancellations are handled through support" in response.data
