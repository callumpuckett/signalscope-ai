import copy
import json
import threading
import time
from unittest.mock import patch

from bs4 import BeautifulSoup
import pytest

import app
from analytics import COOKIE, MAX_AGE


class Store:
    durable = True

    def __init__(self):
        self.state = {}
        self.lock = threading.Lock()

    def update_state(self, name, callback):
        assert name == "analytics"
        with self.lock:
            candidate = copy.deepcopy(self.state)
            callback(candidate)
            self.state = candidate
        return True

    def load_state(self, name):
        return copy.deepcopy(self.state)


@pytest.fixture
def analytics(monkeypatch):
    tracker = app.consent_analytics
    store = Store()
    monkeypatch.setattr(tracker, "enabled", True)
    monkeypatch.setattr(tracker, "storage", lambda: store)
    monkeypatch.setattr(tracker, "api_secret", "test-secret")
    return tracker, store, app.app.test_client()


def accept(analytics):
    tracker, store, client = analytics
    assert client.post("/analytics/consent", json={"choice": "accepted"}).status_code == 200
    return tracker, store, client


def test_no_google_tag_before_consent_and_equal_choices(analytics):
    tracker, store, client = analytics
    response = client.get("/upgrade?email=private@example.test&session_id=cs_live_private")
    document = BeautifulSoup(response.text, "html.parser")
    assert document.select_one('#sr-consent-banner [data-sr-consent="accepted"]').text == "Accept Analytics"
    assert document.select_one('#sr-consent-banner [data-sr-consent="rejected"]').text == "Reject Analytics"
    assert document.select_one('#sr-consent-preferences')
    assert not document.select('script[src*="googletagmanager"], iframe[src*="googletagmanager"]')
    config = json.loads(document.select_one('#sr-analytics')["data-config"])
    assert config["choice"] == "" and config["client_id"] == ""
    assert config["page_path"] == "/upgrade"
    assert "private@example.test" not in json.dumps(config)
    assert "cs_live_private" not in json.dumps(config)
    assert store.state == {}


def test_rejection_and_withdrawal_revoke_server_consent(analytics):
    tracker, store, client = accept(analytics)
    token = next(iter(store.state["consents"]))
    response = client.post("/analytics/consent", json={"choice": "rejected"})
    assert response.json["saved"] is True
    assert not store.state["consents"][token]["granted"]
    assert 'HttpOnly' in response.headers['Set-Cookie']
    assert 'SameSite=Lax' in response.headers['Set-Cookie']
    assert 'rejected' in client.get('/upgrade').text


def test_tampered_cookie_never_grants_analytics(analytics):
    tracker, store, client = analytics
    client.set_cookie(COOKIE, "accepted.forged")
    document = BeautifulSoup(client.get('/upgrade').text, 'html.parser')
    assert json.loads(document.select_one('#sr-analytics')['data-config'])['choice'] == ''


def test_csrf_and_invalid_preference_are_rejected(analytics):
    tracker, store, client = analytics
    assert client.post('/analytics/consent', json={'choice': 'yes'}).status_code == 400
    assert client.post('/analytics/consent', json=['accepted']).status_code == 400
    app.app.config['WTF_CSRF_ENABLED'] = True
    try:
        assert client.post('/analytics/consent', json={'choice': 'accepted'}).status_code == 400
    finally:
        app.app.config['WTF_CSRF_ENABLED'] = False
    assert store.state == {}


def test_durable_storage_failure_fails_closed(analytics, monkeypatch):
    tracker, store, client = analytics
    monkeypatch.setattr(store, 'durable', False)
    assert client.post('/analytics/consent', json={'choice': 'accepted'}).status_code == 503
    assert not client.get_cookie(COOKIE)


def test_private_pages_are_not_instrumented(analytics):
    tracker, store, client = analytics
    assert 'id="sr-analytics"' not in client.get('/login').text
    assert 'id="sr-analytics"' not in client.get('/checkout-success?session_id=cs_live_secret').text


def prepare_checkout(analytics):
    tracker, store, client = accept(analytics)
    with client.session_transaction() as session:
        session['analytics_session_id'] = 12345
    with client.application.test_request_context('/create-checkout-session', headers={
            'Cookie': COOKIE + '=' + client.get_cookie(COOKIE).value}):
        with patch.object(tracker, 'enqueue'):
            tracker.checkout_created({'id': 'cs_live_private'}, 'private-intent')
    return tracker, store, client


def test_paid_purchase_is_durably_deduplicated_and_minimized(analytics):
    tracker, store, client = prepare_checkout(analytics)
    checkout = {'id': 'cs_live_private', 'livemode': True, 'payment_status': 'paid', 'amount_total': 500,
                'subscription': 'sub_private', 'customer': 'cus_private',
                'customer_details': {'email': 'private@example.test'},
                'metadata': {'stockradar_checkout_intent': 'private-intent'}}
    sent = []
    with patch('analytics.threading.Thread') as thread:
        thread.return_value.start.side_effect = lambda: sent.append(thread.call_args.kwargs['args'])
        tracker.paid_checkout(checkout, time.time())
        tracker.paid_checkout(checkout, time.time())
    assert len(sent) == 1
    payload = json.dumps(sent[0][1])
    for secret in ['private@example.test', 'cus_private', 'sub_private', 'private-intent', 'amount_total', '500']:
        assert secret not in payload
    assert sent[0][1]['events'][0]['name'] == 'purchase'
    assert sent[0][1]['events'][0]['params']['transaction_id'].startswith('purchase_')


@pytest.mark.parametrize('change', [{'livemode': False}, {'payment_status': 'unpaid'}, {'amount_total': 0}])
def test_unpaid_test_and_zero_value_never_count(analytics, change):
    tracker, store, client = prepare_checkout(analytics)
    checkout = {'livemode': True, 'payment_status': 'paid', 'amount_total': 500,
                'subscription': 'sub_test', 'metadata': {'stockradar_checkout_intent': 'private-intent'}}
    checkout.update(change)
    with patch.object(tracker, 'enqueue') as enqueue:
        tracker.paid_checkout(checkout, time.time())
    enqueue.assert_not_called()


def test_revocation_prevents_queued_delivery(analytics):
    tracker, store, client = prepare_checkout(analytics)
    with patch('analytics.threading.Thread'):
        tracker.enqueue('purchase_test', tracker.digest('private-intent'), 'purchase')
    client.post('/analytics/consent', json={'choice': 'rejected'})
    with patch('analytics.urllib.request.urlopen') as send:
        tracker.deliver('purchase_test', {'client_id': 'test'})
    send.assert_not_called()
    assert store.state['events']['purchase_test']['status'] == 'consent_unavailable'


def test_missing_consent_or_secret_never_sends(analytics):
    tracker, store, client = analytics
    with patch('analytics.threading.Thread') as thread:
        tracker.enqueue('purchase_unmatched', 'unknown', 'purchase')
    thread.assert_not_called()
    tracker.api_secret = ''
    tracker.enqueue('purchase_no_secret', 'unknown', 'purchase')
    assert not store.state.get('events')


def test_different_checkout_cannot_reuse_consent_association(analytics):
    tracker, store, client = prepare_checkout(analytics)
    checkout = {'id': 'cs_live_other', 'livemode': True, 'payment_status': 'paid',
                'amount_total': 500, 'subscription': 'sub_other',
                'metadata': {'stockradar_checkout_intent': 'private-intent'}}
    with patch('analytics.threading.Thread') as thread:
        tracker.paid_checkout(checkout, time.time())
    thread.assert_not_called()


def test_purchase_recovery_reuses_transaction_and_rechecks_consent(analytics):
    tracker, store, client = prepare_checkout(analytics)
    with patch('analytics.threading.Thread'):
        tracker.enqueue('purchase_recovery', tracker.digest('private-intent'), 'purchase')
    payload = copy.deepcopy(store.state['events']['purchase_recovery']['payload'])
    with patch('analytics.urllib.request.urlopen', side_effect=TimeoutError):
        tracker.deliver('purchase_recovery', payload)
    record = store.state['events']['purchase_recovery']
    assert record['status'] == 'pending' and record['attempts'] == 1
    assert record['payload'] == payload
    record['next_at'] = 0
    response = type('Response', (), {'status': 204, '__enter__': lambda self: self,
                                    '__exit__': lambda *args: None})()
    with patch('analytics.urllib.request.urlopen', return_value=response) as send:
        tracker.deliver('purchase_recovery', payload)
        tracker.deliver('purchase_recovery', payload)
    assert send.call_count == 1
    assert store.state['events']['purchase_recovery']['status'] == 'accepted'
    assert 'payload' not in store.state['events']['purchase_recovery']


def test_checkout_is_never_retried_after_ambiguous_delivery(analytics):
    tracker, store, client = prepare_checkout(analytics)
    with patch('analytics.threading.Thread'):
        tracker.enqueue('checkout_once', tracker.digest('private-intent'), 'begin_checkout')
    payload = store.state['events']['checkout_once']['payload']
    with patch('analytics.urllib.request.urlopen', side_effect=TimeoutError) as send:
        tracker.deliver('checkout_once', payload)
        tracker.deliver('checkout_once', payload)
    assert send.call_count == 1
    assert store.state['events']['checkout_once']['status'] == 'unconfirmed'


@pytest.mark.parametrize('trusted', [False, True])
def test_only_validated_checkout_webhook_reaches_purchase_hook(analytics, monkeypatch, trusted):
    tracker, store, client = analytics
    event_data = {'livemode': True, 'payment_status': 'paid', 'amount_total': 500,
                  'id': 'cs_live_private', 'subscription': 'sub_private', 'customer': 'cus_private'}
    event = {'id': 'evt_analytics_test', 'created': int(time.time()),
             'type': 'checkout.session.completed', 'data': {'object': event_data}}
    monkeypatch.setattr(app, 'STRIPE_WEBHOOK_SECRET', 'test-webhook-secret')
    monkeypatch.setattr(app.stripe.Webhook, 'construct_event', lambda *args: event)
    monkeypatch.setattr(app, 'checkout_session_server_context_matches', lambda data: trusted)
    monkeypatch.setattr(app, 'update_premium_entitlement', lambda **kwargs: {})
    with patch.object(tracker, 'paid_checkout') as paid:
        response = client.post('/stripe-webhook', data='signed fixture', headers={'Stripe-Signature': 'fixture'})
    assert response.status_code == 200
    assert paid.call_count == int(trusted)


def test_invalid_stripe_signature_cannot_measure_purchase(analytics, monkeypatch):
    tracker, store, client = analytics
    monkeypatch.setattr(app, 'STRIPE_WEBHOOK_SECRET', 'test-webhook-secret')
    with patch.object(app.stripe.Webhook, 'construct_event', side_effect=ValueError), patch.object(tracker, 'paid_checkout') as paid:
        assert client.post('/stripe-webhook', data='untrusted').status_code == 400
    paid.assert_not_called()
