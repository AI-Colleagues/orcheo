import time
from collections.abc import Iterable
import pytest
from pydantic import PrivateAttr
from orcheo.graph.state import State
from orcheo.nodes.rag.conversation import (
    BaseMemoryStore,
    ConversationCompressorNode,
    ConversationStateNode,
    InMemoryMemoryStore,
    MemorySummarizerNode,
    MemoryTurn,
    QueryClarificationNode,
    TopicShiftDetectorNode,
)


class DummyStore(BaseMemoryStore):
    """Simple store that tracks appended turns via the base implementation."""

    _appended: list[tuple[str, MemoryTurn]] = PrivateAttr(default_factory=list)

    @property
    def appended(self) -> list[tuple[str, MemoryTurn]]:
        return self._appended

    async def load_history(
        self, session_id: str, limit: int | None = None
    ) -> list[MemoryTurn]:
        return []

    async def append_turn(self, session_id: str, turn: MemoryTurn) -> None:
        self.appended.append((session_id, turn))

    async def prune(self, session_id: str, max_turns: int | None = None) -> None:
        pass

    async def write_summary(
        self, session_id: str, summary: str, ttl_seconds: int | None = None
    ) -> None:
        pass

    async def get_summary(self, session_id: str) -> str | None:
        return None

    async def clear(self, session_id: str) -> None:
        pass


class TrackingStore(InMemoryMemoryStore):
    """Extend the in-memory store to track batch append calls."""

    _batch_calls: int = PrivateAttr(0)

    @property
    def batch_calls(self) -> int:
        return self._batch_calls

    async def batch_append_turns(
        self, session_id: str, turns: Iterable[MemoryTurn]
    ) -> None:
        self._batch_calls += 1
        await super().batch_append_turns(session_id, turns)


@pytest.mark.asyncio
async def test_conversation_state_appends_and_limits_history() -> None:
    store = InMemoryMemoryStore()
    node = ConversationStateNode(
        name="conversation_state", memory_store=store, max_turns=2
    )

    state = State(
        inputs={"session_id": "sess-1", "user_message": "Hello"},
        results={},
        structured_response=None,
    )

    first_result = await node.run(state, {})
    assert first_result["turn_count"] == 1

    state["inputs"]["user_message"] = "Second turn"
    await node.run(state, {})
    state["inputs"]["user_message"] = "Third turn"
    final_result = await node.run(state, {})

    assert final_result["turn_count"] == 2
    assert [turn["content"] for turn in final_result["conversation_history"]] == [
        "Second turn",
        "Third turn",
    ]
    assert final_result["truncated"] is True


@pytest.mark.asyncio
async def test_conversation_state_prunes_existing_history() -> None:
    store = InMemoryMemoryStore()
    await store.append_turn("sess-prune", MemoryTurn(role="user", content="first"))
    await store.append_turn(
        "sess-prune", MemoryTurn(role="assistant", content="second")
    )
    node = ConversationStateNode(
        name="conversation_state", memory_store=store, max_turns=2
    )

    state = State(
        inputs={"session_id": "sess-prune", "user_message": "newest"},
        results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["turn_count"] == 2
    assert result["conversation_history"][-1]["content"] == "newest"


@pytest.mark.asyncio
async def test_conversation_compressor_summarizes_history() -> None:
    node = ConversationCompressorNode(
        name="compressor", max_tokens=4, preserve_recent=1, source_result_key="state"
    )
    state = State(
        inputs={},
        results={
            "state": {
                "conversation_history": [
                    {"role": "user", "content": "short"},
                    {
                        "role": "assistant",
                        "content": "very long message from assistant",
                    },
                ]
            }
        },
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["truncated"] is True
    assert len(result["compressed_history"]) == 1
    assert "assistant" in result["summary"]


@pytest.mark.asyncio
async def test_conversation_compressor_adds_ellipsis_for_overflow() -> None:
    node = ConversationCompressorNode(
        name="compressor", max_tokens=3, preserve_recent=2
    )
    state = State(
        inputs={},
        results={
            "conversation_state": {
                "conversation_history": [
                    {"role": "user", "content": "one two"},
                    {"role": "assistant", "content": "three four"},
                ]
            }
        },
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["summary"].endswith("...")


def test_memory_turn_requires_content() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        MemoryTurn(role="user", content="   ")


@pytest.mark.asyncio
async def test_memory_store_prune_and_expiry_paths() -> None:
    store = InMemoryMemoryStore()
    await store.append_turn("sess-x", MemoryTurn(role="user", content="hello"))

    assert len(await store.load_history("sess-x")) == 1  # limit=None branch
    await store.prune("missing", max_turns=1)  # history is None branch
    await store.prune("sess-x", max_turns=None)  # no-op branch

    await store.write_summary("sess-x", summary="keep", ttl_seconds=1)
    store.summaries["sess-x"] = ("keep", time.time() - 1)

    assert await store.get_summary("sess-x") is None
    assert "sess-x" not in store.summaries


@pytest.mark.asyncio
async def test_memory_store_retains_history_when_summary_expires() -> None:
    store = InMemoryMemoryStore()
    await store.append_turn("sess-retain", MemoryTurn(role="user", content="hello"))
    await store.write_summary("sess-retain", summary="temp", ttl_seconds=1)

    store.summaries["sess-retain"] = ("temp", time.time() - 1)

    assert await store.get_summary("sess-retain") is None
    assert len(await store.load_history("sess-retain")) == 1


@pytest.mark.asyncio
async def test_memory_store_enforces_session_and_turn_capacity() -> None:
    store = InMemoryMemoryStore(max_sessions=2, max_total_turns=3)

    await store.append_turn("sess-1", MemoryTurn(role="user", content="hello"))
    await store.append_turn("sess-1", MemoryTurn(role="assistant", content="hi"))
    await store.append_turn("sess-2", MemoryTurn(role="user", content="hey"))

    # Adding a third session should evict the stalest (sess-1)
    await store.append_turn("sess-3", MemoryTurn(role="user", content="welcome"))

    assert "sess-1" not in store.sessions
    assert "sess-2" in store.sessions
    assert "sess-3" in store.sessions

    # Global turn limit should evict older sessions when exceeded
    await store.append_turn("sess-2", MemoryTurn(role="assistant", content="reply"))
    await store.append_turn("sess-3", MemoryTurn(role="assistant", content="reply"))

    total_turns = sum(len(turns) for turns in store.sessions.values())
    assert total_turns <= store.max_total_turns


@pytest.mark.asyncio
async def test_conversation_state_requires_session_id() -> None:
    node = ConversationStateNode(name="conversation_state")
    state = State(inputs={}, results={}, structured_response=None)

    with pytest.raises(ValueError, match="requires a non-empty session id"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_conversation_state_honors_configurable_max_turns() -> None:
    node = ConversationStateNode(name="conversation_state", max_turns=5)
    state = State(
        inputs={"session_id": "cfg", "user_message": "first"},
        results={},
        structured_response=None,
    )

    await node.run(state, {"configurable": {"max_turns": 1}})
    state["inputs"]["user_message"] = "second"
    result = await node.run(state, {"configurable": {"max_turns": 1}})

    assert result["turn_count"] == 1
    assert result["conversation_history"][0]["content"] == "second"


@pytest.mark.asyncio
async def test_base_memory_store_batch_append_uses_append_turns() -> None:
    store = DummyStore()
    turns = [
        MemoryTurn(role="user", content="hi"),
        MemoryTurn(role="assistant", content="reply"),
    ]

    await store.batch_append_turns("base", turns)
    assert len(store.appended) == 2
    assert store.appended[0][0] == "base"


def test_inmemory_store_sync_helpers_and_eviction() -> None:
    store = InMemoryMemoryStore(max_sessions=1, max_total_turns=1)

    store.append_turn_sync("first", MemoryTurn(role="user", content="hello"))
    assert store.load_history_sync("first", limit=1)[0].content == "hello"

    store.append_turn_sync("second", MemoryTurn(role="assistant", content="hi"))
    assert "first" not in store.sessions
    assert "second" in store.sessions

    store.clear_sync("second")
    assert not store.sessions
    store._evict_stalest_session_sync()


@pytest.mark.asyncio
async def test_inmemory_batch_append_handles_empty_iterable() -> None:
    store = InMemoryMemoryStore()

    await store.batch_append_turns("noop", [])
    assert store.sessions == {}


def test_inmemory_load_history_sync_without_limit_returns_all_turns() -> None:
    store = InMemoryMemoryStore()
    turn = MemoryTurn(role="user", content="hi")
    store.sessions["all"] = [turn]

    assert store.load_history_sync("all") == [turn]


def test_inmemory_ensure_capacity_sync_evicts_when_limits_exceeded() -> None:
    store = InMemoryMemoryStore(max_sessions=1, max_total_turns=1)
    store.sessions["old"] = [MemoryTurn(role="assistant", content="old")]
    store.session_last_updated["old"] = time.time() - 10

    store._ensure_capacity_sync("new", incoming_count=2)

    assert not store.sessions
    assert not store.session_last_updated


def test_inmemory_ensure_capacity_sync_respects_session_limit() -> None:
    store = InMemoryMemoryStore(max_sessions=1, max_total_turns=10)
    store.sessions["primary"] = [MemoryTurn(role="user", content="hello")]
    store.session_last_updated["primary"] = time.time() - 5

    store._ensure_capacity_sync("secondary", incoming_count=1)

    assert "primary" not in store.sessions
    assert "secondary" in store.session_last_updated


def test_inmemory_ensure_capacity_sync_respects_turn_limit() -> None:
    store = InMemoryMemoryStore(max_sessions=5, max_total_turns=1)
    store.sessions["primary"] = [MemoryTurn(role="user", content="hello")]
    store.session_last_updated["primary"] = time.time() - 5

    store._ensure_capacity_sync("primary", incoming_count=1)

    assert store.sessions == {}
    assert store.session_last_updated == {}


def test_inmemory_ensure_capacity_sync_skips_turn_limit_when_disabled() -> None:
    store = InMemoryMemoryStore(max_sessions=1, max_total_turns=None)
    store.sessions["primary"] = [MemoryTurn(role="user", content="hello")]
    store.session_last_updated["primary"] = time.time() - 5

    store._ensure_capacity_sync("secondary", incoming_count=1)

    assert "primary" not in store.sessions
    assert "secondary" in store.session_last_updated


def test_inmemory_ensure_capacity_sync_skips_turn_limit_when_zero() -> None:
    store = InMemoryMemoryStore(max_sessions=2, max_total_turns=0)
    store.sessions["primary"] = [MemoryTurn(role="user", content="hello")]
    store.session_last_updated["primary"] = time.time() - 5

    store._ensure_capacity_sync("primary", incoming_count=1)

    assert "primary" in store.sessions
    assert len(store.sessions["primary"]) == 1


@pytest.mark.asyncio
async def test_inmemory_store_eviction_short_circuits_when_empty() -> None:
    store = InMemoryMemoryStore()
    await store._evict_stalest_session()
    assert store.sessions == {}


@pytest.mark.asyncio
async def test_inmemory_ensure_capacity_skips_turn_limit_when_zero() -> None:
    store = InMemoryMemoryStore(max_sessions=2, max_total_turns=0)
    await store.append_turn("primary", MemoryTurn(role="user", content="hello"))

    await store._ensure_capacity("primary", incoming_count=1)

    assert "primary" in store.sessions
    assert len(store.sessions["primary"]) == 1


@pytest.mark.asyncio
async def test_conversation_state_tracks_batch_appends_and_skips_empty() -> None:
    store = TrackingStore()
    node = ConversationStateNode(name="conversation_state", memory_store=store)
    state = State(
        inputs={
            "session_id": "batch",
            "user_message": "hello",
            "assistant_message": "reply",
        },
        results={},
        structured_response=None,
    )

    await node.run(state, {})
    assert store.batch_calls == 1
    assert len(store.sessions["batch"]) == 2

    empty_state = State(
        inputs={"session_id": "batch"},
        results={},
        structured_response=None,
    )
    await node.run(empty_state, {})
    assert store.batch_calls == 1


def test_conversation_state_config_value_defaults_when_invalid() -> None:
    assert (
        ConversationStateNode._config_value(
            {"configurable": {"max_turns": 0}}, "max_turns", 5
        )
        == 5
    )


def test_conversation_state_config_value_returns_default_without_dict() -> None:
    assert ConversationStateNode._config_value(None, "max_turns", 5) == 5


def test_conversation_compressor_config_value_defaults_when_missing() -> None:
    assert (
        ConversationCompressorNode._config_value({"configurable": {}}, "max_tokens", 10)
        == 10
    )


def test_conversation_compressor_config_value_prefers_override() -> None:
    assert (
        ConversationCompressorNode._config_value(
            {"configurable": {"max_tokens": 2}}, "max_tokens", 10
        )
        == 2
    )


def test_conversation_compressor_config_value_ignores_invalid_override() -> None:
    assert (
        ConversationCompressorNode._config_value(
            {"configurable": {"max_tokens": "lots"}}, "max_tokens", 10
        )
        == 10
    )


def test_conversation_compressor_config_value_returns_default_with_no_config() -> None:
    assert ConversationCompressorNode._config_value(None, "max_tokens", 10) == 10


@pytest.mark.asyncio
async def test_conversation_compressor_rejects_non_list_history() -> None:
    node = ConversationCompressorNode(name="compressor")
    state = State(
        inputs={},
        results={"conversation_state": {"conversation_history": "invalid"}},
        structured_response=None,
    )

    with pytest.raises(ValueError, match="must be a list of turn dictionaries"):
        await node.run(state, {})


def test_conversation_compressor_summarize_snippet_when_first_turn_overflows() -> None:
    node = ConversationCompressorNode(name="compressor")
    turn = MemoryTurn(role="user", content="alpha beta")
    summary = node._summarize([turn], token_limit=1)
    assert summary == "user: alpha..."


def test_conversation_compressor_summarize_adds_trailing_ellipsis() -> None:
    node = ConversationCompressorNode(name="compressor")
    summary = node._summarize(
        [
            MemoryTurn(role="user", content="one"),
            MemoryTurn(role="assistant", content="two"),
        ],
        token_limit=1,
    )
    assert summary == "user: one | ..."


def test_topic_shift_detector_config_helpers() -> None:
    assert (
        TopicShiftDetectorNode._config_value(
            {"configurable": {"similarity_threshold": 0.65}},
            "similarity_threshold",
            0.35,
        )
        == 0.65
    )
    assert (
        TopicShiftDetectorNode._config_int_value(
            {"configurable": {"recent_turns": 2}}, "recent_turns", 3
        )
        == 2
    )


def test_topic_shift_detector_config_defaults_without_override() -> None:
    assert (
        TopicShiftDetectorNode._config_value(
            {"configurable": {}}, "similarity_threshold", 0.35
        )
        == 0.35
    )
    assert (
        TopicShiftDetectorNode._config_int_value(
            {"configurable": {"recent_turns": 0}}, "recent_turns", 3
        )
        == 3
    )


def test_topic_shift_detector_config_value_ignores_non_numeric_override() -> None:
    assert (
        TopicShiftDetectorNode._config_value(
            {"configurable": {"similarity_threshold": "high"}},
            "similarity_threshold",
            0.35,
        )
        == 0.35
    )


def test_topic_shift_detector_config_int_value_ignores_non_int_override() -> None:
    assert (
        TopicShiftDetectorNode._config_int_value(
            {"configurable": {"recent_turns": "many"}}, "recent_turns", 3
        )
        == 3
    )


def test_topic_shift_detector_config_helpers_default_without_dict() -> None:
    assert (
        TopicShiftDetectorNode._config_value(None, "similarity_threshold", 0.35) == 0.35
    )
    assert TopicShiftDetectorNode._config_int_value(None, "recent_turns", 3) == 3


def test_topic_shift_detector_stopwords_override_filters_invalid_entries() -> None:
    node = TopicShiftDetectorNode(name="topic")
    overrides = {"configurable": {"stopwords": ["custom", 123, None]}}

    assert node._config_stopwords(overrides) == {"custom"}
    assert node._config_stopwords(None) == set(node.stopwords)


@pytest.mark.asyncio
async def test_topic_shift_detector_requires_query() -> None:
    node = TopicShiftDetectorNode(name="topic")
    state = State(inputs={}, results={}, structured_response=None)

    with pytest.raises(ValueError, match="requires a non-empty query"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_topic_shift_detector_returns_continue_without_history() -> None:
    node = TopicShiftDetectorNode(name="topic")
    state = State(
        inputs={"query": "new question"},
        results={"conversation_state": []},
        structured_response=None,
    )

    result = await node.run(state, {})
    assert result["route"] == "continue"
    assert result["reason"] == "no_history"


def test_topic_shift_detector_extract_turns_rejects_wrong_type() -> None:
    node = TopicShiftDetectorNode(name="topic")

    with pytest.raises(ValueError, match="must be provided as a list"):
        node._extract_turns("string")


@pytest.mark.asyncio
async def test_topic_shift_detector_respects_stopwords_and_window() -> None:
    node = TopicShiftDetectorNode(name="topic")
    state = State(
        inputs={"query": "Different apples"},
        results={
            "conversation_state": {
                "conversation_history": [
                    {"role": "assistant", "content": "apples and bananas"},
                    {"role": "user", "content": "apples always"},
                ]
            }
        },
        structured_response=None,
    )
    config = {
        "configurable": {
            "recent_turns": 1,
            "stopwords": ["different"],
            "similarity_threshold": 0.1,
        }
    }

    result = await node.run(state, config)
    assert result["route"] in {"clarify", "continue"}
    assert isinstance(result["similarity"], float)


@pytest.mark.asyncio
async def test_query_clarification_requires_query() -> None:
    node = QueryClarificationNode(name="clarifier")
    state = State(inputs={}, results={}, structured_response=None)

    with pytest.raises(ValueError, match="requires a non-empty query"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_query_clarification_falls_back_to_message_input() -> None:
    node = QueryClarificationNode(name="clarifier")
    state = State(
        inputs={"message": "Clarify which option to focus on"},
        results={},
        structured_response=None,
    )

    result = await node.run(state, {})
    assert result["needs_clarification"] is True
    assert result["clarifications"]


@pytest.mark.asyncio
async def test_query_clarification_falls_back_to_user_message_when_message_missing() -> (  # noqa: E501
    None
):
    node = QueryClarificationNode(name="clarifier")
    state = State(
        inputs={"user_message": "Clarify which option to focus on"},
        results={},
        structured_response=None,
    )

    result = await node.run(state, {})
    assert result["needs_clarification"] is True
    assert result["clarifications"]


@pytest.mark.asyncio
async def test_query_clarification_uses_summary_context_hint() -> None:
    node = QueryClarificationNode(name="clarifier")
    state = State(
        inputs={"query": "Clarify this"},
        results={"conversation_history": {"summary": "latest summary"}},
        structured_response=None,
    )

    result = await node.run(state, {})
    assert result["context_hint"] == "latest summary"
    assert result["needs_clarification"] is True


@pytest.mark.asyncio
async def test_query_clarification_builds_questions_from_history_list() -> None:
    node = QueryClarificationNode(name="clarifier")
    state = State(
        inputs={"query": "Tell me more"},
        results={"conversation_history": ["first turn", "second turn"]},
        structured_response=None,
    )

    result = await node.run(state, {})
    assert result["context_hint"] == "second turn"
    assert result["clarifications"]


@pytest.mark.asyncio
async def test_query_clarification_limits_questions_for_ambiguous_tokens() -> None:
    node = QueryClarificationNode(name="clarifier", max_questions=1)
    state = State(
        inputs={"query": "It or that option"},
        results={},
        structured_response=None,
    )

    result = await node.run(state, {})
    assert len(result["clarifications"]) == 1
    assert result["clarifications"][0].startswith("What specific item")


@pytest.mark.asyncio
async def test_memory_summarizer_uses_existing_summary_and_configured_ttl() -> None:
    store = InMemoryMemoryStore()
    node = MemorySummarizerNode(name="summarizer", memory_store=store)
    turns = [
        MemoryTurn(role="user", content="hello there"),
        MemoryTurn(role="assistant", content="again"),
    ]
    state = State(
        inputs={"session_id": "summarize"},
        results={
            "conversation_state": {
                "summary": "existing summary",
                "conversation_history": [turn.model_dump() for turn in turns],
            }
        },
        structured_response=None,
    )

    result = await node.run(state, {"configurable": {"retention_seconds": None}})
    assert result["summary"] == "existing summary"
    assert result["ttl_seconds"] is None
    assert store.summaries["summarize"][0] == "existing summary"


def test_memory_summarizer_config_value_handles_none_and_positive() -> None:
    assert (
        MemorySummarizerNode._config_value(
            {"configurable": {"retention_seconds": None}}, "retention_seconds", 10
        )
        is None
    )
    assert (
        MemorySummarizerNode._config_value(
            {"configurable": {"retention_seconds": 20}}, "retention_seconds", 10
        )
        == 20
    )


def test_memory_summarizer_config_value_defaults_when_key_missing() -> None:
    assert (
        MemorySummarizerNode._config_value(
            {"configurable": {}}, "retention_seconds", 30
        )
        == 30
    )


def test_memory_summarizer_config_value_rejects_invalid_override() -> None:
    assert (
        MemorySummarizerNode._config_value(
            {"configurable": {"retention_seconds": -5}}, "retention_seconds", 15
        )
        == 15
    )


def test_memory_summarizer_config_value_returns_default_without_config() -> None:
    assert MemorySummarizerNode._config_value(None, "retention_seconds", 12) == 12


def test_memory_summarizer_summarize_returns_placeholder_when_empty() -> None:
    node = MemorySummarizerNode(name="summarizer")
    assert node._summarize([], max_tokens=5) == "No conversation history yet."


@pytest.mark.asyncio
async def test_memory_summarizer_rejects_nonpositive_retention() -> None:
    node = MemorySummarizerNode(name="summarizer", retention_seconds=0)
    state = State(
        inputs={"session_id": "summarize"},
        results={"conversation_state": {"conversation_history": []}},
        structured_response=None,
    )

    with pytest.raises(ValueError, match="retention_seconds must be positive"):
        await node.run(state, {"configurable": {"retention_seconds": 0}})


@pytest.mark.asyncio
async def test_conversation_compressor_validates_history_payload() -> None:
    node = ConversationCompressorNode(name="compressor")
    state = State(
        inputs={}, results={"conversation_state": {}}, structured_response=None
    )

    with pytest.raises(ValueError, match="conversation_history must be a list"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_conversation_compressor_requires_turns() -> None:
    node = ConversationCompressorNode(name="compressor")
    state = State(
        inputs={},
        results={"conversation_state": {"conversation_history": []}},
        structured_response=None,
    )

    with pytest.raises(ValueError, match="requires at least one turn"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_conversation_compressor_honors_config_overrides() -> None:
    node = ConversationCompressorNode(
        name="compressor", max_tokens=10, preserve_recent=1
    )
    state = State(
        inputs={},
        results={
            "conversation_state": {
                "conversation_history": [
                    {"role": "user", "content": "one two three four"},
                    {"role": "assistant", "content": "five six seven eight"},
                    {"role": "user", "content": "nine ten eleven twelve"},
                ]
            }
        },
        structured_response=None,
    )

    config = {"configurable": {"max_tokens": 4, "preserve_recent": 2}}
    result = await node.run(state, config)

    assert result["truncated"] is True
    assert len(result["compressed_history"]) == 2


@pytest.mark.asyncio
async def test_topic_shift_detector_flags_divergence() -> None:
    node = TopicShiftDetectorNode(name="shift", similarity_threshold=0.4)
    state = State(
        inputs={"query": "Switch to pricing details"},
        results={
            "conversation_state": {
                "conversation_history": [
                    {"role": "user", "content": "Tell me about embedding quality"}
                ]
            }
        },
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["is_shift"] is True
    assert result["route"] == "clarify"


@pytest.mark.asyncio
async def test_topic_shift_detector_handles_missing_query() -> None:
    node = TopicShiftDetectorNode(name="shift")
    state = State(inputs={}, results={}, structured_response=None)

    with pytest.raises(ValueError, match="requires a non-empty query"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_topic_shift_detector_handles_missing_history() -> None:
    node = TopicShiftDetectorNode(name="shift")
    state = State(
        inputs={"query": "hello"},
        results={"conversation_state": None},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["route"] == "continue"
    assert result["reason"] == "no_history"


@pytest.mark.asyncio
async def test_topic_shift_detector_supports_runtime_overrides() -> None:
    node = TopicShiftDetectorNode(name="shift")
    state = State(
        inputs={"query": "Discuss the project"},
        results={
            "conversation_state": {
                "conversation_history": [
                    {"role": "user", "content": "Tell me about the project"}
                ]
            }
        },
        structured_response=None,
    )

    config = {"configurable": {"similarity_threshold": 0.9, "stopwords": {"project"}}}
    result = await node.run(state, config)

    assert result["is_shift"] is True
    assert result["reason"] == "low_overlap"


@pytest.mark.asyncio
async def test_topic_shift_detector_validates_history_type() -> None:
    node = TopicShiftDetectorNode(name="shift")
    state = State(
        inputs={"query": "hi"},
        results={"conversation_state": "oops"},
        structured_response=None,
    )

    with pytest.raises(ValueError, match="must be provided as a list"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_topic_shift_detector_handles_empty_tokens() -> None:
    node = TopicShiftDetectorNode(name="shift", similarity_threshold=0.1)
    state = State(
        inputs={"query": "and"},
        results={
            "conversation_state": {
                "conversation_history": [{"role": "user", "content": "the"}]
            }
        },
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["similarity"] == 0.0
    assert result["is_shift"] is True


@pytest.mark.asyncio
async def test_query_clarification_generates_prompts() -> None:
    node = QueryClarificationNode(name="clarify")
    state = State(
        inputs={"query": "How does it work?"},
        results={
            "conversation_history": [
                {"role": "assistant", "content": "It handles retrieval and generation."}
            ]
        },
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["needs_clarification"] is True
    assert any("specific" in question for question in result["clarifications"])


@pytest.mark.asyncio
async def test_query_clarification_handles_or_branch_and_summary_hint() -> None:
    node = QueryClarificationNode(name="clarify", max_questions=3)
    state = State(
        inputs={"query": "this or that"},
        results={"conversation_history": {"summary": "previous summary"}},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert "option" in " ".join(result["clarifications"])
    assert result["context_hint"] == "previous summary"


@pytest.mark.asyncio
async def test_memory_summarizer_persists_summary_with_ttl() -> None:
    store = InMemoryMemoryStore()
    node = MemorySummarizerNode(
        name="summarizer",
        memory_store=store,
        retention_seconds=10,
        max_summary_tokens=10,
    )
    state = State(
        inputs={"session_id": "sess-99"},
        results={
            "conversation_state": {
                "conversation_history": [
                    {"role": "user", "content": "We discussed retrieval latency"},
                    {"role": "assistant", "content": "Latency targets are strict."},
                ]
            }
        },
        structured_response=None,
    )

    result = await node.run(state, {})
    summary = await store.get_summary("sess-99")

    assert result["turns_summarized"] == 2
    assert summary is not None
    assert result["summary"] == summary
    assert result["ttl_seconds"] == 10


@pytest.mark.asyncio
async def test_memory_summarizer_respects_configurable_ttl_and_budget() -> None:
    store = InMemoryMemoryStore()
    node = MemorySummarizerNode(name="summarizer", memory_store=store)
    state = State(
        inputs={"session_id": "sess-ttl"},
        results={
            "conversation_state": {
                "conversation_history": [
                    {"role": "user", "content": "short"},
                    {"role": "assistant", "content": "a very long assistant reply"},
                ]
            }
        },
        structured_response=None,
    )

    config = {"configurable": {"retention_seconds": 5, "max_summary_tokens": 2}}
    result = await node.run(state, config)

    assert result["ttl_seconds"] == 5
    assert result["summary"].endswith("...")


@pytest.mark.asyncio
async def test_memory_summarizer_validates_inputs_and_retention() -> None:
    store = InMemoryMemoryStore()
    node = MemorySummarizerNode(
        name="summarizer", memory_store=store, retention_seconds=5
    )

    state_missing_id = State(inputs={}, results={}, structured_response=None)
    with pytest.raises(ValueError, match="non-empty session id"):
        await node.run(state_missing_id, {})

    invalid_retention = MemorySummarizerNode(
        name="summarizer", memory_store=store, retention_seconds=0
    )
    state = State(
        inputs={"session_id": "sess-100"}, results={}, structured_response=None
    )
    with pytest.raises(ValueError, match="retention_seconds must be positive"):
        await invalid_retention.run(state, {})

    node_no_history = MemorySummarizerNode(
        name="summarizer", memory_store=store, retention_seconds=1
    )
    state = State(
        inputs={"session_id": "sess-200"},
        results={"conversation_state": []},
        structured_response=None,
    )
    result = await node_no_history.run(state, {})

    assert result["summary"] == "No conversation history yet."

    retention_none = MemorySummarizerNode(
        name="summarizer", memory_store=store, retention_seconds=None
    )
    state = State(
        inputs={"session_id": "sess-201"},
        results={
            "conversation_state": {
                "conversation_history": [{"role": "user", "content": "short"}]
            }
        },
        structured_response=None,
    )

    result_none = await retention_none.run(state, {})

    assert result_none["ttl_seconds"] is None


@pytest.mark.asyncio
async def test_memory_summarizer_truncates_long_history() -> None:
    store = InMemoryMemoryStore()
    node = MemorySummarizerNode(
        name="summarizer", memory_store=store, retention_seconds=2, max_summary_tokens=3
    )
    state = State(
        inputs={"session_id": "sess-ellipsis"},
        results={
            "conversation_state": {
                "conversation_history": [
                    {"role": "user", "content": "one two three four"},
                ]
            }
        },
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["summary"].endswith("...")


@pytest.mark.asyncio
async def test_memory_summarizer_uses_existing_summary() -> None:
    store = InMemoryMemoryStore()
    node = MemorySummarizerNode(name="summarizer", memory_store=store)
    state = State(
        inputs={"session_id": "sess-summary"},
        results={
            "conversation_state": {
                "summary": "provided",
                "conversation_history": [{"role": "user", "content": "ignored"}],
            }
        },
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["summary"] == "provided"
