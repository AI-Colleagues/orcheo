"""MCP Apps: interactive views rendered by the MCP host next to tool results.

Credential secrets are entered in an embedded form and submitted by the form
itself through an app-only tool, so they never pass through the model. The
workflow diagram view renders a version's Mermaid graph.
"""

from __future__ import annotations
import re
from functools import cache
from importlib.resources import files
from typing import Annotated, Any, Literal
from fastmcp import FastMCP
from fastmcp.apps import AppConfig, ResourceCSP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from pydantic import Field
from orcheo_backend.app.mcp_server._shared import (
    MCP_ACTOR,
    READ_ONLY,
    WRITE,
    VersionArg,
    WorkflowArg,
    WorkspaceArg,
    api_client,
    caller_subject,
    fetch_version,
)
from orcheo_backend.app.mcp_server.form_tokens import (
    FORM_TOKEN_META_KEY,
    CredentialForm,
    issue_form_token,
    verify_form_token,
)
from orcheo_backend.app.mcp_server.scopes import requires


CREDENTIAL_FORM_URI = "ui://orcheo/credential-form"
WORKFLOW_DIAGRAM_URI = "ui://orcheo/workflow-diagram"
MERMAID_CDN = "https://cdn.jsdelivr.net"
_BRIDGE_MARKER = "/* ORCHEO_BRIDGE */"

AccessArg = Annotated[
    Literal["scoped", "shared"] | None,
    Field(
        description=(
            "'scoped' limits use to 'workflow'; 'shared' allows every workflow in "
            "the workspace. Defaults to 'scoped' when a workflow is given, "
            "otherwise 'shared'."
        )
    ),
]
OptionalWorkflowArg = Annotated[
    str | None,
    Field(description="Workflow ID or handle the credential is scoped to."),
]


@cache
def load_view(name: str) -> str:
    """Return a view's HTML with the shared MCP Apps bridge inlined."""
    ui = files("orcheo_backend.app.mcp_server") / "ui"
    bridge = (ui / "bridge.js").read_text(encoding="utf-8")
    return (ui / name).read_text(encoding="utf-8").replace(_BRIDGE_MARKER, bridge)


def _result(
    text: str,
    structured: dict[str, Any],
    meta: dict[str, Any] | None = None,
) -> ToolResult:
    # Both content and structured_content may reach the model. Only meta is
    # private to the embedded view; never put secrets in either content field.
    return ToolResult(content=text, structured_content=structured, meta=meta)


def _register_views(server: FastMCP) -> None:
    @server.resource(
        CREDENTIAL_FORM_URI,
        name="credential_form",
        title="Orcheo credential form",
        app=AppConfig(prefers_border=True),
    )
    def credential_form() -> str:
        return load_view("credential_form.html")

    @server.resource(
        WORKFLOW_DIAGRAM_URI,
        name="workflow_diagram",
        title="Orcheo workflow diagram",
        app=AppConfig(
            prefers_border=True,
            csp=ResourceCSP(resource_domains=[MERMAID_CDN]),
        ),
    )
    def workflow_diagram() -> str:
        return load_view("workflow_diagram.html")


def _register_credential_tools(server: FastMCP) -> None:
    @server.tool(
        annotations=READ_ONLY,
        app=AppConfig(resource_uri=CREDENTIAL_FORM_URI),
        tags=requires("vault:write"),
    )
    async def open_credential_form(
        name: Annotated[
            str | None, Field(description="Suggested name, referenced as [[name]].")
        ] = None,
        provider: Annotated[
            str | None, Field(description="Suggested provider, e.g. 'openai'.")
        ] = None,
        credential_id: Annotated[
            str | None,
            Field(description="Existing credential to update instead of creating."),
        ] = None,
        workflow: OptionalWorkflowArg = None,
        access: AccessArg = None,
        workspace: WorkspaceArg = None,
    ) -> ToolResult:
        """Show the user a form to add or update a credential in the vault.

        The user types the secret into the form, which saves it directly; you
        never see it and cannot save credentials yourself. Do not ask the user
        to paste secrets into the chat.
        """
        prefill: dict[str, Any] = {
            "name": name,
            "provider": provider,
            "workflow": workflow,
            "access": access,
            "credential_id": credential_id,
        }
        async with api_client(workspace) as api:
            if credential_id is not None:
                existing = await api.get(
                    "/api/credentials", params={"workflow_id": workflow}
                )
                match = next(
                    (item for item in existing if str(item["id"]) == credential_id),
                    None,
                )
                if match is None:
                    raise ToolError(f"Credential '{credential_id}' was not found.")
                prefill.update(
                    name=name or match["name"],
                    provider=provider or match["provider"],
                    access=access or match.get("access"),
                )
            active = await api.get("/api/workspaces/active")
        token = issue_form_token(
            CredentialForm(
                subject=caller_subject(),
                workspace=active["slug"],
                credential_id=credential_id,
            )
        )
        action = "update" if credential_id else "add"
        label = f" '{prefill['name']}'" if prefill["name"] else ""
        return _result(
            f"Opened a form for the user to {action} the credential{label} in "
            f"workspace '{active['slug']}'. The user enters the secret there and "
            "saves it; you will be told when it is saved.",
            {key: value for key, value in prefill.items() if value is not None},
            {FORM_TOKEN_META_KEY: token},
        )

    @server.tool(
        annotations=WRITE,
        app=AppConfig(resource_uri=CREDENTIAL_FORM_URI, visibility=["app"]),
        tags=requires("vault:write"),
    )
    async def save_credential(
        form_token: str,
        name: str,
        provider: str,
        secret: str | None = None,
        access: AccessArg = None,
        workflow: OptionalWorkflowArg = None,
    ) -> ToolResult:
        """Save a credential submitted from the Orcheo credential form.

        Only the credential form can call this tool; it requires the form's
        token, which is never shown to the model.
        """
        form = verify_form_token(form_token, subject=caller_subject())
        resolved_access = access or ("scoped" if workflow is not None else "shared")
        payload: dict[str, Any] = {
            "name": name,
            "provider": provider,
            "access": resolved_access,
            "actor": MCP_ACTOR,
        }
        if workflow is not None:
            payload["workflow_id"] = workflow
        async with api_client(form.workspace) as api:
            if form.credential_id is None:
                if not secret:
                    raise ToolError("Enter a secret for the new credential.")
                saved = await api.post(
                    "/api/credentials", json_body={**payload, "secret": secret}
                )
            else:
                if secret:
                    payload["secret"] = secret
                saved = await api.request(
                    "PATCH",
                    f"/api/credentials/{form.credential_id}",
                    json_body=payload,
                )
        return _result(f"Saved credential '{saved['name']}'.", saved)


def _one_line(text: str) -> str:
    """Collapse whitespace so user text cannot start new lines."""
    return " ".join(text.split())


def _fenced(language: str, body: str) -> str:
    """Wrap ``body`` in a code fence longer than any backtick run inside it.

    The body is user-authored, so a fixed fence could be closed early and
    let the rest reach the model as text outside the block.
    """
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{body}\n{fence}"


def _register_diagram_tool(server: FastMCP) -> None:
    @server.tool(
        annotations=READ_ONLY,
        app=AppConfig(resource_uri=WORKFLOW_DIAGRAM_URI),
        tags=requires("workflows:read"),
    )
    async def show_workflow_diagram(
        workflow: WorkflowArg,
        version: VersionArg = None,
        workspace: WorkspaceArg = None,
    ) -> ToolResult:
        """Show a workflow version's graph as a Mermaid diagram."""
        async with api_client(workspace) as api:
            record = await api.get(f"/api/workflows/{workflow}")
            selected = await fetch_version(api, workflow, version)
            number = selected["version"]
            rendered = await api.get(
                f"/api/workflows/{workflow}/versions/{number}/mermaid"
            )
        mermaid = rendered["mermaid"]
        return _result(
            f"Workflow '{_one_line(record['name'])}' v{number}:\n"
            f"{_fenced('mermaid', mermaid)}",
            {
                "workflow_id": record["id"],
                "name": record["name"],
                "version": number,
                "mermaid": mermaid,
            },
        )


def register_app_tools(server: FastMCP) -> None:
    """Register the MCP Apps views and the tools that open them."""
    _register_views(server)
    _register_credential_tools(server)
    _register_diagram_tool(server)


__all__ = [
    "CREDENTIAL_FORM_URI",
    "WORKFLOW_DIAGRAM_URI",
    "load_view",
    "register_app_tools",
]
