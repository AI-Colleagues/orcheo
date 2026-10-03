"""Webhook controls that keep authentication secrets out of model messages."""

from __future__ import annotations
from typing import Any
from fastmcp import FastMCP
from fastmcp.apps import AppConfig
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from pydantic import BaseModel, ConfigDict, Field
from orcheo.triggers.webhook import RateLimitConfig
from orcheo_backend.app.mcp_server._shared import (
    IDEMPOTENT_WRITE,
    READ_ONLY,
    WorkflowArg,
    WorkspaceArg,
    api_client,
    caller_subject,
)
from orcheo_backend.app.mcp_server.api_client import InProcessApiClient, McpApiError
from orcheo_backend.app.mcp_server.app_tools import load_view
from orcheo_backend.app.mcp_server.form_tokens import (
    FORM_TOKEN_META_KEY,
    CredentialForm,
    issue_form_token,
    verify_form_token,
)
from orcheo_backend.app.mcp_server.scopes import requires


WEBHOOK_FORM_URI = "ui://orcheo/webhook-form"


class WebhookSettings(BaseModel):
    """Nonsecret settings agents can change while retaining authentication."""

    model_config = ConfigDict(extra="forbid")
    allowed_methods: list[str] = Field(default_factory=lambda: ["GET", "POST"])
    rate_limit: RateLimitConfig | None = None
    hmac_algorithm: str = "sha256"
    hmac_timestamp_header: str | None = None
    hmac_tolerance_seconds: int = Field(default=300, ge=0)


def _redact(config: dict[str, Any]) -> dict[str, Any]:
    """Return safe metadata, excluding all secret and header/query values."""
    hidden = {
        "shared_secret",
        "hmac_secret",
        "required_headers",
        "required_query_params",
    }
    return {
        **{key: value for key, value in config.items() if key not in hidden},
        "has_shared_secret": bool(config.get("shared_secret")),
        "has_hmac_secret": bool(config.get("hmac_secret")),
        "required_header_names": list(config.get("required_headers", {})),
        "required_query_param_names": list(config.get("required_query_params", {})),
    }


async def _save(
    api: InProcessApiClient,
    workflow: str,
    changes: dict[str, Any],
) -> dict[str, Any]:
    path = f"/api/workflows/{workflow}/triggers/webhook/config"
    current = await api.get(path)
    try:
        saved = await api.put(path, json_body={**current, **changes})
    except McpApiError as exc:
        # API validation errors can echo input secrets. Do not relay their body.
        raise ToolError(
            f"Webhook configuration could not be saved (HTTP {exc.status_code}). "
            "Check header/secret pairs, methods, HMAC algorithm and rate limits."
        ) from None
    return _redact(saved)


def register_webhook_tools(server: FastMCP) -> None:
    """Register safe webhook controls and the user-only authentication form."""

    @server.resource(
        WEBHOOK_FORM_URI,
        name="webhook_form",
        title="Orcheo webhook authentication",
        app=AppConfig(prefers_border=True),
    )
    def webhook_form() -> str:
        return load_view("webhook_form.html")

    @server.tool(annotations=READ_ONLY, tags=requires("workflows:read"))
    async def get_webhook_config(
        workflow: WorkflowArg,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Read webhook settings with secrets and header/query values redacted."""
        async with api_client(workspace) as api:
            return _redact(
                await api.get(f"/api/workflows/{workflow}/triggers/webhook/config")
            )

    @server.tool(annotations=IDEMPOTENT_WRITE, tags=requires("workflows:write"))
    async def configure_webhook(
        workflow: WorkflowArg,
        settings: WebhookSettings,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Change supplied webhook settings, preserving existing authentication.

        Use open_webhook_form for secrets and required header/query values.
        """
        async with api_client(workspace) as api:
            return await _save(
                api, workflow, settings.model_dump(mode="json", exclude_unset=True)
            )

    @server.tool(
        annotations=READ_ONLY,
        tags=requires("workflows:write"),
        app=AppConfig(resource_uri=WEBHOOK_FORM_URI),
    )
    async def open_webhook_form(
        workflow: WorkflowArg,
        workspace: WorkspaceArg = None,
    ) -> ToolResult:
        """Open a form for the user to set webhook authentication privately."""
        async with api_client(workspace) as api:
            record = await api.get(f"/api/workflows/{workflow}")
            active = await api.get("/api/workspaces/active")
            config = await api.get(
                f"/api/workflows/{record['id']}/triggers/webhook/config"
            )
        token = issue_form_token(
            CredentialForm(
                subject=caller_subject(),
                workspace=active["slug"],
                credential_id=None,
                purpose="webhook",
                workflow_id=record["id"],
            )
        )
        return ToolResult(
            content="Opened webhook authentication form. Enter secrets in the form.",
            structured_content={"workflow_id": record["id"], "config": _redact(config)},
            meta={FORM_TOKEN_META_KEY: token},
        )

    @server.tool(
        annotations=IDEMPOTENT_WRITE,
        tags=requires("workflows:write"),
        app=AppConfig(resource_uri=WEBHOOK_FORM_URI, visibility=["app"]),
    )
    async def save_webhook_config(
        form_token: str,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        """Save user-entered webhook settings from the signed form only."""
        form = verify_form_token(
            form_token,
            subject=caller_subject(),
            purpose="webhook",
        )
        if form.workflow_id is None:
            raise ToolError("Open a new webhook form.")
        async with api_client(form.workspace) as api:
            return await _save(api, form.workflow_id, changes)
