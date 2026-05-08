"""In-memory remediation candidate repository support."""

from __future__ import annotations
from datetime import datetime
from typing import Any
from uuid import UUID
from orcheo.models.workflow import (
    WorkflowRunRemediation,
    WorkflowRunRemediationClassification,
    WorkflowRunRemediationStatus,
)
from orcheo_backend.app.repository.errors import (
    WorkflowRunRemediationNotFoundError,
)
from orcheo_backend.app.repository.in_memory.state import InMemoryRepositoryState


class WorkflowRemediationMixin(InMemoryRepositoryState):
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
        async with self._lock:
            for candidate in self._remediations.values():
                if candidate.fingerprint == fingerprint and candidate.status.is_active:
                    return candidate.model_copy(deep=True)

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
            self._remediations[candidate.id] = candidate
            return candidate.model_copy(deep=True)

    async def claim_next_remediation_candidate(
        self,
        *,
        actor: str,
        now: datetime | None = None,
        max_attempts: int | None = None,
    ) -> WorkflowRunRemediation | None:
        """Claim the oldest pending candidate eligible for another attempt."""
        async with self._lock:
            candidates = sorted(
                self._remediations.values(),
                key=lambda item: item.created_at,
            )
            for candidate in candidates:
                if candidate.status is not WorkflowRunRemediationStatus.PENDING:
                    continue
                if max_attempts is not None and candidate.attempt_count >= max_attempts:
                    continue
                candidate.claim(actor=actor, claimed_at=now)
                return candidate.model_copy(deep=True)
            return None

    async def get_remediation_candidate(
        self,
        remediation_id: UUID,
    ) -> WorkflowRunRemediation:
        """Return a remediation candidate by id."""
        async with self._lock:
            candidate = self._remediations.get(remediation_id)
            if candidate is None:
                raise WorkflowRunRemediationNotFoundError(str(remediation_id))
            return candidate.model_copy(deep=True)

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
        status_value = (
            WorkflowRunRemediationStatus(status) if status is not None else None
        )
        async with self._lock:
            candidates = [
                candidate.model_copy(deep=True)
                for candidate in self._remediations.values()
                if workflow_id is None or candidate.workflow_id == workflow_id
                if workflow_version_id is None
                or candidate.workflow_version_id == workflow_version_id
                if run_id is None or candidate.run_id == run_id
                if status_value is None or candidate.status is status_value
            ]
            candidates.sort(key=lambda item: item.created_at, reverse=True)
            return candidates[:limit] if limit is not None else candidates

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

    async def _update_remediation(
        self,
        remediation_id: UUID,
        updater: Any,
    ) -> WorkflowRunRemediation:
        async with self._lock:
            candidate = self._remediations.get(remediation_id)
            if candidate is None:
                raise WorkflowRunRemediationNotFoundError(str(remediation_id))
            updater(candidate)
            return candidate.model_copy(deep=True)


__all__ = ["WorkflowRemediationMixin"]
