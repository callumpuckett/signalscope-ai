import pytest
from flask import Flask, session
from types import SimpleNamespace
from unittest.mock import patch

import app


def test_production_cookie_has_secure_httponly_and_samesite_lax():
    test_app = Flask(__name__)
    app.configure_session_security(test_app, "s" * 32, production=True)

    @test_app.route("/set-session")
    def set_session():
        session["owner_logged_in"] = True
        return "ok"

    response = test_app.test_client().get("/set-session")
    cookie = response.headers["Set-Cookie"]

    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_local_cookie_does_not_require_https():
    test_app = Flask(__name__)
    app.configure_session_security(test_app, "", production=False)

    assert test_app.config["SESSION_COOKIE_SECURE"] is False
    assert test_app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert test_app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_production_requires_strong_secret():
    test_app = Flask(__name__)

    with pytest.raises(RuntimeError):
        app.configure_session_security(test_app, "too-short", production=True)


def test_direct_checkout_success_does_not_unlock_premium():
    with app.app.test_client() as client:
        response = client.get("/checkout-success")

        with client.session_transaction() as current_session:
            assert current_session.get("owner_logged_in") is not True

    assert response.status_code in {400, 503}


def test_checkout_success_without_session_id_does_not_unlock_premium():
    with patch.object(app, "stripe_checkout_configured", return_value=True):
        with app.app.test_client() as client:
            response = client.get("/checkout-success")

            with client.session_transaction() as current_session:
                assert current_session.get("owner_logged_in") is not True

    assert response.status_code == 400


def test_paid_status_alone_does_not_unlock_premium():
    paid_session = SimpleNamespace(
        id="cs_test_paid",
        payment_status="paid",
        status="complete",
    )

    with (
        patch.object(app, "stripe_checkout_configured", return_value=True),
        patch.object(app, "STRIPE_SECRET_KEY", "sk_test_configured"),
        patch.object(app.stripe.checkout.Session, "retrieve", return_value=paid_session) as retrieve,
    ):
        with app.app.test_client() as client:
            response = client.get("/checkout-success?session_id=cs_test_paid")

            with client.session_transaction() as current_session:
                assert current_session.get("premium_active") is not True
                assert current_session.get("owner_logged_in") is not True

    retrieve.assert_called_once_with(
        "cs_test_paid",
        expand=["line_items.data.price.product"],
    )
    assert response.status_code == 400
    assert b"could not be verified" in response.data


def test_unpaid_stripe_session_does_not_unlock_premium():
    unpaid_session = {"payment_status": "unpaid", "status": "open"}

    with (
        patch.object(app, "stripe_checkout_configured", return_value=True),
        patch.object(app, "STRIPE_SECRET_KEY", "sk_test_configured"),
        patch.object(app.stripe.checkout.Session, "retrieve", return_value=unpaid_session),
    ):
        with app.app.test_client() as client:
            response = client.get("/checkout-success?session_id=cs_test_unpaid")

            with client.session_transaction() as current_session:
                assert current_session.get("owner_logged_in") is not True

    assert response.status_code == 400
    assert b"could not be verified" in response.data


def test_logout_clears_access_and_redirects_without_error():
    with app.app.test_client() as client:
        with client.session_transaction() as current_session:
            current_session["owner_logged_in"] = True

        response = client.get("/logout")

        with client.session_transaction() as current_session:
            assert current_session.get("owner_logged_in") is not True

    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_ai_recommendations_redirects_without_error():
    response = app.app.test_client().get("/ai-recommendations")

    assert response.status_code == 302
    assert response.headers["Location"] == "/?tab=watchlist"


def test_production_url_defaults_and_environment_overrides(monkeypatch):
    assert app.PRODUCTION_BASE_URL == "https://www.stockradarhq.com"
    assert app.RENDER_FALLBACK_BASE_URL == (
        "https://signalscope-ai-1-0v3g.onrender.com"
    )
    assert app.DEFAULT_STRIPE_SUCCESS_URL == (
        "https://www.stockradarhq.com/"
        "checkout-success?session_id={CHECKOUT_SESSION_ID}"
    )
    assert app.DEFAULT_STRIPE_CANCEL_URL == (
        "https://www.stockradarhq.com/upgrade"
    )

    monkeypatch.setenv("STRIPE_SUCCESS_URL", "https://example.test/override")
    assert app.configured_url(
        "STRIPE_SUCCESS_URL", app.DEFAULT_STRIPE_SUCCESS_URL
    ) == (
        "https://example.test/override"
    )


def test_checkout_disabled_returns_safe_400_without_creating_session():
    with (
        patch.object(app, "PREMIUM_PAYMENTS_ENABLED", False),
        patch.object(app.stripe.checkout.Session, "create") as create_session,
    ):
        response = app.app.test_client().post("/create-checkout-session")

    assert response.status_code == 400
    assert b"Checkout unavailable" in response.data
    assert b"no payment was started" in response.data
    create_session.assert_not_called()


def test_checkout_requires_flag_stripe_and_both_credentials():
    configurations = [
        (False, object(), "sk_test", "price_test"),
        (True, None, "sk_test", "price_test"),
        (True, object(), "", "price_test"),
        (True, object(), "sk_test", ""),
    ]

    for enabled, stripe_module, secret_key, price_id in configurations:
        with (
            patch.object(app, "PREMIUM_PAYMENTS_ENABLED", enabled),
            patch.object(app, "stripe", stripe_module),
            patch.object(app, "STRIPE_SECRET_KEY", secret_key),
            patch.object(app, "STRIPE_PRICE_ID", price_id),
        ):
            assert app.stripe_checkout_configured() is False


def test_enabled_checkout_uses_official_success_and_cancel_urls():
    checkout_session = SimpleNamespace(url="https://checkout.stripe.test/session")

    with (
        patch.object(app, "PREMIUM_PAYMENTS_ENABLED", True),
        patch.object(app, "STRIPE_SECRET_KEY", "sk_test_present"),
        patch.object(app, "STRIPE_PRICE_ID", "price_test_present"),
        patch.object(
            app.stripe.checkout.Session,
            "create",
            return_value=checkout_session,
        ) as create_session,
    ):
        response = app.app.test_client().post("/create-checkout-session")

    assert response.status_code == 303
    assert response.headers["Location"] == checkout_session.url
    create_session.assert_called_once()
    checkout_args = create_session.call_args.kwargs
    assert checkout_args["mode"] == "subscription"
    assert checkout_args["line_items"] == [
        {"price": "price_test_present", "quantity": 1}
    ]
    assert checkout_args["success_url"] == (
        "https://www.stockradarhq.com/"
        "checkout-success?session_id={CHECKOUT_SESSION_ID}"
    )
    assert checkout_args["cancel_url"] == "https://www.stockradarhq.com/upgrade"
    assert checkout_args["allow_promotion_codes"] is True
    assert len(checkout_args["client_reference_id"]) == 64
    assert checkout_args["metadata"]["stockradar_checkout_flow"] == (
        app.CHECKOUT_FLOW_NAME
    )
    assert checkout_args["metadata"]["stockradar_price_id"] == (
        "price_test_present"
    )
    assert checkout_args["subscription_data"]["metadata"] == (
        checkout_args["metadata"]
    )


def test_unbound_paid_checkout_does_not_grant_premium_or_owner_access():
    paid_session = SimpleNamespace(payment_status="paid", status="complete")

    with (
        patch.object(app, "stripe_checkout_configured", return_value=True),
        patch.object(app, "STRIPE_SECRET_KEY", "sk_test_configured"),
        patch.object(app.stripe.checkout.Session, "retrieve", return_value=paid_session),
    ):
        with app.app.test_client() as client:
            response = client.get("/checkout-success?session_id=cs_test_paid")
            admin_response = client.get("/admin/newsletter-preview")

            with client.session_transaction() as current_session:
                assert current_session.get("premium_active") is not True
                assert current_session.get("owner_logged_in") is not True

    assert response.status_code == 400
    assert admin_response.status_code == 302
    assert "/login" in admin_response.headers["Location"]
