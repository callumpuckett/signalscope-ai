"""Opt-in timings. Never log SQL, arguments, URLs, cookies or exception messages."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import logging
import os
import secrets
import time

_ACTIVE = ContextVar('stockradar_timing', default=None)
_WORKER_STARTED = time.perf_counter()
_LOG = logging.getLogger('stockradar.performance')
_LOG.setLevel(logging.INFO)
if not _LOG.handlers:
    _LOG.addHandler(logging.StreamHandler())


def enabled():
    return os.environ.get('STOCKRADAR_TIMING_ENABLED', '').lower() == 'true'


def count(name):
    trace = _ACTIVE.get()
    if trace is not None:
        trace['counts'][name] = trace['counts'].get(name, 0) + 1


@contextmanager
def stage(name):
    trace = _ACTIVE.get()
    if trace is None:
        yield
        return
    started = time.perf_counter()
    frame = {'children': 0.0}
    trace['stack'].append(frame)
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        duration = time.perf_counter() - started
        trace['stack'].pop()
        if trace['stack']:
            trace['stack'][-1]['children'] += duration
        item = trace['stages'].setdefault(name, {'calls': 0, 'ms': 0.0, 'self_ms': 0.0, 'errors': 0})
        item['calls'] += 1
        item['ms'] += duration * 1000
        item['self_ms'] += max(0, duration - frame['children']) * 1000
        item['errors'] += int(failed)


def measured(name):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with stage(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate


@contextmanager
def operation(scope, route='', method='', trace_id=None):
    if not enabled():
        yield None
        return
    started = time.perf_counter()
    trace = {'event': 'stockradar_timing', 'scope': scope, 'route': route,
             'method': method, 'trace_id': trace_id or secrets.token_hex(8),
             'pid': os.getpid(), 'worker_age_ms': round((started - _WORKER_STARTED) * 1000, 3),
             'counts': {}, 'stages': {}, 'stack': [], 'status': 500 if scope == 'request' else None}
    token = _ACTIVE.set(trace)
    try:
        yield trace
    finally:
        _ACTIVE.reset(token)
        trace.pop('stack')
        trace['server_response_ms'] = round((time.perf_counter() - started) * 1000, 3)
        for item in trace['stages'].values():
            for key in ('ms', 'self_ms'):
                item[key] = round(item[key], 3)
        _LOG.info(json.dumps(trace, sort_keys=True))


class TimingMiddleware:
    """Measures Flask dispatch through session serialization, before socket delivery.

    Time before WSGI entry (Render queue/cold boot) and browser paint is excluded.
    A random response ID correlates this server log with a browser network trace.
    """
    ROUTES = {'/': 'homepage', '/login': 'login', '/api/market-news': 'market_news',
              '/newsletter/latest': 'newsletter_latest'}

    def __init__(self, application):
        self.application = application

    def __call__(self, environ, start_response):
        route = self.ROUTES.get(environ.get('PATH_INFO'))
        if not route or not enabled():
            return self.application(environ, start_response)
        method = environ.get('REQUEST_METHOD')
        method = method if method in {'GET', 'POST', 'HEAD'} else 'other'
        with operation('request', route, method) as trace:
            def traced_start_response(status, headers, exc_info=None):
                trace['status'] = int(status.split(' ', 1)[0])
                return start_response(status, headers + [('X-StockRadar-Trace', trace['trace_id'])], exc_info)
            with stage('request.flask_dispatch'):
                return self.application(environ, traced_start_response)


class TimedSessionInterface:
    """Delegate the existing session implementation without changing cookie/security rules."""
    def __init__(self, original):
        self.original = original

    def __getattr__(self, name):
        return getattr(self.original, name)

    @measured('session.open_verify')
    def open_session(self, *args, **kwargs):
        return self.original.open_session(*args, **kwargs)

    @measured('session.serialize_save')
    def save_session(self, *args, **kwargs):
        return self.original.save_session(*args, **kwargs)


class TimedCursor:
    def __init__(self, original):
        self.original = original

    def __getattr__(self, name):
        return getattr(self.original, name)

    def __enter__(self):
        self.original.__enter__()
        return self

    def __exit__(self, *args):
        return self.original.__exit__(*args)

    def execute(self, query, *args, **kwargs):
        count('db.queries')
        name = 'db.advisory_lock' if isinstance(query, str) and 'pg_advisory' in query else 'db.query'
        with stage(name):
            return self.original.execute(query, *args, **kwargs)

    @measured('db.fetch')
    def fetchone(self, *args, **kwargs):
        return self.original.fetchone(*args, **kwargs)

    @measured('db.fetch')
    def fetchall(self, *args, **kwargs):
        return self.original.fetchall(*args, **kwargs)


class TimedConnection:
    def __init__(self, original):
        self.original = original

    def __getattr__(self, name):
        return getattr(self.original, name)

    @measured('db.cursor')
    def cursor(self, *args, **kwargs):
        return TimedCursor(self.original.cursor(*args, **kwargs))

    @measured('db.commit')
    def commit(self, *args, **kwargs):
        return self.original.commit(*args, **kwargs)

    @measured('db.rollback')
    def rollback(self, *args, **kwargs):
        return self.original.rollback(*args, **kwargs)

    @measured('db.close')
    def close(self, *args, **kwargs):
        return self.original.close(*args, **kwargs)


def instrument_connection(connection):
    return TimedConnection(connection) if _ACTIVE.get() is not None else connection
