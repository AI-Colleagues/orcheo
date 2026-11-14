"""The PostgreSQL vault's transaction-scoped credential lock."""

from __future__ import annotations
import hashlib
from typing import Any
import pytest
from orcheo.models import AesGcmCredentialCipher
from orcheo.vault.postgres import PostgresCredentialVault


class Transaction:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def __enter__(self) -> Transaction:
        self.connection.events.append("begin")
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.connection.events.append("rollback" if exc_type else "commit")


class Connection:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def transaction(self) -> Transaction:
        return Transaction(self)

    def execute(self, query: str, params: Any = None) -> None:
        self.events.append((query.strip(), params))


class Pool:
    def __init__(self) -> None:
        self.connection_obj = Connection()
        self.checkouts = 0

    def connection(self) -> Pool:
        self.checkouts += 1
        return self

    def __enter__(self) -> Connection:
        return self.connection_obj

    def __exit__(self, *exc: Any) -> None:
        return None


@pytest.fixture
def vault() -> PostgresCredentialVault:
    vault = PostgresCredentialVault(
        dsn="postgresql://test", cipher=AesGcmCredentialCipher(key="test-key")
    )
    # The constructor opens a real pool; close it before swapping in the fake.
    vault._pool.close()
    vault._pool = Pool()  # type: ignore[assignment]
    vault._initialized = True
    return vault


def _lock_id(key: str) -> int:
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)


def test_the_lock_is_a_transaction_scoped_advisory_lock_on_the_key(
    vault: PostgresCredentialVault,
) -> None:
    pool: Pool = vault._pool  # type: ignore[assignment]
    with vault.credential_lock("email-schedule:42"):
        pass
    assert pool.connection_obj.events == [
        "begin",
        ("SELECT pg_advisory_xact_lock(%s)", (_lock_id("email-schedule:42"),)),
        "commit",
    ]
    assert _lock_id("email-schedule:42") != _lock_id("email-schedule:43")


def test_vault_calls_inside_the_lock_share_its_transaction(
    vault: PostgresCredentialVault,
) -> None:
    pool: Pool = vault._pool  # type: ignore[assignment]
    with vault.credential_lock("key"):
        assert pool.checkouts == 1
        with vault._connection() as inner:
            assert inner is pool.connection_obj
        with vault.credential_lock("other"):
            with vault._connection() as nested:
                assert nested is pool.connection_obj
        # Still inside the outer lock after the nested one finishes.
        with vault._connection() as again:
            assert again is pool.connection_obj
    # Nested locks and vault calls reused the one checked-out connection.
    assert pool.checkouts == 1
    # Outside the lock a call checks a connection out of the pool again.
    with vault._connection() as outside:
        assert outside is pool.connection_obj
    assert pool.checkouts == 2


def test_a_failure_rolls_back_and_releases_the_transaction(
    vault: PostgresCredentialVault,
) -> None:
    pool: Pool = vault._pool  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="boom"):
        with vault.credential_lock("key"):
            raise RuntimeError("boom")
    assert pool.connection_obj.events[-1] == "rollback"
    assert vault._transaction.get() is None
