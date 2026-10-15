"""Reclaim expired shared limiter state without weakening active counters."""
import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import app


class StateStorage:
    durable = True

    def __init__(self, state):
        self.state = copy.deepcopy(state)
        self.lock = threading.Lock()
        self.writes = 0

    def update_state(self, store_name, updater):
        assert store_name == 'rate_limits'
        with self.lock:
            updated = copy.deepcopy(self.state)
            if updater(updated) is not False:
                self.state = updated
                self.writes += 1
        return True


@pytest.mark.parametrize('durable', [False, True])
def test_expired_buckets_below_old_threshold_are_pruned_with_same_retention(monkeypatch, durable):
    now = 1_000_000.
    buckets = {f'{index:064x}': {'count': 1, 'reset_at': now - 3601} for index in range(1510)}
    retained = {f'{index:064x}': {'count': 2, 'reset_at': now + 100} for index in range(1510, 1525)}
    buckets.update(retained)
    state = {'buckets': buckets}
    before_bytes = len(json.dumps(state).encode())
    store = StateStorage(state)
    monkeypatch.setattr(app, 'IS_PRODUCTION', durable)
    monkeypatch.setattr(app, 'NEWSLETTER_STORAGE', store if durable else None)
    monkeypatch.setattr(app, 'SECURITY_RATE_LIMIT_STATE', copy.deepcopy(state))
    with app.app.test_request_context('/stock/MSFT'):
        assert app.consume_rate_limit('stock-report', 60, 300, now=now)['allowed']
    after = store.state if durable else app.SECURITY_RATE_LIMIT_STATE
    assert len(after['buckets']) == 16  # 15 retained plus this request's counter
    for key, bucket in retained.items():
        assert after['buckets'][key] == bucket
    assert len(json.dumps(after).encode()) < before_bytes / 50
    if durable:
        assert store.writes == 1


def test_cleanup_retains_boundary_recently_expired_and_live_counters(monkeypatch):
    now = 1_000_000.
    state = {'buckets': {
        'older': {'count': 1, 'reset_at': now - 3600.01},
        'boundary': {'count': 2, 'reset_at': now - 3600},
        'recently_expired': {'count': 3, 'reset_at': now - 1},
        'live': {'count': 4, 'reset_at': now + 1},
    }}
    store = StateStorage(state)
    monkeypatch.setattr(app, 'IS_PRODUCTION', True)
    monkeypatch.setattr(app, 'NEWSLETTER_STORAGE', store)
    with app.app.test_request_context('/api/market-news'):
        assert app.consume_rate_limit('market-news', 120, 300, now=now)['allowed']
    assert 'older' not in store.state['buckets']
    for key in ('boundary', 'recently_expired', 'live'):
        assert store.state['buckets'][key] == state['buckets'][key]


def test_pruning_does_not_reset_an_active_limit_or_change_retry_after(monkeypatch):
    now = 1_000_000.
    monkeypatch.setattr(app, 'IS_PRODUCTION', True)
    store = StateStorage({'buckets': {'expired': {'count': 1, 'reset_at': now - 4000}}})
    monkeypatch.setattr(app, 'NEWSLETTER_STORAGE', store)
    with app.app.test_request_context('/api/market-news'):
        key = hashlib.sha256(f'market-news|{app.rate_limit_identity()}'.encode()).hexdigest()
        store.state['buckets'][key] = {'count': 120, 'reset_at': now + 40}
        assert app.consume_rate_limit('market-news', 120, 300, now=now) == {'allowed': False, 'retry_after': 40}
        assert store.state['buckets'][key]['count'] == 120
        assert store.writes == 0  # A denied request still does not commit a state change.
        assert app.consume_rate_limit('market-news', 120, 300, now=now + 40)['allowed']
        assert store.state['buckets'][key] == {'count': 1, 'reset_at': now + 340}
        assert 'expired' not in store.state['buckets']


def test_existing_longer_window_retention_is_preserved(monkeypatch):
    now = 1_000_000.
    state = {'buckets': {'retained_for_long_window': {'count': 1, 'reset_at': now - 4000}}}
    store = StateStorage(state)
    monkeypatch.setattr(app, 'IS_PRODUCTION', True)
    monkeypatch.setattr(app, 'NEWSLETTER_STORAGE', store)
    with app.app.test_request_context('/api/market-news'):
        assert app.consume_rate_limit('long-window', 5, 7200, now=now)['allowed']
    assert store.state['buckets']['retained_for_long_window'] == state['buckets']['retained_for_long_window']


def test_concurrent_shared_counter_remains_exact_while_pruning(monkeypatch):
    store = StateStorage({'buckets': {str(i): {'count': 1, 'reset_at': 1} for i in range(100)}})
    monkeypatch.setattr(app, 'IS_PRODUCTION', True)
    monkeypatch.setattr(app, 'NEWSLETTER_STORAGE', store)
    def consume(_):
        with app.app.test_request_context('/api/market-news'):
            return app.consume_rate_limit('shared', 7, 60, now=10000)
    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(consume, range(24)))
    assert sum(row['allowed'] for row in outcomes) == 7
    assert all(row['retry_after'] == 60 for row in outcomes if not row['allowed'])
    assert len(store.state['buckets']) == 1
