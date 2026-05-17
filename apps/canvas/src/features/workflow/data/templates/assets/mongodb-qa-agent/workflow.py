from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode
from orcheo.nodes.rag import (
    SearchResultAdapterNode,
    SearchResultFormatterNode,
    TextEmbeddingNode,
)
from orcheo.nodes.storage.mongodb import MongoDBHybridSearchNode


class HybridSearchInput(BaseModel):
    """Input for the MongoDB hybrid search tool."""

    query: str = Field(description="User question to search for.")


def build_hybrid_search_tool_graph() -> StateGraph:
    """Build a subworkflow used as an agent tool."""
    graph = StateGraph(State)
    graph.add_node(
        "query_embedding",
        TextEmbeddingNode(
            name="query_embedding",
            input_key="query",
            embed_model="{{config.configurable.embed_model}}",
            model_kwargs={
                "api_key": "[[openai_api_key]]",
                "dimensions": "{{config.configurable.dimensions}}",
            },
            dense_output_key="vector",
            unwrap_single=True,
        ),
    )
    graph.add_node(
        "hybrid_search",
        MongoDBHybridSearchNode(
            name="hybrid_search",
            database="{{config.configurable.database}}",
            collection="{{config.configurable.collection}}",
            text_query="{{inputs.query}}",
            vector="{{query_embedding.vector}}",
            text_paths="{{config.configurable.text_paths}}",
            vector_path="{{config.configurable.vector_path}}",
            top_k=5,
        ),
    )
    graph.add_node(
        "adapt_results",
        SearchResultAdapterNode(
            name="adapt_results",
            source_result_key="hybrid_search",
            results_field="results",
            text_field="raw.recommendation_reason",
            metadata_field="raw",
            source_name="mongodb",
        ),
    )
    graph.add_node(
        "format_results",
        SearchResultFormatterNode(
            name="format_results",
            source_result_key="adapt_results",
            results_field="results",
            title_fields=["platform_name", "name", "title"],
            snippet_fields=["recommendation_reason", "summary", "description"],
            url_fields=["source_url", "url", "link", "source"],
            snippet_label="Reason",
        ),
    )
    graph.add_edge(START, "query_embedding")
    graph.add_edge("query_embedding", "hybrid_search")
    graph.add_edge("hybrid_search", "adapt_results")
    graph.add_edge("adapt_results", "format_results")
    graph.add_edge("format_results", END)
    return graph


def orcheo_workflow() -> StateGraph:
    """Build the MongoDB QA agent workflow."""
    graph = StateGraph(State)

    agent_node = AgentNode(
        name="agent",
        ai_model="{{config.configurable.ai_model}}",
        model_kwargs={"api_key": "[[openai_api_key]]"},
        system_prompt="{{config.configurable.system_prompt}}",
        workflow_tools=[
            {
                "name": "mongodb_hybrid_search",
                "description": "Hybrid search over MongoDB Atlas data.",
                "graph": build_hybrid_search_tool_graph(),
                "args_schema": HybridSearchInput,
                "output_path": "results.format_results.markdown",
            }
        ],
    )

    graph.add_node("agent", agent_node)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    return graph
