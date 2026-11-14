"""Input validation for the email MCP tool arguments."""

from __future__ import annotations
from typing import Any
import pytest
from pydantic import ValidationError
from orcheo_backend.app.mcp_server.email_client import (
    EmailAccountSave,
    EmailActions,
    EmailAttachment,
    EmailMessageIds,
    EmailProtocol,
    SearchEmails,
    SendEmail,
)
from orcheo_backend.app.mcp_server.email_mail import ForwardEmail
from tests.backend.mcp_email_support import account_payload, b64


def _rejects(model: Any, message: str, **fields: Any) -> None:
    with pytest.raises(ValidationError) as raised:
        model(**fields)
    assert message in str(raised.value)


def _send(**fields: Any) -> dict[str, Any]:
    return {"subject": "Hi", "to": ["a@example.org"], "body_text": "Body", **fields}


def _protocol(**overrides: Any) -> dict[str, Any]:
    return {
        "host": "imap.example.org",
        "port": 993,
        "username": "me",
        "password": "secret",
        **overrides,
    }


def test_server_settings_are_trimmed_and_reject_control_characters() -> None:
    assert EmailProtocol(**_protocol(host="  imap.example.org ")).host == (
        "imap.example.org"
    )
    _rejects(EmailProtocol, "valid server name", **_protocol(host="bad\x01host"))
    _rejects(EmailProtocol, "valid server name", **_protocol(username="   "))


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"display_name": "Bad\nName"}, "without control characters"),
        ({"email_address": "Ann <ann@example.org>"}, "valid email address"),
        ({"email_address": "not-an-address"}, "valid email address"),
        ({"labels": ["fine", "bad\x01label"]}, "Labels must be 1"),
        ({"labels": ["x" * 101]}, "Labels must be 1"),
    ],
)
def test_account_fields_are_validated(override: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as raised:
        account_payload(**override)
    assert message in str(raised.value)


def test_labels_are_deduplicated_and_new_accounts_need_both_passwords() -> None:
    payload = account_payload(labels=[" work ", "work", "", "home"])
    assert payload["labels"] == ["work", "home"]
    for protocol in ("imap", "smtp"):
        fields = account_payload()
        fields[protocol].pop("password")
        _rejects(EmailAccountSave, "Enter an IMAP and SMTP password", **fields)
    # Editing an existing account may leave passwords blank.
    existing = account_payload(account_id="abc")
    existing["imap"].pop("password")
    assert EmailAccountSave(**existing).account_id == "abc"


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"cursor": "abc"}, "cursor is invalid"),
        ({"after": "2026/11/01"}, "YYYY-MM-DD"),
        ({"before": "2026-13-45"}, "YYYY-MM-DD"),
        ({"after": "2026-11-14", "before": "2026-11-13"}, "must not be after"),
    ],
)
def test_search_arguments_are_validated(fields: dict[str, Any], message: str) -> None:
    _rejects(SearchEmails, message, **fields)


def test_search_accepts_cursors_and_ordered_ranges() -> None:
    assert SearchEmails(cursor="12.34").cursor == "12.34"
    assert SearchEmails(after="2026-11-13", before="2026-11-13").after == "2026-11-13"


def test_attachment_filenames_cannot_carry_paths() -> None:
    content = {"content_base64": b64(b"x")}
    for name in ("a/b.txt", "a\\b.txt", "bad\x00.txt"):
        _rejects(EmailAttachment, "filename is invalid", filename=name, **content)
    assert EmailAttachment(filename="ok.txt", **content).filename == "ok.txt"


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"to": ["a@example.org\nBcc: x@example.org"]}, "valid email addresses"),
        ({"to": ["not-an-address"]}, "Use email addresses"),
        ({"cc": ["Ann a@example.org"]}, "Use email addresses"),
        ({"reply_to": "bad"}, "Use email addresses"),
        ({"subject": "Hi\r\nBcc: x@example.org"}, "cannot contain line breaks"),
        ({"in_reply_to": "<a>\n<b>"}, "cannot contain line breaks"),
        ({"references": ["<a>", "<b>\n"]}, "cannot contain line breaks"),
        ({"send_at": "tomorrow"}, "ISO 8601"),
        ({"send_at": "2026-12-01T09:00:00"}, "UTC offset"),
        ({"body_text": None}, "text or HTML message body"),
        ({"to": []}, "at least one recipient"),
        ({"attachments": [{"filename": "a", "content_base64": "@@@"}]}, "valid base64"),
    ],
)
def test_send_arguments_are_validated(fields: dict[str, Any], message: str) -> None:
    _rejects(SendEmail, message, **_send(**fields))


def test_send_attachment_size_limits() -> None:
    big = b64(b"x" * 500_001)
    _rejects(
        SendEmail,
        "Each attachment must be 500 KB or less",
        **_send(attachments=[{"filename": "a", "content_base64": big}]),
    )
    half = b64(b"x" * 300_000)
    _rejects(
        SendEmail,
        "Attachments must total 500 KB or less",
        **_send(
            attachments=[
                {"filename": "a", "content_base64": half},
                {"filename": "b", "content_base64": half},
            ]
        ),
    )


def test_send_normalizes_valid_input() -> None:
    message = SendEmail(
        **_send(
            to=["Ann <a@example.org>", "Ann <a@example.org>"],
            reply_to=None,
            send_at="2026-12-01T09:00:00Z",
            bcc=["b@example.org"],
        )
    )
    assert message.to == ["Ann <a@example.org>"]
    assert message.reply_to is None
    assert message.send_at == "2026-12-01T09:00:00Z"
    only_bcc = SendEmail(
        **_send(to=[], bcc=["b@example.org"], body_text=None, body_html="<p>x</p>")
    )
    assert only_bcc.bcc == ["b@example.org"]


def test_forward_recipients_follow_the_send_rules() -> None:
    _rejects(ForwardEmail, "Use email addresses", message_id="m", to=["nope"])
    assert ForwardEmail(message_id="m", to=["a@example.org"]).to == ["a@example.org"]


def test_batches_must_not_repeat_a_message() -> None:
    _rejects(EmailMessageIds, "once", message_ids=["a", "a"])
    assert EmailMessageIds(message_ids=["a", "b"]).message_ids == ["a", "b"]


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"action": "move"}, "Choose a destination folder"),
        ({"action": "copy", "target_folder": ""}, "Choose a destination folder"),
        ({"action": "flag", "target_folder": "Archive"}, "only used when moving"),
        ({"action": "flag", "permanent": True}, "only applies to deleting"),
        ({"action": "add_labels"}, "Labels go with add_labels"),
        ({"action": "flag", "labels": ["x"]}, "Labels go with add_labels"),
        ({"action": "add_labels", "labels": ["two words"]}, "single words"),
        ({"action": "add_labels", "labels": ["a(b)"]}, "single words"),
    ],
)
def test_message_actions_are_validated(fields: dict[str, Any], message: str) -> None:
    _rejects(EmailActions, message, message_ids=["m"], **fields)


def test_valid_message_actions() -> None:
    assert EmailActions(message_ids=["m"], action="delete", permanent=True).permanent
    assert EmailActions(
        message_ids=["m"], action="move", target_folder="A"
    ).target_folder
    assert EmailActions(
        message_ids=["m"], action="add_labels", labels=["\\Seen"]
    ).labels


def test_drafts_cannot_be_scheduled_and_the_schema_does_not_say_they_can() -> None:
    from orcheo_backend.app.mcp_server.email_client import DraftEmail
    from orcheo_backend.app.mcp_server.email_mail import TOOL_SPECS

    assert "send_at" not in DraftEmail.model_fields
    assert "send_at" in SendEmail.model_fields
    assert TOOL_SPECS["save_email_draft"][2] is DraftEmail
    assert TOOL_SPECS["send_email"][2] is SendEmail
    _rejects(DraftEmail, "send_at", **_send(send_at="2026-12-01T09:00:00Z"))
    # Everything else a draft shares with a send is still validated.
    _rejects(DraftEmail, "Use email addresses", **_send(to=["nope"]))
    assert DraftEmail(**_send(idempotency_key="k")).idempotency_key == "k"
