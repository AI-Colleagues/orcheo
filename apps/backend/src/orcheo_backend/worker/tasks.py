"""Celery tasks for asynchronous workflow execution."""

from __future__ import annotations
import asyncio
import logging
import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from uuid import UUID
from celery import Task
from celery.signals import task_failure, task_postrun, task_prerun
from orcheo_backend.worker.celery_app import celery_app


logger = logging.getLogger(__name__)

# Track task start times for duration calculation
_task_start_times: dict[str, float] = {}


@task_prerun.connect
def task_prerun_handler(
    task_id: str | None = None,
    task: Task | None = None,
    **kwargs: Any,
) -> None:
    """Log when a task starts execution."""
    if task_id:
        _task_start_times[task_id] = time.monotonic()
    task_name = task.name if task else "unknown"
    logger.info("Task started: %s (id=%s)", task_name, task_id)


@task_postrun.connect
def task_postrun_handler(
    task_id: str | None = None,
    task: Task | None = None,
    retval: Any = None,
    **kwargs: Any,
) -> None:
    """Log when a task completes with duration."""
    task_name = task.name if task else "unknown"
    duration_ms = None
    if task_id and task_id in _task_start_times:
        duration_ms = (time.monotonic() - _task_start_times.pop(task_id)) * 1000
        logger.info(
            "Task completed: %s (id=%s, duration=%.2fms)",
            task_name,
            task_id,
            duration_ms,
        )
    else:
        logger.info("Task completed: %s (id=%s)", task_name, task_id)


@task_failure.connect
def task_failure_handler(
    task_id: str | None = None,
    task: Task | None = None,
    exception: Exception | None = None,
    **kwargs: Any,
) -> None:
    """Log when a task fails."""
    task_name = task.name if task else "unknown"
    # Clean up start time if present
    if task_id:
        _task_start_times.pop(task_id, None)
    logger.error(
        "Task failed: %s (id=%s, error=%s)",
        task_name,
        task_id,
        str(exception) if exception else "unknown",
    )


WORKER_ACTOR = "worker"


def _get_event_loop() -> asyncio.AbstractEventLoop:
    """Get or create an event loop for running async code in sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _external_agent_provider_environment() -> dict[str, str]:
    """Return shared external-agent auth env from the runtime store."""
    from orcheo_backend.app.dependencies import get_external_agent_runtime_store
    from orcheo_backend.app.external_agent_runtime_store import (
        list_external_agent_providers,
    )

    runtime_store = get_external_agent_runtime_store()
    merged: dict[str, str] = {}
    for provider_name in list_external_agent_providers():
        merged.update(runtime_store.get_provider_environment(provider_name))
    return merged


@contextmanager
def _patched_environment(updates: Mapping[str, str]) -> Iterator[None]:
    """Temporarily apply environment variables for the current worker process."""
    original = {key: os.environ.get(key) for key in updates}
    for key, value in updates.items():
        os.environ[key] = value
    try:
        yield
    finally:
        for key, old_value in original.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


async def _load_and_validate_run(
    run_id: str,
) -> tuple[Any, dict[str, Any] | None]:
    """Load run from repository and validate its status.

    Args:
        run_id: UUID string of the run to load

    Returns:
        Tuple of (run object, error dict if any)
    """
    from orcheo.models.workflow_entities import WorkflowRunStatus
    from orcheo_backend.app.dependencies import get_repository
    from orcheo_backend.app.repository import WorkflowRunNotFoundError

    repository = get_repository()

    try:
        run = await repository.get_run(UUID(run_id))
    except WorkflowRunNotFoundError:
        logger.error("Run %s not found", run_id)
        return None, {"status": "failed", "error": "Run not found"}

    if run.status != WorkflowRunStatus.PENDING:
        logger.warning(
            "Run %s is already in status '%s', skipping execution",
            run_id,
            run.status,
        )
        return None, {
            "status": "skipped",
            "reason": f"Run already in status: {run.status}",
        }

    return run, None


async def _mark_run_started(run: Any, run_id: str) -> dict[str, Any] | None:
    """Mark run as started in the repository.

    Args:
        run: The workflow run object
        run_id: UUID string of the run

    Returns:
        Error dict if marking failed, None on success
    """
    from orcheo_backend.app.dependencies import get_repository

    repository = get_repository()

    try:
        await repository.mark_run_started(run.id, actor=WORKER_ACTOR)
        logger.info("Run %s marked as started", run_id)
        return None
    except ValueError as exc:
        logger.warning("Failed to start run %s: %s", run_id, exc)
        return {"status": "skipped", "reason": str(exc)}


async def _execute_workflow(run: Any) -> dict[str, Any]:
    """Execute the workflow for the given run.

    Args:
        run: The workflow run object

    Returns:
        Result dict with status and optional error
    """
    from langchain_core.runnables import RunnableConfig
    from orcheo.config import get_settings
    from orcheo.graph.builder import build_graph
    from orcheo.models import CredentialAccessContext
    from orcheo.persistence import create_checkpointer, create_graph_store
    from orcheo.runtime.credentials import CredentialResolver, credential_resolution
    from orcheo.runtime.runnable_config import merge_runnable_configs
    from orcheo_backend.app.dependencies import (
        get_history_store,
        get_repository,
        get_vault,
    )
    from orcheo_backend.app.history import RunHistoryError
    from orcheo_backend.app.workflow_execution import _build_initial_state

    repository = get_repository()
    history_store = get_history_store()
    run_id = str(run.id)

    try:
        version = await repository.get_version(run.workflow_version_id)
        graph_config = version.graph
        inputs = run.input_payload or {}

        settings = get_settings()
        vault = get_vault()
        credential_context = CredentialAccessContext(workflow_id=version.workflow_id)
        resolver = CredentialResolver(vault, context=credential_context)

        execution_id = str(run.id)
        stored_config = run.runnable_config or version.runnable_config
        merged_config = merge_runnable_configs(stored_config, None)
        runtime_config: RunnableConfig = merged_config.to_runnable_config(execution_id)
        state_config = merged_config.to_state_config(execution_id)
        await _start_history_record(
            history_store=history_store,
            workflow_id=str(version.workflow_id),
            execution_id=execution_id,
            inputs=inputs,
            merged_config=merged_config,
            history_error_cls=RunHistoryError,
        )

        external_agent_environ = _external_agent_provider_environment()
        with _patched_environment(external_agent_environ):
            with credential_resolution(resolver):
                async with create_checkpointer(settings) as checkpointer:
                    async with create_graph_store(settings) as graph_store:
                        graph = build_graph(graph_config)
                        compiled = graph.compile(
                            checkpointer=checkpointer,
                            store=graph_store,
                        )
                        state = _build_initial_state(graph_config, inputs, state_config)
                        await _stream_run_history_steps(
                            compiled=compiled,
                            state=state,
                            runtime_config=runtime_config,
                            history_store=history_store,
                            execution_id=execution_id,
                            history_error_cls=RunHistoryError,
                        )
                        final_state = await compiled.aget_state(runtime_config)
                        final_state = getattr(final_state, "values", final_state)

        output = _extract_output(final_state)
        await repository.mark_run_succeeded(
            run.id,
            actor=WORKER_ACTOR,
            output=output,
        )
        await _mark_history_completed(
            history_store=history_store,
            execution_id=execution_id,
            history_error_cls=RunHistoryError,
        )
        logger.info("Run %s completed successfully", run_id)
        return {"status": "succeeded"}

    except Exception as exc:
        return await _handle_execution_failure(
            run,
            exc,
            history_store=history_store,
        )


async def _start_history_record(
    *,
    history_store: Any,
    workflow_id: str,
    execution_id: str,
    inputs: dict[str, Any],
    merged_config: Any,
    history_error_cls: type[Exception],
) -> None:
    """Persist initial run history metadata for worker executions."""
    stored_config_payload = merged_config.to_json_config(execution_id)
    try:
        await history_store.start_run(
            workflow_id=workflow_id,
            execution_id=execution_id,
            inputs=inputs,
            runnable_config=stored_config_payload,
            tags=merged_config.tags,
            callbacks=merged_config.callbacks,
            metadata=merged_config.metadata,
            run_name=merged_config.run_name,
        )
    except history_error_cls:
        logger.exception(
            "Failed to start run history for execution %s",
            execution_id,
        )


async def _stream_run_history_steps(
    *,
    compiled: Any,
    state: Any,
    runtime_config: Any,
    history_store: Any,
    execution_id: str,
    history_error_cls: type[Exception],
) -> None:
    """Append streamed node updates to the run history store."""
    async for step in compiled.astream(
        state,
        config=runtime_config,  # type: ignore[arg-type]
        stream_mode="updates",
    ):
        try:
            await history_store.append_step(execution_id, step)
        except history_error_cls:
            logger.exception(
                "Failed to append run history step for execution %s",
                execution_id,
            )


async def _mark_history_completed(
    *,
    history_store: Any,
    execution_id: str,
    history_error_cls: type[Exception],
) -> None:
    """Persist completion markers for worker-executed runs."""
    completion_payload = {"status": "completed"}
    try:
        await history_store.append_step(execution_id, completion_payload)
        await history_store.mark_completed(execution_id)
    except history_error_cls:
        logger.exception(
            "Failed to mark run history completed for execution %s",
            execution_id,
        )


def _extract_output(final_state: Any) -> dict[str, Any] | None:
    """Extract output from final workflow state.

    Args:
        final_state: The final state from workflow execution

    Returns:
        Output dict or None
    """
    if isinstance(final_state, dict):
        return {"final_state": final_state}
    if hasattr(final_state, "model_dump"):
        return {"final_state": final_state.model_dump()}
    return None


async def _handle_execution_failure(
    run: Any,
    exc: Exception,
    *,
    history_store: Any | None = None,
) -> dict[str, Any]:
    """Handle workflow execution failure.

    Args:
        run: The workflow run object
        exc: The exception that occurred
        history_store: Optional run history store for failure persistence.

    Returns:
        Error result dict
    """
    from orcheo_backend.app.dependencies import get_repository
    from orcheo_backend.app.workflow_remediation import create_candidate_for_failed_run

    repository = get_repository()
    run_id = str(run.id)
    error_message = str(exc)
    run_failure_persisted = False

    logger.exception("Run %s failed: %s", run_id, error_message)

    try:
        await repository.mark_run_failed(
            run.id,
            actor=WORKER_ACTOR,
            error=error_message,
        )
        run_failure_persisted = True
    except Exception as mark_exc:
        logger.exception(
            "Failed to mark run %s as failed: %s",
            run_id,
            mark_exc,
        )

    if history_store is not None:
        error_payload = {"status": "error", "error": error_message}
        try:
            await history_store.append_step(run_id, error_payload)
            await history_store.mark_failed(run_id, error_message)
        except Exception as history_exc:
            logger.exception(
                "Failed to mark run history %s as failed: %s",
                run_id,
                history_exc,
            )

    if run_failure_persisted:
        await create_candidate_for_failed_run(
            repository=repository,
            history_store=history_store,
            run=run,
            exc=exc,
        )

    return {"status": "failed", "error": error_message}


async def _execute_run_async(run_id: str) -> dict[str, Any]:
    """Execute a workflow run asynchronously.

    Args:
        run_id: UUID string of the run to execute

    Returns:
        dict with keys: status (succeeded/failed), error (optional)
    """
    run, error = await _load_and_validate_run(run_id)
    if error:
        return error

    start_error = await _mark_run_started(run, run_id)
    if start_error:
        return start_error

    return await _execute_workflow(run)


@celery_app.task(bind=True, max_retries=0)
def execute_run(self: Task, run_id: str) -> dict[str, Any]:  # noqa: ARG001
    """Execute a workflow run by ID.

    Args:
        self: Celery task instance (unused, required by bind=True)
        run_id: UUID of the run to execute

    Returns:
        dict with keys: status (succeeded/failed/skipped), error (optional)
    """
    logger.info("Executing run %s", run_id)
    loop = _get_event_loop()
    return loop.run_until_complete(_execute_run_async(run_id))


async def _dispatch_cron_triggers_async() -> list[str]:
    """Dispatch due cron triggers and return enqueued run IDs.

    Returns:
        List of enqueued run IDs
    """
    from orcheo_backend.app.dependencies import get_repository

    repository = get_repository()
    runs = await repository.dispatch_due_cron_runs()
    return [str(run.id) for run in runs]


async def _refresh_external_agent_status_async(provider_name: str) -> dict[str, str]:
    """Refresh worker-scoped status for one external agent provider."""
    from orcheo_backend.worker.external_agents import (
        refresh_external_agent_status_async,
    )

    return await refresh_external_agent_status_async(provider_name)


async def _start_external_agent_login_async(
    provider_name: str,
    session_id: str,
) -> dict[str, str]:
    """Run a worker-side external agent OAuth session."""
    from orcheo_backend.worker.external_agents import (
        start_external_agent_login_async,
    )

    return await start_external_agent_login_async(provider_name, session_id)


async def _disconnect_external_agent_async(provider_name: str) -> dict[str, str]:
    """Clear worker-side auth state for one external agent provider."""
    from orcheo_backend.worker.external_agents import disconnect_external_agent_async

    return await disconnect_external_agent_async(provider_name)


@celery_app.task(bind=True)
def dispatch_cron_triggers(self: Task) -> dict[str, Any]:  # noqa: ARG001
    """Dispatch due cron triggers by calling the cron dispatch endpoint.

    This task is invoked periodically by Celery Beat to trigger scheduled runs.

    Args:
        self: Celery task instance (unused, required by bind=True)

    Returns:
        dict with keys: dispatched_runs (list of run IDs)
    """
    logger.info("Dispatching cron triggers")
    loop = _get_event_loop()
    run_ids = loop.run_until_complete(_dispatch_cron_triggers_async())
    logger.info("Dispatched %d cron runs", len(run_ids))
    return {"dispatched_runs": run_ids}


async def _scan_workflow_remediations_async() -> dict[str, Any]:
    """Scan and claim pending workflow remediation candidates when idle."""
    from orcheo.config import get_settings
    from orcheo_backend.app.dependencies import get_repository
    from orcheo_backend.app.workflow_remediation import (
        load_workflow_autofix_settings,
        scan_workflow_remediations_async,
    )

    return await scan_workflow_remediations_async(
        repository=get_repository(),
        celery_app=celery_app,
        settings=load_workflow_autofix_settings(get_settings()),
    )


async def _attempt_workflow_remediation_async(remediation_id: str) -> dict[str, Any]:
    """Attempt one claimed workflow remediation candidate."""
    from orcheo.config import get_settings
    from orcheo_backend.app.dependencies import get_repository
    from orcheo_backend.app.workflow_remediation import (
        attempt_workflow_remediation_async,
        load_workflow_autofix_settings,
    )

    return await attempt_workflow_remediation_async(
        repository=get_repository(),
        remediation_id=UUID(remediation_id),
        settings=load_workflow_autofix_settings(get_settings()),
    )


@celery_app.task(bind=True)
def scan_workflow_remediations(self: Task) -> dict[str, Any]:  # noqa: ARG001
    """Claim and enqueue one workflow autofix candidate when the worker is idle."""
    logger.info("Scanning workflow remediation candidates")
    loop = _get_event_loop()
    return loop.run_until_complete(_scan_workflow_remediations_async())


@celery_app.task(bind=True, max_retries=0)
def attempt_workflow_remediation(
    self: Task,  # noqa: ARG001
    remediation_id: str,
) -> dict[str, Any]:
    """Run one workflow autofix remediation attempt by candidate id."""
    logger.info("Attempting workflow remediation %s", remediation_id)
    loop = _get_event_loop()
    return loop.run_until_complete(_attempt_workflow_remediation_async(remediation_id))


@celery_app.task(bind=True)
def refresh_external_agent_status(
    self: Task,  # noqa: ARG001
    provider_name: str,
) -> dict[str, str]:
    """Refresh worker-scoped status for one external agent provider."""
    logger.info("Refreshing external agent status for %s", provider_name)
    loop = _get_event_loop()
    return loop.run_until_complete(_refresh_external_agent_status_async(provider_name))


@celery_app.task(bind=True)
def start_external_agent_login(
    self: Task,  # noqa: ARG001
    provider_name: str,
    session_id: str,
) -> dict[str, str]:
    """Run a worker-side external agent OAuth login session."""
    logger.info(
        "Starting external agent login for %s (session=%s)",
        provider_name,
        session_id,
    )
    loop = _get_event_loop()
    return loop.run_until_complete(
        _start_external_agent_login_async(provider_name, session_id)
    )


@celery_app.task(bind=True)
def disconnect_external_agent(
    self: Task,  # noqa: ARG001
    provider_name: str,
) -> dict[str, str]:
    """Clear worker-side auth state for one external agent provider."""
    logger.info("Disconnecting external agent auth for %s", provider_name)
    loop = _get_event_loop()
    return loop.run_until_complete(_disconnect_external_agent_async(provider_name))


__all__ = [
    "disconnect_external_agent",
    "dispatch_cron_triggers",
    "execute_run",
    "refresh_external_agent_status",
    "start_external_agent_login",
]
