import pytest
from orcheo.graph.state import State
from orcheo.nodes.rag import (
    ChunkEmbeddingNode,
    ChunkingStrategyNode,
    ConversationCompressorNode,
    ConversationStateNode,
    DenseSearchNode,
    DocumentLoaderNode,
    GroundedGeneratorNode,
    InMemoryMemoryStore,
    InMemoryVectorStore,
    MemorySummarizerNode,
    QueryClarificationNode,
    TopicShiftDetectorNode,
    VectorStoreUpsertNode,
)
from orcheo.nodes.rag.ingestion import (
    RawDocumentInput,
)


@pytest.mark.asyncio
async def test_multi_turn_flow_handles_topic_shift_and_compression() -> None:
    vector_store = InMemoryVectorStore()
    memory_store = InMemoryMemoryStore()

    loader = DocumentLoaderNode(
        name="document_loader",
        documents=[
            RawDocumentInput(
                content="Orcheo offers modular retrieval nodes and generators.",
                metadata={"source": "primer"},
            ),
            RawDocumentInput(
                content="Pricing is usage-based with credits for early adopters.",
                metadata={"source": "policy"},
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
        top_k=2,
        embed_model="test:fake",
    )
    generator = GroundedGeneratorNode(
        name="generator", context_result_key="retriever", context_field="results"
    )

    ingest_state = State(inputs={}, node_results={}, structured_response=None)
    ingest_state["node_results"][loader.name] = await loader.run(ingest_state, {})
    ingest_state["node_results"][chunker.name] = await chunker.run(ingest_state, {})
    ingest_state["node_results"][chunk_embedder.name] = await chunk_embedder.run(
        ingest_state, {}
    )
    ingest_state["node_results"][vector_upsert.name] = await vector_upsert.run(
        ingest_state, {}
    )

    conversation = ConversationStateNode(
        name="conversation_state", memory_store=memory_store
    )
    compressor = ConversationCompressorNode(name="conversation_compressor")
    shift_detector = TopicShiftDetectorNode(
        name="topic_shift", similarity_threshold=0.5
    )
    clarifier = QueryClarificationNode(name="clarifier")
    summarizer = MemorySummarizerNode(
        name="memory_summarizer", memory_store=memory_store
    )

    turn_state = State(
        inputs={"session_id": "sess-flow", "user_message": "Tell me about Orcheo"},
        node_results={},
        structured_response=None,
    )
    turn_state["node_results"]["ingestion"] = ingest_state["node_results"]

    convo_result = await conversation.run(turn_state, {})
    turn_state["node_results"][conversation.name] = convo_result
    turn_state["inputs"]["query"] = convo_result["conversation_history"][-1]["content"]

    retrieval_result = await retriever.run(turn_state, {})
    turn_state["node_results"][retriever.name] = retrieval_result
    generation_result = await generator.run(turn_state, {})
    assert generation_result["citations"]

    turn_state["inputs"]["assistant_message"] = generation_result["reply"]
    convo_result = await conversation.run(turn_state, {})
    turn_state["node_results"][conversation.name] = convo_result

    compressed = await compressor.run(turn_state, {})
    assert compressed["summary"]

    turn_state["inputs"]["user_message"] = "Now what about pricing?"
    convo_result = await conversation.run(turn_state, {})
    turn_state["node_results"][conversation.name] = convo_result
    turn_state["inputs"]["query"] = "What pricing do you offer?"

    shift_result = await shift_detector.run(turn_state, {})
    assert shift_result["route"] == "clarify"

    clarification = await clarifier.run(turn_state, {})
    assert clarification["needs_clarification"] is True

    summary_result = await summarizer.run(turn_state, {})
    assert summary_result["summary"]
    assert await memory_store.get_summary("sess-flow") is not None
