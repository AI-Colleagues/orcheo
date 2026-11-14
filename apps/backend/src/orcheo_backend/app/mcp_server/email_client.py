"""IMAP/SMTP operations for Orcheo's MCP tools.

Mailbox settings and passwords are stored as encrypted Orcheo vault secrets and
are never included in a tool result. Outbound connections require TLS and pin a
public DNS result for the lifetime of the socket so a configured host cannot
reach private services.
"""

# The mailbox protocol implementation preserves the compact public schemas and
# interoperable parsing helpers of the migrated client.
# ruff: noqa: D101,D102,D103,D107,E501,C901,PLR0911,PLR0912,PLR0915

from __future__ import annotations
import base64
import email
import email.policy
import hashlib
import html
import imaplib
import io
import ipaddress
import itertools
import json
import logging
import re
import smtplib
import socket
import ssl
import time
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import formataddr, formatdate, getaddresses, make_msgid, parseaddr
from html.parser import HTMLParser
from typing import Annotated, Any, Literal, cast
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from orcheo.models import CredentialAccessContext, CredentialMetadata, CredentialScope
from orcheo.vault import (
    BaseCredentialVault,
    CredentialNotFoundError,
    WorkflowScopeError,
)
from orcheo.workspace import WorkspaceAuditEvent
from orcheo_backend.app.credential_internal import EMAIL_ACCOUNT_CREDENTIAL_PROVIDER
from orcheo_backend.app.mcp_server.email_errors import ServiceError


MAX_READ_BATCH = 100
MAX_EMAIL_SIZE = 4_000_000
MAX_ATTACHMENT_SIZE = 500_000
EMAIL_RESULT_RETENTION = timedelta(days=1)
EMAIL_FAILURE_RETENTION = timedelta(days=30)
SCHEDULE_INTERRUPTED_AFTER = timedelta(minutes=15)
EMAIL_CREDENTIAL_ROLE = "email-tools"
# smtplib only negotiates BODY=8BITMIME for international envelope addresses.
# Encode bodies safely even when ordinary addresses carry Unicode text.
OUTBOUND_POLICY = email.policy.SMTP.clone(cte_type="7bit")
logger = logging.getLogger("orcheo.mcp_email")
MessageIdentifier = Annotated[str, Field(min_length=1, max_length=1000)]
EmailIdentifier = Annotated[str, Field(min_length=1, max_length=200)]
ShortText = Annotated[str, Field(max_length=500)]


class EmailArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmailProtocol(EmailArguments):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    security: Literal["ssl", "starttls"] = "ssl"
    username: str = Field(min_length=1, max_length=320)
    password: str | None = Field(default=None, max_length=512)

    @field_validator("host", "username")
    @classmethod
    def trim_required(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Enter a valid server name or username.")
        return value


class EmailAccountSave(EmailArguments):
    account_id: EmailIdentifier | None = None
    display_name: str = Field(min_length=1, max_length=100)
    email_address: str = Field(min_length=3, max_length=320)
    labels: list[ShortText] = Field(default_factory=list, max_length=20)
    is_default: bool = False
    enabled: bool = True
    imap: EmailProtocol
    smtp: EmailProtocol
    signature_text: str = Field(default="", max_length=2000)
    save_sent_copy: bool = True

    @field_validator("display_name", "email_address")
    @classmethod
    def trim_value(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Enter valid text without control characters.")
        return value

    @field_validator("email_address")
    @classmethod
    def valid_email(cls, value: str) -> str:
        name, address = parseaddr(value)
        if name or address != value or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+", value):
            raise ValueError("Enter a valid email address.")
        return value

    @field_validator("labels")
    @classmethod
    def clean_labels(cls, labels: list[str]) -> list[str]:
        cleaned = list(
            dict.fromkeys(label.strip() for label in labels if label.strip())
        )
        if any(
            len(label) > 100 or any(ord(char) < 32 for char in label)
            for label in cleaned
        ):
            raise ValueError("Labels must be 1–100 characters.")
        return cleaned

    @model_validator(mode="after")
    def require_new_account_passwords(self) -> EmailAccountSave:
        if not self.account_id and (not self.imap.password or not self.smtp.password):
            raise ValueError("Enter an IMAP and SMTP password for a new account.")
        return self


class EmailAccountIdentifier(EmailArguments):
    account_id: EmailIdentifier


class TestEmailAccount(EmailAccountIdentifier):
    protocol: Literal["imap", "smtp", "both"] = "both"


class SearchEmails(EmailArguments):
    account_id: EmailIdentifier | None = None
    folder: str = Field(
        default="INBOX",
        min_length=1,
        max_length=255,
        description="A folder name, or one of inbox, sent, drafts, trash, junk, archive, all.",
    )
    unread_only: bool = False
    from_address: ShortText | None = None
    to_address: ShortText | None = None
    subject: ShortText | None = None
    text: ShortText | None = Field(
        default=None, description="Words anywhere in the headers or body."
    )
    flagged: bool | None = None
    answered: bool | None = None
    larger_than_kb: int | None = Field(default=None, ge=1, le=100_000)
    has_attachments: bool | None = None
    after: str | None = Field(default=None, max_length=10)
    before: str | None = Field(default=None, max_length=10)
    limit: int = Field(default=20, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=40)

    @field_validator("cursor")
    @classmethod
    def numeric_cursor(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"(\d+\.)?\d+", value):
            raise ValueError("Email page cursor is invalid.")
        return value

    @model_validator(mode="after")
    def valid_range(self) -> SearchEmails:
        for value in (self.after, self.before):
            if value is not None:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError("Use dates in YYYY-MM-DD format.")
                try:
                    date.fromisoformat(value)
                except ValueError as error:
                    raise ValueError("Use dates in YYYY-MM-DD format.") from error
        if (
            self.after
            and self.before
            and date.fromisoformat(self.after) > date.fromisoformat(self.before)
        ):
            raise ValueError("The start date must not be after the end date.")
        return self


class GetEmail(EmailArguments):
    message_id: MessageIdentifier
    include_html: bool = Field(
        default=False,
        description="Also return the HTML body. The text body is usually enough.",
    )
    max_body_chars: int = Field(default=20_000, ge=500, le=100_000)


class EmailAttachment(EmailArguments):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream", max_length=150)
    content_base64: str = Field(min_length=1, max_length=700_000)

    @field_validator("filename")
    @classmethod
    def valid_filename(cls, value: str) -> str:
        if (
            any(ord(character) < 32 for character in value)
            or "/" in value
            or "\\" in value
        ):
            raise ValueError("Attachment filename is invalid.")
        return value


class DraftEmail(EmailArguments):
    account_id: EmailIdentifier | None = None
    to: list[str] = Field(default_factory=list, max_length=50)
    cc: list[str] = Field(default_factory=list, max_length=50)
    bcc: list[str] = Field(default_factory=list, max_length=50)
    reply_to: str | None = Field(default=None, max_length=320)
    subject: str = Field(min_length=1, max_length=998)
    body_text: str | None = Field(default=None, max_length=100_000)
    body_html: str | None = Field(default=None, max_length=100_000)
    in_reply_to: str | None = Field(default=None, max_length=998)
    references: list[str] = Field(default_factory=list, max_length=20)
    attachments: list[EmailAttachment] = Field(default_factory=list, max_length=3)
    include_signature: bool = True
    idempotency_key: str | None = Field(
        default=None,
        max_length=100,
        description="Your own key for this send; a retry with the same key is not sent twice.",
    )

    @field_validator("to", "cc", "bcc")
    @classmethod
    def valid_addresses(cls, values: list[str]) -> list[str]:
        for value in values:
            if "\r" in value or "\n" in value:
                raise ValueError("Use valid email addresses.")
            name, address = parseaddr(value)
            if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+", address) or (
                not name and address != value.strip()
            ):
                raise ValueError(
                    "Use email addresses, optionally with a name: Ada <ada@example.com>."
                )
        return list(dict.fromkeys(values))

    @field_validator("reply_to")
    @classmethod
    def valid_reply_to(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return cls.valid_addresses([value])[0]

    @field_validator("subject", "in_reply_to")
    @classmethod
    def no_header_injection(cls, value: str | None) -> str | None:
        if value is not None and ("\r" in value or "\n" in value):
            raise ValueError("Email headers cannot contain line breaks.")
        return value

    @field_validator("references")
    @classmethod
    def valid_references(cls, values: list[str]) -> list[str]:
        if any("\r" in value or "\n" in value for value in values):
            raise ValueError("Email references cannot contain line breaks.")
        return values

    @model_validator(mode="after")
    def valid_message(self) -> DraftEmail:
        if not self.body_text and not self.body_html:
            raise ValueError("Provide a text or HTML message body.")
        if sum(map(len, (self.to, self.cc, self.bcc))) == 0:
            raise ValueError("Provide at least one recipient.")
        if sum(len(item.content_base64) for item in self.attachments) > 700_000:
            raise ValueError("Attachments must total 500 KB or less.")
        for item in self.attachments:
            try:
                raw = base64.b64decode(item.content_base64, validate=True)
            except ValueError as error:
                raise ValueError("Attachment content must be valid base64.") from error
            if len(raw) > MAX_ATTACHMENT_SIZE:
                raise ValueError("Each attachment must be 500 KB or less.")
        return self


class SendEmail(DraftEmail):
    send_at: str | None = Field(
        default=None,
        max_length=40,
        description="Schedule for later (ISO 8601 with offset), up to 30 days ahead.",
    )

    @field_validator("send_at")
    @classmethod
    def aware_send_at(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("Use an ISO 8601 time with a UTC offset.") from error
            if parsed.utcoffset() is None:
                raise ValueError("Use an ISO 8601 time with a UTC offset.")
        return value


class EmailMessageIds(EmailArguments):
    message_ids: list[MessageIdentifier] = Field(
        min_length=1, max_length=MAX_READ_BATCH
    )

    @field_validator("message_ids")
    @classmethod
    def unique_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Provide each email identifier once.")
        return values


class DownloadEmailAttachment(EmailArguments):
    attachment_id: MessageIdentifier


class EmailActions(EmailMessageIds):
    action: Literal[
        "archive",
        "move",
        "copy",
        "flag",
        "unflag",
        "delete",
        "junk",
        "not_junk",
        "add_labels",
        "remove_labels",
        "mark_answered",
    ]
    target_folder: str | None = Field(default=None, max_length=255)
    labels: list[ShortText] = Field(default_factory=list, max_length=10)
    # Deleting moves to Trash, where the user can still recover a message an
    # assistant picked by mistake. Expunging is the explicit exception.
    permanent: bool = False

    @model_validator(mode="after")
    def move_requires_folder(self) -> EmailActions:
        needs_folder = self.action in {"move", "copy"}
        if needs_folder and not self.target_folder:
            raise ValueError("Choose a destination folder for moving or copying.")
        if not needs_folder and self.target_folder:
            raise ValueError(
                "A destination folder is only used when moving or copying."
            )
        if self.permanent and self.action != "delete":
            raise ValueError("permanent only applies to deleting messages.")
        if (self.action in {"add_labels", "remove_labels"}) != bool(self.labels):
            raise ValueError(
                "Labels go with add_labels or remove_labels, and only then."
            )
        if any(
            not label.strip() or re.search(r'[\s()"\\{}%*\]]', label)
            for label in self.labels
        ) and not all(label.startswith("\\") for label in self.labels):
            raise ValueError("Labels are single words without brackets or quotes.")
        return self


_SPECIAL_FOLDERS = {
    "archive": (rb"\\archive", {"archive", "archives"}),
    "trash": (
        rb"\\trash",
        {"trash", "deleted items", "deleted messages", "[gmail]/trash", "inbox.trash"},
    ),
    "sent": (
        rb"\\sent",
        {
            "sent",
            "sent items",
            "sent messages",
            "sent mail",
            "[gmail]/sent mail",
            "inbox.sent",
        },
    ),
    "drafts": (rb"\\drafts", {"drafts", "draft", "[gmail]/drafts", "inbox.drafts"}),
    "junk": (
        rb"\\junk",
        {"junk", "spam", "junk e-mail", "junk email", "[gmail]/spam", "inbox.junk"},
    ),
    "all": (rb"\\all", {"all mail", "[gmail]/all mail"}),
}
FOLDER_ALIASES = frozenset({"sent", "drafts", "trash", "junk", "archive", "all"})


def _special_folders(client: imaplib.IMAP4, kind: str) -> list[str]:
    r"""The server's own names for a special-use folder, best first.

    The RFC 6154 special-use flag wins; a conventional name is the fallback
    for servers that do not advertise one. Several folders can carry the
    same flag: Dovecot's stock config marks both "Sent" and Apple Mail's
    "Sent Messages" as \\Sent. The one named after the role is the server's
    own, the folder its webmail shows, so that one comes first.
    """
    flag, names = _SPECIAL_FOLDERS[kind]
    _, folders = client.list()
    named: list[str] = []
    flagged: list[str] = []
    by_name = None
    for folder_row in folders or []:
        if not isinstance(folder_row, bytes):
            continue
        match = re.search(
            rb'\((.*?)\)\s+(?:"([^"]*)"|NIL)\s+("(?:[^"\\]|\\.)*"|[^\s]+)$',
            folder_row,
        )
        if not match:
            continue
        candidate = match.group(3).decode("utf-8", "replace").strip('"')
        if re.search(flag + rb"(?:\s|$)", match.group(1).lower()):
            delimiter = (match.group(2) or b"").decode("utf-8", "replace")
            leaf = candidate.rsplit(delimiter, 1)[-1] if delimiter else candidate
            (named if leaf.lower() == kind else flagged).append(candidate)
        if by_name is None and candidate.lower() in names:
            by_name = candidate
    return named + flagged or ([by_name] if by_name else [])


def _special_folder(client: imaplib.IMAP4, kind: str) -> str | None:
    """The one folder to store into or move to for a special-use role."""
    return next(iter(_special_folders(client, kind)), None)


def _decode_imap_utf7(value: str) -> str:
    """A mailbox name from LIST, in modified UTF-7, as the Unicode it names."""

    def decode(match: re.Match[str]) -> str:
        chunk = match.group(1)
        if not chunk:
            return "&"
        raw = chunk.replace(",", "/")
        raw += "=" * (-len(raw) % 4)
        try:
            return base64.b64decode(raw).decode("utf-16-be")
        except (ValueError, UnicodeDecodeError):
            return match.group(0)

    return re.sub(r"&([A-Za-z0-9+,]*)-", decode, value)


def _list_folders(client: imaplib.IMAP4) -> list[tuple[str, str]]:
    """Each folder as (Unicode name, lowercased LIST attributes)."""
    _, rows = client.list()
    folders = []
    for folder_row in rows or []:
        if not isinstance(folder_row, bytes):
            continue
        match = re.search(
            rb'\((.*?)\)\s+(?:"(?:[^"]*)"|NIL)\s+("(?:[^"\\]|\\.)*"|[^\s]+)$',
            folder_row,
        )
        if not match:
            continue
        wire = match.group(2).decode("utf-8", "replace").strip('"')
        folders.append(
            (_decode_imap_utf7(wire), match.group(1).decode("ascii", "replace").lower())
        )
    return folders


def _resolve_folder(client: imaplib.IMAP4, folder: str) -> str:
    """A folder name, with the special-use aliases looked up on this server."""
    alias = folder.strip().lower()
    if alias == "inbox":
        return "INBOX"
    if alias in FOLDER_ALIASES:
        found = _special_folder(client, alias)
        # No special-use folder: the name may be a real folder all the same.
        if found:
            return _decode_imap_utf7(found)
    return _clean_folder(folder)


def _clean_folder(folder: str) -> str:
    if (
        len(folder) > 255
        or any(ord(character) < 32 for character in folder)
        or "\x7f" in folder
    ):
        raise ServiceError("email.invalid_folder", "Folder name is invalid.", 400)
    return folder


def _imap_quote(value: str) -> str:
    _clean_folder(value)
    # IMAP mailbox names use modified UTF-7 unless UTF8=ACCEPT is negotiated.
    encoded: list[str] = []
    non_ascii: list[str] = []

    def flush() -> None:
        if non_ascii:
            raw = "".join(non_ascii).encode("utf-16-be")
            encoded.append(
                "&" + base64.b64encode(raw).decode().rstrip("=").replace("/", ",") + "-"
            )
            non_ascii.clear()

    for character in value:
        if 0x20 <= ord(character) <= 0x7E:
            flush()
            encoded.append("&-" if character == "&" else character)
        else:
            non_ascii.append(character)
    flush()
    return _imap_raw_quote("".join(encoded))


def _imap_raw_quote(value: str) -> str:
    _clean_folder(value)
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, UnicodeDecodeError) as error:
        raise ServiceError(
            "email.invalid_id", "Email identifier is invalid.", 400
        ) from error


def _uidvalidity(client: imaplib.IMAP4) -> int | None:
    """The selected folder's UIDVALIDITY, or None when the server gave no number."""
    values = client.response("UIDVALIDITY")[1]
    value = values[0] if values else None
    return int(value) if isinstance(value, bytes) and value.isdigit() else None


def _parse_attachment_id(attachment_id: str) -> tuple[str, int]:
    """The message and MIME part index an attachment identifier names."""
    try:
        kind, message_id, index = json.loads(_unb64(attachment_id))
        if (
            kind != "attachment"
            or not isinstance(message_id, str)
            or not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
        ):
            raise ValueError
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise ServiceError(
            "email.invalid_attachment", "Attachment identifier is invalid.", 400
        ) from error
    return message_id, index


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeDecodeError, ValueError):
        return value


def _is_attachment(part: Message) -> bool:
    disposition = part.get_content_disposition()
    return disposition == "attachment" or (
        disposition != "inline" and bool(part.get_filename())
    )


_IMAP_QUOTED_STRING = rb'"(?:[^"\\]|\\.)*"'
_IMAP_VALUE = re.compile(_IMAP_QUOTED_STRING + rb'|[()]|[^\s()"]+')
_FETCH_START = re.compile(rb"^\d+ \(")
_HEADER_LITERAL = re.compile(rb"BODY\[HEADER\.FIELDS \([^)]*\)\] \{\d+\}$", re.I)
_LITERAL_MARKER = re.compile(rb"\{\d+\}$")


def _bodystructure_has_attachment(metadata: bytes) -> bool | None:
    """Read disposition and filename parameters from IMAP MIME metadata."""
    start = re.search(rb"\bBODYSTRUCTURE\s+(?=\()", metadata, re.I)
    if not start:
        return None
    stack: list[list[Any]] = []
    for match in _IMAP_VALUE.finditer(metadata, start.end()):
        token = match.group()
        if token == b"(":
            child: list[Any] = []
            if stack:
                stack[-1].append(child)
            stack.append(child)
            if len(stack) > 100:
                return None
        elif token == b")":
            body = stack.pop()
            if not stack:
                return _mime_structure_has_attachment(body)
        else:
            stack[-1].append(token.strip(b'"').upper())
    return None


def _mime_structure_has_attachment(body: list[Any]) -> bool:
    if not body:
        return False
    if isinstance(body[0], list):
        children = list(itertools.takewhile(lambda part: isinstance(part, list), body))
        params_index = len(children) + 1
        disposition_index = params_index + 1
    else:
        media = tuple(body[:2])
        children = (
            body[8:9]
            if media in {(b"MESSAGE", b"RFC822"), (b"MESSAGE", b"GLOBAL")}
            else []
        )
        params_index = 2
        # Single parts have seven base fields. Text adds a line count, and
        # embedded messages add an envelope, body and line count before MD5.
        disposition_index = 11 if children else 9 if body[0] == b"TEXT" else 8
    disposition = next(iter(body[disposition_index:]), None)
    if isinstance(disposition, list) and disposition:
        if disposition[0] == b"ATTACHMENT":
            return True
    params = next(iter(body[params_index:]), None)
    if (
        not (isinstance(disposition, list) and disposition[0:1] == [b"INLINE"])
        and isinstance(params, list)
        and any(
            name == b"NAME" or isinstance(name, bytes) and name.startswith(b"NAME*")
            for name in params[::2]
        )
    ):
        return True
    return any(
        _mime_structure_has_attachment(child)
        for child in children
        if isinstance(child, list)
    )


def _fetch_search_page(
    client: imaplib.IMAP4, uids: Sequence[int]
) -> dict[int, tuple[bytes, bytes]]:
    """Fetch a page of headers and MIME structures, keyed by returned UID."""
    kind, data = client.uid(
        "fetch",
        ",".join(map(str, uids)),
        "(UID FLAGS RFC822.SIZE BODYSTRUCTURE BODY.PEEK[HEADER.FIELDS "
        "(FROM TO CC SUBJECT DATE MESSAGE-ID)])",
    )
    if kind != "OK":
        return {}
    results: dict[int, tuple[bytes, bytes]] = {}
    metadata = bytearray()
    headers = None
    for entry in [*data, b"0 ("]:
        if entry is None:
            continue
        fragment = entry[0] if isinstance(entry, tuple) else entry
        if _FETCH_START.match(fragment):
            if headers is not None:
                attributes = re.sub(_IMAP_QUOTED_STRING, b'""', metadata)
                uid = re.search(rb"\bUID (\d+)", attributes, re.I)
                if uid:
                    results[int(uid.group(1))] = (bytes(metadata), headers)
            metadata.clear()
            headers = None
        if isinstance(entry, tuple):
            if _HEADER_LITERAL.search(fragment):
                headers = entry[1]
                literal = b'""'
            else:
                literal = (
                    b'"' + entry[1].replace(b"\\", b"\\\\").replace(b'"', b'\\"') + b'"'
                )
            marker = _LITERAL_MARKER.search(fragment)
            if marker:
                fragment = fragment[: marker.start()] + literal
        metadata.extend(fragment)
    return results


def _part_payload(part: Message) -> bytes:
    payload = part.get_payload(decode=True)
    return payload if isinstance(payload, bytes) else b""


def _part_text(part: Message) -> str:
    """A text part's content, decoded leniently.

    Senders name charsets Python has never heard of; the words in the message
    are still worth returning, so those fall back to UTF-8.
    """
    payload = _part_payload(part)
    try:
        return payload.decode(part.get_content_charset() or "utf-8", "replace")
    except LookupError:
        return payload.decode("utf-8", "replace")


def _append_imap_message(
    client: imaplib.IMAP4, folder: str, message: EmailMessage, flags: str
) -> tuple[str, list[bytes | None]]:
    # imaplib returns the status and response data, though typeshed says str.
    return cast(
        tuple[str, list[bytes | None]],
        client.append(
            _imap_raw_quote(folder),
            flags,
            imaplib.Time2Internaldate(time.time()),
            message.as_bytes(policy=OUTBOUND_POLICY),
        ),
    )


def _email_text(message: EmailMessage, *, html_body: bool = False) -> str:
    chunks: list[str] = []
    wanted = "html" if html_body else "plain"
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_content_disposition() == "attachment" or part.get_filename():
            continue
        if part.get_content_type() != f"text/{wanted}":
            continue
        try:
            content = cast(EmailMessage, part).get_content()
        except (LookupError, UnicodeDecodeError, email.errors.MessageError):
            content = _part_text(part)
        if isinstance(content, str):
            chunks.append(content)
    return "\n".join(chunks)


class _TextOfHTML(HTMLParser):
    """Readable text from an HTML body: block breaks kept, scripts dropped."""

    BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in {"script", "style", "head"}:
            self.hidden += 1
        elif tag in self.BLOCKS:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.chunks.append(data)


def html_to_text(html: str) -> str:
    parser = _TextOfHTML()
    parser.feed(html)
    text = "".join(parser.chunks)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


_ICS_ESCAPE = re.compile(r"\\(.)", re.DOTALL)
_ICS_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _ics_unescape(match: re.Match[str]) -> str:
    """One escape sequence of an iCalendar TEXT value (RFC 5545 section 3.3.11)."""
    return "\n" if match.group(1) in "nN" else match.group(1)


def _ics_text(value: str) -> str:
    """Escape untrusted text for an iCalendar TEXT value.

    Backslash goes first so the escapes added after it are not doubled, and line
    breaks become escaped newlines so a value can never start a new content line.
    """
    escaped = value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    escaped = escaped.replace("\r\n", "\\n").replace("\r", "\\n")
    return _ICS_CONTROL.sub("", escaped.replace("\n", "\\n"))


def _ics_param(value: str) -> str:
    """A property parameter value that cannot end the parameter or the line."""
    cleaned = _ICS_CONTROL.sub("", " ".join(value.split())).replace('"', "'")
    return f'"{cleaned}"' if any(char in cleaned for char in ";:,") else cleaned


def _unfold_ics(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line[:1] in {" ", "\t"} and lines:
            lines[-1] += line[1:]
        elif line:
            lines.append(line)
    return lines


def parse_calendar(text: str) -> list[dict[str, Any]]:
    """The events in an iCalendar part: what an invitation asks of you."""
    method = None
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in _unfold_ics(text):
        name, _, value = line.partition(":")
        key, *params = name.split(";")
        key = key.upper()
        if key == "METHOD":
            method = value.strip().upper()
        elif key == "BEGIN" and value.strip().upper() == "VEVENT":
            current = {"attendees": []}
        elif key == "END" and value.strip().upper() == "VEVENT" and current is not None:
            events.append(current)
            current = None
        elif current is not None:
            unescaped = _ICS_ESCAPE.sub(_ics_unescape, value)
            field = {
                "UID": "uid",
                "SUMMARY": "summary",
                "DTSTART": "starts_at",
                "DTEND": "ends_at",
                "LOCATION": "location",
                "DESCRIPTION": "description",
                "SEQUENCE": "sequence",
                "STATUS": "status",
            }.get(key)
            if field:
                current[field] = unescaped
                tzid = next(
                    (
                        p.split("=", 1)[1]
                        for p in params
                        if p.upper().startswith("TZID=")
                    ),
                    None,
                )
                if tzid and field in {"starts_at", "ends_at"}:
                    current["timezone"] = tzid
            elif key == "ORGANIZER":
                current["organizer"] = value.removeprefix("mailto:").removeprefix(
                    "MAILTO:"
                )
            elif key == "ATTENDEE":
                current["attendees"].append(
                    value.removeprefix("mailto:").removeprefix("MAILTO:")
                )
    return [{**event, "method": method} for event in events]


def _calendar_parts(message: email.message.Message) -> list[dict[str, Any]]:
    invites: list[dict[str, Any]] = []
    for part in message.walk():
        if part.get_content_type() != "text/calendar":
            continue
        invites.extend(parse_calendar(_part_text(part)))
    return invites


# Providers whose SMTP service files the sent message itself; appending
# another copy over IMAP would leave two in Sent.
AUTO_SENT_HOSTS = (
    "gmail.com",
    "googlemail.com",
    "office365.com",
    "outlook.com",
    "live.com",
)


def _auto_saves_sent(smtp_host: str) -> bool:
    host = smtp_host.lower().rstrip(".")
    return any(
        host == domain or host.endswith("." + domain) for domain in AUTO_SENT_HOSTS
    )


def _summary(
    message: email.message.Message, message_id: str, flags: bytes
) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "from": _decode_header(message.get("From")),
        "to": _decode_header(message.get("To")),
        "cc": _decode_header(message.get("Cc")),
        "subject": _decode_header(message.get("Subject")),
        "date": message.get("Date"),
        "unread": b"\\Seen" not in flags,
    }


def _server_ips(host: str, port: int) -> list[str]:
    host = host.strip().rstrip(".")
    if not host or any(character in host for character in "/\\@\r\n\t "):
        raise ServiceError("email.invalid_host", "Mail server address is invalid.", 400)
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise ServiceError(
            "email.connection_failed", "Mail server could not be reached.", 502
        ) from error
    addresses = list(dict.fromkeys(str(answer[4][0]) for answer in answers))
    if not addresses or any(
        not ipaddress.ip_address(value).is_global for value in addresses
    ):
        raise ServiceError(
            "email.invalid_host", "Mail servers must resolve to public addresses.", 400
        )
    return addresses


class _PinnedIMAP(imaplib.IMAP4):
    def __init__(self, host: str, port: int, address: str, use_ssl: bool) -> None:
        self._pinned_address = address
        self._use_ssl = use_ssl
        self.ssl_context = ssl.create_default_context()
        super().__init__(host=host, port=port, timeout=15)

    def _create_socket(self, timeout: float | None) -> socket.socket:
        raw = socket.create_connection((self._pinned_address, self.port), timeout)
        if self._use_ssl:
            return self.ssl_context.wrap_socket(raw, server_hostname=self.host)
        return raw


class _PinnedSMTP(smtplib.SMTP):
    def __init__(self, host: str, port: int, address: str, use_ssl: bool) -> None:
        self._pinned_address = address
        self._use_ssl = use_ssl
        self.context = ssl.create_default_context()
        super().__init__(host=host, port=port, timeout=15)

    def _get_socket(self, host: str, port: int, timeout: float | None) -> socket.socket:
        raw = socket.create_connection((self._pinned_address, port), timeout)
        if self._use_ssl:
            return self.context.wrap_socket(raw, server_hostname=host)
        return raw


def _delivery_error(error: Exception) -> ServiceError:
    """Keep actionable SMTP status without exposing server-supplied mail data."""
    stage = "connection"
    responses: list[tuple[int, bytes | str]] = []
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        stage = "recipients"
        responses = list(error.recipients.values())
    elif isinstance(error, smtplib.SMTPResponseException):
        stage = (
            "sender"
            if isinstance(error, smtplib.SMTPSenderRefused)
            else "content"
            if isinstance(error, smtplib.SMTPDataError)
            else "connection"
        )
        responses = [(error.smtp_code, error.smtp_error)]
    elif isinstance(error, smtplib.SMTPNotSupportedError):
        stage = "content"

    rejected = isinstance(
        error,
        (
            smtplib.SMTPResponseException,
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPNotSupportedError,
        ),
    )
    details: dict[str, Any] = {
        "smtp_stage": stage,
        "delivery_status": "rejected" if rejected else "unknown",
    }
    codes = []
    # Compare only sanitized status fields. Reply prose often differs for each
    # recipient and must neither hide a shared code nor expose a Bcc address.
    smtp_codes = {code for code, _ in responses}
    if len(smtp_codes) == 1:
        code = smtp_codes.pop()
        details["smtp_code"] = code
        codes.append(f"SMTP {code}")
    enhanced_statuses = set()
    for _, reply in responses:
        reply_text = (
            reply.decode("ascii", errors="replace")
            if isinstance(reply, bytes)
            else reply
        )
        enhanced = re.match(r"\s*([245]\.\d{1,3}\.\d{1,3})(?:\s|$)", reply_text)
        enhanced_statuses.add(enhanced.group(1) if enhanced else None)
    if len(enhanced_statuses) == 1 and (enhanced_status := enhanced_statuses.pop()):
        details["enhanced_status"] = enhanced_status
        codes.append(enhanced_status)
    if rejected:
        message = {
            "sender": "The SMTP server rejected the sender address.",
            "recipients": "The SMTP server rejected the recipients.",
            "content": "The SMTP server rejected the message content or format.",
            "connection": "The SMTP server rejected the connection.",
        }[stage]
        if codes:
            message = f"{message[:-1]} ({', '.join(codes)})."
    else:
        message = (
            "The SMTP connection was interrupted and delivery could not be confirmed. "
            "Check Sent before retrying to avoid a duplicate."
        )
    logger.warning(
        "SMTP delivery failed: stage=%s delivery_status=%s smtp_code=%s enhanced_status=%s",
        stage,
        details["delivery_status"],
        details.get("smtp_code"),
        details.get("enhanced_status"),
    )
    return ServiceError("email.send_failed", message, 502, details=details)


class EmailAccounts:
    """Workspace-scoped IMAP/SMTP adapter backed by encrypted vault entries."""

    ACCOUNT_PROVIDER = EMAIL_ACCOUNT_CREDENTIAL_PROVIDER
    SCHEDULE_PROVIDER = "orcheo-email-schedule"
    IDEMPOTENCY_PROVIDER = "orcheo-email-idempotency"

    def __init__(
        self, vault: BaseCredentialVault, workspace_id: str, *, actor: str = "mcp"
    ) -> None:
        self.vault = vault
        self.workspace_id = str(workspace_id)
        self.actor = actor
        try:
            workspace_uuid = uuid.UUID(self.workspace_id)
        except ValueError as error:
            raise ServiceError(
                "email.workspace_unavailable", "The active workspace is invalid.", 503
            ) from error
        self.workspace_uuid = workspace_uuid
        self.context = CredentialAccessContext(
            workspace_id=workspace_uuid, roles=[EMAIL_CREDENTIAL_ROLE]
        )

    def _metadata(self, provider: str) -> list[CredentialMetadata]:
        return [
            item
            for item in self.vault.list_credentials(
                context=self.context, workspace_id=self.workspace_id
            )
            if item.workspace_id == self.workspace_id
            and item.provider == provider
            and item.template_id is None
        ]

    def _secret(self, metadata: CredentialMetadata) -> dict[str, Any]:
        try:
            parsed = json.loads(metadata.reveal(cipher=self.vault.cipher))
            if not isinstance(parsed, dict):
                raise ValueError("not an object")
            return parsed
        except (CredentialNotFoundError, ValueError, TypeError) as error:
            raise ServiceError(
                "email.credential_unavailable",
                "Stored email settings are unavailable. Re-enter them in the vault.",
                503,
            ) from error

    @staticmethod
    def _row_from(
        metadata: CredentialMetadata, config: dict[str, Any]
    ) -> dict[str, Any]:
        imap = config.get("imap") or {}
        smtp = config.get("smtp") or {}
        return {
            "id": str(metadata.id),
            "created_at": metadata.created_at.isoformat(),
            "display_name": config.get("display_name") or metadata.name,
            "email_address": config.get("email_address") or "",
            "labels_json": config.get("labels") or [],
            "is_default": bool(config.get("is_default")),
            "enabled": bool(config.get("enabled", True)),
            "imap_host": imap.get("host", ""),
            "imap_port": int(imap.get("port", 993)),
            "imap_security": imap.get("security", "ssl"),
            "imap_username": imap.get("username", ""),
            "imap_secret": imap.get("password", ""),
            "imap_status": config.get("imap_status", "not-tested"),
            "imap_tested_at": config.get("imap_tested_at"),
            "smtp_host": smtp.get("host", ""),
            "smtp_port": int(smtp.get("port", 465)),
            "smtp_security": smtp.get("security", "ssl"),
            "smtp_username": smtp.get("username", ""),
            "smtp_secret": smtp.get("password", ""),
            "smtp_status": config.get("smtp_status", "not-tested"),
            "smtp_tested_at": config.get("smtp_tested_at"),
            "signature_text": config.get("signature_text", ""),
            "save_sent_copy": bool(config.get("save_sent_copy", True)),
            "_credential": metadata,
            "_config": config,
        }

    def _all_rows(self) -> list[dict[str, Any]]:
        return [
            self._row_from(item, self._secret(item))
            for item in self._metadata(self.ACCOUNT_PROVIDER)
        ]

    def _row(self, actor_id: str, account_id: str) -> dict[str, Any]:
        del actor_id  # The vault's workspace and credential scope are authoritative.
        try:
            metadata = next(
                item
                for item in self._metadata(self.ACCOUNT_PROVIDER)
                if str(item.id) == account_id
            )
        except StopIteration as error:
            raise ServiceError(
                "email.account_not_found", "Email account not found.", 404
            ) from error
        return self._row_from(metadata, self._secret(metadata))

    def _write(self, row: dict[str, Any], config: dict[str, Any]) -> None:
        self.vault.update_credential(
            credential_id=row["_credential"].id,
            actor=self.actor,
            secret=json.dumps(config, separators=(",", ":")),
            context=self.context,
        )

    def _account_result(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "account_id": row["id"],
            "display_name": row["display_name"],
            "email_address": row["email_address"],
            "labels": row["labels_json"],
            "is_default": bool(row["is_default"]),
            "enabled": bool(row["enabled"]),
            "signature_text": row["signature_text"],
            "save_sent_copy": bool(row["save_sent_copy"]),
            "imap": {
                "host": row["imap_host"],
                "port": row["imap_port"],
                "security": row["imap_security"],
                "username": row["imap_username"],
                "status": row["imap_status"],
                "tested_at": row["imap_tested_at"],
            },
            "smtp": {
                "host": row["smtp_host"],
                "port": row["smtp_port"],
                "security": row["smtp_security"],
                "username": row["smtp_username"],
                "status": row["smtp_status"],
                "tested_at": row["smtp_tested_at"],
            },
        }

    def get_account_settings(self, actor_id: str, account_id: str) -> dict[str, Any]:
        return self._account_result(self._row(actor_id, account_id))

    def _ensure_default(self) -> None:
        rows = sorted(
            self._all_rows(),
            key=lambda row: (not row["enabled"], row["created_at"], row["id"]),
        )
        if not any(row["is_default"] for row in rows) and rows:
            rows[0]["_config"]["is_default"] = True
            self._write(rows[0], rows[0]["_config"])

    def list_accounts(self, actor_id: str) -> list[dict[str, Any]]:
        rows = self._all_rows()
        rows.sort(key=lambda row: (not row["is_default"], row["created_at"], row["id"]))
        return [self._account_result(row) for row in rows]

    def list_agent_accounts(self, actor_id: str) -> list[dict[str, Any]]:
        return [
            {
                **{
                    key: account[key]
                    for key in (
                        "account_id",
                        "display_name",
                        "email_address",
                        "labels",
                        "is_default",
                        "enabled",
                        "save_sent_copy",
                    )
                },
                "has_signature": bool(account["signature_text"]),
                "health": {
                    protocol: {
                        "status": account[protocol]["status"],
                        "tested_at": account[protocol]["tested_at"],
                    }
                    for protocol in ("imap", "smtp")
                },
            }
            for account in self.list_accounts(actor_id)
        ]

    def set_default(self, actor_id: str, account_id: str) -> dict[str, Any]:
        with self.vault.credential_lock(f"email-accounts:{self.workspace_id}"):
            self._set_default(actor_id, account_id)
        return {"account_id": account_id, "is_default": True}

    def _set_default(self, actor_id: str, account_id: str) -> None:
        target = self._row(actor_id, account_id)
        if not target["enabled"]:
            raise ServiceError(
                "email.account_disabled", "Enable this account before using it.", 409
            )
        for row in self._all_rows():
            config = row["_config"]
            wanted = row["id"] == account_id
            if bool(config.get("is_default")) != wanted:
                config["is_default"] = wanted
                self._write(row, config)

    def prepare_account(
        self, actor_id: str, data: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Validate edits and return the account row plus encrypted-secret payload."""
        current = (
            self._row(actor_id, data["account_id"]) if data.get("account_id") else None
        )
        for protocol in ("imap", "smtp"):
            settings = data[protocol]
            endpoint_changed = current and any(
                settings[field] != current[f"{protocol}_{field}"]
                for field in ("host", "port", "security", "username")
            )
            if not settings.get("password") and (not current or endpoint_changed):
                raise ServiceError(
                    "email.password_required",
                    f"{protocol.upper()} password is required when its server settings change.",
                    400,
                )
            if not settings.get("password") and current:
                settings["password"] = current[f"{protocol}_secret"]
        config = {
            "display_name": data["display_name"],
            "email_address": data["email_address"],
            "labels": data["labels"],
            "is_default": bool(data.get("is_default")),
            "enabled": bool(data.get("enabled", True)),
            "imap": data["imap"],
            "smtp": data["smtp"],
            "signature_text": data.get("signature_text", ""),
            "save_sent_copy": bool(data.get("save_sent_copy", True)),
            "imap_status": "not-tested",
            "smtp_status": "not-tested",
            "imap_tested_at": None,
            "smtp_tested_at": None,
        }
        return current, config

    def save_account(self, actor_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Store mailbox settings and return the updated, non-secret settings."""
        with self.vault.credential_lock(f"email-accounts:{self.workspace_id}"):
            current, config = self.prepare_account(actor_id, data)
            if config["is_default"] and not config["enabled"]:
                raise ServiceError(
                    "email.account_disabled",
                    "Enable this account before using it.",
                    409,
                )
            name = f"mailbox-{data['email_address'].lower()}"
            secret = json.dumps(config, separators=(",", ":"))
            if current:
                metadata = self.vault.update_credential(
                    credential_id=current["_credential"].id,
                    actor=self.actor,
                    name=name,
                    secret=secret,
                    context=self.context,
                )
            else:
                metadata = self.vault.create_credential(
                    name=name,
                    provider=self.ACCOUNT_PROVIDER,
                    scopes=["email:imap", "email:smtp"],
                    secret=secret,
                    actor=self.actor,
                    scope=CredentialScope.for_roles(EMAIL_CREDENTIAL_ROLE),
                    workspace_id=self.workspace_id,
                )
            account_id = str(metadata.id)
            if config["is_default"]:
                self._set_default(actor_id, account_id)
            else:
                self._ensure_default()
            return self.get_account_settings(actor_id, account_id)

    def delete_account(self, actor_id: str, account_id: str) -> dict[str, Any]:
        with self.vault.credential_lock(f"email-accounts:{self.workspace_id}"):
            row = self._row(actor_id, account_id)
            self.vault.delete_credential(row["_credential"].id, context=self.context)
            self._ensure_default()
        return {"account_id": account_id, "deleted": True}

    def _selected_account(
        self, actor_id: str, account_id: str | None
    ) -> dict[str, Any]:
        if account_id:
            row = self._row(actor_id, account_id)
        else:
            rows = [row for row in self._all_rows() if row["enabled"]]
            rows.sort(
                key=lambda row: (not row["is_default"], row["created_at"], row["id"])
            )
            if not rows:
                raise ServiceError(
                    "email.account_required", "Add an email account first.", 409
                )
            row = rows[0]
        if not row["enabled"]:
            raise ServiceError(
                "email.account_disabled", "This email account is disabled.", 409
            )
        return row

    def _credentials(self, row: dict[str, Any], protocol: str) -> dict[str, Any]:
        config = row["_config"][protocol]
        try:
            from orcheo_backend.app.workspace import get_workspace_repository

            get_workspace_repository().record_audit_event(
                WorkspaceAuditEvent(
                    workspace_id=uuid.UUID(self.workspace_id),
                    action="vault.email_account_used",
                    actor=self.actor,
                    subject=self.actor,
                    resource_type="credential",
                    resource_id=row["id"],
                    details={"protocol": protocol},
                )
            )
        except Exception:  # Audit failure must not disclose secrets or break mail.
            logger.warning("Could not record email credential usage")
        return {
            key: config[key]
            for key in ("host", "port", "security", "username", "password")
        }

    def _imap(self, row: dict[str, Any]) -> imaplib.IMAP4:
        config = self._credentials(row, "imap")
        addresses = _server_ips(config["host"], config["port"])
        last_error: Exception | None = None
        for address in addresses:
            client = None
            try:
                client = _PinnedIMAP(
                    config["host"],
                    config["port"],
                    address,
                    config["security"] == "ssl",
                )
                if config["security"] == "starttls":
                    client.starttls(ssl_context=ssl.create_default_context())
            except (OSError, imaplib.IMAP4.abort) as error:
                last_error = error
                if client is not None:
                    try:
                        client.logout()
                    except (OSError, imaplib.IMAP4.error):
                        pass
                continue
            except imaplib.IMAP4.error as error:
                if client is not None:
                    try:
                        client.logout()
                    except (OSError, imaplib.IMAP4.error):
                        pass
                raise ServiceError(
                    "email.imap_failed",
                    "Could not connect to the IMAP server. Check its settings and password.",
                    502,
                ) from error
            try:
                client.login(config["username"], config["password"])
                return client
            except (OSError, imaplib.IMAP4.abort, ssl.SSLError) as error:
                last_error = error
                try:
                    client.logout()
                except (OSError, imaplib.IMAP4.error):
                    pass
            except imaplib.IMAP4.error as error:
                try:
                    client.logout()
                except (OSError, imaplib.IMAP4.error):
                    pass
                raise ServiceError(
                    "email.imap_failed",
                    "Could not connect to the IMAP server. Check its settings and password.",
                    502,
                ) from error
        raise ServiceError(
            "email.imap_failed",
            "Could not connect to the IMAP server. Check its settings and password.",
            502,
        ) from last_error

    def _smtp(self, row: dict[str, Any]) -> smtplib.SMTP:
        config = self._credentials(row, "smtp")
        addresses = _server_ips(config["host"], config["port"])
        last_error: Exception | None = None
        for address in addresses:
            client = None
            try:
                client = _PinnedSMTP(
                    config["host"],
                    config["port"],
                    address,
                    config["security"] == "ssl",
                )
                if config["security"] == "starttls":
                    client.ehlo()
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
            except (OSError, smtplib.SMTPException) as error:
                if client is not None:
                    try:
                        client.quit()
                    except (OSError, smtplib.SMTPException):
                        pass
                if isinstance(error, smtplib.SMTPException) and not isinstance(
                    error, smtplib.SMTPServerDisconnected
                ):
                    raise ServiceError(
                        "email.smtp_failed",
                        "Could not connect to the SMTP server. Check its settings and password.",
                        502,
                    ) from error
                last_error = error
                continue
            try:
                client.login(config["username"], config["password"])
                return client
            except (OSError, smtplib.SMTPException) as error:
                try:
                    client.quit()
                except (OSError, smtplib.SMTPException):
                    pass
                if isinstance(error, smtplib.SMTPException) and not isinstance(
                    error, smtplib.SMTPServerDisconnected
                ):
                    raise ServiceError(
                        "email.smtp_failed",
                        "Could not connect to the SMTP server. Check its settings and password.",
                        502,
                    ) from error
                last_error = error
        raise ServiceError(
            "email.smtp_failed",
            "Could not connect to the SMTP server. Check its settings and password.",
            502,
        ) from last_error

    def test_account(
        self, actor_id: str, account_id: str, protocol: str
    ) -> dict[str, Any]:
        row = self._row(actor_id, account_id)
        if not row["enabled"]:
            raise ServiceError(
                "email.account_disabled", "Enable this account before testing it.", 409
            )
        protocols = ("imap", "smtp") if protocol == "both" else (protocol,)
        outcomes: dict[str, Any] = {}
        tested_at: dict[str, str] = {}
        for name in protocols:
            try:
                if name == "imap":
                    client = self._imap(row)
                    try:
                        client.logout()
                    except (OSError, imaplib.IMAP4.error):
                        pass
                else:
                    smtp = self._smtp(row)
                    smtp.quit()
                status = "connected"
                message = "Connection successful."
            except ServiceError as error:
                status = "failed"
                message = error.message
            tested_at[name] = datetime.now(UTC).isoformat()
            outcomes[name] = {"status": status, "message": message}
        # The connections can take a while. Record only the outcome, on the
        # settings as they are now, so a save made meanwhile is not reverted.
        with self.vault.credential_lock(f"email-accounts:{self.workspace_id}"):
            current = self._row(actor_id, account_id)
            config = current["_config"]
            for name, outcome in outcomes.items():
                config[f"{name}_status"] = outcome["status"]
                config[f"{name}_tested_at"] = tested_at[name]
            self._write(current, config)
        return {"account_id": account_id, "connections": outcomes}

    @staticmethod
    def _decode_message_id(message_id: str) -> tuple[str, str, int, int]:
        try:
            parts = json.loads(_unb64(message_id))
            if not isinstance(parts, list) or len(parts) != 4:
                raise ValueError
            account_id, folder, uidvalidity, uid = parts
            if not isinstance(account_id, str) or not isinstance(folder, str):
                raise ValueError
            uidvalidity, uid = int(uidvalidity), int(uid)
            if uidvalidity < 1 or uid < 1:
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise ServiceError(
                "email.invalid_id", "Email identifier is invalid.", 400
            ) from error
        return account_id, folder, uidvalidity, uid

    def _message_location(
        self, actor_id: str, message_id: str
    ) -> tuple[dict[str, Any], str, int, int]:
        account_id, folder, uidvalidity, uid = self._decode_message_id(message_id)
        return self._selected_account(actor_id, account_id), folder, uidvalidity, uid

    def _group_messages(
        self, actor_id: str, message_ids: list[str]
    ) -> tuple[
        dict[tuple[str, str, int], list[tuple[int, str]]], dict[str, dict[str, Any]]
    ]:
        """Group messages by folder generation, resolving each mailbox once."""
        rows: dict[str, dict[str, Any]] = {}
        groups: dict[tuple[str, str, int], list[tuple[int, str]]] = {}
        for message_id in message_ids:
            account_id, folder, validity, uid = self._decode_message_id(message_id)
            if account_id not in rows:
                rows[account_id] = self._selected_account(actor_id, account_id)
            groups.setdefault((account_id, folder, validity), []).append(
                (uid, message_id)
            )
        return groups, rows

    def _fetch_raw(
        self, client: imaplib.IMAP4, folder: str, uidvalidity: int, uid: int
    ) -> tuple[bytes, bytes]:
        kind, _ = client.select(_imap_quote(folder), readonly=True)
        if kind != "OK":
            raise ServiceError(
                "email.folder_not_found", "Mail folder could not be opened.", 404
            )
        if _uidvalidity(client) != uidvalidity:
            raise ServiceError(
                "email.message_expired",
                "This email identifier is no longer valid. Search the folder again.",
                404,
            )
        kind, data = client.uid("fetch", str(uid), "(UID FLAGS RFC822.SIZE)")
        if kind != "OK":
            raise ServiceError("email.message_not_found", "Email not found.", 404)
        # Metadata-only FETCH responses are bytes; responses with literals are
        # tuples. Normalize both forms and ignore trailing protocol delimiters.
        for entry in data or []:
            metadata = entry[0] if isinstance(entry, tuple) else entry
            if not isinstance(metadata, bytes):
                continue
            size = re.search(rb"RFC822\.SIZE\s+(\d+)", metadata, re.I)
            if size:
                break
        else:
            raise ServiceError("email.message_not_found", "Email not found.", 404)
        if int(size.group(1)) > MAX_EMAIL_SIZE:
            raise ServiceError(
                "email.message_too_large", "This email is too large to fetch.", 413
            )
        flags_match = re.search(rb"FLAGS\s+\(([^)]*)\)", metadata, re.I)
        flags = flags_match.group(1) if flags_match else b""
        kind, data = client.uid("fetch", str(uid), "(BODY.PEEK[])")
        if kind != "OK":
            raise ServiceError("email.message_not_found", "Email not found.", 404)
        for entry in data or []:
            if isinstance(entry, tuple) and isinstance(entry[1], bytes):
                return entry[1], flags
        raise ServiceError("email.message_not_found", "Email not found.", 404)

    def search(self, actor_id: str, args: dict[str, Any]) -> dict[str, Any]:
        row = self._selected_account(actor_id, args.get("account_id"))
        _clean_folder(args["folder"])
        client = self._imap(row)
        try:
            folder = _resolve_folder(client, args["folder"])
            kind, _ = client.select(_imap_quote(folder), readonly=True)
            if kind != "OK":
                raise ServiceError(
                    "email.folder_not_found", "Mail folder could not be opened.", 404
                )
            uidvalidity = _uidvalidity(client)
            if uidvalidity is None:
                raise ServiceError(
                    "email.invalid_mailbox",
                    "The IMAP server did not provide a stable mailbox identifier.",
                    502,
                )
            criteria: list[str] = ["UNSEEN"] if args["unread_only"] else ["ALL"]
            if args.get("after"):
                criteria.extend(
                    ["SINCE", date.fromisoformat(args["after"]).strftime("%d-%b-%Y")]
                )
            if args.get("before"):
                exclusive_before = date.fromisoformat(args["before"]) + timedelta(
                    days=1
                )
                criteria.extend(["BEFORE", exclusive_before.strftime("%d-%b-%Y")])
            for key, yes, no in (
                ("flagged", "FLAGGED", "UNFLAGGED"),
                ("answered", "ANSWERED", "UNANSWERED"),
            ):
                if args.get(key) is not None:
                    criteria.append(yes if args[key] else no)
            if args.get("larger_than_kb"):
                criteria.extend(["LARGER", str(int(args["larger_than_kb"]) * 1024)])
            for key, criterion in (
                ("from_address", "FROM"),
                ("to_address", "TO"),
                ("subject", "SUBJECT"),
                ("text", "TEXT"),
            ):
                if args.get(key):
                    value = str(args[key]).replace("\r", " ").replace("\n", " ")
                    if not value.isascii():
                        raise ServiceError(
                            "email.search_charset",
                            "Email search filters currently support ASCII text only.",
                            400,
                        )
                    criteria.extend([criterion, _imap_raw_quote(value)])
            kind, data = client.uid("search", *criteria)
            if kind != "OK":
                raise ServiceError(
                    "email.search_failed",
                    "The IMAP server could not search this folder.",
                    502,
                )
            uids = sorted(
                (int(item) for item in (data[0].split() if data and data[0] else [])),
                reverse=True,
            )
            if args.get("cursor"):
                # A cursor names the folder generation it was made in: after
                # the server renumbers the folder, its UIDs mean other mail.
                cursor_validity, _, cursor_uid = args["cursor"].rpartition(".")
                try:
                    before_uid = int(cursor_uid)
                    expired = (
                        bool(cursor_validity) and int(cursor_validity) != uidvalidity
                    )
                except ValueError as error:
                    raise ServiceError(
                        "email.invalid_cursor", "Email page cursor is invalid.", 400
                    ) from error
                if expired:
                    raise ServiceError(
                        "email.cursor_expired",
                        "This folder changed since the last page. Search again.",
                        409,
                    )
                uids = [uid for uid in uids if uid < before_uid]
            items: list[dict[str, Any]] = []
            skipped: list[int] = []
            examined = 0
            limit = args["limit"]
            attachment_filter = args.get("has_attachments")
            batch_size = limit if attachment_filter is None else 50
            for batch in itertools.batched(uids, batch_size):
                fetched = _fetch_search_page(client, batch)
                for uid in batch:
                    examined += 1
                    if uid not in fetched:
                        skipped.append(uid)
                        continue
                    metadata, raw_headers = fetched[uid]
                    has_attachments = _bodystructure_has_attachment(metadata)
                    if attachment_filter is not None and has_attachments is None:
                        skipped.append(uid)
                        continue
                    if (
                        attachment_filter is not None
                        and has_attachments != attachment_filter
                    ):
                        continue
                    parsed = BytesParser(policy=email.policy.default).parsebytes(
                        raw_headers
                    )
                    opaque_id = _b64(
                        json.dumps(
                            [row["id"], folder, uidvalidity, uid], separators=(",", ":")
                        ).encode()
                    )
                    attributes = re.sub(_IMAP_QUOTED_STRING, b'""', metadata)
                    flags_match = re.search(rb"FLAGS\s+\(([^)]*)\)", attributes, re.I)
                    flags = flags_match.group(1) if flags_match else b""
                    size = re.search(rb"RFC822\.SIZE\s+(\d+)", attributes, re.I)
                    items.append(
                        {
                            **_summary(parsed, opaque_id, flags),
                            "flagged": b"\\Flagged" in flags,
                            "answered": b"\\Answered" in flags,
                            "has_attachments": has_attachments,
                            "size": int(size.group(1)) if size else None,
                        }
                    )
                    if len(items) == limit:
                        break
                if len(items) == limit:
                    break
            next_cursor = (
                f"{uidvalidity}.{uids[examined - 1]}"
                if 0 < examined < len(uids)
                else None
            )
            result = {
                "account_id": row["id"],
                "folder": folder,
                "items": items,
                "next_cursor": next_cursor,
            }
            if skipped:
                result["skipped_uids"] = skipped
            return result
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass

    def get_email(
        self,
        actor_id: str,
        message_id: str,
        *,
        include_html: bool = True,
        max_body_chars: int | None = None,
    ) -> dict[str, Any]:
        row, folder, uidvalidity, uid = self._message_location(actor_id, message_id)
        client = self._imap(row)
        try:
            raw, flags = self._fetch_raw(client, folder, uidvalidity, uid)
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
        parsed = BytesParser(policy=email.policy.default).parsebytes(raw)
        summary = _summary(parsed, message_id, flags)
        attachments = []
        for index, part in enumerate(parsed.walk()):
            filename = part.get_filename()
            if _is_attachment(part):
                payload = _part_payload(part)
                attachment_id = _b64(
                    json.dumps(
                        ["attachment", message_id, index], separators=(",", ":")
                    ).encode()
                )
                attachments.append(
                    {
                        "attachment_id": attachment_id,
                        "filename": _decode_header(filename) or "attachment",
                        "content_type": part.get_content_type(),
                        "size": len(payload),
                    }
                )
        body_html = _email_text(parsed, html_body=True)
        body_text = _email_text(parsed)
        if not body_text and body_html:
            # HTML-only mail still has words in it.
            body_text = html_to_text(body_html)
        result = {
            **summary,
            "reply_to": _decode_header(parsed.get("Reply-To")),
            "message_header_id": parsed.get("Message-ID"),
            "in_reply_to": parsed.get("In-Reply-To"),
            "references": parsed.get_all("References", []),
            "body_text": body_text,
            "attachments": attachments,
        }
        if max_body_chars and len(body_text) > max_body_chars:
            result["body_text"] = body_text[:max_body_chars]
            result["body_text_truncated"] = True
        if include_html:
            result["body_html"] = body_html
            if max_body_chars and len(body_html) > max_body_chars * 3:
                result["body_html"] = body_html[: max_body_chars * 3]
                result["body_html_truncated"] = True
        invites = _calendar_parts(parsed)
        if invites:
            result["calendar_invites"] = invites
        return {"email": result}

    def download_attachment(self, actor_id: str, attachment_id: str) -> dict[str, Any]:
        message_id, index = _parse_attachment_id(attachment_id)
        row, folder, uidvalidity, uid = self._message_location(actor_id, message_id)
        client = self._imap(row)
        try:
            raw, _ = self._fetch_raw(client, folder, uidvalidity, uid)
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
        message = BytesParser(policy=email.policy.default).parsebytes(raw)
        parts = list(message.walk())
        if index >= len(parts):
            raise ServiceError(
                "email.attachment_not_found", "Attachment not found.", 404
            )
        part = parts[index]
        payload = _part_payload(part)
        if len(payload) > MAX_ATTACHMENT_SIZE:
            raise ServiceError(
                "email.attachment_too_large",
                "Attachments must be 500 KB or less to download.",
                413,
            )
        return {
            "filename": _decode_header(part.get_filename()) or "attachment",
            "content_type": part.get_content_type(),
            "content_base64": base64.b64encode(payload).decode(),
        }

    def mark_read(
        self, actor_id: str, message_ids: list[str], is_read: bool
    ) -> dict[str, Any]:
        # Keep the original IDs alongside UIDs for an exact, actor-scoped result.
        grouped_ids, rows = self._group_messages(actor_id, message_ids)
        if len(grouped_ids) > 5:
            raise ServiceError(
                "email.too_many_mailboxes",
                "Mark messages from at most five account and folder combinations at a time.",
                400,
            )
        affected: list[str] = []
        failed: list[dict[str, str]] = []
        for (account_id, folder, validity), entries in grouped_ids.items():
            client = self._imap(rows[account_id])
            try:
                kind, _ = client.select(_imap_quote(folder), readonly=False)
                if kind != "OK" or _uidvalidity(client) != validity:
                    failed.extend(
                        {"message_id": message_id, "code": "email.message_expired"}
                        for _, message_id in entries
                    )
                    continue
                for uid, message_id in entries:
                    operation = "+FLAGS.SILENT" if is_read else "-FLAGS.SILENT"
                    kind, _ = client.uid("store", str(uid), operation, "(\\Seen)")
                    if kind == "OK":
                        affected.append(message_id)
                    else:
                        failed.append(
                            {"message_id": message_id, "code": "email.action_failed"}
                        )
            finally:
                try:
                    client.logout()
                except (OSError, imaplib.IMAP4.error):
                    pass
        result: dict[str, Any] = {
            "is_read": is_read,
            "affected_count": len(affected),
            "message_ids": affected,
        }
        if failed:
            result["failed"] = failed
        return result

    def manage_messages(self, actor_id: str, args: dict[str, Any]) -> dict[str, Any]:
        grouped, rows = self._group_messages(actor_id, args["message_ids"])
        if len(grouped) > 5:
            raise ServiceError(
                "email.too_many_mailboxes",
                "Manage messages from at most five account and folder combinations at a time.",
                400,
            )
        affected: list[str] = []
        failed: list[dict[str, str]] = []
        moved: list[dict[str, str]] = []
        for (account_id, folder, validity), entries in grouped.items():
            row = rows[account_id]
            client = self._imap(row)
            try:
                kind, _ = client.select(_imap_quote(folder), readonly=False)
                if kind != "OK" or _uidvalidity(client) != validity:
                    failed.extend(
                        {"message_id": message_id, "code": "email.message_expired"}
                        for _, message_id in entries
                    )
                    continue
                plan = self._action_plan(client, folder, args)
                for uid, message_id in entries:
                    kind, data = self._apply_action(client, uid, plan)
                    if kind == "OK":
                        affected.append(message_id)
                        new_id = self._copied_id(row["id"], plan, data, uid)
                        if new_id:
                            moved.append(
                                {"message_id": message_id, "new_message_id": new_id}
                            )
                    else:
                        failed.append(
                            {"message_id": message_id, "code": "email.action_failed"}
                        )
            finally:
                try:
                    client.logout()
                except (OSError, imaplib.IMAP4.error):
                    pass
        result: dict[str, Any] = {
            "action": args["action"],
            "affected_count": len(affected),
            "message_ids": affected,
        }
        if failed:
            result["failed"] = failed
        if moved:
            result["moved"] = moved
        if args["action"] == "delete":
            result["permanent"] = bool(args.get("permanent"))
        return result

    def _action_plan(
        self, client: imaplib.IMAP4, folder: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """What one action means on this server, worked out once per folder."""
        action = args["action"]
        gmail = "X-GM-EXT-1" in client.capabilities
        if action in {"move", "copy"}:
            return {
                "kind": action,
                "target": _resolve_folder(client, args["target_folder"]),
            }
        if action == "archive":
            target = _special_folder(client, "archive")
            if target:
                return {"kind": "move", "target": _decode_imap_utf7(target)}
            if gmail and folder.upper() == "INBOX":
                # Gmail archives by taking the Inbox label off.
                return {"kind": "store", "op": "-X-GM-LABELS", "value": "(\\Inbox)"}
            raise ServiceError(
                "email.archive_unsupported",
                "This mailbox has no Archive folder. Use manage_email_messages with action=move and a destination folder instead.",
                409,
            )
        if action == "delete" and not args.get("permanent"):
            target = _special_folder(client, "trash")
            if not target:
                raise ServiceError(
                    "email.trash_unsupported",
                    "This mailbox has no Trash folder. Deleting would be permanent; ask the user before retrying with permanent=true.",
                    409,
                )
            if _decode_imap_utf7(target) == folder:
                raise ServiceError(
                    "email.already_in_trash",
                    "These messages are already in Trash. Deleting them again is permanent; ask the user before retrying with permanent=true.",
                    409,
                )
            return {"kind": "move", "target": _decode_imap_utf7(target)}
        if action == "delete":
            return {"kind": "expunge"}
        if action == "junk":
            target = _special_folder(client, "junk")
            if not target:
                raise ServiceError(
                    "email.junk_unsupported", "This mailbox has no Junk folder.", 409
                )
            return {"kind": "move", "target": _decode_imap_utf7(target)}
        if action == "not_junk":
            return {"kind": "move", "target": "INBOX"}
        if action in {"add_labels", "remove_labels"}:
            sign = "+" if action == "add_labels" else "-"
            labels = " ".join(_imap_raw_quote(label) for label in args["labels"])
            if gmail:
                return {
                    "kind": "store",
                    "op": f"{sign}X-GM-LABELS",
                    "value": f"({labels})",
                }
            # Elsewhere a label is an IMAP keyword, which takes atoms only.
            atoms = " ".join(
                re.sub(r"[^A-Za-z0-9_.-]", "_", label) for label in args["labels"]
            )
            return {"kind": "store", "op": f"{sign}FLAGS.SILENT", "value": f"({atoms})"}
        flag = {
            "flag": ("+FLAGS.SILENT", "(\\Flagged)"),
            "unflag": ("-FLAGS.SILENT", "(\\Flagged)"),
            "mark_answered": ("+FLAGS.SILENT", "(\\Answered)"),
        }[action]
        return {"kind": "store", "op": flag[0], "value": flag[1]}

    @staticmethod
    def _apply_action(
        client: imaplib.IMAP4, uid: int, plan: dict[str, Any]
    ) -> tuple[str, Any]:
        if plan["kind"] == "store":
            return client.uid("store", str(uid), plan["op"], plan["value"])
        if plan["kind"] == "expunge":
            if "UIDPLUS" not in client.capabilities:
                raise ServiceError(
                    "email.delete_unsupported",
                    "This server cannot safely delete individual messages.",
                    409,
                )
            kind, data = client.uid("store", str(uid), "+FLAGS.SILENT", "(\\Deleted)")
            if kind == "OK":
                kind, data = client.uid("expunge", str(uid))
            return kind, data
        target = _imap_quote(plan["target"])
        if plan["kind"] == "copy":
            return client.uid("copy", str(uid), target)
        if "MOVE" in client.capabilities:
            return client.uid("move", str(uid), target)
        if "UIDPLUS" in client.capabilities:
            kind, data = client.uid("copy", str(uid), target)
            if kind == "OK":
                copied = data
                kind, _ = client.uid("store", str(uid), "+FLAGS.SILENT", "(\\Deleted)")
                if kind == "OK":
                    kind, _ = client.uid("expunge", str(uid))
                return kind, copied
            return kind, data
        raise ServiceError(
            "email.move_unsupported",
            "This server cannot safely move individual messages.",
            409,
        )

    @staticmethod
    def _copied_id(
        account_id: str, plan: dict[str, Any], data: Any, uid: int
    ) -> str | None:
        """The message's ID in its new folder, from the server's COPYUID answer."""
        if plan["kind"] not in {"move", "copy"}:
            return None
        text = b" ".join(
            item if isinstance(item, bytes) else b""
            for item in (data or [])
            if item is not None
        )
        match = re.search(rb"COPYUID\s+(\d+)\s+([\d:,]+)\s+([\d:,]+)", text, re.I)
        if not match or b"," in match.group(3) or b":" in match.group(3):
            return None
        return _b64(
            json.dumps(
                [account_id, plan["target"], int(match.group(1)), int(match.group(3))],
                separators=(",", ":"),
            ).encode()
        )

    def compose(
        self,
        row: dict[str, Any],
        args: dict[str, Any],
        *,
        extra_attachments: Sequence[tuple[bytes, str, str, str]] = (),
    ) -> EmailMessage:
        """Build the message, signed when the account has a signature."""
        message = EmailMessage(policy=OUTBOUND_POLICY)
        message["From"] = formataddr((row["display_name"], row["email_address"]))
        message["To"] = (
            ", ".join(args["to"]) if args["to"] else "undisclosed-recipients:;"
        )
        if args.get("cc"):
            message["Cc"] = ", ".join(args["cc"])
        if args.get("reply_to"):
            message["Reply-To"] = args["reply_to"]
        message["Subject"] = args["subject"]
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(
            domain=row["email_address"].rpartition("@")[2]
        )
        if args.get("in_reply_to"):
            message["In-Reply-To"] = args["in_reply_to"]
        if args.get("references"):
            message["References"] = " ".join(args["references"])
        signature = (row.get("signature_text") or "").strip()
        body_text = args.get("body_text")
        body_html = args.get("body_html")
        if signature and args.get("include_signature", True):
            if body_text:
                body_text = f"{body_text.rstrip()}\n\n-- \n{signature}"
            if body_html:
                escaped = html.escape(signature).replace("\n", "<br>")
                body_html = f"{body_html}<br><br>-- <br>{escaped}"
        if body_text:
            message.set_content(body_text)
            if body_html:
                message.add_alternative(body_html, subtype="html")
        else:
            message.set_content(body_html, subtype="html")
        for item in args.get("attachments") or []:
            payload = base64.b64decode(item["content_base64"], validate=True)
            maintype, _, subtype = item["content_type"].partition("/")
            if (
                not maintype
                or not subtype
                or not maintype.isalnum()
                or not subtype.replace("-", "").replace("+", "").isalnum()
            ):
                maintype, subtype = "application", "octet-stream"
            message.add_attachment(
                payload,
                maintype=maintype,
                subtype=subtype,
                filename=item["filename"],
            )
        for payload, maintype, subtype, filename in extra_attachments:
            message.add_attachment(
                payload, maintype=maintype, subtype=subtype, filename=filename
            )
        return message

    def deliver(
        self, row: dict[str, Any], message: EmailMessage, recipients: list[str]
    ) -> dict[str, Any]:
        """Hand a message to SMTP, then keep a copy in Sent where needed."""
        envelope = [parseaddr(value)[1] for value in recipients]
        # Drafts fetched from IMAP can still have an 8bit transfer encoding.
        # The generator must apply the same safe policy to those messages too.
        message.policy = OUTBOUND_POLICY
        client = self._smtp(row)
        try:
            refused = client.send_message(
                message, from_addr=row["email_address"], to_addrs=envelope
            )
        except (smtplib.SMTPException, OSError) as error:
            raise _delivery_error(error) from error
        finally:
            try:
                client.quit()
            except (OSError, smtplib.SMTPException):
                pass
        saved = None
        if row.get("save_sent_copy", True) and not _auto_saves_sent(row["smtp_host"]):
            saved = self._append(row, "sent", message, b"(\\Seen)")
        return {
            "sent": not refused,
            "delivery_status": "partial" if refused else "accepted",
            "refused_recipients": list(refused),
            "saved_to_sent": saved,
        }

    def _append(
        self, row: dict[str, Any], alias: str, message: EmailMessage, flags: bytes
    ) -> bool:
        """Store a message in a special folder.

        The send already happened, so a failure here is reported, never raised.
        """
        try:
            client = self._imap(row)
        except ServiceError:
            return False
        try:
            folder = _special_folder(client, alias)
            if not folder:
                return False
            kind, _ = _append_imap_message(client, folder, message, flags.decode())
            return kind == "OK"
        except (OSError, imaplib.IMAP4.error):
            return False
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass

    def send(
        self,
        actor_id: str,
        args: dict[str, Any],
        *,
        extra_bcc: Sequence[str] = (),
        extra_attachments: Sequence[tuple[bytes, str, str, str]] = (),
    ) -> dict[str, Any]:
        row = self._selected_account(actor_id, args.get("account_id"))
        message = self.compose(row, args, extra_attachments=extra_attachments)
        outcome = self.deliver(
            row, message, [*args["to"], *args["cc"], *args["bcc"], *extra_bcc]
        )
        return {
            **outcome,
            "account_id": row["id"],
            "from": row["email_address"],
            "to": args["to"],
            "cc": args["cc"],
            "subject": args["subject"],
            "message_id": message.get("Message-ID"),
        }

    def run_idempotently(
        self,
        actor_id: str,
        operation_name: str,
        args: dict[str, Any],
        operation: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        """Run a keyed email operation once and replay its result for one day."""
        data = {key: value for key, value in args.items() if key != "idempotency_key"}
        idempotency_key = args.get("idempotency_key")
        if not idempotency_key:
            return operation(data)
        scope_key = hashlib.sha256(
            f"{actor_id}\x1f{self.workspace_id}\x1f{operation_name}\x1f"
            f"{idempotency_key}".encode()
        ).hexdigest()
        name = f"email-op-{scope_key}"
        request_hash = hashlib.sha256(
            json.dumps(
                data, sort_keys=True, separators=(",", ":"), default=str
            ).encode()
        ).hexdigest()

        with self.vault.credential_lock(f"{self.workspace_id}:{name}"):
            existing = next(
                (
                    item
                    for item in self._metadata(self.IDEMPOTENCY_PROVIDER)
                    if item.name == name
                ),
                None,
            )
            now = datetime.now(UTC)
            if existing is not None:
                record = self._secret(existing)
                if self._idempotency_expired(existing, record, now):
                    self.vault.delete_credential(existing.id, context=self.context)
                    existing = None
            if existing is not None:
                if record.get("request_hash") != request_hash:
                    raise ServiceError(
                        "email.idempotency_conflict",
                        "This idempotency key was already used with different arguments.",
                        409,
                    )
                if record.get("status") == "processing":
                    raise ServiceError(
                        "email.idempotency_in_progress",
                        "This request is still running or its outcome is unknown. "
                        "Check its outcome before using a new key.",
                        409,
                    )
                if record.get("status") == "failed":
                    details = dict(record.get("details") or {})
                    details["cached_error"] = True
                    raise ServiceError(
                        str(record.get("code", "email.send_failed")),
                        "This is a cached failure from an earlier attempt; no new "
                        f"send was attempted. {record.get('message', 'Email send failed.')}",
                        int(record.get("status_code", 502)),
                        details=details,
                    )
                response = record.get("response")
                if isinstance(response, dict):
                    return {**response, "duplicate": True}
                raise ServiceError(
                    "email.idempotency_in_progress",
                    "This request is still running or its outcome is unknown. "
                    "Check its outcome before using a new key.",
                    409,
                )

            metadata = self.vault.create_credential(
                name=name,
                provider=self.IDEMPOTENCY_PROVIDER,
                scopes=["email:send"],
                secret=json.dumps(
                    {"status": "processing", "request_hash": request_hash},
                    separators=(",", ":"),
                ),
                actor=self.actor,
                scope=CredentialScope.for_roles(EMAIL_CREDENTIAL_ROLE),
                workspace_id=self.workspace_id,
            )
        # Commit the claim before contacting SMTP. If the worker stops, the
        # persisted claim prevents a retry from delivering a duplicate.
        try:
            response = operation(data)
        except ServiceError as error:
            self._write_email_record(
                metadata,
                {
                    "status": "failed",
                    "request_hash": request_hash,
                    "code": error.code,
                    "message": error.message,
                    "status_code": error.status_code,
                    "details": error.details,
                },
            )
            raise
        self._write_email_record(
            metadata,
            {"status": "complete", "request_hash": request_hash, "response": response},
        )
        return response

    @staticmethod
    def _idempotency_expired(
        metadata: CredentialMetadata, record: dict[str, Any], now: datetime
    ) -> bool:
        retention = (
            EMAIL_FAILURE_RETENTION
            if record.get("status") == "processing"
            else EMAIL_RESULT_RETENTION
        )
        return metadata.updated_at < now - retention

    # ------------------------------------------------------------------
    # Mail client operations beyond search, read and send
    # ------------------------------------------------------------------

    def _with_client(self, row: dict[str, Any], work: Any) -> Any:
        client = self._imap(row)
        try:
            return work(client)
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass

    def _parsed(
        self, actor_id: str, message_id: str
    ) -> tuple[dict[str, Any], str, int, int, email.message.EmailMessage]:
        row, folder, validity, uid = self._message_location(actor_id, message_id)
        raw, _ = self._with_client(
            row, lambda client: self._fetch_raw(client, folder, validity, uid)
        )
        message = BytesParser(policy=email.policy.default).parsebytes(raw)
        return row, folder, validity, uid, message

    def list_folders(self, actor_id: str, account_id: str | None) -> dict[str, Any]:
        row = self._selected_account(actor_id, account_id)

        def read(client: imaplib.IMAP4) -> list[dict[str, Any]]:
            folders = []
            for name, attributes in _list_folders(client)[:60]:
                if "\\noselect" in attributes:
                    continue
                special = next(
                    (
                        alias
                        for alias in (
                            "sent",
                            "drafts",
                            "trash",
                            "junk",
                            "archive",
                            "all",
                        )
                        if f"\\{alias}" in attributes
                    ),
                    "inbox" if name.upper() == "INBOX" else None,
                )
                if not special:
                    # Servers without special-use flags name them by convention.
                    special = next(
                        (
                            alias
                            for alias, (_, names) in _SPECIAL_FOLDERS.items()
                            if name.lower() in names
                        ),
                        None,
                    )
                entry: dict[str, Any] = {"name": name, "special_use": special}
                try:
                    kind, data = client.status(_imap_quote(name), "(MESSAGES UNSEEN)")
                except (imaplib.IMAP4.error, ServiceError):
                    kind, data = "NO", []
                if kind == "OK" and data and isinstance(data[0], bytes):
                    counts = dict(
                        re.findall(rb"(MESSAGES|UNSEEN)\s+(\d+)", data[0], re.I)
                    )
                    entry["total"] = int(counts.get(b"MESSAGES", 0))
                    entry["unread"] = int(counts.get(b"UNSEEN", 0))
                folders.append(entry)
            return folders

        return {"account_id": row["id"], "folders": self._with_client(row, read)}

    def _own_addresses(self, actor_id: str) -> set[str]:
        return {str(row["email_address"]).lower() for row in self._all_rows()}

    @staticmethod
    def _quoted(message: email.message.Message, text: str) -> str:
        sender = _decode_header(message.get("From"))
        when = message.get("Date") or ""
        quoted = "\n".join(f"> {line}" for line in text[:10_000].splitlines())
        return f"On {when}, {sender} wrote:\n{quoted}"

    def reply(
        self, actor_id: str, message_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Answer a message in its thread, recipients and headers worked out here."""
        row, folder, validity, uid, original = self._parsed(actor_id, message_id)
        own = self._own_addresses(actor_id)
        reply_to = getaddresses(
            original.get_all("Reply-To", []) or original.get_all("From", [])
        )
        to = [
            formataddr(pair)
            for pair in reply_to
            if pair[1] and pair[1].lower() not in own
        ]
        cc: list[str] = []
        if args.get("reply_all"):
            seen = {parseaddr(value)[1].lower() for value in to}
            for pair in getaddresses(
                original.get_all("To", []) + original.get_all("Cc", [])
            ):
                address = pair[1].lower()
                if address and address not in own and address not in seen:
                    seen.add(address)
                    cc.append(formataddr(pair))
        if not to and not cc:
            # Replying to your own message: send it to whoever it went to.
            to = [formataddr(pair) for pair in getaddresses(original.get_all("To", []))]
        subject = _decode_header(original.get("Subject"))
        if not re.match(r"(?i)re:", subject):
            subject = f"Re: {subject}"
        references = [
            *" ".join(original.get_all("References", [])).split(),
            *([original["Message-ID"]] if original.get("Message-ID") else []),
        ][-20:]
        body = args["body_text"].rstrip()
        if args.get("quote_original", True):
            body = f"{body}\n\n{self._quoted(original, _email_text(original) or html_to_text(_email_text(original, html_body=True)))}"
        outgoing = {
            "account_id": row["id"],
            "to": to,
            "cc": cc,
            "bcc": [],
            "reply_to": None,
            "subject": subject[:998],
            "body_text": body,
            "body_html": None,
            "in_reply_to": original.get("Message-ID"),
            "references": references,
            "attachments": args.get("attachments") or [],
            "include_signature": args.get("include_signature", True),
        }
        if args.get("save_as_draft"):
            return self.save_draft(actor_id, outgoing)
        result = self.send(actor_id, outgoing)

        def answered(client: imaplib.IMAP4) -> bool:
            kind, _ = client.select(_imap_quote(folder), readonly=False)
            if kind != "OK":
                return False
            kind, _ = client.uid("store", str(uid), "+FLAGS.SILENT", "(\\Answered)")
            return kind == "OK"

        try:
            result["original_marked_answered"] = self._with_client(row, answered)
        except ServiceError:
            # The reply went out; only the bookkeeping on the original failed.
            result["original_marked_answered"] = False
        return result

    def forward(
        self, actor_id: str, message_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Send a message and its attachments without passing them through the assistant.

        The message contents stay on the mail server until the final send.
        """
        row, _, _, _, original = self._parsed(actor_id, message_id)
        subject = _decode_header(original.get("Subject"))
        if not re.match(r"(?i)fwd?:", subject):
            subject = f"Fwd: {subject}"
        header = "\n".join(
            f"{label}: {_decode_header(original.get(label))}"
            for label in ("From", "Date", "Subject", "To", "Cc")
            if original.get(label)
        )
        original_text = _email_text(original) or html_to_text(
            _email_text(original, html_body=True)
        )
        body = (
            f"{(args.get('body_text') or '').rstrip()}\n\n"
            f"---------- Forwarded message ----------\n{header}\n\n{original_text[:50_000]}"
        ).lstrip()
        extra: list[tuple[bytes, str, str, str]] = []
        if args.get("include_attachments", True):
            for part in original.walk():
                filename = part.get_filename()
                if _is_attachment(part):
                    maintype, _, subtype = part.get_content_type().partition("/")
                    extra.append(
                        (
                            _part_payload(part),
                            maintype or "application",
                            subtype or "octet-stream",
                            _decode_header(filename) or "attachment",
                        )
                    )
        outgoing = {
            "account_id": row["id"],
            "to": args["to"],
            "cc": args.get("cc") or [],
            "bcc": args.get("bcc") or [],
            "reply_to": None,
            "subject": subject[:998],
            "body_text": body,
            "body_html": None,
            "in_reply_to": None,
            "references": [],
            "attachments": [],
            "include_signature": args.get("include_signature", True),
        }
        if args.get("save_as_draft"):
            return self.save_draft(actor_id, outgoing, extra_attachments=extra)
        return self.send(actor_id, outgoing, extra_attachments=extra)

    def save_draft(
        self,
        actor_id: str,
        args: dict[str, Any],
        *,
        extra_attachments: Sequence[tuple[bytes, str, str, str]] = (),
    ) -> dict[str, Any]:
        """Keep a message in Drafts, where the user can edit and send it."""
        row = self._selected_account(actor_id, args.get("account_id"))
        message = self.compose(row, args, extra_attachments=extra_attachments)
        if args.get("bcc"):
            # A draft keeps its Bcc so sending it later reaches everyone.
            message["Bcc"] = ", ".join(args["bcc"])

        def store(client: imaplib.IMAP4) -> dict[str, Any]:
            folder = _special_folder(client, "drafts")
            if not folder:
                raise ServiceError(
                    "email.drafts_unsupported",
                    "This mailbox has no Drafts folder.",
                    409,
                )
            kind, data = _append_imap_message(
                client, folder, message, "(\\Draft \\Seen)"
            )
            if kind != "OK":
                raise ServiceError(
                    "email.draft_failed", "The draft could not be saved.", 502
                )
            text = b" ".join(item for item in data or [] if isinstance(item, bytes))
            appended = re.search(rb"APPENDUID\s+(\d+)\s+(\d+)", text, re.I)
            draft_id = (
                _b64(
                    json.dumps(
                        [
                            row["id"],
                            _decode_imap_utf7(folder),
                            int(appended.group(1)),
                            int(appended.group(2)),
                        ],
                        separators=(",", ":"),
                    ).encode()
                )
                if appended
                else None
            )
            return {
                "saved": True,
                "draft_id": draft_id,
                "folder": _decode_imap_utf7(folder),
            }

        result = self._with_client(row, store)
        return {**result, "account_id": row["id"], "subject": args["subject"]}

    def send_draft(self, actor_id: str, message_id: str) -> dict[str, Any]:
        row, folder, validity, uid = self._message_location(actor_id, message_id)
        raw, flags = self._with_client(
            row, lambda client: self._fetch_raw(client, folder, validity, uid)
        )
        if b"\\draft" not in flags.lower().split():
            raise ServiceError("email.not_a_draft", "This message is not a draft.", 409)
        draft = BytesParser(policy=email.policy.default).parsebytes(raw)
        recipients = [
            formataddr(pair)
            for pair in getaddresses(
                draft.get_all("To", [])
                + draft.get_all("Cc", [])
                + draft.get_all("Bcc", [])
            )
            if pair[1]
        ]
        if not recipients:
            raise ServiceError(
                "email.no_recipients", "This draft has no recipients yet.", 409
            )
        del draft["Bcc"]
        del draft["Date"]
        del draft["Message-ID"]
        draft["Date"] = formatdate(localtime=True)
        draft["Message-ID"] = make_msgid(domain=row["email_address"].rpartition("@")[2])
        result = self.deliver(row, draft, recipients)

        def discard(client: imaplib.IMAP4) -> bool:
            kind, _ = client.select(_imap_quote(folder), readonly=False)
            if kind != "OK" or "UIDPLUS" not in client.capabilities:
                return False
            kind, _ = client.uid("store", str(uid), "+FLAGS.SILENT", "(\\Deleted)")
            if kind == "OK":
                kind, _ = client.uid("expunge", str(uid))
            return kind == "OK"

        try:
            result["draft_removed"] = self._with_client(row, discard)
        except ServiceError:
            result["draft_removed"] = False
        return {
            **result,
            "account_id": row["id"],
            "subject": _decode_header(draft.get("Subject")),
        }

    @staticmethod
    def _thread_uids(client: imaplib.IMAP4, header_ids: set[str]) -> set[int]:
        """UIDs of the messages naming any of these Message-IDs, in one SEARCH."""
        terms = [
            ("HEADER", field, _imap_raw_quote(header_id.strip()))
            for header_id in sorted(header_ids)[:10]
            for field in ("MESSAGE-ID", "REFERENCES", "IN-REPLY-TO")
        ]
        if not terms:
            return set()
        # IMAP's OR takes two keys, so n terms nest as: OR t1 OR t2 ... tn.
        criteria = [
            token
            for index, term in enumerate(terms)
            for token in (("OR", *term) if index < len(terms) - 1 else term)
        ]
        kind, data = client.uid("search", *criteria)
        if kind != "OK" or not data or not data[0]:
            return set()
        return {int(item) for item in data[0].split()}

    def thread(self, actor_id: str, message_id: str, limit: int = 30) -> dict[str, Any]:
        """Find the conversation across its folder and Sent, oldest first.

        Include at most ``limit`` messages.
        """
        row, folder, _, uid, original = self._parsed(actor_id, message_id)
        ids = {
            value
            for value in [
                original.get("Message-ID"),
                original.get("In-Reply-To"),
                *" ".join(original.get_all("References", [])).split(),
            ]
            if value and re.fullmatch(r"<[^<>\s]+>", value.strip())
        }

        def gather(client: imaplib.IMAP4) -> list[dict[str, Any]]:
            folders = [folder]
            # Every \Sent folder: Apple Mail and webmail can each keep their own.
            for sent in _special_folders(client, "sent"):
                if _decode_imap_utf7(sent) not in folders:
                    folders.append(_decode_imap_utf7(sent))
            found: dict[str, dict[str, Any]] = {}
            for name in folders:
                kind, _ = client.select(_imap_quote(name), readonly=True)
                if kind != "OK":
                    continue
                validity = _uidvalidity(client)
                if validity is None:
                    continue
                uids = self._thread_uids(client, ids)
                if not uids:
                    continue
                kind, fetched = client.uid(
                    "fetch",
                    ",".join(map(str, sorted(uids)[-limit:])),
                    "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID)])",
                )
                if kind != "OK":
                    continue
                for part in fetched or []:
                    if not isinstance(part, tuple):
                        continue
                    meta, raw_headers = part
                    uid_match = re.search(rb"\bUID\s+(\d+)", meta, re.I)
                    if not uid_match:
                        continue
                    found_uid = int(uid_match.group(1))
                    parsed = BytesParser(policy=email.policy.default).parsebytes(
                        raw_headers
                    )
                    flags = re.search(rb"FLAGS\s+\(([^)]*)\)", meta, re.I)
                    key = parsed.get("Message-ID") or f"{name}:{found_uid}"
                    found.setdefault(
                        key,
                        {
                            **_summary(
                                parsed,
                                _b64(
                                    json.dumps(
                                        [row["id"], name, validity, found_uid],
                                        separators=(",", ":"),
                                    ).encode()
                                ),
                                flags.group(1) if flags else b"",
                            ),
                            "folder": name,
                        },
                    )
            return list(found.values())

        items = self._with_client(row, gather)

        def when(item: dict[str, Any]) -> float:
            try:
                return email.utils.parsedate_to_datetime(item["date"]).timestamp()
            except (TypeError, ValueError, IndexError):
                return 0.0

        items.sort(key=when)
        return {"account_id": row["id"], "messages": items[-limit:]}

    def attachment_text(
        self, actor_id: str, attachment_id: str, max_chars: int
    ) -> dict[str, Any]:
        """The words in an attachment: plain text, HTML or PDF."""
        message_id, index = _parse_attachment_id(attachment_id)
        _, _, _, _, message = self._parsed(actor_id, message_id)
        parts = list(message.walk())
        if index >= len(parts):
            raise ServiceError(
                "email.attachment_not_found", "Attachment not found.", 404
            )
        part = parts[index]
        payload = _part_payload(part)
        content_type = part.get_content_type()
        if content_type == "application/pdf":
            text = _pdf_text(payload)
        elif content_type == "text/html":
            text = html_to_text(_part_text(part))
        elif content_type.startswith("text/") or content_type in {
            "application/json",
            "application/xml",
        }:
            text = _part_text(part)
        else:
            raise ServiceError(
                "email.attachment_not_text",
                "Only text, HTML and PDF attachments can be read as text; download others.",
                415,
            )
        return {
            "filename": _decode_header(part.get_filename()) or "attachment",
            "content_type": content_type,
            "text": text[:max_chars],
            "truncated": len(text) > max_chars,
        }

    def respond_to_invite(
        self, actor_id: str, message_id: str, response: str, comment: str
    ) -> dict[str, Any]:
        """Answer an invitation with an iTIP REPLY the organiser's calendar can apply.

        The reply is sent to the invitation organiser.
        """
        row, _, _, _, message = self._parsed(actor_id, message_id)
        invites = [
            invite
            for invite in _calendar_parts(message)
            if invite.get("method") in {"REQUEST", None} and invite.get("uid")
        ]
        if not invites:
            raise ServiceError(
                "email.no_invitation",
                "This email has no calendar invitation to answer.",
                409,
            )
        invite = invites[0]
        organizer = invite.get("organizer")
        if not organizer or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+", organizer):
            raise ServiceError(
                "email.no_organizer",
                "This invitation names no organiser to reply to.",
                409,
            )
        status = {
            "accepted": "ACCEPTED",
            "declined": "DECLINED",
            "tentative": "TENTATIVE",
        }[response]
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        lines = [
            "BEGIN:VCALENDAR",
            "PRODID:-//Orcheo//MCP mail//EN",
            "VERSION:2.0",
            "METHOD:REPLY",
            "BEGIN:VEVENT",
            f"UID:{_ics_text(invite['uid'])}",
            f"DTSTAMP:{stamp}",
            f"ORGANIZER:mailto:{organizer}",
            f"ATTENDEE;PARTSTAT={status};CN={_ics_param(row['display_name'])}"
            f":mailto:{row['email_address']}",
        ]
        # The invitation is untrusted: only a plain number may be echoed back.
        if re.fullmatch(r"\d{1,9}", str(invite.get("sequence") or "")):
            lines.append(f"SEQUENCE:{invite['sequence']}")
        summary = " ".join(str(invite.get("summary") or "").split())
        if summary:
            lines.append(f"SUMMARY:{_ics_text(summary)}")
        if comment:
            lines.append(f"COMMENT:{_ics_text(comment)}")
        lines += ["END:VEVENT", "END:VCALENDAR"]
        label = {
            "accepted": "Accepted",
            "declined": "Declined",
            "tentative": "Tentative",
        }[response]
        outgoing = self.compose(
            row,
            {
                "to": [organizer],
                "cc": [],
                "subject": f"{label}: {summary or 'Invitation'}"[:998],
                "body_text": comment
                or f"{row['display_name']} has {response} this invitation.",
                "include_signature": False,
            },
        )
        outgoing.add_alternative(
            "\r\n".join(lines) + "\r\n", subtype="calendar", params={"method": "REPLY"}
        )
        result = self.deliver(row, outgoing, [organizer])
        return {
            **result,
            "response": response,
            "uid": invite["uid"],
            "organizer": organizer,
        }

    def event_links(self, actor_id: str, message_id: str) -> dict[str, Any]:
        """Extract event links and calendar invitations from a message."""
        _, _, _, _, message = self._parsed(actor_id, message_id)
        text = " ".join([_email_text(message), _email_text(message, html_body=True)])
        urls = list(dict.fromkeys(re.findall(r"https://[^\s\"'<>)]+", text)))
        known = ("eventbrite.", "meetup.com", "lu.ma", "luma.com", "partiful.com")
        ranked = sorted(urls, key=lambda url: not any(host in url for host in known))
        return {"urls": ranked[:10], "calendar_invites": _calendar_parts(message)}

    def find_contacts(
        self, actor_id: str, account_id: str | None, query: str, limit: int
    ) -> dict[str, Any]:
        """People you have mailed with whose name or address matches."""
        row = self._selected_account(actor_id, account_id)
        needle = query.replace("\r", " ").replace("\n", " ")
        if not needle.isascii():
            raise ServiceError(
                "email.search_charset", "Contact search supports ASCII text only.", 400
            )

        def scan(client: imaplib.IMAP4) -> dict[str, dict[str, Any]]:
            seen: dict[str, dict[str, Any]] = {}
            folders = ["INBOX"]
            folders += [
                _decode_imap_utf7(sent) for sent in _special_folders(client, "sent")
            ]
            for name in folders:
                kind, _ = client.select(_imap_quote(name), readonly=True)
                if kind != "OK":
                    continue
                # Match locally over the latest headers: server-side address
                # search differs between servers and misses display names.
                kind, data = client.uid("search", "ALL")
                if kind != "OK" or not data or not data[0]:
                    continue
                recent = sorted(int(item) for item in data[0].split())[-200:]
                kind, fetched = client.uid(
                    "fetch",
                    ",".join(str(found_uid) for found_uid in recent),
                    "(BODY.PEEK[HEADER.FIELDS (FROM TO CC)])",
                )
                if kind != "OK":
                    continue
                for part in fetched or []:
                    if not isinstance(part, tuple) or not isinstance(part[1], bytes):
                        continue
                    headers = BytesParser(policy=email.policy.default).parsebytes(
                        part[1]
                    )
                    for display, address in getaddresses(
                        headers.get_all("From", [])
                        + headers.get_all("To", [])
                        + headers.get_all("Cc", [])
                    ):
                        normalized_address = address.lower()
                        if (
                            not normalized_address
                            or needle.lower()
                            not in f"{display} {normalized_address}".lower()
                        ):
                            continue
                        entry = seen.setdefault(
                            normalized_address,
                            {
                                "email": normalized_address,
                                "name": display,
                                "messages": 0,
                            },
                        )
                        entry["messages"] += 1
                        entry["name"] = entry["name"] or display
            return seen

        contacts = sorted(
            self._with_client(row, scan).values(), key=lambda entry: -entry["messages"]
        )
        return {"contacts": contacts[:limit]}

    # Scheduled sending ------------------------------------------------

    def schedule(
        self,
        actor_id: str,
        args: dict[str, Any],
        send_at: str,
        *,
        extra_bcc: Sequence[str] = (),
        extra_attachments: Sequence[tuple[bytes, str, str, str]] = (),
    ) -> dict[str, Any]:
        row = self._selected_account(actor_id, args.get("account_id"))
        payload = {
            "actor_id": actor_id,
            "account_id": row["id"],
            "args": {**args, "account_id": row["id"], "send_at": None},
            "extra_bcc": list(extra_bcc),
            "extra_attachments": [
                [base64.b64encode(content).decode(), maintype, subtype, filename]
                for content, maintype, subtype, filename in extra_attachments
            ],
            "subject": args["subject"][:200],
            "send_at": send_at,
            "status": "scheduled",
            "last_error": None,
        }
        scheduled_id = f"scheduled_email_{uuid.uuid4().hex}"
        metadata = self.vault.create_credential(
            name=scheduled_id,
            provider=self.SCHEDULE_PROVIDER,
            scopes=["email:schedule"],
            secret=json.dumps(payload, separators=(",", ":")),
            actor=self.actor,
            scope=CredentialScope.for_roles(EMAIL_CREDENTIAL_ROLE),
            workspace_id=self.workspace_id,
        )
        return {
            "scheduled_id": str(metadata.id),
            "status": "scheduled",
            "send_at": send_at,
            "account_id": row["id"],
        }

    def list_scheduled(self, actor_id: str) -> list[dict[str, Any]]:
        del actor_id
        results = []
        for metadata in self._metadata(self.SCHEDULE_PROVIDER):
            schedule = self._secret(metadata)
            if schedule.get("status") in {"scheduled", "sending", "failed", "partial"}:
                result = {
                    "scheduled_id": str(metadata.id),
                    "account_id": schedule.get("account_id")
                    or (schedule.get("args") or {}).get("account_id"),
                    "subject": schedule.get("subject", ""),
                    "send_at": schedule.get("send_at"),
                    "status": schedule.get("status"),
                    "last_error": schedule.get("last_error"),
                }
                if schedule.get("status") == "partial":
                    result.update(schedule["delivery"])
                results.append(result)
        return sorted(results, key=lambda item: item.get("send_at") or "")

    def cleanup_idempotency(self) -> None:
        """Remove expired retry records, including abandoned claims."""
        now = datetime.now(UTC)
        for metadata in self._metadata(self.IDEMPOTENCY_PROVIDER):
            if metadata.updated_at >= now - EMAIL_RESULT_RETENTION:
                continue
            with self.vault.credential_lock(f"{self.workspace_id}:{metadata.name}"):
                current = self._email_record(metadata.id, self.IDEMPOTENCY_PROVIDER)
                if current and self._idempotency_expired(
                    current, self._secret(current), now
                ):
                    self.vault.delete_credential(current.id, context=self.context)

    def _email_record(
        self, credential_id: uuid.UUID, provider: str
    ) -> CredentialMetadata | None:
        try:
            metadata = self.vault.get_credential_metadata(
                credential_id, context=self.context
            )
        except (CredentialNotFoundError, WorkflowScopeError):
            return None
        if (
            metadata.provider == provider
            and metadata.workspace_id == self.workspace_id
            and metadata.template_id is None
        ):
            return metadata
        return None

    def _write_email_record(
        self, metadata: CredentialMetadata, record: dict[str, Any]
    ) -> None:
        self.vault.update_credential(
            credential_id=metadata.id,
            actor=self.actor,
            secret=json.dumps(record, separators=(",", ":"), default=str),
            context=self.context,
        )

    def cancel_scheduled(self, actor_id: str, scheduled_id: str) -> dict[str, Any]:
        """Stop a waiting message, or clear a failed or partly delivered one."""
        del actor_id
        try:
            credential_id = uuid.UUID(scheduled_id)
        except ValueError:
            credential_id = None
        if credential_id is not None:
            with self.vault.credential_lock(f"email-schedule:{credential_id}"):
                metadata = self._email_record(credential_id, self.SCHEDULE_PROVIDER)
                status = self._secret(metadata).get("status") if metadata else None
                if metadata and status in {"scheduled", "failed", "partial"}:
                    self.vault.delete_credential(metadata.id, context=self.context)
                    return {
                        "scheduled_id": scheduled_id,
                        "status": "cancelled" if status == "scheduled" else "dismissed",
                    }
        raise ServiceError(
            "email.scheduled_not_found",
            "No scheduled email with that ID can be cancelled or dismissed.",
            404,
        )

    def _claim_schedule(self, credential_id: uuid.UUID) -> dict[str, Any] | None:
        with self.vault.credential_lock(f"email-schedule:{credential_id}"):
            metadata = self._email_record(credential_id, self.SCHEDULE_PROVIDER)
            if metadata is None:
                return None
            schedule = self._secret(metadata)
            if schedule.get("status") != "scheduled":
                return None
            schedule["status"] = "sending"
            self._write_email_record(metadata, schedule)
            return schedule

    def _recover_schedule(self, credential_id: uuid.UUID, now: datetime) -> bool:
        with self.vault.credential_lock(f"email-schedule:{credential_id}"):
            metadata = self._email_record(credential_id, self.SCHEDULE_PROVIDER)
            if metadata is None:
                return False
            schedule = self._secret(metadata)
            status = schedule.get("status")
            age = now - metadata.updated_at
            if (
                status in {"cancelled", "sent"}
                or status in {"partial", "failed"}
                and age >= EMAIL_FAILURE_RETENTION
            ):
                self.vault.delete_credential(metadata.id, context=self.context)
            elif status == "sending" and age >= SCHEDULE_INTERRUPTED_AFTER:
                schedule.update(
                    status="failed",
                    last_error="Interrupted while sending; check Sent before retrying.",
                )
                self._write_email_record(metadata, schedule)
                return True
            return False

    def _finish_schedule(
        self,
        credential_id: uuid.UUID,
        outcome: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        with self.vault.credential_lock(f"email-schedule:{credential_id}"):
            metadata = self._email_record(credential_id, self.SCHEDULE_PROVIDER)
            if metadata is None:
                return
            if outcome is not None and outcome["sent"]:
                self.vault.delete_credential(metadata.id, context=self.context)
                return
            schedule = self._secret(metadata)
            schedule["account_id"] = (schedule.get("args") or {}).get("account_id")
            if outcome is not None:
                schedule.update(
                    status="partial",
                    last_error="SMTP refused some recipients. Check the delivery details.",
                    delivery={
                        field: outcome[field]
                        for field in (
                            "delivery_status",
                            "refused_recipients",
                            "message_id",
                            "saved_to_sent",
                        )
                    },
                    args=None,
                    extra_bcc=[],
                    extra_attachments=[],
                )
            else:
                schedule.update(status="failed", last_error=error)
            self._write_email_record(metadata, schedule)

    def process_scheduled(self, limit: int = 20) -> dict[str, int]:
        """Send due workspace emails from their encrypted vault records.

        ``recovered`` counts interrupted sends this pass marked as failed; those
        are housekeeping, not deliveries attempted now.
        """
        counts = {"sent": 0, "failed": 0, "recovered": 0}
        now = datetime.now(UTC)
        due: list[tuple[datetime, uuid.UUID]] = []
        for metadata in self._metadata(self.SCHEDULE_PROVIDER):
            schedule = self._secret(metadata)
            status = schedule.get("status")
            if status != "scheduled":
                age = now - metadata.updated_at
                if (
                    status in {"cancelled", "sent"}
                    or status == "sending"
                    and age >= SCHEDULE_INTERRUPTED_AFTER
                    or status in {"partial", "failed"}
                    and age >= EMAIL_FAILURE_RETENTION
                ) and self._recover_schedule(metadata.id, now):
                    counts["recovered"] += 1
                continue
            try:
                send_at = datetime.fromisoformat(schedule["send_at"])
                if send_at.utcoffset() is None:
                    raise ValueError("A timezone is required")
            except (KeyError, TypeError, ValueError):
                if self._claim_schedule(metadata.id) is not None:
                    self._finish_schedule(
                        metadata.id, None, "Scheduled time is invalid."
                    )
                    counts["failed"] += 1
                continue
            if send_at <= now:
                due.append((send_at, metadata.id))
        for _, credential_id in sorted(due)[:limit]:
            claimed = self._claim_schedule(credential_id)
            if claimed is None:
                continue
            try:
                outcome = self.send(
                    claimed["actor_id"],
                    claimed["args"],
                    extra_bcc=claimed.get("extra_bcc") or [],
                    extra_attachments=[
                        (base64.b64decode(content), maintype, subtype, filename)
                        for content, maintype, subtype, filename in claimed.get(
                            "extra_attachments"
                        )
                        or []
                    ],
                )
            except Exception as error:
                message = (
                    error.message
                    if isinstance(error, ServiceError)
                    else f"Scheduled email failed ({type(error).__name__})."
                )
                self._finish_schedule(credential_id, None, message)
                counts["failed"] += 1
            else:
                self._finish_schedule(credential_id, outcome, None)
                counts["sent" if outcome["sent"] else "failed"] += 1
        return counts


def _pdf_text(payload: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(payload))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages[:50])
    except (PdfReadError, ValueError, KeyError) as error:
        raise ServiceError(
            "email.attachment_unreadable", "This PDF could not be read.", 422
        ) from error
