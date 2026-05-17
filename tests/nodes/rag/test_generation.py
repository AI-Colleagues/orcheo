from unittest.mock import MagicMock, patch
import pytest
from orcheo.graph.state import State
from orcheo.nodes.rag.generation import (
    GroundedGeneratorNode,
    SearchResultFormatterNode,
    StreamingGeneratorNode,
    _truncate_snippet,
)
from orcheo.nodes.rag.models import SearchResult


def _state_with_context(query: str) -> State:
    results = {
        "retriever": {
            "results": [
                SearchResult(
                    id="chunk-1",
                    score=0.9,
                    text="Orcheo ships modular nodes for RAG workflows.",
                    metadata={"page": 1},
                    source="vector",
                ),
                SearchResult(
                    id="chunk-2",
                    score=0.8,
                    text="Grounded answers include citations.",
                    metadata={"page": 2},
                    source="bm25",
                ),
            ]
        }
    }
    return State(inputs={"query": query}, results=results, structured_response=None)


@pytest.mark.asyncio
async def test_grounded_generator_appends_citations() -> None:
    node = GroundedGeneratorNode(name="generator")
    state = _state_with_context("What does Orcheo provide?")

    result = await node.run(state, {})

    assert result["citations"]
    assert result["reply"]
    assert result["tokens_used"] > 0


@pytest.mark.asyncio
async def test_grounded_generator_resolves_context_from_results_field() -> None:
    node = GroundedGeneratorNode(name="generator", context_result_key="hybrid")
    source_state = _state_with_context("How are citations handled?")
    retrieval_results = source_state["results"]["retriever"]["results"]
    state = State(
        inputs=source_state["inputs"],
        results={"results": retrieval_results, "hybrid": {}},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["citations"][0]["source_id"] == "chunk-1"


# Retry logic has been removed - retries should be configured via model_kwargs


@pytest.mark.asyncio
async def test_grounded_generator_requires_query_and_context() -> None:
    node = GroundedGeneratorNode(name="generator")
    empty_state = State(inputs={}, results={}, structured_response=None)

    with pytest.raises(
        ValueError, match="GroundedGeneratorNode requires a non-empty query string"
    ):
        await node.run(empty_state, {})

    # Test non-RAG mode: works without context
    state = State(inputs={"query": "hi"}, results={}, structured_response=None)
    result = await node.run(state, {})
    assert result["mode"] == "non_rag"
    assert result["citations"] == []
    assert "reply" in result


@pytest.mark.asyncio
async def test_grounded_generator_rejects_non_list_context_payload() -> None:
    node = GroundedGeneratorNode(name="generator")
    state = State(
        inputs={"query": "hi"},
        results={"retriever": {"results": "not-a-list"}},
        structured_response=None,
    )

    with pytest.raises(
        ValueError, match="Context payload must be a list of retrieval results"
    ):
        await node.run(state, {})


def test_truncate_snippet_enforces_length_and_removes_newlines() -> None:
    text = "  first line\nsecond line third line extra \n"
    snippet = _truncate_snippet(text, limit=25)

    assert "second line" in snippet
    assert "\n" not in snippet
    assert snippet.endswith("…")
    assert len(snippet) <= 25


def test_truncate_snippet_returns_empty_when_limit_non_positive() -> None:
    assert _truncate_snippet("Should be ignored", limit=0) == ""


def test_truncate_snippet_returns_ellipsis_for_minimum_limit() -> None:
    assert _truncate_snippet("visible", limit=1) == "…"


def test_truncate_snippet_handles_truncated_whitespace() -> None:
    class FakeText:
        def strip(self) -> str:  # return whitespace even after strip
            return "    "

    assert _truncate_snippet(FakeText(), limit=3) == "…"


def test_attach_citations_returns_completion_unchanged() -> None:
    node = GroundedGeneratorNode(name="generator")

    assert node._attach_citations("answer", []) == "answer"
    assert node._attach_citations("answer [1]", [{"id": "1"}]) == "answer [1]"
    assert node._attach_citations("answer", [{"id": "1"}]) == "answer"


def test_attach_citations_appends_markers_for_non_inline_styles() -> None:
    """Test that citation markers are appended for footnote/endnote styles."""
    # Test footnote style
    node_footnote = GroundedGeneratorNode(name="generator", citation_style="footnote")
    result = node_footnote._attach_citations("answer", [{"id": "1"}, {"id": "2"}])
    assert result == "answer [1] [2]"

    # Test endnote style
    node_endnote = GroundedGeneratorNode(name="generator", citation_style="endnote")
    result = node_endnote._attach_citations(
        "answer with trailing spaces   ", [{"id": "1"}]
    )
    assert result == "answer with trailing spaces [1]"

    # Test with multiple citations
    node_other = GroundedGeneratorNode(name="generator", citation_style="other")
    result = node_other._attach_citations(
        "text", [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    )
    assert result == "text [a] [b] [c]"


# Retry logic has been removed - retries should be configured via model_kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_result", [123, "   "])
@patch("orcheo.nodes.rag.generation.create_agent")
async def test_invoke_ai_model_rejects_invalid_response(
    mock_create_agent, invalid_result
) -> None:
    # Mock the agent to return invalid result
    async def invalid_invoke(state):
        return {
            "messages": [
                MagicMock(content=invalid_result),
            ]
        }

    mock_agent = MagicMock()
    mock_agent.ainvoke = invalid_invoke
    mock_create_agent.return_value = mock_agent

    node = GroundedGeneratorNode(name="generator", ai_model="gpt-4")

    with pytest.raises(ValueError, match="Agent must return a non-empty string"):
        await node._invoke_ai_model("prompt")


@pytest.mark.asyncio
@patch("orcheo.nodes.rag.generation.create_agent")
async def test_grounded_generator_with_history_containing_empty_turns(
    mock_create_agent,
) -> None:
    """Test that history with non-dict items and empty content is handled."""

    async def mock_invoke(state):
        return {"messages": [MagicMock(content="Response with citations [1]")]}

    mock_agent = MagicMock()
    mock_agent.ainvoke = mock_invoke
    mock_create_agent.return_value = mock_agent

    node = GroundedGeneratorNode(name="generator", ai_model="gpt-4")
    state = _state_with_context("test query")
    # Mix of valid, invalid, and empty history items
    state["inputs"]["history"] = [
        {"role": "user", "content": "first"},
        "not a dict",  # Invalid item
        {"role": "user", "content": ""},  # Empty content
        {"role": "assistant", "content": ""},  # Empty content
        {"role": "other", "content": "ignored"},  # Wrong role
        {"role": "user", "content": "second"},
    ]

    result = await node.run(state, {})

    assert result["mode"] == "rag"
    assert len(result["citations"]) > 0


@pytest.mark.asyncio
@patch("orcheo.nodes.rag.generation.create_agent")
async def test_extract_response_text_from_dict_message(mock_create_agent) -> None:
    """Test extracting response text from dict message."""

    async def mock_invoke(state):
        return {"messages": [{"content": "Response text [1]"}]}

    mock_agent = MagicMock()
    mock_agent.ainvoke = mock_invoke
    mock_create_agent.return_value = mock_agent

    node = GroundedGeneratorNode(name="generator", ai_model="gpt-4")
    state = _state_with_context("test query")

    result = await node.run(state, {})

    assert "Response text" in result["reply"]


@pytest.mark.asyncio
@patch("orcheo.nodes.rag.generation.create_agent")
async def test_extract_response_text_from_non_message_object(mock_create_agent) -> None:
    """Test extracting response when message is not dict or has no content attr."""

    async def mock_invoke(state):
        # Return a message that's neither dict nor has content attribute
        return {"messages": ["plain string message"]}

    mock_agent = MagicMock()
    mock_agent.ainvoke = mock_invoke
    mock_create_agent.return_value = mock_agent

    node = GroundedGeneratorNode(name="generator", ai_model="gpt-4")
    state = _state_with_context("test query")

    result = await node.run(state, {})

    assert "plain string message" in result["reply"]


@pytest.mark.asyncio
async def test_grounded_generator_non_rag_mode() -> None:
    """Test non-RAG mode when no context is available."""
    node = GroundedGeneratorNode(name="generator")
    state = State(
        inputs={"query": "What is the weather?"},
        results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["mode"] == "non_rag"
    assert result["citations"] == []
    assert "reply" in result
    assert result["tokens_used"] > 0


@pytest.mark.asyncio
async def test_estimate_tokens_from_history_with_invalid_items() -> None:
    """Test token estimation with history containing invalid items."""
    node = GroundedGeneratorNode(name="generator")

    # History with non-dict items and empty content
    history = [
        {"role": "user", "content": "hello"},
        "not a dict",
        {"role": "user", "content": ""},
        {"role": "assistant"},  # Missing content
        {"role": "user", "content": "world"},
    ]

    tokens = node._estimate_tokens_from_history(history, "query", "response")

    # Should only count valid content: "hello", "world", "query", "response"
    assert tokens > 0


# StreamingGeneratorNode tests


@pytest.mark.asyncio
@patch("orcheo.nodes.rag.generation.create_agent")
async def test_streaming_generator_with_history_edge_cases(mock_create_agent) -> None:
    """Test StreamingGeneratorNode with history containing edge cases."""
    from orcheo.nodes.rag.generation import StreamingGeneratorNode

    async def mock_invoke(state):
        return {"messages": [MagicMock(content="Streaming response")]}

    mock_agent = MagicMock()
    mock_agent.ainvoke = mock_invoke
    mock_create_agent.return_value = mock_agent

    node = StreamingGeneratorNode(name="streamer", ai_model="gpt-4")
    state = State(
        inputs={
            "message": "test query",
            "history": [
                {"role": "user", "content": "first"},
                "not a dict",  # Invalid
                {"role": "user", "content": ""},  # Empty
                {"role": "assistant", "content": ""},  # Empty
                {"role": "other", "content": "ignored"},  # Wrong role
            ],
        },
        results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["reply"] == "Streaming response"


@pytest.mark.asyncio
@patch("orcheo.nodes.rag.generation.create_agent")
async def test_streaming_generator_extract_from_dict_message(mock_create_agent) -> None:
    """Test StreamingGeneratorNode extracting text from dict message."""
    from orcheo.nodes.rag.generation import StreamingGeneratorNode

    async def mock_invoke(state):
        return {"messages": [{"content": "Dict response"}]}

    mock_agent = MagicMock()
    mock_agent.ainvoke = mock_invoke
    mock_create_agent.return_value = mock_agent

    node = StreamingGeneratorNode(name="streamer", ai_model="gpt-4")
    state = State(
        inputs={"message": "test"},
        results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["reply"] == "Dict response"


@pytest.mark.asyncio
@patch("orcheo.nodes.rag.generation.create_agent")
async def test_streaming_generator_extract_from_non_message_result(
    mock_create_agent,
) -> None:
    """Test StreamingGeneratorNode with non-dict result."""
    from orcheo.nodes.rag.generation import StreamingGeneratorNode

    async def mock_invoke(state):
        # Return non-dict result
        return "plain result"

    mock_agent = MagicMock()
    mock_agent.ainvoke = mock_invoke
    mock_create_agent.return_value = mock_agent

    node = StreamingGeneratorNode(name="streamer", ai_model="gpt-4")
    state = State(
        inputs={"message": "test"},
        results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["reply"] == "plain result"


@pytest.mark.asyncio
@patch("orcheo.nodes.rag.generation.create_agent")
async def test_grounded_generator_with_valid_assistant_history(
    mock_create_agent,
) -> None:
    """Test that valid assistant history is correctly processed."""

    async def mock_invoke(state):
        return {"messages": [MagicMock(content="Response")]}

    mock_agent = MagicMock()
    mock_agent.ainvoke = mock_invoke
    mock_create_agent.return_value = mock_agent

    node = GroundedGeneratorNode(name="generator", ai_model="gpt-4")
    state = _state_with_context("query")
    state["inputs"]["history"] = [{"role": "assistant", "content": "previous answer"}]

    await node.run(state, {})
    # Implicitly covers line 192 by executing the path


@pytest.mark.asyncio
@patch("orcheo.nodes.rag.generation.create_agent")
async def test_grounded_generator_handles_direct_string_result(
    mock_create_agent,
) -> None:
    """Test handling of direct string result from agent."""

    async def mock_invoke(state):
        return "Direct string response"

    mock_agent = MagicMock()
    mock_agent.ainvoke = mock_invoke
    mock_create_agent.return_value = mock_agent

    node = GroundedGeneratorNode(name="generator", ai_model="gpt-4")
    state = _state_with_context("query")

    result = await node.run(state, {})
    assert "Direct string response" in result["reply"]


@pytest.mark.asyncio
@patch("orcheo.nodes.rag.generation.create_agent")
@patch("orcheo.nodes.rag.generation.init_chat_model")
@patch(
    "orcheo.nodes.rag.generation.infer_chat_result_model_name",
    return_value=None,
)
@patch(
    "orcheo.nodes.rag.generation.infer_model_name_from_instance",
    return_value="fallback-model",
)
async def test_grounded_generator_records_trace_metadata_without_model_name(
    mock_infer_model,
    mock_infer_chat,
    mock_init,
    mock_create_agent,
) -> None:
    """Ensure trace metadata falls back to the instantiated model name."""
    fake_model = MagicMock()

    async def fake_invoke(payload):
        return {"messages": [MagicMock(content="Response with citation [1]")]}

    mock_agent = MagicMock()
    mock_agent.ainvoke = fake_invoke
    mock_create_agent.return_value = mock_agent
    mock_init.return_value = fake_model

    node = GroundedGeneratorNode(
        name="generator", ai_model="gpt-4", model_kwargs={"temperature": 0.1}
    )
    node._set_trace_metadata_for_run = MagicMock()
    state = _state_with_context("fallback")

    result = await node.run(state, {})

    assert "Response with citation" in result["reply"]
    node._set_trace_metadata_for_run.assert_called_once()
    trace_payload = node._set_trace_metadata_for_run.call_args[0][0]
    assert trace_payload["ai"]["actual_model"] == "fallback-model"


def test_estimate_tokens_static_method() -> None:
    """Test the static _estimate_tokens method."""
    # "hello" + "world" -> "helloworld" -> 1 token
    count = GroundedGeneratorNode._estimate_tokens("hello", "world")
    assert count == 1

    # "hello " + "world" -> "hello world" -> 2 tokens
    count = GroundedGeneratorNode._estimate_tokens("hello ", "world")
    assert count == 2


@pytest.mark.asyncio
@patch("orcheo.nodes.rag.generation.create_agent")
async def test_streaming_generator_with_valid_assistant_history(
    mock_create_agent,
) -> None:
    """Test StreamingGeneratorNode with valid assistant history."""

    async def mock_invoke(state):
        return {"messages": [MagicMock(content="Response")]}

    mock_agent = MagicMock()
    mock_agent.ainvoke = mock_invoke
    mock_create_agent.return_value = mock_agent

    node = StreamingGeneratorNode(name="streamer", ai_model="gpt-4")
    state = State(
        inputs={
            "message": "query",
            "history": [{"role": "assistant", "content": "previous"}],
        },
        results={},
        structured_response=None,
    )

    await node.run(state, {})
    # Implicitly covers line 375


@pytest.mark.asyncio
@patch("orcheo.nodes.rag.generation.create_agent")
async def test_streaming_generator_handles_string_message_in_list(
    mock_create_agent,
) -> None:
    """Test StreamingGeneratorNode handling string message in messages list."""

    async def mock_invoke(state):
        return {"messages": ["string message"]}

    mock_agent = MagicMock()
    mock_agent.ainvoke = mock_invoke
    mock_create_agent.return_value = mock_agent

    node = StreamingGeneratorNode(name="streamer", ai_model="gpt-4")
    state = State(
        inputs={"message": "query"},
        results={},
        structured_response=None,
    )

    result = await node.run(state, {})
    assert result["reply"] == "string message"


# SearchResultFormatterNode tests


def _formatter_state(entries: list[SearchResult] | None = None) -> State:
    payload = {"results": entries} if entries is not None else {}
    return State(
        inputs={},
        results={"retriever": payload},
        structured_response=None,
    )


@pytest.mark.asyncio
async def test_formatter_returns_empty_message_when_no_entries() -> None:
    node = SearchResultFormatterNode(name="formatter")
    state = _formatter_state([])

    result = await node.run(state, {})

    assert result["markdown"] == "No results found."


@pytest.mark.asyncio
async def test_formatter_includes_header() -> None:
    entries = [
        SearchResult(
            id="r1",
            score=0.9,
            text="body",
            metadata={"title": "First"},
        )
    ]
    node = SearchResultFormatterNode(name="formatter", header="## Results")
    state = _formatter_state(entries)

    result = await node.run(state, {})

    assert result["markdown"].startswith("## Results\n")


@pytest.mark.asyncio
async def test_formatter_omits_header_when_empty() -> None:
    entries = [
        SearchResult(
            id="r1",
            score=0.5,
            text="body",
            metadata={"title": "First"},
        )
    ]
    node = SearchResultFormatterNode(name="formatter", header="")
    state = _formatter_state(entries)

    result = await node.run(state, {})

    assert not result["markdown"].startswith("\n")


@pytest.mark.asyncio
async def test_formatter_uses_title_fallback_when_no_title_in_metadata() -> None:
    entries = [SearchResult(id="r1", score=0.5, text="body", metadata={})]
    node = SearchResultFormatterNode(name="formatter", header="")
    state = _formatter_state(entries)

    result = await node.run(state, {})

    assert "Result 1" in result["markdown"]


@pytest.mark.asyncio
async def test_formatter_includes_score() -> None:
    entries = [
        SearchResult(
            id="r1",
            score=0.123456,
            text="body",
            metadata={"title": "T"},
        )
    ]
    node = SearchResultFormatterNode(
        name="formatter", include_score=True, score_precision=2, header=""
    )
    state = _formatter_state(entries)

    result = await node.run(state, {})

    assert "(score: 0.12)" in result["markdown"]


@pytest.mark.asyncio
async def test_formatter_falls_back_to_text_for_snippet() -> None:
    entries = [
        SearchResult(
            id="r1",
            score=0.5,
            text="  The actual content  ",
            metadata={"title": "T"},
        )
    ]
    node = SearchResultFormatterNode(name="formatter", fallback_to_text=True, header="")
    state = _formatter_state(entries)

    result = await node.run(state, {})

    assert "Snippet: The actual content" in result["markdown"]


@pytest.mark.asyncio
async def test_formatter_shows_snippet_from_metadata() -> None:
    entries = [
        SearchResult(
            id="r1",
            score=0.5,
            text="body",
            metadata={"title": "T", "snippet": "Meta snippet"},
        )
    ]
    node = SearchResultFormatterNode(name="formatter", header="")
    state = _formatter_state(entries)

    result = await node.run(state, {})

    assert "Snippet: Meta snippet" in result["markdown"]


@pytest.mark.asyncio
async def test_formatter_shows_url_from_metadata() -> None:
    entries = [
        SearchResult(
            id="r1",
            score=0.5,
            text="body",
            metadata={"title": "T", "url": "https://example.com"},
        )
    ]
    node = SearchResultFormatterNode(name="formatter", header="")
    state = _formatter_state(entries)

    result = await node.run(state, {})

    assert "Source: https://example.com" in result["markdown"]


@pytest.mark.asyncio
async def test_formatter_strips_trailing_blank_line() -> None:
    entries = [SearchResult(id="r1", score=0.5, text="body", metadata={"title": "T"})]
    node = SearchResultFormatterNode(name="formatter", header="")
    state = _formatter_state(entries)

    result = await node.run(state, {})

    assert not result["markdown"].endswith("\n")


@pytest.mark.asyncio
async def test_formatter_resolve_results_payload_not_dict() -> None:
    """Covers _resolve_results when payload is not a dict with results_field."""
    state = State(
        inputs={},
        results={"retriever": [{"id": "r1", "score": 0.5, "text": "t"}]},
        structured_response=None,
    )
    node = SearchResultFormatterNode(name="formatter", header="")

    result = await node.run(state, {})

    assert "1. Result 1" in result["markdown"]


@pytest.mark.asyncio
async def test_formatter_resolve_results_none_entries() -> None:
    state = State(
        inputs={},
        results={"retriever": {"results": None}},
        structured_response=None,
    )
    node = SearchResultFormatterNode(name="formatter")

    result = await node.run(state, {})

    assert result["markdown"] == "No results found."


@pytest.mark.asyncio
async def test_formatter_resolve_results_rejects_non_list() -> None:
    state = State(
        inputs={},
        results={"retriever": {"results": "invalid"}},
        structured_response=None,
    )
    node = SearchResultFormatterNode(name="formatter")

    with pytest.raises(
        ValueError,
        match="SearchResultFormatterNode requires a list of retrieval results",
    ):
        await node.run(state, {})


def test_formatter_format_title_fallback_handles_format_error() -> None:
    node = SearchResultFormatterNode(name="formatter", title_fallback="{bad_key}")
    entry = SearchResult(id="r1", score=0.5, text="t", metadata={})

    result = node._format_title_fallback(entry, 1)

    assert result == "Result 1"


def test_formatter_pick_field_returns_none_for_empty_metadata() -> None:
    result = SearchResultFormatterNode._pick_field({}, ["title", "name"])

    assert result is None


def test_formatter_pick_field_skips_non_string_values() -> None:
    result = SearchResultFormatterNode._pick_field(
        {"title": 123, "name": "Valid"}, ["title", "name"]
    )

    assert result == "Valid"


def test_formatter_pick_field_skips_blank_strings() -> None:
    result = SearchResultFormatterNode._pick_field(
        {"title": "   ", "name": "Valid"}, ["title", "name"]
    )

    assert result == "Valid"


def test_formatter_format_score_returns_na_for_non_numeric() -> None:
    node = SearchResultFormatterNode(name="formatter")

    assert node._format_score("not-a-number") == "n/a"
    assert node._format_score(None) == "n/a"


@pytest.mark.asyncio
async def test_formatter_excludes_score_when_disabled() -> None:
    entries = [SearchResult(id="r1", score=0.5, text="body", metadata={"title": "T"})]
    node = SearchResultFormatterNode(name="formatter", include_score=False, header="")
    state = _formatter_state(entries)

    result = await node.run(state, {})

    assert "score" not in result["markdown"]


@pytest.mark.asyncio
async def test_formatter_no_snippet_and_no_fallback() -> None:
    """Covers the branch where snippet is empty and fallback_to_text is False."""
    entries = [
        SearchResult(id="r1", score=0.5, text="body text", metadata={"title": "T"})
    ]
    node = SearchResultFormatterNode(
        name="formatter", fallback_to_text=False, header=""
    )
    state = _formatter_state(entries)

    result = await node.run(state, {})

    assert "Snippet" not in result["markdown"]
