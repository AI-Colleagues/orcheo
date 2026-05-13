"""Tests for SQLite vault mixins and template operations."""

from __future__ import annotations
import sqlite3
from pathlib import Path
from uuid import UUID, uuid4
import pytest
from orcheo.models import (
    AesGcmCredentialCipher,
    CredentialAccessContext,
    CredentialMetadata,
    CredentialScope,
    CredentialTemplate,
    GovernanceAlertKind,
    SecretGovernanceAlert,
    SecretGovernanceAlertSeverity,
)
from orcheo.vault.errors import CredentialTemplateNotFoundError, WorkflowScopeError
from orcheo.vault.sqlite_alerts import SQLiteAlertStoreMixin
from orcheo.vault.sqlite_core import SQLiteConnectionMixin
from orcheo.vault.sqlite_credentials import SQLiteCredentialStoreMixin
from orcheo.vault.sqlite_templates import SQLiteTemplateStoreMixin
from orcheo.vault.templates import TemplateOperationsMixin


class SQLiteTestVault(
    SQLiteConnectionMixin,
    SQLiteCredentialStoreMixin,
    SQLiteTemplateStoreMixin,
    SQLiteAlertStoreMixin,
):
    """Small harness exposing the SQLite vault mixins."""

    def __init__(self, path: Path) -> None:
        SQLiteConnectionMixin.__init__(self, path)


class TemplateHarness(TemplateOperationsMixin):
    """In-memory harness for template operations."""

    def __init__(self) -> None:
        self._templates: dict[object, CredentialTemplate] = {}

    def _persist_template(self, template: CredentialTemplate) -> None:
        self._templates[template.id] = template.model_copy(deep=True)

    def _load_template(self, template_id: UUID) -> CredentialTemplate:
        return self._templates[template_id].model_copy(deep=True)

    def _iter_templates(
        self, *, workspace_id: str | None = None
    ) -> list[CredentialTemplate]:
        return list(self._templates.values())

    def _remove_template(self, template_id: UUID) -> None:
        if template_id not in self._templates:
            raise CredentialTemplateNotFoundError("Credential template was not found.")
        self._templates.pop(template_id)


def test_sqlite_connection_migrations_add_missing_workspace_columns(
    tmp_path: Path,
) -> None:
    db = tmp_path / "core.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE credentials (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                name TEXT NOT NULL,
                provider TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE credential_templates (
                id TEXT PRIMARY KEY,
                scope_hint TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE governance_alerts (
                id TEXT PRIMARY KEY,
                scope_hint TEXT NOT NULL
            )
            """
        )

        SQLiteConnectionMixin._migrate_credentials_workspace_id(conn)
        SQLiteConnectionMixin._migrate_workspace_column(conn, "credential_templates")
        SQLiteConnectionMixin._migrate_workspace_column(conn, "governance_alerts")

        credential_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(credentials)")
        }
        template_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(credential_templates)")
        }
        alert_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(governance_alerts)")
        }

    assert "workspace_id" in credential_columns
    assert "workspace_id" in template_columns
    assert "workspace_id" in alert_columns


def test_sqlite_connection_pool_acquire_and_release_branches(
    tmp_path: Path,
) -> None:
    vault = SQLiteTestVault(tmp_path / "pool.sqlite")

    # The initialization step leaves one reusable connection in the pool.
    for _ in range(4):
        vault._release_connection(vault._create_connection())

    overflow = vault._create_connection()
    overflow.execute("BEGIN")
    vault._release_connection(overflow)

    while not vault._connection_pool.empty():
        vault._connection_pool.get_nowait().close()

    with vault._acquire_connection() as conn:
        assert conn is not None


def test_sqlite_credential_store_backfills_workspace_id_and_filters(
    tmp_path: Path,
) -> None:
    cipher = AesGcmCredentialCipher(key="sqlite-credentials")
    vault = SQLiteTestVault(tmp_path / "credentials.sqlite")
    metadata = CredentialMetadata.create(
        name="Slack",
        provider="slack",
        scopes=["chat:write"],
        secret="secret",
        cipher=cipher,
        actor="alice",
    )

    vault._persist_metadata(metadata)
    with vault._locked_connection() as conn:
        conn.execute(
            "UPDATE credentials SET workspace_id = ? WHERE id = ?",
            ("workspace-a", str(metadata.id)),
        )
        conn.commit()

    loaded = vault._load_metadata(metadata.id)
    assert loaded.workspace_id == "workspace-a"

    filtered = list(vault._iter_metadata(workspace_id="workspace-a"))
    assert [item.id for item in filtered] == [metadata.id]
    assert filtered[0].workspace_id == "workspace-a"


def test_sqlite_template_store_filters_by_workspace(tmp_path: Path) -> None:
    vault = SQLiteTestVault(tmp_path / "templates.sqlite")
    template = CredentialTemplate.create(
        name="API",
        provider="api",
        scopes=["read"],
        actor="alice",
        workspace_id="workspace-a",
    )
    vault._persist_template(template)

    filtered = list(vault._iter_templates(workspace_id="workspace-a"))
    assert [item.id for item in filtered] == [template.id]


def test_sqlite_alert_store_filters_by_workspace(tmp_path: Path) -> None:
    vault = SQLiteTestVault(tmp_path / "alerts.sqlite")
    alert = SecretGovernanceAlert.create(
        scope=CredentialScope.unrestricted(),
        kind=GovernanceAlertKind.VALIDATION_FAILED,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="Rotate",
        actor="ops",
        workspace_id="workspace-a",
    )

    vault._persist_alert(alert)

    filtered = list(vault._iter_alerts(workspace_id="workspace-a"))
    assert [item.id for item in filtered] == [alert.id]


def test_template_operations_reject_workspace_mismatch() -> None:
    harness = TemplateHarness()
    template_workspace_id = str(uuid4())
    template = harness.create_template(
        name="Restricted",
        provider="service",
        scopes=["read"],
        actor="alice",
        workspace_id=template_workspace_id,
    )
    context = CredentialAccessContext(
        workflow_id=uuid4(),
        workspace_id=uuid4(),
    )

    with pytest.raises(WorkflowScopeError):
        harness.get_template(template_id=template.id, context=context)

    with pytest.raises(WorkflowScopeError):
        harness._get_template(template_id=template.id, context=context)
