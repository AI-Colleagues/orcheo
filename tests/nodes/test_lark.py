"""Tests for Lark nodes."""

from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.lark import LarkSendMessageNode, LarkTenantAccessTokenNode


@pytest.mark.asyncio
async def test_lark_tenant_access_token_node_returns_normalized_payload() -> None:
    """The standard Lark token node should normalize the auth response shape."""
    node = LarkTenantAccessTokenNode(
        name="get_lark_tenant_token",
        app_id="cli_app_id",
        app_secret="app_secret",
    )

    token_response = MagicMock()
    token_response.json.return_value = {
        "code": 0,
        "msg": "success",
        "tenant_access_token": "tenant_token",
        "expire": 7200,
    }
    token_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=token_response)

    with patch("orcheo.nodes.lark.httpx.AsyncClient", return_value=mock_client):
        result = await node.run(
            State(messages=[], inputs={}, results={}), RunnableConfig()
        )

    assert result == {
        "tenant_access_token": "tenant_token",
        "expire": 7200,
        "code": 0,
        "msg": "success",
        "json": {
            "code": 0,
            "msg": "success",
            "tenant_access_token": "tenant_token",
            "expire": 7200,
        },
    }


@pytest.mark.asyncio
async def test_lark_send_message_replies_in_thread() -> None:
    """Reply sends should use the message reply endpoint."""
    node = LarkSendMessageNode(
        name="send_lark",
        app_id="cli_app_id",
        app_secret="app_secret",
        reply_to_message_id="om_message",
        thread_id="omt_thread",
        message='Hello "Lark"',
    )

    state = State(
        messages=[],
        inputs={},
        results={
            "get_lark_tenant_token": {"json": {"tenant_access_token": "tenant_token"}}
        },
    )

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "code": 0,
        "msg": "success",
        "data": {"message_id": "om_reply"},
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("orcheo.nodes.lark.httpx.AsyncClient", return_value=mock_client):
        result = await node.run(state, RunnableConfig())

    assert result["is_error"] is False
    call_args = mock_client.post.call_args
    assert call_args.args[0].endswith("/im/v1/messages/om_message/reply")
    assert call_args.kwargs["json"]["reply_in_thread"] is True
    assert call_args.kwargs["json"]["content"] == '{"text": "Hello \\"Lark\\""}'


@pytest.mark.asyncio
async def test_lark_send_message_fetches_token_for_new_message() -> None:
    """New-message sends should fetch a tenant token when no prior result exists."""
    node = LarkSendMessageNode(
        name="send_lark",
        app_id="cli_app_id",
        app_secret="app_secret",
        receive_id="oc_chat",
        message="Hello chat",
    )

    state = State(messages=[], inputs={}, results={})

    token_response = MagicMock()
    token_response.json.return_value = {
        "code": 0,
        "msg": "success",
        "tenant_access_token": "tenant_token",
    }
    token_response.raise_for_status = MagicMock()

    send_response = MagicMock()
    send_response.json.return_value = {
        "code": 0,
        "msg": "success",
        "data": {"message_id": "om_sent"},
    }
    send_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(side_effect=[token_response, send_response])

    with patch("orcheo.nodes.lark.httpx.AsyncClient", return_value=mock_client):
        result = await node.run(state, RunnableConfig())

    assert result["is_error"] is False
    first_call = mock_client.post.call_args_list[0]
    second_call = mock_client.post.call_args_list[1]
    assert first_call.args[0].endswith("/auth/v3/tenant_access_token/internal")
    assert second_call.args[0].endswith("/im/v1/messages")
    assert second_call.kwargs["params"] == {"receive_id_type": "chat_id"}
    assert second_call.kwargs["json"]["receive_id"] == "oc_chat"


@pytest.mark.asyncio
async def test_lark_send_message_ignores_unresolved_templates() -> None:
    """Unresolved template strings should not be treated as valid recipients."""
    node = LarkSendMessageNode(
        name="send_lark",
        app_id="cli_app_id",
        app_secret="app_secret",
        receive_id="{{results.lark_listener.reply_target.chat_id}}",
        reply_to_message_id="{{results.lark_listener.reply_target.message_id}}",
        message="Hello chat",
    )

    state = State(
        messages=[],
        inputs={},
        results={
            "get_lark_tenant_token": {"json": {"tenant_access_token": "tenant_token"}}
        },
    )

    result = await node.run(state, RunnableConfig())

    assert result == {
        "is_error": True,
        "error": "No Lark receive_id or reply_to_message_id provided",
    }


@pytest.mark.asyncio
async def test_lark_fetch_tenant_token_api_error_code() -> None:
    """_fetch_tenant_access_token raises ValueError on non-zero Lark API code."""
    node = LarkSendMessageNode(
        name="send_lark",
        app_id="bad_app",
        app_secret="bad_secret",
        receive_id="oc_chat",
        message="Hello",
    )

    token_response = MagicMock()
    token_response.json.return_value = {
        "code": 99,
        "msg": "invalid app_id",
    }
    token_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=token_response)

    with patch("orcheo.nodes.lark.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ValueError, match="invalid app_id"):
            await node._fetch_tenant_access_token()


@pytest.mark.asyncio
async def test_lark_fetch_tenant_token_missing_token_field() -> None:
    """_fetch_tenant_access_token raises ValueError when token field is missing."""
    node = LarkSendMessageNode(
        name="send_lark",
        app_id="app",
        app_secret="secret",
        receive_id="oc_chat",
        message="Hello",
    )

    token_response = MagicMock()
    token_response.json.return_value = {
        "code": 0,
        "msg": "success",
    }  # no tenant_access_token
    token_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=token_response)

    with patch("orcheo.nodes.lark.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ValueError, match="missing tenant_access_token"):
            await node._fetch_tenant_access_token()


@pytest.mark.asyncio
async def test_lark_resolve_access_token_results_not_dict() -> None:
    """_resolve_access_token fetches token when results is not a dict."""
    node = LarkSendMessageNode(
        name="send_lark",
        app_id="app",
        app_secret="secret",
        receive_id="oc_chat",
        message="Hello",
    )
    # results is a list, not a dict
    state = State(messages=[], inputs={}, results=[])

    token_response = MagicMock()
    token_response.json.return_value = {
        "code": 0,
        "msg": "success",
        "tenant_access_token": "fetched_token",
    }
    token_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=token_response)

    with patch("orcheo.nodes.lark.httpx.AsyncClient", return_value=mock_client):
        token = await node._resolve_access_token(state)
    assert token == "fetched_token"


@pytest.mark.asyncio
async def test_lark_resolve_access_token_standard_node_result() -> None:
    """_resolve_access_token should accept the standard node's top-level token."""
    node = LarkSendMessageNode(
        name="send_lark",
        app_id="app",
        app_secret="secret",
        receive_id="oc_chat",
        message="Hello",
    )
    state = State(
        messages=[],
        inputs={},
        results={"get_lark_tenant_token": {"tenant_access_token": "node_token"}},
    )

    token = await node._resolve_access_token(state)

    assert token == "node_token"


@pytest.mark.asyncio
async def test_lark_resolve_access_token_token_result_not_dict() -> None:
    """_resolve_access_token fetches token when token_result is not a dict."""
    node = LarkSendMessageNode(
        name="send_lark",
        app_id="app",
        app_secret="secret",
        receive_id="oc_chat",
        message="Hello",
    )
    # get_lark_tenant_token result is a string, not a dict
    state = State(
        messages=[],
        inputs={},
        results={"get_lark_tenant_token": "not-a-dict"},
    )

    token_response = MagicMock()
    token_response.json.return_value = {
        "code": 0,
        "msg": "success",
        "tenant_access_token": "fetched_token",
    }
    token_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=token_response)

    with patch("orcheo.nodes.lark.httpx.AsyncClient", return_value=mock_client):
        token = await node._resolve_access_token(state)
    assert token == "fetched_token"


@pytest.mark.asyncio
async def test_lark_resolve_access_token_json_payload_not_dict() -> None:
    """_resolve_access_token fetches when json payload is not a dict."""
    node = LarkSendMessageNode(
        name="send_lark",
        app_id="app",
        app_secret="secret",
        receive_id="oc_chat",
        message="Hello",
    )
    # token_result exists as dict but json is not a dict
    state = State(
        messages=[],
        inputs={},
        results={"get_lark_tenant_token": {"json": "not-a-dict"}},
    )

    token_response = MagicMock()
    token_response.json.return_value = {
        "code": 0,
        "msg": "success",
        "tenant_access_token": "fetched_token",
    }
    token_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=token_response)

    with patch("orcheo.nodes.lark.httpx.AsyncClient", return_value=mock_client):
        token = await node._resolve_access_token(state)
    assert token == "fetched_token"


@pytest.mark.asyncio
async def test_lark_resolve_access_token_empty_token_string() -> None:
    """_resolve_access_token fetches when cached token is empty/whitespace."""
    node = LarkSendMessageNode(
        name="send_lark",
        app_id="app",
        app_secret="secret",
        receive_id="oc_chat",
        message="Hello",
    )
    # token field exists but is empty
    state = State(
        messages=[],
        inputs={},
        results={"get_lark_tenant_token": {"json": {"tenant_access_token": "   "}}},
    )

    token_response = MagicMock()
    token_response.json.return_value = {
        "code": 0,
        "msg": "success",
        "tenant_access_token": "fetched_token",
    }
    token_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=token_response)

    with patch("orcheo.nodes.lark.httpx.AsyncClient", return_value=mock_client):
        token = await node._resolve_access_token(state)
    assert token == "fetched_token"


@pytest.mark.asyncio
async def test_lark_send_message_delivery_error_code() -> None:
    """run returns error dict when Lark message delivery returns non-zero code."""
    node = LarkSendMessageNode(
        name="send_lark",
        app_id="app",
        app_secret="secret",
        receive_id="oc_chat",
        message="Hello",
    )
    state = State(
        messages=[],
        inputs={},
        results={
            "get_lark_tenant_token": {"json": {"tenant_access_token": "tenant_token"}}
        },
    )

    send_response = MagicMock()
    send_response.json.return_value = {
        "code": 1002003,
        "msg": "user_not_exist",
    }
    send_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=send_response)

    with patch("orcheo.nodes.lark.httpx.AsyncClient", return_value=mock_client):
        result = await node.run(state, RunnableConfig())

    assert result["is_error"] is True
    assert result["code"] == 1002003
    assert result["msg"] == "user_not_exist"
