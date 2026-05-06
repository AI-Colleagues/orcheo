"""SQLite remediation candidate repository support."""

from __future__ import annotations
from datetime import datetime
from typing import Any
from uuid import UUID
from orcheo.models.workflow import (
    WorkflowRunRemediation,
    WorkflowRunRemediationClassification,
    WorkflowRunRemediationStatus,
)
from orcheo_backend.app.repository.errors import WorkflowRunRemediationNotFoundError
from orcheo_backend.app.repository_sqlite._persistence import SqlitePersistenceMixin


class WorkflowRemediationMixin(SqlitePersistenceMixin):
    """Persist and transition workflow remediation candidates."""

    async def create_remediation_candidate(
        self,
        *,
        workflow_id: UUID,
        workflow_version_id: UUID,
        run_id: UUID,
        fingerprint: str,
        version_checksum: str,
        graph_format: str | None,
        context: dict[str, Any],
    ) -> WorkflowRunRemediation:
        """Persist a pending candidate unless an active duplicate exists."""
        await self._ensure_initialized()
        async with self._lock:
            async with self._connection() as conn:
                cursor = await conn.execute(
                    """
                    SELECT payload
                      FROM workflow_run_remediations
                     WHERE fingerprint = ?
                       AND status IN (?, ?)
                  ORDER BY created_at ASC
                     LIMIT 1
                    """,
                    (
                        fingerprint,
                        WorkflowRunRemediationStatus.PENDING.value,
                        WorkflowRunRemediationStatus.CLAIMED.value,
                    ),
                )
                row = await cursor.fetchone()
                if row is not None:
                    return WorkflowRunRemediation.model_validate_json(
                        row["payload"]
                    ).model_copy(deep=True)

                candidate = WorkflowRunRemediation(
                    workflow_id=workflow_id,
                    workflow_version_id=workflow_version_id,
                    run_id=run_id,
                    fingerprint=fingerprint,
                    version_checksum=version_checksum,
                    graph_format=graph_format,
                    context=dict(context),
                )
                candidate.record_event(actor="worker", action="remediation_created")
                await conn.execute(
                    """
                    INSERT INTO workflow_run_remediations (
                        id,
                        workflow_id,
                        workflow_version_id,
                        run_id,
                        status,
                        fingerprint,
                        version_checksum,
                        payload,
                        attempt_count,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._remediation_row_values(candidate),
                )
                return candidate.model_copy(deep=True)

    async def claim_next_remediation_candidate(
        self,
        *,
        actor: str,
        now: datetime | None = None,
        max_attempts: int | None = None,
    ) -> WorkflowRunRemediation | None:
        """Claim the oldest pending candidate eligible for another attempt."""
        await self._ensure_initialized()
        async with self._lock:
            query = """
                SELECT payload
                  FROM workflow_run_remediations
                 WHERE status = ?
            """
            params: list[Any] = [WorkflowRunRemediationStatus.PENDING.value]
            if max_attempts is not None:
                query += " AND attempt_count < ?"
                params.append(max_attempts)
            query += " ORDER BY created_at ASC LIMIT 1"
            async with self._connection() as conn:
                cursor = await conn.execute(query, tuple(params))
                row = await cursor.fetchone()
                if row is None:
                    return None
                candidate = WorkflowRunRemediation.model_validate_json(row["payload"])
                candidate.claim(actor=actor, claimed_at=now)
                cursor = await conn.execute(
                    """
                    UPDATE workflow_run_remediations
                       SET status = ?,
                           payload = ?,
                           attempt_count = ?,
                           updated_at = ?
                     WHERE id = ?
                       AND status = ?
                    """,
                    (
                        candidate.status.value,
                        self._dump_model(candidate),
                        candidate.attempt_count,
                        candidate.updated_at.isoformat(),
                        str(candidate.id),
                        WorkflowRunRemediationStatus.PENDING.value,
                    ),
                )
                if cursor.rowcount == 0:
                    return None
                return candidate.model_copy(deep=True)

    async def get_remediation_candidate(
        self,
        remediation_id: UUID,
    ) -> WorkflowRunRemediation:
        """Return a remediation candidate by id."""
        await self._ensure_initialized()
        async with self._lock:
            return await self._get_remediation_locked(remediation_id)

    async def list_remediation_candidates(
        self,
        *,
        workflow_id: UUID | None = None,
        workflow_version_id: UUID | None = None,
        run_id: UUID | None = None,
        status: WorkflowRunRemediationStatus | str | None = None,
        limit: int | None = None,
    ) -> list[WorkflowRunRemediation]:
        """List remediation candidates using optional filters."""
        await self._ensure_initialized()
        query = "SELECT payload FROM workflow_run_remediations"
        clauses: list[str] = []
        params: list[Any] = []
        if workflow_id is not None:
            clauses.append("workflow_id = ?")
            params.append(str(workflow_id))
        if workflow_version_id is not None:
            clauses.append("workflow_version_id = ?")
            params.append(str(workflow_version_id))
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(str(run_id))
        if status is not None:
            clauses.append("status = ?")
            params.append(WorkflowRunRemediationStatus(status).value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        async with self._lock:
            async with self._connection() as conn:
                cursor = await conn.execute(query, tuple(params))
                rows = await cursor.fetchall()
            return [
                WorkflowRunRemediation.model_validate_json(row["payload"]).model_copy(
                    deep=True
                )
                for row in rows
            ]

    async def mark_remediation_fixed(
        self,
        remediation_id: UUID,
        *,
        created_version_id: UUID,
        classification: WorkflowRunRemediationClassification | str,
        developer_note: str,
        artifacts: dict[str, Any],
        validation_result: dict[str, Any],
    ) -> WorkflowRunRemediation:
        """Mark a remediation as fixed."""
        return await self._update_remediation(
            remediation_id,
            lambda candidate: candidate.mark_fixed(
                actor="worker",
                created_version_id=created_version_id,
                classification=WorkflowRunRemediationClassification(classification),
                developer_note=developer_note,
                artifacts=artifacts,
                validation_result=validation_result,
            ),
        )

    async def mark_remediation_note_only(
        self,
        remediation_id: UUID,
        *,
        classification: WorkflowRunRemediationClassification | str,
        developer_note: str,
        artifacts: dict[str, Any],
    ) -> WorkflowRunRemediation:
        """Mark a remediation as note-only."""
        return await self._update_remediation(
            remediation_id,
            lambda candidate: candidate.mark_note_only(
                actor="worker",
                classification=WorkflowRunRemediationClassification(classification),
                developer_note=developer_note,
                artifacts=artifacts,
            ),
        )

    async def dismiss_remediation_candidate(
        self,
        remediation_id: UUID,
        *,
        actor: str,
        reason: str | None = None,
    ) -> WorkflowRunRemediation:
        """Dismiss a remediation candidate."""
        return await self._update_remediation(
            remediation_id,
            lambda candidate: candidate.dismiss(actor=actor, reason=reason),
        )

    async def mark_remediation_failed(
        self,
        remediation_id: UUID,
        *,
        error: str,
        artifacts: dict[str, Any] | None = None,
        validation_result: dict[str, Any] | None = None,
    ) -> WorkflowRunRemediation:
        """Mark a remediation as failed."""
        return await self._update_remediation(
            remediation_id,
            lambda candidate: candidate.mark_failed(
                actor="worker",
                error=error,
                artifacts=artifacts,
                validation_result=validation_result,
            ),
        )

    async def _get_remediation_locked(
        self,
        remediation_id: UUID,
    ) -> WorkflowRunRemediation:
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT payload FROM workflow_run_remediations WHERE id = ?",
                (str(remediation_id),),
            )
            row = await cursor.fetchone()
        if row is None:
            raise WorkflowRunRemediationNotFoundError(str(remediation_id))
        return WorkflowRunRemediation.model_validate_json(row["payload"]).model_copy(
            deep=True
        )

    async def _update_remediation(
        self,
        remediation_id: UUID,
        updater: Any,
    ) -> WorkflowRunRemediation:
        await self._ensure_initialized()
        async with self._lock:
            candidate = await self._get_remediation_locked(remediation_id)
            updater(candidate)
            async with self._connection() as conn:
                await conn.execute(
                    """
                    UPDATE workflow_run_remediations
                       SET status = ?,
                           payload = ?,
                           attempt_count = ?,
                           updated_at = ?
                     WHERE id = ?
                    """,
                    (
                        candidate.status.value,
                        self._dump_model(candidate),
                        candidate.attempt_count,
                        candidate.updated_at.isoformat(),
                        str(candidate.id),
                    ),
                )
            return candidate.model_copy(deep=True)

    def _remediation_row_values(
        self,
        candidate: WorkflowRunRemediation,
    ) -> tuple[Any, ...]:
        return (
            str(candidate.id),
            str(candidate.workflow_id),
            str(candidate.workflow_version_id),
            str(candidate.run_id),
            candidate.status.value,
            candidate.fingerprint,
            candidate.version_checksum,
            self._dump_model(candidate),
            candidate.attempt_count,
            candidate.created_at.isoformat(),
            candidate.updated_at.isoformat(),
        )


__all__ = ["WorkflowRemediationMixin"]
