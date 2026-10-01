"""Opt-in real PostgreSQL regression; requires an explicitly designated test DB."""
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import uuid

import pytest
import app
from newsletter_storage import PostgresNewsletterStorage


def test_real_postgres_pooled_durable_limits_and_rollback(monkeypatch):
    database_url = os.environ.get('STOCKRADAR_TEST_DATABASE_URL', '')
    if not database_url:
        pytest.skip('Explicit isolated PostgreSQL test database not configured')
    psycopg = pytest.importorskip('psycopg')
    schema = 'pool_test_' + uuid.uuid4().hex
    from psycopg import sql
    # Only a designated test database is used, never the production DATABASE_URL.
    admin = psycopg.connect(database_url, autocommit=True, connect_timeout=5)
    pools = []
    try:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        def connector(url, connect_timeout):
            return psycopg.connect(url, connect_timeout=connect_timeout,
                                   options='-csearch_path=' + schema)
        storages = [PostgresNewsletterStorage(database_url, connector, pool_connections=True)
                    for _ in range(2)]
        pools = [storage.connection_pool for storage in storages]
        assert storages[0].initialize_schema()
        selected = threading.local()
        class Workers:
            durable = True
            def update_state(self, name, updater):
                return storages[selected.worker].update_state(name, updater)
        monkeypatch.setattr(app, 'IS_PRODUCTION', True)
        monkeypatch.setattr(app, 'NEWSLETTER_STORAGE', Workers())
        def consume(index):
            selected.worker = index % 2
            with app.app.test_request_context('/api/market-news'):
                return app.consume_rate_limit('integration', 7, 60, now=100)
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(consume, range(24)))
        assert sum(result['allowed'] for result in results) == 7
        assert all(result['retry_after'] == 60 for result in results if not result['allowed'])
        assert all(pool.size <= 2 for pool in pools)
        storage = storages[0]
        def fail(state):
            state['must_not_commit'] = True
            raise RuntimeError('controlled-updater-failure')
        assert not storage.update_state('rate_limits', fail)
        assert 'must_not_commit' not in storage.load_state('rate_limits')
        # A second session can immediately obtain the same advisory transaction
        # lock after both successful and failed pooled transactions.
        with admin.transaction():
            row = admin.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))',
                                ('newsletter-store:rate_limits',)).fetchone()
            assert row[0] is True
        first = storage.connection_pool.acquire()
        raw = first.original
        first.close()
        again = storage.connection_pool.acquire()
        assert again.original is raw
        assert raw.info.transaction_status == 0
        again.close()
    finally:
        for pool in pools:
            for connection in pool.idle:
                connection.close()
            pool.idle.clear()
        admin.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
        admin.close()
