"""Production-ready conversational search demo highlighting guardrails and caching."""

from collections import OrderedDict
from typing import Any
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from pydantic import Field
from orcheo.edges import SwitchCase, SwitchEdge
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.conversational_search.conversation import (
    AnswerCachingNode,
    ConversationStateNode,
    InMemoryMemoryStore,
    SessionManagementNode,
)
from orcheo.nodes.conversational_search.generation import (
    CitationsFormatterNode,
    GroundedGeneratorNode,
    HallucinationGuardNode,
    StreamingGeneratorNode,
)
from orcheo.nodes.conversational_search.query_processing import (
    MultiHopPlannerNode,
    QueryRewriteNode,
)
from orcheo.nodes.conversational_search.retrieval import (
    DenseSearchNode,
    SourceRouterNode,
)
from orcheo.nodes.conversational_search.vector_store import (
    BaseVectorStore,
    PineconeVectorStore,
)
from orcheo.nodes.evaluation import (
    MemoryPrivacyNode,
    PolicyComplianceNode,
)


class ResultToInputsNode(TaskNode):
    """Copy values out of a result entry into the running input payload."""

    source_result_key: str = Field(
        default="grounded_generator", description="Result entry to read from."
    )
    mappings: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of target input key -> source field path",
    )
    allow_missing: bool = Field(
        default=True,
        description="If true, missing source fields are ignored.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Populate inputs using the configured result mappings."""
        results = state.get("results", {})
        payload = results.get(self.source_result_key, {})
        if not isinstance(payload, dict):
            return {"mapped_keys": []}

        inputs = state.get("inputs") or {}
        state["inputs"] = inputs

        mapped: list[str] = []
        for target_key, source_path in self.mappings.items():
            value = payload
            for segment in source_path.split("."):
                if not isinstance(value, dict) or segment not in value:
                    value = None
                    break
                value = value.get(segment)
            if value is None:
                if not self.allow_missing:
                    raise ValueError(
                        f"Field '{source_path}' missing from {self.source_result_key}"
                    )
                continue
            inputs[target_key] = value
            mapped.append(target_key)
        return {"mapped_keys": mapped}


class PlanToSearchQueryNode(TaskNode):
    """Use the multi-hop plan to pick the next search query."""

    plan_source: str = Field(default="multi_hop", description="Plan entry key.")
    plan_key: str = Field(default="plan", description="Plan payload field.")
    query_key: str = Field(default="search_query", description="Target query key.")
    plan_target_key: str = Field(
        default="multi_hop_plan", description="Where to stash the plan."
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Select the next search query from the multi-hop plan."""
        results = state.get("results", {})
        plan_payload = results.get(self.plan_source, {})
        plan_entries = plan_payload.get(self.plan_key) or []
        normalized = [
            entry
            for entry in plan_entries
            if isinstance(entry, dict) and isinstance(entry.get("query"), str)
        ]

        inputs = state.get("inputs") or {}
        state["inputs"] = inputs
        inputs[self.plan_target_key] = normalized

        selected_query = (
            normalized[0]["query"] if normalized else inputs.get(self.query_key)
        )
        if selected_query:
            inputs[self.query_key] = selected_query

        return {"selected_query": selected_query, "hop_count": len(normalized)}


def build_demo_nodes(
    *,
    vector_store: BaseVectorStore,
    memory_store: InMemoryMemoryStore,
    retrieval_cfg: dict[str, Any],
    session_cfg: dict[str, Any],
    caching_cfg: dict[str, Any],
    multi_hop_cfg: dict[str, Any],
    privacy_cfg: dict[str, Any],
    streaming_cfg: dict[str, Any],
    shared_cache: OrderedDict[str, tuple[str, float | None]],
    guardrails: list[str],
) -> dict[str, TaskNode]:
    """Create the nodes that drive the production conversational graph."""
    nodes: dict[str, TaskNode] = {}

    nodes["session_manager"] = SessionManagementNode(
        name="session_manager",
        memory_store=memory_store,
        max_turns=session_cfg.get("max_turns"),
    )

    nodes["conversation_state"] = ConversationStateNode(
        name="conversation_state",
        memory_store=memory_store,
        max_turns=session_cfg.get("max_turns"),
    )

    nodes["conversation_history_sync"] = ResultToInputsNode(
        name="conversation_history_sync",
        source_result_key=nodes["conversation_state"].name,
        mappings={
            "history": "conversation_history",
            "conversation_history": "conversation_history",
        },
    )

    nodes["answer_cache_check"] = AnswerCachingNode(
        name="answer_cache_check",
        cache=shared_cache,
        ttl_seconds=caching_cfg.get("ttl_seconds"),
        max_entries=caching_cfg.get("max_entries", 128),
        source_result_key="policy_compliance",
        response_field="sanitized",
    )

    nodes["query_rewrite"] = QueryRewriteNode(
        name="query_rewrite",
        ai_model="openai:gpt-4o-mini",
        model_kwargs={"api_key": "[[openai_api_key]]"},
    )
    nodes["rewrite_to_search"] = ResultToInputsNode(
        name="rewrite_to_search",
        source_result_key=nodes["query_rewrite"].name,
        mappings={"search_query": "query"},
    )

    nodes["multi_hop_planner"] = MultiHopPlannerNode(
        name="multi_hop_planner",
        query_key="search_query",
        max_hops=multi_hop_cfg.get("max_hops", 3),
    )
    nodes["plan_to_search_query"] = PlanToSearchQueryNode(name="plan_to_search_query")

    nodes["dense_search"] = DenseSearchNode(
        name="dense_search",
        vector_store=vector_store,
        embed_model=retrieval_cfg.get("embed_model", "openai:text-embedding-3-small"),
        model_kwargs={"api_key": "[[openai_api_key]]", "dimensions": 512},
        top_k=retrieval_cfg.get("top_k", 4),
        score_threshold=retrieval_cfg.get("score_threshold", 0.0),
        query_key="search_query",
    )
    nodes["source_router"] = SourceRouterNode(
        name="source_router",
        source_result_key=nodes["dense_search"].name,
        min_score=retrieval_cfg.get("score_threshold", 0.0),
    )

    nodes["grounded_generator"] = GroundedGeneratorNode(
        name="grounded_generator",
        context_result_key=nodes["dense_search"].name,
        ai_model="openai:gpt-4o-mini",
        citation_style="inline",
        model_kwargs={"api_key": "[[openai_api_key]]"},
    )

    nodes["citations"] = CitationsFormatterNode(
        name="citations",
        source_result_key=nodes["grounded_generator"].name,
    )

    nodes["hallucination_guard"] = HallucinationGuardNode(
        name="hallucination_guard",
        generator_result_key=nodes["citations"].name,
    )

    nodes["guard_to_policy"] = ResultToInputsNode(
        name="guard_to_policy",
        source_result_key=nodes["hallucination_guard"].name,
        mappings={"content": "reply"},
    )

    nodes["policy_compliance"] = PolicyComplianceNode(
        name="policy_compliance",
        blocked_terms=guardrails,
    )

    nodes["memory_privacy"] = MemoryPrivacyNode(
        name="memory_privacy",
        retention_count=privacy_cfg.get("retention_count"),
    )

    nodes["answer_cache_store"] = AnswerCachingNode(
        name="answer_cache_store",
        cache=shared_cache,
        ttl_seconds=caching_cfg.get("ttl_seconds"),
        max_entries=caching_cfg.get("max_entries", 128),
        source_result_key=nodes["policy_compliance"].name,
        response_field="sanitized",
    )

    nodes["policy_to_stream_inputs"] = ResultToInputsNode(
        name="policy_to_stream_inputs",
        source_result_key=nodes["policy_compliance"].name,
        mappings={
            "stream_prompt": "sanitized",
            "assistant_message": "sanitized",
        },
    )

    streaming = StreamingGeneratorNode(
        name="streaming_generator",
        prompt_key="stream_prompt",
        chunk_size=streaming_cfg.get("chunk_size", 8),
        buffer_limit=streaming_cfg.get("buffer_limit", 64),
        ai_model="openai:gpt-4o-mini",
        model_kwargs={"api_key": "[[openai_api_key]]"},
    )
    nodes["streaming_generator"] = streaming

    nodes["conversation_state_update"] = ConversationStateNode(
        name="conversation_state_update",
        memory_store=memory_store,
        user_message_key="__unused__",
        assistant_message_key="assistant_message",
        max_turns=session_cfg.get("max_turns"),
    )

    nodes["cache_hit_to_inputs"] = ResultToInputsNode(
        name="cache_hit_to_inputs",
        source_result_key=nodes["answer_cache_check"].name,
        mappings={
            "content": "reply",
            "stream_prompt": "reply",
            "assistant_message": "reply",
        },
    )

    return nodes


def assemble_demo_workflow(nodes: dict[str, TaskNode]) -> StateGraph:
    """Wire the provided nodes together into the demo StateGraph."""
    workflow = StateGraph(State)
    for node in nodes.values():
        workflow.add_node(node.name, node)

    workflow.set_entry_point(nodes["session_manager"].name)

    initial_edges = [
        ("session_manager", "conversation_state"),
        ("conversation_state", "conversation_history_sync"),
        ("conversation_history_sync", "answer_cache_check"),
    ]

    for src, dst in initial_edges:
        workflow.add_edge(nodes[src].name, nodes[dst].name)

    cache_switch = SwitchEdge(
        name="cache_routing",
        value=f"{{{{{nodes['answer_cache_check'].name}.cached}}}}",
        case_sensitive=False,
        default_branch_key="miss",
        cases=[SwitchCase(match=True, branch_key="hit")],
    )

    workflow.add_conditional_edges(
        nodes["answer_cache_check"].name,
        cache_switch,
        {
            "hit": nodes["cache_hit_to_inputs"].name,
            "miss": nodes["query_rewrite"].name,
        },
    )

    routing_edges = [
        ("cache_hit_to_inputs", "conversation_state_update"),
        ("query_rewrite", "rewrite_to_search"),
        ("rewrite_to_search", "multi_hop_planner"),
        ("multi_hop_planner", "plan_to_search_query"),
        ("plan_to_search_query", "dense_search"),
        ("dense_search", "source_router"),
        ("source_router", "grounded_generator"),
        ("grounded_generator", "citations"),
        ("citations", "hallucination_guard"),
        ("hallucination_guard", "guard_to_policy"),
        ("guard_to_policy", "policy_compliance"),
        ("policy_compliance", "memory_privacy"),
        ("memory_privacy", "answer_cache_store"),
        ("answer_cache_store", "policy_to_stream_inputs"),
        ("policy_to_stream_inputs", "streaming_generator"),
        ("streaming_generator", "conversation_state_update"),
    ]

    for src, dst in routing_edges:
        workflow.add_edge(nodes[src].name, nodes[dst].name)

    workflow.add_edge(nodes["conversation_state_update"].name, END)

    return workflow


async def orcheo_workflow() -> StateGraph:
    """Assemble the production workflow graph described in the design doc.

    Configuration is loaded from config.json and accessed via template strings
    like {{config.configurable.session.max_turns}}.
    """
    vector_store = PineconeVectorStore(
        index_name="{{config.configurable.retrieval.vector_store.index_name}}",
        namespace="{{config.configurable.retrieval.vector_store.namespace}}",
        client_kwargs={"api_key": "[[pinecone_api_key]]"},
    )
    memory_store = InMemoryMemoryStore(
        max_sessions="{{config.configurable.session.max_sessions}}",
        max_total_turns="{{config.configurable.session.max_total_turns}}",
    )

    shared_cache: OrderedDict[str, tuple[str, float | None]] = OrderedDict()

    retrieval_cfg = {
        "embed_model": "{{config.configurable.retrieval.embed_model}}",
        "top_k": "{{config.configurable.retrieval.top_k}}",
        "score_threshold": "{{config.configurable.retrieval.score_threshold}}",
    }
    session_cfg = {
        "max_turns": "{{config.configurable.session.max_turns}}",
    }
    caching_cfg = {
        "ttl_seconds": "{{config.configurable.caching.ttl_seconds}}",
        "max_entries": "{{config.configurable.caching.max_entries}}",
    }
    multi_hop_cfg = {
        "max_hops": "{{config.configurable.multi_hop.max_hops}}",
    }
    privacy_cfg = {
        "retention_count": "{{config.configurable.memory_privacy.retention_count}}",
    }
    streaming_cfg = {
        "chunk_size": "{{config.configurable.streaming.chunk_size}}",
        "buffer_limit": "{{config.configurable.streaming.buffer_limit}}",
    }

    nodes = build_demo_nodes(
        vector_store=vector_store,
        memory_store=memory_store,
        retrieval_cfg=retrieval_cfg,
        session_cfg=session_cfg,
        caching_cfg=caching_cfg,
        multi_hop_cfg=multi_hop_cfg,
        privacy_cfg=privacy_cfg,
        streaming_cfg=streaming_cfg,
        shared_cache=shared_cache,
        guardrails="{{config.configurable.guardrails.blocked_terms}}",
    )

    return assemble_demo_workflow(nodes)
