"""iCalendar parsing and the iTIP reply built from untrusted invitations."""

from __future__ import annotations
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Any
from uuid import uuid4
import pytest
from orcheo.vault import InMemoryCredentialVault
from orcheo_backend.app.mcp_server import email_client
from orcheo_backend.app.mcp_server.email_client import EmailAccounts, parse_calendar
from orcheo_backend.app.mcp_server.email_errors import ServiceError


HOSTILE_UID = "evil\nEND:VEVENT\nBEGIN:VEVENT\nUID:forged"
HOSTILE_SUMMARY = "Lunch\nATTENDEE;PARTSTAT=ACCEPTED:mailto:victim@example.com"


def _invitation(**fields: str) -> str:
    """An RFC 5545 invitation whose TEXT values are escaped as a sender would."""
    values = {
        "UID": "abc-123@example.com",
        "SUMMARY": "Planning, Q1; kickoff",
        "ORGANIZER": "mailto:boss@example.com",
        "SEQUENCE": "2",
        **fields,
    }
    body = "\r\n".join(f"{key}:{value}" for key, value in values.items() if value)
    return (
        "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
        f"{body}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )


def _message(calendar: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "Invitation"
    message.set_content("You are invited.")
    message.add_alternative(calendar, subtype="calendar", params={"method": "REQUEST"})
    return message


@pytest.fixture
def accounts(monkeypatch: pytest.MonkeyPatch) -> EmailAccounts:
    manager = EmailAccounts(InMemoryCredentialVault(), str(uuid4()))
    row = {
        "id": "account-1",
        "display_name": 'Ada; "Ops", Inc: Team',
        "email_address": "ada@example.org",
    }
    monkeypatch.setattr(manager, "_parsed", lambda *args: (row, "INBOX", 1, 1, None))
    return manager


def _reply(
    accounts: EmailAccounts,
    monkeypatch: pytest.MonkeyPatch,
    calendar: str,
    comment: str = "",
) -> tuple[dict[str, Any], EmailMessage, str]:
    row = accounts._parsed("test", "id")[0]  # noqa: SLF001 - patched fixture
    monkeypatch.setattr(
        accounts, "_parsed", lambda *args: (row, "INBOX", 1, 1, _message(calendar))
    )
    sent: list[EmailMessage] = []

    def deliver(row: dict[str, Any], message: EmailMessage, to: list[str]) -> Any:
        assert to == ["boss@example.com"]
        sent.append(message)
        return {"sent": True}

    monkeypatch.setattr(accounts, "deliver", deliver)
    result = accounts.respond_to_invite("test", "id", "accepted", comment)
    [message] = sent
    wire = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
    [calendar_part] = [
        p for p in wire.walk() if p.get_content_type() == "text/calendar"
    ]
    return result, message, calendar_part.get_content()


def test_parse_calendar_unescapes_each_sequence_exactly_once() -> None:
    [event] = parse_calendar(
        _invitation(
            SUMMARY=r"a\\nb\;c\,d\Ne",
            UID="back\\\\slash",
        )
    )
    # An escaped backslash followed by "n" is a backslash and an "n", not a
    # line break; "\N" is the other spelling of a line break.
    assert event["summary"] == "a\\nb;c,d\ne"
    assert event["uid"] == "back\\slash"
    assert event["sequence"] == "2"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain", "plain"),
        ("a;b,c", r"a\;b\,c"),
        ("back\\slash", "back\\\\slash"),
        ("one\ntwo", "one\\ntwo"),
        ("one\r\ntwo\rthree", "one\\ntwo\\nthree"),
        ("bell\x07 tab\t", "bell tab\t"),
    ],
)
def test_ics_text_escapes_untrusted_values(raw: str, expected: str) -> None:
    assert email_client._ics_text(raw) == expected  # noqa: SLF001


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ada", "Ada"),
        ("Ada; Ops", '"Ada; Ops"'),
        ('Say "hi", Ada', "\"Say 'hi', Ada\""),
        ("Line\nBreak\x07", "Line Break"),
    ],
)
def test_ics_param_quotes_values_that_would_end_a_parameter(
    raw: str, expected: str
) -> None:
    assert email_client._ics_param(raw) == expected  # noqa: SLF001


def test_reply_cannot_be_forged_through_invitation_fields(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    calendar = (
        "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
        "UID:evil\\nEND:VEVENT\\nBEGIN:VEVENT\\nUID:forged\r\n"
        "SUMMARY:Lunch\\nATTENDEE;PARTSTAT=ACCEPTED:mailto:victim@example.com\r\n"
        "SEQUENCE:1\\nATTENDEE:mailto:victim@example.com\r\n"
        "ORGANIZER:mailto:boss@example.com\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    [parsed] = parse_calendar(calendar)
    assert parsed["uid"] == HOSTILE_UID
    assert parsed["summary"] == HOSTILE_SUMMARY

    result, message, body = _reply(
        accounts, monkeypatch, calendar, comment="Thanks,\nsee you; \\o/\r\nBye"
    )

    lines = body.replace("\r\n", "\n").strip().split("\n")
    assert lines.count("BEGIN:VEVENT") == 1
    assert lines.count("END:VEVENT") == 1
    assert [line for line in lines if line.startswith("ATTENDEE")] == [
        "ATTENDEE;PARTSTAT=ACCEPTED;CN=\"Ada; 'Ops', Inc: Team\":mailto:ada@example.org"
    ]
    assert not any(line.startswith("SEQUENCE") for line in lines)
    # Every untrusted value stays on its own line, escaped, and round-trips.
    [reply] = parse_calendar(body)
    assert reply["uid"] == HOSTILE_UID
    assert reply["summary"] == " ".join(HOSTILE_SUMMARY.split())
    assert reply["method"] == "REPLY"
    assert "\n" not in message["Subject"]
    assert message["Subject"].startswith("Accepted: Lunch ATTENDEE;PARTSTAT=")
    assert result["response"] == "accepted"
    assert result["uid"] == HOSTILE_UID
    assert r"COMMENT:Thanks\,\nsee you\; \\o/\nBye" in lines


def test_reply_echoes_a_numeric_sequence_and_defaults_the_subject(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, message, body = _reply(accounts, monkeypatch, _invitation(SUMMARY=""))
    assert "SEQUENCE:2" in body
    assert "SUMMARY" not in body
    assert message["Subject"] == "Accepted: Invitation"


def test_reply_requires_a_valid_invitation_and_organizer(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = accounts._parsed("test", "id")[0]  # noqa: SLF001 - patched fixture
    for calendar, code in (
        ("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", "email.no_invitation"),
        (_invitation(ORGANIZER=""), "email.no_organizer"),
        (_invitation(ORGANIZER="mailto:not an address"), "email.no_organizer"),
    ):
        monkeypatch.setattr(
            accounts,
            "_parsed",
            lambda *a, c=calendar: (row, "INBOX", 1, 1, _message(c)),
        )
        with pytest.raises(ServiceError) as raised:
            accounts.respond_to_invite("test", "id", "accepted", "")
        assert raised.value.code == code
