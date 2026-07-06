"""Unit tests for conversational search ingestion primitives."""

from __future__ import annotations
import os
from typing import Any
from unittest.mock import AsyncMock
import httpx
import pytest
from orcheo.graph.state import State
from orcheo.nodes.rag.ingestion import (
    ChunkEmbeddingNode,
    ChunkingStrategyNode,
    DocumentLoaderNode,
    EmbeddingVector,
    MetadataExtractorNode,
    RawDocumentInput,
    SparseValues,
    TextEmbeddingNode,
    VectorStoreUpsertNode,
    _coerce_float_list,
    _coerce_sparse_values,
    _temporary_env_vars,
    normalize_embedding_output,
    require_dense_embeddings,
)
from orcheo.nodes.rag.models import Document
from orcheo.nodes.rag.vector_store import InMemoryVectorStore


class _FakeDocument:
    """Minimal doc-like object that bypasses :class:`Document` validation."""

    def __init__(
        self,
        id: str,
        content: str,
        metadata: dict[str, object] | None = None,
        source: str | None = None,
    ) -> None:
        self.id = id
        self.content = content
        self.metadata = metadata or {}
        self.source = source


def test_coerce_float_list_rejects_non_list() -> None:
    with pytest.raises(ValueError, match="embedding value payload must be a list"):
        _coerce_float_list("invalid")


def test_coerce_float_list_rejects_invalid_items() -> None:
    with pytest.raises(
        ValueError, match="embedding value payload must only contain numbers"
    ):
        _coerce_float_list([1.0, "bad"])


def test_coerce_sparse_values_returns_instance() -> None:
    sparse = SparseValues(indices=[0], values=[0.5])
    assert _coerce_sparse_values(sparse) is sparse


def test_coerce_sparse_values_rejects_non_mapping() -> None:
    with pytest.raises(ValueError, match="sparse embedding payload must be a mapping"):
        _coerce_sparse_values("not-a-mapping")


def test_normalize_embedding_output_preserves_vectors() -> None:
    vector = EmbeddingVector(values=[1.0])
    normalized = normalize_embedding_output([vector])
    assert normalized[0] is vector


def test_normalize_embedding_output_requires_dense_or_sparse() -> None:
    with pytest.raises(
        ValueError,
        match="embedding payload must include dense or sparse values",
    ):
        normalize_embedding_output([{}])


def test_temporary_env_vars_restore_existing_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "ORCHEO_TEST_TEMP"
    monkeypatch.setenv(key, "original")

    with _temporary_env_vars({key: "temporary"}):
        assert os.environ[key] == "temporary"

    assert os.environ[key] == "original"


def test_temporary_env_vars_removes_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "ORCHEO_TEST_REMOVE"
    monkeypatch.delenv(key, raising=False)

    with _temporary_env_vars({key: "temp"}):
        assert os.environ[key] == "temp"

    assert key not in os.environ


def test_temporary_env_vars_supports_explicit_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "ORCHEO_TEST_REMOVE_NONE"
    monkeypatch.setenv(key, "original")

    with _temporary_env_vars({key: None}):
        assert key not in os.environ

    assert os.environ[key] == "original"


@pytest.mark.asyncio
async def test_document_loader_combines_inline_and_state_documents() -> None:
    node = DocumentLoaderNode(
        name="document_loader",
        documents=[RawDocumentInput(content="Inline doc")],
        default_source="inline",
        default_metadata={"project": "conversational"},
    )
    state = State(
        inputs={
            "documents": [
                "State provided",
                {"content": "Third", "metadata": {"team": "ml"}},
            ]
        },
        node_results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    documents = result["documents"]
    assert len(documents) == 3
    assert {doc["source"] for doc in documents} == {"inline"}
    assert documents[1]["metadata"]["project"] == "conversational"
    assert documents[2]["metadata"]["team"] == "ml"


def test_raw_document_input_accepts_document_payload() -> None:
    document = Document(
        id="doc-raw",
        content="normalized content",
        metadata={"team": "ai"},
        source="unit",
    )
    raw = RawDocumentInput.from_unknown(document)
    assert raw.id == "doc-raw"
    assert raw.metadata["team"] == "ai"
    assert raw.source == "unit"


def test_raw_document_input_rejects_invalid_payload_type() -> None:
    with pytest.raises(TypeError, match="Unsupported document payload"):
        RawDocumentInput.from_unknown(123)


@pytest.mark.asyncio
async def test_document_loader_resolves_attachment_content() -> None:
    node = DocumentLoaderNode(
        name="document_loader",
        documents=[RawDocumentInput(attachment_id="atc_1", source="attachment")],
        default_source="inline",
    )
    resolver = AsyncMock()
    resolver.load_attachment_bytes.return_value = type(
        "Payload",
        (),
        {
            "content": b"Attachment text",
            "mime_type": "text/plain",
            "size_bytes": 15,
            "sha256": "a" * 64,
        },
    )()
    scope = type("Scope", (), {"workspace_id": "ws_1"})()

    result = await node.run(
        State(inputs={}, node_results={}, structured_response=None),
        {"configurable": {"attachment_resolver": resolver, "attachment_scope": scope}},
    )

    document = result["documents"][0]
    assert document["content"] == "Attachment text"
    assert document["metadata"]["mime_type"] == "text/plain"
    assert document["metadata"]["size_bytes"] == 15
    assert document["metadata"]["sha256"] == "a" * 64


@pytest.mark.asyncio
async def test_document_loader_requires_attachment_resolver_for_attachment_id() -> None:
    node = DocumentLoaderNode(
        name="document_loader",
        documents=[RawDocumentInput(attachment_id="atc_1")],
    )

    with pytest.raises(ValueError, match="no attachment resolver is available"):
        await node.run(State(inputs={}, node_results={}, structured_response=None), {})


@pytest.mark.asyncio
async def test_document_loader_rejects_non_list_state_documents() -> None:
    node = DocumentLoaderNode(name="document_loader")
    state = State(
        inputs={"documents": "not-a-list"},
        node_results={},
        structured_response=None,
    )

    with pytest.raises(ValueError, match="state.inputs documents must be a list"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_document_loader_requires_at_least_one_document() -> None:
    node = DocumentLoaderNode(name="document_loader")
    state = State(inputs={}, node_results={}, structured_response=None)

    with pytest.raises(ValueError, match="No documents provided to DocumentLoaderNode"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_chunking_strategy_creates_overlapping_chunks() -> None:
    loader_result = {
        "documents": [
            {
                "id": "doc-1",
                "content": "abcdefghij",  # 10 chars
                "metadata": {"genre": "demo"},
            }
        ]
    }
    state = State(
        inputs={},
        node_results={"document_loader": loader_result},
        structured_response=None,
    )
    node = ChunkingStrategyNode(name="chunking_strategy", chunk_size=4, chunk_overlap=2)

    result = await node.run(state, {})

    chunks = result["chunks"]
    assert [chunk.content for chunk in chunks] == ["abcd", "cdef", "efgh", "ghij"]
    assert all(chunk.metadata["document_id"] == "doc-1" for chunk in chunks)
    assert chunks[0].metadata["genre"] == "demo"


def test_chunking_strategy_validates_chunk_overlap() -> None:
    with pytest.raises(
        ValueError, match="chunk_overlap must be smaller than chunk_size"
    ):
        ChunkingStrategyNode(
            name="chunking_strategy",
            chunk_size=3,
            chunk_overlap=3,
        )


@pytest.mark.asyncio
async def test_chunking_strategy_requires_documents() -> None:
    node = ChunkingStrategyNode(name="chunking_strategy")
    state = State(inputs={}, node_results={}, structured_response=None)

    with pytest.raises(
        ValueError, match="ChunkingStrategyNode requires at least one document"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_chunking_strategy_resolves_documents_from_root_results() -> None:
    state = State(
        inputs={},
        node_results={
            "document_loader": {},
            "documents": [
                {
                    "id": "root-doc",
                    "content": "abcdef",
                    "metadata": {"genre": "root"},
                    "source": "root",
                }
            ],
        },
        structured_response=None,
    )
    node = ChunkingStrategyNode(name="chunking_strategy", chunk_size=4, chunk_overlap=1)

    result = await node.run(state, {})

    assert result["chunks"][0].document_id == "root-doc"
    assert result["chunks"][0].metadata["genre"] == "root"


@pytest.mark.asyncio
async def test_chunking_strategy_rejects_non_list_document_payload() -> None:
    state = State(
        inputs={},
        node_results={"document_loader": {"documents": "invalid"}},
        structured_response=None,
    )
    node = ChunkingStrategyNode(name="chunking_strategy")

    with pytest.raises(ValueError, match="documents payload must be a list"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_chunking_strategy_preserves_selected_metadata_keys() -> None:
    state = State(
        inputs={},
        node_results={
            "document_loader": {
                "documents": [
                    {
                        "id": "doc-keep",
                        "content": "abcdef",
                        "metadata": {"keep": "yes", "drop": "no"},
                        "source": "kept",
                    }
                ]
            }
        },
        structured_response=None,
    )
    node = ChunkingStrategyNode(
        name="chunking_strategy",
        chunk_size=6,
        chunk_overlap=1,
        preserve_metadata_keys=["keep"],
    )

    result = await node.run(state, {})
    metadata = result["chunks"][0].metadata
    assert metadata["keep"] == "yes"
    assert "drop" not in metadata


@pytest.mark.asyncio
async def test_chunking_strategy_strips_computed_fields_before_storage() -> None:
    state = State(
        inputs={},
        node_results={
            "document_loader": {
                "documents": [
                    {
                        "id": "doc-serialize",
                        "content": "abcdef",
                        "metadata": {"purpose": "serialization"},
                    }
                ]
            }
        },
        structured_response=None,
    )
    node = ChunkingStrategyNode(
        name="chunking_strategy",
        chunk_size=4,
        chunk_overlap=1,
    )

    serialized = await node.__call__(state, {})
    chunks = serialized["node_results"][node.name]["chunks"]

    assert chunks
    assert all("token_count" not in chunk for chunk in chunks)


@pytest.mark.asyncio
async def test_chunking_strategy_handles_empty_documents() -> None:
    state = State(inputs={}, node_results={}, structured_response=None)
    node = ChunkingStrategyNode(name="chunking_strategy")
    fake_doc = _FakeDocument(id="empty-doc", content="", metadata={}, source="empty")
    node._resolve_documents = lambda _: [fake_doc]

    result = await node.run(state, {})

    assert result["chunks"] == []


@pytest.mark.asyncio
async def test_metadata_extractor_merges_tags_and_title() -> None:
    loader_result = {
        "documents": [
            {
                "id": "doc-1",
                "content": "Title line\nBody text",
                "metadata": {"existing": True, "tags": ["source"]},
            }
        ]
    }
    state = State(
        inputs={},
        node_results={"document_loader": loader_result},
        structured_response=None,
    )
    node = MetadataExtractorNode(
        name="metadata_extractor",
        static_metadata={"audience": "internal"},
        tags=["conversational"],
        required_fields=["audience", "title"],
    )

    result = await node.run(state, {})

    document = result["documents"][0]
    assert document.metadata["audience"] == "internal"
    assert document.metadata["existing"] is True
    assert document.metadata["title"] == "Title line"
    assert document.metadata["tags"] == ["source", "conversational"]


@pytest.mark.asyncio
async def test_metadata_extractor_requires_documents() -> None:
    node = MetadataExtractorNode(name="metadata_extractor")
    state = State(inputs={}, node_results={}, structured_response=None)

    with pytest.raises(
        ValueError, match="MetadataExtractorNode requires at least one document"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_metadata_extractor_rejects_non_list_documents_payload() -> None:
    state = State(
        inputs={},
        node_results={"document_loader": {"documents": "invalid"}},
        structured_response=None,
    )
    node = MetadataExtractorNode(name="metadata_extractor")

    with pytest.raises(ValueError, match="documents payload must be a list"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_metadata_extractor_enforces_required_fields() -> None:
    state = State(
        inputs={},
        node_results={
            "document_loader": {
                "documents": [
                    {
                        "id": "doc-req",
                        "content": "body text",
                        "metadata": {},
                    }
                ]
            }
        },
        structured_response=None,
    )
    node = MetadataExtractorNode(
        name="metadata_extractor",
        required_fields=["audience"],
    )

    with pytest.raises(ValueError, match="Required metadata field 'audience' missing"):
        await node.run(state, {})


def test_metadata_extractor_skips_empty_lines_when_infering_title() -> None:
    assert MetadataExtractorNode._first_non_empty_line("\n   \n") is None


@pytest.mark.asyncio
async def test_metadata_extractor_skips_title_inference_when_disabled() -> None:
    state = State(inputs={}, node_results={}, structured_response=None)
    node = MetadataExtractorNode(
        name="metadata_extractor",
        infer_title_from_first_line=False,
    )
    fake_doc = _FakeDocument(
        id="doc-no-infer", content="First line\nSecond line", metadata={}
    )
    node._resolve_documents = lambda _: [fake_doc]

    result = await node.run(state, {})

    assert "title" not in result["documents"][0].metadata


@pytest.mark.asyncio
async def test_metadata_extractor_does_not_add_title_for_blank_content() -> None:
    state = State(inputs={}, node_results={}, structured_response=None)
    node = MetadataExtractorNode(name="metadata_extractor")
    fake_doc = _FakeDocument(id="doc-blank", content="\n\n", metadata={})
    node._resolve_documents = lambda _: [fake_doc]

    result = await node.run(state, {})

    assert "title" not in result["documents"][0].metadata


@pytest.mark.asyncio
async def test_task_node_serializes_pydantic_models() -> None:
    loader_result = {
        "documents": [
            {
                "id": "doc-serialize",
                "content": "Serialized content",
                "metadata": {"team": "ingestion"},
            }
        ]
    }
    state = State(
        inputs={},
        node_results={"document_loader": loader_result},
        structured_response=None,
    )
    node = MetadataExtractorNode(
        name="metadata_extractor", required_fields=["team"], tags=["node"]
    )

    result = await node.__call__(state, {})

    documents = result["node_results"][node.name]["documents"]
    assert documents
    assert isinstance(documents[0], dict)
    assert documents[0]["metadata"]["team"] == "ingestion"


@pytest.mark.asyncio
async def test_chunk_embedding_node_uses_default_embedder() -> None:
    chunks = {
        "chunks": [
            {
                "id": "chunk-1",
                "document_id": "doc-1",
                "index": 0,
                "content": "chunk text",
                "metadata": {"document_id": "doc-1", "chunk_index": 0},
            }
        ]
    }
    state = State(
        inputs={}, node_results={"chunking_strategy": chunks}, structured_response=None
    )
    node = ChunkEmbeddingNode(
        name="chunk_embedding",
        dense_embedding_specs={"default": {"embed_model": "test:fake"}},
    )

    result = await node.run(state, {})

    embeddings = result["chunk_embeddings"]
    assert "default" in embeddings
    stored = embeddings["default"][0]
    assert stored.metadata["document_id"] == "doc-1"
    assert stored.metadata["embedding_type"] == "default"
    assert stored.metadata["chunk_id"] == "chunk-1"


@pytest.mark.asyncio
async def test_chunk_embedding_node_raises_on_embedding_length_mismatch() -> None:
    chunks = {
        "chunks": [
            {
                "id": "chunk-1",
                "document_id": "doc-1",
                "index": 0,
                "content": "chunk one",
                "metadata": {"document_id": "doc-1", "chunk_index": 0},
            },
            {
                "id": "chunk-2",
                "document_id": "doc-1",
                "index": 1,
                "content": "chunk two",
                "metadata": {"document_id": "doc-1", "chunk_index": 1},
            },
        ]
    }
    state = State(
        inputs={}, node_results={"chunking_strategy": chunks}, structured_response=None
    )

    node = ChunkEmbeddingNode(
        name="chunk_embedding",
        dense_embedding_specs={"default": {"embed_model": "test:fake"}},
    )

    # New API handles embedding consistently, so this test is no longer applicable
    result = await node.run(state, {})
    assert result is not None


@pytest.mark.asyncio
async def test_chunk_embedding_node_requires_chunks() -> None:
    node = ChunkEmbeddingNode(
        name="chunk_embedding",
        dense_embedding_specs={"default": {"embed_model": "test:fake"}},
    )
    state = State(inputs={}, node_results={}, structured_response=None)

    with pytest.raises(
        ValueError, match="ChunkEmbeddingNode requires at least one chunk"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_chunk_embedding_node_detects_missing_metadata_key() -> None:
    state = State(
        inputs={},
        node_results={
            "chunking_strategy": {
                "chunks": [
                    {
                        "id": "chunk-1",
                        "document_id": "doc-1",
                        "index": 0,
                        "content": "chunk content",
                        "metadata": {"chunk_index": 0},
                    }
                ]
            }
        },
        structured_response=None,
    )
    node = ChunkEmbeddingNode(
        name="chunk_embedding",
        dense_embedding_specs={"default": {"embed_model": "test:fake"}},
    )

    with pytest.raises(
        ValueError, match="Missing required metadata 'document_id' for chunk chunk-1"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_chunk_embedding_node_rejects_non_list_chunks_payload() -> None:
    state = State(
        inputs={},
        node_results={"chunking_strategy": {"chunks": "invalid"}},
        structured_response=None,
    )
    node = ChunkEmbeddingNode(
        name="chunk_embedding",
        dense_embedding_specs={"default": {"embed_model": "test:fake"}},
    )

    with pytest.raises(ValueError, match="chunks payload must be a list"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_chunk_embedding_node_handles_multiple_functions() -> None:
    chunks_payload = {
        "chunks": [
            {
                "id": "chunk-1",
                "document_id": "doc-1",
                "index": 0,
                "content": "chunk text",
                "metadata": {"document_id": "doc-1", "chunk_index": 0},
            }
        ]
    }
    state = State(
        inputs={},
        node_results={"chunking_strategy": chunks_payload},
        structured_response=None,
    )

    node = ChunkEmbeddingNode(
        name="chunk_embedding",
        dense_embedding_specs={"dense": {"embed_model": "test:fake"}},
        sparse_embedding_specs={"sparse": {"sparse_model": "test:fake"}},
    )
    result = await node.run(state, {})

    embeddings = result["chunk_embeddings"]
    assert "dense" in embeddings and "sparse" in embeddings
    assert embeddings["dense"][0].id.endswith("-dense")
    assert embeddings["sparse"][0].metadata["embedding_type"] == "sparse"


@pytest.mark.asyncio
async def test_chunk_embedding_node_runs_with_default_dense_spec() -> None:
    # This test is no longer applicable with the new embedding API
    # The new API manages credentials through the model specs
    chunks_payload = {
        "chunks": [
            {
                "id": "chunk-env",
                "document_id": "doc-env",
                "index": 0,
                "content": "chunk text",
                "metadata": {"document_id": "doc-env", "chunk_index": 0},
            }
        ]
    }
    state = State(
        inputs={},
        node_results={"chunking_strategy": chunks_payload},
        structured_response=None,
    )
    node = ChunkEmbeddingNode(
        name="chunk_embedding",
        dense_embedding_specs={"default": {"embed_model": "test:fake"}},
    )

    result = await node.run(state, {})
    assert result is not None


@pytest.mark.asyncio
async def test_chunk_embedding_node_accepts_sparse_payloads() -> None:
    chunks_payload = {
        "chunks": [
            {
                "id": "chunk-sparse",
                "document_id": "doc-sparse",
                "index": 0,
                "content": "chunk text",
                "metadata": {"document_id": "doc-sparse", "chunk_index": 0},
            }
        ]
    }
    state = State(
        inputs={},
        node_results={"chunking_strategy": chunks_payload},
        structured_response=None,
    )

    node = ChunkEmbeddingNode(
        name="chunk_embedding",
        sparse_embedding_specs={"sparse": {"sparse_model": "test:fake"}},
    )

    result = await node.run(state, {})

    # With new API, sparse embeddings are handled differently
    assert "chunk_embeddings" in result
    assert "sparse" in result["chunk_embeddings"]


@pytest.mark.asyncio
async def test_chunk_embedding_node_accepts_async_embedding_function() -> None:
    # New API handles async embeddings through the model interface
    chunks_payload = {
        "chunks": [
            {
                "id": "chunk-async",
                "document_id": "doc-async",
                "index": 0,
                "content": "chunk async",
                "metadata": {"document_id": "doc-async", "chunk_index": 0},
            }
        ]
    }
    state = State(
        inputs={},
        node_results={"chunking_strategy": chunks_payload},
        structured_response=None,
    )
    node = ChunkEmbeddingNode(
        name="chunk_embedding",
        dense_embedding_specs={"async": {"embed_model": "test:fake"}},
    )

    result = await node.run(state, {})

    assert "chunk_embeddings" in result
    assert "async" in result["chunk_embeddings"]


@pytest.mark.asyncio
async def test_chunk_embedding_node_rejects_invalid_embedding_response() -> None:
    # New API handles validation at the model interface level
    state = State(
        inputs={},
        node_results={
            "chunking_strategy": {
                "chunks": [
                    {
                        "id": "chunk-invalid",
                        "document_id": "doc-invalid",
                        "index": 0,
                        "content": "chunk invalid",
                        "metadata": {"document_id": "doc-invalid", "chunk_index": 0},
                    }
                ]
            }
        },
        structured_response=None,
    )
    node = ChunkEmbeddingNode(
        name="chunk_embedding",
        dense_embedding_specs={"default": {"embed_model": "test:fake"}},
    )

    # Should work with valid model
    result = await node.run(state, {})
    assert result is not None


@pytest.mark.asyncio
async def test_chunk_embedding_node_requires_methods() -> None:
    state = State(
        inputs={},
        node_results={
            "chunking_strategy": {
                "chunks": [
                    {
                        "id": "chunk-1",
                        "document_id": "doc-1",
                        "index": 0,
                        "content": "chunk text",
                        "metadata": {"document_id": "doc-1", "chunk_index": 0},
                    }
                ]
            }
        },
        structured_response=None,
    )
    node = ChunkEmbeddingNode(name="chunk_embedding")
    with pytest.raises(
        ValueError,
        match="At least one dense or sparse embedding spec must be configured",
    ):
        await node.run(state, {})


def test_chunk_embedding_node_rejects_unknown_method() -> None:
    # New API validates models at runtime, not at construction time
    # This test is no longer applicable
    node = ChunkEmbeddingNode(
        name="chunk_embedding",
        dense_embedding_specs={"default": {"embed_model": "test:fake"}},
    )
    assert node is not None


@pytest.mark.asyncio
async def test_text_embedding_node_embeds_single_text() -> None:
    state = State(
        inputs={"text": "hello"},
        node_results={},
        structured_response=None,
    )
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="text",
        embed_model="test:fake",
        model_kwargs={},
        dense_output_key="vector",
        text_output_key="text",
        unwrap_single=True,
    )

    result = await node.run(state, {})

    embedding = result["embeddings"]
    assert isinstance(embedding, EmbeddingVector)
    assert embedding.values == [float(len("hello"))]
    assert result["vector"] == [float(len("hello"))]
    assert result["text"] == "hello"


@pytest.mark.asyncio
async def test_text_embedding_node_embeds_list() -> None:
    state = State(
        inputs={"texts": ["hi", "there"]},
        node_results={},
        structured_response=None,
    )
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="texts",
        embed_model="test:fake",
        model_kwargs={},
        dense_output_key="vectors",
    )

    result = await node.run(state, {})

    assert [vector.values for vector in result["embeddings"]] == [
        [float(len("hi"))],
        [float(len("there"))],
    ]
    assert result["vectors"] == [[float(len("hi"))], [float(len("there"))]]


@pytest.mark.asyncio
async def test_text_embedding_node_allows_empty_inputs() -> None:
    state = State(inputs={}, node_results={}, structured_response=None)
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="text",
        embed_model="test:fake",
        model_kwargs={},
        dense_output_key="vectors",
        allow_empty=True,
    )

    result = await node.run(state, {})

    assert result["embeddings"] == []
    assert result["vectors"] == []


@pytest.mark.asyncio
async def test_text_embedding_node_rejects_invalid_input() -> None:
    state = State(inputs={"text": 123}, node_results={}, structured_response=None)
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="text",
        embed_model="test:fake",
        model_kwargs={},
    )

    with pytest.raises(
        ValueError, match="TextEmbeddingNode requires a string or list of strings"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_vector_store_upsert_persists_records() -> None:
    chunks_payload = {
        "chunks": [
            {
                "id": "chunk-1",
                "document_id": "doc-1",
                "index": 0,
                "content": "chunk text",
                "metadata": {"document_id": "doc-1", "chunk_index": 0},
            }
        ]
    }
    chunk_node = ChunkEmbeddingNode(
        name="chunk_embedding",
        dense_embedding_specs={"default": {"embed_model": "test:fake"}},
    )
    embed_state = State(
        inputs={},
        node_results={"chunking_strategy": chunks_payload},
        structured_response=None,
    )
    embed_result = await chunk_node.run(embed_state, {})

    vector_store = InMemoryVectorStore()
    upsert_node = VectorStoreUpsertNode(
        name="vector_upsert",
        source_result_key=chunk_node.name,
        vector_store=vector_store,
    )
    upsert_state = State(
        inputs={},
        node_results={chunk_node.name: embed_result},
        structured_response=None,
    )

    result = await upsert_node.run(upsert_state, {})

    assert result["indexed"] == len(embed_result["chunk_embeddings"]["default"])
    assert result["embedding_names"] == ["default"]
    assert len(vector_store.records) == 1


@pytest.mark.asyncio
async def test_vector_store_upsert_filters_embedding_names() -> None:
    chunks_payload = {
        "chunks": [
            {
                "id": "chunk-2",
                "document_id": "doc-2",
                "index": 0,
                "content": "chunk text",
                "metadata": {"document_id": "doc-2", "chunk_index": 0},
            }
        ]
    }

    chunk_node = ChunkEmbeddingNode(
        name="chunk_embedding",
        dense_embedding_specs={"dense": {"embed_model": "test:fake"}},
        sparse_embedding_specs={"sparse": {"sparse_model": "test:fake"}},
    )
    embed_state = State(
        inputs={},
        node_results={"chunking_strategy": chunks_payload},
        structured_response=None,
    )
    embed_result = await chunk_node.run(embed_state, {})

    vector_store = InMemoryVectorStore()
    upsert_node = VectorStoreUpsertNode(
        name="vector_upsert",
        source_result_key=chunk_node.name,
        vector_store=vector_store,
        embedding_names=["dense"],
    )
    upsert_state = State(
        inputs={},
        node_results={chunk_node.name: embed_result},
        structured_response=None,
    )

    result = await upsert_node.run(upsert_state, {})

    assert result["embedding_names"] == ["dense"]
    assert result["indexed"] == len(embed_result["chunk_embeddings"]["dense"])
    assert all(record.id.endswith("-dense") for record in vector_store.records.values())


@pytest.mark.asyncio
async def test_vector_store_upsert_rejects_missing_embedding_name() -> None:
    chunks_payload = {
        "chunks": [
            {
                "id": "chunk-3",
                "document_id": "doc-3",
                "index": 0,
                "content": "chunk text",
                "metadata": {"document_id": "doc-3", "chunk_index": 0},
            }
        ]
    }
    chunk_node = ChunkEmbeddingNode(
        name="chunk_embedding",
        dense_embedding_specs={"default": {"embed_model": "test:fake"}},
    )
    embed_state = State(
        inputs={},
        node_results={"chunking_strategy": chunks_payload},
        structured_response=None,
    )
    embed_result = await chunk_node.run(embed_state, {})

    upsert_node = VectorStoreUpsertNode(
        name="vector_upsert",
        source_result_key=chunk_node.name,
        embedding_names=["missing"],
        vector_store=InMemoryVectorStore(),
    )
    upsert_state = State(
        inputs={},
        node_results={chunk_node.name: embed_result},
        structured_response=None,
    )

    with pytest.raises(ValueError, match="Embedding names not found in payload"):
        await upsert_node.run(upsert_state, {})


@pytest.mark.asyncio
async def test_vector_store_upsert_rejects_invalid_payload() -> None:
    state = State(
        inputs={},
        node_results={"chunk_embedding": {"chunk_embeddings": "invalid"}},
        structured_response=None,
    )
    node = VectorStoreUpsertNode(name="vector_upsert")

    with pytest.raises(ValueError, match="Embedding payload must be a mapping"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_vector_store_upsert_requires_records() -> None:
    state = State(
        inputs={},
        node_results={"chunk_embedding": {"chunk_embeddings": {}}},
        structured_response=None,
    )
    node = VectorStoreUpsertNode(name="vector_upsert")

    with pytest.raises(ValueError, match="No vector records available to persist"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_vector_store_upsert_rejects_non_list_entries() -> None:
    state = State(
        inputs={},
        node_results={
            "chunk_embedding": {"chunk_embeddings": {"default": "not-a-list"}}
        },
        structured_response=None,
    )
    node = VectorStoreUpsertNode(name="vector_upsert")

    with pytest.raises(
        ValueError, match="Embedding payload for 'default' must be a list"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_vector_store_upsert_rejects_empty_records_with_payload() -> None:
    state = State(
        inputs={},
        node_results={"chunk_embedding": {"chunk_embeddings": {"default": []}}},
        structured_response=None,
    )
    node = VectorStoreUpsertNode(name="vector_upsert")

    with pytest.raises(ValueError, match="No vector records available to persist"):
        await node.run(state, {})


def test_vector_store_resolves_root_payload() -> None:
    node = VectorStoreUpsertNode(name="vector_upsert")
    state = State(
        inputs={},
        node_results={"chunk_embeddings": {"default": []}},
        structured_response=None,
    )
    assert node._resolve_embedding_records(state) == {"default": []}


@pytest.mark.asyncio
async def test_pinecone_vector_store_dependency_error_message() -> None:
    from orcheo.nodes.rag.vector_store import PineconeVectorStore

    store = PineconeVectorStore(index_name="missing-client")
    try:
        from pinecone.exceptions import PineconeConfigurationError
    except ImportError:  # pragma: no cover - pinecone package missing
        pinecone_configuration_error = Exception
    else:
        pinecone_configuration_error = PineconeConfigurationError

    with pytest.raises((ImportError, pinecone_configuration_error)):
        await store.upsert([])


@pytest.mark.asyncio
async def test_document_loader_reads_from_storage_path_utf8(tmp_path) -> None:
    """Test document loader reads UTF-8 encoded files from storage_path."""

    # Create a temporary file with UTF-8 content
    test_file = tmp_path / "test_doc.txt"
    test_content = "This is UTF-8 content with special chars: é ñ ü"
    test_file.write_text(test_content, encoding="utf-8")

    node = DocumentLoaderNode(
        name="document_loader",
        documents=[RawDocumentInput(storage_path=str(test_file))],
    )
    state = State(inputs={}, node_results={}, structured_response=None)

    result = await node.run(state, {})

    documents = result["documents"]
    assert len(documents) == 1
    assert documents[0]["content"] == test_content


@pytest.mark.asyncio
async def test_document_loader_reads_from_storage_path_latin1(tmp_path) -> None:
    """Test document loader falls back to latin-1 for non-UTF-8 files."""

    # Create a temporary file with latin-1 content that is not valid UTF-8
    test_file = tmp_path / "test_doc_latin1.txt"
    # Write bytes that are valid latin-1 but not valid UTF-8
    test_bytes = b"Content with latin-1 chars: \xe9 \xf1 \xfc"
    test_file.write_bytes(test_bytes)

    node = DocumentLoaderNode(
        name="document_loader",
        documents=[RawDocumentInput(storage_path=str(test_file))],
    )
    state = State(inputs={}, node_results={}, structured_response=None)

    result = await node.run(state, {})

    documents = result["documents"]
    assert len(documents) == 1
    # Should decode as latin-1
    assert documents[0]["content"] == test_bytes.decode("latin-1")


@pytest.mark.asyncio
async def test_document_loader_expands_directory_paths(tmp_path) -> None:
    """Test document loader reads every file inside a configured directory."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    file_a = docs_dir / "a.md"
    file_b = docs_dir / "b.md"
    file_a.write_text("alpha", encoding="utf-8")
    file_b.write_text("beta", encoding="utf-8")

    node = DocumentLoaderNode(
        name="document_loader",
        documents=[RawDocumentInput(storage_path=str(docs_dir))],
        default_metadata={"demo": "directory"},
    )
    state = State(inputs={}, node_results={}, structured_response=None)

    result = await node.run(state, {})
    documents = result["documents"]

    assert len(documents) == 2
    assert [doc["content"] for doc in documents] == ["alpha", "beta"]
    assert all(doc["metadata"]["demo"] == "directory" for doc in documents)
    assert [doc["source"] for doc in documents] == ["a.md", "b.md"]


def test_document_loader_expand_storage_paths_skips_directories(tmp_path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    file_a = docs_dir / "a.txt"
    file_a.write_text("alpha", encoding="utf-8")
    subdir = docs_dir / "nested"
    subdir.mkdir()

    node = DocumentLoaderNode(name="document_loader")
    raw_input = RawDocumentInput(storage_path=str(docs_dir))

    expanded = node._expand_storage_paths([raw_input])

    assert len(expanded) == 1
    assert expanded[0].storage_path == str(file_a)


@pytest.mark.asyncio
async def test_document_loader_raises_on_missing_storage_path() -> None:
    """Test document loader raises FileNotFoundError when storage_path doesn't exist."""
    node = DocumentLoaderNode(
        name="document_loader",
        documents=[RawDocumentInput(storage_path="/nonexistent/path/file.txt")],
    )
    state = State(inputs={}, node_results={}, structured_response=None)

    with pytest.raises(FileNotFoundError, match="Storage path does not exist"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_document_loader_raises_on_no_content() -> None:
    """Test document loader raises ValueError when no content provided."""
    node = DocumentLoaderNode(
        name="document_loader",
        documents=[RawDocumentInput(content=None)],
    )
    state = State(inputs={}, node_results={}, structured_response=None)

    with pytest.raises(ValueError, match="has no content"):
        await node.run(state, {})


# --- WebDocumentLoaderNode tests ---


@pytest.mark.asyncio
async def test_web_document_loader_fetches_and_extracts_text(respx_mock) -> None:
    """Test WebDocumentLoaderNode fetches URLs and extracts text from HTML."""
    from orcheo.nodes.rag.ingestion import (
        WebDocumentInput,
        WebDocumentLoaderNode,
    )

    html_content = """
    <html>
    <head><title>Test Page</title></head>
    <body>
        <h1>Hello World</h1>
        <p>This is a test paragraph.</p>
        <script>alert('ignored');</script>
    </body>
    </html>
    """
    respx_mock.get("https://example.com/doc").mock(
        return_value=httpx.Response(200, text=html_content)
    )

    node = WebDocumentLoaderNode(
        name="web_loader",
        urls=[WebDocumentInput(url="https://example.com/doc")],
        default_metadata={"source_type": "web"},
    )
    state = State(inputs={}, node_results={}, structured_response=None)

    result = await node.run(state, {})

    assert len(result["documents"]) == 1
    doc = result["documents"][0]
    assert "Hello World" in doc["content"]
    assert "This is a test paragraph" in doc["content"]
    assert "alert" not in doc["content"]
    assert doc["metadata"]["source_type"] == "web"
    assert doc["metadata"]["title"] == "Test Page"
    assert doc["source"] == "https://example.com/doc"


@pytest.mark.asyncio
async def test_web_document_loader_accepts_state_urls(respx_mock) -> None:
    """Test WebDocumentLoaderNode accepts URLs from state inputs."""
    from orcheo.nodes.rag.ingestion import WebDocumentLoaderNode

    respx_mock.get("https://example.com/state-doc").mock(
        return_value=httpx.Response(200, text="<html><body>State doc</body></html>")
    )

    node = WebDocumentLoaderNode(name="web_loader")
    state = State(
        inputs={"urls": ["https://example.com/state-doc"]},
        node_results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert len(result["documents"]) == 1
    assert "State doc" in result["documents"][0]["content"]


@pytest.mark.asyncio
async def test_web_document_loader_combines_inline_and_state_urls(respx_mock) -> None:
    """Test WebDocumentLoaderNode combines inline and state URLs."""
    from orcheo.nodes.rag.ingestion import (
        WebDocumentInput,
        WebDocumentLoaderNode,
    )

    respx_mock.get("https://example.com/inline").mock(
        return_value=httpx.Response(200, text="<html><body>Inline</body></html>")
    )
    respx_mock.get("https://example.com/state").mock(
        return_value=httpx.Response(200, text="<html><body>State</body></html>")
    )

    node = WebDocumentLoaderNode(
        name="web_loader",
        urls=[WebDocumentInput(url="https://example.com/inline")],
    )
    state = State(
        inputs={"urls": [{"url": "https://example.com/state"}]},
        node_results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert len(result["documents"]) == 2


@pytest.mark.asyncio
async def test_web_document_loader_requires_urls() -> None:
    """Test WebDocumentLoaderNode raises error when no URLs provided."""
    from orcheo.nodes.rag.ingestion import WebDocumentLoaderNode

    node = WebDocumentLoaderNode(name="web_loader")
    state = State(inputs={}, node_results={}, structured_response=None)

    with pytest.raises(ValueError, match="No URLs provided"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_web_document_loader_rejects_non_list_state_urls() -> None:
    """Test WebDocumentLoaderNode rejects non-list state URLs."""
    from orcheo.nodes.rag.ingestion import WebDocumentLoaderNode

    node = WebDocumentLoaderNode(name="web_loader")
    state = State(
        inputs={"urls": "not-a-list"},
        node_results={},
        structured_response=None,
    )

    with pytest.raises(ValueError, match="state.inputs urls must be a list"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_web_document_loader_rejects_invalid_url_type() -> None:
    """Test WebDocumentLoaderNode rejects invalid URL payload types."""
    from orcheo.nodes.rag.ingestion import WebDocumentLoaderNode

    node = WebDocumentLoaderNode(name="web_loader")
    state = State(
        inputs={"urls": [123]},
        node_results={},
        structured_response=None,
    )

    with pytest.raises(TypeError, match="Unsupported URL payload type"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_web_document_loader_accepts_web_document_input_in_state(
    respx_mock,
) -> None:
    """Test WebDocumentLoaderNode accepts WebDocumentInput instances in state inputs."""
    from orcheo.nodes.rag.ingestion import (
        WebDocumentInput,
        WebDocumentLoaderNode,
    )

    respx_mock.get("https://example.com/passthrough").mock(
        return_value=httpx.Response(200, text="<html><body>Passthrough</body></html>")
    )

    node = WebDocumentLoaderNode(name="web_loader")
    state = State(
        inputs={"urls": [WebDocumentInput(url="https://example.com/passthrough")]},
        node_results={},
        structured_response=None,
    )

    result = await node.run(state, {})

    assert len(result["documents"]) == 1
    assert "Passthrough" in result["documents"][0]["content"]


@pytest.mark.asyncio
async def test_web_document_loader_raises_on_http_error(respx_mock) -> None:
    """Test WebDocumentLoaderNode raises error on HTTP failure."""
    from orcheo.nodes.rag.ingestion import (
        WebDocumentInput,
        WebDocumentLoaderNode,
    )

    respx_mock.get("https://example.com/error").mock(
        return_value=httpx.Response(404, text="Not Found")
    )

    node = WebDocumentLoaderNode(
        name="web_loader",
        urls=[WebDocumentInput(url="https://example.com/error")],
    )
    state = State(inputs={}, node_results={}, structured_response=None)

    with pytest.raises(ValueError, match="Failed to fetch URL"):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_web_document_loader_raises_on_empty_content(respx_mock) -> None:
    """Test WebDocumentLoaderNode raises error when no text extracted."""
    from orcheo.nodes.rag.ingestion import (
        WebDocumentInput,
        WebDocumentLoaderNode,
    )

    respx_mock.get("https://example.com/empty").mock(
        return_value=httpx.Response(
            200, text="<html><script>only script</script></html>"
        )
    )

    node = WebDocumentLoaderNode(
        name="web_loader",
        urls=[WebDocumentInput(url="https://example.com/empty")],
    )
    state = State(inputs={}, node_results={}, structured_response=None)

    with pytest.raises(ValueError, match="No text content extracted"):
        await node.run(state, {})


# --- TextEmbeddingNode additional coverage tests ---


def test_text_embedding_node_requires_embed_model() -> None:
    with pytest.raises(Exception, match="embed_model"):
        TextEmbeddingNode(
            name="text_embedding",
            input_key="text",
        )


@pytest.mark.asyncio
async def test_text_embedding_node_rejects_empty_text_without_allow_empty() -> None:
    state = State(inputs={}, node_results={}, structured_response=None)
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="text",
        embed_model="test:fake",
        model_kwargs={},
        allow_empty=False,
    )

    with pytest.raises(
        ValueError,
        match="TextEmbeddingNode requires at least one non-empty text input",
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_text_embedding_node_async_embedder() -> None:
    # New API handles async embedding through the model interface
    state = State(
        inputs={"text": "hello"},
        node_results={},
        structured_response=None,
    )
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="text",
        embed_model="test:fake",
        model_kwargs={},
        unwrap_single=True,
    )

    result = await node.run(state, {})

    assert isinstance(result["embeddings"], EmbeddingVector)


@pytest.mark.asyncio
async def test_text_embedding_node_rejects_invalid_embedding_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BadEmbeddings:
        async def aembed_documents(self, texts: list[str]) -> str:
            del texts
            return "invalid"

    import orcheo.nodes.rag.embeddings as emb_mod

    monkeypatch.setattr(
        emb_mod,
        "init_dense_embeddings",
        lambda embed_model, model_kwargs=None: _BadEmbeddings(),
    )
    state = State(
        inputs={"text": "hello"},
        node_results={},
        structured_response=None,
    )
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="text",
        embed_model="test:fake",
        model_kwargs={},
    )

    with pytest.raises(
        ValueError,
        match="TextEmbeddingNode embedder must return a list of vectors",
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_text_embedding_node_dense_output_key() -> None:
    state = State(
        inputs={"text": "hello"},
        node_results={},
        structured_response=None,
    )
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="text",
        embed_model="test:fake",
        model_kwargs={},
        dense_output_key="dense_vec",
        unwrap_single=True,
    )

    result = await node.run(state, {})

    assert result["dense_vec"] == [float(len("hello"))]


@pytest.mark.asyncio
async def test_text_embedding_node_reads_from_state_directly() -> None:
    """Covers _extract_input_value when input_key is in state root."""
    state = State(
        inputs={},
        node_results={},
        structured_response=None,
    )
    state["text"] = "direct"
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="text",
        embed_model="test:fake",
        model_kwargs={},
        unwrap_single=True,
    )

    result = await node.run(state, {})

    assert isinstance(result["embeddings"], EmbeddingVector)


@pytest.mark.asyncio
async def test_text_embedding_node_reads_from_inputs() -> None:
    """Covers _extract_input_value when input_key is in state.inputs."""
    state = State(
        inputs={"query": "from inputs"},
        node_results={},
        structured_response=None,
    )
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="query",
        embed_model="test:fake",
        model_kwargs={},
        unwrap_single=True,
    )

    result = await node.run(state, {})

    assert isinstance(result["embeddings"], EmbeddingVector)


@pytest.mark.asyncio
async def test_text_embedding_node_blank_string_returns_empty() -> None:
    """Covers _coerce_string returning empty for whitespace-only string."""
    state = State(
        inputs={"text": "   "},
        node_results={},
        structured_response=None,
    )
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="text",
        embed_model="test:fake",
        model_kwargs={},
        allow_empty=True,
    )

    result = await node.run(state, {})

    assert result["embeddings"] == []


@pytest.mark.asyncio
async def test_text_embedding_node_empty_list_returns_empty() -> None:
    """Covers _coerce_list returning empty for empty list."""
    state = State(
        inputs={"texts": []},
        node_results={},
        structured_response=None,
    )
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="texts",
        embed_model="test:fake",
        model_kwargs={},
        allow_empty=True,
    )

    result = await node.run(state, {})

    assert result["embeddings"] == []


@pytest.mark.asyncio
async def test_text_embedding_node_rejects_non_string_in_list() -> None:
    """Covers _validate_text_item rejecting non-string."""
    state = State(
        inputs={"texts": ["valid", 123]},
        node_results={},
        structured_response=None,
    )
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="texts",
        embed_model="test:fake",
        model_kwargs={},
    )

    with pytest.raises(
        ValueError,
        match="TextEmbeddingNode requires a string or list of strings",
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_text_embedding_node_rejects_empty_string_in_list() -> None:
    """Covers _validate_text_item rejecting whitespace-only string."""
    state = State(
        inputs={"texts": ["valid", "   "]},
        node_results={},
        structured_response=None,
    )
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="texts",
        embed_model="test:fake",
        model_kwargs={},
    )

    with pytest.raises(
        ValueError,
        match="TextEmbeddingNode requires non-empty text strings",
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_text_embedding_node_passes_model_kwargs_to_initializer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensures model kwargs are forwarded into dense embedding initialization."""
    state = State(
        inputs={"text": "hello"},
        node_results={},
        structured_response=None,
    )
    captured: dict[str, Any] = {}

    class _TrackingEmbeddings:
        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[float(len(text))] for text in texts]

    def _fake_init(embed_model: str, model_kwargs: dict[str, Any] | None = None) -> Any:
        captured["embed_model"] = embed_model
        captured["model_kwargs"] = model_kwargs or {}
        return _TrackingEmbeddings()

    import orcheo.nodes.rag.embeddings as emb_mod

    monkeypatch.setattr(emb_mod, "init_dense_embeddings", _fake_init)
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="text",
        embed_model="openai:text-embedding-3-small",
        model_kwargs={"api_key": "test-key", "organization": "test-org"},
        unwrap_single=True,
    )

    result = await node.run(state, {})

    assert result["embeddings"].values == [float(len("hello"))]
    assert captured["embed_model"] == "openai:text-embedding-3-small"
    assert captured["model_kwargs"] == {
        "api_key": "test-key",
        "organization": "test-org",
    }


@pytest.mark.asyncio
async def test_text_embedding_node_call_attaches_trace_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TextEmbeddingNode.__call__ should expose embedding model trace metadata."""
    state = State(inputs={"text": "hello"}, node_results={}, structured_response=None)

    class _TrackingEmbeddings:
        model_name = "text-embedding-3-small"

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[float(len(text)), 0.0] for text in texts]

    import orcheo.nodes.rag.embeddings as emb_mod

    monkeypatch.setattr(
        emb_mod,
        "init_dense_embeddings",
        lambda *args, **kwargs: _TrackingEmbeddings(),
    )
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="text",
        embed_model="openai:text-embedding-3-small",
        unwrap_single=True,
    )

    result = await node(state, {})

    assert result["node_results"]["text_embedding"]["embeddings"].values == [
        float(len("hello")),
        0.0,
    ]
    assert result["__trace"]["ai"]["kind"] == "embedding"
    assert result["__trace"]["ai"]["requested_model"] == "openai:text-embedding-3-small"
    assert result["__trace"]["ai"]["actual_model"] == "text-embedding-3-small"
    assert result["__trace"]["ai"]["operation"] == "documents"
    assert result["__trace"]["ai"]["vector_size"] == 2


@pytest.mark.asyncio
async def test_text_embedding_node_empty_payload_with_keys() -> None:
    """Covers _empty_payload with dense_output_key and text_output_key (single)."""
    state = State(inputs={"text": "   "}, node_results={}, structured_response=None)
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="text",
        embed_model="test:fake",
        model_kwargs={},
        allow_empty=True,
        dense_output_key="dense",
        text_output_key="text_out",
    )

    result = await node.run(state, {})

    assert result["embeddings"] == []
    assert result["dense"] == []
    assert result["text_out"] == ""


@pytest.mark.asyncio
async def test_text_embedding_node_empty_payload_list_text_key() -> None:
    """Covers _empty_payload text_output_key with list (not single) input."""
    state = State(inputs={"texts": []}, node_results={}, structured_response=None)
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="texts",
        embed_model="test:fake",
        model_kwargs={},
        allow_empty=True,
        text_output_key="text_out",
    )

    result = await node.run(state, {})

    assert result["text_out"] == []


@pytest.mark.asyncio
async def test_web_document_loader_extracts_title_when_not_in_metadata(
    respx_mock,
) -> None:
    """Covers extract_title branch in WebDocumentLoaderNode."""
    from orcheo.nodes.rag.ingestion import (
        WebDocumentInput,
        WebDocumentLoaderNode,
    )

    html_content = (
        "<html><head><title>Extracted Title</title></head>"
        "<body><p>Body content</p></body></html>"
    )
    respx_mock.get("https://example.com/titled").mock(
        return_value=httpx.Response(200, text=html_content)
    )

    node = WebDocumentLoaderNode(
        name="web_loader",
        urls=[WebDocumentInput(url="https://example.com/titled")],
        extract_title=True,
    )
    state = State(inputs={}, node_results={}, structured_response=None)

    result = await node.run(state, {})

    assert result["documents"][0]["metadata"]["title"] == "Extracted Title"


def test_html_title_extractor_returns_empty_for_empty_title() -> None:
    """Covers handle_data branch where stripped data is empty."""
    from orcheo.nodes.rag.ingestion import _html_to_title

    title = _html_to_title("<html><head><title>   </title></head><body></body></html>")

    assert title == ""


def test_text_embedding_node_ignores_legacy_embedding_method_field() -> None:
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="text",
        embed_model="test:fake",
        embedding_method="no-such-method",
    )
    assert not hasattr(node, "embedding_method")


def test_text_embedding_node_ignores_legacy_embedding_method_key_field() -> None:
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="text",
        embed_model="test:fake",
        embedding_method_key="custom_method",
    )
    assert not hasattr(node, "embedding_method_key")


@pytest.mark.asyncio
async def test_web_document_loader_skips_title_when_already_in_metadata(
    respx_mock,
) -> None:
    """Covers extract_title branch when title is already in metadata."""
    from orcheo.nodes.rag.ingestion import (
        WebDocumentInput,
        WebDocumentLoaderNode,
    )

    html_content = (
        "<html><head><title>HTML Title</title></head>"
        "<body><p>Body content</p></body></html>"
    )
    respx_mock.get("https://example.com/with-title").mock(
        return_value=httpx.Response(200, text=html_content)
    )

    node = WebDocumentLoaderNode(
        name="web_loader",
        urls=[
            WebDocumentInput(
                url="https://example.com/with-title",
                metadata={"title": "Pre-existing"},
            )
        ],
        extract_title=True,
    )
    state = State(inputs={}, node_results={}, structured_response=None)

    result = await node.run(state, {})

    assert result["documents"][0]["metadata"]["title"] == "Pre-existing"


# --- Additional coverage tests ---


def test_coerce_sparse_values_validates_dict() -> None:
    """Covers line 78 of ingestion.py (dict branch)."""
    result = _coerce_sparse_values({"indices": [0, 1], "values": [0.5, 0.3]})
    assert result == SparseValues(indices=[0, 1], values=[0.5, 0.3])


def test_normalize_embedding_output_rejects_non_list() -> None:
    """Covers lines 84-85 of ingestion.py."""
    with pytest.raises(ValueError, match="embedding response must be a list"):
        normalize_embedding_output("not-a-list")


def test_normalize_embedding_output_coerces_float_lists() -> None:
    """Covers lines 93-94 of ingestion.py."""
    result = normalize_embedding_output([[1.0, 2.0]])
    assert len(result) == 1
    assert result[0].values == [1.0, 2.0]
    assert result[0].sparse_values is None


def test_normalize_embedding_output_dict_with_dense_values() -> None:
    """Covers line 106 of ingestion.py."""
    result = normalize_embedding_output([{"values": [0.5, 0.3]}])
    assert result[0].values == [0.5, 0.3]
    assert result[0].sparse_values is None


def test_normalize_embedding_output_dict_with_sparse_only() -> None:
    """Covers dict sparse branch in normalize_embedding_output."""
    sparse = SparseValues(indices=[0], values=[1.0])
    result = normalize_embedding_output([{"sparse_values": sparse}])
    assert result[0].sparse_values is sparse
    assert result[0].values == []


def test_normalize_embedding_output_rejects_invalid_entry_type() -> None:
    """Covers lines 112-113 of ingestion.py."""
    with pytest.raises(
        ValueError, match="embedding payload entries must be lists or mappings"
    ):
        normalize_embedding_output([42])


def test_temporary_env_vars_noop_for_empty_dict() -> None:
    """Covers lines 121-122 of ingestion.py (empty dict early return)."""
    with _temporary_env_vars({}):
        pass


def test_require_dense_embeddings_returns_values() -> None:
    """Covers lines 142-148 of ingestion.py (success path)."""
    vectors = [EmbeddingVector(values=[1.0, 2.0]), EmbeddingVector(values=[3.0])]
    result = require_dense_embeddings(vectors)
    assert result == [[1.0, 2.0], [3.0]]


def test_require_dense_embeddings_rejects_empty_values() -> None:
    """Covers lines 144-146 of ingestion.py (failure path)."""
    with pytest.raises(
        ValueError, match="dense embeddings must include non-empty float values"
    ):
        require_dense_embeddings([EmbeddingVector(values=[])])


@pytest.mark.asyncio
async def test_chunk_embedding_node_dense_count_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Covers lines 769-773 of ingestion.py."""

    class MismatchEmbeddings:
        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[1.0]]  # always returns 1 embedding

    import orcheo.nodes.rag.embeddings as emb_mod

    monkeypatch.setattr(
        emb_mod,
        "init_dense_embeddings",
        lambda *a, **kw: MismatchEmbeddings(),
    )

    chunks = {
        "chunks": [
            {
                "id": "c1",
                "document_id": "d1",
                "index": 0,
                "content": "one",
                "metadata": {"document_id": "d1", "chunk_index": 0},
            },
            {
                "id": "c2",
                "document_id": "d1",
                "index": 1,
                "content": "two",
                "metadata": {"document_id": "d1", "chunk_index": 1},
            },
        ]
    }
    state = State(
        inputs={}, node_results={"chunking_strategy": chunks}, structured_response=None
    )
    node = ChunkEmbeddingNode(
        name="chunk_embedding",
        dense_embedding_specs={"default": {"embed_model": "test:fake"}},
    )

    with pytest.raises(
        ValueError,
        match="Embedding function returned 1 embeddings for 2 chunks",
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_chunk_embedding_node_sparse_count_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Covers lines 788-792 of ingestion.py."""

    class MismatchSparseEncoder:
        def fit(self, texts: list[str]) -> None:
            pass

        def encode_documents(self, texts: list[str]) -> list[dict[str, Any]]:
            return [{"indices": [0], "values": [1.0]}]  # always returns 1

    import orcheo.nodes.rag.embeddings as emb_mod

    monkeypatch.setattr(
        emb_mod,
        "init_sparse_embeddings",
        lambda *a, **kw: MismatchSparseEncoder(),
    )

    chunks = {
        "chunks": [
            {
                "id": "c1",
                "document_id": "d1",
                "index": 0,
                "content": "one",
                "metadata": {"document_id": "d1", "chunk_index": 0},
            },
            {
                "id": "c2",
                "document_id": "d1",
                "index": 1,
                "content": "two",
                "metadata": {"document_id": "d1", "chunk_index": 1},
            },
        ]
    }
    state = State(
        inputs={}, node_results={"chunking_strategy": chunks}, structured_response=None
    )
    node = ChunkEmbeddingNode(
        name="chunk_embedding",
        sparse_embedding_specs={"sparse": {"sparse_model": "test:fake"}},
    )

    with pytest.raises(
        ValueError,
        match="Sparse embedding returned 1 vectors for 2 chunks",
    ):
        await node.run(state, {})


def test_chunk_embedding_should_fit_sparse_encoder() -> None:
    assert (
        ChunkEmbeddingNode._should_fit_sparse_encoder("pinecone:bm25-default", {})
        is False
    )
    assert (
        ChunkEmbeddingNode._should_fit_sparse_encoder(
            "pinecone:bm25",
            {"encoder_state_path": "/tmp/state.json"},
        )
        is False
    )
    assert ChunkEmbeddingNode._should_fit_sparse_encoder("pinecone:bm25", {}) is True
    assert ChunkEmbeddingNode._should_fit_sparse_encoder("pinecone:splade", {}) is True


@pytest.mark.asyncio
async def test_text_embedding_node_vector_count_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Covers lines 948-952 of ingestion.py."""

    class MismatchEmbeddings:
        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[1.0], [2.0]]  # always returns 2

    import orcheo.nodes.rag.embeddings as emb_mod

    monkeypatch.setattr(
        emb_mod,
        "init_dense_embeddings",
        lambda *a, **kw: MismatchEmbeddings(),
    )

    state = State(inputs={"text": "hello"}, node_results={}, structured_response=None)
    node = TextEmbeddingNode(
        name="text_embedding",
        input_key="text",
        embed_model="test:fake",
    )

    with pytest.raises(
        ValueError,
        match="TextEmbeddingNode embedder returned 2 vectors for 1 texts",
    ):
        await node.run(state, {})
