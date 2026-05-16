"""Shared workflow ChatKit metadata mutation helpers."""

from __future__ import annotations
from typing import Any
from orcheo.models.workflow import (
    ChatKitStartScreenPrompt,
    ChatKitSupportedModel,
    Workflow,
    WorkflowChatKitConfig,
)


def _ensure_chatkit(workflow: Workflow) -> WorkflowChatKitConfig:
    """Return the mutable ChatKit config attached to ``workflow``."""
    chatkit = workflow.chatkit
    if chatkit is None:
        chatkit = WorkflowChatKitConfig()
        workflow.chatkit = chatkit
    return chatkit


def apply_chatkit_start_screen_prompts_update(
    workflow: Workflow,
    metadata: dict[str, Any],
    *,
    chatkit_start_screen_prompts: list[ChatKitStartScreenPrompt] | None = None,
    clear_chatkit_start_screen_prompts: bool = False,
) -> None:
    """Apply ChatKit starter-prompt changes to a workflow and audit metadata."""
    chatkit = workflow.chatkit
    if clear_chatkit_start_screen_prompts:
        if chatkit is not None and chatkit.start_screen_prompts is not None:
            metadata["chatkit_start_screen_prompts"] = {
                "from": [
                    prompt.model_dump(mode="json")
                    for prompt in chatkit.start_screen_prompts
                ],
                "to": None,
            }
            chatkit.start_screen_prompts = None
            if chatkit.supported_models is None:
                workflow.chatkit = None
        return

    if chatkit_start_screen_prompts is None:
        return

    chatkit = _ensure_chatkit(workflow)
    current_prompts = chatkit.start_screen_prompts
    current_payload = (
        [prompt.model_dump(mode="json") for prompt in current_prompts]
        if current_prompts is not None
        else None
    )
    next_payload = [
        prompt.model_dump(mode="json") for prompt in chatkit_start_screen_prompts
    ]
    if next_payload == current_payload:
        return

    metadata["chatkit_start_screen_prompts"] = {
        "from": current_payload,
        "to": next_payload,
    }
    chatkit.start_screen_prompts = [
        prompt.model_copy(deep=True) for prompt in chatkit_start_screen_prompts
    ]


def apply_chatkit_supported_models_update(
    workflow: Workflow,
    metadata: dict[str, Any],
    *,
    chatkit_supported_models: list[ChatKitSupportedModel] | None = None,
    clear_chatkit_supported_models: bool = False,
) -> None:
    """Apply ChatKit supported-model changes to a workflow and audit metadata."""
    chatkit = workflow.chatkit
    if clear_chatkit_supported_models:
        if chatkit is not None and chatkit.supported_models is not None:
            metadata["chatkit_supported_models"] = {
                "from": [
                    model.model_dump(mode="json") for model in chatkit.supported_models
                ],
                "to": None,
            }
            chatkit.supported_models = None
            if chatkit.start_screen_prompts is None:
                workflow.chatkit = None
        return

    if chatkit_supported_models is None:
        return

    chatkit = _ensure_chatkit(workflow)
    current_models = chatkit.supported_models
    current_payload = (
        [model.model_dump(mode="json") for model in current_models]
        if current_models is not None
        else None
    )
    next_payload = [model.model_dump(mode="json") for model in chatkit_supported_models]
    if next_payload == current_payload:
        return

    metadata["chatkit_supported_models"] = {
        "from": current_payload,
        "to": next_payload,
    }
    chatkit.supported_models = [
        model.model_copy(deep=True) for model in chatkit_supported_models
    ]
