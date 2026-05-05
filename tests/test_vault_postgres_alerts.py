"""Tests for PostgreSQL-backed credential vault - alert operations."""

from __future__ import annotations
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
import pytest
from orcheo.models import (
    AesGcmCredentialCipher,
    GovernanceAlertKind,
    SecretGovernanceAlertSeverity,
)
from orcheo.vault.errors import GovernanceAlertNotFoundError
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

    def execute(self, query: str, params: Any | None = None) -> FakeCursor:
        self.queries.append((query.strip(), params))
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


def test_postgres_vault_persist_alert() -> None:
    """Test persisting alert metadata."""
    cipher = AesGcmCredentialCipher(key="test-key")
    workspace_id = "workspace-a"

    conn = FakeConnection(responses=[{}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    alert = vault.record_alert(
        kind=GovernanceAlertKind.TOKEN_EXPIRING,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="Token expiring soon",
        actor="test-actor",
        workspace_id=workspace_id,
    )

    # Verify INSERT was called
    insert_queries = [
        q for q in conn.queries if "INSERT INTO governance_alerts" in q[0]
    ]
    assert len(insert_queries) == 1
    params = insert_queries[0][1]
    assert params[0] == str(alert.id)
    assert params[1] == alert.scope.scope_hint()
    assert params[2] == workspace_id
    assert params[3] is False  # acknowledged
    assert params[6] is not None  # payload


def test_postgres_vault_load_alert_found() -> None:
    """Test loading alert metadata when it exists."""
    cipher = AesGcmCredentialCipher(key="test-key")
    alert_id = uuid4()

    alert_dict = {
        "id": str(alert_id),
        "kind": "token_expiring",
        "severity": "warning",
        "message": "Alert message",
        "created_at": datetime.now(tz=UTC).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "scope": {"workflow_ids": [], "workspace_ids": [], "roles": []},
        "is_acknowledged": False,
        "audit_log": [],
    }

    conn = FakeConnection(responses=[{"row": {"payload": json.dumps(alert_dict)}}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    result = vault._load_alert(alert_id)

    assert result.id == alert_id
    assert result.kind == GovernanceAlertKind.TOKEN_EXPIRING
    assert result.severity == SecretGovernanceAlertSeverity.WARNING


def test_postgres_vault_load_alert_dict_payload() -> None:
    """Test loading alert when payload is a dict (not string)."""
    cipher = AesGcmCredentialCipher(key="test-key")
    alert_id = uuid4()

    alert_dict = {
        "id": str(alert_id),
        "kind": "validation_failed",
        "severity": "critical",
        "message": "Validation failed",
        "created_at": datetime.now(tz=UTC).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "scope": {"workflow_ids": [], "workspace_ids": [], "roles": []},
        "is_acknowledged": True,
        "audit_log": [],
    }

    # Return dict instead of JSON string
    conn = FakeConnection(responses=[{"row": {"payload": alert_dict}}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    result = vault._load_alert(alert_id)
    assert result.id == alert_id
    assert result.is_acknowledged is True


def test_postgres_vault_load_alert_not_found() -> None:
    """Test loading alert when it doesn't exist."""
    cipher = AesGcmCredentialCipher(key="test-key")
    alert_id = uuid4()

    conn = FakeConnection(responses=[{"row": None}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    with pytest.raises(GovernanceAlertNotFoundError, match="alert was not found"):
        vault._load_alert(alert_id)


def test_postgres_vault_iter_alerts() -> None:
    """Test iterating over all alerts."""
    cipher = AesGcmCredentialCipher(key="test-key")

    alert1 = {
        "id": str(uuid4()),
        "kind": "token_expiring",
        "severity": "warning",
        "message": "Alert 1",
        "created_at": datetime.now(tz=UTC).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "scope": {"workflow_ids": [], "workspace_ids": [], "roles": []},
        "is_acknowledged": False,
        "audit_log": [],
    }

    alert2 = {
        "id": str(uuid4()),
        "kind": "rotation_overdue",
        "severity": "critical",
        "message": "Alert 2",
        "created_at": datetime.now(tz=UTC).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "scope": {"workflow_ids": [], "workspace_ids": [], "roles": []},
        "is_acknowledged": False,
        "audit_log": [],
    }

    conn = FakeConnection(
        responses=[
            {
                "rows": [
                    {"payload": json.dumps(alert1)},
                    {"payload": alert2},  # Dict payload
                ]
            }
        ]
    )
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    results = list(vault._iter_alerts())
    assert len(results) == 2
    assert results[0].message == "Alert 1"
    assert results[1].message == "Alert 2"


def test_postgres_vault_iter_alerts_filters_by_workspace() -> None:
    """Test iterating over workspace-scoped alerts."""
    cipher = AesGcmCredentialCipher(key="test-key")

    alert = {
        "id": str(uuid4()),
        "kind": "token_expiring",
        "severity": "warning",
        "message": "Alert 1",
        "created_at": datetime.now(tz=UTC).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "scope": {"workflow_ids": [], "workspace_ids": [], "roles": []},
        "is_acknowledged": False,
        "audit_log": [],
    }

    conn = FakeConnection(
        responses=[
            {
                "rows": [
                    {"payload": json.dumps(alert)},
                ]
            }
        ]
    )
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    results = list(vault._iter_alerts(workspace_id="workspace-a"))
    assert len(results) == 1
    assert results[0].message == "Alert 1"
    assert "workspace_id IS NULL OR workspace_id = %s" in conn.queries[0][0]


def test_postgres_vault_remove_alert() -> None:
    """Test removing an alert (no error check, always succeeds)."""
    cipher = AesGcmCredentialCipher(key="test-key")
    alert_id = uuid4()

    conn = FakeConnection(responses=[{"rowcount": 1}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    vault._remove_alert(alert_id)

    delete_queries = [
        q for q in conn.queries if "DELETE FROM governance_alerts" in q[0]
    ]
    assert len(delete_queries) == 1
    assert delete_queries[0][1] == (str(alert_id),)


def test_postgres_vault_remove_alert_not_found_no_error() -> None:
    """Test that removing non-existent alert doesn't raise error."""
    cipher = AesGcmCredentialCipher(key="test-key")
    alert_id = uuid4()

    conn = FakeConnection(responses=[{"rowcount": 0}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    # Should not raise error even if rowcount is 0
    vault._remove_alert(alert_id)

    delete_queries = [
        q for q in conn.queries if "DELETE FROM governance_alerts" in q[0]
    ]
    assert len(delete_queries) == 1
