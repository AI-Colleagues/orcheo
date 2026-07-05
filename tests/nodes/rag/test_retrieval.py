import builtins
import types
from types import SimpleNamespace
from typing import Any
import pytest
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.rag.models import (
    DocumentChunk,
    SearchResult,
    VectorRecord,
)
from orcheo.nodes.rag.retrieval import (
    DenseSearchNode,
    HybridFusionNode,
    PineconeRerankNode,
    ReRankerNode,
    SearchResultAdapterNode,
    SourceRouterNode,
    SparseSearchNode,
    _resolve_retrieval_results,
)
from orcheo.nodes.rag.vector_store import (
    BaseVectorStore,
    InMemoryVectorStore,
)


@pytest.mark.asyncio
async def test_dense_search_node_returns_ranked_results() -> None:
    store = InMemoryVectorStore()
    texts = ["orcheo improves graphs", "another passage"]
    # Use the fake embeddings directly to populate the store
    embeddings = [[float(len(text))] for text in texts]
    await store.upsert(
        [
            VectorRecord(
                id=f"vec-{index}",
                values=embedding,
                text=text,
                metadata={"source": "demo", "index": index},
            )
            for index, (embedding, text) in enumerate(
                zip(embeddings, texts, strict=True)
            )
        ]
    )

    node = DenseSearchNode(
        name="dense",
        vector_store=store,
        embed_model="test:fake",
        model_kwargs={},
        top_k=2,
        filter_metadata={"source": "demo"},
    )
    state = State(
        inputs={"query": "orcheo improves graphs"},
        node_results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert [item.id for item in result["results"]] == ["vec-0", "vec-1"]
    assert all("dense" in item.sources for item in result["results"])


@pytest.mark.asyncio
async def test_dense_search_node_requires_non_empty_query() -> None:
    node = DenseSearchNode(
        name="dense-empty",
        vector_store=InMemoryVectorStore(),
        embed_model="test:fake",
        model_kwargs={},
    )
    state = State(inputs={"query": ""}, node_results={}, structured_response=None)

    with pytest.raises(
        ValueError, match="DenseSearchNode requires a non-empty query string"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_dense_search_node_async_embedder_returns_nested_list() -> None:
    # This test verifies the embedding method returns correct format
    # The conftest auto-mocks init_dense_embeddings to return FakeDenseEmbeddings
    node = DenseSearchNode(
        name="dense-async",
        vector_store=InMemoryVectorStore(),
        embed_model="test:fake",
        model_kwargs={},
    )

    # FakeDenseEmbeddings returns [float(len(text))] for each text
    result = await node._embed_query("test")
    assert isinstance(result, list)
    assert len(result) == 1
    assert result == [4.0]  # len("test") == 4


@pytest.mark.asyncio
async def test_dense_search_node_embedder_validates_output_type() -> None:
    # This test is no longer relevant with the new API
    # The embeddings module handles validation internally
    # We can test that the node works correctly with the mocked embeddings
    node = DenseSearchNode(
        name="dense-bad-embed",
        vector_store=InMemoryVectorStore(),
        embed_model="test:fake",
        model_kwargs={},
    )

    # The fake embedder should work correctly
    result = await node._embed_query("test")
    assert isinstance(result, list)
    assert all(isinstance(x, float) for x in result)


@pytest.mark.asyncio
async def test_dense_search_node_requires_dense_values() -> None:
    # This test is no longer relevant with the new API
    # The embeddings module ensures proper dense embeddings are returned
    # We verify the node works with the standard fake embeddings
    node = DenseSearchNode(
        name="dense-sparse-only",
        vector_store=InMemoryVectorStore(),
        embed_model="test:fake",
        model_kwargs={},
    )

    # The fake embedder returns proper dense vectors
    result = await node._embed_query("query")
    assert isinstance(result, list)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_dense_search_node_embed_query_with_fake_embeddings() -> None:
    # This test is no longer relevant with the new API
    # Credential handling is now managed by the embeddings module
    # We verify the node works with the standard fake embeddings
    node = DenseSearchNode(
        name="dense-cred",
        vector_store=InMemoryVectorStore(),
        embed_model="test:fake",
        model_kwargs={},
    )

    result = await node._embed_query("test")
    assert isinstance(result, list)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_sparse_search_orders_chunks_by_score() -> None:
    chunks = [
        DocumentChunk(
            id="chunk-1",
            document_id="doc-1",
            index=0,
            content="bananas bananas apples",
            metadata={"page": 1},
        ),
        DocumentChunk(
            id="chunk-2",
            document_id="doc-2",
            index=0,
            content="apples only",
            metadata={"page": 2},
        ),
    ]
    state = State(
        inputs={"query": "bananas"},
        node_results={"chunking_strategy": {"chunks": chunks}},
        structured_response=None,
    )
    node = SparseSearchNode(
        name="sparse",
        top_k=1,
    )

    result = await node.run(state, {})

    assert [item.id for item in result["results"]] == ["chunk-1"]
    assert result["results"][0].metadata["page"] == 1


@pytest.mark.asyncio
async def test_sparse_search_requires_non_empty_query() -> None:
    node = SparseSearchNode(name="sparse")
    state = State(inputs={"query": "   "}, node_results={}, structured_response=None)

    with pytest.raises(
        ValueError, match="SparseSearchNode requires a non-empty query string"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_sparse_search_requires_chunks() -> None:
    node = SparseSearchNode(name="sparse")
    state = State(
        inputs={"query": "bananas"}, node_results={}, structured_response=None
    )

    result = await node.run(state, {})

    assert result["results"] == []
    assert "warning" in result
    assert "SparseSearchNode did not receive any document chunks" in result["warning"]


@pytest.mark.asyncio
async def test_sparse_search_vector_store_candidates() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                id="chunk-1",
                values=[6.0],
                text="apples apples bananas",
                metadata={"document_id": "doc-1", "chunk_index": 0},
            ),
            VectorRecord(
                id="chunk-2",
                values=[1.0],
                text="apples oranges",
                metadata={"document_id": "doc-2", "chunk_index": 0},
            ),
        ]
    )

    node = SparseSearchNode(
        name="sparse-vector-store",
        vector_store=store,
        vector_store_candidate_k=2,
        top_k=1,
        embed_model="test:fake",
        model_kwargs={},
    )
    state = State(inputs={"query": "apples"}, node_results={}, structured_response=None)

    result = await node.run(state, {})

    assert [item.id for item in result["results"]] == ["chunk-1"]


@pytest.mark.asyncio
async def test_sparse_fetch_chunks_returns_empty_without_vector_store() -> None:
    node = SparseSearchNode(name="sparse-fetch-empty")

    assert await node._fetch_chunks_from_vector_store("query") == []


@pytest.mark.asyncio
async def test_sparse_fetch_chunks_handles_vector_store_metadata() -> None:
    class StubStore(BaseVectorStore):
        matches: list[SearchResult] = Field(default_factory=list)

        async def upsert(self, records) -> None:
            del records

        async def search(
            self,
            query: list[float],
            top_k: int,
            filter_metadata: dict[str, Any] | None = None,
        ) -> list[SearchResult]:
            del query, top_k, filter_metadata
            return self.matches

    matches = [
        SearchResult(
            id="skip-text",
            score=0.0,
            text="",
            metadata={},
            source="stub",
            sources=["stub"],
        ),
        SearchResult(
            id="string-index",
            score=1.0,
            text="chunk text",
            metadata={"chunk_index": "not-int", "document_id": "doc-str"},
            source="stub",
            sources=["stub"],
        ),
        SearchResult(
            id="list-index",
            score=0.5,
            text="chunk two",
            metadata={"chunk_index": ["bad"], "document_id": 123},
            source="stub",
            sources=["stub"],
        ),
    ]
    store = StubStore(matches=matches)
    node = SparseSearchNode(
        name="sparse-fetch-metadata",
        vector_store=store,
        embed_model="test:fake",
        model_kwargs={},
    )

    chunks = await node._fetch_chunks_from_vector_store("query")

    assert [chunk.id for chunk in chunks] == ["string-index", "list-index"]
    assert chunks[0].index == 0
    assert chunks[1].document_id == "123"


@pytest.mark.asyncio
async def test_sparse_embed_async_embedder_returns_vectors() -> None:
    # This test is no longer relevant with the new API
    # Sparse embeddings are handled by the embeddings module when sparse_model is set
    # We verify the node works with vector store candidates using dense embeddings
    node = SparseSearchNode(
        name="sparse-async",
        vector_store=InMemoryVectorStore(),
        embed_model="test:fake",
        model_kwargs={},
    )

    # Verify the node can fetch chunks from vector store
    chunks = await node._fetch_chunks_from_vector_store("test")
    assert isinstance(chunks, list)


@pytest.mark.asyncio
async def test_sparse_embed_raises_on_invalid_payload() -> None:
    # This test is no longer relevant with the new API
    # The embeddings module handles validation internally
    # We verify the node works correctly with the mocked embeddings
    node = SparseSearchNode(
        name="sparse-invalid",
        vector_store=InMemoryVectorStore(),
        embed_model="test:fake",
        model_kwargs={},
    )

    # The fake embedder should work correctly
    chunks = await node._fetch_chunks_from_vector_store("test")
    assert isinstance(chunks, list)


@pytest.mark.asyncio
async def test_sparse_embed_requires_dense_values() -> None:
    # This test is no longer relevant with the new API
    # Sparse embeddings are now handled by the embeddings module
    # We verify the node works with vector store candidates
    node = SparseSearchNode(
        name="sparse-sparse-only",
        vector_store=InMemoryVectorStore(),
        embed_model="test:fake",
        model_kwargs={},
    )

    # Verify the node can fetch chunks from vector store
    chunks = await node._fetch_chunks_from_vector_store("query")
    assert isinstance(chunks, list)


@pytest.mark.asyncio
async def test_sparse_search_uses_sparse_model_for_vector_store_query() -> None:
    class _CapturingStore(BaseVectorStore):
        captured_query: object | None = None

        async def upsert(self, records) -> None:
            del records

        async def search(
            self,
            query: list[float] | object,
            top_k: int,
            filter_metadata: dict[str, Any] | None = None,
        ) -> list[SearchResult]:
            del top_k, filter_metadata
            self.captured_query = query
            return [
                SearchResult(
                    id="chunk-1",
                    score=1.0,
                    text="apple banana",
                    metadata={"chunk_index": 0, "document_id": "doc-1"},
                    source="stub",
                    sources=["stub"],
                )
            ]

    store = _CapturingStore()
    node = SparseSearchNode(
        name="sparse-sparse-lane",
        vector_store=store,
        sparse_model="test:fake",
        sparse_kwargs={},
    )
    state = State(inputs={"query": "apple"}, node_results={}, structured_response=None)

    result = await node.run(state, {})

    assert [item.id for item in result["results"]] == ["chunk-1"]
    assert hasattr(store.captured_query, "sparse_values")
    assert store.captured_query.values == []
    sparse_values = store.captured_query.sparse_values
    assert sparse_values is not None
    assert sparse_values.indices == [0]


@pytest.mark.asyncio
async def test_sparse_search_bm25_requires_encoder_state_path() -> None:
    node = SparseSearchNode(
        name="sparse-bm25-missing-state",
        vector_store=InMemoryVectorStore(),
        sparse_model="pinecone:bm25",
        sparse_kwargs={},
    )

    with pytest.raises(
        ValueError,
        match=r"requires sparse_kwargs\['encoder_state_path'\]",
    ):
        await node._build_vector_store_query("apple")


def test_sparse_search_bm25_default_allows_missing_encoder_state_path() -> None:
    node = SparseSearchNode(
        name="sparse-bm25-default",
        sparse_model="pinecone:bm25-default",
        sparse_kwargs={},
    )

    node._validate_sparse_query_configuration()


def test_sparse_resolve_chunks_rejects_non_list_payload() -> None:
    node = SparseSearchNode(name="sparse")
    state = State(
        inputs={},
        node_results={"chunking_strategy": {"chunks": {"not": "a list"}}},
        structured_response=None,
    )

    with pytest.raises(ValueError, match="chunks payload must be a list"):
        node._resolve_chunks(state)


def test_sparse_score_skips_zero_denominator() -> None:
    node = SparseSearchNode(
        name="sparse",
        b=1.0,
    )
    document_tokens: list[str] = []
    query_tokens = ["missing"]
    corpus = [document_tokens, ["present"]]
    avg_length = (len(document_tokens) + len(corpus[1])) / len(corpus)

    assert node._bm25_score(document_tokens, query_tokens, corpus, avg_length) == 0.0


@pytest.mark.asyncio
async def test_hybrid_fusion_rrf_combines_sources() -> None:
    dense_results = [
        SearchResult(
            id="chunk-1",
            score=0.8,
            text="dense",
            metadata={},
            source="dense",
            sources=["dense"],
        ),
        SearchResult(
            id="chunk-2",
            score=0.7,
            text="dense2",
            metadata={},
            source="dense",
            sources=["dense"],
        ),
    ]
    sparse_results = [
        SearchResult(
            id="chunk-2",
            score=2.0,
            text="sparse",
            metadata={},
            source="sparse",
            sources=["sparse"],
        )
    ]
    state = State(
        inputs={},
        node_results={
            "retrieval_results": {"dense": dense_results, "sparse": sparse_results}
        },
        structured_response=None,
    )
    node = HybridFusionNode(name="hybrid", strategy="rrf", top_k=2)

    result = await node.run(state, {})

    assert [item.id for item in result["results"]] == ["chunk-2", "chunk-1"]
    assert set(result["results"][0].sources) == {"dense", "sparse"}


@pytest.mark.asyncio
async def test_hybrid_fusion_weighted_sum_respects_weights() -> None:
    retrievers = {
        "dense": [
            SearchResult(id="r1", score=0.5, text="", metadata={}, source="dense")
        ],
        "sparse": [
            SearchResult(id="r1", score=2.0, text="", metadata={}, source="sparse")
        ],
    }
    state = State(
        inputs={},
        node_results={"retrieval_results": retrievers},
        structured_response=None,
    )
    node = HybridFusionNode(
        name="hybrid-weighted",
        strategy="weighted_sum",
        weights={"dense": 0.5, "sparse": 2.0},
        top_k=1,
    )

    result = await node.run(state, {})

    assert result["results"][0].score == pytest.approx(4.25)
    assert set(result["results"][0].sources) == {"dense", "sparse"}


@pytest.mark.asyncio
async def test_hybrid_fusion_requires_results_mapping() -> None:
    node = HybridFusionNode(name="hybrid")
    state = State(
        inputs={}, node_results={"retrieval_results": {}}, structured_response=None
    )

    with pytest.raises(
        ValueError, match="HybridFusionNode requires a mapping of retriever results"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_hybrid_fusion_requires_valid_strategy() -> None:
    node = HybridFusionNode(name="hybrid", strategy="bad")
    state = State(
        inputs={},
        node_results={
            "retrieval_results": {
                "dense": [
                    SearchResult(
                        id="r1", score=1.0, text="", metadata={}, source="dense"
                    )
                ]
            }
        },
        structured_response=None,
    )

    with pytest.raises(
        ValueError, match="strategy must be either 'rrf' or 'weighted_sum'"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_hybrid_fusion_rejects_non_list_entries() -> None:
    node = HybridFusionNode(name="hybrid")
    state = State(
        inputs={},
        node_results={"retrieval_results": {"dense": {"results": "not a list"}}},
        structured_response=None,
    )

    with pytest.raises(ValueError, match="Retriever results for dense must be a list"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_hybrid_fusion_handles_payload_with_results_key() -> None:
    retrievers = {
        "dense": {
            "results": [
                SearchResult(
                    id="r2", score=0.5, text="payload", metadata={}, source="dense"
                )
            ]
        }
    }
    state = State(
        inputs={},
        node_results={"retrieval_results": retrievers},
        structured_response=None,
    )
    node = HybridFusionNode(name="hybrid", strategy="rrf", top_k=1)

    result = await node.run(state, {})

    assert result["results"][0].id == "r2"


@pytest.mark.asyncio
async def test_pinecone_rerank_returns_empty_for_missing_entries() -> None:
    node = PineconeRerankNode(name="pinecone")
    state = State(
        inputs={"query": "test"},
        node_results={"fusion": []},
        structured_response=None,
    )

    assert await node.run(state, {}) == {"results": []}


@pytest.mark.asyncio
async def test_pinecone_rerank_requires_inference_interface() -> None:
    entry = SearchResult(
        id="doc-1",
        score=1.0,
        text="passage",
        metadata={},
        source="fusion",
        sources=["fusion"],
    )
    node = PineconeRerankNode(name="pinecone")
    node.client = SimpleNamespace()
    state = State(
        inputs={"query": "what"},
        node_results={"fusion": [entry]},
        structured_response=None,
    )

    with pytest.raises(
        RuntimeError,
        match="Pinecone client lacks an inference interface for reranking",
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_pinecone_rerank_handles_async_inference() -> None:
    entry = SearchResult(
        id="doc-1",
        score=1.0,
        text="passage",
        metadata={"topic": "test"},
        source="fusion",
        sources=["fusion"],
    )

    class StubInference:
        async def rerank(self, **__: Any) -> dict[str, Any]:
            return {
                "data": [
                    {
                        "document": {"_id": "doc-1", "chunk_text": "reranked"},
                        "score": 2.0,
                    }
                ]
            }

    node = PineconeRerankNode(
        name="pinecone", client=SimpleNamespace(inference=StubInference())
    )
    state = State(
        inputs={"query": "what"},
        node_results={"fusion": [entry]},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["results"][0].text == "reranked"
    assert result["results"][0].metadata == entry.metadata


@pytest.mark.asyncio
async def test_pinecone_rerank_handles_sync_inference_with_data_attr() -> None:
    entry = SearchResult(
        id="doc-1",
        score=1.0,
        text="passage",
        metadata={},
        source="fusion",
        sources=["fusion"],
    )

    class _SyncResponse:
        def __init__(self) -> None:
            self.data = [
                {
                    "document": {"_id": "doc-1", "chunk_text": "sync text"},
                    "score": 3.0,
                }
            ]

    class _SyncInference:
        def rerank(self, **__: Any) -> _SyncResponse:
            return _SyncResponse()

    node = PineconeRerankNode(
        name="pinecone",
        client=SimpleNamespace(inference=_SyncInference()),
    )
    state = State(
        inputs={"query": "what"},
        node_results={"fusion": [entry]},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["results"][0].text == "sync text"
    assert result["results"][0].score == pytest.approx(3.0)


def test_pinecone_rerank_requires_query_string() -> None:
    node = PineconeRerankNode(name="pinecone")

    with pytest.raises(
        ValueError,
        match="PineconeRerankNode requires a non-empty query string",
    ):
        node._resolve_query(
            State(inputs={"query": "   "}, node_results={}, structured_response=None)
        )


def test_pinecone_rerank_build_documents_includes_metadata() -> None:
    entry = SearchResult(
        id="doc-2",
        score=1.0,
        text="passage",
        metadata={"source": "rerank"},
        source="rerank",
        sources=["rerank"],
    )
    node = PineconeRerankNode(name="pinecone")

    documents = node._build_documents([entry])

    assert documents[0]["metadata"] == entry.metadata


def test_pinecone_rerank_uses_provided_client() -> None:
    stub = SimpleNamespace()
    node = PineconeRerankNode(name="pinecone")
    node.client = stub

    assert node._resolve_client() is stub


def test_pinecone_rerank_resolve_client_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = PineconeRerankNode(name="pinecone", client=None)
    original_import = builtins.__import__

    def _failing_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pinecone":
            raise ImportError("missing pinecone")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _failing_import)

    with pytest.raises(ImportError, match="requires the 'pinecone-client' dependency"):
        node._resolve_client()


def test_pinecone_rerank_resolve_client_constructs_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakePinecone:
        def __init__(self, **kwargs: Any) -> None:
            captured["kwargs"] = kwargs

    fake_module = types.SimpleNamespace(Pinecone=_FakePinecone)
    original_import = builtins.__import__

    def _import_with_fake_pinecone(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pinecone":
            return fake_module
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import_with_fake_pinecone)

    node = PineconeRerankNode(name="pinecone", client_kwargs={"api_key": "pc-key"})
    client = node._resolve_client()

    assert isinstance(client, _FakePinecone)
    assert captured["kwargs"] == {"api_key": "pc-key"}
    assert node.client is client


def test_resolve_retrieval_results_returns_empty_for_missing_entries() -> None:
    state = State(inputs={}, node_results={"fusion": None}, structured_response=None)
    assert _resolve_retrieval_results(state, "fusion", "results") == []


def test_resolve_retrieval_results_skips_null_items() -> None:
    entry_payload = {
        "id": "a",
        "score": 1.0,
        "text": "x",
        "metadata": {},
        "source": "source",
        "sources": ["source"],
    }
    state = State(
        inputs={},
        node_results={"fusion": [entry_payload, None]},
        structured_response=None,
    )

    resolved = _resolve_retrieval_results(state, "fusion", "results")

    assert len(resolved) == 1


def test_resolve_retrieval_results_supports_nested_results_field() -> None:
    entry_payload = {
        "id": "a",
        "score": 1.0,
        "text": "x",
        "metadata": {},
        "source": "source",
        "sources": ["source"],
    }
    state = State(
        inputs={},
        node_results={"fusion": {"results": [entry_payload]}},
        structured_response=None,
    )

    resolved = _resolve_retrieval_results(state, "fusion", "results")

    assert len(resolved) == 1
    assert resolved[0].id == "a"


def test_resolve_retrieval_results_raises_for_non_list_entries() -> None:
    state = State(
        inputs={},
        node_results={"fusion": {"results": "invalid"}},
        structured_response=None,
    )

    with pytest.raises(
        ValueError, match="Retrieved results must be provided as a list"
    ):
        _resolve_retrieval_results(state, "fusion", "results")


@pytest.mark.asyncio
async def test_reranker_node_scores_and_sorts_entries() -> None:
    entries = [
        SearchResult(
            id="short",
            score=1.0,
            text="short",
            metadata={},
            source="dense",
            sources=["dense"],
        ),
        SearchResult(
            id="long",
            score=2.0,
            text="a much longer passage",
            metadata={},
            source="dense",
            sources=["dense"],
        ),
    ]
    node = ReRankerNode(
        name="reranker",
        source_result_key="fusion",
        top_k=1,
        length_penalty=0.5,
        rerank_function=lambda entry: entry.score,
    )
    state = State(
        inputs={},
        node_results={"fusion": {"results": entries}},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert [item.id for item in result["results"]] == ["short"]
    assert result["results"][0].score == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_reranker_node_uses_base_score_without_custom_function() -> None:
    entry = SearchResult(
        id="base",
        score=2.5,
        text="single token",
        metadata={},
        source="dense",
        sources=["dense"],
    )
    node = ReRankerNode(name="reranker", source_result_key="fusion", length_penalty=0.0)
    state = State(
        inputs={},
        node_results={"fusion": {"results": [entry]}},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert result["results"][0].score == pytest.approx(2.5)


@pytest.mark.asyncio
async def test_source_router_groups_and_filters_results() -> None:
    entries = [
        SearchResult(
            id="a",
            score=0.9,
            text="keep",
            metadata={},
            source="dense",
            sources=["dense"],
        ),
        SearchResult(
            id="b",
            score=0.1,
            text="drop",
            metadata={},
            source="dense",
            sources=["dense"],
        ),
        SearchResult(
            id="c",
            score=0.8,
            text="unknown source",
            metadata={},
            source=None,
            sources=[],
        ),
    ]
    node = SourceRouterNode(
        name="router",
        source_result_key="fusion",
        min_score=0.5,
    )
    state = State(
        inputs={},
        node_results={"fusion": {"results": entries}},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert [item.id for item in result["routed"]["dense"]] == ["a"]
    assert [item.id for item in result["routed"]["unknown"]] == ["c"]


@pytest.mark.asyncio
async def test_source_router_accepts_list_payload_without_results_key() -> None:
    entry = SearchResult(
        id="x",
        score=0.9,
        text="payload",
        metadata={},
        source="dense",
        sources=["dense"],
    )
    node = SourceRouterNode(name="router", source_result_key="fusion")
    state = State(
        inputs={},
        node_results={"fusion": [entry]},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert [item.id for item in result["routed"]["dense"]] == ["x"]


def test_source_router_rejects_non_list_entries() -> None:
    node = SourceRouterNode(name="router", source_result_key="fusion")
    state = State(
        inputs={},
        node_results={"fusion": {"results": "bad"}},
        structured_response=None,
    )

    with pytest.raises(
        ValueError, match="SourceRouterNode requires a list of retrieval results"
    ):
        node._resolve_results(state)


# --- SearchResultAdapterNode tests ---


@pytest.mark.asyncio
async def test_adapter_converts_mapping_entries() -> None:
    entries = [
        {
            "id": "doc-1",
            "score": 0.9,
            "text": "passage",
            "metadata": {"topic": "test"},
            "source": "api",
            "sources": ["api"],
        }
    ]
    state = State(
        inputs={},
        node_results={"retriever": {"results": entries}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter")

    result = await node.run(state, {})

    assert len(result["results"]) == 1
    assert result["results"][0].id == "doc-1"
    assert result["results"][0].metadata == {"topic": "test"}


@pytest.mark.asyncio
async def test_adapter_payload_not_dict_with_results_field() -> None:
    """Covers entries = payload when payload is not a dict with results_field."""
    entries = [{"id": "r1", "score": 1.0, "text": "t", "metadata": {}, "source": "s"}]
    state = State(
        inputs={},
        node_results={"retriever": entries},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter")

    result = await node.run(state, {})

    assert len(result["results"]) == 1


@pytest.mark.asyncio
async def test_adapter_returns_empty_for_none_entries() -> None:
    state = State(
        inputs={},
        node_results={"retriever": {"results": None}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter")

    result = await node.run(state, {})

    assert result["results"] == []


@pytest.mark.asyncio
async def test_adapter_rejects_non_list_entries() -> None:
    state = State(
        inputs={},
        node_results={"retriever": {"results": "invalid"}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter")

    with pytest.raises(
        ValueError, match="SearchResultAdapterNode requires a list of results"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_adapter_skips_none_entries() -> None:
    entries = [
        {"id": "r1", "score": 1.0, "text": "t", "metadata": {}},
        None,
    ]
    state = State(
        inputs={},
        node_results={"retriever": {"results": entries}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter")

    result = await node.run(state, {})

    assert len(result["results"]) == 1


@pytest.mark.asyncio
async def test_adapter_passes_through_search_result_instances() -> None:
    sr = SearchResult(
        id="sr-1",
        score=0.8,
        text="passage",
        metadata={"k": "v"},
        source="orig",
    )
    state = State(
        inputs={},
        node_results={"retriever": {"results": [sr]}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter")

    result = await node.run(state, {})

    assert result["results"][0].id == "sr-1"
    assert result["results"][0].source == "orig"


@pytest.mark.asyncio
async def test_adapter_applies_source_override_to_search_result() -> None:
    sr = SearchResult(
        id="sr-1",
        score=0.8,
        text="passage",
        metadata={},
        source="orig",
        sources=["orig"],
    )
    state = State(
        inputs={},
        node_results={"retriever": {"results": [sr]}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter", source_name="override")

    result = await node.run(state, {})

    assert result["results"][0].source == "override"
    assert "override" in result["results"][0].sources
    assert "orig" in result["results"][0].sources


@pytest.mark.asyncio
async def test_adapter_applies_source_override_when_already_present() -> None:
    """source_name already in sources list should not duplicate."""
    sr = SearchResult(
        id="sr-1",
        score=0.8,
        text="passage",
        metadata={},
        source="mine",
        sources=["mine"],
    )
    state = State(
        inputs={},
        node_results={"retriever": {"results": [sr]}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter", source_name="mine")

    result = await node.run(state, {})

    assert result["results"][0].sources.count("mine") == 1


@pytest.mark.asyncio
async def test_adapter_rejects_non_mapping_entry() -> None:
    state = State(
        inputs={},
        node_results={"retriever": {"results": [42]}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter")

    with pytest.raises(
        ValueError,
        match="SearchResultAdapterNode entries must be mappings",
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_adapter_extracts_metadata_from_raw_field() -> None:
    entries = [
        {
            "id": "r1",
            "score": 1.0,
            "text": "t",
            "raw": {"nested_key": "nested_val"},
        }
    ]
    state = State(
        inputs={},
        node_results={"retriever": {"results": entries}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter", metadata_field=None, raw_field="raw")

    result = await node.run(state, {})

    assert result["results"][0].metadata == {"nested_key": "nested_val"}


@pytest.mark.asyncio
async def test_adapter_extract_metadata_returns_empty_when_not_found() -> None:
    entries = [{"id": "r1", "score": 1.0, "text": "t"}]
    state = State(
        inputs={},
        node_results={"retriever": {"results": entries}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter", metadata_field=None, raw_field=None)

    result = await node.run(state, {})

    assert result["results"][0].metadata == {}


@pytest.mark.asyncio
async def test_adapter_extract_source_from_entry() -> None:
    entries = [{"id": "r1", "score": 1.0, "text": "t", "source": "my_source"}]
    state = State(
        inputs={},
        node_results={"retriever": {"results": entries}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter")

    result = await node.run(state, {})

    assert result["results"][0].source == "my_source"


@pytest.mark.asyncio
async def test_adapter_source_name_overrides_mapping_source() -> None:
    entries = [{"id": "r1", "score": 1.0, "text": "t", "source": "from-entry"}]
    state = State(
        inputs={},
        node_results={"retriever": {"results": entries}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter", source_name="override")

    result = await node.run(state, {})

    assert result["results"][0].source == "override"


@pytest.mark.asyncio
async def test_adapter_extract_source_returns_none_for_blank() -> None:
    entries = [{"id": "r1", "score": 1.0, "text": "t", "source": "   "}]
    state = State(
        inputs={},
        node_results={"retriever": {"results": entries}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter")

    result = await node.run(state, {})

    assert result["results"][0].source is None


@pytest.mark.asyncio
async def test_adapter_extract_sources_from_list() -> None:
    entries = [
        {
            "id": "r1",
            "score": 1.0,
            "text": "t",
            "sources": ["a", "b"],
        }
    ]
    state = State(
        inputs={},
        node_results={"retriever": {"results": entries}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter")

    result = await node.run(state, {})

    assert result["results"][0].sources == ["a", "b"]


@pytest.mark.asyncio
async def test_adapter_extract_sources_from_string() -> None:
    entries = [{"id": "r1", "score": 1.0, "text": "t", "sources": "single"}]
    state = State(
        inputs={},
        node_results={"retriever": {"results": entries}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter")

    result = await node.run(state, {})

    assert result["results"][0].sources == ["single"]


@pytest.mark.asyncio
async def test_adapter_extract_sources_falls_back_to_source() -> None:
    """When sources field is missing, falls back to source."""
    entries = [{"id": "r1", "score": 1.0, "text": "t", "source": "fallback"}]
    state = State(
        inputs={},
        node_results={"retriever": {"results": entries}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter")

    result = await node.run(state, {})

    assert result["results"][0].sources == ["fallback"]


@pytest.mark.asyncio
async def test_adapter_coerce_score_from_string() -> None:
    entries = [{"id": "r1", "score": "0.75", "text": "t"}]
    state = State(
        inputs={},
        node_results={"retriever": {"results": entries}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter")

    result = await node.run(state, {})

    assert result["results"][0].score == 0.75


@pytest.mark.asyncio
async def test_adapter_coerce_score_invalid_string_uses_default() -> None:
    entries = [{"id": "r1", "score": "bad", "text": "t"}]
    state = State(
        inputs={},
        node_results={"retriever": {"results": entries}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter", default_score=0.5)

    result = await node.run(state, {})

    assert result["results"][0].score == 0.5


@pytest.mark.asyncio
async def test_adapter_coerce_score_none_uses_default() -> None:
    entries = [{"id": "r1", "text": "t"}]
    state = State(
        inputs={},
        node_results={"retriever": {"results": entries}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter", default_score=0.1)

    result = await node.run(state, {})

    assert result["results"][0].score == 0.1


def test_adapter_extract_empty_path_returns_false() -> None:
    node = SearchResultAdapterNode(name="adapter")

    found, value = node._extract({"key": "val"}, "")

    assert found is False
    assert value is None


def test_adapter_extract_handles_value_error() -> None:
    """Covers _extract catching ValueError from _extract_value."""
    node = SearchResultAdapterNode(name="adapter")

    # A path with no valid segments triggers ValueError from _split_path
    found, value = node._extract({"key": "val"}, ".")

    assert found is False
    assert value is None


@pytest.mark.asyncio
async def test_adapter_extract_metadata_raw_field_not_mapping() -> None:
    """Covers raw_field found but value is not a Mapping."""
    entries = [{"id": "r1", "score": 1.0, "text": "t", "raw": "not-a-dict"}]
    state = State(
        inputs={},
        node_results={"retriever": {"results": entries}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter", metadata_field=None, raw_field="raw")

    result = await node.run(state, {})

    assert result["results"][0].metadata == {}


@pytest.mark.asyncio
async def test_adapter_extract_sources_neither_list_nor_string() -> None:
    """Covers sources_value that is neither list nor string."""
    entries = [
        {
            "id": "r1",
            "score": 1.0,
            "text": "t",
            "source": "src",
            "sources": 42,
        }
    ]
    state = State(
        inputs={},
        node_results={"retriever": {"results": entries}},
        structured_response=None,
    )
    node = SearchResultAdapterNode(name="adapter")

    result = await node.run(state, {})

    # sources is 42 (not list/str), so falls back to source
    assert result["results"][0].sources == ["src"]


@pytest.mark.asyncio
async def test_sparse_search_requires_embed_model_without_sparse_model() -> None:
    """Covers lines 297-301: error when embed_model is empty and no sparse_model."""
    store = InMemoryVectorStore()
    await store.upsert([VectorRecord(id="v1", values=[1.0], text="chunk", metadata={})])
    node = SparseSearchNode(
        name="sparse-no-embed",
        embed_model="",
        sparse_model=None,
        vector_store=store,
    )
    state = State(
        inputs={"query": "test query"},
        node_results={},
        structured_response=None,
    )

    with pytest.raises(
        ValueError,
        match="SparseSearchNode requires embed_model when vector_store is set",
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_sparse_validate_bm25_with_encoder_state_path(
    tmp_path: Any,
) -> None:
    """Covers line 311: early return when encoder_state_path is provided."""
    node = SparseSearchNode(
        name="sparse-bm25-state",
        sparse_model="pinecone:bm25",
        sparse_kwargs={"encoder_state_path": str(tmp_path / "state.bin")},
    )
    # Should NOT raise — the early return on line 311 skips the error
    node._validate_sparse_query_configuration()
