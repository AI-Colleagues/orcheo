"""The private mailbox form and tool error handling, over the MCP endpoint."""

from __future__ import annotations
from typing import Any
import pytest
from fastapi.testclient import TestClient
from orcheo.vault import CredentialNotFoundError
from orcheo_backend.app.authentication import RequestContext
from orcheo_backend.app.errors import WorkspaceQuotaExceededError
from orcheo_backend.app.mcp_server import email_tools
from orcheo_backend.app.mcp_server.email_tools import EMAIL_FORM_URI
from orcheo_backend.app.mcp_server.form_tokens import (
    FORM_TOKEN_META_KEY,
    CredentialForm,
    issue_form_token,
)
from orcheo_backend.app.routers.mcp import authenticate_mcp_request
from tests.backend.api.mcp_support import McpCaller, mcp_session
from tests.backend.mcp_email_support import (
    FakeIMAP,
    FakeSMTP,
    account_payload,
    install_servers,
)


def _sign_in(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    identity_type: str = "user",
) -> None:
    monkeypatch.setitem(
        api_client.app.dependency_overrides,
        authenticate_mcp_request,
        lambda: RequestContext(
            subject="anonymous",
            identity_type=identity_type,
            claims={"client_id": "test-mcp"},
            scopes=frozenset({"email:read", "email:write"}),
        ),
    )


def _payload(token: str, **overrides: Any) -> dict[str, Any]:
    return {"form_token": token, **account_payload(**overrides)}


async def _open(mcp: McpCaller, **arguments: Any) -> tuple[str, dict[str, Any]]:
    opened = await mcp.call_result("open_email_settings", arguments)
    return opened["_meta"][FORM_TOKEN_META_KEY], opened["structuredContent"]


def _token(api_client: TestClient, **fields: Any) -> str:
    active = api_client.get("/api/workspaces/active").json()
    form: dict[str, Any] = {
        "subject": "anonymous",
        "client_id": "test-mcp",
        "workspace": active["slug"],
        "workspace_id": active["workspace_id"],
        "credential_id": None,
        "purpose": "email_account",
        **fields,
    }
    return issue_form_token(CredentialForm(**form))


@pytest.mark.asyncio()
async def test_email_tools_need_a_signed_in_user(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_in(api_client, monkeypatch, identity_type="anonymous")
    async with mcp_session(api_client.app) as mcp:
        assert "Sign in to use email tools" in await mcp.call_error(
            "open_email_settings"
        )
        token = _token(api_client)
        error = await mcp.call_error("save_email_account", _payload(token))
        assert "Sign in to use email tools" in error


@pytest.mark.asyncio()
async def test_accounts_are_listed_and_the_form_can_open_one_by_id(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_in(api_client, monkeypatch)
    async with mcp_session(api_client.app) as mcp:
        assert await mcp.call("list_email_accounts") == {"accounts": []}
        token, _ = await _open(mcp)
        saved = await mcp.call("save_email_account", _payload(token))
        account_id = saved["account"]["account_id"]

        listed = (await mcp.call("list_email_accounts"))["accounts"]
        assert [item["account_id"] for item in listed] == [account_id]
        assert "imap" not in listed[0]
        assert "password" not in str(listed)

        _, opened = await _open(mcp, account_id=account_id)
        assert opened["account_id"] == account_id
        assert [item["account_id"] for item in opened["accounts"]] == [account_id]
        assert "Email account not found" in await mcp.call_error(
            "open_email_settings", {"account_id": "missing"}
        )

        resource = await mcp.rpc("resources/read", {"uri": EMAIL_FORM_URI})
        [content] = resource.json()["result"]["contents"]
        assert "Email accounts" in content["text"]


@pytest.mark.asyncio()
async def test_a_form_bound_to_one_mailbox_cannot_touch_another(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_in(api_client, monkeypatch)
    async with mcp_session(api_client.app) as mcp:
        token, _ = await _open(mcp)
        first = (await mcp.call("save_email_account", _payload(token)))["account"]
        second = (
            await mcp.call(
                "save_email_account",
                _payload(token, email_address="two@example.org"),
            )
        )["account"]
        bound = _token(api_client, credential_id=first["account_id"])
        other = second["account_id"]
        message = "This form is bound to another email account."

        for tool, arguments in (
            ("get_email_account_settings", {"account_id": other}),
            ("test_email_account", {"account_id": other}),
            ("delete_email_account", {"account_id": other}),
            ("save_email_account", account_payload(account_id=other)),
        ):
            assert message in await mcp.call_error(
                tool, {"form_token": bound, **arguments}
            )

        own = await mcp.call(
            "get_email_account_settings",
            {"form_token": bound, "account_id": first["account_id"]},
        )
        assert own["account"]["email_address"] == "me@example.org"


@pytest.mark.asyncio()
async def test_a_form_from_another_workspace_is_refused(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_in(api_client, monkeypatch)
    stale = _token(api_client, workspace_id="00000000-0000-0000-0000-000000000000")
    async with mcp_session(api_client.app) as mcp:
        error = await mcp.call_error("save_email_account", _payload(stale))
    assert "The form's workspace has changed" in error


@pytest.mark.asyncio()
async def test_the_form_can_test_a_mailbox_connection(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_in(api_client, monkeypatch)
    install_servers(monkeypatch, FakeIMAP(), FakeSMTP())
    async with mcp_session(api_client.app) as mcp:
        token, _ = await _open(mcp)
        saved = await mcp.call("save_email_account", _payload(token))
        account_id = saved["account"]["account_id"]
        result = await mcp.call(
            "test_email_account",
            {"form_token": token, "account_id": account_id, "protocol": "smtp"},
        )
        assert result["connections"] == {
            "smtp": {"status": "connected", "message": "Connection successful."}
        }
        everything = await mcp.call(
            "test_email_account", {"form_token": token, "account_id": account_id}
        )
        assert set(everything["connections"]) == {"imap", "smtp"}


@pytest.mark.asyncio()
async def test_tool_failures_reach_the_client_as_plain_errors(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_in(api_client, monkeypatch)

    async def over_quota(*args: Any, **kwargs: Any) -> None:
        raise WorkspaceQuotaExceededError(
            "Workspace reached its credential quota",
            code="workspace.quota.credentials",
            details={},
        )

    async with mcp_session(api_client.app) as mcp:
        # A mailbox failure from the client library.
        assert "Email identifier is invalid" in await mcp.call_error(
            "get_email", {"message_id": "***"}
        )
        # Argument rules the JSON schema cannot express.
        invalid = await mcp.call_error(
            "send_email", {"subject": "s", "to": ["nope"], "body_text": "b"}
        )
        assert "Use email addresses" in invalid
        # A workspace credential quota.
        monkeypatch.setattr(
            email_tools, "ensure_workspace_credential_quota", over_quota
        )
        token, _ = await _open(mcp)
        quota = await mcp.call_error("save_email_account", _payload(token))
        assert quota == "Workspace reached its credential quota"
        # Nothing is configured, so a send has no mailbox to use.
        assert "Add an email account first" in await mcp.call_error(
            "send_email", {"subject": "s", "to": ["a@example.org"], "body_text": "b"}
        )
        assert await mcp.call("list_scheduled_emails") == {"scheduled": []}


@pytest.mark.asyncio()
async def test_a_mailbox_removed_mid_request_is_not_an_internal_error(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_in(api_client, monkeypatch)

    def vanished(self: Any, actor_id: str) -> None:
        raise CredentialNotFoundError("Credential was not found.")

    monkeypatch.setattr(email_tools.EmailAccounts, "list_scheduled", vanished)
    async with mcp_session(api_client.app) as mcp:
        error = await mcp.call_error("list_scheduled_emails")
    assert error == "Stored email settings changed during this request. Try again."
