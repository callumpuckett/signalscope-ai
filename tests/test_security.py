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


def test_verified_paid_stripe_session_unlocks_premium():
    paid_session = SimpleNamespace(payment_status="paid", status="complete")

    with (
        patch.object(app, "stripe_checkout_configured", return_value=True),
        patch.object(app.stripe.checkout.Session, "retrieve", return_value=paid_session) as retrieve,
    ):
        with app.app.test_client() as client:
            response = client.get("/checkout-success?session_id=cs_test_paid")

            with client.session_transaction() as current_session:
                assert current_session.get("owner_logged_in") is True

    retrieve.assert_called_once_with("cs_test_paid")
    assert response.status_code == 200
    assert b"Premium activated" in response.data


def test_unpaid_stripe_session_does_not_unlock_premium():
    unpaid_session = {"payment_status": "unpaid", "status": "open"}

    with (
        patch.object(app, "stripe_checkout_configured", return_value=True),
        patch.object(app.stripe.checkout.Session, "retrieve", return_value=unpaid_session),
    ):
        with app.app.test_client() as client:
            response = client.get("/checkout-success?session_id=cs_test_unpaid")

            with client.session_transaction() as current_session:
                assert current_session.get("owner_logged_in") is not True

    assert response.status_code == 400
    assert b"Payment pending" in response.data


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
        "https://signalscope-ai-1-0v3g.onrender.com/"
        "checkout-success?session_id={CHECKOUT_SESSION_ID}"
    )
    assert app.DEFAULT_STRIPE_CANCEL_URL == (
        "https://signalscope-ai-1-0v3g.onrender.com/upgrade"
    )

    monkeypatch.setenv("STRIPE_SUCCESS_URL", "https://example.test/override")
    assert app.configured_url(
        "STRIPE_SUCCESS_URL", app.DEFAULT_STRIPE_SUCCESS_URL
    ) == (
        "https://example.test/override"
    )
