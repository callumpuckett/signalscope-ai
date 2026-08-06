import time
from unittest.mock import Mock

import pytest
from werkzeug.security import generate_password_hash

import app


@pytest.fixture(autouse=True)
def reset_security_state(monkeypatch):
    app.app.config.update(TESTING=True)
    with app.LOGIN_RATE_LIMIT_LOCK:
        app.LOGIN_RATE_LIMIT_STATE["ip"].clear()
        app.LOGIN_RATE_LIMIT_STATE["identity"].clear()
    monkeypatch.setattr(app, "OWNER_EMAIL", "owner@example.test")
    monkeypatch.setattr(app, "OWNER_PASSWORD", "")
    monkeypatch.setattr(
        app,
        "OWNER_PASSWORD_HASH",
        generate_password_hash("correct horse battery staple"),
    )
    yield
    with app.LOGIN_RATE_LIMIT_LOCK:
        app.LOGIN_RATE_LIMIT_STATE["ip"].clear()
        app.LOGIN_RATE_LIMIT_STATE["identity"].clear()


def valid_compass_form(**overrides):
    values = {
        "goal": "growth",
        "horizon": "10plus",
        "risk": "medium",
        "experience": "new",
        "style": "simple",
        "amount": "100",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    "user_agent",
    [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile",
    ],
)
@pytest.mark.parametrize(
    "field,payload",
    [
        ("amount", "<script>alert(1)</script>"),
        ("amount", '<img src=x onerror="alert(1)">'),
        ("amount", '" onfocus="alert(1)'),
        ("amount", "{{ 7 * 7 }}"),
        ("amount", "NaN"),
        ("amount", "Infinity"),
        ("amount", "-1"),
        ("amount", "1000001"),
        ("risk", '<svg onload="alert(1)">'),
    ],
)
def test_beginner_rejects_untrusted_payloads_without_reflection(
    field,
    payload,
    user_agent,
):
    response = app.app.test_client().post(
        "/beginner",
        data=valid_compass_form(**{field: payload}),
        headers={"User-Agent": user_agent},
    )
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert payload not in page
    assert "role=\"alert\"" in page
    assert 'id="beginner-result"' not in page


def test_beginner_normal_calculation_uses_safely_rendered_template():
    response = app.app.test_client().post(
        "/beginner",
        data=valid_compass_form(amount="100"),
    )
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "At around £100 per month" in page
    assert page.count('id="beginner-result"') == 1


def test_login_accepts_password_hash_and_rotates_session():
    client = app.app.test_client()
    with client.session_transaction() as current_session:
        current_session["pre_login_marker"] = "must-be-cleared"
        current_session["premium_active"] = True

    response = client.post(
        "/login",
        data={
            "email": "owner@example.test",
            "password": "correct horse battery staple",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/owner")
    with client.session_transaction() as current_session:
        assert dict(current_session) == {"owner_logged_in": True}


def test_login_plaintext_migration_fallback_preserves_owner_access(monkeypatch):
    monkeypatch.setattr(app, "OWNER_PASSWORD_HASH", "")
    monkeypatch.setattr(app, "OWNER_PASSWORD", "temporary migration password")

    response = app.app.test_client().post(
        "/login",
        data={
            "email": "owner@example.test",
            "password": "temporary migration password",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/owner")


def test_login_failure_is_generic_for_known_unknown_and_unconfigured(monkeypatch):
    client = app.app.test_client()
    responses = [
        client.post(
            "/login",
            data={"email": "owner@example.test", "password": "wrong"},
        ),
        client.post(
            "/login",
            data={"email": "unknown@example.test", "password": "wrong"},
        ),
    ]
    monkeypatch.setattr(app, "OWNER_EMAIL", "")
    monkeypatch.setattr(app, "OWNER_PASSWORD_HASH", "")
    responses.append(
        client.post(
            "/login",
            data={"email": "nobody@example.test", "password": "wrong"},
        )
    )

    for response in responses:
        assert response.status_code == 200
        assert b"Invalid email or password." in response.data
        assert b"not configured" not in response.data
        assert b"owner email" not in response.data.lower()


def test_login_rate_limit_returns_429_and_retry_after():
    client = app.app.test_client()
    statuses = []
    for _ in range(app.LOGIN_RATE_LIMIT_MAX_FAILURES):
        response = client.post(
            "/login",
            data={"email": "owner@example.test", "password": "wrong"},
        )
        statuses.append(response.status_code)

    assert statuses[:-1] == [200] * (app.LOGIN_RATE_LIMIT_MAX_FAILURES - 1)
    assert statuses[-1] == 429
    assert int(response.headers["Retry-After"]) > 0
    assert b"Too many login attempts" in response.data


def test_successful_login_resets_failure_counters():
    client = app.app.test_client()
    for _ in range(app.LOGIN_RATE_LIMIT_MAX_FAILURES - 1):
        assert client.post(
            "/login",
            data={"email": "owner@example.test", "password": "wrong"},
        ).status_code == 200

    assert client.post(
        "/login",
        data={
            "email": "owner@example.test",
            "password": "correct horse battery staple",
        },
    ).status_code == 302
    client.post("/logout")

    for _ in range(app.LOGIN_RATE_LIMIT_MAX_FAILURES - 1):
        assert client.post(
            "/login",
            data={"email": "owner@example.test", "password": "wrong"},
        ).status_code == 200


def test_proxy_fix_uses_only_the_trusted_rightmost_forwarded_hop():
    original_wsgi_app = app.app.wsgi_app
    app.configure_render_proxy(app.app, enabled=True)
    try:
        client = app.app.test_client()
        for attempt in range(app.LOGIN_RATE_LIMIT_MAX_FAILURES):
            response = client.post(
                "/login",
                data={
                    "email": f"unknown-{attempt}@example.test",
                    "password": "wrong",
                },
                headers={
                    "X-Forwarded-For": (
                        f"198.51.100.{attempt + 1}, 203.0.113.9"
                    )
                },
            )

        assert response.status_code == 429

        with app.LOGIN_RATE_LIMIT_LOCK:
            app.LOGIN_RATE_LIMIT_STATE["ip"].clear()
            app.LOGIN_RATE_LIMIT_STATE["identity"].clear()

        for attempt in range(app.LOGIN_RATE_LIMIT_MAX_FAILURES + 1):
            response = client.post(
                "/login",
                data={
                    "email": f"other-{attempt}@example.test",
                    "password": "wrong",
                },
                headers={
                    "X-Forwarded-For": (
                        f"198.51.100.1, 203.0.113.{attempt + 20}"
                    )
                },
            )
            assert response.status_code == 200
    finally:
        app.app.wsgi_app = original_wsgi_app


def checkout_session(checkout_intent, **overrides):
    created_at = int(time.time())
    price_id = overrides.pop("line_item_price", "price_premium")
    product_id = overrides.pop("line_item_product", "prod_premium")
    values = {
        "id": "cs_test_stockradar",
        "livemode": False,
        "mode": "subscription",
        "payment_status": "paid",
        "status": "complete",
        "subscription": "sub_stockradar",
        "customer": "cus_stockradar",
        "customer_details": {"email": "reader@example.test"},
        "created": created_at,
        "client_reference_id": app.checkout_intent_reference(checkout_intent),
        "metadata": {
            "stockradar_checkout_flow": app.CHECKOUT_FLOW_NAME,
            "stockradar_checkout_intent": checkout_intent,
            "stockradar_price_id": "price_premium",
            "stockradar_product_id": "prod_premium",
        },
        "line_items": {
            "data": [
                {
                    "price": {
                        "id": price_id,
                        "product": {"id": product_id},
                    }
                }
            ]
        },
    }
    values.update(overrides)
    return values


def configure_checkout(monkeypatch):
    monkeypatch.setattr(app, "PREMIUM_PAYMENTS_ENABLED", True)
    monkeypatch.setattr(app, "STRIPE_SECRET_KEY", "sk_test_configured")
    monkeypatch.setattr(app, "STRIPE_PRICE_ID", "price_premium")
    monkeypatch.setattr(app, "STRIPE_PRODUCT_ID", "prod_premium")


def set_checkout_intent(client, checkout_intent):
    with client.session_transaction() as current_session:
        current_session["checkout_intent"] = checkout_intent
        current_session["checkout_intent_created_at"] = time.time()
        current_session["pre_activation_marker"] = "must-be-cleared"


def test_correct_subscription_activates_premium_after_durable_save(monkeypatch):
    configure_checkout(monkeypatch)
    intent = "browser-bound-intent"
    verified = checkout_session(intent)
    retrieve = Mock(return_value=verified)
    monkeypatch.setattr(app.stripe.checkout.Session, "retrieve", retrieve)
    monkeypatch.setattr(
        app,
        "update_premium_entitlement",
        Mock(
            return_value={
                "premium_active": True,
                "entitlement_version": 4,
            }
        ),
    )
    client = app.app.test_client()
    set_checkout_intent(client, intent)

    response = client.get("/checkout-success?session_id=cs_test_stockradar")

    assert response.status_code == 200
    retrieve.assert_called_once_with(
        "cs_test_stockradar",
        expand=["line_items.data.price.product"],
    )
    with client.session_transaction() as current_session:
        assert current_session["premium_active"] is True
        assert current_session["stripe_customer_id"] == "cus_stockradar"
        assert current_session["stripe_subscription_id"] == "sub_stockradar"
        assert current_session["premium_email"] == "reader@example.test"
        assert current_session["entitlement_version"] == 4
        assert "checkout_intent" not in current_session
        assert "pre_activation_marker" not in current_session
        assert current_session.get("owner_logged_in") is not True


@pytest.mark.parametrize(
    "overrides",
    [
        {"line_item_price": "price_other"},
        {"line_item_product": "prod_other"},
        {"mode": "payment"},
        {"livemode": True},
        {"subscription": ""},
        {"mode": "payment", "subscription": ""},
        {
            "line_item_price": "price_other",
            "line_item_product": "prod_other",
        },
        {
            "metadata": {
                "stockradar_checkout_flow": app.CHECKOUT_FLOW_NAME,
                "stockradar_checkout_intent": "different-intent",
                "stockradar_price_id": "price_premium",
            }
        },
    ],
    ids=[
        "wrong-price",
        "wrong-product",
        "wrong-mode",
        "test-live-mismatch",
        "missing-subscription",
        "paid-one-time",
        "different-stripe-product",
        "mismatched-intent",
    ],
)
def test_invalid_checkout_variants_never_grant_premium(monkeypatch, overrides):
    configure_checkout(monkeypatch)
    intent = "browser-bound-intent"
    retrieve = Mock(return_value=checkout_session(intent, **overrides))
    update = Mock()
    monkeypatch.setattr(app.stripe.checkout.Session, "retrieve", retrieve)
    monkeypatch.setattr(app, "update_premium_entitlement", update)
    client = app.app.test_client()
    set_checkout_intent(client, intent)

    response = client.get("/checkout-success?session_id=cs_test_stockradar")

    assert response.status_code == 400
    update.assert_not_called()
    with client.session_transaction() as current_session:
        assert current_session.get("premium_active") is not True
        assert "checkout_intent" not in current_session


def test_invalid_checkout_session_id_is_rejected_before_stripe(monkeypatch):
    configure_checkout(monkeypatch)
    retrieve = Mock()
    monkeypatch.setattr(app.stripe.checkout.Session, "retrieve", retrieve)

    response = app.app.test_client().get(
        "/checkout-success?session_id=not-a-stripe-session"
    )

    assert response.status_code == 400
    retrieve.assert_not_called()


def test_checkout_intent_is_one_use_and_cannot_be_replayed(monkeypatch):
    configure_checkout(monkeypatch)
    intent = "one-use-intent"
    monkeypatch.setattr(
        app.stripe.checkout.Session,
        "retrieve",
        Mock(return_value=checkout_session(intent)),
    )
    monkeypatch.setattr(
        app,
        "update_premium_entitlement",
        Mock(return_value={"premium_active": True, "entitlement_version": 1}),
    )
    client = app.app.test_client()
    set_checkout_intent(client, intent)

    assert client.get(
        "/checkout-success?session_id=cs_test_stockradar"
    ).status_code == 200
    assert client.get(
        "/checkout-success?session_id=cs_test_stockradar"
    ).status_code == 400


def test_entitlement_save_failure_denies_premium(monkeypatch):
    configure_checkout(monkeypatch)
    intent = "save-failure-intent"
    monkeypatch.setattr(
        app.stripe.checkout.Session,
        "retrieve",
        Mock(return_value=checkout_session(intent)),
    )
    monkeypatch.setattr(app, "update_premium_entitlement", Mock(return_value=None))
    client = app.app.test_client()
    set_checkout_intent(client, intent)

    response = client.get("/checkout-success?session_id=cs_test_stockradar")

    assert response.status_code == 503
    with client.session_transaction() as current_session:
        assert current_session.get("premium_active") is not True


def test_webhook_cannot_activate_a_different_stripe_product(monkeypatch):
    configure_checkout(monkeypatch)
    monkeypatch.setattr(app, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    intent = "other-product-intent"
    event_session = checkout_session(
        intent,
        metadata={
            "stockradar_checkout_flow": app.CHECKOUT_FLOW_NAME,
            "stockradar_checkout_intent": intent,
            "stockradar_price_id": "price_other",
            "stockradar_product_id": "prod_other",
        },
    )
    event = {
        "id": "evt_wrong_product",
        "created": 1_700_000_000,
        "type": "checkout.session.completed",
        "data": {"object": event_session},
    }
    update = Mock()
    monkeypatch.setattr(
        app.stripe.Webhook,
        "construct_event",
        Mock(return_value=event),
    )
    monkeypatch.setattr(app, "update_premium_entitlement", update)

    response = app.app.test_client().post(
        "/stripe-webhook",
        data=b"{}",
        headers={"Stripe-Signature": "test-signature"},
    )

    assert response.status_code == 200
    assert response.get_json()["ignored"] == "checkout.session.completed"
    update.assert_not_called()


def test_unrelated_invoice_cannot_overwrite_premium_entitlement(monkeypatch):
    configure_checkout(monkeypatch)
    monkeypatch.setattr(app, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    event = {
        "id": "evt_unrelated_invoice",
        "created": 1_700_000_001,
        "type": "invoice.payment_succeeded",
        "data": {
            "object": {
                "id": "in_other",
                "livemode": False,
                "customer": "cus_stockradar",
                "subscription": "sub_other_product",
                "customer_email": "reader@example.test",
            }
        },
    }
    monkeypatch.setattr(
        app.stripe.Webhook,
        "construct_event",
        Mock(return_value=event),
    )
    monkeypatch.setattr(
        app,
        "premium_entitlement_record",
        Mock(return_value=None),
    )
    update = Mock()
    monkeypatch.setattr(app, "update_premium_entitlement", update)

    response = app.app.test_client().post(
        "/stripe-webhook",
        data=b"{}",
        headers={"Stripe-Signature": "test-signature"},
    )

    assert response.status_code == 200
    assert response.get_json()["ignored"] == "invoice.payment_succeeded"
    update.assert_not_called()


def test_inactive_entitlement_revokes_existing_session_and_stale_ids(monkeypatch):
    monkeypatch.setattr(
        app,
        "premium_entitlement_record",
        lambda **kwargs: {
            "premium_active": False,
            "entitlement_version": 8,
        }
        if any(kwargs.values())
        else None,
    )
    client = app.app.test_client()
    with client.session_transaction() as current_session:
        current_session.update(
            {
                "premium_active": True,
                "stripe_customer_id": "cus_revoked",
                "stripe_subscription_id": "sub_revoked",
                "premium_email": "reader@example.test",
                "entitlement_version": 7,
                "checkout_intent": "stale",
                "checkout_intent_created_at": time.time(),
            }
        )

    response = client.get("/portfolio-fit")

    assert "Upgrade to unlock portfolio fit reviews" in response.get_data(as_text=True)
    with client.session_transaction() as current_session:
        for key in app.PREMIUM_SESSION_KEYS:
            assert key not in current_session
    assert client.get("/portfolio-fit").status_code == 200
    with client.session_transaction() as current_session:
        assert current_session.get("premium_active") is not True


def test_active_premium_and_owner_access_remain_separate(monkeypatch):
    monkeypatch.setattr(
        app,
        "premium_entitlement_record",
        lambda **kwargs: {
            "premium_active": True,
            "entitlement_version": 9,
        }
        if any(kwargs.values())
        else None,
    )
    premium_client = app.app.test_client()
    with premium_client.session_transaction() as current_session:
        current_session["stripe_subscription_id"] = "sub_active"
    assert "Upgrade to unlock portfolio fit reviews" not in premium_client.get(
        "/portfolio-fit"
    ).get_data(as_text=True)

    owner_client = app.app.test_client()
    with owner_client.session_transaction() as current_session:
        current_session["owner_logged_in"] = True
    assert "Upgrade to unlock portfolio fit reviews" not in owner_client.get(
        "/portfolio-fit"
    ).get_data(as_text=True)
    with owner_client.session_transaction() as current_session:
        assert current_session.get("premium_active") is not True


def test_legacy_active_session_is_revalidated_once_and_migrated(monkeypatch):
    configure_checkout(monkeypatch)
    subscription = {
        "id": "sub_legacy_active",
        "customer": "cus_legacy_active",
        "livemode": False,
        "status": "active",
        "items": {
            "data": [
                {
                    "price": {
                        "id": "price_premium",
                        "product": {"id": "prod_premium"},
                    }
                }
            ]
        },
    }
    retrieve = Mock(return_value=subscription)
    update = Mock(
        return_value={"premium_active": True, "entitlement_version": 1}
    )
    migrated_record = {"premium_active": True, "entitlement_version": 1}
    monkeypatch.setattr(
        app,
        "premium_entitlement_record",
        Mock(side_effect=[None, migrated_record, migrated_record]),
    )
    monkeypatch.setattr(app.stripe.Subscription, "retrieve", retrieve)
    monkeypatch.setattr(app, "update_premium_entitlement", update)
    client = app.app.test_client()
    with client.session_transaction() as current_session:
        current_session.update(
            {
                "premium_active": True,
                "stripe_customer_id": "cus_legacy_active",
                "stripe_subscription_id": "sub_legacy_active",
                "premium_email": "reader@example.test",
            }
        )

    response = client.get("/portfolio-fit")

    assert "Upgrade to unlock portfolio fit reviews" not in response.get_data(
        as_text=True
    )
    retrieve.assert_called_once_with(
        "sub_legacy_active",
        expand=["items.data.price.product"],
    )
    update.assert_called_once()


def test_legacy_session_with_wrong_product_fails_closed(monkeypatch):
    configure_checkout(monkeypatch)
    subscription = {
        "id": "sub_legacy_other",
        "customer": "cus_legacy_other",
        "livemode": False,
        "status": "active",
        "items": {
            "data": [
                {
                    "price": {
                        "id": "price_other",
                        "product": {"id": "prod_other"},
                    }
                }
            ]
        },
    }
    update = Mock()
    monkeypatch.setattr(app, "premium_entitlement_record", Mock(return_value=None))
    monkeypatch.setattr(
        app.stripe.Subscription,
        "retrieve",
        Mock(return_value=subscription),
    )
    monkeypatch.setattr(app, "update_premium_entitlement", update)
    client = app.app.test_client()
    with client.session_transaction() as current_session:
        current_session.update(
            {
                "premium_active": True,
                "stripe_customer_id": "cus_legacy_other",
                "stripe_subscription_id": "sub_legacy_other",
                "premium_email": "reader@example.test",
            }
        )

    response = client.get("/portfolio-fit")

    assert "Upgrade to unlock portfolio fit reviews" in response.get_data(
        as_text=True
    )
    update.assert_not_called()
    with client.session_transaction() as current_session:
        for key in app.PREMIUM_SESSION_KEYS:
            assert key not in current_session


def test_logout_clears_entire_session():
    client = app.app.test_client()
    with client.session_transaction() as current_session:
        current_session.update(
            {
                "owner_logged_in": True,
                "premium_active": True,
                "stripe_customer_id": "cus_sensitive",
                "stripe_subscription_id": "sub_sensitive",
                "premium_email": "reader@example.test",
                "checkout_intent": "sensitive-intent",
                "checkout_intent_created_at": time.time(),
                "entitlement_version": 3,
                "unrelated_marker": "also-cleared",
            }
        )

    assert client.get("/logout").status_code == 405
    response = client.post("/logout")

    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    with client.session_transaction() as current_session:
        assert dict(current_session) == {}
