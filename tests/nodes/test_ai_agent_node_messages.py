"""AgentNode message construction tests."""

from __future__ import annotations
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode


@pytest.mark.asyncio
async def test_agentnode_builds_messages_from_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AgentNode should convert ChatKit inputs into LangChain messages."""

    fake_agent = AsyncMock()
    fake_agent.ainvoke.return_value = {"messages": [AIMessage(content="done")]}

    async def fake_prepare_tools(self: AgentNode):  # type: ignore[unused-argument]
        return []

    def fake_init_chat_model(*args, **kwargs):
        return "model"

    def fake_create_agent(model, tools, system_prompt=None, response_format=None):
        return fake_agent

    monkeypatch.setattr("orcheo.nodes.ai.init_chat_model", fake_init_chat_model)
    monkeypatch.setattr("orcheo.nodes.ai.create_agent", fake_create_agent)
    monkeypatch.setattr(AgentNode, "_prepare_tools", fake_prepare_tools)

    node = AgentNode(name="agent", ai_model="test-model", system_prompt="sys-prompt")
    state = State(
        inputs={
            "message": "How can you help?",
            "history": [
                {"role": "assistant", "content": "Welcome back!"},
                {"role": "user", "content": "Remind me what you can do."},
            ],
        },
        results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result == {"messages": [AIMessage(content="done")]}
    payload = fake_agent.ainvoke.await_args.args[0]
    messages = payload["messages"]
    assert isinstance(messages[0], AIMessage)
    assert isinstance(messages[1], HumanMessage)
    assert isinstance(messages[2], HumanMessage)
    assert messages[0].content == "Welcome back!"
    assert messages[1].content == "Remind me what you can do."
    assert messages[2].content == "How can you help?"


@pytest.mark.asyncio
async def test_agentnode_prefers_existing_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit messages should be used when provided."""

    fake_agent = AsyncMock()
    fake_agent.ainvoke.return_value = {"messages": [AIMessage(content="done")]}

    async def fake_prepare_tools(self: AgentNode):  # type: ignore[unused-argument]
        return []

    def fake_init_chat_model(*args, **kwargs):
        return "model"

    def fake_create_agent(model, tools, system_prompt=None, response_format=None):
        return fake_agent

    monkeypatch.setattr("orcheo.nodes.ai.init_chat_model", fake_init_chat_model)
    monkeypatch.setattr("orcheo.nodes.ai.create_agent", fake_create_agent)
    monkeypatch.setattr(AgentNode, "_prepare_tools", fake_prepare_tools)

    node = AgentNode(name="agent", ai_model="test-model")
    state = State(
        messages=[{"role": "user", "content": "Use these messages instead."}],
        inputs={
            "message": "This message should not be appended",
            "history": [{"role": "assistant", "content": "ignored"}],
        },
        results={},
        structured_response=None,
    )

    await node.run(state, {})

    payload = fake_agent.ainvoke.await_args.args[0]
    messages = payload["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "Use these messages instead."


@pytest.mark.asyncio
async def test_agentnode_propagates_workspace_id_to_tool_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace id should reach nested workflow-tool runtime config."""

    fake_agent = AsyncMock()
    fake_agent.ainvoke.return_value = {"messages": [AIMessage(content="done")]}
    captured_runtime_config: dict[str, object] = {}

    async def fake_prepare_tools(self: AgentNode):  # type: ignore[unused-argument]
        return []

    def fake_init_chat_model(*args, **kwargs):
        return "model"

    def fake_create_agent(model, tools, system_prompt=None, response_format=None):
        return fake_agent

    def fake_tool_execution_context(config):
        captured_runtime_config.update(dict(config or {}))
        return contextlib.nullcontext()

    monkeypatch.setattr("orcheo.nodes.ai.init_chat_model", fake_init_chat_model)
    monkeypatch.setattr("orcheo.nodes.ai.create_agent", fake_create_agent)
    monkeypatch.setattr(AgentNode, "_prepare_tools", fake_prepare_tools)
    monkeypatch.setattr(
        "orcheo.nodes.ai.tool_execution_context", fake_tool_execution_context
    )

    node = AgentNode(name="agent", ai_model="test-model", system_prompt="sys-prompt")
    state = State(
        workspace_id="workspace-1",
        inputs={"message": "How can you help?"},
        results={},
        structured_response=None,
    )

    await node.run(state, {})

    assert captured_runtime_config["configurable"]["workspace_id"] == "workspace-1"
    assert fake_agent.ainvoke.await_args.args[1]["configurable"]["workspace_id"] == (
        "workspace-1"
    )


@pytest.mark.asyncio
async def test_agentnode_propagates_conversation_key_to_tool_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolved conversation keys should be available to workflow tools."""

    fake_agent = AsyncMock()
    fake_agent.ainvoke.return_value = {"messages": [AIMessage(content="done")]}
    captured_runtime_config: dict[str, object] = {}

    async def fake_prepare_tools(self: AgentNode):  # type: ignore[unused-argument]
        return []

    def fake_init_chat_model(*args, **kwargs):
        return "model"

    def fake_create_agent(model, tools, system_prompt=None, response_format=None):
        return fake_agent

    def fake_tool_execution_context(config):
        captured_runtime_config.update(dict(config or {}))
        return contextlib.nullcontext()

    monkeypatch.setattr("orcheo.nodes.ai.init_chat_model", fake_init_chat_model)
    monkeypatch.setattr("orcheo.nodes.ai.create_agent", fake_create_agent)
    monkeypatch.setattr(AgentNode, "_prepare_tools", fake_prepare_tools)
    monkeypatch.setattr(
        "orcheo.nodes.ai.tool_execution_context", fake_tool_execution_context
    )

    node = AgentNode(
        name="agent",
        ai_model="test-model",
        system_prompt="sys-prompt",
        use_graph_chat_history=True,
        history_key_candidates=["room-1"],
    )
    state = State(
        workspace_id="workspace-1",
        inputs={"message": "How can you help?"},
        results={},
        structured_response=None,
    )

    await node.run(state, {})

    assert captured_runtime_config["configurable"]["conversation_key"] == "room-1"
    assert fake_agent.ainvoke.await_args.args[1]["configurable"][
        "conversation_key"
    ] == ("room-1")


def test_messages_from_inputs_handles_history_and_prompt() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    inputs = {
        "history": [
            {"role": "assistant", "content": "Welcome!"},
            "not a mapping",
            {"role": "user", "content": "  "},
            {"role": "user", "content": "Tell me more"},
        ],
        "prompt": "   Add this prompt  ",
    }
    messages = node._messages_from_inputs(inputs)
    assert len(messages) == 3
    assert messages[0].content == "Welcome!"
    assert messages[1].content == "Tell me more"
    assert messages[2].content == "Add this prompt"


def test_normalize_messages_creates_base_messages() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    inputs = [
        AIMessage(content="existing"),
        {"role": "assistant", "content": "helper"},
        {"role": "other", "content": "fallback"},
        {"role": "user", "content": ""},
        123,
    ]
    normalized = node._normalize_messages(inputs)
    assert len(normalized) == 3
    assert normalized[0].content == "existing"
    assert normalized[1].content == "helper"
    assert normalized[2].content == "fallback"


def test_build_messages_uses_inputs_when_messages_absent() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    state = State(
        inputs={
            "message": "Fallback message",
            "history": [{"role": "assistant", "content": "Earlier"}],
        },
        results={},
        messages=[],
        structured_response=None,
    )
    messages = node._build_messages(state)
    assert len(messages) == 2
    assert messages[0].content == "Earlier"
    assert messages[1].content == "Fallback message"


def test_messages_from_inputs_prefers_user_message_over_prompt() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    inputs = {
        "history": [{"role": "assistant", "content": "Old"}],
        "user_message": "  user input  ",
        "prompt": "should be ignored",
    }
    messages = node._messages_from_inputs(inputs)
    assert messages[-1].content == "user input"


def test_messages_from_inputs_handles_query_value() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    inputs = {
        "query": "  q  ",
    }
    messages = node._messages_from_inputs(inputs)
    assert len(messages) == 1
    assert messages[0].content == "q"


def test_messages_from_inputs_handles_listener_payload_message() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    inputs = {
        "platform": "telegram",
        "message": {
            "chat_id": "12345",
            "text": "  hello from Telegram  ",
        },
        "listener": {
            "message": {
                "chat_id": "12345",
                "text": "hello from Telegram",
            }
        },
    }
    messages = node._messages_from_inputs(inputs)
    assert len(messages) == 1
    assert messages[0].content == "hello from Telegram"


def test_input_message_text_uses_listener_payload() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    inputs = {
        "listener": {
            "message": {"text": "  payload text  "},
        },
    }
    assert node._input_message_text(inputs) == "payload text"


def test_coerce_input_message_text_prefers_content_key() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    payload = {"text": 123, "content": "  inferred content  "}
    assert node._coerce_input_message_text(payload) == "inferred content"


def test_messages_from_inputs_skips_duplicate_latest_user_turn() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    inputs = {
        "history": [
            {"role": "assistant", "content": "Previous answer"},
            {"role": "user", "content": "Latest question"},
        ],
        "message": "  Latest question  ",
    }
    messages = node._messages_from_inputs(inputs)
    assert len(messages) == 2
    assert messages[0].content == "Previous answer"
    assert messages[1].content == "Latest question"


def test_messages_from_inputs_keeps_message_when_latest_turn_not_user() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    inputs = {
        "history": [{"role": "assistant", "content": "Latest question"}],
        "message": "Latest question",
    }
    messages = node._messages_from_inputs(inputs)
    assert len(messages) == 2
    assert messages[0].content == "Latest question"
    assert messages[1].content == "Latest question"


def test_build_messages_prefers_existing_state_messages() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    state = State(
        messages=[{"role": "assistant", "content": "Existing"}],
        inputs={"message": "ignored"},
        results={},
        structured_response=None,
    )
    messages = node._build_messages(state)
    assert len(messages) == 1
    assert messages[0].content == "Existing"


def test_apply_reset_command_filters_to_latest_reset() -> None:
    node = AgentNode(
        name="agent",
        ai_model="test-model",
        reset_command="RESET",
    )
    messages = [
        HumanMessage(content="before"),
        HumanMessage(content="RESET"),
        HumanMessage(content="after"),
    ]
    trimmed = node._apply_reset_command(messages)
    assert trimmed == messages[1:]


def test_apply_reset_command_returns_original_when_command_missing() -> None:
    node = AgentNode(
        name="agent",
        ai_model="test-model",
        reset_command="RESET",
    )
    messages = [
        HumanMessage(content="keep one"),
        HumanMessage(content="keep two"),
    ]
    assert node._apply_reset_command(messages) == messages


def test_build_messages_respects_max_messages_limit() -> None:
    node = AgentNode(
        name="agent",
        ai_model="test-model",
        max_messages=1,
    )
    state = State(
        messages=[
            {"role": "assistant", "content": "first"},
            {"role": "user", "content": "last"},
        ],
        inputs={},
        results={},
        structured_response=None,
        config=None,
    )
    messages = node._build_messages(state)
    assert len(messages) == 1
    assert messages[0].content == "last"


def test_build_messages_appends_current_input_when_checkpointed() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    state = State(
        messages=[{"role": "assistant", "content": "Previous answer"}],
        inputs={"message": "New user turn"},
        results={},
        structured_response=None,
        config=None,
    )
    messages = node._build_messages(
        state,
        config={"configurable": {"thread_id": "thread-1", "__pregel_checkpointer": {}}},
    )
    assert len(messages) == 2
    assert messages[0].content == "Previous answer"
    assert messages[1].content == "New user turn"


def test_build_messages_keeps_existing_messages_without_checkpointer() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    state = State(
        messages=[{"role": "assistant", "content": "Previous answer"}],
        inputs={"message": "Ignored user turn"},
        results={},
        structured_response=None,
        config=None,
    )
    messages = node._build_messages(
        state,
        config={"configurable": {"thread_id": "thread-1"}},
    )
    assert len(messages) == 1
    assert messages[0].content == "Previous answer"


def test_build_messages_checkpointer_with_no_new_inputs() -> None:
    """Checkpointer present but inputs produce no new messages – branch 352->355."""
    node = AgentNode(name="agent", ai_model="test-model")
    state = State(
        messages=[{"role": "assistant", "content": "Previous answer"}],
        inputs={},
        results={},
        structured_response=None,
        config=None,
    )
    messages = node._build_messages(
        state,
        config={"configurable": {"thread_id": "thread-1", "__pregel_checkpointer": {}}},
    )
    assert len(messages) == 1
    assert messages[0].content == "Previous answer"


def test_resolve_history_key_supports_literal_and_template_candidates() -> None:
    node = AgentNode(
        name="agent",
        ai_model="test-model",
        use_graph_chat_history=True,
        history_key_candidates=[
            "support-room-1",
            "{{results.resolve_history_key.session_key}}",
        ],
        history_key_template="session:{{conversation_key}}",
    )
    state = State(
        messages=[],
        inputs={},
        results={"resolve_history_key": {"session_key": "ignored"}},
        structured_response=None,
        config=None,
    )
    key = node._resolve_history_key(state, {"configurable": {"thread_id": "thread-1"}})
    assert key == "session:support-room-1"


def test_default_history_key_candidates_exclude_config_input_and_thread() -> None:
    node = AgentNode(name="agent", ai_model="test-model")

    assert (
        "{{results.resolve_history_key.session_key}}" not in node.history_key_candidates
    )
    assert "{{configurable.history_key}}" not in node.history_key_candidates
    assert "{{inputs.history_key}}" not in node.history_key_candidates
    assert "{{thread_id}}" not in node.history_key_candidates


def test_resolve_history_key_supports_configurable_candidate() -> None:
    node = AgentNode(
        name="agent",
        ai_model="test-model",
        history_key_candidates=["{{config.configurable.history_key}}"],
    )
    state = State(
        messages=[],
        inputs={},
        results={},
        structured_response=None,
        config={"configurable": {"history_key": "room-1"}},
    )
    assert node._resolve_history_key(state, None) == "room-1"


def test_resolve_history_key_supports_channel_templates() -> None:
    listener_node = AgentNode(name="agent", ai_model="test-model")
    listener_state = State(
        messages=[],
        inputs={
            "platform": "telegram",
            "message": {"chat_id": "12345", "text": "hello"},
        },
        results={},
        structured_response=None,
        config=None,
    )
    assert (
        listener_node._resolve_history_key(
            listener_state, {"configurable": {"thread_id": "thread-1"}}
        )
        == "telegram:12345"
    )

    telegram_node = AgentNode(name="agent", ai_model="test-model")
    telegram_state = State(
        messages=[],
        inputs={},
        results={"telegram_events_parser": {"chat_id": "12345"}},
        structured_response=None,
        config=None,
    )
    assert (
        telegram_node._resolve_history_key(
            telegram_state, {"configurable": {"thread_id": "thread-1"}}
        )
        == "telegram:12345"
    )

    wecom_cs_node = AgentNode(name="agent", ai_model="test-model")
    wecom_cs_state = State(
        messages=[],
        inputs={},
        results={
            "wecom_cs_sync": {
                "open_kf_id": "kf-001",
                "external_userid": "ext-abc",
            }
        },
        structured_response=None,
        config=None,
    )
    assert (
        wecom_cs_node._resolve_history_key(
            wecom_cs_state, {"configurable": {"thread_id": "thread-1"}}
        )
        == "wecom_cs:kf-001:ext-abc"
    )


def test_resolve_history_key_rejects_invalid_candidates() -> None:
    unresolved_node = AgentNode(
        name="agent",
        ai_model="test-model",
        history_key_candidates=["{{results.missing.value}}"],
    )
    invalid_state = State(
        messages=[],
        inputs={},
        results={},
        structured_response=None,
        config=None,
    )
    assert (
        unresolved_node._resolve_history_key(
            invalid_state, {"configurable": {"thread_id": "thread-1"}}
        )
        is None
    )

    invalid_chars_node = AgentNode(
        name="agent",
        ai_model="test-model",
        history_key_candidates=["bad/key"],
    )
    assert (
        invalid_chars_node._resolve_history_key(
            invalid_state, {"configurable": {"thread_id": "thread-1"}}
        )
        is None
    )

    too_long_node = AgentNode(
        name="agent",
        ai_model="test-model",
        history_key_candidates=["a" * 257],
    )
    assert (
        too_long_node._resolve_history_key(
            invalid_state, {"configurable": {"thread_id": "thread-1"}}
        )
        is None
    )


class _MemoryGraphStore:
    def __init__(self, value: dict[str, object] | None = None) -> None:
        self.value = value
        self.get_calls = 0
        self.put_calls = 0

    async def aget(self, namespace: tuple[str, ...], key: str) -> object | None:
        self.get_calls += 1
        if self.value is None:
            return None
        return SimpleNamespace(namespace=namespace, key=key, value=self.value)

    async def aput(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, object],
    ) -> None:
        self.put_calls += 1
        self.value = value


class _ConflictGraphStore:
    def __init__(self) -> None:
        self.put_calls = 0

    async def aget(self, namespace: tuple[str, ...], key: str) -> object:
        return SimpleNamespace(
            namespace=namespace,
            key=key,
            value={"version": 0, "messages": []},
        )

    async def aput(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, object],
    ) -> None:
        self.put_calls += 1


@pytest.mark.asyncio
async def test_agentnode_graph_history_merge_trim_and_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent = AsyncMock()
    fake_agent.ainvoke.return_value = {
        "messages": [
            HumanMessage(content="new question"),
            AIMessage(content="new answer"),
        ]
    }

    async def fake_prepare_tools(self: AgentNode):  # type: ignore[unused-argument]
        return []

    monkeypatch.setattr("orcheo.nodes.ai.init_chat_model", lambda *args, **kwargs: "m")
    monkeypatch.setattr(
        "orcheo.nodes.ai.create_agent", lambda *args, **kwargs: fake_agent
    )
    monkeypatch.setattr(AgentNode, "_prepare_tools", fake_prepare_tools)

    store = _MemoryGraphStore(
        value={
            "version": 1,
            "messages": [
                {"role": "assistant", "content": "old-1"},
                {"role": "user", "content": "old-2"},
                {"role": "assistant", "content": "old-3"},
            ],
        }
    )
    runtime = SimpleNamespace(store=store)

    node = AgentNode(
        name="agent",
        ai_model="test-model",
        use_graph_chat_history=True,
        max_messages=3,
        history_key_candidates=["room-1"],
    )
    state = State(
        messages=[],
        inputs={"message": "new question"},
        results={},
        structured_response=None,
        config=None,
    )

    await node.run(
        state,
        {"configurable": {"thread_id": "thread-1", "__pregel_runtime": runtime}},
    )

    payload = fake_agent.ainvoke.await_args.args[0]
    sent_messages = payload["messages"]
    assert [message.content for message in sent_messages] == [
        "old-2",
        "old-3",
        "new question",
    ]
    assert store.put_calls >= 1
    assert isinstance(store.value, dict)
    persisted_messages = store.value["messages"]  # type: ignore[index]
    assert persisted_messages[-2:] == [
        {"role": "user", "content": "new question"},
        {"role": "assistant", "content": "new answer"},
    ]


@pytest.mark.asyncio
async def test_agentnode_graph_history_disabled_skips_store_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent = AsyncMock()
    fake_agent.ainvoke.return_value = {"messages": [AIMessage(content="done")]}

    async def fake_prepare_tools(self: AgentNode):  # type: ignore[unused-argument]
        return []

    monkeypatch.setattr("orcheo.nodes.ai.init_chat_model", lambda *args, **kwargs: "m")
    monkeypatch.setattr(
        "orcheo.nodes.ai.create_agent", lambda *args, **kwargs: fake_agent
    )
    monkeypatch.setattr(AgentNode, "_prepare_tools", fake_prepare_tools)

    class FailingStore:
        async def aget(self, namespace: tuple[str, ...], key: str) -> object:
            raise AssertionError("store should not be used when disabled")

    runtime = SimpleNamespace(store=FailingStore())
    node = AgentNode(name="agent", ai_model="test-model", use_graph_chat_history=False)
    state = State(
        messages=[],
        inputs={"message": "hello"},
        results={},
        structured_response=None,
        config=None,
    )

    await node.run(
        state,
        {"configurable": {"thread_id": "thread-1", "__pregel_runtime": runtime}},
    )
    fake_agent.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_agentnode_graph_history_conflict_retry_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent = AsyncMock()
    fake_agent.ainvoke.return_value = {"messages": [AIMessage(content="done")]}

    async def fake_prepare_tools(self: AgentNode):  # type: ignore[unused-argument]
        return []

    monkeypatch.setattr("orcheo.nodes.ai.init_chat_model", lambda *args, **kwargs: "m")
    monkeypatch.setattr(
        "orcheo.nodes.ai.create_agent", lambda *args, **kwargs: fake_agent
    )
    monkeypatch.setattr(AgentNode, "_prepare_tools", fake_prepare_tools)

    store = _ConflictGraphStore()
    runtime = SimpleNamespace(store=store)
    node = AgentNode(
        name="agent",
        ai_model="test-model",
        use_graph_chat_history=True,
        history_key_candidates=["room-1"],
    )
    state = State(
        messages=[],
        inputs={"message": "hello"},
        results={},
        structured_response=None,
        config=None,
    )

    await node.run(
        state,
        {"configurable": {"thread_id": "thread-1", "__pregel_runtime": runtime}},
    )
    assert store.put_calls == 3


def test_get_graph_store_handles_mapping_variants() -> None:
    node = AgentNode(name="agent", ai_model="test-model")

    assert node._get_graph_store(None) is None
    assert node._get_graph_store({"configurable": "bad"}) is None
    assert node._get_graph_store({"configurable": {"__pregel_store": "fallback"}}) == (
        "fallback"
    )
    assert (
        node._get_graph_store(
            {"configurable": {"__pregel_runtime": {"store": "runtime-store"}}}
        )
        == "runtime-store"
    )
    assert (
        node._get_graph_store(
            {
                "configurable": {
                    "__pregel_runtime": {"store": None},
                    "__pregel_store": "fallback-2",
                }
            }
        )
        == "fallback-2"
    )


def test_resolve_history_key_skips_non_string_and_empty_candidates() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    node.history_key_candidates = [123, "{{results.empty.value}}", "room-1"]  # type: ignore[list-item]
    state = State(
        messages=[],
        inputs={},
        results={"empty": {"value": ""}},
        structured_response=None,
        config=None,
    )
    assert node._resolve_history_key(state, None) == "room-1"


def test_resolve_history_key_rejects_invalid_template_result() -> None:
    node = AgentNode(
        name="agent",
        ai_model="test-model",
        history_key_candidates=["room-1"],
        history_key_template="{{conversation_key}}/{{conversation_key}}",
    )
    state = State(
        messages=[], inputs={}, results={}, structured_response=None, config=None
    )
    assert node._resolve_history_key(state, None) is None


@pytest.mark.asyncio
async def test_store_get_item_sync_get_variants() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    namespace = ("ns",)

    class SyncStore:
        def get(self, namespace: tuple[str, ...], key: str) -> object:
            del namespace, key
            return {"value": 1}

    class CoroutineStore:
        async def _async_get(self) -> object:
            return {"value": 2}

        def get(self, namespace: tuple[str, ...], key: str) -> object:
            del namespace, key
            return self._async_get()

    class EmptyStore:
        pass

    assert await node._store_get_item(SyncStore(), namespace, "k") == {"value": 1}
    assert await node._store_get_item(CoroutineStore(), namespace, "k") == {"value": 2}
    assert await node._store_get_item(EmptyStore(), namespace, "k") is None


@pytest.mark.asyncio
async def test_store_put_item_sync_put_coroutine() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    namespace = ("ns",)
    calls: list[dict[str, object]] = []

    class PutStore:
        async def _async_put(self, value: dict[str, object]) -> None:
            calls.append(value)

        def put(
            self,
            namespace: tuple[str, ...],
            key: str,
            value: dict[str, object],
        ) -> object:
            del namespace, key
            return self._async_put(value)

    await node._store_put_item(PutStore(), namespace, "k", {"x": 1})
    assert calls == [{"x": 1}]


@pytest.mark.asyncio
async def test_store_put_item_sync_put_non_coroutine_and_missing_put() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    namespace = ("ns",)
    calls: list[dict[str, object]] = []

    class SyncPutStore:
        def put(
            self,
            namespace: tuple[str, ...],
            key: str,
            value: dict[str, object],
        ) -> object:
            del namespace, key
            calls.append(value)
            return "done"

    class MissingPutStore:
        pass

    await node._store_put_item(SyncPutStore(), namespace, "k", {"y": 2})
    await node._store_put_item(MissingPutStore(), namespace, "k", {"z": 3})
    assert calls == [{"y": 2}]


def test_history_payload_from_item_and_message_normalization() -> None:
    node = AgentNode(name="agent", ai_model="test-model")

    assert node._history_payload_from_item(None) == {}
    assert node._history_payload_from_item({"value": "not-mapping"}) == {}
    assert node._history_payload_from_item({"value": {"messages": []}}) == {
        "messages": []
    }
    assert node._normalize_history_store_messages("not-list") == []

    messages = node._normalize_history_store_messages(
        [
            "bad",
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": " "},
            {"role": "user", "content": "b"},
        ]
    )
    assert [message.content for message in messages] == ["a", "b"]


def test_history_serialization_and_signature_helpers() -> None:
    node = AgentNode(name="agent", ai_model="test-model", history_value_field="text")
    serialized = node._serialize_history_messages(
        [
            AIMessage(content="a"),
            HumanMessage(content=" "),
            SystemMessage(content="system"),
            HumanMessage(content="b"),
        ]
    )
    assert serialized == [
        {"role": "assistant", "text": "a", "content": "a"},
        {"role": "user", "text": "b", "content": "b"},
    ]
    assert node._message_signature(SystemMessage(content="x")) == ("system", "x")
    assert (
        node._suffix_prefix_overlap(
            [HumanMessage(content="1"), AIMessage(content="2")],
            [AIMessage(content="2"), HumanMessage(content="3")],
        )
        == 1
    )
    assert node._history_version_from_payload({"version": "2"}) == 2
    assert node._history_version_from_payload({"version": "x"}) == 0


def test_extract_observed_messages_uses_inference_prefix() -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    current_messages = [HumanMessage(content="old")]
    inference_messages = [HumanMessage(content="old"), AIMessage(content="draft")]
    result = {
        "messages": [
            HumanMessage(content="old"),
            AIMessage(content="draft"),
            AIMessage(content="final"),
        ]
    }

    observed = node._extract_observed_messages(
        current_messages, inference_messages, result
    )
    assert [message.content for message in observed] == ["old", "final"]


@pytest.mark.asyncio
async def test_persist_graph_history_early_returns_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    namespace = ("ns",)

    class ReadErrorStore:
        async def aget(self, namespace: tuple[str, ...], key: str) -> object:
            del namespace, key
            msg = "read fail"
            raise RuntimeError(msg)

    class NoNewMessagesStore:
        async def aget(self, namespace: tuple[str, ...], key: str) -> object:
            del namespace, key
            return {
                "value": {
                    "version": 1,
                    "messages": [{"role": "user", "content": "hello"}],
                }
            }

        async def aput(
            self,
            namespace: tuple[str, ...],
            key: str,
            value: dict[str, object],
        ) -> None:
            del namespace, key, value
            raise AssertionError("no write expected")

    await node._persist_graph_history(
        store=NoNewMessagesStore(),
        namespace=namespace,
        key="k",
        observed_messages=[SystemMessage(content="ignore")],
    )

    await node._persist_graph_history(
        store=ReadErrorStore(),
        namespace=namespace,
        key="k",
        observed_messages=[HumanMessage(content="hello")],
    )
    await node._persist_graph_history(
        store=NoNewMessagesStore(),
        namespace=namespace,
        key="k",
        observed_messages=[HumanMessage(content="hello")],
    )

    monkeypatch.setattr(AgentNode, "_history_write_retry_limit", 0)
    await node._persist_graph_history(
        store=NoNewMessagesStore(),
        namespace=namespace,
        key="k",
        observed_messages=[HumanMessage(content="hello")],
    )


@pytest.mark.asyncio
async def test_persist_graph_history_put_failure_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = AgentNode(name="agent", ai_model="test-model")
    namespace = ("ns",)
    sleeps: list[float] = []

    class PutErrorStore:
        async def aget(self, namespace: tuple[str, ...], key: str) -> object:
            del namespace, key
            return {"value": {"version": 0, "messages": []}}

        async def aput(
            self,
            namespace: tuple[str, ...],
            key: str,
            value: dict[str, object],
        ) -> None:
            del namespace, key, value
            msg = "put fail"
            raise RuntimeError(msg)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(AgentNode, "_history_write_retry_limit", 2)
    monkeypatch.setattr("orcheo.nodes.ai.random.uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr("orcheo.nodes.ai.asyncio.sleep", fake_sleep)

    await node._persist_graph_history(
        store=PutErrorStore(),
        namespace=namespace,
        key="k",
        observed_messages=[HumanMessage(content="hello")],
    )
    assert sleeps == [node._history_retry_base_backoff_seconds]


@pytest.mark.asyncio
async def test_run_graph_history_fallback_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent = AsyncMock()
    fake_agent.ainvoke.return_value = {"messages": [AIMessage(content="done")]}

    async def fake_prepare_tools(self: AgentNode):  # type: ignore[unused-argument]
        return []

    monkeypatch.setattr("orcheo.nodes.ai.init_chat_model", lambda *args, **kwargs: "m")
    monkeypatch.setattr(
        "orcheo.nodes.ai.create_agent", lambda *args, **kwargs: fake_agent
    )
    monkeypatch.setattr(AgentNode, "_prepare_tools", fake_prepare_tools)

    no_store_node = AgentNode(
        name="agent",
        ai_model="test-model",
        use_graph_chat_history=True,
        history_key_candidates=["room-1"],
    )
    missing_key_node = AgentNode(
        name="agent",
        ai_model="test-model",
        use_graph_chat_history=True,
        history_key_candidates=["{{results.missing.value}}"],
    )
    bad_key_node = AgentNode(
        name="agent",
        ai_model="test-model",
        use_graph_chat_history=True,
        history_key_candidates=["{{results.missing.value}}"],
    )
    load_fail_node = AgentNode(
        name="agent",
        ai_model="test-model",
        use_graph_chat_history=True,
        history_key_candidates=["room-1"],
    )
    state = State(
        messages=[],
        inputs={"message": "hello"},
        results={},
        structured_response=None,
        config=None,
    )

    class RaiseStore:
        async def aget(self, namespace: tuple[str, ...], key: str) -> object:
            del namespace, key
            msg = "load fail"
            raise RuntimeError(msg)

    await no_store_node.run(state, {"configurable": {"thread_id": "thread-1"}})
    await bad_key_node.run(
        state,
        {"configurable": {"thread_id": "thread-1", "__pregel_runtime": object()}},
    )
    await missing_key_node.run(
        state,
        {
            "configurable": {
                "thread_id": "thread-1",
                "__pregel_runtime": SimpleNamespace(store=_MemoryGraphStore()),
            }
        },
    )
    await load_fail_node.run(
        state,
        {
            "configurable": {
                "thread_id": "thread-1",
                "__pregel_runtime": SimpleNamespace(store=RaiseStore()),
            }
        },
    )
