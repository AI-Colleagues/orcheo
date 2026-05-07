"""ChatKit server implementation streaming Orcheo workflow results."""
# ruff: noqa: I001

from __future__ import annotations
import asyncio
import json
import logging
import warnings
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=".*named widget classes is deprecated.*",
        category=DeprecationWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message=".*named action classes is deprecated.*",
        category=DeprecationWarning,
    )
    from chatkit.errors import CustomStreamError
    from chatkit.server import ChatKitServer
    from chatkit.store import Store
    from chatkit.types import (
        Action,
        AssistantMessageItem,
        NoticeEvent,
        ProgressUpdateEvent,
        ThreadItemDoneEvent,
        ThreadItemUpdatedEvent,
        ThreadMetadata,
        ThreadStreamEvent,
        UserMessageItem,
        WidgetItem,
        WidgetRoot,
    )
    from chatkit.types import WidgetRootUpdated
    from chatkit.widgets import DynamicWidgetRoot
from dynaconf import Dynaconf
from langchain_core.messages import ToolMessage
from pydantic import TypeAdapter, ValidationError
from orcheo.config import get_settings
from orcheo.vault import BaseCredentialVault
from orcheo_backend.app.chatkit.context import ChatKitRequestContext
from orcheo_backend.app.chatkit.message_utils import (
    build_action_inputs_payload,
    collect_text_from_user_content,
)
from orcheo_backend.app.chatkit.model_selection import resolve_chatkit_selected_model
from orcheo_backend.app.chatkit.messages import (
    build_assistant_item,
    build_history,
    build_inputs_payload,
    record_run_metadata,
    require_workflow_id,
    resolve_user_item,
    sync_thread_inference_metadata,
)
from orcheo_backend.app.chatkit.workflow_executor import WorkflowExecutor
from orcheo_backend.app.chatkit.telemetry import chatkit_telemetry
from orcheo_backend.app.chatkit_store_postgres import PostgresChatKitStore
from orcheo_backend.app.chatkit_store_sqlite import SqliteChatKitStore
from orcheo_backend.app.repository import (
    Workflow,
    WorkflowNotFoundError,
    WorkflowRepository,
    WorkflowRun,
    WorkflowVersionNotFoundError,
)


logger = logging.getLogger(__name__)

_WIDGET_ROOT_ADAPTER: TypeAdapter[DynamicWidgetRoot] = TypeAdapter(DynamicWidgetRoot)
_MAX_WIDGET_PAYLOAD_BYTES = 50_000
_DEFAULT_WIDGET_TYPES = {"Card", "ListView"}
_DEFAULT_WIDGET_ACTION_TYPES = {"submit"}
_WIDGET_TYPES: set[str] = set(_DEFAULT_WIDGET_TYPES)
_ALLOWED_WIDGET_ACTION_TYPES: set[str] = set(_DEFAULT_WIDGET_ACTION_TYPES)


def _coerce_config_set(value: object, default: set[str]) -> set[str]:
    """Normalize configuration values into a non-empty set of strings."""
    if value is None:
        return set(default)
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
        return set(parts) or set(default)
    iterable: Iterable[Any]
    if isinstance(value, set | frozenset):
        iterable = value
    elif isinstance(value, list | tuple):
        iterable = value
    else:
        return set(default)
    coerced = {str(entry).strip() for entry in iterable if str(entry).strip()}
    return coerced or set(default)


def _refresh_widget_policy(settings: Any | None = None) -> None:
    """Update allowed widget and action types from configuration."""
    config = settings or get_settings()
    widget_types_raw: Any | None
    action_types_raw: Any | None

    if hasattr(config, "get"):
        widget_types_raw = config.get("CHATKIT_WIDGET_TYPES")
        action_types_raw = config.get("CHATKIT_WIDGET_ACTION_TYPES")
    elif isinstance(config, Mapping):
        widget_types_raw = (
            config.get("CHATKIT_WIDGET_TYPES")
            or config.get("chatkit_widget_types")
            or None
        )
        action_types_raw = (
            config.get("CHATKIT_WIDGET_ACTION_TYPES")
            or config.get("chatkit_widget_action_types")
            or None
        )
    else:
        widget_types_raw = getattr(config, "chatkit_widget_types", None) or getattr(
            config, "CHATKIT_WIDGET_TYPES", None
        )
        action_types_raw = getattr(
            config, "chatkit_widget_action_types", None
        ) or getattr(config, "CHATKIT_WIDGET_ACTION_TYPES", None)

    widget_types = _coerce_config_set(widget_types_raw, _DEFAULT_WIDGET_TYPES)
    allowed_action_types = _coerce_config_set(
        action_types_raw, _DEFAULT_WIDGET_ACTION_TYPES
    )
    _WIDGET_TYPES.clear()
    _WIDGET_TYPES.update(widget_types)
    _ALLOWED_WIDGET_ACTION_TYPES.clear()
    _ALLOWED_WIDGET_ACTION_TYPES.update(allowed_action_types)


_refresh_widget_policy()


class _WidgetCandidate(NamedTuple):
    """Intermediate representation of a widget payload."""

    payload: Any
    copy_text: str | None = None


class _WidgetHydrationError(Exception):
    """Raised when widget payloads fail validation or policy checks."""

    def __init__(
        self,
        reason: str,
        detail: str | None = None,
        *,
        size_bytes: int | None = None,
    ) -> None:
        self.reason = reason
        self.detail = detail
        self.size_bytes = size_bytes
        super().__init__(reason)


class _ActionValidationResult(NamedTuple):
    """Represents the outcome of validating a widget action."""

    allowed: bool
    notice: NoticeEvent | None = None
    reason: str | None = None
    action_type: str | None = None


def _messages_from_state(state_view: Mapping[str, Any]) -> list[Any]:
    """Return LangChain messages embedded in the workflow state."""
    messages = state_view.get("_messages") or state_view.get("messages") or []
    return messages if isinstance(messages, list) else []


def _is_tool_message(message: Any) -> bool:
    """Return True when ``message`` represents a ToolMessage."""
    if isinstance(message, ToolMessage):
        return True
    return isinstance(message, Mapping) and message.get("type") == "tool"


def _workflow_id_from_thread(thread: ThreadMetadata) -> str | None:
    """Best-effort extraction of the workflow id from thread metadata."""
    metadata = thread.metadata or {}
    workflow_id = metadata.get("workflow_id")
    if workflow_id is None:
        return None
    return str(workflow_id)


def _action_type_for_logging(
    action: Action[str, Any] | Mapping[str, Any] | object,
) -> str:
    """Extract the action type for logging contexts."""
    if isinstance(action, Mapping):
        action_type = action.get("type")
    else:
        action_type = getattr(action, "type", None)
    return str(action_type) if action_type is not None else ""


def _candidate_type(payload: Any) -> str | None:
    """Return the candidate widget type when present."""
    if isinstance(payload, Mapping):
        type_value = payload.get("type")
    else:
        type_value = getattr(payload, "type", None)
    return str(type_value) if type_value is not None else None


def _content_text(content: Any) -> str | None:
    """Extract text content from ToolMessage payloads."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # pragma: no branch
        for entry in content:
            if isinstance(entry, Mapping):
                text_value = entry.get("text")
                if isinstance(text_value, str):
                    return text_value
            text_attr = getattr(entry, "text", None)
            if isinstance(text_attr, str):
                return text_attr
    return None


def _format_node_event_update(node: str, event: str, payload: Any) -> str:
    """Format node event payloads into a progress update string."""
    normalized_event = event.strip().lower()
    if normalized_event == "on_chain_start":
        return f"-> {node} starting..."
    if normalized_event == "on_chain_end":
        return f"- {node} completed"
    if normalized_event == "on_chain_error":
        error_message = None
        if isinstance(payload, Mapping):
            error_message = payload.get("error")
        if error_message:
            return f"! {node} error: {error_message}"
        return f"! {node} error"
    if normalized_event == "node_status":
        return _format_node_status_update(node, payload)
    return f"[{event}] {node}"


def _format_node_status_update(node: str, payload: Any) -> str:
    """Render an in-node ``emit_node_status`` payload as progress text."""
    if isinstance(payload, Mapping):
        for key in ("message", "text", "status"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return f"{node}: {value.strip()}"
    if isinstance(payload, str) and payload.strip():
        return f"{node}: {payload.strip()}"
    return f"{node}: status update"


def _progress_texts_for_step(step: Mapping[str, Any]) -> list[str]:
    """Generate progress text updates for a workflow step payload."""
    if not step:
        return []

    node_value = step.get("node")
    event_value = step.get("event")
    if node_value and event_value:
        payload = step.get("payload") or step.get("data")
        return [
            _format_node_event_update(str(node_value), str(event_value), payload),
        ]

    texts: list[str] = []
    for node_key, _ in step.items():
        if node_key is None:
            continue
        node_name = str(node_key).strip()
        if not node_name:
            continue
        texts.append(f"Running {node_name}")
    return texts


async def _enqueue_progress_updates(
    progress_queue: asyncio.Queue[ThreadStreamEvent | None],
    step: Mapping[str, Any],
) -> None:
    """Queue progress events for the provided step payload."""
    for text in _progress_texts_for_step(step):
        await progress_queue.put(ProgressUpdateEvent(text=text))


def _candidate_from_artifact(
    artifact: Mapping[str, Any] | None,
) -> _WidgetCandidate | None:
    """Return a candidate when structured content is embedded in the artifact."""
    if not isinstance(artifact, Mapping):
        return None
    payload = artifact.get("structured_content")
    raw_copy_text = artifact.get("copy_text")
    copy_text = raw_copy_text if isinstance(raw_copy_text, str) else None
    if payload is None:
        return None  # pragma: no cover - defensive programming
    return _WidgetCandidate(payload=payload, copy_text=copy_text)


def _candidate_from_content(
    content: Any, copy_text: str | None
) -> _WidgetCandidate | None:
    """Attempt to parse widget payloads from ToolMessage content."""
    text_value = _content_text(content)
    if not text_value:
        return None  # pragma: no cover - defensive programming
    stripped = text_value.strip()
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:  # pragma: no cover - defensive programming
        return None

    candidate_type = _candidate_type(payload)
    if candidate_type not in _WIDGET_TYPES:
        return None

    return _WidgetCandidate(payload=payload, copy_text=copy_text)


def _extract_widget_candidate(message: Any) -> _WidgetCandidate | None:
    """Return a widget candidate when the ToolMessage contains widget payloads."""
    artifact = getattr(message, "artifact", None)
    content = getattr(message, "content", None)
    if isinstance(message, Mapping):
        artifact = message.get("artifact")
        content = message.get("content")

    artifact_candidate = _candidate_from_artifact(artifact)
    if artifact_candidate:
        # Structured content without a type is treated as a candidate so validation
        # can surface a notice to the user.
        candidate_type = _candidate_type(artifact_candidate.payload)
        if (
            candidate_type in _WIDGET_TYPES or candidate_type is None
        ):  # pragma: no branch
            return artifact_candidate

    return _candidate_from_content(
        content, getattr(artifact_candidate, "copy_text", None)
    )


def _validate_widget_root(payload: Any) -> WidgetRoot:
    """Validate and size-check a widget payload."""
    try:
        widget_root = _WIDGET_ROOT_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise _WidgetHydrationError("invalid_widget", detail=str(exc)) from exc

    serialized = widget_root.model_dump_json(exclude_none=True)
    size_bytes = len(serialized.encode("utf-8"))
    if size_bytes > _MAX_WIDGET_PAYLOAD_BYTES:
        raise _WidgetHydrationError(
            "too_large",
            detail="Widget payload exceeds maximum size",
            size_bytes=size_bytes,
        )

    return widget_root


def _notice_for_widget_error(error: _WidgetHydrationError) -> NoticeEvent:
    """Build a user-facing notice describing widget hydration issues."""
    if error.reason == "too_large":
        limit_kb = int(_MAX_WIDGET_PAYLOAD_BYTES / 1024)
        message = (
            f"The workflow returned a widget that is too large to display "
            f"(limit is roughly {limit_kb} KB)."
        )
        title = "Widget too large"
    else:
        message = "The workflow returned a widget that could not be rendered."
        title = "Widget unavailable"
    return NoticeEvent(level="danger", message=message, title=title)


class OrcheoChatKitServer(ChatKitServer[ChatKitRequestContext]):
    """ChatKit server streaming Orcheo workflow outputs back to the widget."""

    def __init__(
        self,
        store: Store[ChatKitRequestContext],
        repository: WorkflowRepository,
        vault_provider: Callable[[], BaseCredentialVault],
    ) -> None:
        """Initialise the ChatKit server with the configured repository."""
        super().__init__(store=store)
        self._repository = repository
        self._vault_provider = vault_provider
        self._workflow_executor = WorkflowExecutor(
            repository=repository, vault_provider=vault_provider
        )

    async def _history(
        self, thread: ThreadMetadata, context: ChatKitRequestContext
    ) -> list[dict[str, str]]:
        """Delegate to the shared history helper."""
        return await build_history(self.store, thread, context)

    @staticmethod
    def _require_workflow_id(thread: ThreadMetadata) -> UUID:
        """Delegate to the workflow id helper."""
        return require_workflow_id(thread)

    @staticmethod
    def _ensure_workflow_metadata(
        thread: ThreadMetadata, context: ChatKitRequestContext
    ) -> None:
        """Merge the latest request metadata onto the thread before execution."""
        metadata = dict(thread.metadata or {})
        request = context.get("chatkit_request") if context else None
        request_metadata = getattr(request, "metadata", None)
        if isinstance(request_metadata, Mapping) and request_metadata:
            metadata.update(request_metadata)
        context_workflow_id = context.get("workflow_id") if context else None
        if context_workflow_id:
            metadata["workflow_id"] = context_workflow_id
        thread.metadata = metadata

    async def _resolve_user_item(
        self,
        thread: ThreadMetadata,
        item: UserMessageItem | None,
        context: ChatKitRequestContext,
    ) -> UserMessageItem:
        """Delegate to the user item helper."""
        return await resolve_user_item(self.store, thread, item, context)

    def _build_inputs_payload(
        self,
        workflow: Workflow,
        thread: ThreadMetadata,
        message_text: str,
        history: list[dict[str, str]],
        user_item: UserMessageItem | None = None,
    ) -> dict[str, Any]:
        """Delegate to the payload helper."""
        selected_model = resolve_chatkit_selected_model(
            workflow,
            getattr(getattr(user_item, "inference_options", None), "model", None),
        )
        return build_inputs_payload(
            thread,
            message_text,
            history,
            user_item,
            selected_model=selected_model,
        )

    @staticmethod
    def _record_run_metadata(thread: ThreadMetadata, run: WorkflowRun | None) -> None:
        """Delegate to the metadata helper."""
        record_run_metadata(thread, run)

    @staticmethod
    async def _drain_progress_queue(
        progress_queue: asyncio.Queue[ThreadStreamEvent | None],
    ) -> AsyncIterator[ThreadStreamEvent]:
        """Yield progress events until the queue signals completion."""
        while True:
            event = await progress_queue.get()
            if event is None:
                break
            yield event

    def _build_assistant_item(
        self,
        thread: ThreadMetadata,
        reply: str,
        context: ChatKitRequestContext,
    ) -> AssistantMessageItem:
        """Delegate to the assistant item helper."""
        return build_assistant_item(self.store, thread, reply, context)

    async def _hydrate_widget_items(
        self,
        thread: ThreadMetadata,
        state_view: Mapping[str, Any],
        context: ChatKitRequestContext,
    ) -> tuple[list[WidgetItem], list[NoticeEvent]]:
        """Hydrate widget thread items from LangChain ToolMessages."""
        candidates: list[_WidgetCandidate] = []
        for message in _messages_from_state(state_view):
            if not _is_tool_message(message):
                continue
            candidate = _extract_widget_candidate(message)
            if candidate is not None:  # pragma: no branch
                candidates.append(candidate)

        if not candidates:
            return [], []

        async def _validate_candidate(
            candidate: _WidgetCandidate,
        ) -> tuple[_WidgetCandidate, WidgetRoot | None, _WidgetHydrationError | None]:
            try:
                widget_root = await asyncio.to_thread(
                    _validate_widget_root, candidate.payload
                )
            except _WidgetHydrationError as error:
                return candidate, None, error
            return candidate, widget_root, None

        results = await asyncio.gather(
            *(_validate_candidate(candidate) for candidate in candidates)
        )

        widget_items: list[WidgetItem] = []
        notices: list[NoticeEvent] = []
        for candidate, widget_root, error in results:
            if error:
                workflow_id = _workflow_id_from_thread(thread)
                logger.warning(
                    "Skipping widget payload on thread %s workflow %s: %s",
                    thread.id,
                    workflow_id or "unknown",
                    error.detail or error.reason,
                    extra={
                        "thread_id": str(thread.id),
                        "workflow_id": workflow_id,
                        "widget_error": error.reason,
                        "widget_error_detail": error.detail,
                        "widget_payload_size": error.size_bytes,
                    },
                )
                chatkit_telemetry.increment(f"widget.validation_error.{error.reason}")
                notices.append(_notice_for_widget_error(error))
                continue

            if widget_root is None:  # pragma: no cover - defensive
                continue
            widget_items.append(
                WidgetItem(
                    id=self.store.generate_item_id("message", thread, context),
                    thread_id=thread.id,
                    created_at=datetime.now(UTC),
                    widget=widget_root,
                    copy_text=candidate.copy_text,
                )
            )
            chatkit_telemetry.increment("widget.hydrated")

        return widget_items, notices

    async def _run_workflow(
        self,
        workflow_id: UUID,
        inputs: Mapping[str, Any],
        *,
        actor: str = "chatkit",
        progress_callback: Callable[[Mapping[str, Any]], Awaitable[None]] | None = None,
        workspace_id: str | None = None,
    ) -> tuple[str, Mapping[str, Any], WorkflowRun | None]:
        """Delegate execution to the workflow executor."""
        return await self._workflow_executor.run(
            workflow_id,
            inputs,
            actor=actor,
            progress_callback=progress_callback,
            workspace_id=workspace_id,
        )

    async def respond(
        self,
        thread: ThreadMetadata,
        item: UserMessageItem | None,
        context: ChatKitRequestContext,
    ) -> AsyncIterator[ThreadStreamEvent]:
        """Execute the workflow and yield assistant events."""
        self._ensure_workflow_metadata(thread, context)
        workflow_id = self._require_workflow_id(thread)
        try:
            workflow = await self._repository.get_workflow(workflow_id)
        except WorkflowNotFoundError as exc:
            raise CustomStreamError(str(exc), allow_retry=False) from exc
        user_item = await self._resolve_user_item(thread, item, context)
        selected_model = resolve_chatkit_selected_model(
            workflow,
            getattr(getattr(user_item, "inference_options", None), "model", None),
        )
        sync_thread_inference_metadata(thread, user_item, selected_model=selected_model)
        message_text = collect_text_from_user_content(user_item.content)
        history = await self._history(thread, context)
        inputs = self._build_inputs_payload(
            workflow,
            thread,
            message_text,
            history,
            user_item,
        )

        actor = str(context.get("actor") or "chatkit")
        progress_queue: asyncio.Queue[ThreadStreamEvent | None] = asyncio.Queue()

        async def on_progress(step: Mapping[str, Any]) -> None:
            await _enqueue_progress_updates(progress_queue, step)

        try:
            yield ProgressUpdateEvent(text="Agent is working...")
            workspace_id = context.get("workspace_id")
            run_task = asyncio.create_task(
                self._run_workflow(
                    workflow_id,
                    inputs,
                    actor=actor,
                    progress_callback=on_progress,
                    workspace_id=workspace_id,
                )
            )
            run_task.add_done_callback(lambda _: progress_queue.put_nowait(None))

            async for event in self._drain_progress_queue(progress_queue):
                yield event

            reply, state_view, run = await run_task
        except WorkflowNotFoundError as exc:
            raise CustomStreamError(str(exc), allow_retry=False) from exc
        except WorkflowVersionNotFoundError as exc:
            raise CustomStreamError(str(exc), allow_retry=False) from exc

        widget_items, widget_notices = await self._hydrate_widget_items(
            thread, state_view, context
        )
        self._record_run_metadata(thread, run)
        for notice in widget_notices:
            yield notice
        for widget_item in widget_items:
            await self.store.add_thread_item(thread.id, widget_item, context)
            yield ThreadItemDoneEvent(item=widget_item)

        assistant_item = self._build_assistant_item(thread, reply, context)
        await self.store.add_thread_item(thread.id, assistant_item, context)
        await self.store.save_thread(thread, context)
        yield ThreadItemDoneEvent(item=assistant_item)

    def _log_action_failure(
        self,
        thread: ThreadMetadata,
        action: Action[str, Any] | Mapping[str, Any],
        exc: Exception,
    ) -> None:
        """Emit structured logging for widget action errors."""
        workflow_id = _workflow_id_from_thread(thread)
        logger.exception(
            "Widget action failed on thread %s workflow %s",
            thread.id,
            workflow_id or "unknown",
            exc_info=exc,
            extra={
                "thread_id": str(thread.id),
                "workflow_id": workflow_id,
                "widget_action_type": _action_type_for_logging(action),
            },
        )

    def _is_supported_action_type(
        self,
        thread: ThreadMetadata,
        action: Action[str, Any] | Mapping[str, Any],
    ) -> _ActionValidationResult:
        """Return action validation metadata, including user-facing notices."""
        action_type = _action_type_for_logging(action)
        if action_type in _ALLOWED_WIDGET_ACTION_TYPES:
            return _ActionValidationResult(
                allowed=True,
                notice=None,
                reason=None,
                action_type=action_type,
            )

        workflow_id = _workflow_id_from_thread(thread)
        allowed_action_types = sorted(_ALLOWED_WIDGET_ACTION_TYPES)
        chatkit_telemetry.increment(
            f"widget_action.unsupported.{action_type or 'unknown'}"
        )
        notice = NoticeEvent(
            level="warning",
            title="Unsupported widget action",
            message=(
                "This widget action is not supported. "
                f"Allowed action types: {', '.join(allowed_action_types) or 'none'}."
            ),
        )
        logger.warning(
            "Ignoring widget action on thread %s workflow %s with unsupported type %s",
            thread.id,
            workflow_id or "unknown",
            action_type or "unknown",
            extra={
                "thread_id": str(thread.id),
                "workflow_id": workflow_id,
                "widget_action_type": action_type,
                "allowed_widget_action_types": allowed_action_types,
                "error_code": "unsupported_widget_action",
            },
        )
        return _ActionValidationResult(
            allowed=False,
            notice=notice,
            reason="unsupported_widget_action",
            action_type=action_type or None,
        )

    async def action(
        self,
        thread: ThreadMetadata,
        action: Action[str, Any] | Mapping[str, Any],
        sender: WidgetItem | None,
        context: ChatKitRequestContext,
    ) -> AsyncIterator[ThreadStreamEvent]:
        """Handle widget actions by re-invoking the workflow."""
        self._ensure_workflow_metadata(thread, context)
        workflow_id = self._require_workflow_id(thread)
        validation = self._is_supported_action_type(thread, action)
        if not validation.allowed:
            return
        history = await self._history(thread, context)
        inputs = build_action_inputs_payload(thread, action, history, sender)

        actor = str(context.get("actor") or "chatkit")
        progress_queue: asyncio.Queue[ThreadStreamEvent | None] = asyncio.Queue()

        async def on_progress(step: Mapping[str, Any]) -> None:
            await _enqueue_progress_updates(progress_queue, step)

        try:
            run_task = asyncio.create_task(
                self._run_workflow(
                    workflow_id,
                    inputs,
                    actor=actor,
                    progress_callback=on_progress,
                )
            )
            run_task.add_done_callback(lambda _: progress_queue.put_nowait(None))

            async for event in self._drain_progress_queue(progress_queue):
                yield event

            reply, state_view, run = await run_task
        except WorkflowNotFoundError as exc:
            self._log_action_failure(thread, action, exc)
            raise CustomStreamError(str(exc), allow_retry=False) from exc
        except WorkflowVersionNotFoundError as exc:
            self._log_action_failure(thread, action, exc)
            raise CustomStreamError(str(exc), allow_retry=False) from exc
        except Exception as exc:
            self._log_action_failure(thread, action, exc)
            raise

        widget_items, widget_notices = await self._hydrate_widget_items(
            thread, state_view, context
        )
        self._record_run_metadata(thread, run)
        for notice in widget_notices:
            yield notice
        if sender and widget_items:
            updated_widget = widget_items.pop(0)
            updated_item = WidgetItem(
                id=sender.id,
                thread_id=sender.thread_id,
                created_at=sender.created_at,
                widget=updated_widget.widget,
                copy_text=updated_widget.copy_text,
            )
            await self.store.save_item(thread.id, updated_item, context)
            yield ThreadItemUpdatedEvent(
                item_id=sender.id,
                update=WidgetRootUpdated(widget=updated_widget.widget),
            )
        for widget_item in widget_items:
            await self.store.add_thread_item(thread.id, widget_item, context)
            yield ThreadItemDoneEvent(item=widget_item)

        assistant_item = self._build_assistant_item(thread, reply, context)
        await self.store.add_thread_item(thread.id, assistant_item, context)
        await self.store.save_thread(thread, context)
        yield ThreadItemDoneEvent(item=assistant_item)


def _resolve_chatkit_sqlite_path(settings: Any) -> Path:
    """Return the configured ChatKit SQLite path with a consistent strategy."""
    default_path = Path("~/.orcheo/chatkit.sqlite")
    candidate: Any | None = None

    if isinstance(settings, Dynaconf):
        candidate = settings.get("CHATKIT_SQLITE_PATH")
    elif isinstance(settings, Mapping):
        candidate = settings.get("CHATKIT_SQLITE_PATH")
    else:
        candidate = getattr(settings, "chatkit_sqlite_path", None)
        if candidate is None:
            candidate = getattr(settings, "CHATKIT_SQLITE_PATH", None)

    if not candidate:
        return default_path.expanduser()

    return Path(str(candidate)).expanduser()


def _resolve_chatkit_backend(settings: Any) -> str:
    """Return the configured ChatKit persistence backend."""
    candidate: Any | None = None

    if isinstance(settings, Dynaconf):
        candidate = settings.get("CHATKIT_BACKEND")
    elif isinstance(settings, Mapping):
        candidate = settings.get("CHATKIT_BACKEND") or settings.get("chatkit_backend")
    else:
        candidate = getattr(settings, "chatkit_backend", None)
        if candidate is None:
            candidate = getattr(settings, "CHATKIT_BACKEND", None)

    backend = str(candidate or "sqlite").lower()
    if backend not in {"sqlite", "postgres"}:
        msg = "CHATKIT_BACKEND must be either 'sqlite' or 'postgres'."
        raise ValueError(msg)
    return backend


def _resolve_chatkit_postgres_dsn(settings: Any) -> str:
    """Return the PostgreSQL DSN for ChatKit persistence."""
    candidate: Any | None = None

    if isinstance(settings, Dynaconf):
        candidate = settings.get("POSTGRES_DSN")
    elif isinstance(settings, Mapping):
        candidate = settings.get("POSTGRES_DSN") or settings.get("postgres_dsn")
    else:
        candidate = getattr(settings, "postgres_dsn", None)
        if candidate is None:
            candidate = getattr(settings, "POSTGRES_DSN", None)

    if not candidate:
        msg = "ORCHEO_POSTGRES_DSN must be set when using the postgres backend."
        raise ValueError(msg)
    return str(candidate)


def _resolve_chatkit_pool_settings(settings: Any) -> tuple[int, int, float, float]:
    """Return pool settings for ChatKit's PostgreSQL store."""
    defaults = (1, 10, 30.0, 300.0)
    if isinstance(settings, Dynaconf):
        return (
            settings.get("POSTGRES_POOL_MIN_SIZE", defaults[0]),
            settings.get("POSTGRES_POOL_MAX_SIZE", defaults[1]),
            settings.get("POSTGRES_POOL_TIMEOUT", defaults[2]),
            settings.get("POSTGRES_POOL_MAX_IDLE", defaults[3]),
        )
    if isinstance(settings, Mapping):
        return (
            settings.get("POSTGRES_POOL_MIN_SIZE", defaults[0]),
            settings.get("POSTGRES_POOL_MAX_SIZE", defaults[1]),
            settings.get("POSTGRES_POOL_TIMEOUT", defaults[2]),
            settings.get("POSTGRES_POOL_MAX_IDLE", defaults[3]),
        )
    return (
        getattr(settings, "postgres_pool_min_size", defaults[0]),
        getattr(settings, "postgres_pool_max_size", defaults[1]),
        getattr(settings, "postgres_pool_timeout", defaults[2]),
        getattr(settings, "postgres_pool_max_idle", defaults[3]),
    )


def create_chatkit_server(
    repository: WorkflowRepository,
    vault_provider: Callable[[], BaseCredentialVault],
    *,
    store: Store[ChatKitRequestContext] | None = None,
) -> OrcheoChatKitServer:
    """Factory returning an Orcheo-configured ChatKit server."""
    settings = get_settings()
    _refresh_widget_policy(settings)
    if store is None:
        backend = _resolve_chatkit_backend(settings)
        if backend == "postgres":
            dsn = _resolve_chatkit_postgres_dsn(settings)
            pool_min_size, pool_max_size, pool_timeout, pool_max_idle = (
                _resolve_chatkit_pool_settings(settings)
            )
            store = PostgresChatKitStore(
                dsn,
                pool_min_size=int(pool_min_size),
                pool_max_size=int(pool_max_size),
                pool_timeout=float(pool_timeout),
                pool_max_idle=float(pool_max_idle),
            )
        else:
            sqlite_path = _resolve_chatkit_sqlite_path(settings)
            store = SqliteChatKitStore(sqlite_path)
    return OrcheoChatKitServer(
        store=store,
        repository=repository,
        vault_provider=vault_provider,
    )


__all__ = ["OrcheoChatKitServer", "create_chatkit_server"]
