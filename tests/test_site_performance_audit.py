"""Navigation must stay independent of slow/failing market providers."""
import threading
import time
from unittest.mock import Mock

import pytest
import app


@pytest.fixture(autouse=True)
def isolate_local_lookup_caches(monkeypatch):
    # Real route renders must not leave logo/display lookup state in later tests.
    for name in ['STOCK_UNIVERSE_CACHE', 'STOCK_DISPLAY_LOOKUP_CACHE',
                 'STOCK_IDENTITY_LOOKUP_CACHE', 'COMPANY_LOGO_METADATA_CACHE',
                 'RECOMMENDATIONS_CACHE']:
        monkeypatch.setattr(app, name, dict(getattr(app, name)))


@pytest.fixture
def cache(monkeypatch):
    data = app.prepare_dashboard_data(include_market_snapshots=False, local_only=True)
    data.update(last_updated='29 Sep 2026, 12:00', ticker_updated='12:00')
    monkeypatch.setattr(app, 'DASHBOARD_CACHE', {'data': data, 'timestamp': 1})
    monkeypatch.setattr(app, 'DASHBOARD_REFRESH_PENDING', False)
    monkeypatch.setattr(app, 'DASHBOARD_REFRESH_RETRY_AT', 0)
    return data


def test_slow_refresh_does_not_block_routes_and_is_deduplicated(cache, monkeypatch):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = app._background_dashboard_refresh
    def refresh(**kwargs):
        try:
            original(**kwargs)
        finally:
            finished.set()
    def provider(**kwargs):
        entered.set()
        assert release.wait(3)
        raise RuntimeError('provider unavailable')
    provider_mock = Mock(side_effect=provider)
    monkeypatch.setattr(app, 'prepare_dashboard_data', provider_mock)
    monkeypatch.setattr(app, '_background_dashboard_refresh', refresh)
    client = app.app.test_client()
    try:
        started = time.perf_counter()
        assert client.get('/').status_code == 200
        assert entered.wait(1)
        for route in ['/?tab=signals', '/?tab=watchlist', '/?tab=radar', '/api/market-news']:
            assert client.get(route).status_code == 200
        assert time.perf_counter() - started < 1.5
        assert provider_mock.call_count == 1
        assert not finished.is_set()
    finally:
        release.set()
        assert finished.wait(2)
    assert app.DASHBOARD_CACHE['data'] == cache
    assert app.DASHBOARD_CACHE['timestamp'] == 1
    client.get('/api/market-news')
    assert provider_mock.call_count == 1  # failure cooldown


def test_future_day_refresh_keeps_data_timestamp_until_success(cache, monkeypatch):
    monkeypatch.setattr(app.time, 'time', lambda: 100000)
    fresh = {**cache, 'last_updated': '01 Oct 2026, 12:00'}
    source = Mock(return_value=fresh)
    monkeypatch.setattr(app, 'prepare_dashboard_data', source)
    app._background_dashboard_refresh(force_refresh=False, include_market_snapshots=False)
    assert app.DASHBOARD_CACHE['data'] == fresh
    assert app.DASHBOARD_CACHE['timestamp'] == 100000
    app._background_dashboard_refresh(force_refresh=False, include_market_snapshots=False)
    source.assert_called_once()
    monkeypatch.setattr(app.time, 'time', lambda: 200000)
    app._background_dashboard_refresh(force_refresh=False, include_market_snapshots=False)
    assert source.call_count == 2


def test_login_and_redirect_work_when_all_providers_fail(cache, monkeypatch):
    for name in ['safe_history', '_fetch_dividend_context', 'fetch_live_market_news',
                 'get_stock_universe', 'get_recommendations']:
        monkeypatch.setattr(app, name, Mock(side_effect=AssertionError('market work during login')))
    monkeypatch.setattr(app, 'OWNER_EMAIL', 'audit@example.test')
    monkeypatch.setattr(app, 'OWNER_PASSWORD', 'local-audit-password')
    monkeypatch.setattr(app, 'OWNER_PASSWORD_HASH', '')
    client = app.app.test_client()
    assert client.get('/login').status_code == 200
    response = client.post('/login', data={'email': 'audit@example.test', 'password': 'local-audit-password'})
    assert response.status_code == 302
    assert response.headers['Location'] == '/'
    with client.session_transaction() as session:
        assert session['owner_logged_in'] is True
    for name in ['safe_history', '_fetch_dividend_context', 'fetch_live_market_news',
                 'get_stock_universe', 'get_recommendations']:
        getattr(app, name).assert_not_called()


def test_unrelated_routes_do_not_prepare_dashboard(cache, monkeypatch):
    source = Mock(side_effect=AssertionError('unrelated dashboard work'))
    monkeypatch.setattr(app, 'prepare_dashboard_data', source)
    for route in ['/login', '/what-if', '/compare', '/upgrade', '/how-it-works', '/privacy', '/premium-watchlist']:
        assert app.app.test_client().get(route).status_code == 200
    source.assert_not_called()


def test_cold_navigation_uses_local_shell_without_provider_wait(monkeypatch):
    monkeypatch.setattr(app, 'DASHBOARD_CACHE', {})
    monkeypatch.setattr(app, 'DASHBOARD_REFRESH_PENDING', True)  # controlled held worker
    provider = Mock(side_effect=AssertionError('blocking provider'))
    monkeypatch.setattr(app, 'safe_build_live_headlines', provider)
    monkeypatch.setattr(app, 'fetch_symbol_snapshot', provider)
    response = app.app.test_client().get('/?tab=overview')
    assert response.status_code == 200
    response = app.app.test_client().get('/api/market-news')
    assert response.json['ticker_updated'] == ''
    provider.assert_not_called()


def test_invalid_refresh_preserves_last_good(cache, monkeypatch):
    monkeypatch.setattr(app, 'prepare_dashboard_data', Mock(return_value={}))
    app._background_dashboard_refresh(force_refresh=False, include_market_snapshots=False)
    assert app.DASHBOARD_CACHE['data'] == cache
    assert app.DASHBOARD_CACHE['timestamp'] == 1
    assert app.DASHBOARD_REFRESH_RETRY_AT > app.time.time()
