"""SQLite persistence helpers for governance alerts."""

from __future__ import annotations
from collections.abc import Iterable
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Protocol
from uuid import UUID
from orcheo.models import SecretGovernanceAlert
from orcheo.vault.errors import GovernanceAlertNotFoundError


if TYPE_CHECKING:
    import sqlite3

    class _SQLiteConnectionSupport(Protocol):
        def _locked_connection(self) -> AbstractContextManager[sqlite3.Connection]: ...


class SQLiteAlertStoreMixin:
    """Mixin implementing governance alert persistence in SQLite."""

    def _persist_alert(
        self: _SQLiteConnectionSupport, alert: SecretGovernanceAlert
    ) -> None:
        payload = alert.model_dump_json()
        with self._locked_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO governance_alerts (
                    id,
                    scope_hint,
                    tenant_id,
                    acknowledged,
                    created_at,
                    updated_at,
                    payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(alert.id),
                    alert.scope.scope_hint(),
                    alert.tenant_id,
                    1 if alert.is_acknowledged else 0,
                    alert.created_at.isoformat(),
                    alert.updated_at.isoformat(),
                    payload,
                ),
            )
            conn.commit()

    def _load_alert(
        self: _SQLiteConnectionSupport, alert_id: UUID
    ) -> SecretGovernanceAlert:
        with self._locked_connection() as conn:
            cursor = conn.execute(
                "SELECT payload FROM governance_alerts WHERE id = ?",
                (str(alert_id),),
            )
            row = cursor.fetchone()
        if row is None:
            msg = "Governance alert was not found."
            raise GovernanceAlertNotFoundError(msg)
        return SecretGovernanceAlert.model_validate_json(row[0])

    def _iter_alerts(
        self: _SQLiteConnectionSupport,
        *,
        tenant_id: str | None = None,
    ) -> Iterable[SecretGovernanceAlert]:
        with self._locked_connection() as conn:
            if tenant_id is None:
                cursor = conn.execute(
                    """
                    SELECT payload
                      FROM governance_alerts
                  ORDER BY created_at ASC
                    """
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT payload
                      FROM governance_alerts
                     WHERE tenant_id IS NULL OR tenant_id = ?
                  ORDER BY created_at ASC
                    """,
                    (tenant_id,),
                )
            rows = cursor.fetchall()
        for row in rows:
            yield SecretGovernanceAlert.model_validate_json(row[0])

    def _remove_alert(self: _SQLiteConnectionSupport, alert_id: UUID) -> None:
        with self._locked_connection() as conn:
            conn.execute(
                "DELETE FROM governance_alerts WHERE id = ?",
                (str(alert_id),),
            )
            conn.commit()


__all__ = ["SQLiteAlertStoreMixin"]
