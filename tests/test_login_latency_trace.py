"""Safe telemetry and controlled reproduction of login/database/cache stages."""
import json
import logging
import time
from unittest.mock import Mock

import pytest
from werkzeug.security import generate_password_hash
import app
import performance_timing as timing
from newsletter_storage import PostgresNewsletterStorage


class Cursor:
    def __init__(self, payload=None):
        self.payload = payload
        self.entitlement_read = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        # Deliberately slow connection/query boundaries, without real database traffic.
        time.sleep(.005)
        self.entitlement_read = params == ('premium_entitlements',)

    def fetchone(self):
        return (self.payload,) if self.entitlement_read and self.payload is not None else None


class Connection:
    def __init__(self, payload=None):
        self.payload = payload

    def cursor(self):
        return Cursor(self.payload)

    def commit(self):
        time.sleep(.003)

    def rollback(self):
        pass

    def close(self):
        pass


def request_records(caplog):
    return [json.loads(record.getMessage()) for record in caplog.records
            if record.name == 'stockradar.performance'
            and json.loads(record.getMessage())['scope'] == 'request']


@pytest.fixture
def sources(monkeypatch):
    # Avoid leaving display caches or recommendation mutations for unrelated tests.
    for name in ['STOCK_UNIVERSE_CACHE', 'STOCK_DISPLAY_LOOKUP_CACHE',
                 'STOCK_IDENTITY_LOOKUP_CACHE', 'COMPANY_LOGO_METADATA_CACHE',
                 'RECOMMENDATIONS_CACHE']:
        monkeypatch.setattr(app, name, dict(getattr(app, name)))
    monkeypatch.setattr(app, 'DASHBOARD_REFRESH_PENDING', True)
    data = app.prepare_dashboard_data(include_market_snapshots=False, local_only=True)
    monkeypatch.setattr(app, 'DASHBOARD_CACHE', {'data': data, 'timestamp': time.time()})
    monkeypatch.setattr(app, 'OWNER_EMAIL', 'timing-user@example.test')
    monkeypatch.setattr(app, 'OWNER_PASSWORD_HASH', generate_password_hash('timing-test-password'))
    monkeypatch.setattr(app, 'OWNER_PASSWORD', '')
    monkeypatch.setattr(app, 'IS_PRODUCTION', True)
    connector = Mock(side_effect=lambda *args, **kwargs: (time.sleep(.03), Connection())[1])
    storage = PostgresNewsletterStorage('postgresql://hidden:password@private-host/private-db', connector)
    monkeypatch.setattr(app, 'NEWSLETTER_STORAGE', storage)
    return connector, data


@pytest.mark.parametrize('cold', [False, True])
def test_login_homepage_breakdown_redacts_all_inputs(sources, monkeypatch, caplog, cold):
    connector, data = sources
    monkeypatch.setenv('STOCKRADAR_TIMING_ENABLED', 'true')
    caplog.set_level(logging.INFO, logger='stockradar.performance')
    if cold:
        monkeypatch.setattr(app, 'DASHBOARD_CACHE', {})
    client = app.app.test_client()
    started = time.perf_counter()
    login = client.post('/login?ignored=secret-query', base_url='https://www.stockradarhq.com', data={
        'email': 'timing-user@example.test', 'password': 'timing-test-password',
    })
    assert login.status_code == 302
    home = client.get('/', base_url='https://www.stockradarhq.com')
    assert home.status_code == 200
    records = request_records(caplog)
    assert [row['route'] for row in records] == ['login', 'homepage']
    post, get = records
    assert login.headers['X-StockRadar-Trace'] == post['trace_id']
    assert home.headers['X-StockRadar-Trace'] == get['trace_id']
    assert post['counts']['db.connections'] == 1
    assert post['counts']['db.queries'] == 3  # shared limit: lock, read, upsert
    assert post['stages']['db.connect']['ms'] >= 25
    assert post['stages']['db.advisory_lock']['calls'] == 1
    assert post['stages']['auth.password_verify']['calls'] == 1
    assert post['stages']['session.serialize_save']['calls'] == 1
    assert post['stages']['response.redirect']['calls'] == 1
    assert 'db.connections' not in get['counts']  # owner bypass unchanged
    assert get['stages']['session.open_verify']['calls'] == 1
    assert get['stages']['template.compile_render']['calls'] > 0
    assert get['counts']['dashboard.cache.cold' if cold else 'dashboard.cache.warm'] == 1
    assert not any(name.startswith('provider.') for row in records for name in row['stages'])
    assert connector.call_count == 1
    logged = json.dumps(records)
    for sensitive in ['timing-user', 'timing-test-password', 'private-host', 'hidden',
                      'secret-query', 'INSERT INTO', 'SELECT payload', 'Cookie']:
        assert sensitive not in logged
    print(json.dumps({'path': 'cold' if cold else 'warm', 'click_to_responses_ms': round((time.perf_counter()-started)*1000, 3),
                      'login': post, 'homepage': get}, sort_keys=True))


def test_instrumentation_disabled_preserves_session_behavior(sources, monkeypatch, caplog):
    monkeypatch.delenv('STOCKRADAR_TIMING_ENABLED', raising=False)
    client = app.app.test_client()
    response = client.post('/login', base_url='https://www.stockradarhq.com', data={
        'email': 'timing-user@example.test', 'password': 'timing-test-password',
    })
    assert response.status_code == 302
    assert 'X-StockRadar-Trace' not in response.headers
    assert not request_records(caplog)
    with client.session_transaction(base_url='https://www.stockradarhq.com') as session:
        assert session['owner_logged_in'] is True


def test_database_failure_records_duration_without_exception_text(monkeypatch, caplog):
    monkeypatch.setenv('STOCKRADAR_TIMING_ENABLED', 'true')
    caplog.set_level(logging.INFO, logger='stockradar.performance')
    def failing(*args, **kwargs):
        time.sleep(.02)
        raise RuntimeError('postgresql://sensitive:password@secret-aiven')
    storage = PostgresNewsletterStorage('private-dsn', failing)
    with timing.operation('request', 'login', 'POST') as trace:
        assert storage.load_state('premium_entitlements') == {'records': []}
        trace['status'] = 200
    row = request_records(caplog)[0]
    assert row['stages']['db.connect']['errors'] == 1
    assert row['stages']['db.connect']['ms'] >= 15
    assert 'secret-aiven' not in json.dumps(row)
    assert 'sensitive' not in json.dumps(row)


def test_nested_timings_separate_inclusive_and_self_time(monkeypatch, caplog):
    monkeypatch.setenv('STOCKRADAR_TIMING_ENABLED', 'true')
    caplog.set_level(logging.INFO, logger='stockradar.performance')
    with timing.operation('request', 'login', 'POST'):
        with timing.stage('outer'):
            with timing.stage('inner'):
                time.sleep(.02)
    row = request_records(caplog)[0]
    assert row['stages']['inner']['ms'] >= 15
    assert row['stages']['outer']['ms'] >= row['stages']['inner']['ms']
    assert row['stages']['outer']['self_ms'] < row['stages']['outer']['ms']



def test_premium_homepage_reads_entitlement_once_without_logging_identity(sources, monkeypatch, caplog):
    monkeypatch.setenv('STOCKRADAR_TIMING_ENABLED', 'true')
    caplog.set_level(logging.INFO, logger='stockradar.performance')
    payload = {'records': [{'customer_email': 'premium-identity@example.test',
                           'premium_active': True, 'entitlement_version': 1,
                           'updated_at': '2026-10-01T12:00:00Z'}]}
    connector = Mock(return_value=Connection(payload))
    monkeypatch.setattr(app, 'NEWSLETTER_STORAGE', PostgresNewsletterStorage('private-dsn', connector))
    client = app.app.test_client()
    with client.session_transaction(base_url='https://www.stockradarhq.com') as session:
        session['premium_email'] = 'premium-identity@example.test'
    response = client.get('/', base_url='https://www.stockradarhq.com')
    assert response.status_code == 200
    row = request_records(caplog)[0]
    assert row['counts']['db.connections'] == 1
    assert row['counts']['db.queries'] == 1
    assert row['counts']['db.store.premium_entitlements'] == 1
    assert row['stages']['premium.access_check']['calls'] == 1
    assert row['stages']['premium.entitlement_lookup']['calls'] == 1
    assert 'premium-identity' not in json.dumps(row)
    with client.session_transaction(base_url='https://www.stockradarhq.com') as session:
        assert session['premium_active'] is True
