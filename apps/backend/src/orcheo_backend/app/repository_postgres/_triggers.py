"""Trigger configuration and dispatch helpers."""

from __future__ import annotations
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID
from orcheo.models import WorkflowRun
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.layer.models import CronDispatchPlan
from orcheo.triggers.manual import ManualDispatchRequest
from orcheo.triggers.webhook import WebhookRequest, WebhookTriggerConfig
from orcheo.vault.oauth import CredentialHealthError
from orcheo_backend.app.errors import WorkspaceQuotaExceededError
from orcheo_backend.app.repository import (
    CronTriggerNotFoundError,
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.repository_postgres._base import logger
from orcheo_backend.app.repository_postgres._persistence import PostgresPersistenceMixin


class _WorkflowWorkspaceLookup(Protocol):
    async def _get_workflow_workspace_id_locked(
        self, workflow_id: UUID
    ) -> str | None: ...  # pragma: no cover


def _enqueue_run_for_execution(run: WorkflowRun) -> bool:
    """Enqueue the workflow run for execution.

    Single-process deployments (the desktop app) have no Celery broker, so with
    ``ORCHEO_INPROCESS_EXECUTION`` enabled the run is executed on the backend
    event loop instead. Otherwise the run is published to Celery.

    This function is best-effort: if Celery/Redis is unavailable, flagged
    trigger runs remain pending for the background reconciler to republish.

    NOTE: This must only be called AFTER the run has been committed to the database.
    """
    from orcheo_backend.app.local_execution import schedule_run_inprocess

    if schedule_run_inprocess(run):
        return True

    try:
        from orcheo_backend.worker.tasks import execute_run

        headers = (
            {"workspace_id": run.workspace_id} if run.workspace_id is not None else {}
        )
        enqueue = getattr(execute_run, "apply_async", None)
        if enqueue is None:
            execute_run.delay(str(run.id), headers=headers or None)
        else:
            enqueue(args=(str(run.id),), headers=headers or None)
        logger.info("Enqueued run %s for execution", run.id)
        return True
    except Exception as exc:
        logger.warning(
            "Failed to enqueue run %s for execution: %s. "
            "Run remains pending for reconciliation.",
            run.id,
            exc,
        )
        return False


async def _enqueue_run_and_confirm(
    repository: PostgresPersistenceMixin, run: WorkflowRun
) -> None:
    """Publish a committed run and record acceptance by the broker."""
    if not _enqueue_run_for_execution(run):
        return
    try:
        await repository.mark_run_enqueued(run.id)
    except Exception:
        logger.exception("Could not confirm enqueue for run %s", run.id)


class TriggerRepositoryMixin(PostgresPersistenceMixin):
    """Coordinate trigger configuration and dispatch flows."""

    async def configure_webhook_trigger(
        self,
        workflow_id: UUID,
        config: WebhookTriggerConfig,
    ) -> WebhookTriggerConfig:
        await self._ensure_initialized()
        async with self._lock:
            workflow = await self._get_workflow_locked(workflow_id)
            if workflow.is_archived:
                raise WorkflowNotFoundError(str(workflow_id))
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
            workflow = await self._get_workflow_locked(workflow_id)
            if workflow.is_archived:
                raise WorkflowNotFoundError(str(workflow_id))
            workspace_repo = cast(_WorkflowWorkspaceLookup, self)
            workspace_id = await workspace_repo._get_workflow_workspace_id_locked(
                workflow_id
            )
            version = await self._get_latest_version_locked(workflow_id)
            await self._ensure_workflow_health(
                workflow_id,
                actor="webhook",
                workspace_id=workspace_id,
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
                workspace_id=workspace_id,
                dispatch_requested=True,
            )
            run_copy = run.model_copy(deep=True)
        # Enqueue AFTER lock is released to ensure commit is fully visible
        await _enqueue_run_and_confirm(self, run_copy)
        return run_copy

    async def configure_cron_trigger(
        self,
        workflow_id: UUID,
        config: CronTriggerConfig,
    ) -> CronTriggerConfig:
        await self._ensure_initialized()
        async with self._lock:
            workflow = await self._get_workflow_locked(workflow_id)
            if workflow.is_archived:
                raise WorkflowNotFoundError(str(workflow_id))
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

    async def _plans_in_workspace_locked(
        self, plans: list[CronDispatchPlan], workspace_id: str | None
    ) -> list[CronDispatchPlan]:
        """Keep only ``workspace_id``'s plans; all of them when it is None."""
        if workspace_id is None:
            return plans
        lookup = cast(_WorkflowWorkspaceLookup, self)
        return [
            plan
            for plan in plans
            if str(await lookup._get_workflow_workspace_id_locked(plan.workflow_id))
            == workspace_id
        ]

    async def dispatch_due_cron_runs(
        self, *, now: datetime | None = None, workspace_id: str | None = None
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
            plans = await self._plans_in_workspace_locked(plans, workspace_id)
            for plan in plans:
                try:
                    workflow = await self._get_workflow_locked(plan.workflow_id)
                except WorkflowNotFoundError:
                    self._trigger_layer.remove_cron_config(plan.workflow_id)
                    continue
                if workflow.is_archived:
                    self._trigger_layer.remove_cron_config(plan.workflow_id)
                    continue
                try:
                    version = await self._get_latest_version_locked(plan.workflow_id)
                except WorkflowVersionNotFoundError:
                    continue
                workspace_repo = cast(_WorkflowWorkspaceLookup, self)
                workspace_id = await workspace_repo._get_workflow_workspace_id_locked(
                    plan.workflow_id
                )

                try:
                    await self._ensure_workflow_health(
                        plan.workflow_id,
                        actor="cron",
                        workspace_id=workspace_id,
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
                    run = await self._create_cron_run_once(
                        plan, version_id=version.id, workspace_id=workspace_id
                    )
                except WorkspaceQuotaExceededError:
                    logger.warning(
                        "Skipping cron dispatch for workflow %s because workspace "
                        "quota was exceeded",
                        plan.workflow_id,
                    )
                    continue
                if run is not None:
                    runs.append(run.model_copy(deep=True))
        # Enqueue AFTER lock is released to ensure commits are fully visible
        for run in runs:
            await _enqueue_run_and_confirm(self, run)
        return runs

    async def _create_cron_run_once(
        self,
        plan: CronDispatchPlan,
        *,
        version_id: UUID,
        workspace_id: str | None,
    ) -> WorkflowRun | None:
        """Create a run and advance its schedule in one row-locked transaction."""
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT config, last_dispatched_at FROM cron_triggers
                 WHERE workflow_id = %s FOR UPDATE SKIP LOCKED
                """,
                (str(plan.workflow_id),),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            config = CronTriggerConfig.model_validate(row["config"])
            if config != self._trigger_layer.get_cron_config(plan.workflow_id):
                return None
            last_dispatched = row["last_dispatched_at"]
            if last_dispatched is not None and last_dispatched >= plan.scheduled_for:
                return None
            if not config.allow_overlapping:
                cursor = await conn.execute(
                    """
                    SELECT 1 FROM workflow_runs
                     WHERE workflow_id = %s AND triggered_by = 'cron'
                       AND status IN ('pending', 'running') LIMIT 1
                    """,
                    (str(plan.workflow_id),),
                )
                if await cursor.fetchone() is not None:
                    return None
            run = await self._create_run_locked(
                workflow_id=plan.workflow_id,
                workflow_version_id=version_id,
                triggered_by="cron",
                input_payload={
                    "scheduled_for": plan.scheduled_for.isoformat(),
                    "timezone": plan.timezone,
                },
                actor="cron",
                workspace_id=workspace_id,
                dispatch_requested=True,
                connection=conn,
            )
            await conn.execute(
                "UPDATE cron_triggers SET last_dispatched_at = %s "
                "WHERE workflow_id = %s",
                (plan.scheduled_for, str(plan.workflow_id)),
            )
        self._trigger_layer.track_run(plan.workflow_id, run.id)
        self._trigger_layer.register_cron_run(run.id)
        self._trigger_layer.commit_cron_dispatch(plan.workflow_id)
        return run

    async def dispatch_manual_runs(
        self, request: ManualDispatchRequest
    ) -> list[WorkflowRun]:
        await self._ensure_initialized()
        async with self._lock:
            workflow = await self._get_workflow_locked(request.workflow_id)
            if workflow.is_archived:
                raise WorkflowNotFoundError(str(request.workflow_id))
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
            workspace_repo = cast(_WorkflowWorkspaceLookup, self)
            workspace_id = await workspace_repo._get_workflow_workspace_id_locked(
                request.workflow_id
            )

            await self._ensure_workflow_health(
                request.workflow_id,
                actor=plan.actor or plan.triggered_by,
                workspace_id=workspace_id,
            )

            runs: list[WorkflowRun] = []
            for resolved in plan.runs:
                version = await self._get_version_locked(resolved.workflow_version_id)
                if version.workflow_id != request.workflow_id:
                    raise WorkflowVersionNotFoundError(
                        str(resolved.workflow_version_id)
                    )

            quota_error: WorkspaceQuotaExceededError | None = None
            for resolved in plan.runs:
                try:
                    run = await self._create_run_locked(
                        workflow_id=request.workflow_id,
                        workflow_version_id=resolved.workflow_version_id,
                        triggered_by=plan.triggered_by,
                        input_payload=resolved.input_payload,
                        runnable_config=resolved.runnable_config,
                        actor=plan.actor,
                        workspace_id=workspace_id,
                        dispatch_requested=True,
                    )
                except WorkspaceQuotaExceededError as exc:
                    logger.warning(
                        "Skipping manual dispatch for workflow %s because workspace "
                        "quota was exceeded",
                        request.workflow_id,
                    )
                    quota_error = exc
                    continue
                runs.append(run.model_copy(deep=True))
            # Refusing every run is a quota response, not an empty success;
            # a batch that partly fit still returns the runs it created.
            if not runs and quota_error is not None:
                raise quota_error
        # Enqueue AFTER lock is released to ensure commits are fully visible
        for run in runs:
            await _enqueue_run_and_confirm(self, run)
        return runs


__all__ = ["TriggerRepositoryMixin"]
