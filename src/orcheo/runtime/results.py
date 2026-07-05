"""Helpers for reading workflow node results from state."""

from __future__ import annotations
from collections.abc import Mapping, Sequence
from typing import Any
from langchain_core.messages import BaseMessage
from orcheo.graph.state import State


def results_map(state: State) -> Mapping[str, Any]:
    """Return the workflow node-results mapping, or an empty mapping."""
    node_results = state.get("node_results") if isinstance(state, Mapping) else None
    return node_results if isinstance(node_results, Mapping) else {}


def node_result(state: State, node_name: str) -> Mapping[str, Any]:
    """Return a named node result mapping from ``node_results``."""
    value = results_map(state).get(node_name)
    return value if isinstance(value, Mapping) else {}


def first_result_field(state: State, field: str, producers: Sequence[str]) -> Any:
    """Return the first non-empty field value produced by the listed nodes."""
    results = results_map(state)
    for producer in producers:
        value = results.get(producer)
        if isinstance(value, Mapping) and value.get(field) is not None:
            return value[field]
    return None


def assistant_message_texts(state: State) -> list[str]:
    """Return assistant-facing message texts from newest to oldest."""
    messages = state.get("messages") if isinstance(state, Mapping) else []
    if not isinstance(messages, list):
        return []

    texts: list[str] = []
    for message in reversed(messages):
        if isinstance(message, Mapping):
            role = message.get("role") or message.get("type")
            if role not in {"assistant", "ai"}:
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                texts.append(content)
            continue
        if isinstance(message, BaseMessage) and message.type in {"ai", "assistant"}:
            content = message.content
            if isinstance(content, str) and content.strip():
                texts.append(content)
    return texts


__all__ = [
    "assistant_message_texts",
    "first_result_field",
    "node_result",
    "results_map",
]
