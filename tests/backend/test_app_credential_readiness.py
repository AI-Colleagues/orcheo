"""Tests for workflow credential readiness helpers and endpoint."""

from __future__ import annotations
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from langgraph.graph import StateGraph
from pydantic import BaseModel
from orcheo.workspace import Role, WorkspaceContext
from orcheo_backend.app import credential_readiness as readiness
from orcheo_backend.app import get_workflow_credential_readiness
from orcheo_backend.app.credential_readiness import (
    collect_workflow_credential_placeholders,
)
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)


def _workspace_context(workspace_id: UUID | None = None) -> WorkspaceContext:
    return WorkspaceContext(
        workspace_id=workspace_id or uuid4(),
        workspace_slug="test",
        user_id="test-user",
        role=Role.OWNER,
    )


def test_collect_workflow_credential_placeholders_scans_source_without_execution() -> (
    None
):
    source = """
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode
from orcheo.nodes.connectors.telegram import MessageTelegram

class TelegramInput(BaseModel):
    message: str = Field(description="Message to send")

def build_tool_graph() -> StateGraph:
    graph = StateGraph(State)
    graph.add_node(
        "send_telegram",
        MessageTelegram(
            name="send_telegram",
            chat_id="{{config.configurable.telegram_chat_id}}",
            message="{{inputs.message}}",
        ),
    )
    graph.add_edge(START, "send_telegram")
    graph.add_edge("send_telegram", END)
    return graph

def orcheo_workflow() -> StateGraph:
    graph = StateGraph(State)
    agent = AgentNode(
        name="agent",
        ai_model="openai:gpt-4o-mini",
        model_kwargs={"api_key": "[[openai_api_key]]"},
        workflow_tools=[
            {
                "name": "send_telegram_message",
                "description": "Send a Telegram message.",
                "graph": build_tool_graph(),
                "args_schema": TelegramInput,
            }
        ],
    )
    graph.add_node("agent", agent)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    return graph
"""

    placeholders = collect_workflow_credential_placeholders(
        {"source": source, "entrypoint": None},
        {"configurable": {"telegram_chat_id": "[[telegram_chat_id]]"}},
    )

    assert sorted(placeholders) == [
        "openai_api_key",
        "telegram_chat_id",
    ]


def test_collect_workflow_credential_placeholders_scans_provider_specific_aliases() -> (
    None
):
    source = """
from langgraph.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode

def orcheo_workflow() -> StateGraph:
    graph = StateGraph(State)
    agent = AgentNode(
        name="agent",
        ai_model="{{config.configurable.ai_model}}",
        model_kwargs={
            "openai_api_key": "[[openai_primary_key]]",
            "deepseek_api_key": "[[deepseek_team_key]]",
        },
    )
    graph.add_node("agent", agent)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    return graph
"""

    placeholders = collect_workflow_credential_placeholders(
        {"source": source, "entrypoint": None},
        {"configurable": {"ai_model": "deepseek:deepseek-chat"}},
    )

    assert sorted(placeholders) == ["deepseek_team_key", "openai_primary_key"]
    assert placeholders["openai_primary_key"] == {"[[openai_primary_key]]"}
    assert placeholders["deepseek_team_key"] == {"[[deepseek_team_key]]"}


def test_collect_workflow_credential_placeholders_falls_back_when_source_fails() -> (
    None
):
    placeholders = collect_workflow_credential_placeholders(
        {
            "source": """
def orcheo_workflow():
    raise RuntimeError("boom")
""",
            "entrypoint": None,
            "nodes": [{"token": "[[slack_bot_token]]"}],
        },
        {"configurable": {"fallback_token": "[[fallback_token]]"}},
    )

    assert placeholders == {
        "fallback_token": {"[[fallback_token]]"},
        "slack_bot_token": {"[[slack_bot_token]]"},
    }


def test_collect_workflow_credential_placeholders_raw_payloads_no_source() -> None:
    placeholders = collect_workflow_credential_placeholders(
        {"nodes": [{"token": "[[slack_bot_token]]"}]},
        None,
    )

    assert placeholders == {"slack_bot_token": {"[[slack_bot_token]]"}}


def test_collect_workflow_credential_placeholders_ignores_optional_external_agent_auth() -> (  # noqa: E501
    None
):
    placeholders = collect_workflow_credential_placeholders(
        {
            "source": """
from langgraph.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes.ai.external.gemini import GeminiNode

def orcheo_workflow() -> StateGraph:
    graph = StateGraph(State)
    graph.add_node(
        "gemini",
        GeminiNode(
            name="gemini",
            prompt="Implement the task",
            working_directory="/workspace/agents",
        ),
    )
    graph.add_edge(START, "gemini")
    graph.add_edge("gemini", END)
    return graph
""",
            "entrypoint": None,
        },
        None,
    )

    assert placeholders == {}


@pytest.mark.asyncio()
async def test_get_workflow_credential_readiness_handles_missing_workflow() -> None:
    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del include_archived, workspace_id
            return UUID(str(workflow_ref))

        async def get_workflow(self, workflow_id):
            raise WorkflowNotFoundError(str(workflow_id))

    class Vault:
        def list_credentials(self, *, context, workspace_id=None):
            del context, workspace_id
            return []

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_credential_readiness(
            str(uuid4()),
            repository=Repository(),
            vault=Vault(),
            workspace=_workspace_context(),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_get_workflow_credential_readiness_without_versions() -> None:
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived, workspace_id
            return workflow_id

        async def get_workflow(self, workflow_id):
            return object()

        async def get_latest_version(self, workflow_id):
            raise WorkflowVersionNotFoundError(str(workflow_id))

    class Vault:
        def list_credentials(self, *, context, workspace_id=None):
            del context, workspace_id
            return []

    response = await get_workflow_credential_readiness(
        str(workflow_id),
        repository=Repository(),
        vault=Vault(),
        workspace=_workspace_context(),
    )

    assert response.status == "not_required"
    assert response.referenced_credentials == []


@pytest.mark.asyncio()
async def test_get_workflow_credential_readiness_reports_available_and_missing() -> (
    None
):
    workflow_id = uuid4()
    version = SimpleNamespace(
        graph={
            "source": """
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode
from orcheo.nodes.connectors.telegram import MessageTelegram

class TelegramInput(BaseModel):
    message: str = Field(description="Message to send")

def build_tool_graph() -> StateGraph:
    graph = StateGraph(State)
    graph.add_node(
        "send_telegram",
        MessageTelegram(
            name="send_telegram",
            chat_id="{{config.configurable.telegram_chat_id}}",
            message="{{inputs.message}}",
        ),
    )
    graph.add_edge(START, "send_telegram")
    graph.add_edge("send_telegram", END)
    return graph

def orcheo_workflow() -> StateGraph:
    graph = StateGraph(State)
    agent = AgentNode(
        name="agent",
        ai_model="openai:gpt-4o-mini",
        model_kwargs={"api_key": "[[openai_api_key]]"},
        workflow_tools=[
            {
                "name": "send_telegram_message",
                "description": "Send a Telegram message.",
                "graph": build_tool_graph(),
                "args_schema": TelegramInput,
            }
        ],
    )
    graph.add_node("agent", agent)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    return graph
""",
            "entrypoint": None,
        },
        runnable_config={"configurable": {"telegram_chat_id": "[[telegram_chat_id]]"}},
    )

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived, workspace_id
            return workflow_id

        async def get_workflow(self, workflow_id):
            return object()

        async def get_latest_version(self, workflow_id):
            return version

        async def get_workflow_workspace_id(self, workflow_id):
            del workflow_id
            return None

    class Vault:
        def list_credentials(self, *, context, workspace_id=None):
            del context, workspace_id
            return [
                SimpleNamespace(
                    id=uuid4(),
                    name="openai_api_key",
                    provider="openai",
                ),
                SimpleNamespace(
                    id=uuid4(),
                    name="telegram_token",
                    provider="telegram",
                ),
            ]

    response = await get_workflow_credential_readiness(
        str(workflow_id),
        repository=Repository(),
        vault=Vault(),
        workspace=_workspace_context(),
    )

    assert response.status == "missing"
    assert response.available_credentials == ["openai_api_key"]
    assert response.missing_credentials == ["telegram_chat_id"]


class _PlaceholderModel(BaseModel):
    api_key: str = "[[placeholder]]"


def test_collect_value_traverses_state_graph_and_model() -> None:
    placeholders: dict[str, set[str]] = {}
    graph = StateGraph(dict)
    graph.nodes["node"] = SimpleNamespace(runnable=_PlaceholderModel())

    readiness._collect_value(graph, placeholders, seen=set())

    assert placeholders == {"placeholder": {"[[placeholder]]"}}


def test_collect_string_ignores_optional_external_agent_placeholder() -> None:
    placeholders: dict[str, set[str]] = {}

    readiness._collect_string("[[GEMINI_AUTH_JSON]]", placeholders)

    assert placeholders == {}


def test_collect_state_graph_returns_when_seen() -> None:
    graph = SimpleNamespace(nodes={})
    placeholders: dict[str, set[str]] = {}
    readiness._collect_state_graph(graph, placeholders, seen={id(graph)})
    assert placeholders == {}


def test_collect_string_handles_invalid_placeholders() -> None:
    placeholders: dict[str, set[str]] = {}
    readiness._collect_string("[[   ]]", placeholders)
    assert placeholders == {}
    readiness._collect_string("[[valid_token]]", placeholders)
    assert placeholders == {"valid_token": {"[[valid_token]]"}}


def test_mark_seen_detects_duplicates() -> None:
    seen: set[int] = set()
    token = object()
    assert not readiness._mark_seen(token, seen)
    assert readiness._mark_seen(token, seen)


def test_collect_helpers_respect_seen_guard() -> None:
    placeholders: dict[str, set[str]] = {}
    model = _PlaceholderModel()
    readiness._collect_model(model, placeholders, seen={id(model)})
    seq = ["[[placeholder]]"]
    readiness._collect_sequence(seq, placeholders, seen={id(seq)})
    mapping = {"token": "[[placeholder]]"}
    readiness._collect_mapping(mapping, placeholders, seen={id(mapping)})
    assert placeholders == {}


def test_unwrap_runnable_looks_inside_langgraph_wrappers() -> None:
    model = _PlaceholderModel()

    class _Wrapper:
        def __init__(self, *, afunc=None, func=None):
            self.afunc = afunc
            self.func = func

    assert readiness._unwrap_runnable(_Wrapper(afunc=model)) is model
    assert readiness._unwrap_runnable(_Wrapper(func=model)) is model
    wrapper = _Wrapper()
    assert readiness._unwrap_runnable(wrapper) is wrapper


@pytest.mark.asyncio()
async def test_get_workflow_credential_readiness_reports_ready_when_all_credentials_present() -> (  # noqa: E501
    None
):
    workflow_id = uuid4()
    version = SimpleNamespace(
        graph={"nodes": [{"name": "node", "token": "[[vault_token]]"}]},
        runnable_config={},
    )

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived, workspace_id
            return workflow_id

        async def get_workflow(self, workflow_id):
            return object()

        async def get_latest_version(self, workflow_id):
            return version

        async def get_workflow_workspace_id(self, workflow_id):
            del workflow_id
            return None

    class Vault:
        def list_credentials(self, *, context, workspace_id=None):
            del context, workspace_id
            return [
                SimpleNamespace(
                    id=uuid4(),
                    name="vault_token",
                    provider="vault",
                )
            ]

    response = await get_workflow_credential_readiness(
        str(workflow_id),
        repository=Repository(),
        vault=Vault(),
        workspace=_workspace_context(),
    )

    assert response.status == "ready"
    assert response.missing_credentials == []
    assert response.available_credentials == ["vault_token"]


@pytest.mark.asyncio()
async def test_get_workflow_credential_readiness_reports_not_required_when_graph_empty() -> (  # noqa: E501
    None
):
    workflow_id = uuid4()
    version = SimpleNamespace(graph={}, runnable_config={})

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived, workspace_id
            return workflow_id

        async def get_workflow(self, workflow_id):
            return object()

        async def get_latest_version(self, workflow_id):
            return version

        async def get_workflow_workspace_id(self, workflow_id):
            del workflow_id
            return None

    class Vault:
        def list_credentials(self, *, context, workspace_id=None):
            del context, workspace_id
            return []

    response = await get_workflow_credential_readiness(
        str(workflow_id),
        repository=Repository(),
        vault=Vault(),
        workspace=_workspace_context(),
    )

    assert response.status == "not_required"
    assert response.referenced_credentials == []


@pytest.mark.asyncio()
async def test_get_workflow_credential_readiness_ignores_other_workspaces() -> None:
    """Credentials in a different workspace must not satisfy readiness."""
    workflow_id = uuid4()
    caller_workspace = uuid4()
    other_workspace = uuid4()
    version = SimpleNamespace(
        graph={"nodes": [{"name": "node", "token": "[[vault_token]]"}]},
        runnable_config={},
    )

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived, workspace_id
            return workflow_id

        async def get_workflow(self, workflow_id):
            return object()

        async def get_latest_version(self, workflow_id):
            return version

        async def get_workflow_workspace_id(self, workflow_id):
            del workflow_id
            return str(caller_workspace)

    class Vault:
        def __init__(self) -> None:
            self.observed_workspace_id: str | None = None
            self._store = {
                str(other_workspace): [
                    SimpleNamespace(
                        id=uuid4(),
                        name="vault_token",
                        provider="vault",
                    )
                ],
                str(caller_workspace): [],
            }

        def list_credentials(self, *, context, workspace_id=None):
            del context
            self.observed_workspace_id = workspace_id
            if workspace_id is None:
                return [item for items in self._store.values() for item in items]
            return list(self._store.get(workspace_id, []))

    vault = Vault()
    response = await get_workflow_credential_readiness(
        str(workflow_id),
        repository=Repository(),
        vault=vault,
        workspace=_workspace_context(workspace_id=caller_workspace),
    )

    assert vault.observed_workspace_id == str(caller_workspace)
    assert response.status == "missing"
    assert response.missing_credentials == ["vault_token"]
    assert response.available_credentials == []
