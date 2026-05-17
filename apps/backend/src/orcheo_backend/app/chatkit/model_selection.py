"""Helpers for resolving ChatKit model-selection behavior."""

from __future__ import annotations
from typing import Any
from orcheo.models import Workflow


CHATKIT_MODEL_CONFIG_KEY = "chatkit_model"


def _default_chatkit_model(workflow: Workflow) -> str | None:
    """Return the workflow's default ChatKit model when one is configured."""
    chatkit = workflow.chatkit
    if chatkit is None or not chatkit.supported_models:
        return None

    enabled_models = [
        model
        for model in chatkit.supported_models
        if not model.disabled and model.id.strip()
    ]
    if not enabled_models:
        return None
    for model in enabled_models:
        if model.default:
            return model.id.strip()
    return enabled_models[0].id.strip()


def resolve_chatkit_selected_model(
    workflow: Workflow,
    candidate: object,
) -> str | None:
    """Return the validated ChatKit-selected model for a workflow run."""
    default_model = _default_chatkit_model(workflow)
    if default_model is None:
        return None
    if not isinstance(candidate, str):
        return default_model
    normalized = candidate.strip()
    if not normalized:
        return default_model

    allowed = {
        model.id.strip()
        for model in workflow.chatkit.supported_models  # type: ignore[union-attr]
        if not model.disabled and model.id.strip()
    }
    if normalized in allowed:
        return normalized
    return default_model


def apply_chatkit_selected_model(
    inputs: dict[str, Any],
    workflow: Workflow,
) -> str | None:
    """Normalize the inbound ChatKit-selected model and update workflow inputs."""
    selected_model = resolve_chatkit_selected_model(workflow, inputs.get("model"))
    if selected_model is None:
        inputs.pop("model", None)
        return None
    inputs["model"] = selected_model
    return selected_model


__all__ = [
    "CHATKIT_MODEL_CONFIG_KEY",
    "apply_chatkit_selected_model",
    "resolve_chatkit_selected_model",
]
