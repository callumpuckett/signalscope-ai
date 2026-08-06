import copy
import re
from pathlib import Path
from unittest.mock import Mock, patch

import app


def csrf_token_from(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match
    return match.group(1)


class MemoryStorage:
    durable = True

    def __init__(self):
        self.states = {
            "premium_entitlements": {"records": []},
            "rate_limits": {"buckets": {}},
            "turnstile_tokens": {"tokens": {}},
        }

    def load_state(self, name):
        return copy.deepcopy(self.states[name])

    def update_state(self, name, updater):
        state = copy.deepcopy(self.states[name])
        should_write = updater(state)
        if should_write is not False:
            self.states[name] = state
        return True


def configure_webhook(monkeypatch):
    storage = MemoryStorage()
    monkeypatch.setattr(app, "NEWSLETTER_STORAGE", storage)
    monkeypatch.setattr(app, "STRIPE_SECRET_KEY", "sk_test_security")
    monkeypatch.setattr(app, "STRIPE_WEBHOOK_SECRET", "whsec_security")
    monkeypatch.setattr(app, "STRIPE_PRICE_ID", "price_stockradar")
    monkeypatch.setattr(app, "STRIPE_PRODUCT_ID", "prod_stockradar")
    return storage


def stripe_items(price="price_stockradar", product="prod_stockradar"):
    return {"data": [{"price": {"id": price, "product": product}}]}


def subscription_event(event_id, created, event_type, status, price="price_stockradar", product="prod_stockradar"):
    return {
        "id": event_id,
        "created": created,
        "type": event_type,
        "data": {
            "object": {
                "id": "sub_security",
                "customer": "cus_security",
                "livemode": False,
                "status": status,
                "items": stripe_items(price, product),
            }
        },
    }


def post_webhook(client, monkeypatch, event):
    monkeypatch.setattr(app.stripe.Webhook, "construct_event", Mock(return_value=event))
    return client.post(
        "/stripe-webhook",
        data=b"{}",
        headers={"Stripe-Signature": "signed"},
    )


def seed_entitlement():
    return app.update_premium_entitlement(
        customer_id="cus_security",
        subscription_id="sub_security",
        email="reader@example.test",
        subscription_status="active",
        premium_active=True,
        event_type="seed",
    )


def test_csrf_rejects_missing_token_and_accepts_session_token():
    client = app.app.test_client()
    app.app.config["WTF_CSRF_ENABLED"] = True

    assert client.post("/beginner", data={"goal": "growth"}).status_code == 400
    token = csrf_token_from(client.get("/beginner"))
    response = client.post(
        "/beginner",
        data={
            "csrf_token": token,
            "goal": "growth",
            "horizon": "10plus",
            "risk": "medium",
            "experience": "new",
            "style": "simple",
            "amount": "100",
        },
    )
    assert response.status_code == 200


def test_logout_is_post_only_and_requires_csrf():
    client = app.app.test_client()
    app.app.config["WTF_CSRF_ENABLED"] = True
    token = csrf_token_from(client.get("/login"))
    with client.session_transaction() as current_session:
        current_session["owner_logged_in"] = True

    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 400
    response = client.post("/logout", data={"csrf_token": token})
    assert response.status_code == 302
    with client.session_transaction() as current_session:
        assert not current_session


def test_cross_origin_post_is_rejected_with_valid_csrf_token():
    client = app.app.test_client()
    app.app.config["WTF_CSRF_ENABLED"] = True
    token = csrf_token_from(client.get("/login"))
    response = client.post(
        "/login",
        data={"csrf_token": token, "email": "x@example.test", "password": "x"},
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403


def test_rate_limit_returns_429_and_retry_after(monkeypatch):
    monkeypatch.setitem(app.RATE_LIMIT_RULES, ("compare", "GET"), (1, 60, "test-compare"))
    client = app.app.test_client()
    assert client.get("/compare").status_code == 200
    limited = client.get("/compare")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1


def test_forced_refresh_is_restricted_for_public_requests(monkeypatch):
    cached = Mock(return_value={})
    monkeypatch.setattr(app, "get_cached_dashboard_data", cached)
    assert app.app.test_client().get("/?refresh=1").status_code == 403
    assert app.app.test_client().get("/api/market-news?refresh=1").status_code == 403
    cached.assert_not_called()


def test_request_size_and_portfolio_limits_are_enforced():
    client = app.app.test_client()
    oversized = client.post("/newsletter", data={"email": "a" * 9000})
    assert oversized.status_code == 413
    too_many = ",".join(f"T{index}" for index in range(51))
    response = client.post("/portfolio-fit", data={"holdings": too_many})
    assert response.status_code == 400


def test_security_headers_hsts_and_sensitive_cache_control(monkeypatch):
    monkeypatch.setattr(app, "IS_PRODUCTION", True)
    response = app.app.test_client().get("/login", base_url="https://www.stockradarhq.com")
    csp = response.headers["Content-Security-Policy-Report-Only"]
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'self'" in csp
    assert "unsafe-inline" not in csp
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    assert response.headers["Cache-Control"].startswith("no-store")


def test_public_diagnostics_are_minimal_and_do_not_fetch_news(monkeypatch):
    fetch = Mock()
    monkeypatch.setattr(app, "fetch_live_market_news", fetch)
    client = app.app.test_client()
    for path in ("/health", "/healthz", "/news-health", "/deploy-version"):
        assert client.get(path).get_json() == {"app": "StockRadar", "status": "ok"}
    fetch.assert_not_called()


def test_internal_header_unlocks_diagnostics_without_query_secret(monkeypatch):
    monkeypatch.setattr(app, "INTERNAL_DIAGNOSTICS_SECRET", "internal-secret")
    monkeypatch.setattr(app, "fetch_live_market_news", Mock(return_value=[]))
    client = app.app.test_client()
    public = client.get("/deploy-version?secret=internal-secret").get_json()
    detailed = client.get(
        "/deploy-version",
        headers={"X-StockRadar-Internal-Secret": "internal-secret"},
    ).get_json()
    assert "commit" not in public
    assert "commit" in detailed


def test_tls_verification_bypass_is_absent():
    source = (Path(app.__file__).read_text(encoding="utf-8"))
    assert "ssl._create_unverified_context" not in source
    assert "verify=False" not in source


def test_duplicate_stripe_event_is_durably_ignored(monkeypatch):
    configure_webhook(monkeypatch)
    seed_entitlement()
    event = subscription_event(
        "evt_renewal_once",
        200,
        "customer.subscription.updated",
        "active",
    )
    client = app.app.test_client()
    assert post_webhook(client, monkeypatch, event).get_json() == {"received": True}
    first_version = app.premium_entitlement_record(subscription_id="sub_security")["entitlement_version"]
    duplicate = post_webhook(client, monkeypatch, event)
    assert duplicate.get_json()["ignored"] == "duplicate"
    assert app.premium_entitlement_record(subscription_id="sub_security")["entitlement_version"] == first_version


def test_older_cancellation_cannot_override_newer_activation(monkeypatch):
    configure_webhook(monkeypatch)
    seed_entitlement()
    client = app.app.test_client()
    activation = subscription_event("evt_new_activation", 300, "customer.subscription.updated", "active")
    cancellation = subscription_event("evt_old_cancel", 200, "customer.subscription.deleted", "canceled")
    assert post_webhook(client, monkeypatch, activation).status_code == 200
    assert post_webhook(client, monkeypatch, cancellation).get_json()["ignored"] == "stale"
    assert app.premium_entitlement_record(subscription_id="sub_security")["premium_active"] is True


def test_older_activation_cannot_override_newer_cancellation(monkeypatch):
    configure_webhook(monkeypatch)
    seed_entitlement()
    client = app.app.test_client()
    cancellation = subscription_event("evt_new_cancel", 300, "customer.subscription.deleted", "canceled")
    activation = subscription_event("evt_old_activation", 200, "customer.subscription.updated", "active")
    assert post_webhook(client, monkeypatch, cancellation).status_code == 200
    assert post_webhook(client, monkeypatch, activation).get_json()["ignored"] == "stale"
    assert app.premium_entitlement_record(subscription_id="sub_security")["premium_active"] is False


def test_wrong_price_and_product_do_not_update_entitlement(monkeypatch):
    configure_webhook(monkeypatch)
    original = seed_entitlement()["entitlement_version"]
    client = app.app.test_client()
    wrong_price = subscription_event("evt_wrong_price", 200, "customer.subscription.updated", "active", price="price_other")
    wrong_product = subscription_event("evt_wrong_product", 201, "customer.subscription.updated", "active", product="prod_other")
    assert post_webhook(client, monkeypatch, wrong_price).get_json()["ignored"] == "customer.subscription.updated"
    assert post_webhook(client, monkeypatch, wrong_product).get_json()["ignored"] == "customer.subscription.updated"
    assert app.premium_entitlement_record(subscription_id="sub_security")["entitlement_version"] == original


def test_valid_renewal_and_cancellation_transitions(monkeypatch):
    configure_webhook(monkeypatch)
    seed_entitlement()
    client = app.app.test_client()
    renewal = subscription_event("evt_valid_renewal", 200, "customer.subscription.updated", "active")
    cancellation = subscription_event("evt_valid_cancel", 201, "customer.subscription.deleted", "canceled")
    assert post_webhook(client, monkeypatch, renewal).get_json() == {"received": True}
    assert app.premium_entitlement_record(subscription_id="sub_security")["premium_active"] is True
    assert post_webhook(client, monkeypatch, cancellation).get_json() == {"received": True}
    assert app.premium_entitlement_record(subscription_id="sub_security")["premium_active"] is False
