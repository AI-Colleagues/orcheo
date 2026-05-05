"""Trigger management for the in-memory repository."""

from __future__ import annotations
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from orcheo.models.workflow import WorkflowRun
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.manual import ManualDispatchRequest
from orcheo.triggers.webhook import WebhookRequest, WebhookTriggerConfig
from orcheo.vault.oauth import CredentialHealthError
from orcheo_backend.app.errors import TenantQuotaExceededError
from orcheo_backend.app.repository.errors import (
    CronTriggerNotFoundError,
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.repository.in_memory.state import InMemoryRepositoryState


logger = logging.getLogger(__name__)


class TriggerDispatchMixin(InMemoryRepositoryState):
    """Handles webhook, cron, and manual trigger flows."""

    async def configure_webhook_trigger(
        self, workflow_id: UUID, config: WebhookTriggerConfig
    ) -> WebhookTriggerConfig:
        """Persist webhook trigger configuration for a workflow."""
        async with self._lock:
            if workflow_id not in self._workflows:
                raise WorkflowNotFoundError(str(workflow_id))
            return self._trigger_layer.configure_webhook(workflow_id, config)

    async def get_webhook_trigger_config(
        self, workflow_id: UUID
    ) -> WebhookTriggerConfig:
        """Return the webhook configuration for the workflow."""
        async with self._lock:
            if workflow_id not in self._workflows:
                raise WorkflowNotFoundError(str(workflow_id))
            return self._trigger_layer.get_webhook_config(workflow_id)

    async def handle_webhook_trigger(
        self,
        workflow_id: UUID,
        *,
        method: str,
        headers: Mapping[str, str],
        query_params: Mapping[str, str],
        payload: Any,
        source_ip: str | None,
    ) -> WorkflowRun:
        """Validate webhook input and enqueue a workflow run."""
        async with self._lock:
            workflow = self._workflows.get(workflow_id)
            if workflow is None:
                raise WorkflowNotFoundError(str(workflow_id))
            tenant_id = self._workflow_tenants.get(workflow_id)

            version_ids = self._workflow_versions.get(workflow_id)
            if not version_ids:
                raise WorkflowVersionNotFoundError("latest")
            latest_version_id = version_ids[-1]
            version = self._versions.get(latest_version_id)
            if version is None:
                raise WorkflowVersionNotFoundError(str(latest_version_id))

            await self._ensure_workflow_health(
                workflow_id,
                actor="webhook",
                tenant_id=tenant_id,
            )

            request = WebhookRequest(
                method=method,
                headers=headers,
                query_params=query_params,
                payload=payload,
                source_ip=source_ip,
            )
            dispatch = self._trigger_layer.prepare_webhook_dispatch(
                workflow_id, request
            )
            run = self._create_run_locked(
                workflow_id=workflow_id,
                workflow_version_id=version.id,
                triggered_by=dispatch.triggered_by,
                input_payload=dispatch.input_payload,
                actor=dispatch.actor,
                tenant_id=tenant_id,
            )
            return run.model_copy(deep=True)

    async def configure_cron_trigger(
        self, workflow_id: UUID, config: CronTriggerConfig
    ) -> CronTriggerConfig:
        """Persist cron trigger configuration for a workflow."""
        async with self._lock:
            if workflow_id not in self._workflows:
                raise WorkflowNotFoundError(str(workflow_id))
            return self._trigger_layer.configure_cron(workflow_id, config)

    async def get_cron_trigger_config(self, workflow_id: UUID) -> CronTriggerConfig:
        """Return the configured cron trigger definition."""
        async with self._lock:
            if workflow_id not in self._workflows:
                raise WorkflowNotFoundError(str(workflow_id))
            config = self._trigger_layer.get_cron_config(workflow_id)
            if config is None:
                raise CronTriggerNotFoundError(
                    f"No cron trigger configured for workflow {workflow_id}"
                )
            return config

    async def delete_cron_trigger(self, workflow_id: UUID) -> None:
        """Remove cron trigger configuration for the workflow."""
        async with self._lock:
            if workflow_id not in self._workflows:
                raise WorkflowNotFoundError(str(workflow_id))
            self._trigger_layer.remove_cron_config(workflow_id)

    async def dispatch_due_cron_runs(
        self, *, now: datetime | None = None
    ) -> list[WorkflowRun]:
        """Evaluate cron schedules and enqueue runs that are due."""
        reference = now or datetime.now(tz=UTC)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)

        async with self._lock:
            runs: list[WorkflowRun] = []
            plans = self._trigger_layer.collect_due_cron_dispatches(now=reference)
            for plan in plans:
                workflow_id = plan.workflow_id
                if workflow_id not in self._workflows:
                    continue
                tenant_id = self._workflow_tenants.get(workflow_id)
                try:
                    await self._ensure_workflow_health(
                        workflow_id,
                        actor="cron",
                        tenant_id=tenant_id,
                    )
                except CredentialHealthError as exc:  # pragma: no cover - logging only
                    logger.warning(
                        "Skipping cron dispatch for workflow %s due to credential "
                        "health error: %s",
                        workflow_id,
                        exc,
                    )
                    continue
                version_ids = self._workflow_versions.get(workflow_id)
                if not version_ids:
                    continue
                latest_version_id = version_ids[-1]
                version = self._versions.get(latest_version_id)
                if version is None:
                    continue

                try:
                    run = self._create_run_locked(
                        workflow_id=workflow_id,
                        workflow_version_id=version.id,
                        triggered_by="cron",
                        input_payload={
                            "scheduled_for": plan.scheduled_for.isoformat(),
                            "timezone": plan.timezone,
                        },
                        actor="cron",
                        tenant_id=tenant_id,
                    )
                except TenantQuotaExceededError:
                    logger.warning(
                        "Skipping cron dispatch for workflow %s because tenant "
                        "quota was exceeded",
                        workflow_id,
                    )
                    continue
                self._trigger_layer.commit_cron_dispatch(workflow_id)
                runs.append(run.model_copy(deep=True))
            return runs

    async def dispatch_manual_runs(
        self, request: ManualDispatchRequest
    ) -> list[WorkflowRun]:
        """Dispatch one or more manual runs for a workflow."""
        async with self._lock:
            if request.workflow_id not in self._workflows:
                raise WorkflowNotFoundError(str(request.workflow_id))

            version_ids = self._workflow_versions.get(request.workflow_id)
            if not version_ids:
                raise WorkflowVersionNotFoundError(str(request.workflow_id))

            default_version_id = version_ids[-1]
            runs: list[WorkflowRun] = []
            plan = self._trigger_layer.prepare_manual_dispatch(
                request, default_workflow_version_id=default_version_id
            )
            triggered_by = plan.triggered_by
            resolved_runs = plan.runs

            tenant_id = self._workflow_tenants.get(request.workflow_id)
            await self._ensure_workflow_health(
                request.workflow_id,
                actor=plan.actor or triggered_by,
                tenant_id=tenant_id,
            )

            for resolved in resolved_runs:
                version = self._versions.get(resolved.workflow_version_id)
                if version is None or version.workflow_id != request.workflow_id:
                    raise WorkflowVersionNotFoundError(  # pragma: no cover, defensive
                        str(resolved.workflow_version_id)
                    )

            for resolved in resolved_runs:
                try:
                    run = self._create_run_locked(
                        workflow_id=request.workflow_id,
                        workflow_version_id=resolved.workflow_version_id,
                        triggered_by=triggered_by,
                        input_payload=resolved.input_payload,
                        actor=plan.actor,
                        tenant_id=tenant_id,
                    )
                except TenantQuotaExceededError:
                    logger.warning(
                        "Skipping manual dispatch for workflow %s because tenant "
                        "quota was exceeded",
                        request.workflow_id,
                    )
                    continue
                runs.append(run.model_copy(deep=True))
            return runs


__all__ = ["TriggerDispatchMixin"]
