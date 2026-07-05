"""AI Agent node."""

from __future__ import annotations
import asyncio
import logging
import random
import re
import sys
from collections.abc import Mapping
from typing import Any, ClassVar, cast
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph import StateGraph
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator
from pydantic.json_schema import SkipJsonSchema
from agentensor.tensor import TextTensor
from orcheo.graph.state import State
from orcheo.nodes.base import AINode, TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


logger = logging.getLogger(__name__)

_STATE_CONFIG_EXCLUDED_KEYS = {
    "attachment_resolver",
    "attachment_scope",
    "attachment_uploader",
}


def _ai_package() -> Any:
    """Return the public AI package module for monkeypatch-aware lookups."""
    module = sys.modules.get("orcheo.nodes.ai")
    if module is None:
        return sys.modules[__name__]
    return module


def _ai_attr(name: str) -> Any:
    """Look up an attribute on the public AI package module."""
    return getattr(_ai_package(), name)


def _llm_trace_metadata(
    requested_model: str,
    *,
    model: Any | None = None,
    result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build normalized trace metadata for a chat-model invocation."""
    actual_model = None
    if result is not None:
        actual_model = _ai_attr("infer_chat_result_model_name")(result)
    if actual_model is None and model is not None:
        actual_model = _ai_attr("infer_model_name_from_instance")(model)
    return {
        "ai": _ai_attr("build_ai_trace_metadata")(
            kind="llm",
            requested_model=requested_model,
            actual_model=actual_model,
        )
    }


def _tool_graph_payload_with_runtime_config(
    payload: dict[str, Any],
    config: RunnableConfig | None,
) -> dict[str, Any]:
    """Attach serializable runnable config data to a tool-graph payload."""
    if config is None:
        return payload

    configurable = {
        k: v
        for k, v in (config.get("configurable") or {}).items()
        if not k.startswith("__") and k not in _STATE_CONFIG_EXCLUDED_KEYS
    }
    runtime_payload = {**payload, "config": {"configurable": configurable}}

    workspace_id = configurable.get("workspace_id")
    if isinstance(workspace_id, str):
        workspace_id = workspace_id.strip()
        if workspace_id:
            runtime_payload = {**runtime_payload, "workspace_id": workspace_id}

    thread_state = configurable.get("thread_state")
    if isinstance(thread_state, Mapping):
        runtime_payload = {
            **runtime_payload,
            "thread_state": dict(thread_state),
        }

    return runtime_payload


async def _ainvoke_tool_graph(
    compiled_graph: Runnable,
    payload: dict[str, Any],
    config: RunnableConfig | None,
) -> Any:
    """Invoke a tool graph with or without an explicit runnable config."""
    if config is None:
        return await compiled_graph.ainvoke(payload)
    return await compiled_graph.ainvoke(payload, config=config)


async def _stream_tool_graph_updates(
    compiled_graph: Runnable,
    payload: dict[str, Any],
    config: RunnableConfig,
    progress_callback: Any,
) -> Any:
    """Stream tool graph updates and return the final values event."""
    last_values: Any | None = None
    stream_kwargs: dict[str, Any] = {
        "config": config,
        "stream_mode": ["updates", "values"],
    }
    output_keys = getattr(compiled_graph, "output_channels", None)
    if output_keys is not None:  # pragma: no branch
        stream_kwargs["output_keys"] = output_keys

    async for event in compiled_graph.astream(  # type: ignore[arg-type]
        payload,
        **stream_kwargs,
    ):
        mode = "updates"
        data = event
        if isinstance(event, tuple) and len(event) == 2:
            mode, data = event
        if mode == "updates":
            await progress_callback(data)
        elif mode == "values":  # pragma: no branch
            last_values = data

    if last_values is not None:
        return last_values

    msg = "Tool graph streaming did not produce final values."
    raise RuntimeError(msg)


async def _run_tool_graph(
    compiled_graph: Runnable,
    payload: dict[str, Any],
) -> Any:
    """Execute a compiled graph, streaming updates when configured."""
    config = _ai_attr("get_active_tool_config")()
    progress_callback = _ai_attr("get_active_tool_progress_callback")()
    payload = _tool_graph_payload_with_runtime_config(payload, config)

    if progress_callback is None or config is None:
        return await _ainvoke_tool_graph(compiled_graph, payload, config)

    return await _stream_tool_graph_updates(
        compiled_graph,
        payload,
        config,
        progress_callback,
    )


def _create_workflow_tool_func(
    compiled_graph: Runnable,
    name: str,
    description: str,
    args_schema: type[BaseModel] | dict[str, Any] | None,
    output_path: str | None = None,
    return_direct: bool = False,
) -> StructuredTool:
    """Create a StructuredTool from a compiled workflow graph.

    This factory function properly binds the compiled_graph to avoid
    closure issues in loops.

    Args:
        compiled_graph: Compiled LangGraph runnable
        name: Tool name
        description: Tool description
        args_schema: Optional Pydantic model or JSON schema for tool arguments
        output_path: Optional dotted path selecting the final tool payload
        return_direct: When True, end the agent loop after this tool runs and
            return its output to the user verbatim

    Returns:
        StructuredTool instance wrapping the workflow
    """

    def select_output(result: Any) -> Any:
        if output_path is None:
            return result
        return _select_workflow_tool_output(result, output_path, name)

    async def workflow_coroutine(**kwargs: Any) -> Any:
        """Execute the workflow graph asynchronously."""
        payload = {"inputs": kwargs, "node_results": {}, "messages": []}
        result = await _run_tool_graph(compiled_graph, payload)
        return select_output(result)

    def workflow_sync(**kwargs: Any) -> Any:
        """Execute the workflow graph synchronously."""
        payload = {"inputs": kwargs, "node_results": {}, "messages": []}
        result = asyncio.run(_run_tool_graph(compiled_graph, payload))
        return select_output(result)

    return StructuredTool.from_function(
        func=workflow_sync,
        coroutine=workflow_coroutine,
        name=name,
        description=description,
        args_schema=args_schema,
        return_direct=return_direct,
    )


def _select_workflow_tool_output(
    result: Any,
    output_path: str,
    tool_name: str,
) -> Any:
    """Select a nested output value from a workflow-tool result."""
    current = result
    for segment in output_path.split("."):
        if isinstance(current, Mapping):
            if segment not in current:
                msg = (
                    f"Workflow tool '{tool_name}' output_path '{output_path}' "
                    f"could not resolve segment '{segment}'."
                )
                raise ValueError(msg)
            current = current[segment]
            continue
        if isinstance(current, list):
            try:
                index = int(segment)
            except ValueError as exc:
                msg = (
                    f"Workflow tool '{tool_name}' output_path '{output_path}' "
                    "encountered a list and requires an integer segment."
                )
                raise ValueError(msg) from exc
            try:
                current = current[index]
            except IndexError as exc:
                msg = (
                    f"Workflow tool '{tool_name}' output_path '{output_path}' "
                    f"index {index} is out of range."
                )
                raise ValueError(msg) from exc
            continue
        msg = (
            f"Workflow tool '{tool_name}' output_path '{output_path}' "
            f"cannot descend into {type(current).__name__}."
        )
        raise ValueError(msg)
    return current


class WorkflowTool(BaseModel):
    """Workflow tool."""

    model_config = {"arbitrary_types_allowed": True}

    name: str
    """Name of the tool."""
    description: str
    """Description of the tool."""
    graph: SkipJsonSchema[StateGraph]
    """Workflow to be used as tool."""
    args_schema: type[BaseModel] | dict[str, Any] | None = None
    """Input schema for the tool."""
    output_path: str | None = None
    """Optional dotted path selecting the value returned to the caller."""
    return_direct: bool = False
    """When True, the tool's output is returned to the user verbatim and the
    agent loop ends after the tool runs, instead of letting the model compose
    (and potentially paraphrase) a follow-up reply. Use for tools whose output
    *is* the user-facing deliverable. ``AgentReplyExtractorNode`` surfaces the
    trailing tool message produced in this case."""
    _compiled_graph: SkipJsonSchema[Runnable | None] = None
    """Cached compiled graph to avoid recompilation."""

    @field_validator("output_path")
    @classmethod
    def _validate_output_path(cls, value: str | None) -> str | None:
        if value is None:
            return None  # pragma: no cover - optional field
        normalized = value.strip()
        if not normalized:
            msg = "output_path must not be empty"
            raise ValueError(msg)
        return normalized

    def get_compiled_graph(self) -> Runnable:
        """Get or compile the graph, caching the result.

        Returns:
            Compiled graph runnable
        """
        if self._compiled_graph is None:
            self._compiled_graph = self.graph.compile()
        return self._compiled_graph


@registry.register(
    NodeMetadata(
        name="AgentNode",
        description="Execute an AI agent with tools",
        category="ai",
    )
)
class AgentNode(AINode):
    """Node for executing an AI agent with tools."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    _history_key_pattern: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9:_-]+$")
    _history_key_max_length: ClassVar[int] = 256
    _history_write_retry_limit: ClassVar[int] = 3
    _history_retry_base_backoff_seconds: ClassVar[float] = 0.025

    ai_model: str
    """Identifier of the AI chat model to use."""
    model_settings: dict | None = None
    """TODO: Implement model settings for the agent."""
    model_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional keyword arguments passed to init_chat_model.",
    )
    """Additional keyword arguments passed to init_chat_model."""
    system_prompt: str | TextTensor | None = None
    """System prompt for the agent."""
    predefined_tools: list[str] = Field(default_factory=list)
    """Tool names predefined by Orcheo."""
    workflow_tools: list[WorkflowTool] = Field(default_factory=list)
    """Workflows to be used as tools."""
    use_chatkit_widget_tools: bool = False
    """Enable local ChatKit widget definitions as tools."""
    chatkit_widgets_dir: str | None = "/app/examples/chatkit_widgets/widgets"
    """Curated directory containing .widget files to project as tools."""
    mcp_servers: dict[str, Any] = Field(default_factory=dict)
    """MCP servers to be used as tools (Connection from langchain_mcp_adapters)."""
    response_format: dict | type[BaseModel] | None = None
    """Response format for the agent."""
    max_messages: int = 30
    """Maximum number of messages to keep when sending to the agent."""
    reset_command: str = ""
    """Command that resets history. Messages before the latest reset are ignored."""
    use_graph_chat_history: bool = False
    """Enable graph-store-backed chat history loading and persistence."""
    history_namespace: list[str] = Field(default_factory=lambda: ["agent_chat_history"])
    """Namespace used for graph-store chat history items."""
    history_key_template: str = "{{conversation_key}}"
    """Template used to derive the final graph-store key."""
    history_key_candidates: list[str] = Field(
        default_factory=lambda: [
            "{{inputs.platform}}:{{inputs.message.chat_id}}",
            "{{inputs.listener.platform}}:{{inputs.listener.message.chat_id}}",
            "telegram:{{node_results.telegram_events_parser.chat_id}}",
            "wecom_cs:{{node_results.wecom_cs_sync.open_kf_id}}:{{node_results.wecom_cs_sync.external_userid}}",
            "wecom_aibot:{{node_results.wecom_ai_bot_events_parser.chat_type}}:{{node_results.wecom_ai_bot_events_parser.user}}",
            "wecom_dm:{{node_results.wecom_events_parser.user}}",
        ]
    )
    """Ordered key candidates used to resolve stable conversation identity."""
    history_value_field: str = "content"
    """Field name used when persisting text content into store records."""

    _DEFERRED_DECODE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"history_key_template", "history_key_candidates"}
    )
    """Fields resolved by ``_resolve_history_key``, skipped in general decode."""

    def _compute_run_updates(self, state: State) -> dict[str, Any]:
        """Skip deferred fields that are resolved by _resolve_history_key."""
        updates: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            if key in self._DEFERRED_DECODE_FIELDS:
                continue
            decoded = self._decode_value(value, state)
            if decoded is not value:
                updates[key] = decoded
        return updates

    def decode_variables(
        self,
        state: Any,
        *,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        """Decode variables, skipping fields handled by _resolve_history_key."""
        for key, value in self.__dict__.items():
            if key in self._DEFERRED_DECODE_FIELDS:
                continue
            self.__dict__[key] = self._decode_value(value, state)
        self.__dict__.update(self._runtime_run_updates(config))

    @field_serializer("system_prompt", when_used="json")
    def _serialize_system_prompt(self, value: str | TextTensor | None) -> str | None:
        """Normalize TextTensor prompts into JSON-safe strings."""
        if isinstance(value, TextTensor):
            return value.text
        return value

    def model_post_init(self, __context: Any) -> None:
        """Normalize system prompts into TextTensor for optimization."""
        if isinstance(self.system_prompt, str):
            if self.contains_template(self.system_prompt):
                return
            if (
                isinstance(self.ai_model, str)
                and ":" not in self.ai_model
                and "model_provider" not in self.model_kwargs
            ):
                return
            self.system_prompt = TextTensor(
                self.system_prompt,
                model=self.ai_model,
                model_kwargs=dict(self.model_kwargs),
            )

    def get_params(self) -> list[TextTensor]:
        """Return trainable parameters for optimizer discovery."""
        if (
            isinstance(self.system_prompt, TextTensor)
            and self.system_prompt.requires_grad
        ):
            return [self.system_prompt]
        return []

    async def _prepare_tools(self) -> list[BaseTool]:
        """Prepare the tools for the agent."""
        tools: list[BaseTool] = []

        # Resolve predefined tools from the tool registry
        for tool_name in self.predefined_tools:
            tool = _ai_attr("tool_registry").get_tool(tool_name)
            if tool is None:
                logger.warning("Tool '%s' not found in registry, skipping", tool_name)
                continue

            # If it's already a BaseTool instance (e.g., from @tool
            # decorator), use it directly
            if isinstance(tool, BaseTool):
                tools.append(tool)
            # Otherwise, check if it's a callable factory
            elif callable(tool):
                try:
                    tool_instance = tool()
                    if not isinstance(tool_instance, BaseTool):
                        logger.error(
                            "Tool factory '%s' did not return a BaseTool instance, "
                            "got %s",
                            tool_name,
                            type(tool_instance).__name__,
                        )
                        continue
                    tools.append(tool_instance)
                except Exception as e:
                    logger.error(
                        "Failed to instantiate tool '%s': %s", tool_name, str(e)
                    )
                    continue
            else:
                logger.error(
                    "Tool '%s' is neither a BaseTool instance nor a callable factory, "
                    "got %s",
                    tool_name,
                    type(tool).__name__,
                )
                continue

        for wf_tool_def in self.workflow_tools:
            # Use cached compiled graph to avoid recompilation on every run
            compiled_graph = wf_tool_def.get_compiled_graph()

            # Create tool using factory function to properly bind variables
            # and avoid closure memory leak issues
            tool = _create_workflow_tool_func(
                compiled_graph=compiled_graph,
                name=wf_tool_def.name,
                description=wf_tool_def.description,
                args_schema=wf_tool_def.args_schema,
                output_path=wf_tool_def.output_path,
                return_direct=wf_tool_def.return_direct,
            )
            tools.append(tool)

        if self.use_chatkit_widget_tools:
            tools.extend(
                _ai_attr("build_chatkit_widget_tools")(self.chatkit_widgets_dir)
            )

        # Get MCP tools
        mcp_client = _ai_attr("MultiServerMCPClient")(connections=self.mcp_servers)
        mcp_tools = await mcp_client.get_tools()
        tools.extend(mcp_tools)

        return tools

    def _messages_from_inputs(self, inputs: Mapping[str, Any]) -> list[BaseMessage]:
        """Build LangChain messages from ChatKit-style inputs."""
        history = inputs.get("history")
        messages: list[BaseMessage] = []

        if isinstance(history, list):
            for turn in history:
                if not isinstance(turn, Mapping):
                    continue
                content = turn.get("content")
                role = turn.get("role")
                if not isinstance(content, str) or not content.strip():
                    continue
                if role == "assistant":
                    messages.append(AIMessage(content=content))
                elif role == "user":  # pragma: no branch
                    messages.append(HumanMessage(content=content))

        message_value = self._input_message_text(inputs)
        if message_value is not None and not self._is_duplicate_latest_user_turn(
            messages, message_value
        ):
            messages.append(HumanMessage(content=message_value))

        return messages

    def _input_message_text(self, inputs: Mapping[str, Any]) -> str | None:
        """Extract the current user turn from chat or listener-style inputs."""
        for key in ("message", "user_message", "query", "prompt"):
            text = self._coerce_input_message_text(inputs.get(key))
            if text is not None:
                return text

        listener = inputs.get("listener")
        if isinstance(listener, Mapping):
            return self._coerce_input_message_text(listener.get("message"))
        return None

    @staticmethod
    def _coerce_input_message_text(value: Any) -> str | None:
        """Normalize string or structured input payloads into message text."""
        if isinstance(value, str):
            candidate = value.strip()
            return candidate or None
        if isinstance(value, Mapping):
            content = value.get("text")
            if not isinstance(content, str):
                content = value.get("content")
            if isinstance(content, str):  # pragma: no branch
                candidate = content.strip()
                return candidate or None
        return None

    @staticmethod
    def _is_duplicate_latest_user_turn(
        messages: list[BaseMessage],
        candidate: str,
    ) -> bool:
        if not messages:
            return False
        latest_message = messages[-1]
        if not isinstance(latest_message, HumanMessage):
            return False
        latest_content = latest_message.content
        latest_text = (
            latest_content if isinstance(latest_content, str) else str(latest_content)
        )
        return latest_text.strip() == candidate

    def _normalize_messages(self, messages: Any) -> list[BaseMessage]:
        """Normalize caller-provided messages into LangChain BaseMessages."""
        normalized: list[BaseMessage] = []
        if not isinstance(messages, list):
            return normalized

        for message in messages:
            if isinstance(message, BaseMessage):
                normalized.append(message)
                continue
            if not isinstance(message, Mapping):
                continue
            content = message.get("content")
            role = message.get("role")
            if not isinstance(content, str) or not content.strip():
                continue
            if role == "assistant":
                normalized.append(AIMessage(content=content))
            else:
                normalized.append(HumanMessage(content=content))

        return normalized

    def _apply_reset_command(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """Trim messages to start from the latest reset command (inclusive)."""
        if not self.reset_command:
            return messages
        for i in range(len(messages) - 1, -1, -1):
            content = messages[i].content
            if isinstance(content, str) and content.strip() == self.reset_command:
                return messages[i:]
        return messages

    def _check_has_checkpointer(self, config: RunnableConfig | None) -> bool:
        """Return True when the LangGraph checkpointer is managing state."""
        configurable = (
            config.get("configurable", {}) if isinstance(config, Mapping) else {}
        )
        return (
            isinstance(configurable, Mapping)
            and "thread_id" in configurable
            and "__pregel_checkpointer" in configurable
        )

    def _build_messages(
        self,
        state: State,
        config: RunnableConfig | None = None,
    ) -> list[BaseMessage]:
        """Construct the message list for the agent invocation."""
        existing_messages = self._normalize_messages(state.get("messages"))
        if existing_messages:
            messages = self._apply_reset_command(existing_messages)
            return self._trim_messages(messages)

        inputs = state.get("inputs", {}) if isinstance(state, Mapping) else {}
        messages = self._messages_from_inputs(inputs)
        messages = self._apply_reset_command(messages)
        return self._trim_messages(messages)

    def _trim_messages(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """Trim history to ``max_messages`` without splitting tool round-trips.

        A plain tail slice can keep a ``ToolMessage`` whose parent ``AIMessage``
        (carrying the matching ``tool_calls``) was dropped, or keep a trailing
        ``AIMessage`` whose ``tool_calls`` have no following ``ToolMessage``.
        OpenAI rejects both shapes, so drop the orphans at each end and leave the
        window starting and ending on a valid boundary.
        """
        trimmed = messages[-self.max_messages :]
        # Drop leading tool results whose parent AIMessage was trimmed away.
        while trimmed and isinstance(trimmed[0], ToolMessage):
            trimmed.pop(0)
        # Drop a trailing assistant turn whose tool calls have no results yet.
        while (
            trimmed
            and isinstance(trimmed[-1], AIMessage)
            and getattr(trimmed[-1], "tool_calls", None)
        ):
            trimmed.pop()
        return trimmed

    def _get_graph_store(self, config: RunnableConfig | None) -> Any | None:
        """Return the runtime graph store when available."""
        return _ai_attr("_get_graph_store_fn")(config)

    def _history_namespace_tuple(self) -> tuple[str, ...]:
        namespace = tuple(
            entry.strip()
            for entry in self.history_namespace
            if isinstance(entry, str) and entry.strip()
        )
        return namespace or ("agent_chat_history",)

    def _validate_history_key(self, key: str) -> tuple[str | None, str]:
        """Validate a candidate history key and return failure reason when invalid."""
        candidate = key.strip()
        if not candidate:
            return None, "empty"
        if self.contains_template_delimiter(candidate):
            return None, "unresolved_template"
        if len(candidate) > self._history_key_max_length:
            return None, "too_long"
        if self._history_key_pattern.fullmatch(candidate) is None:
            return None, "invalid_chars"
        return candidate, "ok"

    def _resolve_history_key(
        self,
        state: State,
        config: RunnableConfig | None,
    ) -> str | None:
        """Resolve and validate the final graph-store history key."""
        del config
        conversation_key: str | None = None

        for candidate in self.history_key_candidates:
            if not isinstance(candidate, str):
                continue
            resolved = self._decode_string_value(candidate, state)
            rendered = (
                str(resolved).strip()
                if isinstance(resolved, str | int | float | bool)
                else candidate
            )
            valid_key, status = self._validate_history_key(rendered)
            if valid_key is not None:
                conversation_key = valid_key
                break
            if rendered:
                logger.debug(
                    "AgentNode '%s' rejected history key candidate '%s' (%s).",
                    self.name,
                    rendered,
                    status,
                )

        if conversation_key is None:
            logger.warning(
                "AgentNode '%s' skipped graph history: no valid conversation key "
                "resolved.",
                self.name,
            )
            return None

        history_template_state = cast(
            State,
            {
                **(dict(state) if isinstance(state, Mapping) else {}),
                "conversation_key": conversation_key,
            },
        )
        rendered_key_value = self._decode_string_value(
            self.history_key_template,
            history_template_state,
        )
        rendered_key = (
            str(rendered_key_value).strip()
            if isinstance(rendered_key_value, str)
            else self.history_key_template.strip()
        )
        final_key, status = self._validate_history_key(rendered_key)
        if final_key is None:
            logger.warning(
                "AgentNode '%s' skipped graph history: resolved key '%s' is invalid "
                "(%s).",
                self.name,
                rendered_key,
                status,
            )
            return None

        return final_key

    async def _store_get_item(
        self,
        store: Any,
        namespace: tuple[str, ...],
        key: str,
    ) -> Any | None:
        """Read a single store item using async/sync API variants."""
        aget = getattr(store, "aget", None)
        if callable(aget):
            return await aget(namespace, key)

        get = getattr(store, "get", None)
        if callable(get):
            result = get(namespace, key)
            if asyncio.iscoroutine(result):
                return await result
            return result
        return None

    async def _store_put_item(
        self,
        store: Any,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, Any],
    ) -> None:
        """Write a single store item using async/sync API variants."""
        aput = getattr(store, "aput", None)
        if callable(aput):
            await aput(namespace, key, value)
            return

        put = getattr(store, "put", None)
        if callable(put):
            result = put(namespace, key, value)
            if asyncio.iscoroutine(result):
                await result

    def _history_payload_from_item(self, item: Any | None) -> Mapping[str, Any]:
        """Extract history payload from a LangGraph item-like response."""
        if item is None:
            return {}
        if isinstance(item, Mapping):
            payload = item.get("value")
            return payload if isinstance(payload, Mapping) else {}
        value = getattr(item, "value", None)
        return value if isinstance(value, Mapping) else {}

    def _normalize_history_store_messages(self, payload: Any) -> list[BaseMessage]:
        """Normalize persisted history payload into LangChain messages."""
        normalized: list[BaseMessage] = []
        if not isinstance(payload, list):
            return normalized

        for item in payload:
            if not isinstance(item, Mapping):
                continue
            role = item.get("role")
            content = item.get(self.history_value_field, item.get("content"))
            if not isinstance(content, str) or not content.strip():
                continue
            if role == "assistant":
                normalized.append(AIMessage(content=content))
            elif role == "user":  # pragma: no branch
                normalized.append(HumanMessage(content=content))
        return normalized

    def _serialize_history_messages(
        self, messages: list[BaseMessage]
    ) -> list[dict[str, str]]:
        """Serialize user/assistant messages for graph-store persistence."""
        serialized: list[dict[str, str]] = []
        for message in messages:
            content = message.content
            text = content if isinstance(content, str) else str(content)
            if not text.strip():
                continue
            if isinstance(message, AIMessage):
                role = "assistant"
            elif isinstance(message, HumanMessage):
                role = "user"
            else:
                continue
            payload = {"role": role, self.history_value_field: text}
            if self.history_value_field != "content":
                payload["content"] = text
            serialized.append(payload)
        return serialized

    def _filter_user_assistant_messages(
        self,
        messages: list[BaseMessage],
    ) -> list[BaseMessage]:
        return [
            message
            for message in messages
            if isinstance(message, HumanMessage | AIMessage)
        ]

    def _message_signature(self, message: BaseMessage) -> tuple[str, str]:
        """Create a deterministic identity for message overlap detection."""
        content = message.content
        text = content if isinstance(content, str) else str(content)
        if isinstance(message, AIMessage):
            return "assistant", text
        if isinstance(message, HumanMessage):
            return "user", text
        return message.type, text

    def _starts_with_messages(
        self,
        messages: list[BaseMessage],
        prefix: list[BaseMessage],
    ) -> bool:
        if len(prefix) > len(messages):
            return False
        return all(
            self._message_signature(messages[index])
            == self._message_signature(prefix[index])
            for index in range(len(prefix))
        )

    def _suffix_prefix_overlap(
        self,
        existing: list[BaseMessage],
        observed: list[BaseMessage],
    ) -> int:
        """Return overlap size between existing suffix and observed prefix."""
        max_overlap = min(len(existing), len(observed))
        for overlap in range(max_overlap, 0, -1):
            existing_slice = existing[-overlap:]
            observed_slice = observed[:overlap]
            if all(
                self._message_signature(existing_slice[index])
                == self._message_signature(observed_slice[index])
                for index in range(overlap)
            ):
                return overlap
        return 0

    def _history_version_from_payload(self, payload: Mapping[str, Any]) -> int:
        raw_version = payload.get("version", 0)
        if isinstance(raw_version, int) and raw_version >= 0:
            return raw_version
        if isinstance(raw_version, str) and raw_version.isdigit():
            return int(raw_version)
        return 0

    def _extract_observed_messages(
        self,
        current_messages: list[BaseMessage],
        inference_messages: list[BaseMessage],
        result: Mapping[str, Any],
    ) -> list[BaseMessage]:
        """Collect user/assistant turns observed during this run."""
        current_turns = self._filter_user_assistant_messages(current_messages)
        result_messages = self._filter_user_assistant_messages(
            self._normalize_messages(result.get("messages"))
        )

        delta = result_messages
        if self._starts_with_messages(result_messages, inference_messages):
            delta = result_messages[len(inference_messages) :]
        elif self._starts_with_messages(result_messages, current_turns):
            delta = result_messages[len(current_turns) :]

        return current_turns + delta

    async def _load_graph_history_messages(
        self,
        *,
        store: Any,
        namespace: tuple[str, ...],
        key: str,
    ) -> list[BaseMessage]:
        """Read full persisted history for the given namespace/key."""
        item = await self._store_get_item(store, namespace, key)
        payload = self._history_payload_from_item(item)
        return self._normalize_history_store_messages(payload.get("messages"))

    async def _persist_graph_history(
        self,
        *,
        store: Any,
        namespace: tuple[str, ...],
        key: str,
        observed_messages: list[BaseMessage],
    ) -> None:
        """Append observed turns to store history using bounded retry."""
        observed = self._filter_user_assistant_messages(observed_messages)
        if not observed:
            return

        for attempt in range(self._history_write_retry_limit):
            try:
                existing_item = await self._store_get_item(store, namespace, key)
            except Exception:
                logger.warning(
                    "AgentNode '%s' failed to read graph history before write "
                    "(key='%s').",
                    self.name,
                    key,
                )
                return

            existing_payload = self._history_payload_from_item(existing_item)
            existing_messages = self._normalize_history_store_messages(
                existing_payload.get("messages")
            )
            overlap = self._suffix_prefix_overlap(existing_messages, observed)
            new_messages = observed[overlap:]
            if not new_messages:
                return

            merged_messages = existing_messages + new_messages
            next_version = self._history_version_from_payload(existing_payload) + 1
            payload = {
                "version": next_version,
                "messages": self._serialize_history_messages(merged_messages),
            }

            try:
                await self._store_put_item(store, namespace, key, payload)
                written_item = await self._store_get_item(store, namespace, key)
            except Exception:
                if attempt + 1 >= self._history_write_retry_limit:
                    logger.warning(
                        "AgentNode '%s' failed to persist graph history after %d "
                        "attempts (key='%s').",
                        self.name,
                        self._history_write_retry_limit,
                        key,
                    )
                    return
                backoff = (2**attempt) * self._history_retry_base_backoff_seconds
                await asyncio.sleep(backoff + random.uniform(0.0, 0.01))
                continue

            written_payload = self._history_payload_from_item(written_item)
            written_messages = self._normalize_history_store_messages(
                written_payload.get("messages")
            )
            written_version = self._history_version_from_payload(written_payload)
            if (
                written_version >= next_version
                and len(written_messages) >= len(merged_messages)
                and self._starts_with_messages(
                    written_messages[-len(merged_messages) :],
                    merged_messages,
                )
            ):
                return

            if attempt + 1 >= self._history_write_retry_limit:
                logger.warning(
                    "AgentNode '%s' detected persistent graph history write conflicts "
                    "after %d attempts (key='%s').",
                    self.name,
                    self._history_write_retry_limit,
                    key,
                )
                return

            backoff = (2**attempt) * self._history_retry_base_backoff_seconds
            await asyncio.sleep(backoff + random.uniform(0.0, 0.01))

    def _build_response_format_strategy(self) -> Any | None:
        """Create the response-format strategy when one is configured."""
        if self.response_format is None:
            return None
        return _ai_attr("ProviderStrategy")(self.response_format)

    def _build_runtime_config(  # noqa: C901
        self,
        state: State,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """Normalize the runnable config used for the agent invocation."""
        runtime_config: dict[str, Any] = {}
        if isinstance(config, Mapping):
            runtime_config.update(config)

        configurable_value = runtime_config.get("configurable")
        configurable = (
            dict(configurable_value) if isinstance(configurable_value, Mapping) else {}
        )
        workspace_id = state.get("workspace_id") if isinstance(state, Mapping) else None
        if isinstance(workspace_id, str):
            workspace_id = workspace_id.strip()
            if workspace_id:
                configurable.setdefault("workspace_id", workspace_id)

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
                results = state.get("node_results")
                if isinstance(results, Mapping):
                    maybe_thread_state = results.get("_thread_state")
                    if isinstance(maybe_thread_state, Mapping):
                        thread_state_payload = dict(maybe_thread_state)
        if thread_state_payload is not None:
            configurable.setdefault("thread_state", thread_state_payload)

        runtime_config["configurable"] = configurable
        return runtime_config

    def _set_conversation_key(
        self,
        runtime_config: dict[str, Any],
        history_key: str | None,
    ) -> None:
        """Store the resolved conversation key in the runnable config."""
        if history_key is None:
            return
        configurable_value = runtime_config.get("configurable")
        configurable = (
            dict(configurable_value) if isinstance(configurable_value, Mapping) else {}
        )
        configurable.setdefault("conversation_key", history_key)
        runtime_config["configurable"] = configurable

    async def _prepare_graph_chat_history(
        self,
        state: State,
        config: RunnableConfig,
        current_messages: list[BaseMessage],
    ) -> tuple[list[BaseMessage], Any | None, tuple[str, ...], str | None]:
        """Load persisted graph history when graph-backed chat history is enabled."""
        messages = list(current_messages)
        history_store: Any | None = None
        history_namespace: tuple[str, ...] = ()
        history_key: str | None = None

        if not self.use_graph_chat_history:
            return messages, history_store, history_namespace, history_key

        history_store = self._get_graph_store(config)
        history_key = self._resolve_history_key(state, config)
        history_namespace = self._history_namespace_tuple()
        if history_store is None:
            logger.warning(
                "AgentNode '%s' enabled graph history but runtime store is missing.",
                self.name,
            )
            return messages, history_store, history_namespace, history_key

        if history_key is None or self._check_has_checkpointer(config):
            return messages, history_store, history_namespace, history_key

        try:
            persisted_messages = await self._load_graph_history_messages(
                store=history_store,
                namespace=history_namespace,
                key=history_key,
            )
        except Exception:
            logger.warning(
                "AgentNode '%s' failed to read graph history (key='%s'); "
                "falling back to in-memory messages.",
                self.name,
                history_key,
            )
            return messages, history_store, history_namespace, history_key

        messages = persisted_messages + current_messages
        if len(messages) > self.max_messages:
            logger.info(
                "AgentNode '%s' truncated merged history from %d to %d messages "
                "(key='%s').",
                self.name,
                len(messages),
                self.max_messages,
                history_key,
            )
        return (
            self._trim_messages(messages),
            history_store,
            history_namespace,
            history_key,
        )

    def _set_run_trace_metadata(
        self,
        model: Any,
        result: Any,
    ) -> None:
        """Attach llm trace metadata when the agent returns structured output."""
        if not isinstance(result, Mapping):
            return
        self._set_trace_metadata_for_run(
            _llm_trace_metadata(self.ai_model, model=model, result=result)
        )

    async def _persist_graph_history_for_run(
        self,
        current_messages: list[BaseMessage],
        messages: list[BaseMessage],
        result: Any,
        history_store: Any | None,
        history_namespace: tuple[str, ...],
        history_key: str | None,
    ) -> None:
        """Persist graph history only when the current run produced mappings."""
        if not self.use_graph_chat_history:
            return
        if history_store is None or history_key is None:
            return
        if not isinstance(result, Mapping):
            return

        observed_messages = self._extract_observed_messages(
            current_messages,
            messages,
            result,
        )
        await self._persist_graph_history(
            store=history_store,
            namespace=history_namespace,
            key=history_key,
            observed_messages=observed_messages,
        )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Execute the agent and return results."""
        self._clear_trace_metadata_for_run()
        tools = await self._prepare_tools()
        response_format_strategy = self._build_response_format_strategy()

        # Initialize chat model with model_kwargs
        model = _ai_attr("init_chat_model")(
            self.ai_model,
            **_ai_attr("normalize_chat_model_kwargs")(self.ai_model, self.model_kwargs),
        )
        agent = _ai_attr("create_agent")(
            model,
            tools=tools,
            system_prompt=self._system_prompt_text,
            response_format=response_format_strategy,
        )
        # TODO: for models that don't support ProviderStrategy, use ToolStrategy

        current_messages = self._build_messages(state, config)
        runtime_config = self._build_runtime_config(state, config)
        (
            messages,
            history_store,
            history_namespace,
            history_key,
        ) = await self._prepare_graph_chat_history(state, config, current_messages)
        self._set_conversation_key(runtime_config, history_key)

        # Execute agent with normalized messages as input
        payload: dict[str, Any] = {"messages": messages}
        with _ai_attr("tool_execution_context")(runtime_config):
            result = await agent.ainvoke(  # type: ignore[arg-type,call-overload]
                payload,
                runtime_config,
            )
        self._set_run_trace_metadata(model, result)
        await self._persist_graph_history_for_run(
            current_messages,
            messages,
            result,
            history_store,
            history_namespace,
            history_key,
        )
        return result

    @property
    def _system_prompt_text(self) -> str | None:
        if isinstance(self.system_prompt, TextTensor):
            return self.system_prompt.text
        return self.system_prompt


@registry.register(
    NodeMetadata(
        name="AgentReplyExtractorNode",
        description="Extract the final assistant reply from agent messages",
        category="ai",
    )
)
class AgentReplyExtractorNode(TaskNode):
    """Extract the final reply from the agent output.

    After an :class:`AgentNode` runs, the workflow state contains a
    ``messages`` list mixing user, assistant, and tool turns.  This node scans
    that list in reverse and returns the most recent reply as plain text.

    It returns the most recent assistant message, *or* a trailing tool message
    when the turn ended on one. A turn ends on a tool message when a tool was
    marked ``return_direct=True`` (see :class:`WorkflowTool`): the agent loop
    exits right after the tool, so its output is surfaced verbatim instead of a
    model-composed paraphrase. In normal turns the last message is the
    assistant reply, so this preserves existing behaviour.
    """

    fallback_message: str = Field(
        default="Sorry, something went wrong. Please try again later.",
        description="Message returned when no assistant reply is found",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return ``{"agent_reply": "..."}`` from the last assistant/tool message."""
        messages = state.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, dict):
                if msg.get("role") in ("assistant", "tool"):
                    content = msg.get("content", "")
                    if content:
                        return {"agent_reply": str(content)}
            elif (
                isinstance(msg, BaseMessage)
                and msg.type in ("ai", "tool")
                and msg.content
            ):
                content = msg.content
                return {
                    "agent_reply": content if isinstance(content, str) else str(content)
                }
        return {"agent_reply": self.fallback_message}


@registry.register(
    NodeMetadata(
        name="LLMNode",
        description="Execute a text-only LLM call",
        category="ai",
    )
)
class LLMNode(AgentNode):
    """Node for executing an LLM on a single text input."""

    input_text: str | None = None
    """Text input to be processed by the LLM."""
    instruction: str | None = None
    """Optional instruction for post-processing the input."""
    user_message: str | None = None
    """Optional user message for language or tone inference."""
    draft_reply: str | None = None
    """Draft reply to be post-processed."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Execute the LLM with a single text input."""
        self._clear_trace_metadata_for_run()
        messages = self._build_messages(state)
        if not messages:
            return {"messages": []}

        tools = await self._prepare_tools()

        response_format_strategy = None
        if self.response_format is not None:
            response_format_strategy = _ai_attr("ProviderStrategy")(
                self.response_format
            )  # type: ignore[arg-type]

        model = _ai_attr("init_chat_model")(
            self.ai_model,
            **_ai_attr("normalize_chat_model_kwargs")(self.ai_model, self.model_kwargs),
        )
        agent = _ai_attr("create_agent")(
            model,
            tools=tools,
            system_prompt=self._system_prompt_text,
            response_format=response_format_strategy,
        )

        payload: dict[str, Any] = {"messages": messages}
        with _ai_attr("tool_execution_context")(config):
            result = await agent.ainvoke(payload, config)  # type: ignore[arg-type,call-overload]
        if isinstance(result, Mapping):  # pragma: no branch
            self._set_trace_metadata_for_run(
                _llm_trace_metadata(self.ai_model, model=model, result=result)
            )
        return result

    def _build_messages(
        self,
        _state: State,
        config: RunnableConfig | None = None,
    ) -> list[BaseMessage]:
        """Construct a single-turn message list for the LLM."""
        draft_reply = self._normalize_text(self.draft_reply)
        input_text = self._normalize_text(self.input_text)
        if not draft_reply and not input_text:
            return []

        base_text = draft_reply or input_text
        user_message = self._normalize_text(self.user_message)

        if user_message:
            content_text = f"User message:\n{user_message}\n\nDraft reply:\n{base_text}"
        else:
            content_text = base_text

        instruction = self._normalize_text(self.instruction)
        if instruction:
            content = f"Instruction:\n{instruction}\n\nText:\n{content_text}"
        else:
            content = content_text
        return [HumanMessage(content=content)]

    @staticmethod
    def _normalize_text(value: str | None) -> str:
        if isinstance(value, str):
            return value.strip()
        return ""
