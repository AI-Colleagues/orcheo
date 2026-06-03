"""Tests for DeepAgentNode."""

from __future__ import annotations
import contextlib
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.ai.deep_agent import DeepAgentNode
from orcheo.nodes.registry import registry


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_deep_agent_node_registered() -> None:
    """DeepAgentNode is registered in the global node registry."""
    metadata = registry.get_metadata("DeepAgentNode")
    assert metadata is not None
    assert metadata.name == "DeepAgentNode"
    assert metadata.category == "ai"


def test_deep_agent_node_registry_description() -> None:
    """Registry description mentions deep research and multi-step."""
    metadata = registry.get_metadata("DeepAgentNode")
    assert metadata is not None
    assert "deep-research" in metadata.description
    assert "multi-step" in metadata.description


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construction_defaults() -> None:
    """Node can be constructed with only required fields."""
    node = DeepAgentNode(name="test", ai_model="openai:gpt-4o")
    assert node.name == "test"
    assert node.ai_model == "openai:gpt-4o"
    assert node.system_prompt is None
    assert node.research_prompt is None
    assert node.predefined_tools == []
    assert node.workflow_tools == []
    assert node.mcp_servers == {}
    assert node.max_iterations == 100
    assert node.model_kwargs == {}
    assert node.response_format is None
    assert node.input_query is None
    assert node.skills is None
    assert node.memory is None
    assert node.debug is False


def test_construction_all_fields() -> None:
    """Node can be constructed with all optional fields."""
    node = DeepAgentNode(
        name="research",
        ai_model="openai:gpt-4o",
        system_prompt="You are a researcher.",
        research_prompt="Plan then synthesize.",
        predefined_tools=["web_search"],
        max_iterations=200,
        model_kwargs={"temperature": 0.1},
        response_format={"type": "json_object"},
        input_query="Find me information about X.",
        skills=["/skills/user/"],
        memory=["/memory/AGENTS.md"],
        debug=True,
    )
    assert node.system_prompt == "You are a researcher."
    assert node.research_prompt == "Plan then synthesize."
    assert node.max_iterations == 200
    assert node.model_kwargs == {"temperature": 0.1}
    assert node.input_query == "Find me information about X."
    assert node.skills == ["/skills/user/"]
    assert node.memory == ["/memory/AGENTS.md"]
    assert node.debug is True


# ---------------------------------------------------------------------------
# _build_system_prompt
# ---------------------------------------------------------------------------


def test_build_system_prompt_both() -> None:
    """Combined prompt joins system_prompt and research_prompt."""
    node = DeepAgentNode(
        name="test",
        ai_model="m",
        system_prompt="Base prompt.",
        research_prompt="Research instructions.",
    )
    result = node._build_system_prompt()
    assert result == "Base prompt.\n\nResearch instructions."


def test_build_system_prompt_only_system() -> None:
    """Only system_prompt is returned when research_prompt is None."""
    node = DeepAgentNode(name="test", ai_model="m", system_prompt="Base.")
    assert node._build_system_prompt() == "Base."


def test_build_system_prompt_only_research() -> None:
    """Only research_prompt is returned when system_prompt is None."""
    node = DeepAgentNode(name="test", ai_model="m", research_prompt="Research.")
    assert node._build_system_prompt() == "Research."


def test_build_system_prompt_none() -> None:
    """Returns None when both prompts are None."""
    node = DeepAgentNode(name="test", ai_model="m")
    assert node._build_system_prompt() is None


def test_build_system_prompt_strips_whitespace() -> None:
    """Whitespace is stripped from both prompt parts."""
    node = DeepAgentNode(
        name="test",
        ai_model="m",
        system_prompt="  Base.  ",
        research_prompt="  Research.  ",
    )
    assert node._build_system_prompt() == "Base.\n\nResearch."


# ---------------------------------------------------------------------------
# _build_messages
# ---------------------------------------------------------------------------


def test_build_messages_from_input_query() -> None:
    """Uses input_query when set."""
    node = DeepAgentNode(name="t", ai_model="m", input_query="Find X.")
    messages = node._build_messages(State({}))
    assert len(messages) == 1
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "Find X."


def test_build_messages_from_inputs_query() -> None:
    """Falls back to inputs.query when input_query is not set."""
    node = DeepAgentNode(name="t", ai_model="m")
    state = State({"inputs": {"query": "Research topic"}})
    messages = node._build_messages(state)
    assert len(messages) == 1
    assert messages[0].content == "Research topic"


def test_build_messages_from_inputs_message() -> None:
    """Falls back to inputs.message."""
    node = DeepAgentNode(name="t", ai_model="m")
    state = State({"inputs": {"message": "Hello agent"}})
    messages = node._build_messages(state)
    assert len(messages) == 1
    assert messages[0].content == "Hello agent"


def test_build_messages_from_inputs_prompt() -> None:
    """Falls back to inputs.prompt."""
    node = DeepAgentNode(name="t", ai_model="m")
    state = State({"inputs": {"prompt": "Do something"}})
    messages = node._build_messages(state)
    assert len(messages) == 1
    assert messages[0].content == "Do something"


def test_build_messages_from_inputs_input() -> None:
    """Falls back to inputs.input."""
    node = DeepAgentNode(name="t", ai_model="m")
    state = State({"inputs": {"input": "Process this"}})
    messages = node._build_messages(state)
    assert len(messages) == 1
    assert messages[0].content == "Process this"


def test_build_messages_empty() -> None:
    """Returns empty list when no query source is available."""
    node = DeepAgentNode(name="t", ai_model="m")
    assert node._build_messages(State({})) == []


def test_build_messages_skips_empty_strings() -> None:
    """Skips empty or whitespace-only input strings."""
    node = DeepAgentNode(name="t", ai_model="m")
    state = State({"inputs": {"query": "  ", "message": "valid"}})
    messages = node._build_messages(state)
    assert len(messages) == 1
    assert messages[0].content == "valid"


def test_build_messages_non_dict_inputs() -> None:
    """Returns empty when inputs is not a dict."""
    node = DeepAgentNode(name="t", ai_model="m")
    state = State({"inputs": "not a dict"})
    assert node._build_messages(state) == []


# ---------------------------------------------------------------------------
# _prepare_tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_tools_predefined() -> None:
    """Predefined tools are resolved from the tool registry."""
    mock_tool = MagicMock(spec=["name"])
    mock_tool.__class__ = type("FakeBaseTool", (), {})

    with (
        patch("orcheo.nodes.ai.deep_agent.tool_registry") as mock_registry,
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
    ):
        from langchain_core.tools import BaseTool

        real_tool = MagicMock(spec=BaseTool)
        mock_registry.get_tool.return_value = real_tool
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(name="t", ai_model="m", predefined_tools=["my_tool"])
        tools = await node._prepare_tools()

        mock_registry.get_tool.assert_called_once_with("my_tool")
        assert real_tool in tools


@pytest.mark.asyncio
async def test_prepare_tools_missing_tool_skipped() -> None:
    """Missing predefined tools are skipped with a warning."""
    with (
        patch("orcheo.nodes.ai.deep_agent.tool_registry") as mock_registry,
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
    ):
        mock_registry.get_tool.return_value = None
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(name="t", ai_model="m", predefined_tools=["nonexistent"])
        tools = await node._prepare_tools()
        assert tools == []


@pytest.mark.asyncio
async def test_prepare_tools_callable_factory() -> None:
    """Callable tool factories are instantiated."""
    from langchain_core.tools import BaseTool

    real_tool = MagicMock(spec=BaseTool)
    factory = MagicMock(return_value=real_tool)

    with (
        patch("orcheo.nodes.ai.deep_agent.tool_registry") as mock_registry,
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
    ):
        mock_registry.get_tool.return_value = factory
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(name="t", ai_model="m", predefined_tools=["factory_tool"])
        tools = await node._prepare_tools()
        factory.assert_called_once()
        assert real_tool in tools


@pytest.mark.asyncio
async def test_prepare_tools_factory_returns_non_tool() -> None:
    """Callable factory returning non-BaseTool is skipped."""
    factory = MagicMock(return_value="not a tool")

    with (
        patch("orcheo.nodes.ai.deep_agent.tool_registry") as mock_registry,
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
    ):
        mock_registry.get_tool.return_value = factory
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(name="t", ai_model="m", predefined_tools=["bad_factory"])
        tools = await node._prepare_tools()
        assert tools == []


@pytest.mark.asyncio
async def test_prepare_tools_factory_raises() -> None:
    """Callable factory that raises is skipped."""
    factory = MagicMock(side_effect=RuntimeError("boom"))

    with (
        patch("orcheo.nodes.ai.deep_agent.tool_registry") as mock_registry,
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
    ):
        mock_registry.get_tool.return_value = factory
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(name="t", ai_model="m", predefined_tools=["error_factory"])
        tools = await node._prepare_tools()
        assert tools == []


@pytest.mark.asyncio
async def test_prepare_tools_non_callable_non_tool() -> None:
    """Non-callable, non-BaseTool registry entries are skipped."""
    with (
        patch("orcheo.nodes.ai.deep_agent.tool_registry") as mock_registry,
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
    ):
        mock_registry.get_tool.return_value = 42
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(name="t", ai_model="m", predefined_tools=["bad_entry"])
        tools = await node._prepare_tools()
        assert tools == []


@pytest.mark.asyncio
async def test_prepare_tools_mcp_servers() -> None:
    """MCP server tools are loaded."""
    mcp_tool = MagicMock()

    with (
        patch("orcheo.nodes.ai.deep_agent.tool_registry"),
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
    ):
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = [mcp_tool]
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(
            name="t",
            ai_model="m",
            mcp_servers={"server1": {"url": "http://localhost"}},
        )
        tools = await node._prepare_tools()
        assert mcp_tool in tools


@pytest.mark.asyncio
async def test_prepare_tools_workflow_tools() -> None:
    """Workflow tools are compiled and added."""
    from langgraph.graph import StateGraph
    from orcheo.nodes.ai import WorkflowTool

    mock_graph = MagicMock(spec=StateGraph)
    mock_compiled = MagicMock()
    mock_graph.compile.return_value = mock_compiled

    wf_tool = WorkflowTool(
        name="sub_wf",
        description="A sub-workflow tool",
        graph=mock_graph,
        output_path="results.final.answer",
    )

    with (
        patch("orcheo.nodes.ai.deep_agent.tool_registry"),
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
        patch("orcheo.nodes.ai.deep_agent._create_workflow_tool_func") as mock_create,
    ):
        from langchain_core.tools import StructuredTool

        mock_structured = MagicMock(spec=StructuredTool)
        mock_create.return_value = mock_structured
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(name="t", ai_model="m", workflow_tools=[wf_tool])
        tools = await node._prepare_tools()
        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["output_path"] == "results.final.answer"
        assert mock_structured in tools


# ---------------------------------------------------------------------------
# _resolve_skills
# ---------------------------------------------------------------------------


def test_resolve_skills_explicit_list() -> None:
    """Returns the explicit skills list when set."""
    node = DeepAgentNode(name="t", ai_model="m", skills=["/custom/skill"])
    assert node._resolve_skills() == ["/custom/skill"]


def test_resolve_skills_explicit_empty_list() -> None:
    """Returns an empty list when skills is explicitly set to []."""
    node = DeepAgentNode(name="t", ai_model="m", skills=[])
    assert node._resolve_skills() == []


def test_resolve_skills_auto_discovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto-discovers installed skills when skills is None."""
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: A test skill.\n---\n"
    )

    node = DeepAgentNode(name="t", ai_model="m")
    monkeypatch.setenv("ORCHEO_SKILLS_DIR", str(skills_dir))
    result = node._resolve_skills()

    assert result is not None
    assert len(result) == 1
    assert result[0] == str(skill_dir)


def test_resolve_skills_none_when_no_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Returns None when no skills are installed."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    node = DeepAgentNode(name="t", ai_model="m")
    monkeypatch.setenv("ORCHEO_SKILLS_DIR", str(skills_dir))
    result = node._resolve_skills()

    assert result is None


def test_resolve_skills_dir_not_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns None when configured skills directory does not exist."""
    monkeypatch.setenv(
        "ORCHEO_SKILLS_DIR", "/nonexistent/path/that/does/not/exist/__orcheo_test__"
    )
    node = DeepAgentNode(name="t", ai_model="m")
    result = node._resolve_skills()
    assert result is None


def test_resolve_skills_handles_exception() -> None:
    """Returns None when skill discovery fails."""
    node = DeepAgentNode(name="t", ai_model="m")
    with patch(
        "orcheo.nodes.ai.deep_agent.Path.home", side_effect=RuntimeError("boom")
    ):
        result = node._resolve_skills()

    assert result is None


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_invokes_deep_agent(tmp_path: Path) -> None:
    """run() creates a deep agent via create_deep_agent and invokes it."""
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": [AIMessage(content="Result")]}

    with (
        patch(
            "orcheo.nodes.ai.deep_agent.create_deep_agent", return_value=mock_agent
        ) as mock_create,
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
        patch.dict(os.environ, {"ORCHEO_SKILLS_DIR": str(tmp_path)}),
    ):
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(
            name="test",
            ai_model="openai:gpt-4o",
            system_prompt="You are helpful.",
            research_prompt="Be thorough.",
            max_iterations=150,
            input_query="Research AI agents.",
        )
        state = State({})
        config = RunnableConfig()
        result = await node.run(state, config)

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        # Model string passed directly (no model_kwargs)
        assert call_kwargs[0][0] == "openai:gpt-4o"
        assert call_kwargs.kwargs["system_prompt"] == (
            "You are helpful.\n\nBe thorough."
        )

        invoke_config = mock_agent.ainvoke.call_args[1]["config"]
        assert invoke_config["recursion_limit"] == 150

        assert result == {"messages": [AIMessage(content="Result")]}


@pytest.mark.asyncio
async def test_run_propagates_workspace_id_to_tool_runtime(tmp_path: Path) -> None:
    """Nested workflow tools should inherit the workspace id in runtime config."""
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": []}
    captured_runtime_config: dict[str, object] = {}

    with (
        patch("orcheo.nodes.ai.deep_agent.create_deep_agent", return_value=mock_agent),
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
        patch.dict(os.environ, {"ORCHEO_SKILLS_DIR": str(tmp_path)}),
        patch(
            "orcheo.nodes.ai.deep_agent.tool_execution_context",
            side_effect=lambda cfg: (
                captured_runtime_config.update(dict(cfg or {}))
                or contextlib.nullcontext()
            ),
        ),
    ):
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(
            name="test",
            ai_model="openai:gpt-4o",
            input_query="Research AI agents.",
        )
        state = State({"workspace_id": "workspace-1"})
        await node.run(state, RunnableConfig())

    assert captured_runtime_config["configurable"]["workspace_id"] == "workspace-1"
    assert (
        mock_agent.ainvoke.await_args.kwargs["config"]["configurable"]["workspace_id"]
        == "workspace-1"
    )


@pytest.mark.asyncio
async def test_run_accepts_mapping_config_and_strips_workspace_id(
    tmp_path: Path,
) -> None:
    """Mapping configs should merge into the runtime payload."""
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": []}
    captured_runtime_config: dict[str, object] = {}

    with (
        patch("orcheo.nodes.ai.deep_agent.create_deep_agent", return_value=mock_agent),
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
        patch.dict(os.environ, {"ORCHEO_SKILLS_DIR": str(tmp_path)}),
        patch(
            "orcheo.nodes.ai.deep_agent.tool_execution_context",
            side_effect=lambda cfg: (
                captured_runtime_config.update(dict(cfg or {}))
                or contextlib.nullcontext()
            ),
        ),
    ):
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(
            name="test",
            ai_model="openai:gpt-4o",
            input_query="Research AI agents.",
        )
        state = State({"workspace_id": " workspace-1 "})
        await node.run(
            state,
            {"configurable": {"custom": "value"}},
        )

    assert captured_runtime_config["configurable"]["workspace_id"] == "workspace-1"
    assert captured_runtime_config["configurable"]["custom"] == "value"


@pytest.mark.asyncio
async def test_run_handles_non_mapping_config_and_blank_workspace_id(
    tmp_path: Path,
) -> None:
    """run() should tolerate a non-mapping config payload and blank workspace ids."""

    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": []}
    captured_runtime_config: dict[str, object] = {}

    with (
        patch("orcheo.nodes.ai.deep_agent.create_deep_agent", return_value=mock_agent),
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
        patch.dict(os.environ, {"ORCHEO_SKILLS_DIR": str(tmp_path)}),
        patch(
            "orcheo.nodes.ai.deep_agent.tool_execution_context",
            side_effect=lambda cfg: (
                captured_runtime_config.update(dict(cfg or {}))
                or contextlib.nullcontext()
            ),
        ),
    ):
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(
            name="test",
            ai_model="openai:gpt-4o",
            input_query="Research AI agents.",
        )
        state = State({"workspace_id": "   "})
        await node.run(state, object())

    assert captured_runtime_config["configurable"] == {}
    assert "workspace_id" not in captured_runtime_config["configurable"]


@pytest.mark.asyncio
async def test_run_passes_model_string_without_kwargs(tmp_path: Path) -> None:
    """run() passes model as string when no model_kwargs are set."""
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": []}

    with (
        patch(
            "orcheo.nodes.ai.deep_agent.create_deep_agent", return_value=mock_agent
        ) as mock_create,
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
        patch("orcheo.nodes.ai.deep_agent.init_chat_model") as mock_init,
        patch.dict(os.environ, {"ORCHEO_SKILLS_DIR": str(tmp_path)}),
    ):
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(name="t", ai_model="openai:gpt-4o", input_query="Q.")
        await node.run(State({}), RunnableConfig())

        # init_chat_model should NOT be called when no model_kwargs
        mock_init.assert_not_called()
        # Model string passed directly to create_deep_agent
        assert mock_create.call_args[0][0] == "openai:gpt-4o"


@pytest.mark.asyncio
async def test_run_infers_model_name_from_instance_for_trace(
    tmp_path: Path,
) -> None:
    """run() falls back to infer_model_name_from_instance when metadata missing."""
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": []}

    fake_model = MagicMock()

    with (
        patch("orcheo.nodes.ai.deep_agent.create_deep_agent", return_value=mock_agent),
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
        patch("orcheo.nodes.ai.deep_agent.init_chat_model", return_value=fake_model),
        patch.dict(os.environ, {"ORCHEO_SKILLS_DIR": str(tmp_path)}),
        patch(
            "orcheo.nodes.ai.deep_agent.tool_execution_context",
            side_effect=lambda config: contextlib.nullcontext(),
        ),
        patch(
            "orcheo.nodes.ai.deep_agent.infer_chat_result_model_name",
            return_value=None,
        ),
        patch(
            "orcheo.nodes.ai.deep_agent.infer_model_name_from_instance",
            return_value="fallback-model",
        ),
    ):
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(
            name="test",
            ai_model="openai:gpt-4o",
            model_kwargs={"temperature": 0.1},
            skills=[],
        )
        node._set_trace_metadata_for_run = MagicMock()

        result = await node.run(State({"inputs": {"query": "trace"}}), RunnableConfig())

    assert result == {"messages": []}
    node._set_trace_metadata_for_run.assert_called_once()
    trace_payload = node._set_trace_metadata_for_run.call_args[0][0]
    assert trace_payload["ai"]["actual_model"] == "fallback-model"


@pytest.mark.asyncio
async def test_run_with_response_format(tmp_path: Path) -> None:
    """run() passes response_format to create_deep_agent."""
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": []}

    with (
        patch("orcheo.nodes.ai.deep_agent.create_deep_agent", return_value=mock_agent),
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
        patch("orcheo.nodes.ai.deep_agent.ProviderStrategy") as mock_strategy,
        patch.dict(os.environ, {"ORCHEO_SKILLS_DIR": str(tmp_path)}),
    ):
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(
            name="t",
            ai_model="m",
            response_format={"type": "json_object"},
            input_query="Test.",
        )
        await node.run(State({}), RunnableConfig())
        mock_strategy.assert_called_once()


@pytest.mark.asyncio
async def test_run_no_response_format(tmp_path: Path) -> None:
    """run() does not create ProviderStrategy when response_format is None."""
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": []}

    with (
        patch("orcheo.nodes.ai.deep_agent.create_deep_agent", return_value=mock_agent),
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
        patch("orcheo.nodes.ai.deep_agent.ProviderStrategy") as mock_strategy,
        patch.dict(os.environ, {"ORCHEO_SKILLS_DIR": str(tmp_path)}),
    ):
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(name="t", ai_model="m", input_query="Test.")
        await node.run(State({}), RunnableConfig())
        mock_strategy.assert_not_called()


@pytest.mark.asyncio
async def test_run_model_kwargs_uses_init_chat_model(tmp_path: Path) -> None:
    """run() uses init_chat_model when model_kwargs are provided."""
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": []}

    with (
        patch("orcheo.nodes.ai.deep_agent.init_chat_model") as mock_init,
        patch(
            "orcheo.nodes.ai.deep_agent.create_deep_agent", return_value=mock_agent
        ) as mock_create,
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
        patch.dict(os.environ, {"ORCHEO_SKILLS_DIR": str(tmp_path)}),
    ):
        mock_model = MagicMock()
        mock_init.return_value = mock_model
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(
            name="t",
            ai_model="m",
            model_kwargs={"temperature": 0.5},
            input_query="Test.",
        )
        await node.run(State({}), RunnableConfig())
        mock_init.assert_called_once_with("m", temperature=0.5)
        # The initialized model object is passed to create_deep_agent
        assert mock_create.call_args[0][0] is mock_model


@pytest.mark.asyncio
async def test_run_uses_inferred_provider_api_key_when_available(
    tmp_path: Path,
) -> None:
    """run() initializes the model when provider key inference supplies kwargs."""
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": []}

    with (
        patch(
            "orcheo.nodes.ai.deep_agent.normalize_chat_model_kwargs",
            return_value={"api_key": "resolved-key"},
        ),
        patch("orcheo.nodes.ai.deep_agent.init_chat_model") as mock_init,
        patch(
            "orcheo.nodes.ai.deep_agent.create_deep_agent", return_value=mock_agent
        ) as mock_create,
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
        patch.dict(os.environ, {"ORCHEO_SKILLS_DIR": str(tmp_path)}),
    ):
        mock_model = MagicMock()
        mock_init.return_value = mock_model
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(
            name="t",
            ai_model="deepseek:deepseek-chat",
            input_query="Test.",
        )
        await node.run(State({}), RunnableConfig())

        mock_init.assert_called_once_with(
            "deepseek:deepseek-chat", api_key="resolved-key"
        )
        assert mock_create.call_args[0][0] is mock_model


@pytest.mark.asyncio
async def test_run_passes_skills_and_memory() -> None:
    """run() passes skills and memory to create_deep_agent."""
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": []}

    with (
        patch(
            "orcheo.nodes.ai.deep_agent.create_deep_agent", return_value=mock_agent
        ) as mock_create,
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
    ):
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(
            name="t",
            ai_model="m",
            input_query="Test.",
            skills=["/skills/user/"],
            memory=["/memory/AGENTS.md"],
        )
        await node.run(State({}), RunnableConfig())

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["skills"] == ["/skills/user/"]
        assert call_kwargs["memory"] == ["/memory/AGENTS.md"]


@pytest.mark.asyncio
async def test_run_passes_debug_flag(tmp_path: Path) -> None:
    """run() passes debug flag to create_deep_agent."""
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": []}

    with (
        patch(
            "orcheo.nodes.ai.deep_agent.create_deep_agent", return_value=mock_agent
        ) as mock_create,
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
        patch.dict(os.environ, {"ORCHEO_SKILLS_DIR": str(tmp_path)}),
    ):
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(name="t", ai_model="m", input_query="Q.", debug=True)
        await node.run(State({}), RunnableConfig())

        assert mock_create.call_args.kwargs["debug"] is True


@pytest.mark.asyncio
async def test_run_defaults_skills_memory_none(tmp_path: Path) -> None:
    """run() passes None for skills/memory when not configured and no installed skills."""  # noqa: E501
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": []}

    with (
        patch(
            "orcheo.nodes.ai.deep_agent.create_deep_agent", return_value=mock_agent
        ) as mock_create,
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
        patch.dict(os.environ, {"ORCHEO_SKILLS_DIR": str(tmp_path)}),
    ):
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(name="t", ai_model="m", input_query="Q.")
        await node.run(State({}), RunnableConfig())

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["skills"] is None
        assert call_kwargs["memory"] is None
        assert call_kwargs["debug"] is False


# ---------------------------------------------------------------------------
# AINode __call__ integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_wraps_result(tmp_path: Path) -> None:
    """AINode.__call__ delegates to run and serializes."""
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": [AIMessage(content="Done")]}

    with (
        patch("orcheo.nodes.ai.deep_agent.create_deep_agent", return_value=mock_agent),
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
        patch.dict(os.environ, {"ORCHEO_SKILLS_DIR": str(tmp_path)}),
    ):
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(name="test", ai_model="m", input_query="Go.")
        result = await node(State({}), RunnableConfig())
        assert "messages" in result


# ---------------------------------------------------------------------------
# Variable interpolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_variable_interpolation_on_input_query(tmp_path: Path) -> None:
    """input_query supports {{variable}} template interpolation."""
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"messages": []}

    with (
        patch("orcheo.nodes.ai.deep_agent.create_deep_agent", return_value=mock_agent),
        patch("orcheo.nodes.ai.deep_agent.MultiServerMCPClient") as mock_mcp,
        patch.dict(os.environ, {"ORCHEO_SKILLS_DIR": str(tmp_path)}),
    ):
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.get_tools.return_value = []
        mock_mcp.return_value = mock_mcp_instance

        node = DeepAgentNode(name="t", ai_model="m", input_query="{{inputs.topic}}")
        state = State({"inputs": {"topic": "quantum computing"}})
        await node(state, RunnableConfig())

        call_args = mock_agent.ainvoke.call_args
        messages = call_args[0][0]["messages"]
        assert len(messages) == 1
        assert messages[0].content == "quantum computing"
