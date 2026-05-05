"""Helper utilities for working with ChatKit and LangChain message payloads."""
# ruff: noqa: I001

from __future__ import annotations

import json
import warnings
from collections.abc import Mapping
from typing import Any

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
    from chatkit.types import (
        Action,
        AssistantMessageContent,
        ThreadMetadata,
        UserMessageContent,
        WidgetItem,
        WidgetRoot,
    )
from langchain_core.messages import BaseMessage
from orcheo.runtime.state_builder import build_initial_state as build_runtime_state


def collect_text_from_user_content(content: list[UserMessageContent]) -> str:
    """Return concatenated text segments from user message content."""
    parts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text:
            parts.append(str(text))
    return " ".join(parts).strip()


def collect_text_from_assistant_content(
    content: list[AssistantMessageContent],
) -> str:
    """Return concatenated text segments from assistant message content."""
    parts: list[str] = []
    for item in content:
        if item.text:
            parts.append(str(item.text))
    return " ".join(parts).strip()


def stringify_langchain_message(message: Any) -> str:
    """Convert LangChain message objects into a plain string."""
    value: Any
    if isinstance(message, BaseMessage):
        value = message.content
    elif isinstance(message, Mapping):
        value = message.get("content") or message.get("text")
    else:
        value = getattr(message, "content", message)

    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for entry in value:
            part = stringify_langchain_message(entry)
            if part:
                parts.append(part)
        return " ".join(parts)
    return str(value)


def build_initial_state(
    graph_config: Mapping[str, Any],
    inputs: Mapping[str, Any],
    runtime_config: Mapping[str, Any] | None = None,
    workspace_id: str | None = None,
) -> Mapping[str, Any]:
    """Create the initial workflow state for the configured format."""
    return build_runtime_state(graph_config, inputs, runtime_config, workspace_id)


def extract_reply_from_state(state: Mapping[str, Any]) -> str | None:
    """Attempt to pull an assistant reply from the workflow state."""
    if "reply" in state:
        reply = state["reply"]
        if reply is not None:
            return str(reply)

    results = state.get("results")
    if isinstance(results, Mapping):
        for value in results.values():
            if isinstance(value, Mapping) and "reply" in value:
                reply = value["reply"]
                if reply is not None:
                    return str(reply)
            if isinstance(value, str):
                return value

    messages = state.get("messages")
    if isinstance(messages, list) and messages:
        return stringify_langchain_message(messages[-1])

    return None


def _action_type(action: Action[str, Any] | Mapping[str, Any] | object) -> str:
    """Extract the action type from either a Pydantic model or mapping."""
    if isinstance(action, Mapping):
        type_value = action.get("type")
    else:
        type_value = getattr(action, "type", None)
    return str(type_value) if type_value is not None else ""


def _action_payload(action: Action[str, Any] | Mapping[str, Any] | object) -> Any:
    """Extract the action payload from either a Pydantic model or mapping."""
    if isinstance(action, Mapping):
        return action.get("payload")
    return getattr(action, "payload", None)


def _dump_action(
    action: Action[str, Any] | Mapping[str, Any] | object,
) -> dict[str, Any]:
    """Normalize an action into a plain dict."""
    if hasattr(action, "model_dump"):
        dumped = action.model_dump()  # type: ignore[call-arg]
        if isinstance(dumped, Mapping):
            return dict(dumped)
    if isinstance(action, Mapping):
        return dict(action)

    payload = _action_payload(action)
    handler = getattr(action, "handler", None)
    loading_behavior = getattr(action, "loadingBehavior", None)
    normalized: dict[str, Any] = {"type": _action_type(action), "payload": payload}
    if handler is not None:
        normalized["handler"] = handler
    if loading_behavior is not None:
        normalized["loadingBehavior"] = loading_behavior
    return normalized


def _stringify_action(action: Action[str, Any] | Mapping[str, Any] | object) -> str:
    """Build a readable message string describing a widget action."""
    payload = _action_payload(action)
    if payload is None:
        payload_text = ""
    elif isinstance(payload, str):
        payload_text = payload
    else:
        try:
            payload_text = json.dumps(payload, default=str)
        except TypeError:
            payload_text = str(payload)
    prefix = f"[action:{_action_type(action)}]"
    return f"{prefix} {payload_text}".strip()


def _dump_widget(widget: WidgetRoot) -> dict[str, Any]:
    """Convert a WidgetRoot into a JSON-serialisable dict."""
    if hasattr(widget, "model_dump"):
        return widget.model_dump(exclude_none=True)  # type: ignore[union-attr]
    if isinstance(widget, Mapping):
        return dict(widget)
    return {"widget": widget}


def build_action_inputs_payload(
    thread: ThreadMetadata,
    action: Action[str, Any] | Mapping[str, Any] | object,
    history: list[dict[str, str]],
    widget_item: WidgetItem | None = None,
) -> dict[str, Any]:
    """Construct the workflow input payload for widget actions."""
    payload: dict[str, Any] = {
        "message": _stringify_action(action),
        "history": history,
        "thread_id": thread.id,
        "session_id": thread.id,
        "metadata": dict(thread.metadata),
    }
    selected_model = thread.metadata.get("chatkit_model")
    if isinstance(selected_model, str) and selected_model.strip():
        payload["model"] = selected_model.strip()
    payload["action"] = _dump_action(action)
    if widget_item is not None:  # pragma: no branch
        payload["widget_item_id"] = widget_item.id
        payload["widget"] = _dump_widget(widget_item.widget)
    return payload


__all__ = [
    "build_initial_state",
    "collect_text_from_assistant_content",
    "collect_text_from_user_content",
    "extract_reply_from_state",
    "stringify_langchain_message",
    "build_action_inputs_payload",
]
