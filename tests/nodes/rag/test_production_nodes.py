import sys
import time
import types
from typing import Any
from unittest.mock import MagicMock, patch
import pytest
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.rag.conversation import (
    AnswerCachingNode,
    InMemoryMemoryStore,
    SessionManagementNode,
)
from orcheo.nodes.rag.generation import (
    CitationsFormatterNode,
    HallucinationGuardNode,
    SearchResultFormatterNode,
    StreamingGeneratorNode,
)
from orcheo.nodes.rag.ingestion import (
    IncrementalIndexerNode,
)
from orcheo.nodes.rag.models import DocumentChunk, SearchResult
from orcheo.nodes.rag.query_processing import MultiHopPlannerNode
from orcheo.nodes.rag.retrieval import (
    PineconeRerankNode,
    ReRankerNode,
    SearchResultAdapterNode,
    SourceRouterNode,
)
from orcheo.nodes.rag.vector_store import (
    BaseVectorStore,
    InMemoryVectorStore,
)


class FlakyVectorStore(InMemoryVectorStore):
    failures: int = 1

    async def upsert(self, records) -> None:  # type: ignore[override]
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("transient failure")
        await super().upsert(records)


@pytest.mark.asyncio
async def test_incremental_indexer_retries_and_skips_duplicates() -> None:
    store = FlakyVectorStore()
    chunks = [
        DocumentChunk(
            id="chunk-1",
            document_id="doc-1",
            index=0,
            content="first chunk",
            metadata={"page": 1},
        ),
        DocumentChunk(
            id="chunk-2",
            document_id="doc-2",
            index=0,
            content="second chunk",
            metadata={"page": 2},
        ),
    ]
    node = IncrementalIndexerNode(
        name="indexer",
        vector_store=store,
        embed_model="test:fake",
        max_retries=1,
        backoff_seconds=0.0,
    )
    state = State(
        inputs={},
        node_results={"chunking_strategy": {"chunks": chunks}},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["indexed_count"] == 2
    assert result["skipped"] == 0

    second_result = await node.run(state, {})

    assert second_result["indexed_count"] == 0
    assert second_result["skipped"] == 2
    assert len(store.records) == 2


@pytest.mark.asyncio
async def test_incremental_indexer_raises_after_exhausting_retries() -> None:
    store = FlakyVectorStore(failures=3)
    chunk = DocumentChunk(
        id="chunk-err",
        document_id="doc",
        index=0,
        content="content",
        metadata={},
    )
    node = IncrementalIndexerNode(
        name="indexer-error",
        vector_store=store,
        embed_model="test:fake",
        max_retries=0,
        backoff_seconds=0.0,
    )
    state = State(inputs={}, node_results={"chunks": [chunk]}, structured_response=None)

    with pytest.raises(RuntimeError, match="upsert failed after retries"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_incremental_indexer_validates_inputs() -> None:
    node = IncrementalIndexerNode(
        name="indexer-empty",
        vector_store=InMemoryVectorStore(),
        embed_model="test:fake",
    )
    empty_state = State(inputs={}, node_results={}, structured_response=None)

    with pytest.raises(
        ValueError, match="IncrementalIndexerNode requires at least one chunk"
    ):
        await node.run(empty_state, {})


class ListRecordStore(BaseVectorStore):
    records: list[str] = Field(default_factory=list)

    async def upsert(self, records) -> None:  # type: ignore[override]
        self.records.append("done")

    async def search(self, query, top_k=10, filter_metadata=None):  # type: ignore[override]
        return []


@pytest.mark.asyncio
async def test_incremental_indexer_handles_invalid_payloads_and_record_check() -> None:
    node = IncrementalIndexerNode(
        name="indexer-invalid",
        vector_store=InMemoryVectorStore(),
        embed_model="test:fake",
    )
    with pytest.raises(ValueError, match="chunks payload must be a list"):
        await node.run(
            State(inputs={}, node_results={"chunks": "bad"}, structured_response=None),
            {},
        )

    chunk = DocumentChunk(
        id="c-unchanged", document_id="doc-1", index=0, content="body", metadata={}
    )
    node_skip = IncrementalIndexerNode(
        name="indexer-weird",
        vector_store=ListRecordStore(),
        skip_unchanged=True,
        embed_model="test:fake",
    )
    result = await node_skip.run(
        State(inputs={}, node_results={"chunks": [chunk]}, structured_response=None), {}
    )

    assert result["indexed_count"] == 1


@pytest.mark.asyncio
async def test_incremental_indexer_persists_dense_embeddings() -> None:
    chunk = DocumentChunk(
        id="chunk-dense",
        document_id="doc-dense",
        index=0,
        content="body",
        metadata={},
    )

    store = InMemoryVectorStore()
    node = IncrementalIndexerNode(
        name="indexer-dense",
        vector_store=store,
        embed_model="test:fake",
        skip_unchanged=False,
    )
    state = State(inputs={}, node_results={"chunks": [chunk]}, structured_response=None)

    result = await node.run(state, {})

    assert result["indexed_count"] == 1
    stored = store.records.get("chunk-dense")
    assert stored is not None
    assert stored.values == [float(len("body"))]
    assert stored.sparse_values is None


@pytest.mark.asyncio
async def test_incremental_indexer_records_trace_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = DocumentChunk(
        id="chunk-trace",
        document_id="doc-trace",
        index=0,
        content="trace",
        metadata={},
    )

    fake_model = MagicMock()

    async def fake_embed(texts: list[str]) -> list[list[float]]:
        return [[float(len(texts[0]))]]

    fake_model.aembed_documents = fake_embed

    import orcheo.nodes.rag.embeddings as emb_mod

    monkeypatch.setattr(
        emb_mod, "init_dense_embeddings", lambda *args, **kwargs: fake_model
    )

    node = IncrementalIndexerNode(
        name="indexer-trace",
        vector_store=InMemoryVectorStore(),
        embed_model="test:fake",
        skip_unchanged=False,
        batch_size=1,
    )
    node._set_trace_metadata_for_run = MagicMock()
    state = State(inputs={}, node_results={"chunks": [chunk]}, structured_response=None)

    await node.run(state, {})

    node._set_trace_metadata_for_run.assert_called_once()


@pytest.mark.asyncio
@patch("orcheo.nodes.rag.generation.create_agent")
async def test_streaming_generator_truncates_and_chunks_tokens(
    mock_create_agent,
) -> None:
    async def mock_invoke(state):
        return {
            "messages": [
                MagicMock(content="one two three four"),
            ]
        }

    # Mock the agent
    mock_agent = MagicMock()
    mock_agent.ainvoke = mock_invoke
    mock_create_agent.return_value = mock_agent

    node = StreamingGeneratorNode(
        name="stream",
        ai_model="gpt-4",
        chunk_size=2,
        buffer_limit=3,
    )
    state = State(inputs={"prompt": "start"}, node_results={}, structured_response=None)

    result = await node.run(state, {})

    assert result["token_count"] == 3
    assert result["truncated"] is True
    assert all(frame["size"] <= 2 for frame in result["frames"])


@pytest.mark.asyncio
async def test_streaming_generator_uses_default_llm() -> None:
    node = StreamingGeneratorNode(
        name="stream-default", buffer_limit=None, chunk_size=3
    )
    state = State(inputs={"prompt": "echo"}, node_results={}, structured_response=None)

    result = await node.run(state, {})

    assert result["reply"].endswith(":: streamed")
    assert result["frames"]


@pytest.mark.asyncio
async def test_hallucination_guard_blocks_missing_markers() -> None:
    node = HallucinationGuardNode(name="guard")
    state = State(
        inputs={},
        node_results={
            "grounded_generator": {
                "reply": "answer without markers",
                "citations": [{"id": "1", "snippet": "snippet"}],
            }
        },
        structured_response=None,
    )

    blocked = await node.run(state, {})

    assert blocked["allowed"] is False
    assert "missing citation markers" in blocked["reason"]
    assert "reply" in blocked
    assert blocked["reply"] == "Unable to provide an answer with proper grounding."


@pytest.mark.asyncio
async def test_hallucination_guard_allows_valid_output() -> None:
    node = HallucinationGuardNode(name="guard-allow")
    state = State(
        inputs={},
        node_results={
            "grounded_generator": {
                "reply": "answer [1]",
                "citations": [{"id": "1", "snippet": "snippet"}],
            }
        },
        structured_response=None,
    )

    allowed = await node.run(state, {})

    assert allowed["allowed"] is True
    assert allowed["reply"].startswith("answer")


@pytest.mark.asyncio
async def test_hallucination_guard_validates_payload_and_citations() -> None:
    node = HallucinationGuardNode(name="guard-extra")
    with pytest.raises(ValueError, match="mapping payload"):
        await node.run(
            State(
                inputs={},
                node_results={"grounded_generator": []},
                structured_response=None,
            ),
            {},
        )

    state_missing = State(
        inputs={},
        node_results={"grounded_generator": {"reply": "reply", "citations": []}},
        structured_response=None,
    )
    blocked = await node.run(state_missing, {})
    assert blocked["allowed"] is False
    assert "reply" in blocked
    assert blocked["reply"] == "Unable to provide an answer with proper grounding."

    state_bad = State(
        inputs={},
        node_results={
            "grounded_generator": {
                "reply": "reply [1]",
                "citations": ["not-a-dict"],
            }
        },
        structured_response=None,
    )
    with pytest.raises(ValueError, match="Citations must be dictionaries"):
        await node.run(state_bad, {})


@pytest.mark.asyncio
async def test_hallucination_guard_blocks_empty_snippet() -> None:
    node = HallucinationGuardNode(name="guard-snippet")
    state = State(
        inputs={},
        node_results={
            "grounded_generator": {
                "reply": "content [1]",
                "citations": [{"id": "1", "snippet": ""}],
            }
        },
        structured_response=None,
    )

    blocked = await node.run(state, {})
    assert blocked["allowed"] is False
    assert "reply" in blocked
    assert blocked["reply"] == "Unable to provide an answer with proper grounding."


@pytest.mark.asyncio
async def test_hallucination_guard_handles_empty_response_and_missing_ids() -> None:
    node = HallucinationGuardNode(name="guard-empty-response")
    empty_response_state = State(
        inputs={},
        node_results={"grounded_generator": {"reply": "", "citations": []}},
        structured_response=None,
    )
    with pytest.raises(ValueError, match="Response payload is missing or empty"):
        await node.run(empty_response_state, {})

    node.require_markers = False
    allowed = await node.run(
        State(
            inputs={},
            node_results={
                "grounded_generator": {
                    "reply": "text",
                    "citations": [{"snippet": "s"}],
                }
            },
            structured_response=None,
        ),
        {},
    )
    assert allowed["allowed"] is True

    missing_id_node = HallucinationGuardNode(name="guard-missing-id")
    allowed_missing = await missing_id_node.run(
        State(
            inputs={},
            node_results={
                "grounded_generator": {
                    "reply": "ok",
                    "citations": [{"snippet": "s"}],
                }
            },
            structured_response=None,
        ),
        {},
    )
    assert allowed_missing["allowed"] is True


@pytest.mark.asyncio
async def test_reranker_and_router_prioritize_trusted_results() -> None:
    entries = [
        SearchResult(
            id="res-1",
            score=0.2,
            text="short text",
            metadata={"signal": "trusted"},
            source="vector",
        ),
        SearchResult(
            id="res-2",
            score=0.5,
            text="longer text passage",
            metadata={},
            source="bm25",
        ),
    ]

    def rerank(entry: SearchResult) -> float:
        bonus = 0.8 if entry.metadata.get("signal") == "trusted" else 0.0
        return entry.score + bonus

    reranker = ReRankerNode(
        name="rerank", rerank_function=rerank, length_penalty=0.0, top_k=2
    )
    state = State(
        inputs={},
        node_results={"retriever": {"results": entries}},
        structured_response=None,
    )

    reranked = await reranker.run(state, {})

    assert reranked["results"][0].id == "res-1"

    router = SourceRouterNode(name="router", min_score=0.3)
    routed = await router.run(state, {})

    assert set(routed["routed"].keys()) == {"vector", "bm25"}
    assert all(result.score >= 0.3 for result in routed["routed"]["bm25"])


@pytest.mark.asyncio
async def test_reranker_length_penalty_and_router_validation() -> None:
    entries = [
        SearchResult(
            id="alpha",
            score=0.6,
            text="lengthy text example",
            metadata={},
            source="hybrid",
        )
    ]
    reranker = ReRankerNode(name="rerank-penalty", length_penalty=0.1, top_k=1)
    reranked = await reranker.run(
        State(inputs={}, node_results={"retriever": entries}, structured_response=None),
        {},
    )

    assert reranked["results"][0].score < 0.6

    router = SourceRouterNode(name="router-invalid")
    with pytest.raises(ValueError, match="list of retrieval results"):
        await router.run(
            State(
                inputs={}, node_results={"retriever": "bad"}, structured_response=None
            ),
            {},
        )

    with pytest.raises(ValueError, match="list of retrieval results"):
        await reranker.run(
            State(
                inputs={},
                node_results={"retriever": {"results": "oops"}},
                structured_response=None,
            ),
            {},
        )


@pytest.mark.asyncio
async def test_pinecone_rerank_node_calls_inference_and_orders_results() -> None:
    class _FakeRerankResult:
        def __init__(self, data: list[dict[str, Any]]) -> None:
            self.data = data

    class _FakeInference:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def rerank(self, **kwargs: Any) -> _FakeRerankResult:
            self.calls.append(kwargs)
            return _FakeRerankResult(
                data=[
                    {
                        "document": {"_id": "res-2", "chunk_text": "second text"},
                        "score": 0.95,
                    },
                    {
                        "document": {"_id": "res-1", "chunk_text": "first text"},
                        "score": 0.9,
                    },
                ]
            )

    fake_module = types.ModuleType("pinecone")

    class _FakePinecone:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.inference = _FakeInference()
            fake_module.client = self

    fake_module.Pinecone = _FakePinecone

    entries = [
        SearchResult(
            id="res-1",
            score=0.2,
            text="first text",
            metadata={"tier": "a"},
            source="dense",
            sources=["dense"],
        ),
        SearchResult(
            id="res-2",
            score=0.3,
            text="second text",
            metadata={"tier": "b"},
            source="bm25",
            sources=["bm25"],
        ),
    ]
    state = State(
        inputs={"query": "legal query"},
        node_results={"fusion": {"results": entries}},
        structured_response=None,
    )

    node = PineconeRerankNode(
        name="pinecone-rerank",
        client_kwargs={"api_key": "token"},
        top_n=2,
        parameters={"truncate": "END"},
    )

    with patch.dict(sys.modules, {"pinecone": fake_module}):
        reranked = await node.run(state, {})

    client = fake_module.client
    assert client.kwargs == {"api_key": "token"}
    call = client.inference.calls[0]
    assert call["model"] == "bge-reranker-v2-m3"
    assert call["query"] == "legal query"
    assert call["rank_fields"] == ["chunk_text"]
    assert call["parameters"] == {"truncate": "END"}
    documents = call["documents"]
    assert documents[0]["_id"] == "res-1"
    assert documents[1]["chunk_text"] == "second text"
    assert documents[0]["metadata"] == entries[0].metadata

    assert reranked["results"][0].id == "res-2"
    assert reranked["results"][1].id == "res-1"
    assert reranked["results"][0].source == "bm25"


@pytest.mark.asyncio
async def test_citations_formatter_normalizes_entries() -> None:
    citations = [
        {"id": "1", "snippet": "text", "sources": ["vector"]},
        {"id": "2", "snippet": "other", "sources": []},
    ]
    node = CitationsFormatterNode(name="formatter")
    state = State(
        inputs={},
        node_results={
            "grounded_generator": {"reply": "Answer base [1]", "citations": citations}
        },
        structured_response=None,
    )

    formatted = await node.run(state, {})

    assert formatted["formatted"][0].startswith("[1]")
    assert formatted["reply"].startswith("Answer base [1]")
    assert "References:" in formatted["reply"]
    assert "- [1] text (sources: vector)" in formatted["reply"]
    assert "- [2] other" not in formatted["reply"]
    assert formatted["citations"][0]["sources"] == ["vector"]


@pytest.mark.asyncio
async def test_citations_formatter_links_snippet_when_metadata_includes_url() -> None:
    citations = [
        {
            "id": "1",
            "snippet": "text",
            "sources": ["vector"],
            "metadata": {"url": "https://example.com", "title": "Example Title"},
        }
    ]
    node = CitationsFormatterNode(name="formatter-links")
    state = State(
        inputs={},
        node_results={
            "grounded_generator": {"reply": "Answer base [1]", "citations": citations}
        },
        structured_response=None,
    )

    formatted = await node.run(state, {})

    assert "References:" in formatted["reply"]
    assert (
        "- [1] text (sources: vector | [source](https://example.com))"
        in formatted["reply"]
    )
    assert (
        "(sources: vector | [source](https://example.com))" in formatted["formatted"][0]
    )


@pytest.mark.asyncio
async def test_citations_formatter_validates_payload() -> None:
    node = CitationsFormatterNode(name="formatter-invalid")
    with pytest.raises(ValueError, match="requires a list of citations"):
        await node.run(
            State(
                inputs={},
                node_results={"grounded_generator": "oops"},
                structured_response=None,
            ),
            {},
        )

    with pytest.raises(ValueError, match="Citation entries must be mappings"):
        await node.run(
            State(
                inputs={},
                node_results={"grounded_generator": {"citations": ["bad"]}},
                structured_response=None,
            ),
            {},
        )


@pytest.mark.asyncio
async def test_citations_formatter_handles_source_and_metadata_variations() -> None:
    citations = [
        {"id": "1", "snippet": "first snippet", "sources": None, "metadata": "invalid"},
        {
            "id": "2",
            "snippet": "second snippet",
            "sources": "legacy",
            "metadata": {"permalink": " https://example.org "},
        },
    ]
    node = CitationsFormatterNode(name="formatter-variations")
    state = State(
        inputs={},
        node_results={
            "grounded_generator": {
                "reply": "  Base answer [1] [2]  ",
                "citations": citations,
            }
        },
        structured_response=None,
    )

    formatted = await node.run(state, {})

    assert formatted["citations"][0]["sources"] == []
    assert formatted["citations"][0]["metadata"] == {}
    assert formatted["citations"][1]["sources"] == ["legacy"]
    assert (
        formatted["formatted"][1]
        == "[2] second snippet (sources: legacy | [source](https://example.org))"
    )
    assert formatted["reply"].startswith("Base answer [1] [2]")
    assert "- [1] first snippet" in formatted["reply"]
    assert "- [2] second snippet (sources: legacy | [source]" in formatted["reply"]
    assert state["node_results"]["grounded_generator"]["reply"] == formatted["reply"]
    assert (
        state["node_results"]["grounded_generator"]["citations"]
        == formatted["citations"]
    )


@pytest.mark.asyncio
async def test_citations_formatter_resolves_url_from_source_metadata_field() -> None:
    """Test formatter resolves URLs from metadata['source'] field."""
    citations = [
        {
            "id": "1",
            "snippet": "indexed chunk",
            "sources": ["dense"],
            "metadata": {"source": "https://www.uva.nl/en/news"},
        },
    ]
    node = CitationsFormatterNode(name="formatter-source-url")
    state = State(
        inputs={},
        node_results={
            "grounded_generator": {
                "reply": "Answer text [1]",
                "citations": citations,
            }
        },
        structured_response=None,
    )

    formatted = await node.run(state, {})

    assert (
        formatted["formatted"][0]
        == "[1] indexed chunk (sources: dense | [source](https://www.uva.nl/en/news))"
    )
    assert "https://www.uva.nl/en/news" in formatted["reply"]


@pytest.mark.asyncio
async def test_citations_formatter_handles_list_payload_without_overwriting_state() -> (
    None
):
    citations = [{"id": "alpha", "snippet": "list snippet", "sources": []}]
    node = CitationsFormatterNode(name="formatter-list")
    state = State(
        inputs={},
        node_results={"grounded_generator": citations},
        structured_response=None,
    )

    formatted = await node.run(state, {})

    assert formatted["reply"] == "References:\n- [alpha] list snippet"
    assert formatted["citations"][0]["id"] == "alpha"
    assert state["node_results"]["grounded_generator"] is citations


def test_citations_formatter_overwrite_source_reply_handles_invalid_state() -> None:
    node = CitationsFormatterNode(name="formatter-overwrite")
    invalid_results = {"node_results": None}
    node._overwrite_source_reply(invalid_results, "reply", [])
    assert invalid_results["node_results"] is None

    list_payload_state = {"node_results": {"grounded_generator": []}}
    node._overwrite_source_reply(list_payload_state, "reply", [])
    assert list_payload_state["node_results"]["grounded_generator"] == []


@pytest.mark.asyncio
async def test_answer_caching_stores_and_serves_cached_response() -> None:
    node = AnswerCachingNode(name="cache", ttl_seconds=None, max_entries=2)

    first_state = State(
        inputs={"query": "What is Orcheo?"},
        node_results={"grounded_generator": {"reply": "An orchestration engine."}},
        structured_response=None,
    )
    first = await node.run(first_state, {})
    assert first == {"cached": False, "reply": "An orchestration engine."}

    second_state = State(
        inputs={"query": "What is Orcheo?"},
        node_results={},
        structured_response=None,
    )
    second = await node.run(second_state, {})

    assert second == {"cached": True, "reply": "An orchestration engine."}


@pytest.mark.asyncio
async def test_answer_caching_handles_expiry_and_validation() -> None:
    node = AnswerCachingNode(name="cache-extra", ttl_seconds=1, max_entries=1)
    cache_state = State(
        inputs={"query": "Q1"},
        node_results={"grounded_generator": {"reply": "first"}},
        structured_response=None,
    )
    await node.run(cache_state, {})
    node.cache["q1"] = ("first", time.time() - 1)

    expired = await node.run(
        State(inputs={"query": "Q1"}, node_results={}, structured_response=None), {}
    )
    assert expired["cached"] is False

    with pytest.raises(ValueError, match="non-empty query"):
        await node.run(
            State(inputs={"query": ""}, node_results={}, structured_response=None), {}
        )

    bad_response_state = State(
        inputs={"query": "Q2"},
        node_results={"grounded_generator": {"reply": ""}},
        structured_response=None,
    )
    with pytest.raises(ValueError, match="non-empty string"):
        await node.run(bad_response_state, {})

    node._store("q3", "latest")
    node._store("q4", "newer")
    assert "q3" not in node.cache

    uncached = await node.run(
        State(
            inputs={"query": "Q5"},
            node_results={"grounded_generator": "skip"},
            structured_response=None,
        ),
        {},
    )
    assert uncached == {"cached": False, "reply": None}


@pytest.mark.asyncio
async def test_session_management_prunes_history() -> None:
    store = InMemoryMemoryStore(max_total_turns=3)
    node = SessionManagementNode(name="session", memory_store=store, max_turns=2)
    state = State(
        inputs={
            "session_id": "sess-1",
            "turns": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "more"},
            ],
        },
        node_results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["turn_count"] == 2
    assert [turn.content for turn in result["history"]] == ["hello", "more"]


@pytest.mark.asyncio
async def test_session_management_requires_session_id() -> None:
    node = SessionManagementNode(name="session-invalid")
    with pytest.raises(ValueError, match="requires a non-empty session id"):
        await node.run(State(inputs={}, node_results={}, structured_response=None), {})


@pytest.mark.asyncio
async def test_session_management_trims_session_id() -> None:
    node = SessionManagementNode(name="session-trim")
    state = State(
        inputs={"session_id": "  spaced  "},
        node_results={},
        structured_response=None,
    )
    result = await node.run(state, {})

    assert result["turn_count"] == 0


@pytest.mark.asyncio
async def test_multi_hop_planner_limits_hops() -> None:
    node = MultiHopPlannerNode(name="planner", max_hops=2)
    state = State(
        inputs={"query": "find revenue and summarize profit and share outlook"},
        node_results={},
        structured_response=None,
    )

    plan = await node.run(state, {})

    assert plan["hop_count"] == 2
    assert plan["plan"][0]["depends_on"] is None
    assert plan["plan"][1]["depends_on"] == plan["plan"][0]["id"]


@pytest.mark.asyncio
async def test_multi_hop_planner_handles_edge_cases() -> None:
    node = MultiHopPlannerNode(name="planner-edge")
    with pytest.raises(ValueError, match="requires a non-empty query"):
        await node.run(
            State(inputs={"query": ""}, node_results={}, structured_response=None), {}
        )

    fallback_plan = await node.run(
        State(inputs={"query": " and "}, node_results={}, structured_response=None), {}
    )
    assert fallback_plan["hop_count"] == 1


@pytest.mark.asyncio
@patch("orcheo.nodes.rag.generation.create_agent")
async def test_streaming_generator_validates_prompt_and_llm_output(
    mock_create_agent,
) -> None:
    node = StreamingGeneratorNode(name="stream-invalid")
    with pytest.raises(ValueError, match="requires a non-empty prompt"):
        await node.run(
            State(inputs={"prompt": "   "}, node_results={}, structured_response=None),
            {},
        )

    # Mock agent to return empty string
    async def bad_invoke(state):
        return {
            "messages": [
                MagicMock(content=""),
            ]
        }

    mock_agent = MagicMock()
    mock_agent.ainvoke = bad_invoke
    mock_create_agent.return_value = mock_agent

    bad_node = StreamingGeneratorNode(name="stream-ai-model", ai_model="gpt-4")
    with pytest.raises(ValueError, match="Agent must return"):
        await bad_node._invoke_ai_model("prompt")


@pytest.mark.asyncio
async def test_search_result_adapter_maps_raw_payloads() -> None:
    node = SearchResultAdapterNode(
        name="adapter",
        source_result_key="hybrid_search",
        results_field="results",
        text_field="raw.recommendation_reason",
        metadata_field="raw",
        source_name="mongodb",
    )
    state = State(
        inputs={},
        node_results={
            "hybrid_search": {
                "results": [
                    {
                        "id": "doc-1",
                        "score": 0.42,
                        "raw": {
                            "platform_name": "Atlas",
                            "recommendation_reason": "Strong overlap with query",
                        },
                    }
                ]
            }
        },
        structured_response=None,
    )

    result = await node.run(state, {})

    entries = result["results"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry.text == "Strong overlap with query"
    assert entry.metadata["platform_name"] == "Atlas"
    assert entry.source == "mongodb"
    assert entry.sources == ["mongodb"]


@pytest.mark.asyncio
async def test_search_result_formatter_renders_markdown() -> None:
    node = SearchResultFormatterNode(
        name="formatter",
        source_result_key="adapt_results",
        results_field="results",
        title_fields=["platform_name"],
        snippet_fields=["recommendation_reason"],
        url_fields=["source_url"],
        snippet_label="Reason",
    )
    entry = SearchResult(
        id="doc-1",
        score=0.9,
        text="Fallback text",
        metadata={
            "platform_name": "Atlas",
            "recommendation_reason": "Strong overlap with query",
            "source_url": "https://example.com",
        },
        source="mongodb",
        sources=["mongodb"],
    )
    state = State(
        inputs={},
        node_results={"adapt_results": {"results": [entry]}},
        structured_response=None,
    )

    result = await node.run(state, {})

    markdown = result["markdown"]
    assert "Search results:" in markdown
    assert "1. Atlas" in markdown
    assert "Reason: Strong overlap with query" in markdown
    assert "Source: https://example.com" in markdown
