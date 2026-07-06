"""Tests covering workflow entity helpers."""

from __future__ import annotations
from uuid import UUID
import pytest
from orcheo.models import workflow_entities
from orcheo.models.workflow_entities import (
    ChatKitStartScreenPrompt,
    ChatKitSupportedModel,
    Workflow,
)


def test_slugify_uses_uuid_when_input_cleans_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = UUID("00000000-0000-0000-0000-000000000042")
    monkeypatch.setattr(workflow_entities, "uuid4", lambda: sentinel)

    assert workflow_entities._slugify("   ") == str(sentinel)


def test_chatkit_prompt_validator_rejects_empty_fields() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        ChatKitStartScreenPrompt(label="", prompt="value")


def test_chatkit_prompt_and_model_normalization() -> None:
    prompt = ChatKitStartScreenPrompt(label="  Label  ", prompt="  Prompt  ", icon="  ")
    model = ChatKitSupportedModel(
        id="  model-1  ",
        label="  Model Label  ",
        description="  Description  ",
    )
    prompt_without_icon = ChatKitStartScreenPrompt(
        label="Label",
        prompt="Prompt",
        icon=None,
    )
    model_without_description = ChatKitSupportedModel(id="model-2", description=None)

    assert prompt.label == "Label"
    assert prompt.prompt == "Prompt"
    assert prompt.icon is None
    assert model.id == "model-1"
    assert model.label == "Model Label"
    assert model.description == "Description"
    assert prompt_without_icon.icon is None
    assert model_without_description.description is None


def test_chatkit_supported_model_requires_id() -> None:
    with pytest.raises(ValueError, match="id must not be empty"):
        ChatKitSupportedModel(id="")


def test_chatkit_prompt_icon_accepts_non_empty_value() -> None:
    prompt = ChatKitStartScreenPrompt(label="Label", prompt="Prompt", icon="  spark  ")

    assert prompt.icon == "spark"


def test_chatkit_config_is_preserved_when_provided_explicitly() -> None:
    workflow = Workflow(
        name="Example",
        chatkit={
            "start_screen_prompts": [ChatKitStartScreenPrompt(label="L", prompt="P")],
            "supported_models": [ChatKitSupportedModel(id="m")],
        },
    )

    assert workflow.chatkit is not None
    assert workflow.chatkit.start_screen_prompts is not None
    assert workflow.chatkit.start_screen_prompts[0].label == "L"
    assert workflow.chatkit.supported_models is not None
    assert workflow.chatkit.supported_models[0].id == "m"


def test_publish_rejects_already_public_workflow() -> None:
    workflow = Workflow(name="Example", is_public=True)

    with pytest.raises(ValueError, match="already published"):
        workflow.publish(require_login=False, actor="actor")


def test_revoke_publish_rejects_when_not_public() -> None:
    workflow = Workflow(name="Example")

    with pytest.raises(ValueError, match="not currently published"):
        workflow.revoke_publish(actor="actor")


def test_mark_upload_error_truncates_overly_long_messages() -> None:
    workflow = Workflow(name="Example")

    workflow.mark_upload_error(message="x" * 5000, actor="actor")

    assert workflow.upload_error is not None
    assert len(workflow.upload_error.message) == 4000
    assert workflow.upload_error.message.endswith("...")


def test_clear_upload_error_is_a_no_op_without_a_prior_error() -> None:
    workflow = Workflow(name="Example")

    workflow.clear_upload_error(actor="actor")

    assert workflow.upload_error is None
    assert workflow.audit_log == []
