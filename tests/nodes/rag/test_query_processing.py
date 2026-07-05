from typing import Any
import pytest
from orcheo.graph.state import State
from orcheo.nodes.rag.models import SearchResult
from orcheo.nodes.rag.query_processing import (
    ContextCompressorNode,
    CoreferenceResolverNode,
    QueryClassifierNode,
    QueryRewriteNode,
    _normalize_messages,
)


@pytest.mark.asyncio
async def test_query_rewrite_rewrites_with_ai_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class StubModel:
        async def ainvoke(self, messages: list[Any]) -> Any:  # type: ignore[override]
            captured["messages"] = messages
            return type("Resp", (), {"content": "How does the vector store scale?"})

    def fake_init_chat_model(*_: Any, **__: Any) -> StubModel:
        return StubModel()

    monkeypatch.setattr(
        "orcheo.nodes.rag.query_processing.init_chat_model",
        fake_init_chat_model,
    )

    node = QueryRewriteNode(
        name="rewrite", ai_model="openai:gpt-4o-mini", max_history_messages=2
    )
    state = State(
        inputs={
            "query": "How does it scale?",
            "history": [
                {"role": "user", "content": "Tell me about the vector store"},
                {"role": "assistant", "content": "It supports namespaces"},
            ],
        },
        node_results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["used_history"] is True
    assert result["query"] == "How does the vector store scale?"
    assert result["original_query"] == "How does it scale?"
    system_msg = captured["messages"][0]
    human_msg = captured["messages"][1]
    assert "query rewriter" in system_msg.content.lower()
    assert "How does it scale?" in human_msg.content


@pytest.mark.asyncio
async def test_coreference_resolution_replaces_first_pronoun() -> None:
    node = CoreferenceResolverNode(name="coref")
    state = State(
        inputs={
            "query": "How does it work?",
            "history": [{"role": "user", "content": "The retriever pipeline"}],
        },
        node_results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["resolved"] is True
    assert result["antecedent"] == "The retriever pipeline"
    assert "The retriever pipeline" in result["query"]


@pytest.mark.asyncio
async def test_query_classifier_uses_heuristics() -> None:
    node = QueryClassifierNode(name="classifier")
    state = State(
        inputs={"query": "Can you clarify which one to use?"},
        node_results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["classification"] == "clarification"
    assert result["confidence"] == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_query_classifier_defaults_to_search() -> None:
    node = QueryClassifierNode(name="classifier-default")
    state = State(
        inputs={"query": "List retrieval options"},
        node_results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result == {"classification": "search", "confidence": 0.6}


@pytest.mark.asyncio
async def test_query_classifier_finalization_branch() -> None:
    node = QueryClassifierNode(name="classifier-finalize")
    state = State(
        inputs={"query": "Thanks for the help!"},
        node_results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result == {"classification": "finalization", "confidence": 0.9}


@pytest.mark.asyncio
async def test_context_compressor_summarizes_and_deduplicates() -> None:
    node = ContextCompressorNode(name="compress", max_tokens=5)
    results = [
        SearchResult(id="a", score=0.9, text="first chunk", metadata={}),
        SearchResult(id="a", score=0.8, text="duplicate chunk", metadata={}),
        SearchResult(id="b", score=0.5, text="second chunk here", metadata={}),
    ]
    state = State(
        inputs={"query": "demo"},
        node_results={"retrieval_results": results},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert [item.id for item in result["original_results"]] == ["a", "b"]
    assert result["results"][0].metadata["source_ids"] == ["a", "b"]
    assert result["summary"].startswith("[1] first chunk [2] second")


def test_normalize_messages_handles_mixed_payloads() -> None:
    history = ["hello", {"content": "world"}, {"missing": "content"}, 42]

    assert _normalize_messages(history) == ["hello", "world"]


@pytest.mark.asyncio
async def test_query_rewrite_skips_model_without_history() -> None:
    node = QueryRewriteNode(name="rewrite-no-context", ai_model="openai:gpt-4o-mini")
    state = State(
        inputs={"query": "Tell me about graphs", "history": []},
        node_results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["query"] == "Tell me about graphs"
    assert result["used_history"] is False


@pytest.mark.asyncio
async def test_query_rewrite_validates_history_type() -> None:
    node = QueryRewriteNode(name="rewrite-error", ai_model="openai:gpt-4o-mini")
    state = State(
        inputs={"query": "hello", "history": "not-a-list"},
        node_results={},
        structured_response=None,
    )

    with pytest.raises(ValueError, match="history must be a list of messages"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_query_rewrite_requires_query() -> None:
    node = QueryRewriteNode(name="rewrite-empty", ai_model="openai:gpt-4o-mini")
    state = State(inputs={"query": "   "}, node_results={}, structured_response=None)

    with pytest.raises(
        ValueError, match="QueryRewriteNode requires a non-empty query string"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_coreference_resolver_handles_missing_referent() -> None:
    node = CoreferenceResolverNode(name="coref-none")
    state = State(
        inputs={"query": "Where is it?", "history": []},
        node_results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result == {"query": "Where is it?", "resolved": False, "antecedent": None}


@pytest.mark.asyncio
async def test_coreference_resolver_without_matching_pronoun() -> None:
    node = CoreferenceResolverNode(name="coref-no-pronoun")
    state = State(
        inputs={
            "query": "Explain the architecture",
            "history": ["Vector store overview"],
        },
        node_results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result == {
        "query": "Explain the architecture",
        "resolved": False,
        "antecedent": None,
    }


@pytest.mark.asyncio
async def test_coreference_resolver_validates_history_type() -> None:
    node = CoreferenceResolverNode(name="coref-error")
    state = State(
        inputs={"query": "hello", "history": "oops"},
        node_results={},
        structured_response=None,
    )

    with pytest.raises(ValueError, match="history must be a list of messages"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_coreference_resolver_requires_query() -> None:
    node = CoreferenceResolverNode(name="coref-empty")
    state = State(inputs={"query": ""}, node_results={}, structured_response=None)

    with pytest.raises(
        ValueError, match="CoreferenceResolverNode requires a non-empty query string"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_query_classifier_requires_query() -> None:
    node = QueryClassifierNode(name="classifier-empty")
    state = State(inputs={"query": ""}, node_results={}, structured_response=None)

    with pytest.raises(
        ValueError, match="QueryClassifierNode requires a non-empty query string"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_context_compressor_handles_results_mapping() -> None:
    node = ContextCompressorNode(name="compress-mapping", max_tokens=10)
    search_result = SearchResult(id="a", score=0.5, text="small chunk", metadata={})
    state = State(
        inputs={},
        node_results={"retrieval_results": {"results": [search_result]}},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["results"][0].source == "summary"
    assert "small chunk" in result["summary"]


@pytest.mark.asyncio
async def test_context_compressor_requires_results() -> None:
    node = ContextCompressorNode(name="compress-missing")
    state = State(inputs={}, node_results={}, structured_response=None)

    with pytest.raises(
        ValueError,
        match="ContextCompressorNode requires retrieval results to summarize",
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_context_compressor_validates_entries_type() -> None:
    node = ContextCompressorNode(name="compress-type")
    state = State(
        inputs={},
        node_results={"retrieval_results": "not-a-list"},
        structured_response=None,
    )

    with pytest.raises(
        ValueError, match="retrieval results must be provided as a list"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_context_compressor_without_deduplication() -> None:
    node = ContextCompressorNode(
        name="compress-no-dedup", max_tokens=10, deduplicate=False
    )
    results = [
        SearchResult(id="a", score=0.2, text="alpha beta", metadata={}),
        SearchResult(id="a", score=0.1, text="gamma delta", metadata={}),
    ]
    state = State(
        inputs={}, node_results={"retrieval_results": results}, structured_response=None
    )

    result = await node.run(state, {})

    assert [item.id for item in result["original_results"]] == ["a", "a"]
    assert result["summary"].startswith("[1] alpha beta\n[2] gamma")


@pytest.mark.asyncio
async def test_context_compressor_invokes_ai_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class StubModel:
        async def ainvoke(self, messages: list[Any]) -> Any:  # type: ignore[override]
            captured["messages"] = messages
            return type("Resp", (), {"content": "AI summary"})

    def fake_init_chat_model(*_: Any, **__: Any) -> StubModel:
        return StubModel()

    monkeypatch.setattr(
        "orcheo.nodes.rag.query_processing.init_chat_model",
        fake_init_chat_model,
    )

    node = ContextCompressorNode(name="compress-ai", ai_model="fake-model")
    results = [
        SearchResult(id="x", score=0.9, text="context", metadata={}),
    ]
    state = State(
        inputs={"query": "What is context?"},
        node_results={"retrieval_results": results},
        structured_response=None,
    )

    summary = await node.run(state, {})

    assert summary["summary"] == "AI summary"
    system_message = captured["messages"][0]
    human_message = captured["messages"][1]
    assert "retrieved context" in human_message.content.lower()
    assert "summarizer" in system_message.content.lower()


@pytest.mark.asyncio
async def test_context_compressor_returns_empty_when_no_entries() -> None:
    node = ContextCompressorNode(name="compress-empty")
    state = State(
        inputs={}, node_results={"retrieval_results": []}, structured_response=None
    )

    result = await node.run(state, {})

    assert result["results"] == []
    assert result["summary"] == ""
    assert result["original_results"] == []


@pytest.mark.asyncio
async def test_context_compressor_handles_blank_context_block() -> None:
    node = ContextCompressorNode(name="compress-blank")
    entry = SearchResult(
        id="blank",
        score=0.5,
        text="   ",
        metadata={},
        source="blank",
        sources=["blank"],
    )
    state = State(
        inputs={"query": "question"},
        node_results={"retrieval_results": [entry]},
        structured_response=None,
    )

    node._build_context_block = lambda entries: ""

    result = await node.run(state, {})

    assert result["summary"] == ""
    assert result["original_results"][0].id == "blank"


@pytest.mark.asyncio
async def test_context_compressor_summary_with_model_requires_ai_model() -> None:
    node = ContextCompressorNode(name="compress-model")

    with pytest.raises(
        ValueError,
        match="AI model identifier is required for model-based summarization",
    ):
        await node._summarize_with_model("query", "context")


@pytest.mark.asyncio
async def test_context_compressor_summary_with_model_handles_empty_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StubModel:
        async def ainvoke(self, messages: list[Any]) -> Any:  # type: ignore[override]
            return type("Resp", (), {"content": ""})()

    def fake_init_chat_model(*_: Any, **__: Any) -> Any:
        return StubModel()

    monkeypatch.setattr(
        "orcheo.nodes.rag.query_processing.init_chat_model",
        fake_init_chat_model,
    )

    node = ContextCompressorNode(name="compress-model-response", ai_model="fake")
    entry = SearchResult(
        id="x",
        score=0.8,
        text="content",
        metadata={},
        source="source",
        sources=["source"],
    )
    state = State(
        inputs={"query": "What is context?"},
        node_results={"retrieval_results": [entry]},
        structured_response=None,
    )

    with pytest.raises(ValueError, match="Summarizer model returned an empty response"):
        await node.run(state, {})


def test_context_compressor_collect_sources_defaults_to_retrieval() -> None:
    entry = SearchResult(
        id="a", score=1.0, text="text", metadata={}, source=None, sources=[]
    )

    assert ContextCompressorNode._collect_sources([entry]) == ["retrieval"]


@pytest.mark.asyncio
async def test_query_rewrite_falls_back_on_empty_ai_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Covers line 104: fallback when AI model returns empty content."""

    class EmptyModel:
        async def ainvoke(self, messages: list[Any]) -> Any:  # type: ignore[override]
            return type("Resp", (), {"content": ""})

    def fake_init_chat_model(*_: Any, **__: Any) -> EmptyModel:
        return EmptyModel()

    monkeypatch.setattr(
        "orcheo.nodes.rag.query_processing.init_chat_model",
        fake_init_chat_model,
    )

    node = QueryRewriteNode(name="rewrite-fallback", ai_model="openai:gpt-4o-mini")
    state = State(
        inputs={
            "query": "How does it scale?",
            "history": [{"role": "user", "content": "Vector store overview"}],
        },
        node_results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["query"] == "How does it scale?"
    assert result["used_history"] is True
