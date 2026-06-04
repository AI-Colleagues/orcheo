"""Deep research agent node for autonomous multi-step research workflows."""

from __future__ import annotations
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
from deepagents import create_deep_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain.chat_models import init_chat_model
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import ConfigDict, Field
from orcheo.graph.state import State
from orcheo.nodes.ai import WorkflowTool, _create_workflow_tool_func
from orcheo.nodes.ai.tools.context import tool_execution_context
from orcheo.nodes.ai.tools.registry import tool_registry
from orcheo.nodes.base import AINode
from orcheo.nodes.registry import NodeMetadata, registry
from orcheo.runtime.chat_models import normalize_chat_model_kwargs
from orcheo.tracing.model_metadata import (
    build_ai_trace_metadata,
    infer_chat_result_model_name,
    infer_model_name_from_instance,
)


logger = logging.getLogger(__name__)


@registry.register(
    NodeMetadata(
        name="DeepAgentNode",
        description=(
            "Execute an autonomous deep-research agent with configurable "
            "iteration depth for multi-step tool use and synthesis"
        ),
        category="ai",
    )
)
class DeepAgentNode(AINode):
    """Node for executing a deep-research agent with multi-step iteration.

    Wraps ``create_deep_agent`` from LangChain's ``deepagents`` package,
    which provides built-in planning tools, a virtual file-system backend,
    sub-agent spawning, and context summarisation middleware.

    Unlike :class:`AgentNode` which includes chat history management and
    multi-turn conversation tracking, ``DeepAgentNode`` is focused on
    single-invocation deep research: the agent receives a query, plans
    its approach, iterates over tools up to ``max_iterations`` times, and
    returns a synthesised result.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    ai_model: str
    """Identifier of the AI chat model to use (e.g. ``"openai:gpt-4o"``)."""
    system_prompt: str | None = None
    """Custom system instructions prepended to the deep-agent base prompt."""
    research_prompt: str | None = None
    """Research-specific instructions appended to ``system_prompt``."""
    predefined_tools: list[str] = Field(default_factory=list)
    """Tool names predefined by Orcheo."""
    workflow_tools: list[WorkflowTool] = Field(default_factory=list)
    """Workflows to be used as tools."""
    mcp_servers: dict[str, Any] = Field(default_factory=dict)
    """MCP servers to be used as tools."""
    max_iterations: int = Field(
        default=100,
        description="Maximum number of agent iterations (recursion limit).",
    )
    """Maximum number of agent iterations (recursion limit)."""
    model_kwargs: dict[str, Any] = Field(default_factory=dict)
    """Additional keyword arguments passed to ``init_chat_model``."""
    response_format: dict | None = None
    """Structured output response format for the agent."""
    input_query: str | None = None
    """Direct text query for the agent. Supports variable interpolation."""
    skills: list[str] | None = None
    """Skill source paths loaded by the deep-agent middleware."""
    memory: list[str] | None = None
    """Memory file paths (``AGENTS.md``) loaded at agent startup."""
    debug: bool = False
    """Enable deep-agent debug mode."""

    def _build_system_prompt(self) -> str | None:
        """Build the combined system prompt from base and research prompts.

        Returns:
            Combined prompt string or None if both are empty.
        """
        parts: list[str] = []
        if self.system_prompt:
            parts.append(self.system_prompt.strip())
        if self.research_prompt:
            parts.append(self.research_prompt.strip())
        return "\n\n".join(parts) if parts else None

    async def _prepare_tools(self) -> list[BaseTool]:
        """Prepare the tools for the agent.

        Resolves predefined tools from the registry, compiles workflow tools,
        and loads MCP server tools.

        Returns:
            List of all resolved tools.
        """
        tools: list[BaseTool] = []

        for tool_name in self.predefined_tools:
            tool = tool_registry.get_tool(tool_name)
            if tool is None:
                logger.warning("Tool '%s' not found in registry, skipping", tool_name)
                continue
            if isinstance(tool, BaseTool):
                tools.append(tool)
            elif callable(tool):
                try:
                    tool_instance = tool()
                    if not isinstance(tool_instance, BaseTool):
                        logger.error(
                            "Tool factory '%s' did not return a BaseTool, got %s",
                            tool_name,
                            type(tool_instance).__name__,
                        )
                        continue
                    tools.append(tool_instance)
                except Exception as exc:
                    logger.error(
                        "Failed to instantiate tool '%s': %s", tool_name, str(exc)
                    )
                    continue
            else:
                logger.error(
                    "Tool '%s' is not a BaseTool or callable, got %s",
                    tool_name,
                    type(tool).__name__,
                )
                continue

        for wf_tool_def in self.workflow_tools:
            compiled_graph = wf_tool_def.get_compiled_graph()
            tool = _create_workflow_tool_func(
                compiled_graph=compiled_graph,
                name=wf_tool_def.name,
                description=wf_tool_def.description,
                args_schema=wf_tool_def.args_schema,
                output_path=wf_tool_def.output_path,
            )
            tools.append(tool)

        mcp_client = MultiServerMCPClient(connections=self.mcp_servers)
        mcp_tools = await mcp_client.get_tools()
        tools.extend(mcp_tools)

        return tools

    def _build_messages(self, state: State) -> list[BaseMessage]:
        """Build the message list for the agent invocation.

        Uses ``input_query`` if set, otherwise falls back to extracting
        the message from workflow inputs.

        Returns:
            List of messages to send to the agent.
        """
        if self.input_query:
            return [HumanMessage(content=self.input_query)]

        inputs = state.get("inputs", {})
        if isinstance(inputs, dict):
            for key in ("query", "message", "prompt", "input"):
                value = inputs.get(key)
                if isinstance(value, str) and value.strip():
                    return [HumanMessage(content=value.strip())]

        return []

    def _resolve_skills(self) -> list[str] | None:
        """Resolve skill paths for the agent.

        When ``self.skills`` is ``None``, automatically discovers all
        installed skills from the Orcheo skills directory.  When set
        explicitly, returns the configured list unchanged.

        Returns:
            List of skill directory paths, or ``None`` if no skills
            are available.
        """
        if self.skills is not None:
            return self.skills

        try:
            override = os.getenv("ORCHEO_SKILLS_DIR")
            skills_dir = (
                Path(override).expanduser()
                if override
                else (Path.home() / ".orcheo" / "skills")
            )
            if not skills_dir.is_dir():
                return None
            paths = sorted(
                str(child)
                for child in skills_dir.iterdir()
                if child.is_dir() and (child / "SKILL.md").exists()
            )
        except Exception:
            logger.warning("Failed to discover installed skills", exc_info=True)
            return None

        return paths if paths else None

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:  # noqa: C901, PLR0912
        """Execute the deep research agent and return results.

        Creates a deep agent via ``create_deep_agent`` from the
        ``deepagents`` package, which includes built-in planning,
        file-system, sub-agent, and summarisation middleware.

        Args:
            state: Current workflow state.
            config: Runnable configuration.

        Returns:
            Agent execution result with messages.
        """
        self._clear_trace_metadata_for_run()
        tools = await self._prepare_tools()

        response_format_strategy: ProviderStrategy | None = None
        if self.response_format is not None:
            response_format_strategy = ProviderStrategy(self.response_format)  # type: ignore[arg-type]

        model_kwargs = normalize_chat_model_kwargs(self.ai_model, self.model_kwargs)
        if model_kwargs:
            model = init_chat_model(
                self.ai_model,
                **model_kwargs,
            )
        else:
            model = self.ai_model  # type: ignore[assignment]

        combined_prompt = self._build_system_prompt()
        resolved_skills = self._resolve_skills()
        agent = create_deep_agent(
            model,
            tools=tools,
            system_prompt=combined_prompt,
            response_format=response_format_strategy,
            skills=resolved_skills,
            memory=self.memory,
            debug=self.debug,
        )

        messages = self._build_messages(state)
        payload: dict[str, Any] = {"messages": messages}
        runtime_config: dict[str, Any] = {}
        if isinstance(config, Mapping):
            runtime_config.update(config)

        configurable_value = runtime_config.get("configurable")
        configurable = (
            dict(configurable_value) if isinstance(configurable_value, Mapping) else {}
        )
        workspace_id = state.get("workspace_id") if isinstance(state, dict) else None
        if isinstance(workspace_id, str) and workspace_id.strip():
            configurable.setdefault("workspace_id", workspace_id.strip())
        if isinstance(state, Mapping):
            inputs_value = state.get("inputs")
            if isinstance(inputs_value, Mapping):
                configurable.setdefault("inputs", dict(inputs_value))
        thread_state_payload: dict[str, Any] | None = None
        if isinstance(state, Mapping):
            thread_state = state.get("thread_state")
            if isinstance(thread_state, Mapping):
                thread_state_payload = dict(thread_state)
            else:
                results = state.get("results")
                if isinstance(results, Mapping):
                    maybe_thread_state = results.get("_thread_state")
                    if isinstance(maybe_thread_state, Mapping):
                        thread_state_payload = dict(maybe_thread_state)
        if thread_state_payload is not None:
            configurable.setdefault("thread_state", thread_state_payload)
        runtime_config["configurable"] = configurable

        runnable_config = cast(RunnableConfig, runtime_config)
        with tool_execution_context(runnable_config):
            result = await agent.ainvoke(
                payload,  # type: ignore[arg-type]
                config={
                    **runnable_config,
                    "recursion_limit": self.max_iterations,
                },
            )
        if isinstance(result, dict):  # pragma: no branch
            actual_model = infer_chat_result_model_name(result)
            if actual_model is None and not isinstance(model, str):
                actual_model = infer_model_name_from_instance(model)
            self._set_trace_metadata_for_run(
                {
                    "ai": build_ai_trace_metadata(
                        kind="llm",
                        requested_model=self.ai_model,
                        actual_model=actual_model,
                    )
                }
            )

        return result


__all__ = ["DeepAgentNode"]
