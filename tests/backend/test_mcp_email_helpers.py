"""Parsing, quoting and network helpers behind the email client."""

from __future__ import annotations
import smtplib
import socket
from email import policy
from email.parser import BytesParser
from typing import Any
import pytest
from orcheo_backend.app.mcp_server import email_client
from orcheo_backend.app.mcp_server.email_client import parse_calendar
from orcheo_backend.app.mcp_server.email_errors import ServiceError
from tests.backend.mcp_email_support import FakeIMAP, make_pdf


def _parse(raw: bytes) -> Any:
    return BytesParser(policy=policy.default).parsebytes(raw)


def _code(call: Any, *args: Any) -> str:
    with pytest.raises(ServiceError) as raised:
        call(*args)
    return raised.value.code


# -- IMAP names ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wire", "name"),
    [
        ("INBOX", "INBOX"),
        ("&-", "&"),
        ("Caf&AOk-", "Café"),
        ("&AOkA6Q-", "éé"),
        ("&ZeVnLIqe-", "日本語"),
        ("Bad&2A-", "Bad&2A-"),
    ],
)
def test_modified_utf7_names_round_trip(wire: str, name: str) -> None:
    assert email_client._decode_imap_utf7(wire) == name  # noqa: SLF001
    if wire != "Bad&2A-":
        assert email_client._imap_quote(name) == f'"{wire}"'  # noqa: SLF001
    # A name split into several runs on the wire reads the same.
    assert email_client._decode_imap_utf7("&AOk-&AOk-") == "éé"  # noqa: SLF001


def test_quoting_escapes_and_validates_names() -> None:
    assert email_client._imap_quote('a"b\\c') == '"a\\"b\\\\c"'  # noqa: SLF001
    assert email_client._imap_quote("x & y") == '"x &- y"'  # noqa: SLF001
    for bad in ("bad\x01", "x" * 256, "del\x7f"):
        assert _code(email_client._imap_quote, bad) == "email.invalid_folder"  # noqa: SLF001


def test_special_folder_discovery_ignores_rows_it_cannot_read() -> None:
    imap = FakeIMAP(
        list_rows=[
            None,
            b"not a folder row",
            b'(\\HasNoChildren \\Sent) "/" "Sent Messages"',
            b'(\\HasNoChildren \\Sent) "/" "Sent"',
            b'(\\HasNoChildren) "." "INBOX.Trash"',
            b'(\\HasNoChildren \\Drafts) NIL "Drafts"',
        ]
    )
    # The folder named after the role is the server's own and goes first.
    assert email_client._special_folders(imap, "sent") == ["Sent", "Sent Messages"]  # noqa: SLF001
    assert email_client._special_folder(imap, "trash") == "INBOX.Trash"  # noqa: SLF001
    assert email_client._special_folder(imap, "drafts") == "Drafts"  # noqa: SLF001
    assert email_client._special_folder(imap, "junk") is None  # noqa: SLF001
    assert email_client._resolve_folder(imap, "Sent") == "Sent"  # noqa: SLF001
    assert email_client._resolve_folder(imap, "junk") == "junk"  # noqa: SLF001
    assert [n for n, _ in email_client._list_folders(imap)] == [  # noqa: SLF001
        "Sent Messages",
        "Sent",
        "INBOX.Trash",
        "Drafts",
    ]


def test_opaque_identifiers_reject_garbage() -> None:
    assert _code(email_client._unb64, "a") == "email.invalid_id"  # noqa: SLF001
    assert email_client._unb64(email_client._b64(b"\xff\x00")) == b"\xff\x00"  # noqa: SLF001


# -- headers and bodies ----------------------------------------------------------


def test_header_decoding_is_lenient() -> None:
    decode = email_client._decode_header  # noqa: SLF001
    assert decode(None) == ""
    assert decode("=?utf-8?q?Caf=C3=A9?=") == "Café"
    assert decode("=?bogus-charset?q?abc?=") == "=?bogus-charset?q?abc?="


def test_text_of_html_drops_scripts_and_keeps_block_breaks() -> None:
    html = (
        "<html><head><title>t</title></head><body><script>bad()</script>"
        "<div>One</div><ul><li>Two</li><li>Three</li></ul>"
        "<style>p{}</style><p>Four &amp; five</p></body></html>"
    )
    assert email_client.html_to_text(html) == "One\nTwo\nThree\nFour & five"


@pytest.mark.parametrize("charset", ["bogus", "x-unknown"])
def test_unknown_charsets_still_yield_text(charset: str) -> None:
    raw = (
        f"Subject: s\r\nContent-Type: text/plain; charset={charset}\r\n"
        "Content-Transfer-Encoding: 8bit\r\n\r\ncaf\xe9\r\n"
    ).encode("latin-1")
    message = _parse(raw)
    assert email_client._email_text(message) == "caf�\r\n"  # noqa: SLF001
    assert email_client._part_text(message) == "caf�\r\n"  # noqa: SLF001
    calendar = (
        f"Content-Type: text/calendar; charset={charset}\r\n\r\n"
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()
    [invite] = email_client._calendar_parts(_parse(calendar))  # noqa: SLF001
    assert invite["uid"] == "u1"


def test_calendar_parsing_unfolds_lines_and_keeps_the_timezone() -> None:
    [event] = parse_calendar(
        "BEGIN:VCALENDAR\r\nMETHOD:request\r\nBEGIN:VEVENT\r\n"
        "UID:u1\r\nSUMMARY:A very long\r\n  summary\r\n"
        "DTSTART;TZID=Europe/Paris:20261101T100000\r\n"
        "DTEND:20261101T110000Z\r\nLOCATION:Room 1\\, Floor 2\r\n"
        "ORGANIZER;CN=Boss:MAILTO:boss@example.org\r\n"
        "ATTENDEE:mailto:a@example.org\r\nATTENDEE:b@example.org\r\n"
        "X-IGNORED:1\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    assert event["summary"] == "A very long summary"
    assert event["starts_at"] == "20261101T100000"
    assert event["timezone"] == "Europe/Paris"
    assert event["ends_at"] == "20261101T110000Z"
    assert event["location"] == "Room 1, Floor 2"
    assert event["organizer"] == "boss@example.org"
    assert event["attendees"] == ["a@example.org", "b@example.org"]
    assert event["method"] == "REQUEST"
    assert "x-ignored" not in event


def test_pdf_text_and_unreadable_pdfs() -> None:
    assert email_client._pdf_text(make_pdf("Quarterly report")) == "Quarterly report"  # noqa: SLF001
    assert _code(email_client._pdf_text, b"not a pdf") == "email.attachment_unreadable"  # noqa: SLF001


# -- MIME structure --------------------------------------------------------------


@pytest.mark.parametrize(
    ("structure", "expected"),
    [
        ('("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 10 1 NIL NIL NIL)', False),
        (
            '("APPLICATION" "PDF" ("NAME" "a.pdf") NIL NIL "BASE64" 10 NIL NIL NIL)',
            True,
        ),
        ('("APPLICATION" "PDF" ("NAME*" "b") NIL NIL "BASE64" 10 NIL NIL NIL)', True),
        (
            '("IMAGE" "PNG" ("NAME" "logo.png") NIL NIL "BASE64" 10 NIL '
            '("INLINE" ("FILENAME" "logo.png")) NIL)',
            False,
        ),
        (
            '("APPLICATION" "ZIP" NIL NIL NIL "BASE64" 10 NIL '
            '("ATTACHMENT" ("FILENAME" "a.zip")) NIL)',
            True,
        ),
        (
            '(("TEXT" "PLAIN" NIL NIL NIL "7BIT" 1 1 NIL NIL NIL)'
            '("APPLICATION" "PDF" ("NAME" "a.pdf") NIL NIL "BASE64" 1 NIL NIL NIL)'
            ' "MIXED" NIL NIL NIL)',
            True,
        ),
        (
            '(("TEXT" "PLAIN" NIL NIL NIL "7BIT" 1 1 NIL NIL NIL) "MIXED" NIL NIL NIL)',
            False,
        ),
        (
            '("MESSAGE" "RFC822" NIL NIL NIL "7BIT" 10 NIL '
            '(("APPLICATION" "PDF" ("NAME" "x.pdf") NIL NIL "BASE64" 1 NIL NIL NIL) '
            '"MIXED" NIL NIL NIL) 5 NIL NIL NIL)',
            True,
        ),
    ],
)
def test_attachments_are_read_from_the_bodystructure(
    structure: str, expected: bool
) -> None:
    metadata = f"1 (UID 5 FLAGS () BODYSTRUCTURE {structure})".encode()
    assert email_client._bodystructure_has_attachment(metadata) is expected  # noqa: SLF001


def test_malformed_bodystructures_are_unknown_not_wrong() -> None:
    has = email_client._bodystructure_has_attachment  # noqa: SLF001
    assert has(b"1 (UID 5 FLAGS ())") is None
    assert has(b"1 (UID 5 BODYSTRUCTURE ") is None
    assert has(b"1 (UID 5 BODYSTRUCTURE " + b"(" * 150 + b")" * 150 + b")") is None
    assert has(b"1 (UID 5 BODYSTRUCTURE (") is None
    assert email_client._mime_structure_has_attachment([]) is False  # noqa: SLF001


class _Client:
    def __init__(self, kind: str, data: list[Any]) -> None:
        self.kind, self.data = kind, data

    def uid(self, *args: str) -> tuple[str, list[Any]]:
        return self.kind, self.data


def test_search_page_parsing_handles_odd_fetch_responses() -> None:
    fetch = email_client._fetch_search_page  # noqa: SLF001
    assert fetch(_Client("NO", []), [7]) == {}
    page = fetch(
        _Client(
            "OK",
            [
                None,
                (b"1 (UID 7 FLAGS (\\Seen) X-NOTE {3}", b'a"b'),
                (
                    b" BODY[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID)] {14}",
                    b"Subject: hi\r\n\r\n",
                ),
                b' BODYSTRUCTURE ("TEXT" "PLAIN" NIL NIL NIL "7BIT" 1 1 NIL NIL NIL))',
                (b"2 (UID 8 FLAGS ()", b"ignored"),
            ],
        ),
        [7, 8],
    )
    assert list(page) == [7]
    metadata, headers = page[7]
    assert headers == b"Subject: hi\r\n\r\n"
    assert b'X-NOTE "a\\"b"' in metadata
    assert b"BODYSTRUCTURE" in metadata


# -- networking ------------------------------------------------------------------


def _resolve(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    answers = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 993)) for ip in addresses
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: answers)


def test_server_addresses_must_all_be_public(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve = email_client._server_ips  # noqa: SLF001
    _resolve(monkeypatch, "93.184.216.34", "93.184.216.34", "2606:2800:220:1::1")
    assert resolve("mail.example.org.", 993) == ["93.184.216.34", "2606:2800:220:1::1"]
    for private in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1"):
        _resolve(monkeypatch, "93.184.216.34", private)
        assert _code(resolve, "mail.example.org", 993) == "email.invalid_host"
    _resolve(monkeypatch)
    assert _code(resolve, "mail.example.org", 993) == "email.invalid_host"


@pytest.mark.parametrize("host", ["", "  ", "a/b", "user@host", "host name", "a\nb"])
def test_server_names_with_unsafe_characters_are_rejected(host: str) -> None:
    assert _code(email_client._server_ips, host, 993) == "email.invalid_host"  # noqa: SLF001


def test_unresolvable_servers_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    assert _code(email_client._server_ips, "nowhere.example", 993) == (  # noqa: SLF001
        "email.connection_failed"
    )


# -- SMTP errors -----------------------------------------------------------------


def _details(error: Exception) -> tuple[str, dict[str, Any]]:
    result = email_client._delivery_error(error)  # noqa: SLF001
    return result.message, result.details


def test_smtp_rejections_report_stage_and_shared_status_codes_only() -> None:
    message, details = _details(
        smtplib.SMTPRecipientsRefused(
            {
                "secret-a@example.org": (550, b"5.1.1 user secret-a unknown"),
                "secret-b@example.org": (550, b"5.1.1 mailbox full"),
            }
        )
    )
    assert details == {
        "smtp_stage": "recipients",
        "delivery_status": "rejected",
        "smtp_code": 550,
        "enhanced_status": "5.1.1",
    }
    assert message == "The SMTP server rejected the recipients (SMTP 550, 5.1.1)."
    assert "secret" not in message

    mixed, mixed_details = _details(
        smtplib.SMTPRecipientsRefused(
            {"a@example.org": (550, b"gone"), "b@example.org": (552, b"4.2.2 full")}
        )
    )
    assert mixed == "The SMTP server rejected the recipients."
    assert "smtp_code" not in mixed_details


@pytest.mark.parametrize(
    ("error", "stage", "text"),
    [
        (
            smtplib.SMTPSenderRefused(553, b"5.7.1 not allowed", "me@example.org"),
            "sender",
            "rejected the sender address (SMTP 553, 5.7.1)",
        ),
        (
            smtplib.SMTPDataError(554, b"message looks like spam"),
            "content",
            "rejected the message content or format (SMTP 554)",
        ),
        (
            smtplib.SMTPResponseException(421, b"4.3.2 shutting down"),
            "connection",
            "rejected the connection (SMTP 421, 4.3.2)",
        ),
        (
            smtplib.SMTPNotSupportedError("SMTPUTF8 not supported"),
            "content",
            "rejected the message content or format.",
        ),
    ],
)
def test_each_smtp_failure_stage_has_its_own_message(
    error: Exception, stage: str, text: str
) -> None:
    message, details = _details(error)
    assert details["smtp_stage"] == stage
    assert details["delivery_status"] == "rejected"
    assert text in message
    assert "spam" not in message
    assert "SMTPUTF8" not in message


def test_a_dropped_connection_is_an_unknown_outcome() -> None:
    message, details = _details(ConnectionResetError("reset by peer 10.0.0.1"))
    assert details == {"smtp_stage": "connection", "delivery_status": "unknown"}
    assert message.startswith("The SMTP connection was interrupted")
    assert "10.0.0.1" not in message
