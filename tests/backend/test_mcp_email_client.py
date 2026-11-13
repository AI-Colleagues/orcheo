"""Regression tests for the MCP email client with fake mail servers."""

from __future__ import annotations
import base64
import imaplib
import json
import smtplib
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from threading import Event
from typing import Any
from uuid import UUID, uuid4
import pytest
from orcheo.vault import InMemoryCredentialVault
from orcheo_backend.app.mcp_server import email_client
from orcheo_backend.app.mcp_server.email_client import EmailAccounts, SearchEmails
from orcheo_backend.app.mcp_server.email_errors import ServiceError


@pytest.fixture
def accounts() -> EmailAccounts:
    vault = InMemoryCredentialVault()
    return EmailAccounts(vault, str(uuid4()))


def _create_mailbox(accounts: EmailAccounts) -> str:
    return str(
        accounts.vault.create_credential(
            name="mailbox",
            provider=accounts.ACCOUNT_PROVIDER,
            scopes=[],
            secret=json.dumps({"email_address": "mailbox@example.org"}),
            actor="test",
            workspace_id=accounts.workspace_id,
        ).id
    )


def _schedule(accounts: EmailAccounts, account_id: str, subject: str, age: int) -> str:
    return accounts.schedule(
        "test",
        {"account_id": account_id, "subject": subject, "body_text": "private body"},
        (datetime.now(UTC) - timedelta(minutes=age)).isoformat(),
        extra_bcc=["private@example.org"],
        extra_attachments=[(b"private attachment", "text", "plain", "notes.txt")],
    )["scheduled_id"]


def test_dispatch_rechecks_later_message_after_successful_cancel(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    account_id = _create_mailbox(accounts)
    first = _schedule(accounts, account_id, "first", 2)
    second = _schedule(accounts, account_id, "second", 1)
    entered = Event()
    release = Event()
    delivered: list[str] = []

    def send(actor: str, args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        delivered.append(args["subject"])
        entered.set()
        assert release.wait(5)
        return {"sent": True}

    monkeypatch.setattr(accounts, "send", send)
    with ThreadPoolExecutor() as pool:
        dispatch = pool.submit(accounts.process_scheduled)
        try:
            assert entered.wait(5)
            with pytest.raises(ServiceError, match="still waiting to send"):
                accounts.cancel_scheduled("test", first)
            assert accounts.cancel_scheduled("test", second)["status"] == "cancelled"
        finally:
            release.set()
        assert dispatch.result(timeout=5) == {"sent": 1, "failed": 0}
    assert delivered == ["first"]
    assert (
        accounts.vault.list_all_credentials(workspace_id=accounts.workspace_id)[
            0
        ].provider
        == accounts.ACCOUNT_PROVIDER
    )
    assert (
        len(accounts.vault.list_all_credentials(workspace_id=accounts.workspace_id))
        == 1
    )


def test_partial_delivery_is_visible_and_never_dispatched_again(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    account_id = _create_mailbox(accounts)
    scheduled_id = _schedule(accounts, account_id, "partial", 1)
    outcome = {
        "sent": False,
        "delivery_status": "partial",
        "refused_recipients": ["refused@example.org"],
        "message_id": "<partial@example.org>",
        "saved_to_sent": True,
    }
    monkeypatch.setattr(accounts, "send", lambda *args, **kwargs: outcome)
    assert accounts.process_scheduled() == {"sent": 0, "failed": 1}
    [partial] = accounts.list_scheduled("test")
    assert partial["status"] == "partial"
    assert partial["account_id"] == account_id
    assert partial["refused_recipients"] == ["refused@example.org"]
    assert partial["message_id"] == "<partial@example.org>"
    record = accounts.vault.reveal_secret(
        credential_id=UUID(scheduled_id), context=accounts.context
    )
    assert "private body" not in record
    assert "private@example.org" not in record
    assert json.loads(record)["extra_attachments"] == []
    assert accounts.process_scheduled() == {"sent": 0, "failed": 0}


def test_legacy_terminal_schedules_and_abandoned_idempotency_claims_expire(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return now + timedelta(days=31)

    for status in ("cancelled", "sent", "failed", "sending"):
        accounts.vault.create_credential(
            name=status,
            provider=accounts.SCHEDULE_PROVIDER,
            scopes=[],
            secret=json.dumps(
                {"status": status, "args": {"body_text": "private body"}}
            ),
            actor="test",
            workspace_id=accounts.workspace_id,
        )
    accounts.vault.create_credential(
        name="old-claim",
        provider=accounts.IDEMPOTENCY_PROVIDER,
        scopes=[],
        secret=json.dumps({"status": "processing"}),
        actor="test",
        workspace_id=accounts.workspace_id,
    )
    monkeypatch.setattr(email_client, "datetime", Clock)
    accounts.cleanup_idempotency()
    assert accounts.process_scheduled() == {"sent": 0, "failed": 1}
    [entry] = accounts.vault.list_all_credentials(workspace_id=accounts.workspace_id)
    assert entry.name == "sending"
    assert accounts.list_scheduled("test")[0]["status"] == "failed"


@pytest.mark.parametrize("encoding", ["base64", "8bit"])
def test_send_draft_preserves_encoded_body(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch, encoding: str
) -> None:
    account_id = _create_mailbox(accounts)
    message_id = email_client._b64(json.dumps([account_id, "Drafts", 12, 4]).encode())
    body = "café\n".encode()
    raw = (
        b"To: to@example.org\r\nBcc: bcc@example.org\r\nSubject: Draft\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: "
        + encoding.encode()
        + b"\r\n\r\n"
        + (base64.b64encode(body) if encoding == "base64" else body)
    )
    monkeypatch.setattr(accounts, "_fetch_raw", lambda *args: (raw, b"\\Draft"))
    monkeypatch.setattr(accounts, "_imap", lambda row: FakeMailbox({}))

    def deliver(
        row: dict[str, Any], message: EmailMessage, recipients: list[str]
    ) -> dict[str, Any]:
        wire = BytesParser(policy=policy.default).parsebytes(
            message.as_bytes(policy=email_client.OUTBOUND_POLICY)
        )
        assert wire.get_payload(decode=True) == body
        assert wire.get("Bcc") is None
        assert recipients == ["to@example.org", "bcc@example.org"]
        return {"sent": True}

    monkeypatch.setattr(accounts, "deliver", deliver)
    assert accounts.send_draft("test", message_id)["sent"] is True


class FakeMailbox:
    capabilities: tuple[str, ...] = ()

    def __init__(self, messages: dict[int, bytes]) -> None:
        self.messages = messages
        self.fetches: list[str] = []

    def select(self, folder: str, *, readonly: bool) -> tuple[str, list]:
        return "OK", []

    def response(self, field: str) -> tuple[str, list[bytes]]:
        return field, [b"12"]

    def logout(self) -> None:
        pass

    def uid(self, command: str, *args: str) -> tuple[str, list]:
        if command == "search":
            return "OK", [b" ".join(str(uid).encode() for uid in self.messages)]
        self.fetches.append(args[0])
        responses = []
        for uid in sorted(map(int, args[0].split(","))):
            prefix = f"{uid + 10} (UID {uid} FLAGS () RFC822.SIZE 100 ".encode()
            headers = f"Subject: message {uid}\r\n\r\n".encode()
            responses.extend(
                [
                    (
                        prefix
                        + b"BODY[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID)] {20}",
                        headers,
                    ),
                    b" BODYSTRUCTURE " + self.messages[uid] + b")",
                ]
            )
        return "OK", responses


def test_search_batches_headers_and_finds_nested_attachments(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    account_id = _create_mailbox(accounts)
    plain = b'("TEXT" "PLAIN" NIL NIL NIL "7BIT" 5 1 NIL NIL)'
    attachment = b'("APPLICATION" "PDF" NIL NIL NIL "BASE64" 10 NIL ("ATTACHMENT" ("FILENAME" "x.pdf")))'
    inline = (
        b'("IMAGE" "PNG" ("NAME" "logo.png") NIL NIL "BASE64" 10 NIL ("INLINE" NIL))'
    )
    mailbox = FakeMailbox(
        {
            1: b"(" + plain + inline + b' "MIXED" NIL NIL)',
            2: plain,
            3: b"(("
            + plain
            + attachment
            + b' "RELATED" NIL NIL)'
            + plain
            + b' "SIGNED" NIL NIL)',
        }
    )
    monkeypatch.setattr(accounts, "_imap", lambda row: mailbox)
    args = SearchEmails(account_id=account_id, limit=2).model_dump()
    page = accounts.search("test", args)
    assert mailbox.fetches == ["3,2"]
    assert [item["has_attachments"] for item in page["items"]] == [True, False]
    assert page["next_cursor"] == "12.2"
    filtered = accounts.search("test", {**args, "has_attachments": False})
    assert [item["subject"] for item in filtered["items"]] == ["message 2", "message 1"]

    opaque_id = page["items"][0]["message_id"]
    row, folder, validity, uid = accounts._message_location("test", opaque_id)
    assert (row["id"], folder, validity, uid) == (account_id, "INBOX", 12, 3)
    message = EmailMessage()
    message.set_content("body")
    message.add_attachment(
        b"notes", maintype="text", subtype="plain", filename="notes.txt"
    )
    monkeypatch.setattr(accounts, "_fetch_raw", lambda *args: (message.as_bytes(), b""))
    [attachment_info] = accounts.get_email("test", opaque_id)["email"]["attachments"]
    saved = accounts.download_attachment("test", attachment_info["attachment_id"])
    assert base64.b64decode(saved["content_base64"]) == b"notes"


def test_mail_server_rejects_mixed_public_and_private_dns_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def resolve(*args: Any, **kwargs: Any) -> list:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 993))
            for address in ("8.8.8.8", "192.168.1.5")
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with pytest.raises(ServiceError, match="public addresses"):
        email_client._server_ips("mail.example.org", 993)


def test_delivery_error_only_keeps_sanitized_smtp_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = email_client._delivery_error(
        smtplib.SMTPRecipientsRefused(
            {
                "secret@example.org": (550, b"5.1.1 secret@example.org was rejected"),
            }
        )
    )
    assert error.details["smtp_code"] == 550
    assert error.details["enhanced_status"] == "5.1.1"
    assert "secret@example.org" not in error.message + str(error.details) + caplog.text
