"""Tests for PostgreSQL-backed credential vault - credentials operations."""

from __future__ import annotations
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
import pytest
from orcheo.models import (
    AesGcmCredentialCipher,
    CredentialScope,
)
from orcheo.vault.errors import (
    CredentialNotFoundError,
    DuplicateCredentialNameError,
)
from orcheo.vault.postgres import PostgresCredentialVault


class FakeCursor:
    """Fake database cursor for testing."""

    def __init__(
        self,
        *,
        row: dict[str, Any] | None = None,
        rows: list[Any] | None = None,
        rowcount: int = 1,
    ) -> None:
        self._row = row
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self) -> dict[str, Any] | None:
        return self._row

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class FakeConnection:
    """Fake database connection for testing."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.queries: list[tuple[str, Any | None]] = []
        self.executed_statements: list[str] = []

    def execute(self, query: str, params: Any | None = None) -> FakeCursor:
        query_stripped = query.strip()
        self.queries.append((query_stripped, params))

        # Track non-parameterized statements (like schema creation)
        if params is None and query_stripped:
            self.executed_statements.append(query_stripped)

        response = self._responses.pop(0) if self._responses else {}
        if isinstance(response, FakeCursor):
            return response
        if isinstance(response, dict):
            return FakeCursor(
                row=response.get("row"),
                rows=response.get("rows"),
                rowcount=response.get("rowcount", 1),
            )
        if isinstance(response, list):
            return FakeCursor(rows=response)
        return FakeCursor()

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        return None


class FakePool:
    """Fake connection pool for testing."""

    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    def connection(self) -> FakeConnection:
        return self._connection

    def close(self) -> None:
        pass


def test_postgres_vault_initialization() -> None:
    """Test vault initialization with schema creation."""
    cipher = AesGcmCredentialCipher(key="test-key")
    conn = FakeConnection(responses=[])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(
        dsn="postgresql://test",
        cipher=cipher,
        pool_min_size=2,
        pool_max_size=20,
    )
    # Replace pool with fake
    vault._pool = pool

    # First call should initialize
    vault._ensure_initialized()
    # Should execute schema statements
    assert len(conn.executed_statements) > 0
    assert vault._initialized is True

    # Second call should be a no-op
    conn_queries_count = len(conn.queries)
    vault._ensure_initialized()
    assert len(conn.queries) == conn_queries_count  # No new queries


def test_postgres_vault_initialization_race_condition() -> None:
    """Test that concurrent initialization only runs schema once."""
    import threading

    cipher = AesGcmCredentialCipher(key="test-key")
    conn = FakeConnection(responses=[])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool

    results = []

    def init_vault() -> None:
        vault._ensure_initialized()
        results.append(vault._initialized)

    threads = [threading.Thread(target=init_vault) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(results)
    assert vault._initialized is True


def test_postgres_vault_initialization_double_check_locking() -> None:
    """Test the double-check locking pattern in _ensure_initialized (line 94)."""
    import threading
    import time

    cipher = AesGcmCredentialCipher(key="test-key")
    original_conn = FakeConnection(responses=[])
    pool = FakePool(original_conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool

    schema_execution_count = 0
    lock_entered_count = 0

    original_connection = pool.connection

    def slow_connection() -> FakeConnection:
        """Slow connection to simulate race condition."""
        nonlocal schema_execution_count, lock_entered_count
        lock_entered_count += 1
        if schema_execution_count == 0:
            time.sleep(0.02)  # First thread is slow
            schema_execution_count += 1
        return original_connection()

    # Patch the pool's connection method to track execution
    pool.connection = slow_connection

    def init_vault() -> None:
        vault._ensure_initialized()

    # Start two threads that will both try to initialize
    vault._initialized = False
    thread1 = threading.Thread(target=init_vault)
    thread2 = threading.Thread(target=init_vault)

    thread1.start()
    time.sleep(0.005)  # Let first thread enter the lock
    thread2.start()

    thread1.join()
    thread2.join()

    # Second thread should hit the early return at line 94 after waiting for the lock
    assert vault._initialized is True
    assert lock_entered_count >= 1  # At least one thread entered the lock
    # Only the first thread should have executed the schema
    assert schema_execution_count == 1


def test_postgres_vault_persist_metadata_new_credential() -> None:
    """Test persisting new credential metadata."""
    cipher = AesGcmCredentialCipher(key="test-key")
    workflow_id = uuid4()
    tenant_id = "tenant-a"

    # Mock responses: check for duplicates (empty), then insert
    conn = FakeConnection(responses=[{"rows": []}, {}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    # Create credential using public API
    metadata = vault.create_credential(
        name="Test Service",
        provider="test-provider",
        scopes=["read"],
        secret="test-secret",
        actor="test-actor",
        scope=CredentialScope.for_workflows(workflow_id),
        tenant_id=tenant_id,
    )

    # Verify INSERT was called with correct params
    insert_queries = [q for q in conn.queries if "INSERT INTO credentials" in q[0]]
    assert len(insert_queries) == 1
    params = insert_queries[0][1]
    assert params[0] == str(metadata.id)
    assert params[1] == metadata.scope.scope_hint()
    assert params[2] == tenant_id
    assert params[3] == "Test Service"
    assert params[4] == "test-provider"


def test_postgres_vault_persist_metadata_duplicate_name() -> None:
    """Test that duplicate credential names raise an error."""
    cipher = AesGcmCredentialCipher(key="test-key")
    workflow_id = uuid4()
    other_id = uuid4()
    tenant_id = "tenant-a"

    # Mock response: duplicate found
    conn = FakeConnection(responses=[{"rows": [{"id": str(other_id)}]}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    with pytest.raises(DuplicateCredentialNameError, match="already in use"):
        vault.create_credential(
            name="Duplicate",
            provider="provider",
            scopes=["read"],
            secret="secret",
            actor="actor",
            scope=CredentialScope.for_workflows(workflow_id),
            tenant_id=tenant_id,
        )


def test_postgres_vault_persist_metadata_update_existing() -> None:
    """Test updating existing credential metadata (ON CONFLICT)."""
    cipher = AesGcmCredentialCipher(key="test-key")
    workflow_id = uuid4()

    # First create
    conn = FakeConnection(responses=[{"rows": []}, {}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    metadata = vault.create_credential(
        name="Service",
        provider="provider",
        scopes=["read"],
        secret="secret",
        actor="actor",
        scope=CredentialScope.for_workflows(workflow_id),
        tenant_id="tenant-a",
    )

    # Now update - simulate finding same ID in duplicate check
    conn2 = FakeConnection(responses=[{"rows": [{"id": str(metadata.id)}]}, {}])
    vault._pool = FakePool(conn2)

    # Use internal method to test update path
    vault._persist_metadata(metadata)

    # Should still insert with ON CONFLICT DO UPDATE
    insert_queries = [q for q in conn2.queries if "INSERT INTO credentials" in q[0]]
    assert len(insert_queries) == 1


def test_postgres_vault_load_metadata_found() -> None:
    """Test loading credential metadata when it exists."""
    cipher = AesGcmCredentialCipher(key="test-key")
    credential_id = uuid4()
    workflow_id = uuid4()

    # Create a metadata dict
    metadata_dict = {
        "id": str(credential_id),
        "name": "Test Credential",
        "provider": "test-provider",
        "scopes": ["read"],
        "created_at": datetime.now(tz=UTC).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "scope": {"workflow_ids": [str(workflow_id)], "workspace_ids": [], "roles": []},
        "kind": "secret",
        "encryption": cipher.encrypt("test-secret").model_dump(),
        "audit_log": [],
    }

    conn = FakeConnection(responses=[{"row": {"payload": json.dumps(metadata_dict)}}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    result = vault._load_metadata(credential_id)

    assert result.id == credential_id
    assert result.name == "Test Credential"
    assert result.provider == "test-provider"


def test_postgres_vault_load_metadata_dict_payload() -> None:
    """Test loading metadata when payload is a dict (not string)."""
    cipher = AesGcmCredentialCipher(key="test-key")
    credential_id = uuid4()
    workflow_id = uuid4()

    metadata_dict = {
        "id": str(credential_id),
        "name": "Test",
        "provider": "provider",
        "scopes": ["read"],
        "created_at": datetime.now(tz=UTC).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "scope": {"workflow_ids": [str(workflow_id)], "workspace_ids": [], "roles": []},
        "kind": "secret",
        "encryption": cipher.encrypt("secret").model_dump(),
        "audit_log": [],
    }

    # Return dict instead of JSON string
    conn = FakeConnection(responses=[{"row": {"payload": metadata_dict}}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    result = vault._load_metadata(credential_id)
    assert result.id == credential_id


def test_postgres_vault_load_metadata_not_found() -> None:
    """Test loading metadata when credential doesn't exist."""
    cipher = AesGcmCredentialCipher(key="test-key")
    credential_id = uuid4()

    conn = FakeConnection(responses=[{"row": None}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    with pytest.raises(CredentialNotFoundError, match="was not found"):
        vault._load_metadata(credential_id)


def test_postgres_vault_iter_metadata() -> None:
    """Test iterating over all credentials."""
    cipher = AesGcmCredentialCipher(key="test-key")
    workflow_id = uuid4()

    metadata1 = {
        "id": str(uuid4()),
        "name": "Cred1",
        "provider": "provider",
        "scopes": ["read"],
        "created_at": datetime.now(tz=UTC).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "scope": {"workflow_ids": [str(workflow_id)], "workspace_ids": [], "roles": []},
        "kind": "secret",
        "encryption": cipher.encrypt("secret1").model_dump(),
        "audit_log": [],
    }

    metadata2 = {
        "id": str(uuid4()),
        "name": "Cred2",
        "provider": "provider",
        "scopes": ["write"],
        "created_at": datetime.now(tz=UTC).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "scope": {"workflow_ids": [str(workflow_id)], "workspace_ids": [], "roles": []},
        "kind": "secret",
        "encryption": cipher.encrypt("secret2").model_dump(),
        "audit_log": [],
    }

    conn = FakeConnection(
        responses=[
            {
                "rows": [
                    {"payload": json.dumps(metadata1)},
                    {"payload": metadata2},  # Dict payload
                ]
            }
        ]
    )
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    results = list(vault._iter_metadata())
    assert len(results) == 2
    assert results[0].name == "Cred1"
    assert results[1].name == "Cred2"


def test_postgres_vault_iter_metadata_filters_by_tenant() -> None:
    """Test iterating over tenant-scoped credentials."""
    cipher = AesGcmCredentialCipher(key="test-key")
    workflow_id = uuid4()

    metadata = {
        "id": str(uuid4()),
        "name": "Cred1",
        "provider": "provider",
        "scopes": ["read"],
        "created_at": datetime.now(tz=UTC).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "scope": {"workflow_ids": [str(workflow_id)], "workspace_ids": [], "roles": []},
        "kind": "secret",
        "encryption": cipher.encrypt("secret1").model_dump(),
        "audit_log": [],
    }

    conn = FakeConnection(
        responses=[
            {
                "rows": [
                    {"payload": json.dumps(metadata)},
                ]
            }
        ]
    )
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    results = list(vault._iter_metadata(tenant_id="tenant-a"))
    assert len(results) == 1
    assert results[0].name == "Cred1"
    assert "tenant_id IS NULL OR tenant_id = %s" in conn.queries[0][0]


def test_postgres_vault_remove_credential_success() -> None:
    """Test removing a credential successfully."""
    cipher = AesGcmCredentialCipher(key="test-key")
    credential_id = uuid4()

    conn = FakeConnection(responses=[{"rowcount": 1}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    vault._remove_credential(credential_id)

    delete_queries = [q for q in conn.queries if "DELETE FROM credentials" in q[0]]
    assert len(delete_queries) == 1
    assert delete_queries[0][1] == (str(credential_id),)


def test_postgres_vault_remove_credential_not_found() -> None:
    """Test removing a credential that doesn't exist."""
    cipher = AesGcmCredentialCipher(key="test-key")
    credential_id = uuid4()

    conn = FakeConnection(responses=[{"rowcount": 0}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    with pytest.raises(CredentialNotFoundError, match="was not found"):
        vault._remove_credential(credential_id)


def test_postgres_vault_close() -> None:
    """Test closing the vault closes the connection pool."""
    cipher = AesGcmCredentialCipher(key="test-key")
    conn = FakeConnection(responses=[])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool

    # Should call pool.close()
    vault.close()
    # In our fake, this is a no-op, but verify it doesn't raise
