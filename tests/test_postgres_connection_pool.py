"""Pool lifecycle and real storage/rate-limiter behaviour with a transactional DB double."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import re
import os
import threading
import time
from types import SimpleNamespace

import pytest

import app
import performance_timing as timing
from newsletter_storage import PostgresNewsletterStorage
from postgres_connection_pool import PoolExhausted, TransactionConnectionPool


class Database:
    def __init__(self):
        self.state = {}
        self.lock = threading.Lock()
        self.connections = []
        self.statements = []

    def connect(self, *args, **kwargs):
        connection = Connection(self)
        self.connections.append(connection)
        return connection


class Connection:
    def __init__(self, db):
        self.db = db
        self.closed = False
        self.broken = False
        self.info = SimpleNamespace(transaction_status=0)
        self.pending = {}
        self.locked = False
        self.fail_reset = False
        self.fail_commit = False
        self.fail_unlock = False

    def cursor(self):
        return Cursor(self)

    def commit(self):
        if self.fail_commit:
            self.broken = True
            raise RuntimeError('private-database-error')
        self.db.state.update(self.pending)
        self.pending = {}
        self._finish()

    def rollback(self):
        if self.fail_reset:
            raise RuntimeError('private-reset-error')
        self.pending = {}
        self._finish()

    def _finish(self):
        self.info.transaction_status = 0
        if self.locked:
            self.db.lock.release()
            self.locked = False

    def close(self):
        self.rollback()
        self.closed = True


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        pass

    def execute(self, sql, params=None):
        connection = self.connection
        if connection.broken or connection.closed:
            raise RuntimeError('private-broken-connection')
        connection.info.transaction_status = 2
        sql = ' '.join(sql.split())
        connection.db.statements.append(sql)
        if 'pg_advisory_xact_lock' in sql:
            assert params == ('newsletter-store:rate_limits',)
            connection.db.lock.acquire()
            connection.locked = True
        elif 'pg_try_advisory_lock' in sql:
            connection.locked = connection.db.lock.acquire(blocking=False)
            self.row = (connection.locked,)
        elif 'pg_advisory_unlock' in sql:
            if connection.fail_unlock:
                raise RuntimeError('private-unlock-error')
            connection._finish()
            self.row = (True,)
        elif sql.startswith('SELECT payload'):
            value = connection.db.state.get(params[0])
            self.row = (copy.deepcopy(value),) if value is not None else None
        elif sql.startswith('INSERT INTO stockradar_application_state'):
            payload = params[1]
            connection.pending[params[0]] = copy.deepcopy(getattr(payload, 'obj', payload))
        else:
            raise AssertionError(sql)

    def fetchone(self):
        return self.row


def backend(db):
    return PostgresNewsletterStorage('postgresql://private:secret@hidden/db', db.connect,
                                     pool_connections=True)


def test_cold_warm_acquisition_and_secret_free_timings(monkeypatch, caplog):
    monkeypatch.setenv('STOCKRADAR_TIMING_ENABLED', 'true')
    caplog.set_level(logging.INFO, logger='stockradar.performance')
    db = Database()
    storage = backend(db)
    traces = []
    for _ in range(2):
        with timing.operation('request', 'login', 'POST') as trace:
            assert storage.update_state('rate_limits', lambda state: state.update({'checked': True}))
            traces.append(trace)
    assert len(db.connections) == 1
    assert traces[0]['counts']['db.pool.created'] == 1
    assert traces[0]['counts']['db.connections'] == 1
    assert traces[0]['stages']['db.connect']['calls'] == 1
    assert traces[1]['counts']['db.pool.reused'] == 1
    assert 'db.connect' not in traces[1]['stages']
    assert 'db.connections' not in traces[1]['counts']
    assert all(row['counts']['db.queries'] == 3 for row in traces)
    assert all(row['stages']['db.pool.acquire']['calls'] == 1 for row in traces)
    logged = '\n'.join(record.getMessage() for record in caplog.records)
    for private in ['private', 'secret', 'hidden', 'postgresql://', 'SELECT', 'checked']:
        assert private not in logged


def test_disabled_pool_timing(monkeypatch, caplog):
    monkeypatch.delenv('STOCKRADAR_TIMING_ENABLED', raising=False)
    storage = backend(Database())
    with timing.operation('request', 'login', 'POST'):
        assert storage.update_state('rate_limits', lambda state: None)
    assert not [record for record in caplog.records if record.name == 'stockradar.performance']


def test_bounded_exhaustion_and_wakeup():
    db = Database()
    pool = TransactionConnectionPool(db.connect, timeout=.03)
    first, second = pool.acquire(), pool.acquire()
    with pytest.raises(PoolExhausted):
        pool.acquire()
    assert len(db.connections) == pool.size == 2
    with ThreadPoolExecutor(max_workers=1) as executor:
        waiting = executor.submit(pool.acquire)
        first.close()
        third = waiting.result(timeout=1)
    assert third.original is first.original
    third.close()
    second.close()


def test_storage_exhaustion_fails_closed_for_limits_and_recovers():
    storage = backend(Database())
    storage.connection_pool.timeout = .01
    leases = [storage._connect(), storage._connect()]
    with pytest.raises(PoolExhausted):
        storage.update_state('rate_limits', lambda state: None)
    assert storage.last_error == 'database_pool_exhausted'
    assert storage.update_state('premium_entitlements', lambda state: None) is False
    for lease in leases:
        lease.close()
    assert storage.update_state('rate_limits', lambda state: None)


@pytest.mark.parametrize('damage', ['closed', 'broken', 'fail_reset'])
def test_unusable_connections_are_discarded(damage):
    db = Database()
    pool = TransactionConnectionPool(db.connect)
    lease = pool.acquire()
    old = lease.original
    setattr(old, damage, True)
    lease.close()
    fresh = pool.acquire()
    assert fresh.original is not old
    assert len(db.connections) == 2
    assert pool.size == 1
    fresh.close()


def test_broken_idle_connection_is_replaced():
    db = Database()
    pool = TransactionConnectionPool(db.connect)
    lease = pool.acquire()
    lease.close()
    lease.original.broken = True
    replacement = pool.acquire()
    assert replacement.original is not lease.original
    replacement.close()


def test_failed_connection_creation_does_not_leak_capacity():
    db = Database()
    attempts = []
    def connect():
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError('private-connect-error')
        return db.connect()
    pool = TransactionConnectionPool(connect)
    with pytest.raises(RuntimeError):
        pool.acquire()
    assert pool.size == 0
    lease = pool.acquire()
    assert pool.size == 1
    lease.close()


def test_commit_rollback_and_transaction_lock_isolation():
    db = Database()
    storage = backend(db)
    assert storage.update_state('rate_limits', lambda state: state['buckets'].update({'good': 1}))
    def fail(state):
        state['buckets']['bad'] = 2
        raise RuntimeError('private-updater-error')
    assert not storage.update_state('rate_limits', fail)
    assert db.state['rate_limits']['buckets'] == {'good': 1}
    assert not db.lock.locked()
    assert storage.update_state('rate_limits', lambda state: state['buckets'].update({'next': 3}))
    assert db.state['rate_limits']['buckets'] == {'good': 1, 'next': 3}
    assert len(db.connections) == 1
    assert db.connections[0].info.transaction_status == 0


def test_release_rolls_back_uncommitted_transaction_and_close_is_idempotent():
    db = Database()
    storage = backend(db)
    lease = storage._connect()
    with lease.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                       ('newsletter-store:rate_limits',))
        cursor.execute('INSERT INTO stockradar_application_state VALUES (%s, %s)',
                       ('rate_limits', {'buckets': {'uncommitted': 1}}))
    lease.close()
    lease.close()
    assert not db.state
    assert not db.lock.locked()
    assert storage.connection_pool.size == 1
    assert len(storage.connection_pool.idle) == 1


def test_failed_commit_is_discarded_without_write_retry():
    db = Database()
    storage = backend(db)
    lease = storage._connect()
    lease.original.fail_commit = True
    lease.close()
    assert not storage.update_state('rate_limits', lambda state: None)
    assert not db.state
    assert storage.connection_pool.size == 0
    assert storage.update_state('rate_limits', lambda state: None)
    assert len(db.connections) == 2


def test_session_advisory_locks_are_dedicated_even_when_unlock_fails():
    db = Database()
    storage = backend(db)
    token = storage.acquire_lock('test-send')
    assert token
    assert storage.connection_pool.size == 0
    assert token['connection'] is db.connections[0]
    db.connections[0].fail_unlock = True
    storage.release_lock(token)
    assert db.connections[0].closed
    assert storage.connection_pool.size == 0


def test_fork_does_not_reuse_sockets_or_inherited_condition():
    db = Database()
    pool = TransactionConnectionPool(db.connect)
    lease = pool.acquire()
    lease.close()
    old_condition = pool.condition
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        try:
            os.close(read_fd)
            fresh = pool.acquire()
            result = [fresh.original is not lease.original, pool.condition is not old_condition,
                      pool.size == 1, len(pool.inherited) == 1]
            fresh.close()
            lease.close()
            os.write(write_fd, json.dumps(result).encode())
        finally:
            os._exit(0)
    os.close(write_fd)
    result = os.read(read_fd, 1024)
    os.close(read_fd)
    _, status = os.waitpid(child, 0)
    assert status == 0
    assert json.loads(result) == [True] * 4
    assert pool.pid == os.getpid()
    again = pool.acquire()
    assert again.original is lease.original
    again.close()


def test_concurrent_durable_limits_across_two_worker_pools(monkeypatch):
    db = Database()
    storages = [backend(db), backend(db)]
    monkeypatch.setattr(app, 'IS_PRODUCTION', True)
    selected = threading.local()
    class WorkerStorage:
        durable = True
        def update_state(self, name, updater):
            return storages[selected.worker].update_state(name, updater)
    monkeypatch.setattr(app, 'NEWSLETTER_STORAGE', WorkerStorage())
    barrier = threading.Barrier(4)
    def attempt(index):
        selected.worker = index % 2
        with app.app.test_request_context('/api/market-news'):
            barrier.wait(timeout=2)
            return app.consume_rate_limit('controlled-same-scope', 3, 60, now=100)
    with ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(executor.map(attempt, range(4)))
    assert sum(row['allowed'] for row in outcomes) == 3
    assert [row['retry_after'] for row in outcomes if not row['allowed']] == [60]
    buckets = db.state['rate_limits']['buckets']
    assert len(buckets) == 1
    assert next(iter(buckets.values()))['count'] == 3
    assert len(db.connections) <= 4
    assert not db.lock.locked()


def test_actual_limiter_expiry_scope_and_429_retry_after(monkeypatch):
    storage = backend(Database())
    monkeypatch.setattr(app, 'IS_PRODUCTION', True)
    monkeypatch.setattr(app, 'NEWSLETTER_STORAGE', storage)
    with app.app.test_request_context('/api/market-news'):
        assert app.consume_rate_limit('test-a', 1, 60, now=100)['allowed']
        assert app.consume_rate_limit('test-a', 1, 60, now=101) == {'allowed': False, 'retry_after': 59}
        assert app.consume_rate_limit('test-b', 1, 60, now=101)['allowed']
        assert app.consume_rate_limit('test-a', 1, 60, now=160)['allowed']
    monkeypatch.setitem(app.RATE_LIMIT_RULES, ('api_market_news', 'GET'), (1, 60, 'test-route'))
    client = app.app.test_client()
    first = client.get('/api/market-news')
    assert first.status_code == 200
    limited = client.get('/api/market-news')
    assert limited.status_code == 429
    assert int(limited.headers['Retry-After']) >= 1
    assert len(storage.connection_pool.idle) == 1


def test_pooled_login_failure_protection_csrf_and_forced_refresh(monkeypatch):
    storage = backend(Database())
    monkeypatch.setattr(app, 'IS_PRODUCTION', True)
    monkeypatch.setattr(app, 'NEWSLETTER_STORAGE', storage)
    monkeypatch.setattr(app, 'LOGIN_RATE_LIMIT_STATE', {'ip': {}, 'identity': {}})
    monkeypatch.setattr(app, 'OWNER_EMAIL', 'owner@example.test')
    monkeypatch.setattr(app, 'OWNER_PASSWORD_HASH', '')
    monkeypatch.setattr(app, 'OWNER_PASSWORD', 'controlled-correct-password')
    monkeypatch.setitem(app.app.config, 'WTF_CSRF_ENABLED', True)
    client = app.app.test_client()
    page = client.get('/login')
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    assert client.post('/login', data={'email': 'owner@example.test', 'password': 'wrong'}).status_code == 400
    assert not storage.connection_pool.size
    for index in range(app.LOGIN_RATE_LIMIT_MAX_FAILURES):
        response = client.post('/login', data={'csrf_token': token, 'email': 'owner@example.test', 'password': 'wrong'})
        assert response.status_code == (429 if index == app.LOGIN_RATE_LIMIT_MAX_FAILURES - 1 else 200)
    assert int(response.headers['Retry-After']) > 0
    # Even valid credentials remain blocked after the existing failure threshold.
    response = client.post('/login', data={'csrf_token': token, 'email': 'owner@example.test', 'password': 'controlled-correct-password'})
    assert response.status_code == 429
    for path in ['/?refresh=1', '/api/market-news?refresh=1']:
        assert client.get(path).status_code == 403
    assert len(storage.connection_pool.idle) == 1


def test_default_driver_path_enables_pool(monkeypatch):
    import newsletter_storage
    db = Database()
    monkeypatch.setattr(newsletter_storage, 'psycopg', SimpleNamespace(connect=db.connect))
    storage = PostgresNewsletterStorage('postgresql://controlled-test')
    assert storage.connection_pool.max_size == 2
    assert storage.connection_pool.timeout == 5
    assert storage.update_state('rate_limits', lambda state: None)
    assert storage.update_state('rate_limits', lambda state: None)
    assert len(db.connections) == 1


def test_parallel_cold_creation_respects_bound_and_exclusive_borrowing():
    db = Database()
    gate = threading.Lock()
    active = set()
    high_water = []
    def connect():
        time.sleep(.01)
        return db.connect()
    pool = TransactionConnectionPool(connect)
    def borrow(_):
        lease = pool.acquire()
        with gate:
            assert id(lease.original) not in active
            active.add(id(lease.original))
            high_water.append(len(active))
        time.sleep(.01)
        with gate:
            active.remove(id(lease.original))
        lease.close()
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(borrow, range(16)))
    assert len(db.connections) == 2
    assert max(high_water) == 2
    assert len(pool.idle) == pool.size == 2


def test_inherited_active_lease_cannot_execute_in_child():
    db = Database()
    pool = TransactionConnectionPool(db.connect)
    lease = pool.acquire()
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        try:
            os.close(read_fd)
            try:
                lease.cursor()
            except RuntimeError:
                lease.close()
                fresh = pool.acquire()
                result = fresh.original is not lease.original and not lease.original.closed
                fresh.close()
                os.write(write_fd, str(int(result)).encode())
        finally:
            os._exit(0)
    os.close(write_fd)
    result = os.read(read_fd, 1024)
    os.close(read_fd)
    _, status = os.waitpid(child, 0)
    assert status == 0 and result == b'1'
    assert not lease.original.closed
    lease.close()


def test_pool_exhaustion_cannot_bypass_durable_limiter(monkeypatch):
    storage = backend(Database())
    storage.connection_pool.timeout = .01
    leases = [storage.connection_pool.acquire(), storage.connection_pool.acquire()]
    monkeypatch.setattr(app, 'IS_PRODUCTION', True)
    monkeypatch.setattr(app, 'NEWSLETTER_STORAGE', storage)
    try:
        client = app.app.test_client()
        response = client.get('/api/market-news')
        assert response.status_code == 429
        assert response.headers['Retry-After'] == '1'
        assert app.SECURITY_RATE_LIMIT_STATE['buckets'] == {}
    finally:
        for lease in leases:
            lease.close()
    assert client.get('/api/market-news').status_code == 200
    assert storage.load_state('rate_limits')['buckets']
