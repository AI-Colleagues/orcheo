"""Workspace email MCP tools backed by encrypted Orcheo vault credentials."""

from __future__ import annotations
import asyncio
import inspect
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote
from fastmcp import FastMCP
from fastmcp.apps import AppConfig
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field
from orcheo_backend.app.authentication import (
    PREAUTHENTICATED_SCOPE_KEY,
    RequestContext,
)
from orcheo_backend.app.dependencies import get_vault
from orcheo_backend.app.mcp_server._shared import (
    DESTRUCTIVE,
    MCP_ACTOR,
    READ_ONLY,
    WRITE,
    WorkspaceArg,
    api_client,
    caller_subject,
)
from orcheo_backend.app.mcp_server.app_tools import load_view
from orcheo_backend.app.mcp_server.email_client import (
    EmailAccounts,
    EmailAccountSave,
    TestEmailAccount,
)
from orcheo_backend.app.mcp_server.email_errors import ServiceError
from orcheo_backend.app.mcp_server.email_mail import TOOL_SPECS
from orcheo_backend.app.mcp_server.form_tokens import (
    FORM_TOKEN_META_KEY,
    CredentialForm,
    issue_form_token,
    verify_form_token,
)
from orcheo_backend.app.mcp_server.scopes import requires


EMAIL_FORM_URI = "ui://orcheo/email-account-form"
EMAIL_ACCOUNT_SETTINGS_META_KEY = "orcheo/emailAccountSettings"
SCHEDULE_HORIZON = timedelta(days=30)


class _FormTokenArgs(BaseModel):
    form_token: str


class _FormSave(EmailAccountSave):
    form_token: str


class _FormAccount(_FormTokenArgs):
    account_id: str = Field(min_length=1, max_length=200)


class _FormAccountTest(_FormAccount):
    protocol: Literal["imap", "smtp", "both"] = "both"


def _client_id(context: RequestContext) -> str | None:
    value = context.claims.get("client_id")
    return value if isinstance(value, str) else None


def _request_context() -> RequestContext:
    context = get_http_request().scope.get(PREAUTHENTICATED_SCOPE_KEY)
    if not isinstance(context, RequestContext) or not context.is_authenticated:
        raise ToolError("Sign in to use email tools.")
    return context


async def _manager(
    workspace: str | None = None,
) -> tuple[EmailAccounts, dict[str, Any]]:
    async with api_client(workspace) as api:
        active = await api.get("/api/workspaces/active")
    return (
        EmailAccounts(get_vault(), str(active["workspace_id"]), actor=caller_subject()),
        active,
    )


async def _verify_form(
    token: str, *, workspace: str | None = None
) -> tuple[EmailAccounts, CredentialForm]:
    context = _request_context()
    form = verify_form_token(
        token,
        subject=context.subject,
        client_id=_client_id(context),
        purpose="email_account",
    )
    async with api_client(form.workspace or workspace) as api:
        active = await api.get("/api/workspaces/active")
    if form.workspace_id != str(active["workspace_id"]):
        raise ToolError("The form's workspace has changed. Open a new one.")
    return (
        EmailAccounts(get_vault(), str(active["workspace_id"]), actor=caller_subject()),
        form,
    )


def _as_account_config(
    manager: EmailAccounts, actor: str, account_id: str
) -> dict[str, Any]:
    row = manager._row(actor, account_id)
    return manager._account_result(row)


def _function_signature(
    model: type[BaseModel], *, workspace: bool = True
) -> inspect.Signature:
    params: list[inspect.Parameter] = []
    for name, field in model.model_fields.items():
        default = field.default
        if field.is_required():
            default = inspect.Parameter.empty
        elif field.default_factory is not None:
            default = field.default_factory()
        params.append(
            inspect.Parameter(
                name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default,
                annotation=field.rebuild_annotation(),
            )
        )
    if workspace:
        params.append(
            inspect.Parameter(
                "workspace",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=None,
                annotation=WorkspaceArg,
            )
        )
    return inspect.Signature(params, return_annotation=dict[str, Any])


def _register_model_tool(
    server: FastMCP,
    *,
    name: str,
    title: str,
    description: str,
    model: type[BaseModel],
    access: str,
    destructive: bool = False,
    app_only: bool = False,
    handler: Any = None,
) -> None:
    async def invoke(**kwargs: Any) -> Any:
        workspace = kwargs.pop("workspace", None)
        try:
            data = model.model_validate(kwargs).model_dump(mode="python")
            if handler is not None:
                return await handler(data, workspace)
            manager, _ = await _manager(workspace)
            actor = caller_subject()
            return await asyncio.to_thread(_dispatch, manager, name, data, actor)
        except ServiceError as error:
            raise ToolError(error.message) from error
        except ValueError as error:
            raise ToolError(str(error)) from error

    invoke.__name__ = name
    invoke.__doc__ = description
    invoke.__signature__ = _function_signature(model)  # type: ignore[attr-defined]
    annotations = (
        DESTRUCTIVE if destructive else WRITE if access == "write" else READ_ONLY
    )
    app: AppConfig | None = None
    if app_only:
        app = AppConfig(resource_uri=EMAIL_FORM_URI, visibility=["app"])
    elif name == "open_email_settings":
        app = AppConfig(resource_uri=EMAIL_FORM_URI, prefers_border=True)
    server.tool(
        name=name,
        title=title,
        description=description,
        annotations=annotations,
        tags=requires(f"email:{access}"),
        app=app,
    )(invoke)


def _dispatch(
    accounts: EmailAccounts,
    name: str,
    data: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    operations = {
        "search_emails": lambda: accounts.search(actor, data),
        "get_email": lambda: accounts.get_email(
            actor,
            data["message_id"],
            include_html=data["include_html"],
            max_body_chars=data["max_body_chars"],
        ),
        "download_email_attachment": lambda: accounts.download_attachment(
            actor, data["attachment_id"]
        ),
        "send_email": lambda: accounts.run_idempotently(
            actor,
            "send_email",
            data,
            lambda send_data: _send_message(accounts, actor, send_data),
        ),
        "mark_emails_read": lambda: accounts.mark_read(
            actor, data["message_ids"], True
        ),
        "mark_emails_unread": lambda: accounts.mark_read(
            actor, data["message_ids"], False
        ),
        "manage_email_messages": lambda: accounts.manage_messages(actor, data),
        "list_email_folders": lambda: accounts.list_folders(actor, data["account_id"]),
        "reply_to_email": lambda: accounts.reply(actor, data["message_id"], data),
        "forward_email": lambda: accounts.forward(actor, data["message_id"], data),
        "save_email_draft": lambda: accounts.run_idempotently(
            actor,
            "save_email_draft",
            data,
            lambda draft_data: accounts.save_draft(actor, draft_data),
        ),
        "send_email_draft": lambda: accounts.send_draft(actor, data["message_id"]),
        "get_email_thread": lambda: accounts.thread(
            actor, data["message_id"], data["limit"]
        ),
        "read_email_attachment": lambda: accounts.attachment_text(
            actor, data["attachment_id"], data["max_chars"]
        ),
        "respond_to_email_invitation": lambda: accounts.respond_to_invite(
            actor, data["message_id"], data["response"], data["comment"]
        ),
        "get_email_event_details": lambda: accounts.event_links(
            actor, data["message_id"]
        ),
        "find_email_contacts": lambda: accounts.find_contacts(
            actor, data["account_id"], data["query"], data["limit"]
        ),
        "list_scheduled_emails": lambda: _list_scheduled(accounts, actor),
        "cancel_scheduled_email": lambda: accounts.cancel_scheduled(
            actor, data["scheduled_id"]
        ),
        "set_default_email_account": lambda: accounts.set_default(
            actor, data["account_id"]
        ),
    }
    try:
        operation = operations[name]
    except KeyError as error:
        raise ToolError(f"Unknown email operation: {name}") from error
    return operation()


def _send_message(
    accounts: EmailAccounts, actor: str, data: dict[str, Any]
) -> dict[str, Any]:
    send_at = data.get("send_at")
    if not send_at:
        return accounts.send(actor, data)
    parsed = datetime.fromisoformat(send_at.replace("Z", "+00:00")).astimezone(UTC)
    now = datetime.now(UTC)
    if parsed <= now or parsed > now + SCHEDULE_HORIZON:
        raise ServiceError(
            "email.invalid_send_at", "Schedule between now and 30 days ahead.", 400
        )
    return accounts.schedule(actor, data, parsed.isoformat())


def _list_scheduled(accounts: EmailAccounts, actor: str) -> dict[str, Any]:
    return {"scheduled": accounts.list_scheduled(actor)}


def _register_account_tools(server: FastMCP) -> None:
    @server.resource(
        EMAIL_FORM_URI,
        name="email_account_form",
        title="Orcheo email account",
        app=AppConfig(prefers_border=True),
    )
    def email_account_form() -> str:
        return load_view("email_account_form.html")

    @server.tool(
        annotations=READ_ONLY,
        app=AppConfig(resource_uri=EMAIL_FORM_URI, prefers_border=True),
        tags=requires("email:write"),
    )
    async def open_email_settings(
        account_id: str | None = None,
        workspace: WorkspaceArg = None,
    ) -> ToolResult:
        """Open the private form for managing encrypted mailbox settings."""
        accounts, active = await _manager(workspace)
        public_accounts = await asyncio.to_thread(
            accounts.list_agent_accounts, caller_subject()
        )
        if account_id is not None and not any(
            item["account_id"] == account_id for item in public_accounts
        ):
            raise ToolError("Email account not found.")
        context = _request_context()
        token = issue_form_token(
            CredentialForm(
                subject=context.subject,
                client_id=_client_id(context),
                workspace=active["slug"],
                workspace_id=str(active["workspace_id"]),
                purpose="email_account",
            )
        )
        return ToolResult(
            content=(
                "Opened the private email account form. Mailbox passwords are "
                "entered directly into the Orcheo credential vault."
            ),
            structured_content={"accounts": public_accounts, "account_id": account_id},
            meta={FORM_TOKEN_META_KEY: token},
        )

    @server.tool(annotations=READ_ONLY, tags=requires("email:read"))
    async def list_email_accounts(workspace: WorkspaceArg = None) -> dict[str, Any]:
        """List linked email accounts without server settings or passwords."""
        accounts, _ = await _manager(workspace)
        public_accounts = await asyncio.to_thread(
            accounts.list_agent_accounts, caller_subject()
        )
        return {"accounts": public_accounts}

    _register_private_account_actions(server)


def _register_private_account_actions(server: FastMCP) -> None:  # noqa: C901
    async def get_settings(data: dict[str, Any], workspace: str | None) -> ToolResult:
        manager, form = await _verify_form(data["form_token"], workspace=workspace)
        if form.credential_id and form.credential_id != data["account_id"]:
            raise ToolError("This form is bound to another email account.")
        account = await asyncio.to_thread(
            _as_account_config, manager, caller_subject(), data["account_id"]
        )
        return ToolResult(
            content=(
                "Loaded mailbox settings. Password fields are blank because saved "
                "passwords cannot be recovered."
            ),
            structured_content={"account": account},
            meta={EMAIL_ACCOUNT_SETTINGS_META_KEY: {"account": account}},
        )

    async def save_account(
        data: dict[str, Any], workspace: str | None
    ) -> dict[str, Any]:
        manager, form = await _verify_form(data.pop("form_token"), workspace=workspace)
        if form.credential_id and form.credential_id != data.get("account_id"):
            raise ToolError("This form is bound to another email account.")
        parsed = EmailAccountSave.model_validate(data)
        current, config = await asyncio.to_thread(
            manager.prepare_account, caller_subject(), parsed.model_dump(mode="python")
        )
        name = f"mailbox-{parsed.email_address.lower()}"
        payload = {
            "name": name,
            "provider": manager.ACCOUNT_PROVIDER,
            "secret": json.dumps(config, separators=(",", ":")),
            "actor": MCP_ACTOR,
            "access": "shared",
            "scopes": ["email:imap", "email:smtp"],
        }
        async with api_client(form.workspace) as api:
            if current is None:
                saved = await api.post("/api/credentials", json_body=payload)
                account_id = str(saved["id"])
            else:
                await api.request(
                    "PATCH",
                    f"/api/credentials/"
                    f"{quote(str(current['_credential'].id), safe='')}",
                    json_body=payload,
                )
                account_id = str(current["_credential"].id)
        if parsed.is_default:
            await asyncio.to_thread(manager.set_default, caller_subject(), account_id)
        else:
            await asyncio.to_thread(manager._ensure_default, caller_subject())
        account = await asyncio.to_thread(
            _as_account_config, manager, caller_subject(), account_id
        )
        return {"saved": True, "account": account}

    async def test_account(
        data: dict[str, Any], workspace: str | None
    ) -> dict[str, Any]:
        manager, form = await _verify_form(data["form_token"], workspace=workspace)
        if form.credential_id and form.credential_id != data["account_id"]:
            raise ToolError("This form is bound to another email account.")
        parsed = TestEmailAccount(
            account_id=data["account_id"], protocol=data["protocol"]
        )
        return await asyncio.to_thread(
            manager.test_account, caller_subject(), parsed.account_id, parsed.protocol
        )

    async def delete_account(
        data: dict[str, Any], workspace: str | None
    ) -> dict[str, Any]:
        manager, form = await _verify_form(data["form_token"], workspace=workspace)
        if form.credential_id and form.credential_id != data["account_id"]:
            raise ToolError("This form is bound to another email account.")
        async with api_client(form.workspace) as api:
            await api.delete(f"/api/credentials/{quote(data['account_id'], safe='')}")
        await asyncio.to_thread(manager._ensure_default, caller_subject())
        return {"deleted": True, "account_id": data["account_id"]}

    _register_model_tool(
        server,
        name="get_email_account_settings",
        title="Load email account settings",
        description="Load non-secret mailbox settings in the private form.",
        model=_FormAccount,
        access="read",
        app_only=True,
        handler=get_settings,
    )
    _register_model_tool(
        server,
        name="save_email_account",
        title="Save an email account",
        description=(
            "Save mailbox settings and encrypted SMTP and IMAP passwords to the "
            "Orcheo vault."
        ),
        model=_FormSave,
        access="write",
        app_only=True,
        handler=save_account,
    )
    _register_model_tool(
        server,
        name="test_email_account",
        title="Test an email account connection",
        description="Test the selected mailbox's IMAP, SMTP, or both connections.",
        model=_FormAccountTest,
        access="write",
        app_only=True,
        handler=test_account,
    )
    _register_model_tool(
        server,
        name="delete_email_account",
        title="Remove an email account",
        description="Remove a linked mailbox and its encrypted vault credential.",
        model=_FormAccount,
        access="write",
        destructive=True,
        app_only=True,
        handler=delete_account,
    )


def register_email_tools(server: FastMCP) -> None:
    """Register all generic email client and account management tools."""
    _register_account_tools(server)
    for name, (
        title,
        description,
        model,
        access,
        destructive,
        app_only,
    ) in TOOL_SPECS.items():
        if name in {
            "open_email_settings",
            "list_email_accounts",
            "get_email_account_settings",
            "save_email_account",
            "test_email_account",
            "delete_email_account",
        }:
            continue
        _register_model_tool(
            server,
            name=name,
            title=title,
            description=description,
            model=model,
            access=access,
            destructive=destructive,
            app_only=app_only,
        )


__all__ = ["EMAIL_FORM_URI", "register_email_tools"]
