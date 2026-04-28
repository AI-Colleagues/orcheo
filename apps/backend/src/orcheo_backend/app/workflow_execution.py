"""Workflow execution helpers and websocket streaming utilities."""

from __future__ import annotations
import asyncio
import logging
import os
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, cast
from uuid import UUID
from fastapi import WebSocket, WebSocketDisconnect
from langchain_core.runnables import RunnableConfig
from opentelemetry.trace import Span, Tracer
from orcheo.agentensor.evaluation import EvaluationRequest
from orcheo.agentensor.training import TrainingRequest
from orcheo.config import get_settings
from orcheo.external_agents import scoped_external_agent_environment
from orcheo.graph.state import State
from orcheo.nodes.agent_tools.context import tool_progress_context
from orcheo.nodes.agentensor import AgentensorNode
from orcheo.nodes.browser import close_browser_sessions_for_scope
from orcheo.runtime.credentials import CredentialResolver, credential_resolution
from orcheo.runtime.runnable_config import (
    RunnableConfigModel,
    merge_runnable_configs,
)
from orcheo.runtime.state_builder import build_initial_state
from orcheo.tracing import (
    get_tracer,
    record_workflow_cancellation,
    record_workflow_completion,
    record_workflow_failure,
    record_workflow_step,
    workflow_span,
)
from orcheo.tracing.model_metadata import strip_trace_metadata
from orcheo_backend.app.dependencies import (
    credential_context_from_workflow,
    get_checkpoint_store,
    get_external_agent_runtime_store,
    get_history_store,
    get_vault,
)
from orcheo_backend.app.external_agent_auth import (
    load_external_agent_vault_environment,
)
from orcheo_backend.app.external_agent_runtime_store import (
    list_external_agent_providers,
)
from orcheo_backend.app.history import (
    RunHistoryError,
    RunHistoryRecord,
    RunHistoryStep,
    RunHistoryStore,
)
from orcheo_backend.app.trace_utils import build_trace_update


logger = logging.getLogger(__name__)


@contextmanager
def _patched_environment(updates: Mapping[str, str]) -> Iterator[None]:
    """Temporarily apply environment variables for the current backend process."""
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


_should_log_sensitive_debug = False


def configure_sensitive_logging(
    *,
    enable_sensitive_debug: bool,
) -> None:
    """Enable or disable sensitive debug logging."""
    global _should_log_sensitive_debug  # noqa: PLW0603
    _should_log_sensitive_debug = enable_sensitive_debug


def _log_sensitive_debug(message: str, *args: Any) -> None:
    if _should_log_sensitive_debug:
        from orcheo_backend.app import logger as app_logger

        app_logger.debug(message, *args)


def _log_step_debug(step: Mapping[str, Any]) -> None:
    if not _should_log_sensitive_debug:
        return
    from orcheo_backend.app import logger as app_logger

    for node_name, node_output in step.items():
        app_logger.debug("=" * 80)
        app_logger.debug("Node executed: %s", node_name)
        app_logger.debug("Node output: %s", node_output)
        app_logger.debug("=" * 80)


def _log_final_state_debug(state_values: Mapping[str, Any] | Any) -> None:
    if not _should_log_sensitive_debug:
        return
    from orcheo_backend.app import logger as app_logger

    app_logger.debug("=" * 80)
    app_logger.debug("Final state values: %s", state_values)
    app_logger.debug("=" * 80)


_CANNOT_SEND_AFTER_CLOSE = 'Cannot call "send" once a close message has been sent.'


def _external_agent_provider_environment() -> dict[str, str]:
    """Return shared external-agent auth env from the runtime store."""
    runtime_store = get_external_agent_runtime_store()
    merged: dict[str, str] = {}
    for provider_name in list_external_agent_providers():
        merged.update(runtime_store.get_provider_environment(provider_name))
    merged.update(load_external_agent_vault_environment(get_vault()))
    return merged


def _sanitize_public_step_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Strip trace-only metadata before sending workflow updates to clients."""
    sanitized = strip_trace_metadata(payload)
    return sanitized if isinstance(sanitized, dict) else dict(payload)


async def _safe_send_json(websocket: WebSocket, payload: Any) -> bool:
    """Send JSON only while the websocket is open."""
    try:
        await websocket.send_json(payload)
    except WebSocketDisconnect:
        logger.debug("Websocket disconnected before payload could be sent.")
        return False
    except RuntimeError as exc:
        if str(exc) == _CANNOT_SEND_AFTER_CLOSE:
            logger.debug("Websocket already closed; skipping payload send.")
            return False
        raise
    return True


async def _emit_trace_update(
    history_store: RunHistoryStore,
    websocket: WebSocket,
    execution_id: str,
    *,
    step: RunHistoryStep | None = None,
    include_root: bool = False,
    complete: bool = False,
) -> None:
    """Fetch the latest history snapshot and stream a trace update."""
    try:
        record = await history_store.get_history(execution_id)
    except RunHistoryError:
        return
    if not isinstance(record, RunHistoryRecord):
        return

    update = build_trace_update(
        record, step=step, include_root=include_root, complete=complete
    )
    if update is not None:
        await _safe_send_json(websocket, update.model_dump(mode="json"))


async def _forward_node_step(
    payload: Mapping[str, Any],
    *,
    history_store: RunHistoryStore,
    execution_id: str,
    websocket: WebSocket,
    tracer: Tracer,
) -> None:
    """Persist and stream a single step payload to the connected client."""
    record_workflow_step(tracer, payload)
    history_step = await history_store.append_step(execution_id, payload)
    await _safe_send_json(websocket, _sanitize_public_step_payload(payload))
    await _emit_trace_update(
        history_store,
        websocket,
        execution_id,
        step=history_step,
    )


async def _stream_workflow_updates(
    compiled_graph: Any,
    state: Any,
    config: RunnableConfig,
    history_store: RunHistoryStore,
    execution_id: str,
    websocket: WebSocket,
    tracer: Tracer,
) -> None:
    """Stream workflow updates to the client while recording history."""

    async def in_node_status_callback(payload: Mapping[str, Any]) -> None:
        if not isinstance(payload, Mapping):
            return  # pragma: no cover - defensive
        if payload.get("event") != "node_status":
            # Sub-graph (tool) updates are surfaced through their own paths.
            return
        await _forward_node_step(
            payload,
            history_store=history_store,
            execution_id=execution_id,
            websocket=websocket,
            tracer=tracer,
        )

    with tool_progress_context(in_node_status_callback):
        async for step in compiled_graph.astream(
            state,
            config=config,  # type: ignore[arg-type]
            stream_mode="updates",
        ):  # pragma: no cover
            _log_step_debug(step)
            record_workflow_step(tracer, step)
            history_step = await history_store.append_step(execution_id, step)
            try:
                await _safe_send_json(websocket, _sanitize_public_step_payload(step))
            except Exception as exc:  # pragma: no cover
                logger.error("Error processing messages: %s", exc)
                raise

            await _emit_trace_update(
                history_store,
                websocket,
                execution_id,
                step=history_step,
            )

    final_state = await compiled_graph.aget_state(cast(RunnableConfig, config))
    _log_final_state_debug(final_state.values)


async def _run_workflow_stream(
    compiled_graph: Any,
    state: Any,
    config: RunnableConfig,
    history_store: RunHistoryStore,
    execution_id: str,
    websocket: WebSocket,
    tracer: Tracer,
    span: Span,
) -> None:
    """Stream updates and handle cancellation or failure outcomes."""
    try:
        await _stream_workflow_updates(
            compiled_graph,
            state,
            config,
            history_store,
            execution_id,
            websocket,
            tracer,
        )
    except asyncio.CancelledError as exc:
        reason = str(exc) or "Workflow execution cancelled"
        record_workflow_cancellation(span, reason=reason)
        cancellation_payload = {"status": "cancelled", "reason": reason}
        await history_store.append_step(execution_id, cancellation_payload)
        await history_store.mark_cancelled(execution_id, reason=reason)
        await _emit_trace_update(
            history_store,
            websocket,
            execution_id,
            include_root=True,
            complete=True,
        )
        raise
    except RunHistoryError as exc:
        _report_history_error(
            execution_id,
            span,
            exc,
            context="persist workflow history",
        )
        raise
    except Exception as exc:
        record_workflow_failure(span, exc)
        error_message = str(exc)
        error_payload = {"status": "error", "error": error_message}
        await _persist_failure_history(
            history_store,
            execution_id,
            error_payload,
            error_message,
            span,
        )
        await _emit_trace_update(
            history_store,
            websocket,
            execution_id,
            include_root=True,
            complete=True,
        )
        raise


def _report_history_error(
    execution_id: str,
    span: Span,
    exc: Exception,
    *,
    context: str,
) -> None:
    """Record tracing metadata and log a run history persistence failure."""
    record_workflow_failure(span, exc)
    logger.exception("Failed to %s for execution %s", context, execution_id)


async def _persist_failure_history(
    history_store: RunHistoryStore,
    execution_id: str,
    payload: Mapping[str, Any],
    error_message: str,
    span: Span,
) -> None:
    """Persist failure metadata while tolerating run history errors."""
    try:
        await history_store.append_step(execution_id, payload)
        await history_store.mark_failed(execution_id, error_message)
    except RunHistoryError as history_exc:
        _report_history_error(
            execution_id,
            span,
            history_exc,
            context="record failure state",
        )


def _build_initial_state(
    graph_config: Mapping[str, Any],
    inputs: dict[str, Any],
    runtime_config: Mapping[str, Any] | None,
) -> Any:
    """Return the starting runtime state for a workflow execution."""
    return build_initial_state(graph_config, inputs, runtime_config)


def _prepare_runnable_config(
    execution_id: str,
    candidate: Mapping[str, Any] | RunnableConfigModel | None,
    stored_config: Mapping[str, Any] | RunnableConfigModel | None = None,
) -> tuple[RunnableConfigModel, RunnableConfig, dict[str, Any], dict[str, Any]]:
    merged_config = merge_runnable_configs(stored_config, candidate)
    runtime_config = merged_config.to_runnable_config(execution_id)
    state_config = merged_config.to_state_config(execution_id)
    stored_payload = merged_config.to_json_config(execution_id)
    return merged_config, runtime_config, state_config, stored_payload


async def _resolve_stored_runnable_config(
    workflow_id: UUID | None,
    stored_runnable_config: Mapping[str, Any] | RunnableConfigModel | None,
) -> Mapping[str, Any] | RunnableConfigModel | None:
    """Return stored runnable config, loading from repository when needed."""
    if stored_runnable_config is not None or workflow_id is None:
        return stored_runnable_config
    from aiosqlite import Error as SqliteError
    from orcheo_backend.app.dependencies import get_repository
    from orcheo_backend.app.repository import (
        RepositoryError,
        WorkflowNotFoundError,
        WorkflowVersionNotFoundError,
    )

    try:
        repository = get_repository()
        version = await repository.get_latest_version(workflow_id)
    except (WorkflowNotFoundError, WorkflowVersionNotFoundError):
        return None
    except (RepositoryError, SqliteError):
        logger.exception(
            "Failed to load stored runnable config for workflow %s",
            workflow_id,
        )
        return {}
    return version.runnable_config


async def execute_workflow(
    workflow_id: str,
    graph_config: dict[str, Any],
    inputs: dict[str, Any],
    execution_id: str,
    websocket: WebSocket,
    runnable_config: Mapping[str, Any] | RunnableConfigModel | None = None,
    stored_runnable_config: Mapping[str, Any] | RunnableConfigModel | None = None,
) -> None:
    """Execute a workflow and stream results over the provided websocket."""
    from orcheo_backend.app import build_graph, create_checkpointer, create_graph_store

    logger.info("Starting workflow %s with execution_id: %s", workflow_id, execution_id)
    _log_sensitive_debug("Initial inputs: %s", inputs)

    settings = get_settings()
    history_store = get_history_store()
    vault = get_vault()
    workflow_uuid: UUID | None = None
    try:
        workflow_uuid = UUID(workflow_id)
    except ValueError:
        pass
    credential_context = credential_context_from_workflow(workflow_uuid)
    resolver = CredentialResolver(vault, context=credential_context)
    tracer = get_tracer(__name__)
    stored_runnable_config = await _resolve_stored_runnable_config(
        workflow_uuid,
        stored_runnable_config,
    )
    parsed_config, runtime_config, state_config, stored_config = (
        _prepare_runnable_config(
            execution_id,
            runnable_config,
            stored_runnable_config,
        )
    )

    try:
        with workflow_span(
            tracer,
            workflow_id=workflow_id,
            execution_id=execution_id,
            inputs=inputs,
            runnable_config=parsed_config,
        ) as span_context:
            await history_store.start_run(
                workflow_id=workflow_id,
                execution_id=execution_id,
                inputs=inputs,
                trace_id=span_context.trace_id,
                trace_started_at=span_context.started_at,
                runnable_config=stored_config,
                tags=parsed_config.tags,
                callbacks=parsed_config.callbacks,
                metadata=parsed_config.metadata,
                run_name=parsed_config.run_name,
            )
            await _emit_trace_update(
                history_store,
                websocket,
                execution_id,
                include_root=True,
            )

            external_agent_environ = _external_agent_provider_environment()
            with scoped_external_agent_environment(external_agent_environ):
                with credential_resolution(resolver):
                    async with create_checkpointer(settings) as checkpointer:
                        async with create_graph_store(settings) as graph_store:
                            graph = build_graph(graph_config)
                            compiled_graph = graph.compile(
                                checkpointer=checkpointer,
                                store=graph_store,
                            )

                            state = _build_initial_state(
                                graph_config, inputs, state_config
                            )
                            _log_sensitive_debug("Initial state: %s", state)

                            await _run_workflow_stream(
                                compiled_graph,
                                state,
                                runtime_config,
                                history_store,
                                execution_id,
                                websocket,
                                tracer,
                                span_context.span,
                            )

            completion_payload = {"status": "completed"}
            record_workflow_completion(span_context.span)
            await history_store.append_step(execution_id, completion_payload)
            await history_store.mark_completed(execution_id)
            await _safe_send_json(websocket, completion_payload)  # pragma: no cover

            await _emit_trace_update(
                history_store,
                websocket,
                execution_id,
                include_root=True,
                complete=True,
            )
    finally:
        await close_browser_sessions_for_scope(execution_id)


async def _run_evaluation_node(
    *,
    graph_config: Mapping[str, Any],
    inputs: dict[str, Any],
    runtime_config: RunnableConfig,
    state_config: Mapping[str, Any],
    evaluation_request: EvaluationRequest,
    parsed_config: RunnableConfigModel,
    history_store: RunHistoryStore,
    websocket: WebSocket,
    execution_id: str,
    tracer: Tracer,
    resolver: CredentialResolver,
    settings: Any,
    span: Span,
) -> None:
    """Compile the graph and execute evaluation cases."""
    from orcheo_backend.app import build_graph, create_checkpointer, create_graph_store

    async def on_progress(payload: Mapping[str, Any]) -> None:
        record_workflow_step(tracer, payload)
        history_step = await history_store.append_step(execution_id, payload)
        await _safe_send_json(websocket, _sanitize_public_step_payload(payload))
        await _emit_trace_update(
            history_store,
            websocket,
            execution_id,
            step=history_step,
        )

    external_agent_environ = _external_agent_provider_environment()
    with scoped_external_agent_environment(external_agent_environ):
        with credential_resolution(resolver):
            async with create_checkpointer(settings) as checkpointer:
                async with create_graph_store(settings) as graph_store:
                    graph = build_graph(graph_config)
                    compiled_graph = graph.compile(
                        checkpointer=checkpointer,
                        store=graph_store,
                    )
                    node = AgentensorNode(
                        name="agentensor_evaluator",
                        mode="evaluate",
                        prompts=parsed_config.prompts or {},
                        dataset=evaluation_request.dataset,
                        evaluators=evaluation_request.evaluators,
                        max_cases=evaluation_request.max_cases,
                        compiled_graph=compiled_graph,
                        graph_config=graph_config,
                        state_config=state_config,
                        progress_callback=on_progress,
                    )
                    state = _build_initial_state(graph_config, inputs, state_config)
                    _log_sensitive_debug("Initial state: %s", state)

                    try:
                        result = await node(state, runtime_config)
                        node_payload = result.get("results", {}).get(
                            node.name, result.get("results", result)
                        )
                        final_step = await history_store.append_step(
                            execution_id,
                            {
                                "node": node.name,
                                "event": "evaluation_result",
                                "payload": node_payload,
                            },
                        )
                        await _safe_send_json(
                            websocket,
                            {
                                "node": node.name,
                                "event": "evaluation_result",
                                "payload": node_payload,
                            },
                        )
                        await _emit_trace_update(
                            history_store,
                            websocket,
                            execution_id,
                            step=final_step,
                        )
                    except asyncio.CancelledError as exc:
                        reason = str(exc) or "Evaluation cancelled"
                        record_workflow_cancellation(span, reason=reason)
                        cancellation_payload = {"status": "cancelled", "reason": reason}
                        await history_store.append_step(
                            execution_id, cancellation_payload
                        )
                        await history_store.mark_cancelled(execution_id, reason=reason)
                        await _emit_trace_update(
                            history_store,
                            websocket,
                            execution_id,
                            include_root=True,
                            complete=True,
                        )
                        raise
                    except Exception as exc:
                        record_workflow_failure(span, exc)
                        error_message = str(exc)
                        error_payload = {"status": "error", "error": error_message}
                        await _persist_failure_history(
                            history_store,
                            execution_id,
                            error_payload,
                            error_message,
                            span,
                        )
                        await _emit_trace_update(
                            history_store,
                            websocket,
                            execution_id,
                            include_root=True,
                            complete=True,
                        )
                        raise


async def _run_training_node(
    *,
    workflow_id: str,
    graph_config: Mapping[str, Any],
    inputs: dict[str, Any],
    runtime_config: RunnableConfig,
    state_config: Mapping[str, Any],
    training_request: TrainingRequest,
    parsed_config: RunnableConfigModel,
    history_store: RunHistoryStore,
    websocket: WebSocket,
    execution_id: str,
    tracer: Tracer,
    resolver: CredentialResolver,
    settings: Any,
    span: Span,
    checkpoint_store: Any,
) -> None:
    """Compile the graph and execute training with checkpoints."""
    from orcheo_backend.app import build_graph, create_checkpointer, create_graph_store

    async def on_progress(payload: Mapping[str, Any]) -> None:
        record_workflow_step(tracer, payload)
        history_step = await history_store.append_step(execution_id, payload)
        await _safe_send_json(websocket, _sanitize_public_step_payload(payload))
        await _emit_trace_update(
            history_store,
            websocket,
            execution_id,
            step=history_step,
        )

    external_agent_environ = _external_agent_provider_environment()
    with scoped_external_agent_environment(external_agent_environ):
        with credential_resolution(resolver):
            async with create_checkpointer(settings) as checkpointer:
                async with create_graph_store(settings) as graph_store:
                    graph = build_graph(graph_config)
                    compiled_graph = graph.compile(
                        checkpointer=checkpointer,
                        store=graph_store,
                    )
                    node = AgentensorNode(
                        name="agentensor_trainer",
                        mode="train",
                        prompts=parsed_config.prompts or {},
                        dataset=training_request.dataset,
                        evaluators=training_request.evaluators,
                        max_cases=training_request.max_cases,
                        optimizer=training_request.optimizer,
                        compiled_graph=compiled_graph,
                        graph_config=graph_config,
                        state_config=state_config,
                        progress_callback=on_progress,
                        workflow_id=workflow_id,
                        checkpoint_store=checkpoint_store,
                    )
                    state = _build_initial_state(graph_config, inputs, state_config)
                    _log_sensitive_debug("Initial state: %s", state)

                    try:
                        result = await node(state, runtime_config)
                        node_payload = result.get("results", {}).get(
                            node.name, result.get("results", result)
                        )
                        final_step = await history_store.append_step(
                            execution_id,
                            {
                                "node": node.name,
                                "event": "training_result",
                                "payload": node_payload,
                            },
                        )
                        await _safe_send_json(
                            websocket,
                            {
                                "node": node.name,
                                "event": "training_result",
                                "payload": node_payload,
                            },
                        )
                        await _emit_trace_update(
                            history_store,
                            websocket,
                            execution_id,
                            step=final_step,
                        )
                    except asyncio.CancelledError as exc:
                        reason = str(exc) or "Training cancelled"
                        record_workflow_cancellation(span, reason=reason)
                        cancellation_payload = {"status": "cancelled", "reason": reason}
                        await history_store.append_step(
                            execution_id, cancellation_payload
                        )
                        await history_store.mark_cancelled(execution_id, reason=reason)
                        await _emit_trace_update(
                            history_store,
                            websocket,
                            execution_id,
                            include_root=True,
                            complete=True,
                        )
                        raise
                    except Exception as exc:
                        record_workflow_failure(span, exc)
                        error_message = str(exc)
                        error_payload = {"status": "error", "error": error_message}
                        await _persist_failure_history(
                            history_store,
                            execution_id,
                            error_payload,
                            error_message,
                            span,
                        )
                        await _emit_trace_update(
                            history_store,
                            websocket,
                            execution_id,
                            include_root=True,
                            complete=True,
                        )
                        raise


async def execute_workflow_evaluation(
    workflow_id: str,
    graph_config: dict[str, Any],
    inputs: dict[str, Any],
    execution_id: str,
    websocket: WebSocket,
    evaluation: Mapping[str, Any] | EvaluationRequest | None,
    runnable_config: Mapping[str, Any] | RunnableConfigModel | None = None,
    stored_runnable_config: Mapping[str, Any] | RunnableConfigModel | None = None,
) -> None:  # noqa: PLR0915
    """Execute workflow evaluation and stream progress via websocket."""
    logger.info(
        "Starting evaluation %s with execution_id: %s", workflow_id, execution_id
    )
    _log_sensitive_debug("Evaluation inputs: %s", inputs)

    try:
        evaluation_request = (
            evaluation
            if isinstance(evaluation, EvaluationRequest)
            else EvaluationRequest.model_validate(evaluation or {})
        )
    except Exception as exc:
        error_msg = f"Invalid evaluation payload: {exc}"
        await _safe_send_json(websocket, {"status": "error", "error": error_msg})
        return

    settings = get_settings()
    history_store = get_history_store()
    vault = get_vault()
    workflow_uuid: UUID | None = None
    try:
        workflow_uuid = UUID(workflow_id)
    except ValueError:
        pass
    credential_context = credential_context_from_workflow(workflow_uuid)
    resolver = CredentialResolver(vault, context=credential_context)
    tracer = get_tracer(__name__)
    stored_runnable_config = await _resolve_stored_runnable_config(
        workflow_uuid,
        stored_runnable_config,
    )
    parsed_config, runtime_config, state_config, stored_config = (
        _prepare_runnable_config(
            execution_id,
            runnable_config,
            stored_runnable_config,
        )
    )

    try:
        with workflow_span(
            tracer,
            workflow_id=workflow_id,
            execution_id=execution_id,
            inputs=inputs,
            runnable_config=parsed_config,
        ) as span_context:
            await history_store.start_run(
                workflow_id=workflow_id,
                execution_id=execution_id,
                inputs=inputs,
                trace_id=span_context.trace_id,
                trace_started_at=span_context.started_at,
                runnable_config=stored_config,
                tags=parsed_config.tags,
                callbacks=parsed_config.callbacks,
                metadata=parsed_config.metadata,
                run_name=parsed_config.run_name,
            )
            await _emit_trace_update(
                history_store,
                websocket,
                execution_id,
                include_root=True,
            )

            await _run_evaluation_node(
                graph_config=graph_config,
                inputs=inputs,
                runtime_config=runtime_config,
                state_config=state_config,
                evaluation_request=evaluation_request,
                parsed_config=parsed_config,
                history_store=history_store,
                websocket=websocket,
                execution_id=execution_id,
                tracer=tracer,
                resolver=resolver,
                settings=settings,
                span=span_context.span,
            )

            completion_payload = {"status": "completed"}
            record_workflow_completion(span_context.span)
            await history_store.append_step(execution_id, completion_payload)
            await history_store.mark_completed(execution_id)
            await _safe_send_json(websocket, completion_payload)  # pragma: no cover

            await _emit_trace_update(
                history_store,
                websocket,
                execution_id,
                include_root=True,
                complete=True,
            )
    finally:
        await close_browser_sessions_for_scope(execution_id)


async def execute_workflow_training(
    workflow_id: str,
    graph_config: dict[str, Any],
    inputs: dict[str, Any],
    execution_id: str,
    websocket: WebSocket,
    training: Mapping[str, Any] | TrainingRequest | None,
    runnable_config: Mapping[str, Any] | RunnableConfigModel | None = None,
    stored_runnable_config: Mapping[str, Any] | RunnableConfigModel | None = None,
) -> None:  # noqa: PLR0915
    """Execute workflow training and stream progress via websocket."""
    logger.info("Starting training %s with execution_id: %s", workflow_id, execution_id)
    _log_sensitive_debug("Training inputs: %s", inputs)

    try:
        training_request = (
            training
            if isinstance(training, TrainingRequest)
            else TrainingRequest.model_validate(training or {})
        )
    except Exception as exc:
        error_msg = f"Invalid training payload: {exc}"
        await _safe_send_json(websocket, {"status": "error", "error": error_msg})
        return

    settings = get_settings()
    history_store = get_history_store()
    checkpoint_store = get_checkpoint_store()
    vault = get_vault()
    workflow_uuid: UUID | None = None
    try:
        workflow_uuid = UUID(workflow_id)
    except ValueError:
        pass
    credential_context = credential_context_from_workflow(workflow_uuid)
    resolver = CredentialResolver(vault, context=credential_context)
    tracer = get_tracer(__name__)
    stored_runnable_config = await _resolve_stored_runnable_config(
        workflow_uuid,
        stored_runnable_config,
    )
    parsed_config, runtime_config, state_config, stored_config = (
        _prepare_runnable_config(
            execution_id,
            runnable_config,
            stored_runnable_config,
        )
    )

    try:
        with workflow_span(
            tracer,
            workflow_id=workflow_id,
            execution_id=execution_id,
            inputs=inputs,
            runnable_config=parsed_config,
        ) as span_context:
            await history_store.start_run(
                workflow_id=workflow_id,
                execution_id=execution_id,
                inputs=inputs,
                trace_id=span_context.trace_id,
                trace_started_at=span_context.started_at,
                runnable_config=stored_config,
                tags=parsed_config.tags,
                callbacks=parsed_config.callbacks,
                metadata=parsed_config.metadata,
                run_name=parsed_config.run_name,
            )
            await _emit_trace_update(
                history_store,
                websocket,
                execution_id,
                include_root=True,
            )

            await _run_training_node(
                workflow_id=workflow_id,
                graph_config=graph_config,
                inputs=inputs,
                runtime_config=runtime_config,
                state_config=state_config,
                training_request=training_request,
                parsed_config=parsed_config,
                history_store=history_store,
                websocket=websocket,
                execution_id=execution_id,
                tracer=tracer,
                resolver=resolver,
                settings=settings,
                span=span_context.span,
                checkpoint_store=checkpoint_store,
            )

            completion_payload = {"status": "completed"}
            record_workflow_completion(span_context.span)
            await history_store.append_step(execution_id, completion_payload)
            await history_store.mark_completed(execution_id)
            await _safe_send_json(websocket, completion_payload)  # pragma: no cover

            await _emit_trace_update(
                history_store,
                websocket,
                execution_id,
                include_root=True,
                complete=True,
            )
    finally:
        await close_browser_sessions_for_scope(execution_id)


async def execute_node(
    node_class: Callable[..., Any],
    node_params: dict[str, Any],
    inputs: dict[str, Any],
    workflow_id: UUID | None = None,
) -> Any:
    """Execute a single node instance with credential resolution."""
    vault = get_vault()
    context = credential_context_from_workflow(workflow_id)
    resolver = CredentialResolver(vault, context=context)

    external_agent_environ = _external_agent_provider_environment()
    with scoped_external_agent_environment(external_agent_environ):
        with credential_resolution(resolver):
            node_instance = node_class(**node_params)
            execution_id = str(uuid.uuid4())
            _, runtime_config, state_config, _ = _prepare_runnable_config(
                execution_id, None
            )
            state: State = {
                "messages": [],
                "results": {},
                "inputs": inputs,
                "structured_response": None,
                "config": state_config,
            }
            return await node_instance(state, runtime_config)


__all__ = [
    "configure_sensitive_logging",
    "execute_node",
    "execute_workflow",
    "execute_workflow_evaluation",
    "execute_workflow_training",
]
