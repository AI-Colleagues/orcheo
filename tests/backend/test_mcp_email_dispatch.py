"""How the email tools map their arguments onto the mailbox client."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
import pytest
from fastmcp.exceptions import ToolError
from orcheo_backend.app.mcp_server import email_tools
from orcheo_backend.app.mcp_server.email_errors import ServiceError
from orcheo_backend.app.mcp_server.email_mail import TOOL_SPECS


class Recorder:
    """Stands in for EmailAccounts and records every call it receives."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> dict[str, Any]:
            self.calls.append((name, args, kwargs))
            return {"called": name}

        return record

    def run_idempotently(
        self, actor: str, name: str, data: dict[str, Any], operation: Any
    ) -> Any:
        self.calls.append(("run_idempotently", (actor, name, data), {}))
        return operation(data)


SEND = {"subject": "Hi", "to": ["a@example.org"], "body_text": "Body"}

# tool -> (minimal arguments, client method, positional args, keyword args)
CASES: dict[str, tuple[dict[str, Any], str, Any, Any]] = {
    "search_emails": ({}, "search", lambda d: ("me", d), {}),
    "get_email": (
        {"message_id": "m"},
        "get_email",
        lambda d: ("me", "m"),
        {"include_html": False, "max_body_chars": 20_000},
    ),
    "download_email_attachment": (
        {"attachment_id": "a"},
        "download_attachment",
        lambda d: ("me", "a"),
        {},
    ),
    "send_email": (SEND, "send", lambda d: ("me", d), {}),
    "mark_emails_read": (
        {"message_ids": ["a", "b"]},
        "mark_read",
        lambda d: ("me", ["a", "b"], True),
        {},
    ),
    "mark_emails_unread": (
        {"message_ids": ["a"]},
        "mark_read",
        lambda d: ("me", ["a"], False),
        {},
    ),
    "manage_email_messages": (
        {"message_ids": ["a"], "action": "flag"},
        "manage_messages",
        lambda d: ("me", d),
        {},
    ),
    "list_email_folders": (
        {"account_id": "acc"},
        "list_folders",
        lambda d: ("me", "acc"),
        {},
    ),
    "reply_to_email": (
        {"message_id": "m", "body_text": "Thanks"},
        "reply",
        lambda d: ("me", "m", d),
        {},
    ),
    "forward_email": (
        {"message_id": "m", "to": ["a@example.org"]},
        "forward",
        lambda d: ("me", "m", d),
        {},
    ),
    "save_email_draft": (SEND, "save_draft", lambda d: ("me", d), {}),
    "send_email_draft": (
        {"message_id": "m"},
        "send_draft",
        lambda d: ("me", "m"),
        {},
    ),
    "get_email_thread": (
        {"message_id": "m", "limit": 5},
        "thread",
        lambda d: ("me", "m", 5),
        {},
    ),
    "read_email_attachment": (
        {"attachment_id": "a"},
        "attachment_text",
        lambda d: ("me", "a", 20_000),
        {},
    ),
    "respond_to_email_invitation": (
        {"message_id": "m", "response": "tentative", "comment": "Maybe"},
        "respond_to_invite",
        lambda d: ("me", "m", "tentative", "Maybe"),
        {},
    ),
    "get_email_event_details": (
        {"message_id": "m"},
        "event_links",
        lambda d: ("me", "m"),
        {},
    ),
    "find_email_contacts": (
        {"query": "ann", "account_id": "acc", "limit": 3},
        "find_contacts",
        lambda d: ("me", "acc", "ann", 3),
        {},
    ),
    "list_scheduled_emails": ({}, "list_scheduled", lambda d: ("me",), {}),
    "cancel_scheduled_email": (
        {"scheduled_id": "s"},
        "cancel_scheduled",
        lambda d: ("me", "s"),
        {},
    ),
    "set_default_email_account": (
        {"account_id": "acc"},
        "set_default",
        lambda d: ("me", "acc"),
        {},
    ),
}


def test_every_registered_tool_has_a_dispatch_case() -> None:
    assert set(CASES) == set(TOOL_SPECS)


@pytest.mark.parametrize("name", sorted(CASES))
def test_tools_call_the_matching_client_method(name: str) -> None:
    arguments, method, expected_args, expected_kwargs = CASES[name]
    model = TOOL_SPECS[name][2]
    data = model(**arguments).model_dump(mode="python")
    recorder = Recorder()

    result = email_tools._dispatch(recorder, name, data, "me")  # noqa: SLF001

    [*idempotent, (called, args, kwargs)] = recorder.calls
    assert called == method
    assert args == expected_args(data)
    assert kwargs == expected_kwargs
    if method == "list_scheduled":
        assert result == {"scheduled": {"called": "list_scheduled"}}
    else:
        assert result == {"called": method}
    if name in {"send_email", "save_email_draft"}:
        assert idempotent == [("run_idempotently", ("me", name, data), {})]
    else:
        assert idempotent == []


def test_unknown_operations_are_refused() -> None:
    with pytest.raises(ToolError, match="Unknown email operation: nope"):
        email_tools._dispatch(Recorder(), "nope", {}, "me")  # noqa: SLF001


def _later(**delta: float) -> str:
    return (datetime.now(UTC) + timedelta(**delta)).isoformat().replace("+00:00", "Z")


def test_scheduled_sends_are_normalized_to_utc() -> None:
    recorder = Recorder()
    when = _later(days=2)
    email_tools._send_message(recorder, "me", {**SEND, "send_at": when})  # noqa: SLF001
    [(method, (actor, data, send_at), _)] = recorder.calls
    assert (method, actor, data["subject"]) == ("schedule", "me", "Hi")
    assert send_at == when.replace("Z", "+00:00")

    offset = (datetime.now(UTC) + timedelta(days=1)).astimezone(
        timezone(timedelta(hours=2))
    )
    recorder = Recorder()
    email_tools._send_message(  # noqa: SLF001
        recorder, "me", {**SEND, "send_at": offset.isoformat()}
    )
    assert recorder.calls[0][1][2] == offset.astimezone(UTC).isoformat()


def test_immediate_sends_skip_the_queue() -> None:
    recorder = Recorder()
    email_tools._send_message(recorder, "me", {**SEND, "send_at": None})  # noqa: SLF001
    assert [call[0] for call in recorder.calls] == ["send"]


@pytest.mark.parametrize("when", [_later(minutes=-1), _later(days=31)])
def test_schedules_outside_the_next_thirty_days_are_refused(when: str) -> None:
    with pytest.raises(ServiceError) as raised:
        email_tools._send_message(Recorder(), "me", {**SEND, "send_at": when})  # noqa: SLF001
    assert raised.value.code == "email.invalid_send_at"
