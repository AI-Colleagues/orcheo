import pytest
from orcheo.graph.state import State
from orcheo.nodes.rag import (
    ChunkEmbeddingNode,
    ChunkingStrategyNode,
    DenseSearchNode,
    DocumentLoaderNode,
    GroundedGeneratorNode,
    VectorStoreUpsertNode,
)
from orcheo.nodes.rag.ingestion import (
    RawDocumentInput,
)
from orcheo.nodes.rag.vector_store import InMemoryVectorStore


@pytest.mark.asyncio
async def test_reference_pipeline_generates_grounded_answer() -> None:
    vector_store = InMemoryVectorStore()

    loader = DocumentLoaderNode(
        name="document_loader",
        documents=[
            RawDocumentInput(
                content=(
                    "Orcheo delivers modular nodes for retrieval augmented generation."
                ),
                metadata={"source": "primer"},
            ),
            RawDocumentInput(
                content="Grounded generation should always emit citations.",
                metadata={"source": "primer"},
            ),
        ],
    )
    chunker = ChunkingStrategyNode(
        name="chunking_strategy", chunk_size=64, chunk_overlap=8
    )
    chunk_embedder = ChunkEmbeddingNode(
        name="chunk_embedding",
        chunks_field="chunks",
        dense_embedding_specs={"default": {"embed_model": "test:fake"}},
    )
    vector_upsert = VectorStoreUpsertNode(
        name="vector_upsert",
        source_result_key=chunk_embedder.name,
        vector_store=vector_store,
    )
    retriever = DenseSearchNode(
        name="retriever",
        vector_store=vector_store,
        query_key="query",
        top_k=3,
        embed_model="test:fake",
    )
    generator = GroundedGeneratorNode(
        name="generator", context_result_key="retriever", context_field="results"
    )

    state = State(
        inputs={"query": "What does Orcheo deliver?"},
        node_results={},
        structured_response=None,
    )

    loader_result = await loader.run(state, {})
    state["node_results"][loader.name] = loader_result

    chunk_result = await chunker.run(state, {})
    state["node_results"][chunker.name] = chunk_result

    chunk_embedding_result = await chunk_embedder.run(state, {})
    state["node_results"][chunk_embedder.name] = chunk_embedding_result
    upsert_result = await vector_upsert.run(state, {})
    state["node_results"][vector_upsert.name] = upsert_result

    retrieval_result = await retriever.run(state, {})
    state["node_results"][retriever.name] = retrieval_result

    generation_result = await generator.run(state, {})

    assert generation_result["citations"]
    assert any(
        "Orcheo delivers" in citation["snippet"]
        for citation in generation_result["citations"]
    )
    assert "reply" in generation_result
    assert "[1]" in generation_result["reply"]
