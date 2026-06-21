from unittest.mock import patch

import app


def test_upgrade_and_manage_subscription_are_clear_and_available():
    client = app.app.test_client()
    upgrade = client.get("/upgrade")
    manage = client.get("/manage-subscription")

    assert upgrade.status_code == 200
    assert manage.status_code == 200
    assert b"\xc2\xa35" in upgrade.data
    assert b"Cancel anytime" in upgrade.data
    assert b"StockRadar is not financial advice" in upgrade.data
    assert b"Cancellation stops future billing" in manage.data
    assert b"Cancel StockRadar Premium" in manage.data


def test_manage_subscription_uses_support_email_when_available(monkeypatch):
    monkeypatch.setattr(app, "SUPPORT_EMAIL", "support@example.test")

    response = app.app.test_client().get("/manage-subscription")

    assert response.status_code == 200
    assert b"support@example.test" in response.data


def test_premium_locked_routes_return_200_for_non_owner():
    client = app.app.test_client()

    for route in ("/premium-watchlist", "/premium-decision/NVDA", "/portfolio-fit"):
        response = client.get(route)
        assert response.status_code == 200


def test_premium_public_pages_do_not_promise_guaranteed_returns():
    client = app.app.test_client()

    for route in (
        "/upgrade",
        "/manage-subscription",
        "/premium-watchlist",
        "/premium-decision/NVDA",
        "/portfolio-fit",
    ):
        response = client.get(route)
        page = response.get_data(as_text=True).lower()

        assert response.status_code == 200
        assert "guaranteed returns" not in page


def test_checkout_disabled_is_safe_even_when_credentials_exist():
    with (
        patch.object(app, "PREMIUM_PAYMENTS_ENABLED", False),
        patch.object(app, "STRIPE_SECRET_KEY", "sk_test_present"),
        patch.object(app, "STRIPE_PRICE_ID", "price_test_present"),
        patch.object(app.stripe.checkout.Session, "create") as create_session,
    ):
        response = app.app.test_client().post("/create-checkout-session")

    assert response.status_code == 400
    assert b"no payment was started" in response.data
    create_session.assert_not_called()
