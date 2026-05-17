from __future__ import annotations
from orcheo.models import (
    ChatKitStartScreenPrompt,
    ChatKitSupportedModel,
    WorkflowChatKitConfig,
)
from orcheo_backend.app.routers.workflows import _chatkit_update_kwargs
from orcheo_backend.app.schemas.workflows import WorkflowUpdateRequest


def test_chatkit_update_kwargs_records_start_screen_prompts() -> None:
    prompts = [ChatKitStartScreenPrompt(label="Entry", prompt="Action")]
    request = WorkflowUpdateRequest(
        chatkit=WorkflowChatKitConfig(start_screen_prompts=prompts)
    )

    kwargs = _chatkit_update_kwargs(request)

    assert kwargs == {"chatkit_start_screen_prompts": prompts}


def test_chatkit_update_kwargs_records_supported_models() -> None:
    models = [ChatKitSupportedModel(id="openai:gpt-5")]
    request = WorkflowUpdateRequest(
        chatkit=WorkflowChatKitConfig(supported_models=models)
    )

    kwargs = _chatkit_update_kwargs(request)

    assert kwargs == {"chatkit_supported_models": models}


def test_chatkit_update_kwargs_clears_when_fields_set_to_none() -> None:
    request = WorkflowUpdateRequest(
        chatkit=WorkflowChatKitConfig(
            start_screen_prompts=None,
            supported_models=None,
        )
    )

    kwargs = _chatkit_update_kwargs(request)

    assert kwargs == {
        "clear_chatkit_start_screen_prompts": True,
        "clear_chatkit_supported_models": True,
    }


def test_chatkit_update_kwargs_uses_explicit_clear_flags() -> None:
    request = WorkflowUpdateRequest(
        clear_chatkit_start_screen_prompts=True,
        clear_chatkit_supported_models=True,
    )

    kwargs = _chatkit_update_kwargs(request)

    assert kwargs == {
        "clear_chatkit_start_screen_prompts": True,
        "clear_chatkit_supported_models": True,
    }
