"""Tests for human input logic nodes."""

import pytest

from orcheo.nodes.logic import HumanInputNode
from orcheo.nodes.logic import human_input as human_input_module


@pytest.mark.asyncio
async def test_human_input_node_interrupts_with_configured_payload(monkeypatch):
    """HumanInputNode should publish the interrupt response as user state."""
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
    assert "inputs" not in result
    assert result["messages"] == [{"role": "user", "content": "42"}]
    assert result["node_results"] == {"ask_human": {"response": "42"}}


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
    assert "inputs" not in result
    assert result["messages"] == [{"role": "user", "content": "continue"}]
    assert result["node_results"] == {"ask_human": {"response": "continue"}}


@pytest.mark.asyncio
async def test_human_input_node_does_not_mutate_graph_inputs(monkeypatch):
    """HumanInputNode should publish resumed input without rewriting graph inputs."""
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
    assert "inputs" not in result
    assert result["messages"] == [{"role": "user", "content": "7"}]
    assert result["node_results"] == {"ask_human": {"response": 7}}


@pytest.mark.asyncio
async def test_human_input_node_omits_messages_when_response_has_no_text(
    monkeypatch,
):
    """No chat message is published when the response carries no text content."""

    def fake_interrupt(payload):
        del payload
        return {"choice": "approve"}

    monkeypatch.setattr(human_input_module, "interrupt", fake_interrupt)

    result = await HumanInputNode(name="ask_human", prompt="Approve?")({}, {})

    assert "messages" not in result
    assert result["node_results"] == {"ask_human": {"response": {"choice": "approve"}}}


@pytest.mark.asyncio
async def test_human_input_node_extracts_message_from_mapping_response(monkeypatch):
    """A mapping response nesting a text field is surfaced as a chat message."""

    def fake_interrupt(payload):
        del payload
        return {"content": "  looks good  "}

    monkeypatch.setattr(human_input_module, "interrupt", fake_interrupt)

    result = await HumanInputNode(name="ask_human", prompt="Approve?")({}, {})

    assert result["messages"] == [{"role": "user", "content": "looks good"}]
