"""Web scraping and MongoDB vector upload workflow.

Scrapes given web pages, chunks the body text, generates vector embeddings,
and uploads the results to a MongoDB collection with source URL metadata.

Configurable inputs (config.json):
- database: MongoDB database name
- collection: MongoDB collection name
- vector_path: Document field used for the embedding vector
- embed_model: Dense embedding model identifier
"""

from langgraph.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes.conversational_search import (
    ChunkEmbeddingNode,
    ChunkingStrategyNode,
    WebDocumentLoaderNode,
)
from orcheo.nodes.integrations.databases.mongodb import MongoDBInsertManyNode


async def orcheo_workflow() -> StateGraph:
    """Build the web scraping and MongoDB upload workflow."""
    web_loader = WebDocumentLoaderNode(
        name="web_loader",
        urls="{{config.configurable.urls}}",
    )

    chunking = ChunkingStrategyNode(
        name="chunking",
        source_result_key="web_loader",
        chunk_size=800,
        chunk_overlap=80,
    )

    chunk_embedding = ChunkEmbeddingNode(
        name="chunk_embedding",
        source_result_key="chunking",
        dense_embedding_specs={
            "dense": {
                "embed_model": "{{config.configurable.embed_model}}",
                "model_kwargs": {
                    "api_key": "[[openai_api_key]]",
                    "dimensions": "{{config.configurable.dimensions}}",
                },
            }
        },
    )

    mongodb_upload = MongoDBInsertManyNode(
        name="mongodb_upload",
        database="{{config.configurable.database}}",
        collection="{{config.configurable.collection}}",
        source_result_key="chunk_embedding",
        embedding_name="dense",
        vector_field="{{config.configurable.vector_path}}",
        text_field="body",
    )

    graph = StateGraph(State)
    graph.add_node("web_loader", web_loader)
    graph.add_node("chunking", chunking)
    graph.add_node("chunk_embedding", chunk_embedding)
    graph.add_node("mongodb_upload", mongodb_upload)
    graph.add_edge(START, "web_loader")
    graph.add_edge("web_loader", "chunking")
    graph.add_edge("chunking", "chunk_embedding")
    graph.add_edge("chunk_embedding", "mongodb_upload")
    graph.add_edge("mongodb_upload", END)

    return graph
