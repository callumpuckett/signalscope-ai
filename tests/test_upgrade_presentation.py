import re
from unittest.mock import patch

import pytest

import app


@pytest.mark.parametrize("identity", ["anonymous", "free", "premium", "owner"])
@pytest.mark.parametrize("payments_enabled", [False, True])
def test_upgrade_offer_and_checkout_preserve_access_states(identity, payments_enabled):
    client = app.app.test_client()
    if identity != "anonymous":
        with client.session_transaction() as current:
            if identity == "owner":
                current["owner_logged_in"] = True
            else:
                current["premium_email"] = "reader@example.test"
    record = None if identity in ("anonymous", "owner") else {
        "premium_active": identity == "premium", "entitlement_version": 1,
    }
    with (
        patch.object(app, "premium_entitlement_record", return_value=record),
        patch.object(app, "revalidate_legacy_premium_session", return_value=None),
        patch.object(app, "stripe_checkout_configured", return_value=payments_enabled),
        patch.object(app, "build_premium_decision_brief", return_value={}),
        patch.object(app.stripe.checkout.Session, "create") as checkout,
    ):
        response = client.get("/upgrade")
    assert response.status_code == 200
    checkout.assert_not_called()
    page = response.get_data(as_text=True)
    forms = re.findall(r'<form\b[^>]*action="/create-checkout-session"[^>]*>.*?</form>', page, re.S)
    if identity in ("premium", "owner"):
        assert "Premium is already active." in page
        assert "You do not need to purchase again." in page
        assert 'href="/opportunities">Open StockRadar Opportunities</a>' in page
        assert 'href="/stock/AAPL">Open Premium Stock Page</a>' in page
        assert not forms
    else:
        assert "Free shows the signal. Premium explains the decision." in page
        assert "£5/month · Cancel anytime" in page
        assert page.index("Understand the signal before you act.") < page.index("What Premium adds")
        assert page.index("What Premium adds") < page.index("Start with a ranked view.")
        assert page.index("Start with a ranked view.") < page.index("Subscription details and support")
        for label in ("Opportunity Radar", "AI Reasoning", "Risk Read", "Portfolio Fit", "Watch Next"):
            assert f"<strong>{label}</strong>" in page
        assert "complete ranked Top 5" in page
        assert "Controlled early access" not in page
        assert "Premium features and support processes are still being improved." in page
        assert "Cancellation stops future billing" in page
        if payments_enabled:
            assert len(forms) == 2
            for form in forms:
                assert 'method="POST"' in form
                assert 'name="csrf_token"' in form
                assert 'type="submit"' in form
                assert "Start Premium — £5/month" in form
            assert 'href="/manage-subscription"' in page
            assert "support until self-service billing management is added" in page
            assert "StockRadar does not store your full card details" in page
        else:
            assert not forms
            assert "Premium subscriptions are not open yet." in page
            assert "Checkout remains disabled during soft launch." in page
    assert 'href="/risk-disclaimer"' in page
    assert "It does not provide personal financial" in page
    assert '<details class="card future-card">' in page
    assert "Not live yet" in page


def test_upgrade_keeps_existing_mobile_layout_rules():
    assert 'name="viewport" content="width=device-width, initial-scale=1.0"' in app.upgrade_html
    assert "@media(max-width:850px)" in app.upgrade_html
    assert ".hero,.grid,.brief-grid{grid-template-columns:1fr;}" in app.upgrade_html
    assert ".button{width:100%;margin-right:0;}" in app.upgrade_html
