"""SQLite persistence helpers for credential templates."""

from __future__ import annotations
from collections.abc import Iterable
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Protocol
from uuid import UUID
from orcheo.models import CredentialTemplate
from orcheo.vault.errors import CredentialTemplateNotFoundError


if TYPE_CHECKING:
    import sqlite3

    class _SQLiteConnectionSupport(Protocol):
        def _locked_connection(self) -> AbstractContextManager[sqlite3.Connection]: ...


class SQLiteTemplateStoreMixin:
    """Mixin implementing template persistence in SQLite."""

    def _persist_template(
        self: _SQLiteConnectionSupport, template: CredentialTemplate
    ) -> None:
        payload = template.model_dump_json()
        with self._locked_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO credential_templates (
                    id,
                    scope_hint,
                    workspace_id,
                    name,
                    provider,
                    created_at,
                    updated_at,
                    payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(template.id),
                    template.scope.scope_hint(),
                    template.workspace_id,
                    template.name,
                    template.provider,
                    template.created_at.isoformat(),
                    template.updated_at.isoformat(),
                    payload,
                ),
            )
            conn.commit()

    def _load_template(
        self: _SQLiteConnectionSupport, template_id: UUID
    ) -> CredentialTemplate:
        with self._locked_connection() as conn:
            cursor = conn.execute(
                "SELECT payload FROM credential_templates WHERE id = ?",
                (str(template_id),),
            )
            row = cursor.fetchone()
        if row is None:
            msg = "Credential template was not found."
            raise CredentialTemplateNotFoundError(msg)
        return CredentialTemplate.model_validate_json(row[0])

    def _iter_templates(
        self: _SQLiteConnectionSupport,
        *,
        workspace_id: str | None = None,
    ) -> Iterable[CredentialTemplate]:
        with self._locked_connection() as conn:
            if workspace_id is None:
                cursor = conn.execute(
                    """
                    SELECT payload
                      FROM credential_templates
                  ORDER BY created_at ASC
                    """
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT payload
                      FROM credential_templates
                     WHERE workspace_id = ?
                  ORDER BY created_at ASC
                    """,
                    (str(workspace_id),),
                )
            rows = cursor.fetchall()
        for row in rows:
            yield CredentialTemplate.model_validate_json(row[0])

    def _remove_template(self: _SQLiteConnectionSupport, template_id: UUID) -> None:
        with self._locked_connection() as conn:
            deleted = conn.execute(
                "DELETE FROM credential_templates WHERE id = ?",
                (str(template_id),),
            ).rowcount
            conn.commit()
        if deleted == 0:
            msg = "Credential template was not found."
            raise CredentialTemplateNotFoundError(msg)


__all__ = ["SQLiteTemplateStoreMixin"]
