"""Locate shared database guard latency without logging request identities."""
import json
import logging
import time
from unittest.mock import Mock

import pandas as pd
import pytest

import app
from newsletter_storage import PostgresNewsletterStorage
from postgres_connection_pool import PoolExhausted


class SlowCursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args, **kwargs):
        time.sleep(.025)

    def fetchone(self):
        return None


class Connection:
    def cursor(self):
        return SlowCursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def sources(monkeypatch):
    monkeypatch.setenv('STOCKRADAR_TIMING_ENABLED', 'true')
    monkeypatch.setattr(app, 'IS_PRODUCTION', True)
    monkeypatch.setattr(app, 'NEWSLETTER_STORAGE', PostgresNewsletterStorage('private-database-url', lambda *a, **k: Connection()))
    monkeypatch.setattr(app, 'DASHBOARD_CACHE', {})
    monkeypatch.setattr(app, 'DASHBOARD_REFRESH_PENDING', True)
    monkeypatch.setattr(app, 'get_dividend_context', Mock(return_value={}))
    history = pd.DataFrame({'Close': [100., 101.]}, index=pd.to_datetime(['2026-01-01', '2026-01-02']))
    monkeypatch.setattr(app, 'safe_history', Mock(return_value=history))
    monkeypatch.setattr(app, 'premium_entitlement_record', Mock(return_value=None))
    monkeypatch.setattr(app, 'revalidate_legacy_premium_session', Mock(return_value=None))


def records(caplog):
    return [json.loads(record.getMessage()) for record in caplog.records
            if record.name == 'stockradar.performance'
            and json.loads(record.getMessage()).get('scope') == 'request']


@pytest.mark.parametrize('symbol', ['MSFT', 'KO', 'SPY'])
def test_stock_trace_separates_rate_limit_storage_from_route_and_providers(sources, caplog, symbol):
    caplog.set_level(logging.INFO, logger='stockradar.performance')
    response = app.app.test_client().get('/stock/' + symbol + '?private=secret-query', base_url='https://www.stockradarhq.com')
    assert response.status_code == 200
    trace, = records(caplog)
    assert trace['route'] == 'stock_detail'
    assert trace['status'] == 200
    assert response.headers['X-StockRadar-Trace'] == trace['trace_id']
    assert trace['counts']['db.store.rate_limits'] == 1
    assert trace['stages']['security.durable_rate_limit']['ms'] >= 60
    assert trace['stages']['db.advisory_lock']['calls'] == 1
    assert trace['stages']['db.query']['calls'] == 2
    assert trace['stages']['db.state_read']['calls'] == 1
    assert trace['stages']['db.state_write']['calls'] == 1
    assert trace['stages']['route.stock_detail']['calls'] == 1
    assert trace['stages']['stock.history']['calls'] == 1
    assert trace['stages']['stock.lifetime_history']['calls'] == 1
    assert 'stock.intelligence_report' not in trace['stages']
    logged = json.dumps(trace)
    for secret in ('secret-query', 'private-database-url', '/stock/' + symbol, 'SELECT ', 'INSERT '):
        assert secret not in logged


def test_news_trace_identifies_database_guard_without_provider_or_premium_work(sources, caplog):
    caplog.set_level(logging.INFO, logger='stockradar.performance')
    response = app.app.test_client().get('/api/market-news', base_url='https://www.stockradarhq.com')
    assert response.status_code == 200
    trace, = records(caplog)
    assert trace['route'] == 'market_news'
    assert trace['counts']['db.store.rate_limits'] == 1
    assert trace['stages']['security.durable_rate_limit']['ms'] >= 60
    assert trace['stages']['route.market_news']['calls'] == 1
    assert trace['stages']['db.state_read']['calls'] == 1
    assert trace['stages']['db.state_write']['calls'] == 1
    assert not any(name.startswith(('stock.', 'premium.', 'provider.')) for name in trace['stages'])


def test_stock_pool_exhaustion_still_fails_closed_before_route(sources, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger='stockradar.performance')
    storage = Mock(durable=True)
    storage.update_state.side_effect = PoolExhausted('private-error')
    monkeypatch.setattr(app, 'NEWSLETTER_STORAGE', storage)
    response = app.app.test_client().get('/stock/MSFT', base_url='https://www.stockradarhq.com')
    assert response.status_code == 429
    assert response.headers['Retry-After'] == '1'
    trace, = records(caplog)
    assert trace['counts']['security.rate_limit.pool_exhausted'] == 1
    assert 'route.stock_detail' not in trace['stages']
    assert 'private-error' not in json.dumps(trace)


def test_timing_disabled_keeps_stock_response_headers_unchanged(sources, monkeypatch, caplog):
    monkeypatch.delenv('STOCKRADAR_TIMING_ENABLED')
    response = app.app.test_client().get('/stock/MSFT', base_url='https://www.stockradarhq.com')
    assert response.status_code == 200
    assert 'X-StockRadar-Trace' not in response.headers
    assert not records(caplog)
