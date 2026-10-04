"""Service token metadata and private MCP App creation/revocation flows."""

from __future__ import annotations
from typing import Annotated, Any, Literal
from urllib.parse import quote
from fastmcp import FastMCP
from fastmcp.apps import AppConfig
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp.tools import ToolResult
from pydantic import Field
from orcheo_backend.app.authentication import (
    PREAUTHENTICATED_SCOPE_KEY,
    RequestContext,
)
from orcheo_backend.app.mcp_server._shared import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITE,
    WorkspaceArg,
    api_client,
)
from orcheo_backend.app.mcp_server.app_tools import load_view
from orcheo_backend.app.mcp_server.form_tokens import (
    FORM_TOKEN_META_KEY,
    CredentialForm,
    issue_form_token,
    verify_form_token,
)
from orcheo_backend.app.mcp_server.scopes import requires


SERVICE_TOKEN_FORM_URI = "ui://orcheo/service-token-form"
SERVICE_TOKEN_SECRET_META_KEY = "orcheo/serviceTokenSecret"
_TOKEN_PATH = "/api/admin/service-tokens"
_PUBLIC_FIELDS = (
    "identifier",
    "name",
    "scopes",
    "workspace_ids",
    "issued_at",
    "expires_at",
    "last_used_at",
    "use_count",
    "revoked_at",
    "revocation_reason",
    "rotated_to",
)
ExpiryArg = Annotated[int | None, Field(ge=60)]


def _public_token(record: dict[str, Any]) -> dict[str, Any]:
    """Allow only metadata into model-visible results, excluding even previews."""
    return {key: record[key] for key in _PUBLIC_FIELDS if key in record}


def _caller() -> RequestContext:
    context = get_http_request().scope.get(PREAUTHENTICATED_SCOPE_KEY)
    if not isinstance(context, RequestContext) or not context.is_authenticated:
        raise ToolError("Sign in to manage service tokens.")
    return context


def _client_id(context: RequestContext) -> str | None:
    client_id = context.claims.get("client_id")
    return client_id if isinstance(client_id, str) else None


async def _verify_form(token: str, action: str) -> CredentialForm:
    context = _caller()
    form = verify_form_token(
        token,
        subject=context.subject,
        client_id=_client_id(context),
        purpose=f"service_token_{action}",
    )
    async with api_client(form.workspace) as api:
        active = await api.get("/api/workspaces/active")
    if form.workspace_id != str(active["workspace_id"]):
        raise ToolError("The form's workspace has changed. Open a new form.")
    return form


def register_service_token_tools(server: FastMCP) -> None:
    """Register metadata tools and an App-only token mutation flow."""

    @server.resource(
        SERVICE_TOKEN_FORM_URI,
        name="service_token_form",
        title="Orcheo service token",
        app=AppConfig(prefers_border=True),
    )
    def service_token_form() -> str:
        return load_view("service_token_form.html")

    @server.tool(annotations=READ_ONLY, tags=requires("admin:tokens:read"))
    async def list_service_tokens(workspace: WorkspaceArg = None) -> dict[str, Any]:
        """List service token metadata in the selected workspace, without secrets."""
        async with api_client(workspace) as api:
            result = await api.get(_TOKEN_PATH)
        tokens = [_public_token(record) for record in result["tokens"]]
        return {"tokens": tokens, "total": len(tokens)}

    @server.tool(annotations=READ_ONLY, tags=requires("admin:tokens:read"))
    async def get_service_token(
        token_id: str, workspace: WorkspaceArg = None
    ) -> dict[str, Any]:
        """Read a token's metadata. Existing secrets cannot be recovered."""
        async with api_client(workspace) as api:
            return _public_token(
                await api.get(f"{_TOKEN_PATH}/{quote(token_id, safe='')}")
            )

    _register_form_opener(server)
    _register_form_actions(server)


def _register_form_opener(server: FastMCP) -> None:
    @server.tool(
        annotations=READ_ONLY,
        app=AppConfig(resource_uri=SERVICE_TOKEN_FORM_URI),
        tags=requires("admin:tokens:write"),
    )
    async def open_service_token_form(
        action: Literal["create", "revoke"] = "create",
        token_id: str | None = None,
        name: str | None = None,
        scopes: list[str] | None = None,
        expires_in_seconds: ExpiryArg = None,
        workspace: WorkspaceArg = None,
    ) -> ToolResult:
        """Open a private form to create or revoke a workspace service token.

        The user confirms the action in the App. A newly minted secret is
        shown only there, once, for copying. Existing token secrets are hashed
        and cannot be revealed. Never ask for or return a secret in chat.
        To replace a token, create one here, then open a revoke form for the old ID.
        """
        if (action == "revoke") != (token_id is not None):
            raise ToolError("Supply token_id only when revoking a token.")
        context = _caller()
        if set(scopes or []) - context.scopes:
            raise ToolError("Select only permissions approved for this application.")
        async with api_client(workspace) as api:
            active = await api.get("/api/workspaces/active")
            existing = (
                _public_token(
                    await api.get(f"{_TOKEN_PATH}/{quote(token_id, safe='')}")
                )
                if token_id is not None
                else None
            )
        token = issue_form_token(
            CredentialForm(
                subject=context.subject,
                client_id=_client_id(context),
                workspace=active["slug"],
                workspace_id=str(active["workspace_id"]),
                credential_id=None,
                service_token_id=token_id,
                purpose=f"service_token_{action}",
            )
        )
        return ToolResult(
            content=f"Opened a private form to {action} a service token. "
            "The user confirms there; token secrets never appear in chat.",
            structured_content={
                "action": action,
                "workspace": active["slug"],
                "token": existing,
                "name": name,
                "scopes": scopes or [],
                "available_scopes": sorted(context.scopes),
                "expires_in_seconds": expires_in_seconds,
            },
            meta={FORM_TOKEN_META_KEY: token},
        )


def _register_form_actions(server: FastMCP) -> None:
    @server.tool(
        annotations=WRITE,
        app=AppConfig(resource_uri=SERVICE_TOKEN_FORM_URI, visibility=["app"]),
        tags=requires("admin:tokens:write"),
    )
    async def create_service_token(
        form_token: str,
        scopes: list[str],
        name: str | None = None,
        expires_in_seconds: ExpiryArg = None,
    ) -> ToolResult:
        """Create a token from the private App; return its secret only in _meta."""
        form = await _verify_form(form_token, "create")
        async with api_client(form.workspace) as api:
            saved = await api.post(
                _TOKEN_PATH,
                json_body={
                    "name": name,
                    "scopes": scopes,
                    "expires_in_seconds": expires_in_seconds,
                },
            )
        secret = saved.get("secret")
        if not isinstance(secret, str) or not secret:
            raise ToolError("The server did not return a new token secret.")
        return ToolResult(
            content="Service token created. Its secret is shown only in the App.",
            structured_content=_public_token(saved),
            meta={SERVICE_TOKEN_SECRET_META_KEY: secret},
        )

    @server.tool(
        annotations=DESTRUCTIVE,
        app=AppConfig(resource_uri=SERVICE_TOKEN_FORM_URI, visibility=["app"]),
        tags=requires("admin:tokens:write"),
    )
    async def revoke_service_token(
        form_token: str, reason: Annotated[str, Field(min_length=1)]
    ) -> ToolResult:
        """Revoke only the token bound to the private form after user confirmation."""
        form = await _verify_form(form_token, "revoke")
        if not form.service_token_id:
            raise ToolError("This form has no service token to revoke.")
        if not reason.strip():
            raise ToolError("Enter a reason for revoking this token.")
        async with api_client(form.workspace) as api:
            await api.request(
                "DELETE",
                f"{_TOKEN_PATH}/{quote(form.service_token_id, safe='')}",
                json_body={"reason": reason},
            )
        return ToolResult(
            content="Service token revoked.",
            structured_content={"identifier": form.service_token_id, "revoked": True},
        )
