from __future__ import annotations
from orcheo.models.workflow import (
    ChatKitStartScreenPrompt,
    ChatKitSupportedModel,
    Workflow,
    WorkflowChatKitConfig,
)
from orcheo_backend.app.repository.chatkit import (
    apply_chatkit_start_screen_prompts_update,
    apply_chatkit_supported_models_update,
)


def _base_prompt(label: str, prompt: str) -> ChatKitStartScreenPrompt:
    return ChatKitStartScreenPrompt(label=label, prompt=prompt)


def _base_model(identifier: str) -> ChatKitSupportedModel:
    return ChatKitSupportedModel(id=identifier)


def _dump_payload(items: list[ChatKitStartScreenPrompt]) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in items]


def _dump_models(models: list[ChatKitSupportedModel]) -> list[dict[str, object]]:
    return [model.model_dump(mode="json") for model in models]


def test_apply_chatkit_start_screen_prompts_update_records_change() -> None:
    workflow = Workflow(
        name="Prompts",
        chatkit=WorkflowChatKitConfig(
            start_screen_prompts=[_base_prompt("Hello", "Hi")]
        ),
    )
    metadata: dict[str, object] = {}
    next_prompts = [_base_prompt("World", "Hello world")]
    original_payload = _dump_payload(workflow.chatkit.start_screen_prompts or [])

    apply_chatkit_start_screen_prompts_update(
        workflow,
        metadata,
        chatkit_start_screen_prompts=next_prompts,
    )

    assert metadata["chatkit_start_screen_prompts"] == {
        "from": original_payload,
        "to": _dump_payload(next_prompts),
    }
    assert workflow.chatkit is not None
    assert workflow.chatkit.start_screen_prompts is not None
    assert workflow.chatkit.start_screen_prompts[0].prompt == "Hello world"


def test_apply_chatkit_start_screen_prompts_update_clears_values() -> None:
    workflow = Workflow(
        name="Prompts",
        chatkit=WorkflowChatKitConfig(
            start_screen_prompts=[_base_prompt("Hello", "Hi")]
        ),
    )
    metadata: dict[str, object] = {}

    apply_chatkit_start_screen_prompts_update(
        workflow,
        metadata,
        clear_chatkit_start_screen_prompts=True,
    )

    assert metadata["chatkit_start_screen_prompts"] == {
        "from": _dump_payload([ChatKitStartScreenPrompt(label="Hello", prompt="Hi")]),
        "to": None,
    }
    assert workflow.chatkit is None


def test_apply_chatkit_start_screen_prompts_update_keeps_supported_models() -> None:
    workflow = Workflow(
        name="Prompts",
        chatkit=WorkflowChatKitConfig(
            start_screen_prompts=[_base_prompt("Hello", "Hi")],
            supported_models=[_base_model("a")],
        ),
    )
    metadata: dict[str, object] = {}

    apply_chatkit_start_screen_prompts_update(
        workflow,
        metadata,
        clear_chatkit_start_screen_prompts=True,
    )

    assert metadata["chatkit_start_screen_prompts"] == {
        "from": _dump_payload([ChatKitStartScreenPrompt(label="Hello", prompt="Hi")]),
        "to": None,
    }
    assert workflow.chatkit is not None
    assert workflow.chatkit.start_screen_prompts is None
    assert workflow.chatkit.supported_models is not None


def test_apply_chatkit_start_screen_prompts_update_noop_without_prompts() -> None:
    workflow = Workflow(
        name="Prompts",
        chatkit=WorkflowChatKitConfig(supported_models=[_base_model("a")]),
    )
    metadata: dict[str, object] = {}

    apply_chatkit_start_screen_prompts_update(
        workflow,
        metadata,
        clear_chatkit_start_screen_prompts=True,
    )

    assert metadata == {}
    assert workflow.chatkit is not None


def test_apply_chatkit_start_screen_prompts_update_ignores_matching_payload() -> None:
    workflow = Workflow(name="Prompts")
    existing = _base_prompt("Hello", "Hi")
    workflow.chatkit = WorkflowChatKitConfig(start_screen_prompts=[existing])
    metadata: dict[str, object] = {}

    apply_chatkit_start_screen_prompts_update(
        workflow,
        metadata,
        chatkit_start_screen_prompts=[_base_prompt("Hello", "Hi")],
    )

    assert metadata == {}
    assert workflow.chatkit is not None


def test_apply_chatkit_supported_models_update_records_change() -> None:
    workflow = Workflow(
        name="Models",
        chatkit=WorkflowChatKitConfig(supported_models=[_base_model("a")]),
    )
    metadata: dict[str, object] = {}
    next_models = [_base_model("b")]
    original_models_payload = _dump_models(workflow.chatkit.supported_models or [])

    apply_chatkit_supported_models_update(
        workflow,
        metadata,
        chatkit_supported_models=next_models,
    )

    assert metadata["chatkit_supported_models"] == {
        "from": original_models_payload,
        "to": _dump_models(next_models),
    }
    assert workflow.chatkit is not None
    assert workflow.chatkit.supported_models is not None
    assert workflow.chatkit.supported_models[0].id == "b"


def test_apply_chatkit_supported_models_update_clears_values() -> None:
    workflow = Workflow(
        name="Models",
        chatkit=WorkflowChatKitConfig(supported_models=[_base_model("a")]),
    )
    metadata: dict[str, object] = {}

    apply_chatkit_supported_models_update(
        workflow,
        metadata,
        clear_chatkit_supported_models=True,
    )

    assert metadata["chatkit_supported_models"] == {
        "from": _dump_models([ChatKitSupportedModel(id="a")]),
        "to": None,
    }
    assert workflow.chatkit is None


def test_apply_chatkit_supported_models_update_keeps_start_screen_prompts() -> None:
    workflow = Workflow(
        name="Models",
        chatkit=WorkflowChatKitConfig(
            start_screen_prompts=[_base_prompt("Hello", "Hi")],
            supported_models=[_base_model("a")],
        ),
    )
    metadata: dict[str, object] = {}

    apply_chatkit_supported_models_update(
        workflow,
        metadata,
        clear_chatkit_supported_models=True,
    )

    assert metadata["chatkit_supported_models"] == {
        "from": _dump_models([ChatKitSupportedModel(id="a")]),
        "to": None,
    }
    assert workflow.chatkit is not None
    assert workflow.chatkit.supported_models is None
    assert workflow.chatkit.start_screen_prompts is not None


def test_apply_chatkit_supported_models_update_noop_without_models() -> None:
    workflow = Workflow(
        name="Models",
        chatkit=WorkflowChatKitConfig(
            start_screen_prompts=[_base_prompt("Hello", "Hi")]
        ),
    )
    metadata: dict[str, object] = {}

    apply_chatkit_supported_models_update(
        workflow,
        metadata,
        clear_chatkit_supported_models=True,
    )

    assert metadata == {}
    assert workflow.chatkit is not None


def test_apply_chatkit_supported_models_update_ignores_matching_payload() -> None:
    workflow = Workflow(name="Models")
    existing = _base_model("a")
    workflow.chatkit = WorkflowChatKitConfig(supported_models=[existing])
    metadata: dict[str, object] = {}

    apply_chatkit_supported_models_update(
        workflow,
        metadata,
        chatkit_supported_models=[_base_model("a")],
    )

    assert metadata == {}
    assert workflow.chatkit is not None
