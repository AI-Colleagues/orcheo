"""Tests for PostgreSQL-backed credential vault - template operations."""

from __future__ import annotations
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
import pytest
from orcheo.models import (
    AesGcmCredentialCipher,
)
from orcheo.vault.errors import CredentialTemplateNotFoundError
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


def test_postgres_vault_persist_template() -> None:
    """Test persisting template metadata."""
    cipher = AesGcmCredentialCipher(key="test-key")
    workspace_id = "workspace-a"

    conn = FakeConnection(responses=[{}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    template = vault.create_template(
        name="Test Template",
        provider="test-provider",
        scopes=["read", "write"],
        actor="test-actor",
        workspace_id=workspace_id,
    )

    # Verify INSERT was called
    insert_queries = [
        q for q in conn.queries if "INSERT INTO credential_templates" in q[0]
    ]
    assert len(insert_queries) == 1
    params = insert_queries[0][1]
    assert params[0] == str(template.id)
    assert params[1] == template.scope.scope_hint()
    assert params[2] == workspace_id
    assert params[3] == "Test Template"
    assert params[4] == "test-provider"


def test_postgres_vault_load_template_found() -> None:
    """Test loading template metadata when it exists."""
    cipher = AesGcmCredentialCipher(key="test-key")
    template_id = uuid4()

    template_dict = {
        "id": str(template_id),
        "name": "Test Template",
        "provider": "test-provider",
        "scopes": ["read"],
        "created_at": datetime.now(tz=UTC).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "scope": {"workflow_ids": [], "workspace_ids": [], "roles": []},
        "kind": "oauth",
        "audit_log": [],
    }

    conn = FakeConnection(responses=[{"row": {"payload": json.dumps(template_dict)}}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    result = vault._load_template(template_id)

    assert result.id == template_id
    assert result.name == "Test Template"
    assert result.provider == "test-provider"


def test_postgres_vault_load_template_dict_payload() -> None:
    """Test loading template when payload is a dict (not string)."""
    cipher = AesGcmCredentialCipher(key="test-key")
    template_id = uuid4()

    template_dict = {
        "id": str(template_id),
        "name": "Template",
        "provider": "provider",
        "scopes": ["read"],
        "created_at": datetime.now(tz=UTC).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "scope": {"workflow_ids": [], "workspace_ids": [], "roles": []},
        "kind": "secret",
        "audit_log": [],
    }

    # Return dict instead of JSON string
    conn = FakeConnection(responses=[{"row": {"payload": template_dict}}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    result = vault._load_template(template_id)
    assert result.id == template_id
    assert result.name == "Template"


def test_postgres_vault_load_template_not_found() -> None:
    """Test loading template when it doesn't exist."""
    cipher = AesGcmCredentialCipher(key="test-key")
    template_id = uuid4()

    conn = FakeConnection(responses=[{"row": None}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    with pytest.raises(CredentialTemplateNotFoundError, match="template was not found"):
        vault._load_template(template_id)


def test_postgres_vault_iter_templates() -> None:
    """Test iterating over all templates."""
    cipher = AesGcmCredentialCipher(key="test-key")

    template1 = {
        "id": str(uuid4()),
        "name": "Template1",
        "provider": "provider",
        "scopes": ["read"],
        "created_at": datetime.now(tz=UTC).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "scope": {"workflow_ids": [], "workspace_ids": [], "roles": []},
        "kind": "oauth",
        "audit_log": [],
    }

    template2 = {
        "id": str(uuid4()),
        "name": "Template2",
        "provider": "provider",
        "scopes": ["write"],
        "created_at": datetime.now(tz=UTC).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "scope": {"workflow_ids": [], "workspace_ids": [], "roles": []},
        "kind": "secret",
        "audit_log": [],
    }

    conn = FakeConnection(
        responses=[
            {
                "rows": [
                    {"payload": json.dumps(template1)},
                    {"payload": template2},  # Dict payload
                ]
            }
        ]
    )
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    results = list(vault._iter_templates())
    assert len(results) == 2
    assert results[0].name == "Template1"
    assert results[1].name == "Template2"


def test_postgres_vault_iter_templates_filters_by_workspace() -> None:
    """Test iterating over workspace-scoped templates."""
    cipher = AesGcmCredentialCipher(key="test-key")

    template = {
        "id": str(uuid4()),
        "name": "Template1",
        "provider": "provider",
        "scopes": ["read"],
        "created_at": datetime.now(tz=UTC).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "scope": {"workflow_ids": [], "workspace_ids": [], "roles": []},
        "kind": "oauth",
        "audit_log": [],
    }

    conn = FakeConnection(
        responses=[
            {
                "rows": [
                    {"payload": json.dumps(template)},
                ]
            }
        ]
    )
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    results = list(vault._iter_templates(workspace_id="workspace-a"))
    assert len(results) == 1
    assert results[0].name == "Template1"
    assert "WHERE workspace_id = %s" in conn.queries[0][0]


def test_postgres_vault_remove_template_success() -> None:
    """Test removing a template successfully."""
    cipher = AesGcmCredentialCipher(key="test-key")
    template_id = uuid4()

    conn = FakeConnection(responses=[{"rowcount": 1}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    vault._remove_template(template_id)

    delete_queries = [
        q for q in conn.queries if "DELETE FROM credential_templates" in q[0]
    ]
    assert len(delete_queries) == 1
    assert delete_queries[0][1] == (str(template_id),)


def test_postgres_vault_remove_template_not_found() -> None:
    """Test removing a template that doesn't exist."""
    cipher = AesGcmCredentialCipher(key="test-key")
    template_id = uuid4()

    conn = FakeConnection(responses=[{"rowcount": 0}])
    pool = FakePool(conn)

    vault = PostgresCredentialVault(dsn="postgresql://test", cipher=cipher)
    vault._pool = pool
    vault._initialized = True

    with pytest.raises(CredentialTemplateNotFoundError, match="template was not found"):
        vault._remove_template(template_id)
