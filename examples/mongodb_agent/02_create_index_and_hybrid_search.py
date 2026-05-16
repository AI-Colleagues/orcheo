"""MongoDB Atlas Search example workflow.

Configurable inputs (config.json):
- database: MongoDB database name
- collection: MongoDB collection name
- text_query: Full-text search query
- vector_path: Document field containing vector embeddings
"""

from langgraph.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes.integrations.databases.mongodb import (
    MongoDBEnsureSearchIndexNode,
    MongoDBEnsureVectorIndexNode,
)


async def orcheo_workflow() -> StateGraph:
    """Build a workflow for index setup and hybrid search."""
    text_index = MongoDBEnsureSearchIndexNode(
        name="ensure_text_index",
        database="{{config.configurable.database}}",
        collection="{{config.configurable.collection}}",
        definition={
            "mappings": {
                "dynamic": False,
                "fields": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
            }
        },
        mode="ensure_or_update",
    )

    vector_index = MongoDBEnsureVectorIndexNode(
        name="ensure_vector_index",
        database="{{config.configurable.database}}",
        collection="{{config.configurable.collection}}",
        dimensions="{{config.configurable.dimensions}}",
        similarity="cosine",
        path="{{config.configurable.vector_path}}",
        mode="ensure_or_update",
    )

    graph = StateGraph(State)
    graph.add_node("ensure_text_index", text_index)
    graph.add_node("ensure_vector_index", vector_index)
    graph.add_edge(START, "ensure_text_index")
    graph.add_edge("ensure_text_index", "ensure_vector_index")
    graph.add_edge("ensure_vector_index", END)
    return graph
