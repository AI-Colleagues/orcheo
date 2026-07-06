"""Helpers for model metadata attached to trace payloads."""

from __future__ import annotations
from collections.abc import Mapping, Sequence
from typing import Any


TRACE_METADATA_KEY = "__trace"
NAMESPACE_METADATA_KEY = "__namespace"


def split_model_identifier(identifier: str | None) -> tuple[str | None, str | None]:
    """Split a ``provider:model`` identifier into its components."""
    if not isinstance(identifier, str):
        return None, None
    normalized = identifier.strip()
    if not normalized:
        return None, None
    if ":" not in normalized:
        return None, normalized
    provider: str | None
    model: str | None
    provider, model = normalized.split(":", 1)
    provider = provider.strip() or None
    model = model.strip() or None
    return provider, model


def build_ai_trace_metadata(
    *,
    kind: str,
    requested_model: str,
    actual_model: str | None = None,
    operation: str | None = None,
    vector_size: int | None = None,
) -> dict[str, Any]:
    """Build normalized AI trace metadata from requested/runtime model details."""
    provider, _ = split_model_identifier(requested_model)
    metadata: dict[str, Any] = {
        "kind": kind,
        "requested_model": requested_model,
    }
    if provider:
        metadata["provider"] = provider
    if actual_model:
        metadata["actual_model"] = actual_model
    if operation:
        metadata["operation"] = operation
    if vector_size is not None:
        metadata["vector_size"] = vector_size
    return metadata


def extract_ai_trace_attributes(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return OpenTelemetry span attributes from trace metadata payloads."""
    trace_payload = payload.get(TRACE_METADATA_KEY)
    if not isinstance(trace_payload, Mapping):
        return {}
    ai_payload = trace_payload.get("ai")
    if not isinstance(ai_payload, Mapping):
        return {}

    attributes: dict[str, Any] = {}
    kind = ai_payload.get("kind")
    if kind is not None:
        attributes["orcheo.ai.kind"] = str(kind)
    requested_model = ai_payload.get("requested_model")
    if requested_model is not None:
        attributes["orcheo.ai.model.requested"] = str(requested_model)
    actual_model = ai_payload.get("actual_model")
    if actual_model is not None:
        attributes["orcheo.ai.model.actual"] = str(actual_model)
    provider = ai_payload.get("provider")
    if provider is not None:
        attributes["orcheo.ai.provider"] = str(provider)
    operation = ai_payload.get("operation")
    if operation is not None:
        attributes["orcheo.ai.embedding.operation"] = str(operation)
    vector_size = ai_payload.get("vector_size")
    if vector_size is not None:
        try:
            attributes["orcheo.ai.embedding.vector_size"] = int(vector_size)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            pass
    return attributes


def infer_chat_result_model_name(payload: Mapping[str, Any]) -> str | None:
    """Extract the provider-reported model name from serialized chat outputs."""
    direct_metadata = payload.get("response_metadata")
    metadata_mapping = _coerce_mapping(direct_metadata)
    if metadata_mapping is not None:
        inferred = _extract_model_name_from_mapping(metadata_mapping)
        if inferred:
            return inferred

    messages = payload.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, str | bytes):
        return None
    for message in reversed(messages):
        response_metadata = None
        if isinstance(message, Mapping):
            response_metadata = message.get("response_metadata")
        else:
            response_metadata = getattr(message, "response_metadata", None)
        metadata_mapping = _coerce_mapping(response_metadata)
        if metadata_mapping is None:
            continue
        inferred = _extract_model_name_from_mapping(metadata_mapping)
        if inferred:
            return inferred
    return None


def infer_model_name_from_instance(instance: Any) -> str | None:
    """Best-effort model identifier extraction from a LangChain model instance."""
    for attribute_name in (
        "model_name",
        "model",
        "model_id",
        "model_id_or_path",
        "deployment_name",
    ):
        value = getattr(instance, attribute_name, None)
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized:
            return normalized
    return None


def encode_step_namespace(
    step: Mapping[str, Any], namespace: Sequence[str]
) -> dict[str, Any]:
    """Return a copy of a stream step tagged with its subgraph namespace.

    LangGraph's ``subgraphs=True`` streaming mode reports the path to the
    subgraph invocation a node update belongs to out-of-band from the update
    dict itself. Stashing it under a reserved sibling key lets it ride along
    through history persistence and websocket delivery unchanged.
    """
    encoded = dict(step)
    if namespace:
        encoded[NAMESPACE_METADATA_KEY] = list(namespace)
    return encoded


def extract_step_namespace(step: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the subgraph namespace segments recorded on a stream step."""
    namespace = step.get(NAMESPACE_METADATA_KEY)
    if isinstance(namespace, Sequence) and not isinstance(namespace, str | bytes):
        return tuple(str(segment) for segment in namespace)
    return ()


def strip_trace_metadata(value: Any) -> Any:
    """Remove trace metadata keys from JSON-like payloads."""
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if str(key) == TRACE_METADATA_KEY:
                continue
            sanitized[str(key)] = strip_trace_metadata(item)
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [strip_trace_metadata(item) for item in value]
    return value


def _extract_model_name_from_mapping(payload: Mapping[str, Any]) -> str | None:
    for key in ("model_name", "model", "model_id", "deployment_name"):
        candidate = payload.get(key)
        if not isinstance(candidate, str):
            continue
        normalized = candidate.strip()
        if normalized:
            return normalized
    return None


def _coerce_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    return None


__all__ = [
    "NAMESPACE_METADATA_KEY",
    "TRACE_METADATA_KEY",
    "build_ai_trace_metadata",
    "encode_step_namespace",
    "extract_ai_trace_attributes",
    "extract_step_namespace",
    "infer_chat_result_model_name",
    "infer_model_name_from_instance",
    "split_model_identifier",
    "strip_trace_metadata",
]
