"""Workflow execution helpers for the ChatKit server."""

from __future__ import annotations
import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import nullcontext
from typing import Any, cast
from uuid import UUID, uuid4
from chatkit.errors import CustomStreamError
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from pydantic import BaseModel
from orcheo.config import get_settings
from orcheo.graph.builder import build_graph
from orcheo.models import CredentialAccessContext
from orcheo.nodes.ai.tools.context import tool_progress_context
from orcheo.persistence import create_checkpointer, create_graph_store
from orcheo.runtime.credentials import CredentialResolver, credential_resolution
from orcheo.runtime.runnable_config import merge_runnable_configs
from orcheo.tracing import encode_step_namespace, split_subgraph_update
from orcheo.vault import BaseCredentialVault
from orcheo_backend.app.chatkit.message_utils import (
    build_initial_state,
    extract_reply_from_state,
)
from orcheo_backend.app.chatkit.model_selection import (
    CHATKIT_MODEL_CONFIG_KEY,
    apply_chatkit_selected_model,
)
from orcheo_backend.app.chatkit_store_postgres.attachment_service import (
    AttachmentService,
    build_attachment_scope,
    build_scoped_resolver,
    build_scoped_uploader,
)
from orcheo_backend.app.dependencies import (
    get_history_store,
)
from orcheo_backend.app.history import RunHistoryError, RunHistoryStore
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowRepository,
    WorkflowRun,
)
from orcheo_backend.app.workflow_execution import _sanitize_public_step_payload


logger = logging.getLogger(__name__)


async def _start_chatkit_history(
    *,
    history_store: RunHistoryStore,
    workflow_id: UUID,
    execution_id: str,
    runtime_thread_id: str,
    inputs: Mapping[str, Any],
    merged_config: Any,
    workspace_id: str | None = None,
) -> None:
    """Persist run metadata in execution history for ChatKit executions."""
    try:
        stored_config = _with_thread_id(
            merged_config.to_json_config(execution_id), runtime_thread_id
        )
        await history_store.start_run(
            workflow_id=str(workflow_id),
            execution_id=execution_id,
            inputs=dict(inputs),
            runnable_config=stored_config,
            tags=merged_config.tags,
            callbacks=merged_config.callbacks,
            metadata=merged_config.metadata,
            run_name=merged_config.run_name,
            workspace_id=workspace_id,
        )
    except RunHistoryError:
        logger.exception(
            "Failed to start chatkit run history for execution %s",
            execution_id,
        )


async def _append_chatkit_history_step(
    history_store: RunHistoryStore,
    execution_id: str,
    step: Mapping[str, Any],
) -> None:
    """Append a streamed node step to ChatKit execution history."""
    try:
        await history_store.append_step(execution_id, step)
    except RunHistoryError:
        logger.exception(
            "Failed to append chatkit history step for execution %s",
            execution_id,
        )


async def _mark_chatkit_history_completed(
    history_store: RunHistoryStore,
    execution_id: str,
) -> None:
    """Mark a ChatKit execution history entry as completed."""
    try:
        await history_store.append_step(execution_id, {"status": "completed"})
        await history_store.mark_completed(execution_id)
    except RunHistoryError:
        logger.exception(
            "Failed to mark chatkit history completed for execution %s",
            execution_id,
        )


async def _mark_chatkit_history_failed(
    history_store: RunHistoryStore,
    execution_id: str,
    error_message: str,
) -> None:
    """Mark a ChatKit execution history entry as failed."""
    try:
        await history_store.append_step(
            execution_id,
            {"status": "error", "error": error_message},
        )
        await history_store.mark_failed(execution_id, error_message)
    except RunHistoryError:
        logger.exception(
            "Failed to mark chatkit history failed for execution %s",
            execution_id,
        )


class WorkflowExecutor:
    """Encapsulates the workflow execution path for ChatKit requests."""

    def __init__(
        self,
        repository: WorkflowRepository,
        vault_provider: Callable[[], BaseCredentialVault],
        attachment_service: AttachmentService | None = None,
    ) -> None:
        """Store collaborators used during workflow execution."""
        self._repository = repository
        self._vault_provider = vault_provider
        self._attachment_service = attachment_service

    async def run(
        self,
        workflow_id: UUID,
        inputs: Mapping[str, Any],
        *,
        actor: str = "chatkit",
        progress_callback: Callable[[Mapping[str, Any]], Awaitable[None]] | None = None,
        workspace_id: str | None = None,
        thread_id: str | None = None,
        upload_session_id: str | None = None,
    ) -> tuple[str, Mapping[str, Any], WorkflowRun | None]:
        """Execute the workflow and return the reply, state view, and run."""
        workflow, version = await asyncio.gather(
            self._repository.get_workflow(workflow_id),
            self._repository.get_latest_version(workflow_id),
        )
        get_workflow_workspace_id = getattr(
            self._repository, "get_workflow_workspace_id", None
        )
        repository_workspace_id = (
            await get_workflow_workspace_id(workflow_id)
            if callable(get_workflow_workspace_id)
            else None
        )
        resolved_workspace_id = workspace_id
        if resolved_workspace_id is None:
            resolved_workspace_id = repository_workspace_id
        elif (
            repository_workspace_id is not None
            and repository_workspace_id != resolved_workspace_id
        ):
            raise WorkflowNotFoundError(str(workflow_id))
        normalized_inputs = dict(inputs)
        selected_model = apply_chatkit_selected_model(normalized_inputs, workflow)
        history_store = get_history_store()
        if resolved_workspace_id is None:
            run = await self._create_run_record(
                workflow_id,
                version.id,
                actor,
                normalized_inputs,
            )
        else:
            run = await self._create_run_record(
                workflow_id,
                version.id,
                actor,
                normalized_inputs,
                workspace_id=resolved_workspace_id,
            )
        execution_id = self._resolve_execution_id(run)
        runtime_thread_id = _resolve_runtime_thread_id(inputs, execution_id)
        merged_config = merge_runnable_configs(version.runnable_config, None)

        attachment_extras = self._build_attachment_config(
            workspace_id=resolved_workspace_id,
            workflow_id=str(workflow_id),
            thread_id=thread_id or runtime_thread_id,
            upload_session_id=upload_session_id,
        )

        config = cast(
            RunnableConfig,
            _with_attachment_scope(
                _with_request_inputs(
                    _with_chatkit_model(
                        _with_thread_id(
                            merged_config.to_runnable_config(execution_id),
                            runtime_thread_id,
                        ),
                        selected_model,
                    ),
                    normalized_inputs,
                ),
                attachment_extras,
            ),
        )
        state_config_input = merged_config.to_state_config(execution_id)
        state_config = _with_chatkit_model(
            _with_request_inputs(
                _with_thread_id(state_config_input, runtime_thread_id),
                normalized_inputs,
            ),
            selected_model,
        )

        await _start_chatkit_history(
            history_store=history_store,
            workflow_id=workflow_id,
            execution_id=execution_id,
            runtime_thread_id=runtime_thread_id,
            inputs=normalized_inputs,
            merged_config=merged_config,
            workspace_id=resolved_workspace_id,
        )

        try:
            step_callback = None
            if progress_callback is not None:
                step_callback = self._build_step_callback(
                    history_store=history_store,
                    execution_id=execution_id,
                    progress_callback=progress_callback,
                )
            final_state = await self._execute_graph(
                workflow_id=workflow_id,
                graph_config=version.graph,
                inputs=normalized_inputs,
                config=config,
                state_config=state_config,
                step_callback=step_callback,
                workspace_id=resolved_workspace_id,
            )
            reply, state_view = _build_reply_state(final_state)
        except Exception as exc:
            await self._record_run_failure(
                run=run,
                actor=actor,
                history_store=history_store,
                execution_id=execution_id,
                error_message=str(exc),
            )
            raise

        await _mark_chatkit_history_completed(history_store, execution_id)
        await self._mark_run_succeeded(run, actor, reply)
        return reply, state_view, run

    @staticmethod
    async def _checkpoint_message_count(compiled: Any, config: Any) -> int:
        """Return the number of messages already persisted for this thread.

        Read before the run so the post-run snapshot can be sliced down to just
        the messages produced this turn. Returns ``0`` when no checkpoint exists
        or the compiled graph cannot report state.
        """
        aget_state = getattr(compiled, "aget_state", None)
        if not callable(aget_state):
            return 0
        try:
            snapshot = await aget_state(cast(Any, config))
        except Exception:  # pragma: no cover - defensive
            logger.debug("Failed to read prior checkpoint state", exc_info=True)
            return 0
        values = getattr(snapshot, "values", snapshot)
        if isinstance(values, Mapping):
            messages = values.get("messages")
            if isinstance(messages, list):
                return len(messages)
        return 0

    @staticmethod
    def _extract_messages(final_state: Any) -> list[BaseMessage]:
        """Return LangChain messages from the workflow state when available."""
        candidates = []
        if isinstance(final_state, Mapping):
            maybe_messages = final_state.get("messages")
            if isinstance(maybe_messages, list):
                candidates = maybe_messages
        if not candidates and hasattr(final_state, "messages"):
            maybe_messages = final_state.messages  # type: ignore[attr-defined]
            if isinstance(maybe_messages, list):  # pragma: no branch
                candidates = maybe_messages

        return [
            message
            for message in candidates
            if isinstance(message, BaseMessage)  # type: ignore[arg-type]
        ]

    def _build_attachment_config(
        self,
        *,
        workspace_id: str | None,
        workflow_id: str,
        thread_id: str | None,
        upload_session_id: str | None,
    ) -> dict[str, Any]:
        """Build attachment resolver/scope extras for RunnableConfig.configurable."""
        if self._attachment_service is None or not workspace_id:
            return {}
        scope = build_attachment_scope(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            thread_id=thread_id,
            upload_session_id=upload_session_id,
        )
        resolver = build_scoped_resolver(self._attachment_service, scope)
        uploader = build_scoped_uploader(self._attachment_service, scope)
        return {
            "attachment_resolver": resolver,
            "attachment_scope": scope,
            "attachment_uploader": uploader,
        }

    @staticmethod
    def _resolve_execution_id(run: WorkflowRun | None) -> str:
        """Return a stable execution identifier for trace history records."""
        if run is not None:
            return str(run.id)
        return str(uuid4())

    async def _create_run_record(
        self,
        workflow_id: UUID,
        workflow_version_id: UUID,
        actor: str,
        inputs: Mapping[str, Any],
        *,
        workspace_id: str | None = None,
    ) -> WorkflowRun | None:
        """Create and start a repository run record when possible."""
        try:
            run = await self._repository.create_run(
                workflow_id,
                workflow_version_id=workflow_version_id,
                triggered_by=actor,
                input_payload=dict(inputs),
                workspace_id=workspace_id,
            )
            await self._repository.mark_run_started(run.id, actor=actor)
            return run
        except Exception:  # pragma: no cover - repository failure
            logger.exception("Failed to record workflow run metadata")
            return None

    def _build_step_callback(
        self,
        *,
        history_store: RunHistoryStore,
        execution_id: str,
        progress_callback: Callable[[Mapping[str, Any]], Awaitable[None]] | None,
    ) -> Callable[[Mapping[str, Any]], Awaitable[None]]:
        """Create a callback that persists history then forwards UI progress."""

        async def _handle_step(step: Mapping[str, Any]) -> None:
            sanitized_step = _sanitize_public_step_payload(step)
            await _append_chatkit_history_step(
                history_store, execution_id, sanitized_step
            )
            if progress_callback is not None:
                await progress_callback(sanitized_step)

        return _handle_step

    async def _execute_graph(
        self,
        *,
        workflow_id: UUID,
        graph_config: Mapping[str, Any],
        inputs: Mapping[str, Any],
        config: RunnableConfig,
        state_config: Mapping[str, Any],
        step_callback: Callable[[Mapping[str, Any]], Awaitable[None]] | None,
        workspace_id: str | None = None,
    ) -> Any:
        """Execute the compiled graph and return the final state payload."""
        settings = get_settings()
        vault = self._vault_provider()
        credential_context = CredentialAccessContext(
            workflow_id=workflow_id,
            workspace_id=UUID(workspace_id) if workspace_id else None,
        )
        credential_resolver = CredentialResolver(vault, context=credential_context)

        async with create_checkpointer(settings) as checkpointer:
            async with create_graph_store(settings) as graph_store:
                graph = build_graph(graph_config)
                compiled = graph.compile(
                    checkpointer=checkpointer,
                    store=graph_store,
                )
                pending_interrupts = await _pending_interrupts(compiled, config)
                if pending_interrupts:
                    payload = Command(resume=_resume_value_from_inputs(inputs))
                elif workspace_id is None:
                    payload: Any = build_initial_state(
                        graph_config,
                        inputs,
                        runtime_config=state_config,
                    )
                else:
                    payload = build_initial_state(
                        graph_config,
                        inputs,
                        runtime_config=state_config,
                        workspace_id=workspace_id,
                    )

                with credential_resolution(credential_resolver):
                    if (
                        step_callback is not None
                        and hasattr(compiled, "astream")
                        and hasattr(compiled, "aget_state")
                    ):
                        progress_context = (
                            tool_progress_context(step_callback)
                            if step_callback is not None
                            else nullcontext()
                        )
                        with progress_context:
                            prior_count = await self._checkpoint_message_count(
                                compiled, config
                            )
                            async for chunk in compiled.astream(
                                payload,
                                config=config,  # type: ignore[arg-type]
                                stream_mode="updates",
                                subgraphs=True,
                            ):
                                namespace, step = split_subgraph_update(chunk)
                                if step_callback is not None:  # pragma: no branch
                                    await step_callback(
                                        encode_step_namespace(step, namespace)
                                    )
                            state_snapshot_config = cast(Any, config)
                            snapshot = await compiled.aget_state(state_snapshot_config)
                            values = getattr(snapshot, "values", snapshot)
                            interrupts = getattr(snapshot, "interrupts", ())
                            if interrupts:
                                return _state_with_interrupts(values, interrupts)
                            return _annotate_new_messages(values, prior_count)

                    prior_count = await self._checkpoint_message_count(compiled, config)
                    result = await compiled.ainvoke(payload, config=config)
                    if isinstance(result, Mapping) and result.get("__interrupt__"):
                        snapshot = await compiled.aget_state(cast(Any, config))
                        values = getattr(snapshot, "values", result)
                        interrupts = getattr(snapshot, "interrupts", ())
                        # Fall back to the invoke result's own interrupt list rather
                        # than the whole state mapping, which would otherwise leak
                        # raw graph state into ``__interrupt__`` / the reply text.
                        return _state_with_interrupts(
                            values, interrupts or result.get("__interrupt__", ())
                        )
                    return _annotate_new_messages(result, prior_count)

    async def _mark_run_succeeded(
        self,
        run: WorkflowRun | None,
        actor: str,
        reply: str,
    ) -> None:
        """Mark the repository run as succeeded, logging failures only."""
        if run is None:
            return
        try:
            await self._repository.mark_run_succeeded(
                run.id,
                actor=actor,
                output={"reply": reply},
            )
        except Exception:  # pragma: no cover - repository failure
            logger.exception("Failed to mark workflow run succeeded")

    async def _record_run_failure(
        self,
        *,
        run: WorkflowRun | None,
        actor: str,
        history_store: RunHistoryStore,
        execution_id: str,
        error_message: str,
    ) -> None:
        """Record repository and history failure states for ChatKit execution."""
        await _mark_chatkit_history_failed(
            history_store,
            execution_id,
            error_message,
        )
        if run is None:
            return
        try:
            await self._repository.mark_run_failed(
                run.id,
                actor=actor,
                error=error_message,
            )
        except Exception:  # pragma: no cover - repository failure
            logger.exception("Failed to mark workflow run failed")


__all__ = ["WorkflowExecutor"]

# Private key used to carry the current turn's messages alongside the full
# accumulated state returned by ``_execute_graph``.
_NEW_MESSAGES_KEY = "_new_messages"


def _annotate_new_messages(state: Any, prior_count: int) -> Any:
    """Attach the current turn's new messages to the returned state.

    The graph's ``messages`` channel accumulates across turns via the
    checkpointer, so slice off everything that existed before this run. When the
    history unexpectedly shrank (``prior_count`` out of range), fall back to the
    full list rather than dropping messages.
    """
    if not isinstance(state, dict):
        return state
    messages = state.get("messages")
    if not isinstance(messages, list):
        return state
    if 0 <= prior_count <= len(messages):
        state[_NEW_MESSAGES_KEY] = messages[prior_count:]
    else:
        state[_NEW_MESSAGES_KEY] = messages
    return state


def _new_messages_from_state(final_state: Any) -> list[BaseMessage] | None:
    """Return the current-turn messages annotated by ``_execute_graph``."""
    if not isinstance(final_state, Mapping):
        return None
    annotated = final_state.get(_NEW_MESSAGES_KEY)
    if not isinstance(annotated, list):
        return None
    return [message for message in annotated if isinstance(message, BaseMessage)]


async def _pending_interrupts(compiled: Any, config: RunnableConfig) -> tuple[Any, ...]:
    """Return interrupts currently waiting on the graph checkpoint, if any."""
    aget_state = getattr(compiled, "aget_state", None)
    if not callable(aget_state):
        return ()
    try:
        snapshot = await aget_state(cast(Any, config))
    except Exception:  # pragma: no cover - defensive
        logger.debug("Failed to inspect checkpoint interrupts", exc_info=True)
        return ()
    interrupts = getattr(snapshot, "interrupts", ())
    if isinstance(interrupts, tuple):
        return interrupts
    if isinstance(interrupts, list):
        return tuple(interrupts)
    return ()


def _resume_value_from_inputs(inputs: Mapping[str, Any]) -> Any:
    """Return the ChatKit human response payload used to resume an interrupt."""
    message = inputs.get("message")
    if isinstance(message, str):
        return message
    if message is not None:
        return message
    action = inputs.get("action")
    if isinstance(action, Mapping):
        payload = action.get("payload")
        if payload is not None:
            return payload
    return dict(inputs)


def _interrupt_value(interrupt: Any) -> Any:
    """Extract the JSON-serializable payload from a LangGraph interrupt."""
    if isinstance(interrupt, Mapping):
        return interrupt.get("value", interrupt)
    return getattr(interrupt, "value", interrupt)


def _interrupt_message(value: Any) -> str:
    """Return the assistant text to show for an interrupt payload."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("message", "question", "prompt", "instruction"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
    return str(value)


def _state_with_interrupts(state: Any, interrupts: Any) -> dict[str, Any]:
    """Attach interrupt metadata and a user-facing reply to a state snapshot."""
    if isinstance(state, Mapping):
        state_view = dict(state)
    else:
        state_view = {}
    interrupt_items = list(interrupts) if isinstance(interrupts, list | tuple) else []
    interrupt_payloads = [_interrupt_value(item) for item in interrupt_items]
    if not interrupt_payloads:
        interrupt_payloads = [_interrupt_value(interrupts)]
    state_view["__interrupt__"] = interrupt_payloads
    state_view["assistant_message"] = _interrupt_message(interrupt_payloads[0])
    return state_view


def _build_reply_state(final_state: Any) -> tuple[str, Mapping[str, Any]]:
    """Extract reply text and normalized state view from final graph state."""
    raw_messages = WorkflowExecutor._extract_messages(final_state)
    new_messages = _new_messages_from_state(final_state)

    if isinstance(final_state, BaseModel):
        state_view: Mapping[str, Any] = final_state.model_dump()
    elif isinstance(final_state, Mapping):
        state_view = dict(final_state)
    else:  # pragma: no cover - defensive
        state_view = dict(final_state or {})

    state_view = dict(state_view)
    # Scope the messages exposed to downstream consumers (e.g. widget hydration)
    # to those produced during the current turn. The checkpointer accumulates
    # the full thread history, so the unscoped list would re-surface every
    # widget emitted on earlier turns. Reply extraction below still sees the
    # full ``messages`` list under its own key.
    state_view.pop(_NEW_MESSAGES_KEY, None)
    scoped_messages = new_messages if new_messages is not None else raw_messages
    if scoped_messages:
        state_view["_messages"] = scoped_messages

    reply = extract_reply_from_state(state_view)
    if reply is None:
        raise CustomStreamError(
            "Workflow completed without producing a reply.",
            allow_retry=False,
        )
    return reply, state_view


def _with_thread_id(config: Mapping[str, Any], thread_id: str) -> dict[str, Any]:
    """Return a config mapping with ``configurable.thread_id`` set."""
    normalized = dict(config)
    configurable = normalized.get("configurable")
    if isinstance(configurable, Mapping):
        configurable_payload = dict(configurable)
    else:
        configurable_payload = {}
    configurable_payload["thread_id"] = thread_id
    normalized["configurable"] = configurable_payload
    return normalized


def _resolve_runtime_thread_id(inputs: Mapping[str, Any], execution_id: str) -> str:
    """Resolve the LangGraph thread identifier for ChatKit executions."""
    for key in ("thread_id", "session_id"):
        candidate = inputs.get(key)
        if isinstance(candidate, str):
            normalized = candidate.strip()
            if normalized:
                return normalized
    return execution_id


def _with_chatkit_model(
    config: Mapping[str, Any],
    selected_model: str | None,
) -> dict[str, Any]:
    """Return a config mapping with the ChatKit-selected model when present."""
    normalized = dict(config)
    configurable = normalized.get("configurable")
    if isinstance(configurable, Mapping):
        configurable_payload = dict(configurable)
    else:
        configurable_payload = {}
    if selected_model:
        configurable_payload[CHATKIT_MODEL_CONFIG_KEY] = selected_model
    else:
        configurable_payload.pop(CHATKIT_MODEL_CONFIG_KEY, None)
    normalized["configurable"] = configurable_payload
    return normalized


def _with_attachment_scope(
    config: Mapping[str, Any],
    attachment_extras: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a config mapping with attachment resolver and scope injected."""
    if not attachment_extras:
        return dict(config)
    normalized = dict(config)
    configurable = normalized.get("configurable")
    if isinstance(configurable, Mapping):
        configurable_payload = dict(configurable)
    else:
        configurable_payload = {}
    configurable_payload.update(attachment_extras)
    normalized["configurable"] = configurable_payload
    return normalized


def _with_request_inputs(
    config: Mapping[str, Any],
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a config mapping with the request inputs mirrored into config."""
    normalized = dict(config)
    configurable = normalized.get("configurable")
    if isinstance(configurable, Mapping):
        configurable_payload = dict(configurable)
    else:
        configurable_payload = {}
    configurable_payload.setdefault("inputs", dict(inputs))
    normalized["configurable"] = configurable_payload
    return normalized
