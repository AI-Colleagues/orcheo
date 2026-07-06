"""Tests for AI node behavior."""

from __future__ import annotations
import pytest
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import AINode


class MockAINode(AINode):
    input_var: str = Field(description="Input variable for testing")

    def __init__(self, name: str, input_var: str):
        super().__init__(name=name, input_var=input_var)

    async def run(self, state: State, config: RunnableConfig) -> dict[str, str]:
        return {"messages": {"result": self.input_var}}  # type: ignore[return-value]


class MockModelOverrideNode(AINode):
    ai_model: str | None = Field(default=None)

    def __init__(self, name: str, ai_model: str | None):
        super().__init__(name=name, ai_model=ai_model)

    async def run(self, state: State, config: RunnableConfig) -> dict[str, str | None]:
        del state, config
        return {"messages": {"model": self.ai_model}}  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_ai_node_call() -> None:
    state = State({"node_results": {}})
    config = RunnableConfig()
    node = MockAINode(name="test_ai", input_var="test_value")

    result = await node(state, config)

    assert result == {"messages": {"result": "test_value"}}


@pytest.mark.asyncio
async def test_ai_node_call_re_resolves_templates_per_invocation() -> None:
    node = MockAINode(name="test_ai", input_var="{{payload.value}}")

    first = await node(
        State({"node_results": {"payload": {"value": "first"}}}),
        RunnableConfig(),
    )
    second = await node(
        State({"node_results": {"payload": {"value": "second"}}}),
        RunnableConfig(),
    )

    assert first == {"messages": {"result": "first"}}
    assert second == {"messages": {"result": "second"}}
    assert node.input_var == "{{payload.value}}"


@pytest.mark.asyncio
async def test_ai_node_call_applies_chatkit_model_override_per_run() -> None:
    node = MockModelOverrideNode(name="test_ai_model", ai_model="openai:gpt-4o-mini")

    result = await node(
        State({"node_results": {}}),
        RunnableConfig(configurable={"chatkit_model": "openai:gpt-5"}),
    )

    assert result["messages"] == {"model": "openai:gpt-5"}
    assert result["__trace"]["ai"]["kind"] == "llm"
    assert result["__trace"]["ai"]["requested_model"] == "openai:gpt-5"
    assert result["__trace"]["ai"]["provider"] == "openai"
    assert node.ai_model == "openai:gpt-4o-mini"
