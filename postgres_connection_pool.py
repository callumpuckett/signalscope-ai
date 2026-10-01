"""Small synchronous pool for transaction-scoped PostgreSQL storage operations."""
import os
import threading
import time

from performance_timing import count, stage


class PoolExhausted(RuntimeError):
    pass


class TransactionConnectionPool:
    """At most two owned transaction connections per process; no background threads.

    Session advisory locks must use dedicated connections outside this pool.
    Inherited sockets are retained but never used/closed in the child: sending a
    PostgreSQL close message there could interfere with the parent's session.
    """
    def __init__(self, connect, max_size=2, timeout=5):
        self.connect = connect
        self.max_size = max_size
        self.timeout = timeout
        self.pid = os.getpid()
        self.condition = threading.Condition()
        self.idle = []
        self.size = 0
        self.inherited = []
        if hasattr(os, 'register_at_fork'):
            os.register_at_fork(after_in_child=self._after_fork)

    def _after_fork(self):
        self.inherited.extend(self.idle)
        self.idle = []
        self.size = 0
        self.pid = os.getpid()
        self.condition = threading.Condition()

    @staticmethod
    def usable(connection):
        return not connection.closed and not connection.broken

    def acquire(self):
        if self.pid != os.getpid():
            self._after_fork()
        deadline = time.monotonic() + self.timeout
        with stage('db.pool.acquire'):
            while True:
                with self.condition:
                    if self.idle:
                        connection = self.idle.pop()
                        if self.usable(connection):
                            count('db.pool.reused')
                            return PoolLease(self, connection)
                        self._discard(connection)
                        continue
                    if self.size < self.max_size:
                        self.size += 1  # Reserve a slot before connecting outside the lock.
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        count('db.pool.exhausted')
                        raise PoolExhausted('database_pool_exhausted')
                    with stage('db.pool.wait'):
                        self.condition.wait(remaining)
            try:
                connection = self.connect()
            except BaseException:
                with self.condition:
                    self.size -= 1
                    self.condition.notify()
                raise
            count('db.pool.created')
            return PoolLease(self, connection)

    def _discard(self, connection):
        # Caller owns condition; never expose driver exception text.
        try:
            connection.close()
        except Exception:
            pass
        self.size -= 1
        count('db.pool.discarded')
        self.condition.notify()

    def release(self, connection):
        clean = False
        with stage('db.pool.release'):
            try:
                if self.usable(connection):
                    # psycopg rollback on an idle connection sends no SQL. On an
                    # open/failed transaction it releases transaction locks.
                    with stage('db.pool.reset'):
                        connection.rollback()
                    clean = self.usable(connection) and connection.info.transaction_status == 0
            except Exception:
                pass
            with self.condition:
                if clean:
                    self.idle.append(connection)
                    self.condition.notify()
                else:
                    self._discard(connection)


class PoolLease:
    def __init__(self, pool, connection):
        self.pool = pool
        self.original = connection
        self.pid = os.getpid()
        self.returned = False

    def __getattr__(self, name):
        if self.pid != os.getpid() or self.returned:
            raise RuntimeError('database_lease_unavailable')
        return getattr(self.original, name)

    def close(self):
        if self.returned:
            return
        self.returned = True
        if self.pid != os.getpid():
            self.pool.inherited.append(self.original)
            return
        self.pool.release(self.original)
