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


def test_backfill_chatkit_config_leaves_non_mappings_alone() -> None:
    assert Workflow._backfill_chatkit_config("text") == "text"


def test_backfill_chatkit_config_cleans_legacy_fields() -> None:
    data = {
        "chatkit": {"supported_models": []},
        "chatkit_start_screen_prompts": [{"label": "L", "prompt": "P"}],
        "chatkit_supported_models": [{"id": "m"}],
    }

    normalized = Workflow._backfill_chatkit_config(data)

    assert "chatkit_start_screen_prompts" not in normalized
    assert "chatkit_supported_models" not in normalized
    assert "chatkit" in normalized


def test_backfill_chatkit_config_wraps_legacy_fields() -> None:
    data = {
        "chatkit_start_screen_prompts": [{"label": "L", "prompt": "P"}],
        "chatkit_supported_models": [{"id": "m"}],
    }

    normalized = Workflow._backfill_chatkit_config(data)

    assert normalized["chatkit"]["start_screen_prompts"] == [
        {"label": "L", "prompt": "P"}
    ]
    assert normalized["chatkit"]["supported_models"] == [{"id": "m"}]


def test_set_chatkit_field_is_noop_when_value_none() -> None:
    workflow = Workflow(name="Example")

    workflow._set_chatkit_field("supported_models", None)

    assert workflow.chatkit is None


def test_chatkit_field_accessors_create_and_clear_config() -> None:
    workflow = Workflow(name="Example")

    assert workflow.chatkit_start_screen_prompts is None
    assert workflow.chatkit_supported_models is None

    prompts = [ChatKitStartScreenPrompt(label="L", prompt="P")]
    models = [ChatKitSupportedModel(id="m")]
    workflow.chatkit_start_screen_prompts = prompts
    workflow.chatkit_supported_models = models

    assert workflow.chatkit_start_screen_prompts == prompts
    assert workflow.chatkit_supported_models == models

    workflow.chatkit_start_screen_prompts = None
    workflow.chatkit_supported_models = None

    assert workflow.chatkit is None


def test_publish_rejects_already_public_workflow() -> None:
    workflow = Workflow(name="Example", is_public=True)

    with pytest.raises(ValueError, match="already published"):
        workflow.publish(require_login=False, actor="actor")


def test_revoke_publish_rejects_when_not_public() -> None:
    workflow = Workflow(name="Example")

    with pytest.raises(ValueError, match="not currently published"):
        workflow.revoke_publish(actor="actor")
