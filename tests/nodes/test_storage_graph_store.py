"""Tests for get_graph_store() and GraphStoreAppendMessageNode."""

from __future__ import annotations
from types import SimpleNamespace
from typing import Any, cast
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.storage import GraphStoreAppendMessageNode, get_graph_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockStore:
    """Minimal async store for testing."""

    def __init__(
        self,
        existing_item: Any = None,
        *,
        aget_error: bool = False,
        aput_error: bool = False,
    ) -> None:
        self.existing_item = existing_item
        self.aget_error = aget_error
        self.aput_error = aput_error
        self.put_calls: list[tuple[tuple[str, ...], str, dict[str, Any]]] = []

    async def aget(self, namespace: tuple[str, ...], key: str) -> Any:
        if self.aget_error:
            raise RuntimeError("store read error")
        return self.existing_item

    async def aput(
        self, namespace: tuple[str, ...], key: str, value: dict[str, Any]
    ) -> None:
        if self.aput_error:
            raise RuntimeError("store write error")
        self.put_calls.append((namespace, key, value))


class MockItem:
    """Mimics a LangGraph store Item with a .value attribute."""

    def __init__(self, value: Any) -> None:
        self.value = value


def _config_with_store(store: Any) -> RunnableConfig:
    return RunnableConfig(configurable={"__pregel_store": store})


# ---------------------------------------------------------------------------
# get_graph_store() tests
# ---------------------------------------------------------------------------


class TestGetGraphStore:
    def test_returns_none_for_none_config(self) -> None:
        assert get_graph_store(None) is None

    def test_returns_none_for_non_mapping_configurable(self) -> None:
        assert get_graph_store({"configurable": "bad"}) is None

    def test_returns_none_when_nothing_available(self) -> None:
        assert get_graph_store({"configurable": {}}) is None

    def test_finds_store_in_runtime_mapping(self) -> None:
        sentinel = object()
        config = {"configurable": {"__pregel_runtime": {"store": sentinel}}}
        assert get_graph_store(config) is sentinel

    def test_finds_store_via_runtime_attribute(self) -> None:
        sentinel = object()
        runtime = SimpleNamespace(store=sentinel)
        config = {"configurable": {"__pregel_runtime": runtime}}
        assert get_graph_store(config) is sentinel

    def test_falls_back_to_pregel_store(self) -> None:
        sentinel = object()
        config = {"configurable": {"__pregel_store": sentinel}}
        assert get_graph_store(config) is sentinel

    def test_skips_none_runtime_store_and_falls_back(self) -> None:
        sentinel = object()
        config = {
            "configurable": {
                "__pregel_runtime": {"store": None},
                "__pregel_store": sentinel,
            }
        }
        assert get_graph_store(config) is sentinel

    def test_runtime_mapping_takes_priority_over_pregel_store(self) -> None:
        runtime_store = object()
        fallback_store = object()
        config = {
            "configurable": {
                "__pregel_runtime": {"store": runtime_store},
                "__pregel_store": fallback_store,
            }
        }
        assert get_graph_store(config) is runtime_store


# ---------------------------------------------------------------------------
# GraphStoreAppendMessageNode._extract_payload() tests
# ---------------------------------------------------------------------------


class TestExtractPayload:
    def test_none_item_returns_fresh_payload(self) -> None:
        result = GraphStoreAppendMessageNode._extract_payload(None)
        assert result == {"version": 0, "messages": []}

    def test_item_with_attribute_value(self) -> None:
        item = MockItem({"version": 5, "messages": [{"role": "user", "content": "hi"}]})
        result = GraphStoreAppendMessageNode._extract_payload(item)
        assert result["version"] == 5
        assert len(result["messages"]) == 1

    def test_item_with_mapping_value(self) -> None:
        item = {"value": {"version": 2, "messages": []}}
        result = GraphStoreAppendMessageNode._extract_payload(item)
        assert result["version"] == 2
        assert result["messages"] == []

    def test_non_dict_value_returns_fresh_payload(self) -> None:
        item = MockItem("not a dict")
        result = GraphStoreAppendMessageNode._extract_payload(item)
        assert result == {"version": 0, "messages": []}

    def test_missing_keys_get_defaults(self) -> None:
        item = MockItem({})
        result = GraphStoreAppendMessageNode._extract_payload(item)
        assert result["version"] == 0
        assert result["messages"] == []

    def test_invalid_types_get_sanitized_defaults(self) -> None:
        item = MockItem({"version": "1", "messages": {"role": "user"}})
        result = GraphStoreAppendMessageNode._extract_payload(item)
        assert result == {"version": 0, "messages": []}


# ---------------------------------------------------------------------------
# GraphStoreAppendMessageNode._namespace_tuple() tests
# ---------------------------------------------------------------------------


class TestNamespaceTuple:
    def test_default_namespace(self) -> None:
        node = GraphStoreAppendMessageNode(name="t", key="k", content="c")
        assert node._namespace_tuple() == ("agent_chat_history",)

    def test_custom_namespace(self) -> None:
        node = GraphStoreAppendMessageNode(
            name="t", key="k", content="c", namespace=["custom", "ns"]
        )
        assert node._namespace_tuple() == ("custom", "ns")

    def test_filters_blanks(self) -> None:
        node = GraphStoreAppendMessageNode(
            name="t", key="k", content="c", namespace=["", " ", "valid"]
        )
        assert node._namespace_tuple() == ("valid",)

    def test_empty_namespace_defaults(self) -> None:
        node = GraphStoreAppendMessageNode(name="t", key="k", content="c", namespace=[])
        assert node._namespace_tuple() == ("agent_chat_history",)

    def test_workspace_prefixes_namespace(self) -> None:
        node = GraphStoreAppendMessageNode(name="t", key="k", content="c")
        assert node._namespace_tuple("workspace-1") == (
            "workspace-1",
            "agent_chat_history",
        )


# ---------------------------------------------------------------------------
# GraphStoreAppendMessageNode.run() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_store_returns_false() -> None:
    node = GraphStoreAppendMessageNode(name="t", key="some_key", content="hello")
    state = State({"results": {}})
    result = await node.run(state, RunnableConfig())
    assert result == {"history_written": False}


@pytest.mark.asyncio
async def test_empty_key_returns_false() -> None:
    store = MockStore()
    node = GraphStoreAppendMessageNode(name="t", key="", content="hello")
    state = State({"results": {}})
    result = await node.run(state, _config_with_store(store))
    assert result == {"history_written": False}


@pytest.mark.asyncio
async def test_empty_content_returns_false() -> None:
    store = MockStore()
    node = GraphStoreAppendMessageNode(name="t", key="some_key", content="")
    state = State({"results": {}})
    result = await node.run(state, _config_with_store(store))
    assert result == {"history_written": False}


@pytest.mark.asyncio
async def test_unresolved_template_in_key_returns_false() -> None:
    store = MockStore()
    node = GraphStoreAppendMessageNode(
        name="t", key="telegram:{{unresolved}}", content="hello"
    )
    state = State({"results": {}})
    result = await node.run(state, _config_with_store(store))
    assert result == {"history_written": False}


@pytest.mark.asyncio
async def test_non_string_key_coerces_to_string() -> None:
    store = MockStore(existing_item=None)
    node = GraphStoreAppendMessageNode(name="t", key="placeholder", content="digest")
    node.key = cast(Any, 123)
    state = State({"results": {}})

    result = await node.run(state, _config_with_store(store))

    assert result == {"history_written": True}
    _, key, _ = store.put_calls[0]
    assert key == "123"


@pytest.mark.asyncio
async def test_none_key_returns_false() -> None:
    store = MockStore()
    node = GraphStoreAppendMessageNode(name="t", key="placeholder", content="hello")
    node.key = cast(Any, None)
    state = State({"results": {}})
    result = await node.run(state, _config_with_store(store))
    assert result == {"history_written": False}


@pytest.mark.asyncio
async def test_non_coercible_key_returns_false() -> None:
    store = MockStore()
    node = GraphStoreAppendMessageNode(name="t", key="placeholder", content="hello")
    node.key = cast(Any, ["not", "a", "string"])
    state = State({"results": {}})
    result = await node.run(state, _config_with_store(store))
    assert result == {"history_written": False}


@pytest.mark.asyncio
async def test_creates_new_entry() -> None:
    store = MockStore(existing_item=None)
    node = GraphStoreAppendMessageNode(name="t", key="telegram:123", content="digest")
    state = State({"results": {}})
    result = await node.run(state, _config_with_store(store))

    assert result == {"history_written": True}
    assert len(store.put_calls) == 1
    ns, key, payload = store.put_calls[0]
    assert ns == ("agent_chat_history",)
    assert key == "telegram:123"
    assert payload["version"] == 1
    assert payload["messages"] == [{"role": "assistant", "content": "digest"}]


@pytest.mark.asyncio
async def test_creates_workspace_namespaced_entry() -> None:
    store = MockStore(existing_item=None)
    node = GraphStoreAppendMessageNode(name="t", key="telegram:123", content="digest")
    state = State({"results": {}, "workspace_id": "workspace-1"})
    result = await node.run(state, _config_with_store(store))

    assert result == {"history_written": True}
    ns, key, payload = store.put_calls[0]
    assert ns == ("workspace-1", "agent_chat_history")
    assert key == "telegram:123"
    assert payload["version"] == 1


@pytest.mark.asyncio
async def test_appends_to_existing_entry() -> None:
    existing = MockItem({"version": 3, "messages": [{"role": "user", "content": "hi"}]})
    store = MockStore(existing_item=existing)
    node = GraphStoreAppendMessageNode(name="t", key="telegram:456", content="news")
    state = State({"results": {}})
    result = await node.run(state, _config_with_store(store))

    assert result == {"history_written": True}
    ns, key, payload = store.put_calls[0]
    assert payload["version"] == 4
    assert len(payload["messages"]) == 2
    assert payload["messages"][0] == {"role": "user", "content": "hi"}
    assert payload["messages"][1] == {"role": "assistant", "content": "news"}


@pytest.mark.asyncio
async def test_custom_role() -> None:
    store = MockStore(existing_item=None)
    node = GraphStoreAppendMessageNode(name="t", key="k", content="msg", role="user")
    state = State({"results": {}})
    await node.run(state, _config_with_store(store))

    _, _, payload = store.put_calls[0]
    assert payload["messages"][0]["role"] == "user"


@pytest.mark.asyncio
async def test_custom_namespace() -> None:
    store = MockStore(existing_item=None)
    node = GraphStoreAppendMessageNode(
        name="t", key="k", content="msg", namespace=["my_ns"]
    )
    state = State({"results": {}})
    await node.run(state, _config_with_store(store))

    ns, _, _ = store.put_calls[0]
    assert ns == ("my_ns",)


@pytest.mark.asyncio
async def test_aget_exception_returns_false() -> None:
    store = MockStore(aget_error=True)
    node = GraphStoreAppendMessageNode(name="t", key="k", content="msg")
    state = State({"results": {}})
    result = await node.run(state, _config_with_store(store))
    assert result == {"history_written": False}
    assert len(store.put_calls) == 0


@pytest.mark.asyncio
async def test_aput_exception_returns_false() -> None:
    store = MockStore(aput_error=True)
    node = GraphStoreAppendMessageNode(name="t", key="k", content="msg")
    state = State({"results": {}})
    result = await node.run(state, _config_with_store(store))
    assert result == {"history_written": False}


@pytest.mark.asyncio
async def test_invalid_existing_payload_types_do_not_fail() -> None:
    existing = MockItem({"version": "4", "messages": "bad"})
    store = MockStore(existing_item=existing)
    node = GraphStoreAppendMessageNode(name="t", key="k", content="msg")
    state = State({"results": {}})

    result = await node.run(state, _config_with_store(store))

    assert result == {"history_written": True}
    _, _, payload = store.put_calls[0]
    assert payload["version"] == 1
    assert payload["messages"] == [{"role": "assistant", "content": "msg"}]
