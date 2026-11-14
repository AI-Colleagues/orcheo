"""Fake IMAP and SMTP servers for the MCP email client tests.

The fakes sit where the sockets would: ``_PinnedIMAP`` and ``_PinnedSMTP`` are
replaced, so ``EmailAccounts._imap`` and ``_smtp`` and every operation built on
them run unchanged against in-memory mailboxes.
"""

from __future__ import annotations
import base64
import imaplib
import json
import re
import smtplib
from collections.abc import Iterable
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from typing import Any
import pytest
from orcheo.vault import InMemoryCredentialVault
from orcheo_backend.app.mcp_server import email_client
from orcheo_backend.app.mcp_server.email_client import EmailAccounts, EmailAccountSave


PUBLIC_IP = "93.184.216.34"
ACCOUNT_SECRET = "imap-secret"  # noqa: S105 - test fixture
SMTP_SECRET = "smtp-secret"  # noqa: S105 - test fixture


def raw_message(
    *,
    subject: str = "Hello",
    sender: str = "Ann <ann@example.org>",
    to: str = "me@example.org",
    cc: str | None = None,
    body: str = "Hi there",
    html: str | None = None,
    attachments: Iterable[tuple[str, str, bytes]] = (),
    headers: dict[str, str] | None = None,
    calendar: str | None = None,
) -> bytes:
    """A complete RFC 5322 message."""
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    if cc:
        message["Cc"] = cc
    message["Subject"] = subject
    message["Date"] = "Fri, 13 Nov 2026 10:00:00 +0000"
    message["Message-ID"] = f"<{abs(hash(subject)) % 10**8}@example.org>"
    for name, value in (headers or {}).items():
        del message[name]
        message[name] = value
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    if calendar:
        message.add_alternative(
            calendar, subtype="calendar", params={"method": "REQUEST"}
        )
    for filename, content_type, payload in attachments:
        maintype, _, subtype = content_type.partition("/")
        message.add_attachment(
            payload, maintype=maintype, subtype=subtype, filename=filename
        )
    return message.as_bytes()


def bodystructure(part: Message) -> str:
    """An IMAP BODYSTRUCTURE for a parsed message."""
    if part.is_multipart():
        children = " ".join(bodystructure(child) for child in part.iter_parts())
        return f'({children} "{part.get_content_subtype().upper()}" NIL NIL NIL)'
    maintype = part.get_content_maintype().upper()
    subtype = part.get_content_subtype().upper()
    filename = part.get_filename()
    params = f'("NAME" "{filename}")' if filename else '("CHARSET" "UTF-8")'
    disposition = part.get_content_disposition()
    disp = (
        f'("{disposition.upper()}" ("FILENAME" "{filename}"))' if disposition else "NIL"
    )
    if maintype == "TEXT":
        return f'("{maintype}" "{subtype}" {params} NIL NIL "7BIT" 10 1 NIL {disp} NIL)'
    return f'("{maintype}" "{subtype}" {params} NIL NIL "BASE64" 10 NIL {disp} NIL)'


@dataclass
class Stored:
    uid: int
    raw: bytes
    flags: set[str] = field(default_factory=set)

    def parsed(self) -> Message:
        return BytesParser(policy=policy.default).parsebytes(self.raw)


def _unquote(value: str) -> str:
    inner = value[1:-1] if value.startswith('"') else value
    inner = inner.replace('\\"', '"').replace("\\\\", "\\")
    return email_client._decode_imap_utf7(inner)  # noqa: SLF001


class FakeIMAP:
    """An imaplib.IMAP4 stand-in over in-memory folders."""

    def __init__(
        self,
        folders: dict[str, list[Stored]] | None = None,
        *,
        special: dict[str, str] | None = None,
        capabilities: tuple[str, ...] = ("UIDPLUS", "MOVE"),
        validity: bytes | None = b"12",
        list_rows: list[Any] | None = None,
    ) -> None:
        self.folders = folders if folders is not None else {"INBOX": []}
        self.special = special or {}
        self.capabilities = capabilities
        self.validity = validity
        self.list_rows = list_rows
        self.selected: str | None = None
        # ``fail`` answers NO and ``blank`` answers OK with no data, for either a
        # whole command ("fetch") or one form of it ("fetch:(BODY.PEEK[])").
        self.fail: set[str] = set()
        self.blank: set[str] = set()
        self.login_error: Exception | None = None
        self.starttls_error: Exception | None = None
        self.logout_error: Exception | None = None
        self.status_error: Exception | None = None
        self.search_results: list[int] | None = None
        self.next_uid = 100
        self.commands: list[tuple[Any, ...]] = []
        self.connections = 0
        self.logouts = 0
        self.starttls_calls = 0

    # -- helpers ----------------------------------------------------------
    def add(self, folder: str, raw: bytes, *flags: str, uid: int | None = None) -> int:
        uid = uid or self.next_uid
        self.next_uid = max(self.next_uid, uid) + 1
        self.folders.setdefault(folder, []).append(Stored(uid, raw, set(flags)))
        return uid

    def find(self, folder: str, uid: int) -> Stored | None:
        return next((m for m in self.folders.get(folder, []) if m.uid == uid), None)

    def _current(self) -> list[Stored]:
        assert self.selected is not None
        return self.folders[self.selected]

    # -- connection -------------------------------------------------------
    def starttls(self, ssl_context: Any = None) -> tuple[str, list]:
        self.starttls_calls += 1
        if self.starttls_error:
            raise self.starttls_error
        return "OK", []

    def login(self, username: str, password: str) -> tuple[str, list]:
        self.commands.append(("login", username, password))
        if self.login_error:
            raise self.login_error
        self.connections += 1
        return "OK", []

    def logout(self) -> tuple[str, list]:
        self.logouts += 1
        if self.logout_error:
            raise self.logout_error
        return "BYE", []

    # -- mailbox ----------------------------------------------------------
    def list(self) -> tuple[str, list[Any]]:
        if self.list_rows is not None:
            return "OK", self.list_rows
        rows: list[Any] = []
        for name in self.folders:
            attribute = self.special.get(name)
            flags = f"\\HasNoChildren {attribute}" if attribute else "\\HasNoChildren"
            rows.append(f'({flags}) "/" "{name}"'.encode())
        return "OK", rows

    def select(self, folder: str, readonly: bool = False) -> tuple[str, list]:
        self.commands.append(("select", folder, readonly))
        name = _unquote(folder)
        if (
            "select" in self.fail
            or (not readonly and "select:write" in self.fail)
            or name not in self.folders
        ):
            return "NO", [b"no such folder"]
        self.selected = name
        return "OK", [str(len(self.folders[name])).encode()]

    def response(self, field_name: str) -> tuple[str, list]:
        if self.validity is None:
            return field_name, []
        return field_name, [self.validity]

    def status(self, folder: str, what: str) -> tuple[str, list]:
        if self.status_error:
            raise self.status_error
        name = _unquote(folder)
        if name not in self.folders:
            return "NO", []
        messages = self.folders[name]
        unseen = sum("\\Seen" not in m.flags for m in messages)
        return "OK", [f'"{name}" (MESSAGES {len(messages)} UNSEEN {unseen})'.encode()]

    def append(
        self, folder: str, flags: str, date_time: Any, payload: bytes
    ) -> tuple[str, list]:
        self.commands.append(("append", folder, flags))
        if "append" in self.fail:
            return "NO", [b"append failed"]
        name = _unquote(folder)
        uid = self.add(name, payload, *re.findall(r"\\\w+", flags))
        if "UIDPLUS" not in self.capabilities:
            return "OK", [b"APPEND completed"]
        return "OK", [f"[APPENDUID 12 {uid}] APPEND completed".encode()]

    # -- UID commands -----------------------------------------------------
    def uid(self, command: str, *args: str) -> tuple[str, list[Any]]:
        self.commands.append((command, *args))
        form = f"{command}:{args[-1]}" if args else command
        if command in self.fail or form in self.fail:
            return "NO", [b"failed"]
        if form in self.blank:
            return "OK", []
        return getattr(self, f"_uid_{command}")(*args)

    def _uid_search(self, *criteria: str) -> tuple[str, list[Any]]:
        if self.search_results is not None:
            found = self.search_results
        elif criteria[0] in {"HEADER", "OR"}:
            matches, used = self._header_query(list(criteria), 0)
            assert used == len(criteria), "unsupported trailing search keys"
            found = [m.uid for m in self._current() if matches(m)]
        else:
            unseen = "UNSEEN" in criteria
            found = [
                m.uid for m in self._current() if not unseen or "\\Seen" not in m.flags
            ]
        return "OK", [b" ".join(str(uid).encode() for uid in found)]

    def _header_query(self, tokens: list[str], index: int) -> tuple[Any, int]:
        """Parse ``OR a b`` / ``HEADER field value`` into a predicate."""
        if tokens[index] == "OR":
            left, index = self._header_query(tokens, index + 1)
            right, index = self._header_query(tokens, index)
            return (lambda m: left(m) or right(m)), index
        assert tokens[index] == "HEADER", f"unsupported search key {tokens[index]}"
        field, value = tokens[index + 1], tokens[index + 2].strip('"')
        return (
            lambda m: value in str(m.parsed()[field] or ""),
            index + 3,
        )

    def _uid_fetch(self, uids: str, attributes: str) -> tuple[str, list[Any]]:
        stored = [self.find(self.selected or "", int(uid)) for uid in uids.split(",")]
        stored = [message for message in stored if message is not None]
        responses: list[Any] = []
        for message in stored:
            flags = " ".join(sorted(message.flags))
            head = f"{message.uid + 10} (UID {message.uid} FLAGS ({flags})"
            if "BODYSTRUCTURE" in attributes:
                headers = self._headers(message, "FROM TO CC SUBJECT DATE MESSAGE-ID")
                responses += [
                    (
                        f"{head} RFC822.SIZE {len(message.raw)} BODY[HEADER.FIELDS "
                        f"(FROM TO CC SUBJECT DATE MESSAGE-ID)] {{{len(headers)}}}".encode(),
                        headers,
                    ),
                    f" BODYSTRUCTURE {bodystructure(message.parsed())})".encode(),
                ]
            elif attributes == "(UID FLAGS RFC822.SIZE)":
                responses.append(f"{head} RFC822.SIZE {len(message.raw)})".encode())
            elif attributes == "(BODY.PEEK[])":
                responses += [
                    (
                        f"{message.uid + 10} (BODY[] {{{len(message.raw)}}}".encode(),
                        message.raw,
                    ),
                    b")",
                ]
            elif "HEADER.FIELDS (FROM TO CC)" in attributes:
                headers = self._headers(message, "FROM TO CC")
                responses += [
                    (
                        f"{message.uid + 10} (UID {message.uid} BODY[HEADER.FIELDS "
                        f"(FROM TO CC)] {{{len(headers)}}}".encode(),
                        headers,
                    ),
                    b")",
                ]
            else:
                headers = self._headers(message, "FROM TO CC SUBJECT DATE MESSAGE-ID")
                responses += [
                    (
                        f"{head} BODY[HEADER.FIELDS (FROM TO CC SUBJECT DATE "
                        f"MESSAGE-ID)] {{{len(headers)}}}".encode(),
                        headers,
                    ),
                    b")",
                ]
        return "OK", responses

    @staticmethod
    def _headers(message: Stored, names: str) -> bytes:
        parsed = message.parsed()
        lines = [
            f"{name.title()}: {parsed[name]}"
            for name in names.split()
            if parsed[name] is not None
        ]
        return ("\r\n".join(lines) + "\r\n\r\n").encode()

    def _uid_store(self, uid: str, operation: str, value: str) -> tuple[str, list]:
        message = self.find(self.selected or "", int(uid))
        if message is None:
            return "NO", []
        flags = set(re.findall(r"\\\w+", value))
        if operation.startswith("+FLAGS"):
            message.flags |= flags
        elif operation.startswith("-FLAGS"):
            message.flags -= flags
        return "OK", []

    def _uid_copy(self, uid: str, target: str) -> tuple[str, list]:
        message = self.find(self.selected or "", int(uid))
        if message is None:
            return "NO", []
        new_uid = self.add(_unquote(target), message.raw, *message.flags)
        return "OK", [self._copyuid(uid, new_uid, "Copy completed")]

    def _uid_move(self, uid: str, target: str) -> tuple[str, list]:
        kind, data = self._uid_copy(uid, target)
        if kind == "OK":
            self._current().remove(self.find(self.selected or "", int(uid)))  # type: ignore[arg-type]
        return kind, data

    def _uid_expunge(self, uid: str) -> tuple[str, list]:
        message = self.find(self.selected or "", int(uid))
        if message is not None and "\\Deleted" in message.flags:
            self._current().remove(message)
        return "OK", []

    def _copyuid(self, uid: str, new_uid: int, text: str) -> bytes:
        if "UIDPLUS" not in self.capabilities:
            return text.encode()
        return f"[COPYUID 12 {uid} {new_uid}] {text}".encode()


class FakeSMTP:
    """An smtplib.SMTP stand-in that records what it was asked to deliver."""

    def __init__(
        self,
        *,
        refused: dict[str, tuple[int, bytes]] | None = None,
        send_error: Exception | None = None,
        login_error: Exception | None = None,
        quit_error: Exception | None = None,
        starttls_error: Exception | None = None,
    ) -> None:
        self.refused = refused or {}
        self.send_error = send_error
        self.login_error = login_error
        self.quit_error = quit_error
        self.starttls_error = starttls_error
        self.sent: list[tuple[EmailMessage, str, list[str]]] = []
        self.calls: list[str] = []

    def ehlo(self) -> tuple[int, bytes]:
        self.calls.append("ehlo")
        return 250, b"ok"

    def starttls(self, context: Any = None) -> tuple[int, bytes]:
        self.calls.append("starttls")
        if self.starttls_error:
            raise self.starttls_error
        return 220, b"ready"

    def login(self, username: str, password: str) -> tuple[int, bytes]:
        self.calls.append("login")
        if self.login_error:
            raise self.login_error
        return 235, b"ok"

    def send_message(
        self, message: EmailMessage, from_addr: str, to_addrs: list[str]
    ) -> dict[str, tuple[int, bytes]]:
        self.calls.append("send_message")
        if self.send_error:
            raise self.send_error
        self.sent.append((message, from_addr, to_addrs))
        return self.refused

    def quit(self) -> tuple[int, bytes]:
        self.calls.append("quit")
        if self.quit_error:
            raise self.quit_error
        return 221, b"bye"


def install_servers(
    monkeypatch: pytest.MonkeyPatch,
    imap: FakeIMAP | None = None,
    smtp: FakeSMTP | None = None,
    *,
    ips: list[str] | None = None,
) -> None:
    """Route the client's pinned connections to the fakes."""
    monkeypatch.setattr(
        email_client, "_server_ips", lambda host, port: ips or [PUBLIC_IP]
    )
    if imap is not None:
        monkeypatch.setattr(
            email_client, "_PinnedIMAP", lambda host, port, address, ssl_on: imap
        )
    if smtp is not None:
        monkeypatch.setattr(
            email_client, "_PinnedSMTP", lambda host, port, address, ssl_on: smtp
        )


def in_sequence(monkeypatch: pytest.MonkeyPatch, *clients: Any) -> None:
    """Hand each new IMAP connection the next client; the last one repeats."""
    queue = list(clients)

    def connect(host: str, port: int, address: str, ssl_on: bool) -> Any:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(email_client, "_PinnedIMAP", connect)


def account_payload(**overrides: Any) -> dict[str, Any]:
    """A validated ``save_email_account`` payload."""
    base: dict[str, Any] = {
        "display_name": "Me",
        "email_address": "me@example.org",
        "imap": {
            "host": "imap.example.org",
            "port": 993,
            "security": "ssl",
            "username": "me",
            "password": ACCOUNT_SECRET,
        },
        "smtp": {
            "host": "smtp.example.org",
            "port": 465,
            "security": "ssl",
            "username": "me",
            "password": SMTP_SECRET,
        },
    }
    return EmailAccountSave.model_validate({**base, **overrides}).model_dump(
        mode="python"
    )


def new_accounts() -> EmailAccounts:
    from uuid import uuid4

    return EmailAccounts(InMemoryCredentialVault(), str(uuid4()))


def add_account(manager: EmailAccounts, **overrides: Any) -> str:
    """Save a mailbox and return its account ID."""
    return manager.save_account("test", account_payload(**overrides))["account_id"]


def message_id(account_id: str, folder: str, uid: int, validity: int = 12) -> str:
    """The opaque ID the client hands out for a message."""
    return email_client._b64(  # noqa: SLF001
        json.dumps([account_id, folder, validity, uid], separators=(",", ":")).encode()
    )


def attachment_id(message: str, index: int) -> str:
    return email_client._b64(  # noqa: SLF001
        json.dumps(["attachment", message, index], separators=(",", ":")).encode()
    )


def make_pdf(text: str) -> bytes:
    """A one-page PDF whose extractable text is ``text``."""
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body = b"%PDF-1.4\n"
    offsets = []
    for number, content in enumerate(objects, start=1):
        offsets.append(len(body))
        body += b"%d 0 obj\n" % number + content + b"\nendobj\n"
    xref = len(body)
    body += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    body += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    body += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (
        len(objects) + 1,
        xref,
    )
    return body


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


__all__ = [
    "ACCOUNT_SECRET",
    "PUBLIC_IP",
    "SMTP_SECRET",
    "FakeIMAP",
    "FakeSMTP",
    "Stored",
    "account_payload",
    "add_account",
    "attachment_id",
    "b64",
    "bodystructure",
    "make_pdf",
    "in_sequence",
    "install_servers",
    "message_id",
    "new_accounts",
    "raw_message",
    "smtplib",
    "imaplib",
]
