"""Tests for human input logic nodes."""

import pytest

from orcheo.nodes.logic import HumanInputNode
from orcheo.nodes.logic import human_input as human_input_module


@pytest.mark.asyncio
async def test_human_input_node_interrupts_with_configured_payload(monkeypatch):
    """HumanInputNode should wrap the interrupt response in TaskNode results."""
    observed_payloads = []

    def fake_interrupt(payload):
        observed_payloads.append(payload)
        return "42"

    monkeypatch.setattr(human_input_module, "interrupt", fake_interrupt)

    node = HumanInputNode(
        name="ask_human",
        prompt="Pick a number.",
        kind="guess",
        expected={"type": "integer", "minimum": 0, "maximum": 100},
    )

    result = await node({}, {})

    assert observed_payloads == [
        {
            "message": "Pick a number.",
            "kind": "guess",
            "expected": {"type": "integer", "minimum": 0, "maximum": 100},
        }
    ]
    assert result["inputs"] == {"message": "42"}
    assert "messages" not in result
    assert result["results"] == {"ask_human": {"response": "42"}}


@pytest.mark.asyncio
async def test_human_input_node_omits_expected_when_not_configured(monkeypatch):
    """HumanInputNode should keep the interrupt payload minimal by default."""
    observed_payloads = []

    def fake_interrupt(payload):
        observed_payloads.append(payload)
        return "continue"

    monkeypatch.setattr(human_input_module, "interrupt", fake_interrupt)

    result = await HumanInputNode(name="ask_human", prompt="Continue?")({}, {})

    assert observed_payloads == [{"message": "Continue?", "kind": "human"}]
    assert result["inputs"] == {"message": "continue"}
    assert "messages" not in result
    assert result["results"] == {"ask_human": {"response": "continue"}}


@pytest.mark.asyncio
async def test_human_input_node_preserves_existing_inputs(monkeypatch):
    """HumanInputNode should replace only the current message input on resume."""
    observed_payloads = []

    def fake_interrupt(payload):
        observed_payloads.append(payload)
        return 7

    monkeypatch.setattr(human_input_module, "interrupt", fake_interrupt)

    result = await HumanInputNode(name="ask_human", prompt="Guess?")(
        {"inputs": {"message": "old", "thread_id": "thread-1"}},
        {},
    )

    assert observed_payloads == [{"message": "Guess?", "kind": "human"}]
    assert result["inputs"] == {"message": "7", "thread_id": "thread-1"}
    assert "messages" not in result
    assert result["results"] == {"ask_human": {"response": 7}}
