"""Unit tests for conversational search conversational search vector stores."""

import sys
import types
import pytest
from orcheo.nodes.rag.ingestion import EmbeddingVector
from orcheo.nodes.rag.models import (
    SearchResult,
    SparseValues,
    VectorRecord,
)
from orcheo.nodes.rag.vector_store import (
    InMemoryVectorStore,
    PineconeVectorStore,
)


class _DummyIndex:
    def __init__(self) -> None:
        self.calls: list[tuple[list[dict], str | None]] = []
        self.queries: list[dict[str, object]] = []

    async def upsert(self, vectors: list[dict], namespace: str | None) -> None:
        self.calls.append((vectors, namespace))

    def query(
        self,
        *,
        vector: list[float],
        top_k: int,
        namespace: str | None,
        include_metadata: bool,
        filter: dict | None,
        sparse_vector: dict[str, object] | None,
    ) -> dict[str, object]:
        payload = {
            "vector": vector,
            "top_k": top_k,
            "namespace": namespace,
            "include_metadata": include_metadata,
            "filter": filter,
            "sparse_vector": sparse_vector,
        }
        self.queries.append(payload)
        return {
            "matches": [
                {
                    "id": "match-1",
                    "score": 0.5,
                    "metadata": {"text": "doc text", "topic": "demo"},
                }
            ]
        }


class _DummyClient:
    def __init__(self, index: _DummyIndex) -> None:
        self._index = index

    def Index(self, name: str) -> _DummyIndex:  # noqa: N802
        return self._index


class _FailingClient:
    def Index(self, name: str) -> None:  # noqa: N802
        raise ValueError("boom")


class _SyncIndex:
    def __init__(self) -> None:
        self.calls: list[tuple[list[dict], str | None]] = []

    def upsert(self, vectors: list[dict], namespace: str | None) -> None:
        self.calls.append((vectors, namespace))


class _AsyncQueryResult:
    def __init__(self, matches: list[dict[str, object]]) -> None:
        self.matches = matches


class _AsyncQueryIndex:
    def __init__(self) -> None:
        self.queries: list[dict[str, object]] = []

    async def query(
        self,
        *,
        vector: list[float],
        top_k: int,
        namespace: str | None,
        include_metadata: bool,
        filter: dict | None,
        sparse_vector: dict[str, object] | None,
    ) -> _AsyncQueryResult:
        payload = {
            "vector": vector,
            "top_k": top_k,
            "namespace": namespace,
            "include_metadata": include_metadata,
            "filter": filter,
            "sparse_vector": sparse_vector,
        }
        self.queries.append(payload)
        return _AsyncQueryResult(
            [
                {
                    "id": "async-match",
                }
            ]
        )


class _AsyncClient:
    def __init__(self, index: _AsyncQueryIndex) -> None:
        self._index = index

    def Index(self, name: str) -> _AsyncQueryIndex:  # noqa: N802
        return self._index


class _EmptyMatchesIndex:
    def __init__(self) -> None:
        self.queries: list[dict[str, object]] = []

    def query(
        self,
        *,
        vector: list[float],
        top_k: int,
        namespace: str | None,
        include_metadata: bool,
        filter: dict | None,
        sparse_vector: dict[str, object] | None,
    ) -> dict[str, object]:
        payload = {
            "vector": vector,
            "top_k": top_k,
            "namespace": namespace,
            "include_metadata": include_metadata,
            "filter": filter,
            "sparse_vector": sparse_vector,
        }
        self.queries.append(payload)
        return {}


class _EmptyMatchesClient:
    def __init__(self, index: _EmptyMatchesIndex) -> None:
        self._index = index

    def Index(self, name: str) -> _EmptyMatchesIndex:  # noqa: N802
        return self._index


class _CustomQueryIndex:
    def __init__(self, matches: list[dict[str, object]]) -> None:
        self.matches = matches
        self.queries: list[dict[str, object]] = []

    def query(
        self,
        *,
        vector: list[float],
        top_k: int,
        namespace: str | None,
        include_metadata: bool,
        filter: dict | None,
        sparse_vector: dict[str, object] | None,
    ) -> dict[str, object]:
        payload = {
            "vector": vector,
            "top_k": top_k,
            "namespace": namespace,
            "include_metadata": include_metadata,
            "filter": filter,
            "sparse_vector": sparse_vector,
        }
        self.queries.append(payload)
        return {"matches": self.matches}


class _CustomClient:
    def __init__(self, index: _CustomQueryIndex) -> None:
        self._index = index

    def Index(self, name: str) -> _CustomQueryIndex:  # noqa: N802
        return self._index


class _AttributeMatch:
    def __init__(
        self, match_id: str, score: float, metadata: dict[str, object]
    ) -> None:
        self.id = match_id
        self.score = score
        self.metadata = metadata


class _AttributeIndex:
    def __init__(self, matches: list[_AttributeMatch]) -> None:
        self.matches = matches
        self.queries: list[dict[str, object]] = []

    def query(
        self,
        *,
        vector: list[float],
        top_k: int,
        namespace: str | None,
        include_metadata: bool,
        filter: dict | None,
        sparse_vector: dict[str, object] | None,
    ) -> dict[str, object]:
        payload = {
            "vector": vector,
            "top_k": top_k,
            "namespace": namespace,
            "include_metadata": include_metadata,
            "filter": filter,
            "sparse_vector": sparse_vector,
        }
        self.queries.append(payload)
        return {"matches": self.matches}


class _AttributeClient:
    def __init__(self, index: _AttributeIndex) -> None:
        self._index = index

    def Index(self, name: str) -> _AttributeIndex:  # noqa: N802
        return self._index


@pytest.mark.asyncio
async def test_pinecone_vector_store_upserts_with_provided_client() -> None:
    index = _DummyIndex()
    client = _DummyClient(index=index)
    store = PineconeVectorStore(
        index_name="pinecone-test", namespace="ns", client=client
    )
    record = VectorRecord(
        id="rec-1", values=[0.1], text="doc text", metadata={"foo": "bar"}
    )

    await store.upsert([record])

    payload, namespace = index.calls[0]
    assert namespace == "ns"
    assert payload[0]["metadata"]["foo"] == "bar"
    assert payload[0]["metadata"]["text"] == "doc text"


@pytest.mark.asyncio
async def test_pinecone_vector_store_includes_sparse_values() -> None:
    index = _DummyIndex()
    client = _DummyClient(index=index)
    store = PineconeVectorStore(index_name="pinecone-test", client=client)
    record = VectorRecord(
        id="rec-sparse",
        values=[0.1],
        text="doc text",
        metadata={"foo": "bar"},
        sparse_values=SparseValues(indices=[1, 3], values=[0.5, 0.25]),
    )

    await store.upsert([record])

    payload, _ = index.calls[0]
    assert payload[0]["sparse_values"] == {"indices": [1, 3], "values": [0.5, 0.25]}


@pytest.mark.asyncio
async def test_pinecone_vector_store_handles_sync_upsert_result() -> None:
    index = _SyncIndex()
    client = _DummyClient(index=index)
    store = PineconeVectorStore(index_name="pinecone-sync", client=client)
    record = VectorRecord(
        id="rec-2", values=[0.2], text="second doc", metadata={"bar": "baz"}
    )

    await store.upsert([record])

    payload, namespace = index.calls[0]
    assert namespace is None
    assert payload[0]["metadata"]["bar"] == "baz"
    assert payload[0]["metadata"]["text"] == "second doc"


def test_pinecone_vector_store_resolves_client_from_dependency(monkeypatch) -> None:
    fake_module = types.ModuleType("pinecone")

    class FakePinecone:
        pass

    fake_module.Pinecone = FakePinecone
    monkeypatch.setitem(sys.modules, "pinecone", fake_module)

    store = PineconeVectorStore(index_name="pinecone-dependency")

    client = store._resolve_client()

    assert isinstance(client, FakePinecone)
    assert store.client is client


@pytest.mark.asyncio
async def test_pinecone_vector_store_raises_runtime_error_when_index_cannot_open() -> (
    None
):
    store = PineconeVectorStore(index_name="pinecone-bad", client=_FailingClient())

    with pytest.raises(
        RuntimeError, match="Unable to open Pinecone index 'pinecone-bad'"
    ):
        await store.upsert([])


@pytest.mark.asyncio
async def test_in_memory_vector_store_search_filters_and_ranks() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                id="vec-1",
                values=[1.0, 0.0],
                text="hello world",
                metadata={"topic": "greeting"},
            ),
            VectorRecord(
                id="vec-2",
                values=[0.0, 1.0],
                text="bye world",
                metadata={"topic": "farewell"},
            ),
        ]
    )

    results = await store.search(
        query=[1.0, 0.0], top_k=2, filter_metadata={"topic": "greeting"}
    )

    assert [result.id for result in results] == ["vec-1"]
    assert results[0].metadata["topic"] == "greeting"


@pytest.mark.asyncio
async def test_in_memory_vector_store_validates_dimensions() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                id="vec-3",
                values=[0.1],
                text="one dim",
                metadata={},
            )
        ]
    )

    with pytest.raises(ValueError, match="Vector dimensions must match"):
        await store.search(query=[0.1, 0.2], top_k=1)


@pytest.mark.asyncio
async def test_in_memory_vector_store_requires_dense_embeddings_for_queries() -> None:
    store = InMemoryVectorStore()

    with pytest.raises(
        ValueError, match="dense embeddings must include non-empty float values"
    ):
        await store.search(query=EmbeddingVector(values=[]))


@pytest.mark.asyncio
async def test_in_memory_vector_store_rejects_sparse_only_query_payloads() -> None:
    store = InMemoryVectorStore()
    with pytest.raises(
        ValueError,
        match="InMemoryVectorStore only supports dense query vectors for search",
    ):
        await store.search(
            query=EmbeddingVector(
                values=[],
                sparse_values=SparseValues(indices=[1], values=[0.5]),
            )
        )


@pytest.mark.asyncio
async def test_pinecone_vector_store_search_normalizes_matches() -> None:
    index = _DummyIndex()
    client = _DummyClient(index=index)
    store = PineconeVectorStore(index_name="pinecone-query", client=client)

    results = await store.search(query=[0.1], top_k=1)

    assert results[0] == SearchResult(
        id="match-1",
        score=0.5,
        text="doc text",
        metadata={"text": "doc text", "topic": "demo"},
    )
    assert index.queries[-1]["top_k"] == 1


@pytest.mark.asyncio
async def test_pinecone_vector_store_requires_dense_or_sparse_embeddings() -> None:
    index = _DummyIndex()
    client = _DummyClient(index=index)
    store = PineconeVectorStore(index_name="pinecone-query", client=client)

    with pytest.raises(
        ValueError, match="query embeddings must include dense or sparse values"
    ):
        await store.search(query=EmbeddingVector(values=[]))


def test_in_memory_vector_store_cosine_similarity_handles_edge_cases() -> None:
    assert InMemoryVectorStore._cosine_similarity([], [1.0]) == 0.0
    assert InMemoryVectorStore._cosine_similarity([1.0], []) == 0.0
    assert InMemoryVectorStore._cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0


@pytest.mark.asyncio
async def test_pinecone_vector_store_search_awaits_async_query_result() -> None:
    index = _AsyncQueryIndex()
    client = _AsyncClient(index=index)
    store = PineconeVectorStore(index_name="pinecone-async", client=client)

    results = await store.search(query=[0.1], top_k=1)

    assert results[0] == SearchResult(
        id="async-match",
        score=0.0,
        text="",
        metadata={},
    )
    assert index.queries[-1]["top_k"] == 1


@pytest.mark.asyncio
async def test_pinecone_vector_store_search_handles_missing_matches_key() -> None:
    index = _EmptyMatchesIndex()
    client = _EmptyMatchesClient(index=index)
    store = PineconeVectorStore(index_name="pinecone-empty", client=client)

    assert await store.search(query=[0.2], top_k=1) == []


@pytest.mark.asyncio
async def test_pinecone_vector_store_search_handles_attribute_matches() -> None:
    match = _AttributeMatch(
        match_id="attr-match",
        score=0.85,
        metadata={"text": "object match", "source": "attr"},
    )
    index = _AttributeIndex(matches=[match])
    client = _AttributeClient(index=index)
    store = PineconeVectorStore(index_name="pinecone-attribute", client=client)

    results = await store.search(query=[0.3], top_k=1)

    assert results == [
        SearchResult(
            id="attr-match",
            score=0.85,
            text="object match",
            metadata={"text": "object match", "source": "attr"},
        )
    ]


@pytest.mark.asyncio
async def test_pinecone_vector_store_search_handles_missing_metadata_and_score() -> (
    None
):
    index = _CustomQueryIndex(matches=[{"id": "no-metadata"}])
    client = _CustomClient(index=index)
    store = PineconeVectorStore(index_name="pinecone-defaults", client=client)

    results = await store.search(query=[0.1], top_k=1)

    assert results == [
        SearchResult(
            id="no-metadata",
            score=0.0,
            text="",
            metadata={},
        )
    ]


@pytest.mark.asyncio
async def test_pinecone_vector_store_search_skips_matches_without_id() -> None:
    index = _CustomQueryIndex(
        matches=[
            {"metadata": {"text": "no id"}, "score": 0.2},
            {"id": "valid-match", "score": 0.8, "metadata": {"text": "has id"}},
        ]
    )
    client = _CustomClient(index=index)
    store = PineconeVectorStore(index_name="pinecone-skip", client=client)

    results = await store.search(query=[0.2], top_k=2)

    assert results == [
        SearchResult(
            id="valid-match",
            score=0.8,
            text="has id",
            metadata={"text": "has id"},
        )
    ]


@pytest.mark.asyncio
async def test_pinecone_vector_store_rejects_records_with_no_dense_or_sparse() -> None:
    index = _DummyIndex()
    client = _DummyClient(index=index)
    store = PineconeVectorStore(index_name="pinecone-test", client=client)

    with pytest.raises(
        ValueError, match="Vector records must include dense values or sparse_values"
    ):
        await store.upsert(
            [
                VectorRecord(
                    id="invalid",
                    values=[],
                    text="empty",
                    metadata={},
                )
            ]
        )


@pytest.mark.asyncio
async def test_in_memory_vector_store_search_accepts_embedding_vector() -> None:
    """Covers line 63: search with EmbeddingVector having non-empty values."""
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                id="vec-ev",
                values=[1.0, 0.0],
                text="ev record",
                metadata={"topic": "ev"},
            )
        ]
    )

    results = await store.search(query=EmbeddingVector(values=[1.0, 0.0]), top_k=1)

    assert len(results) == 1
    assert results[0].id == "vec-ev"


@pytest.mark.asyncio
async def test_in_memory_vector_store_search_skips_empty_value_records() -> None:
    """Covers line 80: records with empty values are skipped during search."""
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                id="empty-vals",
                values=[],
                text="no values",
                metadata={},
                sparse_values=SparseValues(indices=[0], values=[1.0]),
            ),
            VectorRecord(
                id="with-vals",
                values=[1.0],
                text="has values",
                metadata={},
            ),
        ]
    )

    results = await store.search(query=[1.0], top_k=10)

    assert [r.id for r in results] == ["with-vals"]


@pytest.mark.asyncio
async def test_pinecone_vector_store_upserts_sparse_only_records() -> None:
    """Covers lines 144->146: upsert records with sparse_values but no dense."""
    index = _DummyIndex()
    client = _DummyClient(index=index)
    store = PineconeVectorStore(index_name="pinecone-sparse-only", client=client)
    record = VectorRecord(
        id="sparse-only",
        values=[],
        text="sparse doc",
        metadata={"key": "val"},
        sparse_values=SparseValues(indices=[0, 1], values=[0.3, 0.7]),
    )

    await store.upsert([record])

    payload, _ = index.calls[0]
    assert "values" not in payload[0]
    assert payload[0]["sparse_values"] == {"indices": [0, 1], "values": [0.3, 0.7]}
    assert payload[0]["metadata"]["text"] == "sparse doc"
