"""Opt-in PostgreSQL failure tests against an isolated test database.

Set ORCHEO_TEST_POSTGRES_DSN to a disposable PostgreSQL instance. These tests
terminate only the backend connection they create; they never read the
application's ORCHEO_POSTGRES_DSN.
"""

from __future__ import annotations
import os
import time
from collections.abc import Iterator
import psycopg
import pytest
from psycopg.errors import QueryCanceled
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout
from orcheo.identity.postgres_store import PostgresIdentityRepository
from orcheo.postgres_pools import connection_kwargs, pool_kwargs


@pytest.fixture
def pool() -> Iterator[ConnectionPool]:
    """Create a single-connection pool to force reuse after idle failure."""
    dsn = os.environ.get("ORCHEO_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("ORCHEO_TEST_POSTGRES_DSN is not set to an isolated test database.")
    options = pool_kwargs()
    options["timeout"] = 0.2
    with ConnectionPool(
        dsn,
        min_size=1,
        max_size=1,
        kwargs=connection_kwargs(autocommit=False, row_factory=dict_row),
        **options,
    ) as connection_pool:
        connection_pool.wait(timeout=5)
        yield connection_pool


def test_identity_statement_timeout_is_bounded_and_transaction_local(
    pool: ConnectionPool,
) -> None:
    """Cancel slow identity SQL and return a usable connection to the pool."""
    # Avoid schema changes: only exercise the repository's real transaction boundary.
    repository = PostgresIdentityRepository.__new__(PostgresIdentityRepository)
    repository._pool = pool
    repository._statement_timeout_ms = 100
    repository._lock_timeout_ms = 50
    with pool.connection() as connection:
        before = connection.execute("SHOW statement_timeout").fetchone()

    started = time.monotonic()
    with pytest.raises(QueryCanceled):
        with repository._connect() as connection:
            assert connection.execute("SHOW lock_timeout").fetchone() == {
                "lock_timeout": "50ms"
            }
            connection.execute("SELECT pg_sleep(5)")
    assert time.monotonic() - started < 2
    with pool.connection() as connection:
        assert connection.execute("SELECT 1 AS alive").fetchone() == {"alive": 1}
        assert connection.execute("SHOW statement_timeout").fetchone() == before


def test_pool_discards_dead_idle_connection_and_recovers(pool: ConnectionPool) -> None:
    """Checkout validation replaces an idle connection killed by PostgreSQL."""
    with pool.connection() as connection:
        row = connection.execute("SELECT pg_backend_pid() AS pid").fetchone()
        assert row is not None
        pid = row["pid"]
    # Terminate precisely this test's pooled connection, using a separate connection.
    with psycopg.connect(
        os.environ["ORCHEO_TEST_POSTGRES_DSN"], autocommit=True
    ) as admin:
        admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
    with pool.connection(timeout=5) as connection:
        row = connection.execute("SELECT pg_backend_pid() AS pid").fetchone()
        assert row is not None
        assert row["pid"] != pid
        assert connection.execute("SELECT 1 AS alive").fetchone() == {"alive": 1}


def test_pool_exhaustion_fails_within_acquisition_budget(pool: ConnectionPool) -> None:
    """A busy pool must not keep API threads waiting indefinitely."""
    with pool.connection():
        started = time.monotonic()
        with pytest.raises(PoolTimeout):
            with pool.connection():
                pytest.fail(
                    "Single-connection pool unexpectedly lent another connection"
                )
        assert time.monotonic() - started < 1
