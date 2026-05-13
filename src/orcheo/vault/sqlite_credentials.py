"""SQLite persistence helpers for credential metadata."""

from __future__ import annotations
from collections.abc import Iterable
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Protocol
from uuid import UUID
from orcheo.models import CredentialMetadata
from orcheo.vault.errors import CredentialNotFoundError, DuplicateCredentialNameError


if TYPE_CHECKING:
    import sqlite3

    class _SQLiteConnectionSupport(Protocol):
        def _locked_connection(self) -> AbstractContextManager[sqlite3.Connection]: ...


class SQLiteCredentialStoreMixin:
    """Mixin implementing credential persistence in SQLite."""

    def _persist_metadata(
        self: _SQLiteConnectionSupport, metadata: CredentialMetadata
    ) -> None:
        payload = metadata.model_dump_json()
        with self._locked_connection() as conn:
            # Name uniqueness is scoped per workspace (NULL workspace = global scope).
            cursor = conn.execute(
                """
                SELECT id
                  FROM credentials
                 WHERE lower(name) = lower(?)
                   AND workspace_id = ?
                """,
                (metadata.name, metadata.workspace_id),
            )
            rows = [row[0] for row in cursor.fetchall()]
            duplicates = [row_id for row_id in rows if row_id != str(metadata.id)]
            if duplicates:
                msg = f"Credential name '{metadata.name}' is already in use."
                raise DuplicateCredentialNameError(msg)
            conn.execute(
                """
                INSERT OR REPLACE INTO credentials (
                    id,
                    workflow_id,
                    workspace_id,
                    name,
                    provider,
                    created_at,
                    updated_at,
                    payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(metadata.id),
                    metadata.scope.scope_hint(),
                    metadata.workspace_id,
                    metadata.name,
                    metadata.provider,
                    metadata.created_at.isoformat(),
                    metadata.updated_at.isoformat(),
                    payload,
                ),
            )
            conn.commit()

    def _load_metadata(
        self: _SQLiteConnectionSupport, credential_id: UUID
    ) -> CredentialMetadata:
        with self._locked_connection() as conn:
            cursor = conn.execute(
                "SELECT payload, workspace_id FROM credentials WHERE id = ?",
                (str(credential_id),),
            )
            row = cursor.fetchone()
        if row is None:
            msg = "Credential was not found."
            raise CredentialNotFoundError(msg)
        metadata = CredentialMetadata.model_validate_json(row[0])
        if metadata.workspace_id is None and row[1] is not None:
            metadata.workspace_id = row[1]
        return metadata

    def _iter_metadata(
        self: _SQLiteConnectionSupport,
        *,
        workspace_id: str | None = None,
    ) -> Iterable[CredentialMetadata]:
        with self._locked_connection() as conn:
            if workspace_id is None:
                cursor = conn.execute(
                    """
                    SELECT payload, workspace_id
                      FROM credentials
                  ORDER BY created_at ASC
                    """
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT payload, workspace_id
                      FROM credentials
                     WHERE workspace_id = ?
                  ORDER BY created_at ASC
                    """,
                    (str(workspace_id),),
                )
            rows = cursor.fetchall()
        for row in rows:
            metadata = CredentialMetadata.model_validate_json(row[0])
            if metadata.workspace_id is None and row[1] is not None:
                metadata.workspace_id = row[1]
            yield metadata

    def _remove_credential(self: _SQLiteConnectionSupport, credential_id: UUID) -> None:
        with self._locked_connection() as conn:
            deleted = conn.execute(
                "DELETE FROM credentials WHERE id = ?",
                (str(credential_id),),
            ).rowcount
            conn.commit()
        if deleted == 0:
            msg = "Credential was not found."
            raise CredentialNotFoundError(msg)


__all__ = ["SQLiteCredentialStoreMixin"]
