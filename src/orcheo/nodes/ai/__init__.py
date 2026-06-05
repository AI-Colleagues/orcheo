"""AI nodes and helpers."""

from __future__ import annotations
from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain.chat_models import init_chat_model
from langchain_mcp_adapters.client import MultiServerMCPClient
from orcheo.nodes.ai.tools.context import (
    get_active_tool_config,
    get_active_tool_progress_callback,
    tool_execution_context,
    tool_progress_context,
)
from orcheo.nodes.ai.tools.registry import tool_registry
from orcheo.nodes.storage import get_graph_store as _get_graph_store_fn
from orcheo.runtime.chat_models import normalize_chat_model_kwargs
from orcheo.tracing.model_metadata import (
    build_ai_trace_metadata,
    infer_chat_result_model_name,
    infer_model_name_from_instance,
)
from .agent import (
    AgentNode,
    AgentReplyExtractorNode,
    LLMNode,
    WorkflowTool,
    _create_workflow_tool_func,
    _llm_trace_metadata,
    _run_tool_graph,
    _select_workflow_tool_output,
    asyncio,
    random,
)
from .agentensor import AgentensorNode
from .deep_agent import DeepAgentNode


__all__ = [
    "AgentNode",
    "AgentReplyExtractorNode",
    "LLMNode",
    "WorkflowTool",
    "MultiServerMCPClient",
    "ProviderStrategy",
    "create_agent",
    "init_chat_model",
    "tool_registry",
    "tool_execution_context",
    "tool_progress_context",
    "get_active_tool_config",
    "get_active_tool_progress_callback",
    "normalize_chat_model_kwargs",
    "infer_chat_result_model_name",
    "infer_model_name_from_instance",
    "_get_graph_store_fn",
    "asyncio",
    "random",
    "AgentensorNode",
    "DeepAgentNode",
    "_create_workflow_tool_func",
    "build_ai_trace_metadata",
    "_llm_trace_metadata",
    "_run_tool_graph",
    "_select_workflow_tool_output",
]
