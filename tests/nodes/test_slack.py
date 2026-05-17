"""Tests for Slack node."""

from typing import Any
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from orcheo.nodes.connectors.slack import SlackNode


class FakeResponse:
    """Minimal HTTP response double for Slack API tests."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        """Pretend the HTTP request succeeded."""
        return None

    def json(self) -> dict[str, Any]:
        """Return the mocked Slack JSON payload."""
        return self._payload


def _async_client_context(
    *,
    get: AsyncMock | None = None,
    post: AsyncMock | None = None,
) -> AsyncMock:
    """Return an async context manager wrapping a fake httpx client."""
    client = AsyncMock()
    client.get = get or AsyncMock()
    client.post = post or AsyncMock()
    context_manager = AsyncMock()
    context_manager.__aenter__.return_value = client
    context_manager.__aexit__.return_value = None
    return context_manager


@pytest.fixture
def slack_node() -> SlackNode:
    """Return a SlackNode configured for tests."""
    return SlackNode(
        name="slack_node",
        tool_name="slack_post_message",
        kwargs={"channel_id": "C123", "text": "Hello World!"},
        bot_token="test_token",
        team_id="T123",
        channel_ids="C111, C222",
    )


@pytest.mark.asyncio
async def test_slack_node_posts_message_with_full_payload(
    slack_node: SlackNode,
) -> None:
    """Post message preserves extra Slack Web API fields."""
    slack_node.kwargs = {
        "channel_id": "C123",
        "text": "Hello World!",
        "unfurl_links": False,
        "unfurl_media": False,
        "parse": "none",
    }
    post_mock = AsyncMock(return_value=FakeResponse({"ok": True, "ts": "123.456"}))

    with patch(
        "orcheo.nodes.slack.httpx.AsyncClient",
        return_value=_async_client_context(post=post_mock),
    ):
        result = await slack_node.run({}, None)

    post_mock.assert_awaited_once_with(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": "Bearer test_token",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={
            "channel": "C123",
            "text": "Hello World!",
            "unfurl_links": False,
            "unfurl_media": False,
            "parse": "none",
        },
    )
    assert result == {
        "ok": True,
        "ts": "123.456",
        "is_error": False,
        "error": None,
    }


@pytest.mark.asyncio
async def test_slack_node_reply_requires_thread_ts(slack_node: SlackNode) -> None:
    """Thread replies validate required Slack arguments."""
    slack_node.tool_name = "slack_reply_to_thread"
    slack_node.kwargs = {"channel_id": "C123", "text": "reply"}

    result = await slack_node.run({}, None)

    assert result == {
        "is_error": True,
        "error": "Missing required argument: thread_ts",
    }


@pytest.mark.asyncio
async def test_slack_node_lists_public_channels(slack_node: SlackNode) -> None:
    """Channel listing hits Slack conversations.list when no allowlist is set."""
    slack_node.tool_name = "slack_list_channels"
    slack_node.channel_ids = None
    slack_node.kwargs = {"limit": 25, "cursor": "abc"}
    get_mock = AsyncMock(
        return_value=FakeResponse({"ok": True, "channels": [{"id": "C123"}]})
    )

    with patch(
        "orcheo.nodes.slack.httpx.AsyncClient",
        return_value=_async_client_context(get=get_mock),
    ):
        result = await slack_node.run({}, None)

    get_mock.assert_awaited_once_with(
        "https://slack.com/api/conversations.list",
        headers={
            "Authorization": "Bearer test_token",
            "Content-Type": "application/json; charset=utf-8",
        },
        params={
            "types": "public_channel",
            "exclude_archived": "true",
            "limit": "25",
            "team_id": "T123",
            "cursor": "abc",
        },
    )
    assert result["is_error"] is False


@pytest.mark.asyncio
async def test_slack_node_lists_predefined_channels(slack_node: SlackNode) -> None:
    """Channel allowlists fan out through conversations.info."""
    slack_node.tool_name = "slack_list_channels"
    slack_node.kwargs = {}
    get_mock = AsyncMock(
        side_effect=[
            FakeResponse({"ok": True, "channel": {"id": "C111", "is_archived": False}}),
            FakeResponse({"ok": True, "channel": {"id": "C222", "is_archived": True}}),
        ]
    )

    with patch(
        "orcheo.nodes.slack.httpx.AsyncClient",
        return_value=_async_client_context(get=get_mock),
    ):
        result = await slack_node.run({}, None)

    assert get_mock.await_count == 2
    first_call = get_mock.await_args_list[0]
    second_call = get_mock.await_args_list[1]
    assert first_call.kwargs["params"] == {"channel": "C111"}
    assert second_call.kwargs["params"] == {"channel": "C222"}
    assert result["channels"] == [{"id": "C111", "is_archived": False}]


@pytest.mark.asyncio
async def test_slack_node_adds_reaction(slack_node: SlackNode) -> None:
    """Reaction calls map SlackNode kwargs to reactions.add payload."""
    slack_node.tool_name = "slack_add_reaction"
    slack_node.kwargs = {
        "channel_id": "C123",
        "timestamp": "123.456",
        "reaction": "eyes",
    }
    post_mock = AsyncMock(return_value=FakeResponse({"ok": True}))

    with patch(
        "orcheo.nodes.slack.httpx.AsyncClient",
        return_value=_async_client_context(post=post_mock),
    ):
        result = await slack_node.run({}, None)

    post_mock.assert_awaited_once_with(
        "https://slack.com/api/reactions.add",
        headers={
            "Authorization": "Bearer test_token",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={
            "channel": "C123",
            "timestamp": "123.456",
            "name": "eyes",
        },
    )
    assert result["is_error"] is False


@pytest.mark.asyncio
async def test_slack_node_gets_channel_history(slack_node: SlackNode) -> None:
    """History calls use conversations.history."""
    slack_node.tool_name = "slack_get_channel_history"
    slack_node.kwargs = {"channel_id": "C123", "limit": 5}
    get_mock = AsyncMock(return_value=FakeResponse({"ok": True, "messages": []}))

    with patch(
        "orcheo.nodes.slack.httpx.AsyncClient",
        return_value=_async_client_context(get=get_mock),
    ):
        result = await slack_node.run({}, None)

    get_mock.assert_awaited_once_with(
        "https://slack.com/api/conversations.history",
        headers={
            "Authorization": "Bearer test_token",
            "Content-Type": "application/json; charset=utf-8",
        },
        params={"channel": "C123", "limit": "5"},
    )
    assert result["is_error"] is False


@pytest.mark.asyncio
async def test_slack_node_gets_thread_replies(slack_node: SlackNode) -> None:
    """Thread replies use conversations.replies with ts query param."""
    slack_node.tool_name = "slack_get_thread_replies"
    slack_node.kwargs = {"channel_id": "C123", "thread_ts": "123.456"}
    get_mock = AsyncMock(return_value=FakeResponse({"ok": True, "messages": []}))

    with patch(
        "orcheo.nodes.slack.httpx.AsyncClient",
        return_value=_async_client_context(get=get_mock),
    ):
        result = await slack_node.run({}, None)

    get_mock.assert_awaited_once_with(
        "https://slack.com/api/conversations.replies",
        headers={
            "Authorization": "Bearer test_token",
            "Content-Type": "application/json; charset=utf-8",
        },
        params={"channel": "C123", "ts": "123.456"},
    )
    assert result["is_error"] is False


@pytest.mark.asyncio
async def test_slack_node_gets_users_and_profile(slack_node: SlackNode) -> None:
    """User listing and profile fetch both map to Slack user APIs."""
    get_mock = AsyncMock(
        side_effect=[
            FakeResponse({"ok": True, "members": [{"id": "U123"}]}),
            FakeResponse({"ok": True, "profile": {"real_name": "Test User"}}),
        ]
    )

    with patch(
        "orcheo.nodes.slack.httpx.AsyncClient",
        return_value=_async_client_context(get=get_mock),
    ):
        slack_node.tool_name = "slack_get_users"
        slack_node.kwargs = {"limit": 15}
        users_result = await slack_node.run({}, None)

        slack_node.tool_name = "slack_get_user_profile"
        slack_node.kwargs = {"user_id": "U123"}
        profile_result = await slack_node.run({}, None)

    assert get_mock.await_args_list[0].kwargs["params"] == {
        "limit": "15",
        "team_id": "T123",
    }
    assert get_mock.await_args_list[1].kwargs["params"] == {
        "user": "U123",
        "include_labels": "true",
    }
    assert users_result["is_error"] is False
    assert profile_result["is_error"] is False


@pytest.mark.asyncio
async def test_slack_node_returns_http_errors(slack_node: SlackNode) -> None:
    """HTTP failures surface as node errors."""
    post_mock = AsyncMock(side_effect=httpx.HTTPError("boom"))

    with patch(
        "orcheo.nodes.slack.httpx.AsyncClient",
        return_value=_async_client_context(post=post_mock),
    ):
        result = await slack_node.run({}, None)

    assert result == {
        "is_error": True,
        "error": "boom",
    }


def test_slack_node_helpers_handle_default_limits_and_channel_normalization() -> None:
    """Private helpers normalize channel aliases and fallback limit values."""
    node = SlackNode(
        name="slack_node",
        tool_name="slack_post_message",
        kwargs={},
        bot_token="test_token",
        team_id="T123",
    )

    assert node._coerce_limit(None, 10) == 10
    assert node._coerce_limit("invalid", 10) == 10
    assert node._coerce_limit("999", 10) == 200

    node.kwargs = {"channel_id": "C123", "text": "hello"}
    assert node._normalize_channel_payload() == {"channel": "C123", "text": "hello"}

    node.kwargs = {"channel": 123, "text": "hello"}
    assert node._normalize_channel_payload() == {"channel": 123, "text": "hello"}


@pytest.mark.asyncio
async def test_slack_node_post_message_requires_channel_id(
    slack_node: SlackNode,
) -> None:
    """Posting messages requires a Slack channel identifier."""
    slack_node.kwargs = {"text": "Hello World!"}

    result = await slack_node.run({}, None)

    assert result == {
        "is_error": True,
        "error": "Missing required argument: channel_id",
    }


@pytest.mark.asyncio
async def test_slack_node_post_message_requires_text(slack_node: SlackNode) -> None:
    """Posting messages requires text content."""
    slack_node.kwargs = {"channel_id": "C123"}

    result = await slack_node.run({}, None)

    assert result == {
        "is_error": True,
        "error": "Missing required argument: text",
    }


@pytest.mark.asyncio
async def test_slack_node_replies_to_thread_with_valid_payload(
    slack_node: SlackNode,
) -> None:
    """Thread replies post through chat.postMessage when thread_ts is present."""
    slack_node.tool_name = "slack_reply_to_thread"
    slack_node.kwargs = {
        "channel_id": "C123",
        "text": "reply",
        "thread_ts": "123.456",
    }
    post_mock = AsyncMock(return_value=FakeResponse({"ok": True, "ts": "789.000"}))

    with patch(
        "orcheo.nodes.slack.httpx.AsyncClient",
        return_value=_async_client_context(post=post_mock),
    ):
        result = await slack_node.run({}, None)

    post_mock.assert_awaited_once_with(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": "Bearer test_token",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={
            "channel": "C123",
            "text": "reply",
            "thread_ts": "123.456",
        },
    )
    assert result["is_error"] is False


@pytest.mark.asyncio
async def test_slack_node_lists_predefined_channels_skips_blank_entries(
    slack_node: SlackNode,
) -> None:
    """Channel allowlists ignore blank values before calling Slack."""
    slack_node.tool_name = "slack_list_channels"
    slack_node.channel_ids = "C111,   , C222"
    slack_node.kwargs = {}
    get_mock = AsyncMock(
        side_effect=[
            FakeResponse({"ok": True, "channel": {"id": "C111", "is_archived": False}}),
            FakeResponse({"ok": True, "channel": {"id": "C222", "is_archived": False}}),
        ]
    )

    with patch(
        "orcheo.nodes.slack.httpx.AsyncClient",
        return_value=_async_client_context(get=get_mock),
    ):
        result = await slack_node.run({}, None)

    assert get_mock.await_count == 2
    assert result["channels"] == [
        {"id": "C111", "is_archived": False},
        {"id": "C222", "is_archived": False},
    ]


@pytest.mark.asyncio
async def test_slack_node_lists_predefined_channels_returns_api_errors(
    slack_node: SlackNode,
) -> None:
    """Allowlisted channel lookups preserve Slack API error payloads."""
    slack_node.tool_name = "slack_list_channels"
    slack_node.channel_ids = "C111"
    slack_node.kwargs = {}
    get_mock = AsyncMock(
        return_value=FakeResponse({"ok": False, "error": "channel_not_found"})
    )

    with patch(
        "orcheo.nodes.slack.httpx.AsyncClient",
        return_value=_async_client_context(get=get_mock),
    ):
        result = await slack_node.run({}, None)

    assert result == {
        "ok": False,
        "error": "channel_not_found",
        "is_error": True,
    }


@pytest.mark.asyncio
async def test_slack_node_lists_public_channels_omits_invalid_cursor(
    slack_node: SlackNode,
) -> None:
    """Public channel listing omits falsey or invalid cursors."""
    slack_node.tool_name = "slack_list_channels"
    slack_node.channel_ids = None
    slack_node.kwargs = {"limit": "invalid", "cursor": 123}
    get_mock = AsyncMock(return_value=FakeResponse({"ok": True, "channels": []}))

    with patch(
        "orcheo.nodes.slack.httpx.AsyncClient",
        return_value=_async_client_context(get=get_mock),
    ):
        result = await slack_node.run({}, None)

    get_mock.assert_awaited_once_with(
        "https://slack.com/api/conversations.list",
        headers={
            "Authorization": "Bearer test_token",
            "Content-Type": "application/json; charset=utf-8",
        },
        params={
            "types": "public_channel",
            "exclude_archived": "true",
            "limit": "100",
            "team_id": "T123",
        },
    )
    assert result["is_error"] is False


@pytest.mark.asyncio
async def test_slack_node_list_channels_returns_http_errors(
    slack_node: SlackNode,
) -> None:
    """Channel listing surfaces request failures."""
    slack_node.tool_name = "slack_list_channels"
    slack_node.channel_ids = None
    slack_node.kwargs = {}

    with patch.object(
        slack_node, "_get_json", new=AsyncMock(side_effect=httpx.HTTPError("boom"))
    ):
        result = await slack_node.run({}, None)

    assert result == {
        "is_error": True,
        "error": "boom",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"timestamp": "123.456", "reaction": "eyes"},
            "Missing required argument: channel_id",
        ),
        (
            {"channel_id": "C123", "reaction": "eyes"},
            "Missing required argument: timestamp",
        ),
        (
            {"channel_id": "C123", "timestamp": "123.456"},
            "Missing required argument: reaction",
        ),
    ],
)
async def test_slack_node_add_reaction_validates_required_fields(
    slack_node: SlackNode, kwargs: dict[str, Any], message: str
) -> None:
    """Reaction posting validates required Slack arguments."""
    slack_node.tool_name = "slack_add_reaction"
    slack_node.kwargs = kwargs

    result = await slack_node.run({}, None)

    assert result == {
        "is_error": True,
        "error": message,
    }


@pytest.mark.asyncio
async def test_slack_node_add_reaction_returns_http_errors(
    slack_node: SlackNode,
) -> None:
    """Reaction request failures return normalized node errors."""
    slack_node.tool_name = "slack_add_reaction"
    slack_node.kwargs = {
        "channel_id": "C123",
        "timestamp": "123.456",
        "reaction": "eyes",
    }

    with patch.object(
        slack_node, "_post_json", new=AsyncMock(side_effect=httpx.HTTPError("boom"))
    ):
        result = await slack_node.run({}, None)

    assert result == {
        "is_error": True,
        "error": "boom",
    }


@pytest.mark.asyncio
async def test_slack_node_channel_history_requires_channel_id(
    slack_node: SlackNode,
) -> None:
    """History lookup requires a channel identifier."""
    slack_node.tool_name = "slack_get_channel_history"
    slack_node.kwargs = {}

    result = await slack_node.run({}, None)

    assert result == {
        "is_error": True,
        "error": "Missing required argument: channel_id",
    }


@pytest.mark.asyncio
async def test_slack_node_channel_history_returns_http_errors(
    slack_node: SlackNode,
) -> None:
    """History lookup surfaces transport failures."""
    slack_node.tool_name = "slack_get_channel_history"
    slack_node.kwargs = {"channel_id": "C123"}

    with patch.object(
        slack_node, "_get_json", new=AsyncMock(side_effect=httpx.HTTPError("boom"))
    ):
        result = await slack_node.run({}, None)

    assert result == {
        "is_error": True,
        "error": "boom",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"thread_ts": "123.456"}, "Missing required argument: channel_id"),
        ({"channel_id": "C123"}, "Missing required argument: thread_ts"),
    ],
)
async def test_slack_node_thread_replies_validate_required_fields(
    slack_node: SlackNode, kwargs: dict[str, Any], message: str
) -> None:
    """Thread replies require both channel and thread timestamps."""
    slack_node.tool_name = "slack_get_thread_replies"
    slack_node.kwargs = kwargs

    result = await slack_node.run({}, None)

    assert result == {
        "is_error": True,
        "error": message,
    }


@pytest.mark.asyncio
async def test_slack_node_thread_replies_return_http_errors(
    slack_node: SlackNode,
) -> None:
    """Thread reply lookups surface transport failures."""
    slack_node.tool_name = "slack_get_thread_replies"
    slack_node.kwargs = {"channel_id": "C123", "thread_ts": "123.456"}

    with patch.object(
        slack_node, "_get_json", new=AsyncMock(side_effect=httpx.HTTPError("boom"))
    ):
        result = await slack_node.run({}, None)

    assert result == {
        "is_error": True,
        "error": "boom",
    }


@pytest.mark.asyncio
async def test_slack_node_get_users_adds_cursor_and_returns_http_errors(
    slack_node: SlackNode,
) -> None:
    """User listing includes cursors and normalizes request failures."""
    slack_node.tool_name = "slack_get_users"
    slack_node.kwargs = {"cursor": "next-page"}

    with patch.object(
        slack_node, "_get_json", new=AsyncMock(side_effect=httpx.HTTPError("boom"))
    ) as get_json_mock:
        result = await slack_node.run({}, None)

    get_json_mock.assert_awaited_once_with(
        "users.list",
        {
            "limit": "100",
            "team_id": "T123",
            "cursor": "next-page",
        },
    )
    assert result == {
        "is_error": True,
        "error": "boom",
    }


@pytest.mark.asyncio
async def test_slack_node_user_profile_requires_user_id(slack_node: SlackNode) -> None:
    """Profile lookup requires a Slack user ID."""
    slack_node.tool_name = "slack_get_user_profile"
    slack_node.kwargs = {}

    result = await slack_node.run({}, None)

    assert result == {
        "is_error": True,
        "error": "Missing required argument: user_id",
    }


@pytest.mark.asyncio
async def test_slack_node_user_profile_returns_http_errors(
    slack_node: SlackNode,
) -> None:
    """Profile lookup surfaces transport failures."""
    slack_node.tool_name = "slack_get_user_profile"
    slack_node.kwargs = {"user_id": "U123"}

    with patch.object(
        slack_node, "_get_json", new=AsyncMock(side_effect=httpx.HTTPError("boom"))
    ):
        result = await slack_node.run({}, None)

    assert result == {
        "is_error": True,
        "error": "boom",
    }
