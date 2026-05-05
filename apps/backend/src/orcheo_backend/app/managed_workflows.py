"""Helpers for backend-owned managed workflows."""

from __future__ import annotations
from textwrap import dedent
from typing import Any
from orcheo.graph.ingestion import ingest_langgraph_script
from orcheo.models.workflow import Workflow, WorkflowDraftAccess
from orcheo.workspace import Workspace
from orcheo_backend.app.repository import (
    WorkflowHandleConflictError,
    WorkflowNotFoundError,
    WorkflowRepository,
)


MANAGED_VIBE_WORKFLOW_HANDLE = "orcheo-vibe-agent"
MANAGED_VIBE_WORKFLOW_TEMPLATE_ID = "template-vibe-agent"
MANAGED_VIBE_WORKFLOW_NAME = "Orcheo Vibe"
MANAGED_VIBE_WORKFLOW_DESCRIPTION = (
    "Routes ChatKit conversations to the connected external agent runtime "
    "selected in the native ChatKit model picker."
)
MANAGED_VIBE_WORKFLOW_NOTES = "Seeded from the Orcheo Vibe template."
MANAGED_VIBE_WORKFLOW_CREATED_BY = "system"
MANAGED_VIBE_WORKFLOW_ACTOR = "system"

MANAGED_VIBE_TEMPLATE_METADATA: dict[str, Any] = {
    "templateVersion": "1.0.1",
    "minOrcheoVersion": "0.14.2",
    "validatedProviderApi": "private-bot-listener-suite-2026-03-11",
    "validationDate": "2026-04-13",
    "owner": "ShaojieJiang",
    "acceptanceCriteria": [
        "Routes Vibe ChatKit requests to the selected external agent provider.",
        "Includes Canvas page context in the generated external-agent prompt.",
    ],
    "revalidationTriggers": [
        "ChatKit metadata payload shape changes.",
        "External agent provider selection contract changes.",
    ],
}

MANAGED_VIBE_WORKFLOW_SCRIPT = dedent(
    """
    from collections.abc import Mapping, Sequence
    from typing import Any
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableConfig
    from langgraph.graph import END, START, StateGraph
    from orcheo.edges.branching import Switch, SwitchCase
    from orcheo.graph.state import State
    from orcheo.nodes.base import TaskNode
    from orcheo.nodes.claude_code import ClaudeCodeNode
    from orcheo.nodes.codex import CodexNode
    from orcheo.nodes.gemini import GeminiNode


    def stringify_content(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, Mapping):
            text = value.get("text")
            return text.strip() if isinstance(text, str) else ""
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            parts = [stringify_content(item) for item in value]
            return "\\n".join(part for part in parts if part)
        return ""


    def extract_context(inputs: Mapping[str, Any]) -> str:
        metadata = inputs.get("metadata")
        if not isinstance(metadata, Mapping):
            return ""
        context = metadata.get("context")
        return context.strip() if isinstance(context, str) else ""


    def build_conversation_lines(inputs: Mapping[str, Any]) -> list[str]:
        lines: list[str] = []
        history = inputs.get("history")
        if isinstance(history, list):
            for item in history:
                if not isinstance(item, Mapping):
                    continue
                role = item.get("role")
                content = stringify_content(item.get("content"))
                if not isinstance(role, str) or not content:
                    continue
                lines.append(f"{role.strip()}: {content}")

        message = inputs.get("message")
        if isinstance(message, str) and message.strip():
            latest_user_message = f"user: {message.strip()}"
            if not lines or lines[-1] != latest_user_message:
                lines.append(latest_user_message)

        return lines


    def with_context(context: str, label: str, body: str) -> str:
        return f"Canvas context:\\n{context}\\n\\n{label}:\\n{body}"


    def fallback_prompt(inputs: Mapping[str, Any], context: str) -> str:
        for key in ("prompt", "query", "input", "message"):
            value = inputs.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                return with_context(context, "Task", text) if context else text
        return f"Canvas context:\\n{context}" if context else ""


    def flatten_inputs(inputs: Mapping[str, Any]) -> str:
        context = extract_context(inputs)
        conversation = "\\n\\n".join(build_conversation_lines(inputs))
        if conversation and context:
            return with_context(context, "Conversation", conversation)
        if conversation:
            return conversation
        return fallback_prompt(inputs, context)


    class FlattenChatPromptNode(TaskNode):
        async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
            del config
            inputs = state.get("inputs", {})
            if not isinstance(inputs, Mapping):
                return {"prompt": ""}
            return {"prompt": flatten_inputs(inputs)}


    class ExtractExternalAgentReplyNode(TaskNode):
        async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
            del config
            results = state.get("results", {})
            if not isinstance(results, Mapping):
                return {"text": ""}

            for source_result_key in (
                "claude_code_agent",
                "codex_agent",
                "gemini_agent",
            ):
                payload = results.get(source_result_key, {})
                if not isinstance(payload, Mapping):
                    continue
                for key in ("stdout", "stderr", "message"):
                    value = payload.get(key)
                    if isinstance(value, str) and value.strip():
                        return {"text": value.strip()}
            return {"text": ""}

        async def __call__(
            self,
            state: State,
            config: RunnableConfig,
        ) -> dict[str, Any]:
            runnable = self.resolved_for_run(state, config=config)
            result = await runnable.run(state, config)
            output: dict[str, Any] = {"results": {self.name: result}}
            if isinstance(result, Mapping):
                text = result.get("text")
                if isinstance(text, str) and text.strip():
                    output["messages"] = [AIMessage(content=text.strip())]
            return output


    def build_graph() -> StateGraph:
        graph = StateGraph(State)
        graph.add_node("prepare_prompt", FlattenChatPromptNode(name="prepare_prompt"))
        graph.add_node(
            "claude_code_agent",
            ClaudeCodeNode(
                name="claude_code_agent",
                prompt="{{prepare_prompt.prompt}}",
                working_directory="{{config.configurable.working_directory}}",
                timeout_seconds=1200,
            ),
        )
        graph.add_node(
            "codex_agent",
            CodexNode(
                name="codex_agent",
                prompt="{{prepare_prompt.prompt}}",
                working_directory="{{config.configurable.working_directory}}",
                timeout_seconds=1800,
            ),
        )
        graph.add_node(
            "gemini_agent",
            GeminiNode(
                name="gemini_agent",
                prompt="{{prepare_prompt.prompt}}",
                working_directory="{{config.configurable.working_directory}}",
                timeout_seconds=1800,
            ),
        )
        graph.add_node(
            "extract_reply",
            ExtractExternalAgentReplyNode(name="extract_reply"),
        )

        graph.add_edge(START, "prepare_prompt")
        graph.add_conditional_edges(
            "prepare_prompt",
            Switch(
                name="provider_route",
                value="{{inputs.model}}",
                cases=[
                    SwitchCase(match="claude_code", branch_key="claude_code"),
                    SwitchCase(match="codex", branch_key="codex"),
                    SwitchCase(match="gemini", branch_key="gemini"),
                ],
                default_branch_key="codex",
            ),
            {
                "claude_code": "claude_code_agent",
                "codex": "codex_agent",
                "gemini": "gemini_agent",
            },
        )
        graph.add_edge("claude_code_agent", "extract_reply")
        graph.add_edge("codex_agent", "extract_reply")
        graph.add_edge("gemini_agent", "extract_reply")
        graph.add_edge("extract_reply", END)
        return graph
    """
).strip()

MANAGED_VIBE_WORKFLOW_TAGS = ("orcheo-vibe-agent", "external-agent")


async def ensure_managed_vibe_workflow(
    repository: WorkflowRepository,
    workspace: Workspace,
) -> Workflow:
    """Ensure the backend-owned Orcheo Vibe workflow exists and is active."""
    workflow: Workflow | None = None

    try:
        workflow_id = await repository.resolve_workflow_ref(
            MANAGED_VIBE_WORKFLOW_HANDLE,
            include_archived=True,
        )
    except WorkflowNotFoundError:
        workflow_id = None
    else:
        workflow = await repository.get_workflow(workflow_id)
        if workflow.is_archived:
            workflow = await repository.update_workflow(
                workflow.id,
                name=None,
                description=None,
                tags=None,
                draft_access=None,
                is_archived=False,
                actor=MANAGED_VIBE_WORKFLOW_ACTOR,
            )

    if workflow is None:
        try:
            workflow = await repository.create_workflow(
                name=MANAGED_VIBE_WORKFLOW_NAME,
                handle=MANAGED_VIBE_WORKFLOW_HANDLE,
                slug=None,
                description=MANAGED_VIBE_WORKFLOW_DESCRIPTION,
                tags=[*MANAGED_VIBE_WORKFLOW_TAGS],
                draft_access=WorkflowDraftAccess.AUTHENTICATED,
                actor=MANAGED_VIBE_WORKFLOW_ACTOR,
                workspace_id=None,
            )
        except WorkflowHandleConflictError:
            workflow_id = await repository.resolve_workflow_ref(
                MANAGED_VIBE_WORKFLOW_HANDLE,
                include_archived=True,
            )
            workflow = await repository.get_workflow(workflow_id)
            if workflow.is_archived:
                workflow = await repository.update_workflow(
                    workflow.id,
                    name=None,
                    description=None,
                    tags=None,
                    draft_access=None,
                    is_archived=False,
                    actor=MANAGED_VIBE_WORKFLOW_ACTOR,
                )

    versions = await repository.list_versions(workflow.id)
    if versions:
        return workflow

    graph_payload = ingest_langgraph_script(
        MANAGED_VIBE_WORKFLOW_SCRIPT,
        entrypoint="build_graph",
    )
    await repository.create_version(
        workflow.id,
        graph=graph_payload,
        metadata={
            "source": "backend-seed",
            "template_id": MANAGED_VIBE_WORKFLOW_TEMPLATE_ID,
            "template": MANAGED_VIBE_TEMPLATE_METADATA,
        },
        runnable_config={
            "configurable": {
                "working_directory": "/workspace/agents",
            }
        },
        notes=MANAGED_VIBE_WORKFLOW_NOTES,
        created_by=MANAGED_VIBE_WORKFLOW_CREATED_BY,
    )
    return workflow
