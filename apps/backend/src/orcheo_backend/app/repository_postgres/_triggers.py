"""Trigger configuration and dispatch helpers."""

from __future__ import annotations
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID
from orcheo.models.workflow import WorkflowRun
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.manual import ManualDispatchRequest
from orcheo.triggers.webhook import WebhookRequest, WebhookTriggerConfig
from orcheo.vault.oauth import CredentialHealthError
from orcheo_backend.app.errors import TenantQuotaExceededError
from orcheo_backend.app.repository import (
    CronTriggerNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.repository_postgres._base import logger
from orcheo_backend.app.repository_postgres._persistence import PostgresPersistenceMixin


class _WorkflowTenantLookup(Protocol):
    async def _get_workflow_tenant_id_locked(self, workflow_id: UUID) -> str | None: ...


def _enqueue_run_for_execution(run: WorkflowRun) -> None:
    """Enqueue a Celery task to execute the workflow run.

    This function is best-effort: if Celery/Redis is unavailable,
    the run remains pending and can be retried manually.

    NOTE: This must only be called AFTER the run has been committed to the database.
    """
    try:
        from orcheo_backend.worker.tasks import execute_run

        headers = {"tenant_id": run.tenant_id} if run.tenant_id is not None else {}
        enqueue = getattr(execute_run, "apply_async", None)
        if enqueue is None:
            enqueue = execute_run.delay
            enqueue(str(run.id))
        else:
            enqueue(args=(str(run.id),), headers=headers or None)
        logger.info("Enqueued run %s for execution", run.id)
    except Exception as exc:
        logger.warning(
            "Failed to enqueue run %s for execution: %s. "
            "Run will remain pending until manually retried.",
            run.id,
            exc,
        )


class TriggerRepositoryMixin(PostgresPersistenceMixin):
    """Coordinate trigger configuration and dispatch flows."""

    async def configure_webhook_trigger(
        self,
        workflow_id: UUID,
        config: WebhookTriggerConfig,
    ) -> WebhookTriggerConfig:
        await self._ensure_initialized()
        async with self._lock:
            await self._get_workflow_locked(workflow_id)
            normalized = self._trigger_layer.configure_webhook(workflow_id, config)
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO webhook_triggers (workflow_id, config)
                    VALUES (%s, %s)
                    ON CONFLICT(workflow_id) DO UPDATE SET config=EXCLUDED.config
                    """,
                    (str(workflow_id), self._dump_config(normalized)),
                )
            return normalized.model_copy(deep=True)

    async def get_webhook_trigger_config(
        self, workflow_id: UUID
    ) -> WebhookTriggerConfig:
        await self._ensure_initialized()
        async with self._lock:
            await self._get_workflow_locked(workflow_id)
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
        await self._ensure_initialized()
        async with self._lock:
            await self._get_workflow_locked(workflow_id)
            tenant_repo = cast(_WorkflowTenantLookup, self)
            tenant_id = await tenant_repo._get_workflow_tenant_id_locked(workflow_id)
            version = await self._get_latest_version_locked(workflow_id)
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
            run = await self._create_run_locked(
                workflow_id=workflow_id,
                workflow_version_id=version.id,
                triggered_by=dispatch.triggered_by,
                input_payload=dispatch.input_payload,
                actor=dispatch.actor,
                tenant_id=tenant_id,
            )
            run_copy = run.model_copy(deep=True)
        # Enqueue AFTER lock is released to ensure commit is fully visible
        _enqueue_run_for_execution(run_copy)
        return run_copy

    async def configure_cron_trigger(
        self,
        workflow_id: UUID,
        config: CronTriggerConfig,
    ) -> CronTriggerConfig:
        await self._ensure_initialized()
        async with self._lock:
            await self._get_workflow_locked(workflow_id)
            normalized = self._trigger_layer.configure_cron(workflow_id, config)
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO cron_triggers (workflow_id, config)
                    VALUES (%s, %s)
                    ON CONFLICT(workflow_id) DO UPDATE SET config=EXCLUDED.config
                    """,
                    (str(workflow_id), self._dump_config(normalized)),
                )
            return normalized.model_copy(deep=True)

    async def get_cron_trigger_config(self, workflow_id: UUID) -> CronTriggerConfig:
        await self._ensure_initialized()
        async with self._lock:
            await self._get_workflow_locked(workflow_id)
            config = self._trigger_layer.get_cron_config(workflow_id)
            if config is None:
                raise CronTriggerNotFoundError(  # pragma: no cover - defensive
                    f"No cron trigger configured for workflow {workflow_id}"
                )
            return config

    async def delete_cron_trigger(self, workflow_id: UUID) -> None:
        await self._ensure_initialized()
        async with self._lock:
            await self._get_workflow_locked(workflow_id)
            async with self._connection() as conn:
                await conn.execute(
                    """
                    DELETE FROM cron_triggers
                     WHERE workflow_id = %s
                    """,
                    (str(workflow_id),),
                )
            self._trigger_layer.remove_cron_config(workflow_id)

    async def dispatch_due_cron_runs(
        self, *, now: datetime | None = None
    ) -> list[WorkflowRun]:
        await self._ensure_initialized()
        reference = now or datetime.now(tz=UTC)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)

        runs: list[WorkflowRun] = []

        async with self._lock:
            # Sync cron triggers each dispatch to reflect updates from other processes.
            await self._refresh_cron_triggers()
            plans = self._trigger_layer.collect_due_cron_dispatches(now=reference)
            for plan in plans:
                try:
                    version = await self._get_latest_version_locked(plan.workflow_id)
                except WorkflowVersionNotFoundError:
                    continue
                tenant_repo = cast(_WorkflowTenantLookup, self)
                tenant_id = await tenant_repo._get_workflow_tenant_id_locked(
                    plan.workflow_id
                )

                try:
                    await self._ensure_workflow_health(
                        plan.workflow_id,
                        actor="cron",
                        tenant_id=tenant_id,
                    )
                except CredentialHealthError as exc:
                    logger.warning(
                        "Skipping cron dispatch for workflow %s due to credential "
                        "health error: %s",
                        plan.workflow_id,
                        exc,
                    )
                    continue

                try:
                    run = await self._create_run_locked(
                        workflow_id=plan.workflow_id,
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
                        plan.workflow_id,
                    )
                    continue
                self._trigger_layer.commit_cron_dispatch(plan.workflow_id)
                # Persist the last_dispatched_at to survive worker restarts
                last_dispatched = self._trigger_layer.get_cron_last_dispatched_at(
                    plan.workflow_id
                )
                if last_dispatched is not None:  # pragma: no branch
                    async with self._connection() as conn:
                        await conn.execute(
                            """
                            UPDATE cron_triggers
                               SET last_dispatched_at = %s
                             WHERE workflow_id = %s
                            """,
                            (last_dispatched, str(plan.workflow_id)),
                        )
                runs.append(run.model_copy(deep=True))
        # Enqueue AFTER lock is released to ensure commits are fully visible
        for run in runs:
            _enqueue_run_for_execution(run)
        return runs

    async def dispatch_manual_runs(
        self, request: ManualDispatchRequest
    ) -> list[WorkflowRun]:
        await self._ensure_initialized()
        async with self._lock:
            await self._get_workflow_locked(request.workflow_id)
            try:
                latest_version = await self._get_latest_version_locked(
                    request.workflow_id
                )
            except WorkflowVersionNotFoundError as exc:
                raise WorkflowVersionNotFoundError(str(request.workflow_id)) from exc
            default_version_id = latest_version.id
            plan = self._trigger_layer.prepare_manual_dispatch(
                request, default_workflow_version_id=default_version_id
            )
            tenant_repo = cast(_WorkflowTenantLookup, self)
            tenant_id = await tenant_repo._get_workflow_tenant_id_locked(
                request.workflow_id
            )

            await self._ensure_workflow_health(
                request.workflow_id,
                actor=plan.actor or plan.triggered_by,
                tenant_id=tenant_id,
            )

            runs: list[WorkflowRun] = []
            for resolved in plan.runs:
                version = await self._get_version_locked(resolved.workflow_version_id)
                if version.workflow_id != request.workflow_id:
                    raise WorkflowVersionNotFoundError(
                        str(resolved.workflow_version_id)
                    )

            for resolved in plan.runs:
                try:
                    run = await self._create_run_locked(
                        workflow_id=request.workflow_id,
                        workflow_version_id=resolved.workflow_version_id,
                        triggered_by=plan.triggered_by,
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
        # Enqueue AFTER lock is released to ensure commits are fully visible
        for run in runs:
            _enqueue_run_for_execution(run)
        return runs


__all__ = ["TriggerRepositoryMixin"]
