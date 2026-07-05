"""Tests for Agentensor config shims."""

from __future__ import annotations
from typing import cast
from unittest.mock import MagicMock, patch
from pydantic import Field
from orcheo.agentensor.prompts import (
    TrainablePrompt,
    TrainablePrompts,
    build_text_tensors,
)
from orcheo.graph.state import State
from orcheo.nodes.base import BaseRunnable
from orcheo.runtime.credentials import CredentialResolver, credential_resolution
from tests.runtime.credential_test_helpers import create_vault_with_secret


class PromptRunnable(BaseRunnable):
    """Minimal runnable used to exercise prompt decoding."""

    prompts: TrainablePrompts = Field(default_factory=dict)


def test_trainable_prompt_decodes_with_state_values() -> None:
    """Trainable prompts should resolve templates via BaseRunnable helpers."""
    runnable = PromptRunnable(
        name="trainer",
        prompts={
            "welcome": TrainablePrompt(
                text="{{inputs.prompt_text}}",
                requires_grad=True,
                metadata={"lang": "{{inputs.lang}}"},
            )
        },
    )
    state = cast(
        State,
        {
            "inputs": {"prompt_text": "Hello there", "lang": "en"},
            "node_results": {},
            "structured_response": {},
        },
    )

    runnable.decode_variables(state)

    prompt = runnable.prompts["welcome"]
    assert prompt.text == "Hello there"
    assert prompt.metadata["lang"] == "en"
    assert prompt.requires_grad is True


def test_trainable_prompt_decodes_model_kwargs_credentials() -> None:
    """Trainable prompts should resolve credential placeholders in model kwargs."""
    runnable = PromptRunnable(
        name="trainer",
        prompts={
            "welcome": TrainablePrompt(
                text="Hello there",
                model_kwargs={"api_key": "[[telegram_token]]"},
            )
        },
    )
    state = cast(
        State,
        {
            "inputs": {},
            "node_results": {},
            "structured_response": {},
        },
    )
    vault = create_vault_with_secret(secret="token")
    resolver = CredentialResolver(vault)

    with credential_resolution(resolver):
        runnable.decode_variables(state)

    prompt = runnable.prompts["welcome"]
    assert prompt.model_kwargs["api_key"] == "token"


@patch("agentensor.tensor.init_chat_model")
def test_build_text_tensors_converts_configs(mock_init_chat_model: MagicMock) -> None:
    """Building runtime tensors should keep prompt metadata and flags."""
    mock_init_chat_model.return_value = MagicMock()
    prompt = TrainablePrompt(
        text="You are a helpful agent.",
        requires_grad=True,
        metadata={"tone": "formal"},
        model_kwargs={"api_key": "token"},
    )

    tensors = build_text_tensors({"system_prompt": prompt}, model="gpt-test")

    assert set(tensors) == {"system_prompt"}
    tensor = tensors["system_prompt"]
    assert tensor.text == "You are a helpful agent."
    assert tensor.requires_grad is True
    assert tensor.metadata == {"tone": "formal"}
    mock_init_chat_model.assert_called_once_with("gpt-test", api_key="token")


def test_build_text_tensors_handles_empty_mapping() -> None:
    """Empty inputs should return an empty tensor mapping."""
    assert build_text_tensors({}) == {}
    assert build_text_tensors(None) == {}
