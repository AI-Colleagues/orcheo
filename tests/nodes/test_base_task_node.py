"""Tests covering TaskNode behavior and variable decoding."""

from __future__ import annotations
from types import MappingProxyType
from typing import Any
import pytest
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.models import CredentialScope
from orcheo.nodes.base import TaskNode
from orcheo.runtime.credentials import CredentialResolver, credential_resolution
from orcheo.vault import InMemoryCredentialVault


class MockTaskNode(TaskNode):
    """Mock task node used in multiple tests."""

    input_var: str = Field(description="Input variable for testing")

    def __init__(self, name: str, input_var: str):
        super().__init__(name=name, input_var=input_var)

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        return {"result": self.input_var}

    def tool_run(self, *args: Any, **kwargs: Any) -> Any:
        return {"tool_result": args[0]}

    async def tool_arun(self, *args: Any, **kwargs: Any) -> Any:
        return {"async_tool_result": args[0]}


def test_decode_variables() -> None:
    state = State({"results": {"node1": {"data": {"value": "test_value"}}}})
    node = MockTaskNode(name="test", input_var="{{node1.data.value}}")
    node.decode_variables(state)
    assert node.input_var == "test_value"

    node = MockTaskNode(name="test", input_var="plain_text")
    node.decode_variables(state)
    assert node.input_var == "plain_text"


def test_decode_variables_with_results_prefix() -> None:
    state = State({"results": {"node1": {"value": "test_from_results"}}})
    node = MockTaskNode(name="test", input_var="{{results.node1.value}}")
    node.decode_variables(state)
    assert node.input_var == "test_from_results"


def test_decode_variables_reads_config_state() -> None:
    state = State({"results": {}, "config": {"threshold": 0.75}})
    node = MockTaskNode(name="test", input_var="{{config.threshold}}")

    node.decode_variables(state)

    assert node.input_var == 0.75


def test_decode_variables_resolves_credential_placeholder_from_config_state() -> None:
    vault = InMemoryCredentialVault()
    vault.create_credential(
        name="telegram_chat_id",
        provider="telegram",
        scopes=["bot"],
        secret="-1001234567890",
        actor="tester",
        scope=CredentialScope.unrestricted(),
    )
    resolver = CredentialResolver(vault)
    state = State(
        {
            "results": {},
            "messages": [],
            "inputs": {},
            "config": {"configurable": {"telegram_chat_id": "[[telegram_chat_id]]"}},
        }
    )
    node = MockTaskNode(
        name="test",
        input_var="{{config.configurable.telegram_chat_id}}",
    )

    with credential_resolution(resolver):
        node.decode_variables(state)

    assert node.input_var == "-1001234567890"


def test_decode_variables_does_not_inject_config_argument() -> None:
    state = State({"results": {}})
    node = MockTaskNode(name="test", input_var="{{config.limit}}")

    node.decode_variables(state, config={"limit": 5})

    assert node.input_var == "{{config.limit}}"
    assert "config" not in state


def test_decode_variables_keeps_existing_state_config() -> None:
    state = State({"results": {}, "config": {"limit": 9}})
    node = MockTaskNode(name="test", input_var="{{config.limit}}")

    node.decode_variables(state, config={"limit": 5})

    assert node.input_var == 9
    assert state["config"]["limit"] == 9


def test_decode_variables_non_dict_traversal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    state = State({"results": {"node1": "simple_string"}})
    node = MockTaskNode(name="test", input_var="{{node1.nested.value}}")
    with caplog.at_level("WARNING"):
        node.decode_variables(state)
    assert node.input_var == "{{node1.nested.value}}"
    assert any(
        "could not resolve template" in message.lower()
        for _, _, message in caplog.record_tuples
    )


def test_decode_variables_nested_dict() -> None:
    class MockNodeWithDict(TaskNode):
        config: dict[str, Any] = Field(description="Config dictionary")

        async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
            return {"result": self.config}

    state = State({"results": {"prev_node": {"key1": "value1", "key2": "value2"}}})

    node = MockNodeWithDict(
        name="test",
        config={
            "param1": "{{prev_node.key1}}",
            "param2": "{{prev_node.key2}}",
            "static": "unchanged",
        },
    )
    node.decode_variables(state)

    assert node.config == {
        "param1": "value1",
        "param2": "value2",
        "static": "unchanged",
    }


def test_decode_variables_interpolates_multiple_templates_in_string() -> None:
    state = State({"results": {"user": {"name": "Ada", "id": 7}}})
    node = MockTaskNode(
        name="test",
        input_var="user={{user.name}}, id={{user.id}}",
    )

    node.decode_variables(state)

    assert node.input_var == "user=Ada, id=7"


def test_decode_variables_keeps_unresolved_templates_in_interpolated_string() -> None:
    state = State({"results": {"user": {"name": "Ada"}}})
    node = MockTaskNode(
        name="test",
        input_var="user={{user.name}}, org={{user.org}}",
    )

    node.decode_variables(state)

    assert node.input_var == "user=Ada, org={{user.org}}"


def test_decode_variables_single_template_keeps_native_type() -> None:
    class MockNodeWithDictValue(TaskNode):
        payload: Any = Field(description="Value for testing type preservation")

        async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
            return {"result": self.payload}

    state = State({"results": {"data": {"item": {"key": "value"}}}})
    node = MockNodeWithDictValue(name="test", payload="{{data.item}}")

    node.decode_variables(state)

    assert node.payload == {"key": "value"}
    assert isinstance(node.payload, dict)


def test_decode_variables_nested_list() -> None:
    class MockNodeWithList(TaskNode):
        items: list[Any] = Field(description="List of items")

        async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
            return {"result": self.items}

    state = State({"results": {"prev_node": {"val1": "a", "val2": "b"}}})

    node = MockNodeWithList(
        name="test",
        items=["{{prev_node.val1}}", "{{prev_node.val2}}", "static_value"],
    )
    node.decode_variables(state)

    assert node.items == ["a", "b", "static_value"]


def test_decode_variables_nested_tuple_set_and_mapping() -> None:
    class MockNodeWithContainers(TaskNode):
        payload: Any = Field(description="Container payload")

        async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
            return {"result": self.payload}

    state = State({"results": {"prev_node": {"val1": "a", "val2": "b"}}})
    node = MockNodeWithContainers(
        name="test",
        payload={
            "tuple": ("{{prev_node.val1}}", "{{prev_node.val2}}"),
            "set": {"{{prev_node.val1}}"},
            "frozenset": frozenset({"{{prev_node.val2}}"}),
            "mapping": MappingProxyType({"key": "{{prev_node.val1}}"}),
        },
    )

    node.decode_variables(state)

    assert node.payload["tuple"] == ("a", "b")
    assert node.payload["set"] == {"a"}
    assert node.payload["frozenset"] == frozenset({"b"})
    assert node.payload["mapping"] == {"key": "a"}


def test_decode_variables_with_pydantic_model() -> None:
    from pydantic import BaseModel

    class InnerModel(BaseModel):
        field1: str
        field2: str

    class MockNodeWithModel(TaskNode):
        model: InnerModel = Field(description="Inner model")

        async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
            return {"result": self.model.model_dump()}

    state = State({"results": {"data": {"x": "decoded_x", "y": "decoded_y"}}})

    inner = InnerModel(field1="{{data.x}}", field2="{{data.y}}")
    node = MockNodeWithModel(name="test", model=inner)
    node.decode_variables(state)

    assert node.model.field1 == "decoded_x"
    assert node.model.field2 == "decoded_y"


def test_task_node_tool_run() -> None:
    node = MockTaskNode(name="test", input_var="test_value")
    result = node.tool_run("test_arg")
    assert result == {"tool_result": "test_arg"}


@pytest.mark.asyncio
async def test_task_node_tool_arun() -> None:
    node = MockTaskNode(name="test", input_var="test_value")
    result = await node.tool_arun("test_arg")
    assert result == {"async_tool_result": "test_arg"}


@pytest.mark.asyncio
async def test_task_node_call() -> None:
    state = State({"results": {}})
    config = RunnableConfig()
    node = MockTaskNode(name="test_task", input_var="test_value")

    result = await node(state, config)
    assert result == {"results": {"test_task": {"result": "test_value"}}}


@pytest.mark.asyncio
async def test_task_node_call_hoists_reserved_state_fields() -> None:
    class StateUpdatingNode(TaskNode):
        async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
            del state, config
            return {
                "inputs": {"message": "hello"},
                "messages": [{"role": "user", "content": "hello"}],
                "result": "kept",
            }

    node = StateUpdatingNode(name="state_updater")

    result = await node(State({"results": {}}), RunnableConfig())

    assert result == {
        "inputs": {"message": "hello"},
        "messages": [{"role": "user", "content": "hello"}],
        "results": {"state_updater": {"result": "kept"}},
    }


@pytest.mark.asyncio
async def test_task_node_call_re_resolves_templates_per_invocation() -> None:
    node = MockTaskNode(name="test_task", input_var="{{payload.value}}")

    first = await node(
        State({"results": {"payload": {"value": "first"}}}),
        RunnableConfig(),
    )
    second = await node(
        State({"results": {"payload": {"value": "second"}}}),
        RunnableConfig(),
    )

    assert first == {"results": {"test_task": {"result": "first"}}}
    assert second == {"results": {"test_task": {"result": "second"}}}
    assert node.input_var == "{{payload.value}}"
