"""ChatKit server implementation streaming Orcheo workflow results."""
# ruff: noqa: I001

from __future__ import annotations
import asyncio
import json
import logging
import warnings
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime
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


class _WidgetCandidate(NamedTuple):
    """Intermediate representation of a widget payload."""

    payload: Any
    copy_text: str | None = None
    tool_call_id: str | None = None


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


_HYDRATED_WIDGET_TOOL_CALLS_KEY = "hydrated_widget_tool_calls"
_MAX_TRACKED_WIDGET_TOOL_CALLS = 500


def _hydrated_widget_tool_calls(thread: ThreadMetadata) -> set[str]:
    """Return the set of widget tool-call ids already hydrated on the thread."""
    metadata = thread.metadata or {}
    raw = metadata.get(_HYDRATED_WIDGET_TOOL_CALLS_KEY)
    if not isinstance(raw, list):
        return set()
    return {entry for entry in raw if isinstance(entry, str)}


def _record_hydrated_widget_tool_calls(
    thread: ThreadMetadata, tool_call_ids: set[str]
) -> None:
    """Persist newly hydrated widget tool-call ids onto the thread metadata."""
    metadata = dict(thread.metadata or {})
    existing = metadata.get(_HYDRATED_WIDGET_TOOL_CALLS_KEY)
    if isinstance(existing, list):
        ordered = [entry for entry in existing if isinstance(entry, str)]
    else:
        ordered = []
    known = set(ordered)
    for tool_call_id in tool_call_ids:
        if tool_call_id not in known:
            ordered.append(tool_call_id)
            known.add(tool_call_id)
    metadata[_HYDRATED_WIDGET_TOOL_CALLS_KEY] = ordered[
        -_MAX_TRACKED_WIDGET_TOOL_CALLS:
    ]
    thread.metadata = metadata


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


def _tool_call_id_from_message(message: Any) -> str | None:
    """Return the ToolMessage ``tool_call_id`` when present."""
    if isinstance(message, Mapping):
        raw = message.get("tool_call_id")
    else:
        raw = getattr(message, "tool_call_id", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _extract_widget_candidate(message: Any) -> _WidgetCandidate | None:
    """Return a widget candidate when the ToolMessage contains widget payloads."""
    artifact = getattr(message, "artifact", None)
    content = getattr(message, "content", None)
    if isinstance(message, Mapping):
        artifact = message.get("artifact")
        content = message.get("content")

    tool_call_id = _tool_call_id_from_message(message)

    artifact_candidate = _candidate_from_artifact(artifact)
    if artifact_candidate:
        # Structured content without a type is treated as a candidate so validation
        # can surface a notice to the user.
        candidate_type = _candidate_type(artifact_candidate.payload)
        if (
            candidate_type in _WIDGET_TYPES or candidate_type is None
        ):  # pragma: no branch
            return artifact_candidate._replace(tool_call_id=tool_call_id)

    content_candidate = _candidate_from_content(
        content, getattr(artifact_candidate, "copy_text", None)
    )
    if content_candidate is not None:
        return content_candidate._replace(tool_call_id=tool_call_id)
    return None


def _collect_new_widget_candidates(
    state_view: Mapping[str, Any],
    already_hydrated: set[str],
) -> tuple[list[_WidgetCandidate], set[str]]:
    """Return widget candidates not yet hydrated, plus their tool-call ids.

    Skips candidates whose ``tool_call_id`` was hydrated on a previous turn or
    already appears earlier in the current state, so each widget is emitted once.
    """
    candidates: list[_WidgetCandidate] = []
    seen_this_turn: set[str] = set()
    for message in _messages_from_state(state_view):
        if not _is_tool_message(message):
            continue
        candidate = _extract_widget_candidate(message)
        if candidate is None:  # pragma: no cover - defensive programming
            continue
        tool_call_id = candidate.tool_call_id
        if tool_call_id is not None:
            if tool_call_id in already_hydrated or tool_call_id in seen_this_turn:
                continue
            seen_this_turn.add(tool_call_id)
        candidates.append(candidate)
    return candidates, seen_this_turn


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


# Maps form-input widget node types to the attribute that carries their
# rendered (default) value. ChatKit form controls are uncontrolled, so their
# checked/selected state is re-derived from these attributes on every render.
_FORM_INPUT_DEFAULT_FIELDS: dict[str, str] = {
    "Checkbox": "defaultChecked",
    "RadioGroup": "defaultValue",
    "Select": "defaultValue",
    "Input": "defaultValue",
    "Textarea": "defaultValue",
}


def _action_payload_mapping(
    action: Action[str, Any] | Mapping[str, Any] | object,
) -> Mapping[str, Any] | None:
    """Return the action payload when it is a mapping of form values."""
    if isinstance(action, Mapping):
        payload = action.get("payload")
    else:
        payload = getattr(action, "payload", None)
    return payload if isinstance(payload, Mapping) else None


def _resolve_form_value(payload: Mapping[str, Any], name: str) -> tuple[bool, Any]:
    """Resolve a form-control value from a submit payload by field ``name``.

    ChatKit submits nested field names (e.g. ``choices.opt1``) either as a flat
    dotted key or as a nested object, so both shapes are supported. Returns a
    ``(found, value)`` pair where ``found`` is False when the field is absent.
    """
    if name in payload:
        return True, payload[name]
    current: Any = payload
    for part in name.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


def _coerce_checked(value: Any) -> bool:
    """Interpret a submitted checkbox value as a boolean checked state."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "on", "1", "yes", "checked"}
    if isinstance(value, int | float):
        return bool(value)
    return value is not None


def _collect_form_input_nodes(node: Any, names: list[tuple[str, str]]) -> None:
    """Collect ``(type, name)`` pairs for form-control nodes in a widget tree."""
    if isinstance(node, list):
        for child in node:
            _collect_form_input_nodes(child, names)
        return
    if not isinstance(node, Mapping):
        return
    node_type = node.get("type")
    name = node.get("name")
    if (
        isinstance(node_type, str)
        and isinstance(name, str)
        and node_type in _FORM_INPUT_DEFAULT_FIELDS
    ):
        names.append((node_type, name))
    for value in node.values():
        if isinstance(value, list | dict):
            _collect_form_input_nodes(value, names)


def _apply_submitted_form_values(
    node: Any, payload: Mapping[str, Any]
) -> tuple[Any, bool]:
    """Return a copy of ``node`` with input defaults set from ``payload``.

    Walks the widget tree and writes each form control's submitted value into
    its ``defaultChecked``/``defaultValue`` attribute. Absent checkbox fields are
    treated as unchecked (HTML form semantics omit unchecked boxes). Returns the
    updated tree and whether any attribute changed.
    """
    changed = False
    if isinstance(node, list):
        updated_list: list[Any] = []
        for child in node:
            updated_child, child_changed = _apply_submitted_form_values(child, payload)
            updated_list.append(updated_child)
            changed = changed or child_changed
        return updated_list, changed
    if not isinstance(node, Mapping):
        return node, False

    updated: dict[str, Any] = dict(node)
    node_type = updated.get("type")
    name = updated.get("name")
    if (
        isinstance(node_type, str)
        and isinstance(name, str)
        and node_type in _FORM_INPUT_DEFAULT_FIELDS
    ):
        field = _FORM_INPUT_DEFAULT_FIELDS[node_type]
        found, value = _resolve_form_value(payload, name)
        if node_type == "Checkbox":
            new_value: Any = _coerce_checked(value) if found else False
        elif found:
            new_value = value if isinstance(value, str) else str(value)
        else:
            new_value = updated.get(field)
        if updated.get(field) != new_value:
            updated[field] = new_value
            changed = True

    for key, value in list(updated.items()):
        if isinstance(value, list | dict):
            updated[key], child_changed = _apply_submitted_form_values(value, payload)
            changed = changed or child_changed
    return updated, changed


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
        attachment_service = getattr(store, "attachment_service", None)
        self._workflow_executor = WorkflowExecutor(
            repository=repository,
            vault_provider=vault_provider,
            attachment_service=attachment_service,
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
        additional_attachments: list[Any] | None = None,
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
            additional_attachments=additional_attachments,
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
        """Hydrate widget thread items from LangChain ToolMessages.

        The workflow state carries the full checkpointed message history, so the
        same widget ``ToolMessage`` reappears on every turn. Track which widget
        tool calls have already been hydrated on the thread (keyed by
        ``tool_call_id``) and skip them, so a follow-up text message does not
        re-emit every widget produced earlier in the conversation.
        """
        candidates, seen_this_turn = _collect_new_widget_candidates(
            state_view, _hydrated_widget_tool_calls(thread)
        )

        if not candidates:
            return [], []

        if seen_this_turn:
            _record_hydrated_widget_tool_calls(thread, seen_this_turn)

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

    async def _emit_action_widgets(
        self,
        thread: ThreadMetadata,
        state_view: Mapping[str, Any],
        sender: WidgetItem | None,
        action: Action[str, Any] | Mapping[str, Any],
        context: ChatKitRequestContext,
    ) -> AsyncIterator[ThreadStreamEvent]:
        """Emit notices and widgets produced by a widget action.

        Updates the sender widget in place when the workflow re-emits it,
        otherwise freezes the submitted selection into the sender so it survives
        follow-up turns. Any genuinely new widgets are appended to the thread.
        """
        widget_items, widget_notices = await self._hydrate_widget_items(
            thread, state_view, context
        )
        for notice in widget_notices:
            yield notice

        updated_in_place = False
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
            updated_in_place = True

        if sender is not None and not updated_in_place:
            frozen = await self._freeze_widget_selection(
                thread, sender, action, context
            )
            if frozen is not None:
                yield frozen

        for widget_item in widget_items:
            await self.store.add_thread_item(thread.id, widget_item, context)
            yield ThreadItemDoneEvent(item=widget_item)

    async def _freeze_widget_selection(
        self,
        thread: ThreadMetadata,
        sender: WidgetItem,
        action: Action[str, Any] | Mapping[str, Any],
        context: ChatKitRequestContext,
    ) -> ThreadItemUpdatedEvent | None:
        """Persist a submit action's form values into the sender widget root.

        ChatKit form controls are uncontrolled, so the user's selections live
        only in transient browser state and are re-derived from the widget root
        on every render. When the workflow does not re-emit the widget, write the
        submitted selection back into the stored root so it survives follow-up
        turns instead of snapping back to the original defaults.
        """
        payload = _action_payload_mapping(action)
        if payload is None:
            return None

        widget_dict = sender.widget.model_dump(exclude_none=True)
        input_names: list[tuple[str, str]] = []
        _collect_form_input_nodes(widget_dict, input_names)
        # Only treat this as a form submission when the payload carries at least
        # one of the widget's own field values; this avoids clobbering selections
        # for non-submit actions (e.g. toggles) that share the action pipeline.
        if not any(_resolve_form_value(payload, name)[0] for _, name in input_names):
            return None

        updated_dict, changed = _apply_submitted_form_values(widget_dict, payload)
        if not changed:
            return None
        try:
            widget_root = _WIDGET_ROOT_ADAPTER.validate_python(updated_dict)
        except ValidationError:
            return None

        updated_item = WidgetItem(
            id=sender.id,
            thread_id=sender.thread_id,
            created_at=sender.created_at,
            widget=widget_root,
            copy_text=sender.copy_text,
        )
        await self.store.save_item(thread.id, updated_item, context)
        return ThreadItemUpdatedEvent(
            item_id=sender.id,
            update=WidgetRootUpdated(widget=widget_root),
        )

    async def _run_workflow(
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
        """Delegate execution to the workflow executor."""
        return await self._workflow_executor.run(
            workflow_id,
            inputs,
            actor=actor,
            progress_callback=progress_callback,
            workspace_id=workspace_id,
            thread_id=thread_id,
            upload_session_id=upload_session_id,
        )

    async def _link_upload_session(
        self,
        context: ChatKitRequestContext,
        thread: ThreadMetadata,
        user_item: UserMessageItem | None = None,
    ) -> None:
        """Link upload-session attachments to the current thread when applicable."""
        workspace_id = context.get("workspace_id") if context else None
        attachment_service = getattr(self.store, "attachment_service", None)
        if not workspace_id or attachment_service is None:
            return
        attachment_ids = self._attachment_ids_from_user_item(user_item)

        if attachment_ids:
            await self._link_attachments_to_thread(
                attachment_service,
                attachment_ids,
                thread,
                str(workspace_id),
            )

        upload_session_id = context.get("upload_session_id") if context else None
        if not upload_session_id:
            upload_session_id = await self._resolve_upload_session_id(
                attachment_service,
                attachment_ids,
                thread,
                str(workspace_id),
                str(context.get("workflow_id") or ""),
            )
        if not upload_session_id:
            upload_session_id = await self._resolve_recent_upload_session_id(
                attachment_service,
                thread,
                str(workspace_id),
                str(context.get("workflow_id") or ""),
                context.get("subject") if context else None,
            )
        if not upload_session_id:
            return
        context["upload_session_id"] = str(upload_session_id)
        await self._link_upload_session_to_thread(
            attachment_service,
            str(upload_session_id),
            thread,
            str(workspace_id),
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

        await self._link_upload_session(context, thread, user_item)
        additional_attachments = await self._resolve_additional_attachments(
            thread=thread,
            workflow_id=str(workflow_id),
            context=context,
        )

        inputs = self._build_inputs_payload(
            workflow,
            thread,
            message_text,
            history,
            user_item,
            additional_attachments=additional_attachments,
        )

        actor = str(context.get("actor") or "chatkit")
        progress_queue: asyncio.Queue[ThreadStreamEvent | None] = asyncio.Queue()

        async def on_progress(step: Mapping[str, Any]) -> None:
            await _enqueue_progress_updates(progress_queue, step)

        try:
            yield ProgressUpdateEvent(text="Agent is working...")
            workspace_id = context.get("workspace_id")
            ctx_upload_session = context.get("upload_session_id") if context else None
            run_task = asyncio.create_task(
                self._run_workflow(
                    workflow_id,
                    inputs,
                    actor=actor,
                    progress_callback=on_progress,
                    workspace_id=workspace_id,
                    thread_id=str(thread.id),
                    upload_session_id=(
                        str(ctx_upload_session) if ctx_upload_session else None
                    ),
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

    @staticmethod
    def _attachment_ids_from_user_item(user_item: UserMessageItem | None) -> list[str]:
        """Extract attachment ids from a user item in a tolerant, ordered way."""
        if user_item is None:
            return []
        attachments = getattr(user_item, "attachments", None)
        if not attachments:
            return []

        attachment_ids: list[str] = []
        for attachment in attachments:
            attachment_id: Any | None = None
            if isinstance(attachment, str):
                attachment_id = attachment
            elif isinstance(attachment, Mapping):
                attachment_id = attachment.get("id") or attachment.get("file_id")
            else:
                attachment_id = getattr(attachment, "id", None)

            if isinstance(attachment_id, str):
                normalized = attachment_id.strip()
                if normalized:
                    attachment_ids.append(normalized)
        return attachment_ids

    async def _link_attachments_to_thread(
        self,
        attachment_service: Any,
        attachment_ids: list[str],
        thread: ThreadMetadata,
        workspace_id: str,
    ) -> None:
        """Bind attachments referenced on the current message to the thread."""
        try:
            await attachment_service.link_attachments_to_thread(
                attachment_ids,
                str(thread.id),
                workspace_id,
            )
        except Exception:
            logger.exception("Failed to link attachments to thread %s", thread.id)

    async def _resolve_upload_session_id(
        self,
        attachment_service: Any,
        attachment_ids: list[str],
        thread: ThreadMetadata,
        workspace_id: str,
        workflow_id: str,
    ) -> str | None:
        """Resolve an upload session id from current message attachments."""
        if not attachment_ids:
            return None
        try:
            return await attachment_service.resolve_upload_session_id(
                attachment_ids,
                workspace_id,
                workflow_id=workflow_id,
            )
        except Exception:
            logger.exception("Failed to infer upload session for thread %s", thread.id)
            return None

    async def _resolve_recent_upload_session_id(
        self,
        attachment_service: Any,
        thread: ThreadMetadata,
        workspace_id: str,
        workflow_id: str,
        actor_subject: str | None,
    ) -> str | None:
        """Resolve a recent unlinked upload session scoped to the current user."""
        subject = str(actor_subject).strip() if actor_subject else ""
        if not subject:
            return None
        try:
            return await attachment_service.resolve_recent_upload_session_id(
                workspace_id,
                workflow_id,
                actor_subject=subject,
            )
        except Exception:
            logger.exception(
                "Failed to resolve recent upload session for thread %s",
                thread.id,
            )
            return None

    async def _link_upload_session_to_thread(
        self,
        attachment_service: Any,
        upload_session_id: str,
        thread: ThreadMetadata,
        workspace_id: str,
    ) -> None:
        """Bind a resolved upload session to the current thread."""
        try:
            count = await attachment_service.link_upload_session_to_thread(
                upload_session_id=upload_session_id,
                thread_id=str(thread.id),
                workspace_id=workspace_id,
            )
            if count > 0:
                logger.debug(
                    "Linked %d attachment(s) from session %s to thread %s",
                    count,
                    upload_session_id,
                    thread.id,
                )
        except Exception:
            logger.exception(
                "Failed to link upload session %s to thread %s",
                upload_session_id,
                thread.id,
            )

    async def _resolve_additional_attachments(
        self,
        *,
        thread: ThreadMetadata,
        workflow_id: str,
        context: ChatKitRequestContext,
    ) -> list[dict[str, Any]]:
        """Return attachment metadata linked to the current thread or session."""
        workspace_id = context.get("workspace_id") if context else None
        attachment_service = getattr(self.store, "attachment_service", None)
        if attachment_service is None or not workspace_id:
            return []

        upload_session_id = context.get("upload_session_id") if context else None
        try:
            return await attachment_service.list_attachment_summaries(
                workspace_id=str(workspace_id),
                workflow_id=workflow_id,
                thread_id=str(thread.id),
                upload_session_id=(
                    str(upload_session_id) if upload_session_id else None
                ),
            )
        except Exception:
            logger.exception(
                "Failed to resolve ChatKit attachment summaries",
                extra={
                    "thread_id": str(thread.id),
                    "workflow_id": workflow_id,
                    "workspace_id": workspace_id,
                },
            )
            return []

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
                    workspace_id=context.get("workspace_id"),
                    thread_id=str(thread.id),
                    upload_session_id=(
                        str(context["upload_session_id"])
                        if context.get("upload_session_id")
                        else None
                    ),
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

        self._record_run_metadata(thread, run)
        async for event in self._emit_action_widgets(
            thread, state_view, sender, action, context
        ):
            yield event

        assistant_item = self._build_assistant_item(thread, reply, context)
        await self.store.add_thread_item(thread.id, assistant_item, context)
        await self.store.save_thread(thread, context)
        yield ThreadItemDoneEvent(item=assistant_item)


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

    backend = str(candidate or "postgres").lower()
    if backend != "postgres":
        msg = "CHATKIT_BACKEND must be 'postgres'."
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
        if backend != "postgres":
            msg = "ChatKit backend must be 'postgres'."
            raise ValueError(msg)
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
    return OrcheoChatKitServer(
        store=store,
        repository=repository,
        vault_provider=vault_provider,
    )


__all__ = ["OrcheoChatKitServer", "create_chatkit_server"]
