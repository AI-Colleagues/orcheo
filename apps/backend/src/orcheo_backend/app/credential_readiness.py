"""Helpers for determining workflow credential readiness."""

from __future__ import annotations
import re
from collections.abc import Mapping, Sequence
from typing import Any
from langgraph.graph import StateGraph
from pydantic import BaseModel
from orcheo.graph.ingestion.ast_extraction import extract_graph_index
from orcheo.runtime.credentials import parse_credential_reference


_PLACEHOLDER_PATTERN = re.compile(r"\[\[[^\[\]]+\]\]")


def collect_workflow_credential_placeholders(
    graph_payload: Mapping[str, Any],
    runnable_config: Mapping[str, Any] | None,
) -> dict[str, set[str]]:
    """Return statically visible credential placeholders in stored payloads.

    Persisted tenant script source is data at this stage and must never be
    imported or executed in a backend readiness check.
    """
    placeholders: dict[str, set[str]] = {}
    _collect_value(graph_payload, placeholders, seen=set())
    _collect_source_index(graph_payload, placeholders)
    if runnable_config is not None:
        _collect_value(runnable_config, placeholders, seen=set())

    return placeholders


def _collect_source_index(
    graph_payload: Mapping[str, Any],
    placeholders: dict[str, set[str]],
) -> None:
    source = graph_payload.get("source")
    if not isinstance(source, str):
        return
    _collect_value(extract_graph_index(source), placeholders, seen=set())


def _collect_state_graph(
    graph: StateGraph,
    placeholders: dict[str, set[str]],
    *,
    seen: set[int],
) -> None:
    graph_id = id(graph)
    if graph_id in seen:
        return
    seen.add(graph_id)

    for _, spec in graph.nodes.items():
        _collect_value(_unwrap_runnable(spec.runnable), placeholders, seen=seen)


def _collect_value(
    value: Any,
    placeholders: dict[str, set[str]],
    *,
    seen: set[int],
) -> None:
    if isinstance(value, str):
        _collect_string(value, placeholders)
        return

    if isinstance(value, StateGraph):
        _collect_state_graph(value, placeholders, seen=seen)
        return

    if isinstance(value, BaseModel):
        _collect_model(value, placeholders, seen=seen)
        return

    if isinstance(value, Mapping):
        _collect_mapping(value, placeholders, seen=seen)
        return

    if _is_nested_sequence(value):
        _collect_sequence(value, placeholders, seen=seen)


def _collect_string(value: str, placeholders: dict[str, set[str]]) -> None:
    for match in _PLACEHOLDER_PATTERN.finditer(value):
        placeholder = match.group(0)
        reference = parse_credential_reference(placeholder)
        if reference is None:
            continue
        placeholders.setdefault(reference.identifier, set()).add(placeholder)


def _collect_model(
    value: BaseModel,
    placeholders: dict[str, set[str]],
    *,
    seen: set[int],
) -> None:
    if _mark_seen(value, seen):
        return
    for field_name in value.__class__.model_fields:
        _collect_value(getattr(value, field_name), placeholders, seen=seen)


def _collect_mapping(
    value: Mapping[Any, Any],
    placeholders: dict[str, set[str]],
    *,
    seen: set[int],
) -> None:
    if _mark_seen(value, seen):
        return
    for nested in value.values():
        _collect_value(nested, placeholders, seen=seen)


def _collect_sequence(
    value: Sequence[Any],
    placeholders: dict[str, set[str]],
    *,
    seen: set[int],
) -> None:
    if _mark_seen(value, seen):
        return
    for nested in value:
        _collect_value(nested, placeholders, seen=seen)


def _mark_seen(value: object, seen: set[int]) -> bool:
    value_id = id(value)
    if value_id in seen:
        return True
    seen.add(value_id)
    return False


def _is_nested_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, bytes | bytearray | str
    )


def _unwrap_runnable(runnable: Any) -> Any:
    """Return the underlying callable stored within LangGraph wrappers."""
    if hasattr(runnable, "afunc") and isinstance(runnable.afunc, BaseModel):
        return runnable.afunc
    if hasattr(runnable, "func") and isinstance(runnable.func, BaseModel):
        return runnable.func
    return runnable


__all__ = ["collect_workflow_credential_placeholders"]
