"""Prompt handling tests for the AgentNode."""

from __future__ import annotations
from types import SimpleNamespace
import pytest
from agentensor.tensor import TextTensor
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode


@pytest.fixture(autouse=True)
def _patch_agentensor_tensor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agentensor.tensor.init_chat_model", lambda *_, **__: SimpleNamespace()
    )


def test_model_post_init_converts_prompt_for_compound_model() -> None:
    node = AgentNode(
        name="agent",
        ai_model="provider:model",
        system_prompt="Basic prompt",
        model_kwargs={"foo": "bar", "model_provider": "provider"},
    )

    node.model_post_init({})

    assert isinstance(node.system_prompt, TextTensor)
    assert node.system_prompt.text == "Basic prompt"


def test_model_post_init_skips_template_prompt() -> None:
    node = AgentNode(name="agent", ai_model="provider:model", system_prompt="{{value}}")

    node.model_post_init({})

    assert isinstance(node.system_prompt, str)
    assert node.system_prompt == "{{value}}"


def test_get_params_returns_trainable_tensor_when_grad_enabled() -> None:
    node = AgentNode(name="agent", ai_model="provider:model")
    node.system_prompt = TextTensor(
        text="train", requires_grad=True, model=SimpleNamespace()
    )

    params = node.get_params()

    assert len(params) == 1
    assert params[0] is node.system_prompt


def test_get_params_skips_non_trainable_system_prompt() -> None:
    node = AgentNode(name="agent", ai_model="provider:model")
    node.system_prompt = TextTensor(
        text="frozen", requires_grad=False, model=SimpleNamespace()
    )

    assert node.get_params() == []


def test_model_dump_serializes_text_tensor_prompt() -> None:
    node = AgentNode(name="agent", ai_model="provider:model")
    node.system_prompt = TextTensor(
        text="serialized", requires_grad=True, model=SimpleNamespace()
    )

    payload = node.model_dump(mode="json")

    assert payload["system_prompt"] == "serialized"


def test_model_dump_preserves_string_prompt() -> None:
    node = AgentNode(
        name="agent",
        ai_model="simple-model",
        system_prompt="literal prompt",
    )

    assert isinstance(node.system_prompt, str)

    payload = node.model_dump(mode="json")

    assert payload["system_prompt"] == "literal prompt"


def test_decode_variables_interpolates_mixed_system_prompt_templates() -> None:
    node = AgentNode(
        name="agent",
        ai_model="provider:model",
        system_prompt=(
            "internal={{resolve_attendee_id.internal_user_id}} "
            "external={{resolve_attendee_id.external_userid}}"
        ),
    )
    state = State(
        {
            "node_results": {
                "resolve_attendee_id": {
                    "internal_user_id": "alice",
                    "external_userid": "wxid_42",
                }
            }
        }
    )

    node.decode_variables(state)

    assert node.system_prompt == "internal=alice external=wxid_42"


def test_compute_run_updates_skips_deferred_fields_and_decodes_others() -> None:
    """_compute_run_updates omits history_key_template/candidates and resolves
    other template fields such as system_prompt (lines 201-208)."""
    node = AgentNode(
        name="agent",
        ai_model="test-model",
        # history_key_template contains {{ so stays as str, but is deferred
        history_key_template="{{inputs.ctx}}",
        # system_prompt also contains {{ so stays as str, and is NOT deferred
        system_prompt="user:{{inputs.ctx}}",
    )
    state = State(
        {
            "inputs": {"ctx": "my-ctx"},
            "node_results": {},
        }
    )

    updates = node._compute_run_updates(state)

    # Deferred fields must not appear in updates
    assert "history_key_template" not in updates
    assert "history_key_candidates" not in updates
    # Non-deferred template field is decoded
    assert updates.get("system_prompt") == "user:my-ctx"
