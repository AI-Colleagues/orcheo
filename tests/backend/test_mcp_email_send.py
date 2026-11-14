"""Composing, sending, replying, forwarding and drafting mail."""

from __future__ import annotations
import imaplib
import json
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any, NamedTuple
import pytest
from orcheo_backend.app.mcp_server import email_client
from orcheo_backend.app.mcp_server.email_client import EmailAccounts, SendEmail
from orcheo_backend.app.mcp_server.email_errors import ServiceError
from orcheo_backend.app.mcp_server.email_mail import ForwardEmail, ReplyToEmail
from tests.backend.mcp_email_support import (
    FakeIMAP,
    FakeSMTP,
    add_account,
    b64,
    in_sequence,
    install_servers,
    message_id,
    new_accounts,
    raw_message,
)


class Box(NamedTuple):
    accounts: EmailAccounts
    imap: FakeIMAP
    smtp: FakeSMTP
    account_id: str

    def id(self, uid: int, folder: str = "INBOX") -> str:
        return message_id(self.account_id, folder, uid)


def _build(monkeypatch: pytest.MonkeyPatch, **account: Any) -> Box:
    accounts = new_accounts()
    imap = FakeIMAP(
        {"INBOX": [], "Sent": [], "Drafts": [], "Trash": []},
        special={"Sent": "\\Sent", "Drafts": "\\Drafts", "Trash": "\\Trash"},
    )
    smtp = FakeSMTP()
    install_servers(monkeypatch, imap, smtp)
    return Box(accounts, imap, smtp, add_account(accounts, **account))


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch) -> Box:
    return _build(monkeypatch)


def _code(call: Any, *args: Any, **kwargs: Any) -> str:
    with pytest.raises(ServiceError) as raised:
        call(*args, **kwargs)
    return raised.value.code


def _args(**fields: Any) -> dict[str, Any]:
    fields.setdefault("subject", "Status")
    fields.setdefault("to", ["you@example.org"])
    fields.setdefault("body_text", "All good.")
    return SendEmail(**fields).model_dump(mode="python")


def _sent(box: Box) -> EmailMessage:
    [(message, _, _)] = box.smtp.sent
    return message


# -- composing -------------------------------------------------------------------


def test_compose_builds_headers_body_signature_and_attachments(box: Box) -> None:
    row = box.accounts._selected_account("test", None)  # noqa: SLF001
    row["signature_text"] = "Ada\nOps <team>"
    message = box.accounts.compose(
        row,
        _args(
            to=["Ann <ann@example.org>"],
            cc=["cc@example.org"],
            reply_to="Help <help@example.org>",
            body_html="<p>Hi</p>",
            in_reply_to="<root@example.org>",
            references=["<a@example.org>", "<root@example.org>"],
            attachments=[
                {
                    "filename": "ok.json",
                    "content_type": "application/json",
                    "content_base64": b64(b"{}"),
                },
                {
                    "filename": "odd.bin",
                    "content_type": "not a/type",
                    "content_base64": b64(b"\x00"),
                },
            ],
        ),
        extra_attachments=[(b"extra", "text", "plain", "extra.txt")],
    )
    assert message["From"] == "Me <me@example.org>"
    assert message["To"] == "Ann <ann@example.org>"
    assert message["Cc"] == "cc@example.org"
    assert message["Reply-To"] == "Help <help@example.org>"
    assert message["In-Reply-To"] == "<root@example.org>"
    assert message["References"] == "<a@example.org> <root@example.org>"
    assert message["Message-ID"].endswith("@example.org>")
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert body == "All good.\n\n-- \nAda\nOps <team>\n"
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "-- <br>Ada<br>Ops &lt;team&gt;" in html
    attachments = {
        part.get_filename(): part.get_content_type()
        for part in message.iter_attachments()
    }
    assert attachments == {
        "ok.json": "application/json",
        "odd.bin": "application/octet-stream",
        "extra.txt": "text/plain",
    }


def test_compose_html_only_and_bcc_only_messages(box: Box) -> None:
    row = box.accounts._selected_account("test", None)  # noqa: SLF001
    html_only = box.accounts.compose(
        row,
        _args(
            body_text=None, body_html="<p>Only html</p>", to=[], bcc=["b@example.org"]
        ),
    )
    assert html_only.get_content_type() == "text/html"
    assert html_only["To"] == "undisclosed-recipients:;"
    assert html_only["Bcc"] is None
    assert html_only["Cc"] is None
    unsigned = box.accounts.compose(
        {**row, "signature_text": "Sig"}, _args(include_signature=False)
    )
    assert "Sig" not in unsigned.get_content()


# -- delivery --------------------------------------------------------------------


def test_send_delivers_to_every_recipient_and_files_a_sent_copy(box: Box) -> None:
    result = box.accounts.send(
        "test",
        _args(cc=["cc@example.org"], bcc=["Bcc <bcc@example.org>"]),
        extra_bcc=["extra@example.org"],
    )
    [(message, from_addr, envelope)] = box.smtp.sent
    assert from_addr == "me@example.org"
    assert envelope == [
        "you@example.org",
        "cc@example.org",
        "bcc@example.org",
        "extra@example.org",
    ]
    assert message["Bcc"] is None
    assert result["sent"] is True
    assert result["delivery_status"] == "accepted"
    assert result["saved_to_sent"] is True
    assert result["message_id"] == message["Message-ID"]
    assert result["account_id"] == box.account_id
    assert result["subject"] == "Status"
    assert box.smtp.calls[-1] == "quit"
    [copy] = box.imap.folders["Sent"]
    assert "\\Seen" in copy.flags
    assert copy.parsed()["Subject"] == "Status"


def test_send_skips_the_sent_copy_when_the_provider_files_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    box = _build(monkeypatch)
    row = box.accounts._row("test", box.account_id)  # noqa: SLF001
    row["smtp_host"] = "smtp.gmail.com"
    message = box.accounts.compose(row, _args())
    assert (
        box.accounts.deliver(row, message, ["you@example.org"])["saved_to_sent"] is None
    )
    assert box.imap.folders["Sent"] == []

    row["smtp_host"] = "smtp.example.org"
    row["save_sent_copy"] = False
    assert (
        box.accounts.deliver(row, message, ["you@example.org"])["saved_to_sent"] is None
    )
    assert box.imap.folders["Sent"] == []
    assert email_client._auto_saves_sent("SMTP.Office365.com.") is True  # noqa: SLF001
    assert email_client._auto_saves_sent("notgmail.com") is False  # noqa: SLF001


def test_partial_acceptance_is_not_reported_as_sent(box: Box) -> None:
    box.smtp.refused = {"bad@example.org": (550, b"5.1.1 no such user")}
    result = box.accounts.send("test", _args(to=["you@example.org", "bad@example.org"]))
    assert result["sent"] is False
    assert result["delivery_status"] == "partial"
    assert result["refused_recipients"] == ["bad@example.org"]


def test_a_failed_sent_copy_never_fails_the_send(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    box.imap.fail.add("append")
    assert box.accounts.send("test", _args())["saved_to_sent"] is False
    box.imap.fail.clear()

    # Each way the copy can fail leaves the delivered message alone.
    broken = FakeIMAP({"INBOX": []})
    in_sequence(monkeypatch, broken)
    assert (
        box.accounts.send("test", _args())["saved_to_sent"] is False
    )  # no Sent folder
    odd = FakeIMAP({"Sent": []}, special={"Sent": "\\Sent"})
    odd.append = lambda *args: (_ for _ in ()).throw(OSError("reset"))  # type: ignore[method-assign]
    odd.logout_error = OSError("closed")
    in_sequence(monkeypatch, odd)
    assert box.accounts.send("test", _args())["saved_to_sent"] is False
    offline = FakeIMAP()
    offline.login_error = imaplib.IMAP4.error("denied")
    in_sequence(monkeypatch, offline)
    assert box.accounts.send("test", _args())["saved_to_sent"] is False


@pytest.mark.parametrize(
    ("error", "code", "stage"),
    [
        (smtplib.SMTPDataError(554, b"5.7.1 rejected"), "rejected", "content"),
        (smtplib.SMTPServerDisconnected("gone"), "unknown", "connection"),
    ],
)
def test_smtp_failures_become_service_errors(
    box: Box, error: Exception, code: str, stage: str
) -> None:
    box.smtp.send_error = error
    box.smtp.quit_error = OSError("closed")
    with pytest.raises(ServiceError) as raised:
        box.accounts.send("test", _args())
    assert raised.value.code == "email.send_failed"
    assert raised.value.details["delivery_status"] == code
    assert raised.value.details["smtp_stage"] == stage
    assert box.imap.folders["Sent"] == []


# -- idempotency -----------------------------------------------------------------


def test_idempotent_sends_replay_instead_of_resending(box: Box) -> None:
    calls: list[dict[str, Any]] = []

    def operation(data: dict[str, Any]) -> dict[str, Any]:
        calls.append(data)
        return {"sent": True, "n": len(calls)}

    run = box.accounts.run_idempotently
    data = _args(idempotency_key="key-1")
    assert run("test", "send_email", data, operation) == {"sent": True, "n": 1}
    assert "idempotency_key" not in calls[0]
    assert run("test", "send_email", data, operation) == {
        "sent": True,
        "n": 1,
        "duplicate": True,
    }
    assert len(calls) == 1
    other = _args(idempotency_key="key-1", subject="Different")
    assert _code(run, "test", "send_email", other, operation) == (
        "email.idempotency_conflict"
    )
    # Without a key every call runs.
    plain = _args()
    run("test", "send_email", plain, operation)
    run("test", "send_email", plain, operation)
    assert len(calls) == 3


def test_failures_are_cached_and_interrupted_claims_block_reuse(box: Box) -> None:
    run = box.accounts.run_idempotently

    def rejected(data: dict[str, Any]) -> dict[str, Any]:
        raise ServiceError("email.send_failed", "Rejected.", 502, {"smtp_code": 554})

    data = _args(idempotency_key="key-2")
    assert _code(run, "test", "send_email", data, rejected) == "email.send_failed"
    with pytest.raises(ServiceError) as cached:
        run("test", "send_email", data, rejected)
    assert cached.value.status_code == 502
    assert cached.value.details == {"smtp_code": 554, "cached_error": True}
    assert "cached failure" in cached.value.message
    assert cached.value.message.endswith("Rejected.")

    def crash(data: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("worker died")

    crashed = _args(idempotency_key="key-3")
    with pytest.raises(RuntimeError):
        run("test", "send_email", crashed, crash)
    assert _code(run, "test", "send_email", crashed, crash) == (
        "email.idempotency_in_progress"
    )


def test_a_completed_record_without_a_response_is_treated_as_in_flight(
    box: Box,
) -> None:
    run = box.accounts.run_idempotently
    data = _args(idempotency_key="key-4")
    run("test", "send_email", data, lambda d: {"sent": True})
    [record] = box.accounts._metadata(box.accounts.IDEMPOTENCY_PROVIDER)  # noqa: SLF001
    secret = json.loads(record.reveal(cipher=box.accounts.vault.cipher))
    secret["response"] = None
    box.accounts._write_email_record(record, secret)  # noqa: SLF001
    assert _code(run, "test", "send_email", data, lambda d: {}) == (
        "email.idempotency_in_progress"
    )


def test_idempotency_records_expire(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    run = box.accounts.run_idempotently
    data = _args(idempotency_key="key-5")
    assert run("test", "send_email", data, lambda d: {"n": 1}) == {"n": 1}
    now = datetime.now(UTC)

    class Later(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return now + timedelta(days=2)

    monkeypatch.setattr(email_client, "datetime", Later)
    assert run("test", "send_email", data, lambda d: {"n": 2}) == {"n": 2}

    def records() -> int:
        return len(box.accounts._metadata(box.accounts.IDEMPOTENCY_PROVIDER))  # noqa: SLF001

    # A cleanup pass keeps live records and drops expired ones.
    monkeypatch.setattr(email_client, "datetime", datetime)
    box.accounts.cleanup_idempotency()
    assert records() == 1
    monkeypatch.setattr(email_client, "datetime", Later)
    box.accounts.cleanup_idempotency()
    assert records() == 0


# -- replies and forwards ----------------------------------------------------------


def _incoming(box: Box, **fields: Any) -> int:
    fields.setdefault("subject", "Plan")
    fields.setdefault("headers", {"Message-ID": "<orig@example.org>"})
    return box.imap.add("INBOX", raw_message(**fields))


def test_reply_answers_the_sender_in_thread_and_marks_the_original(box: Box) -> None:
    uid = _incoming(
        box,
        headers={
            "Message-ID": "<orig@example.org>",
            "References": "<older@example.org>",
            "Reply-To": "Team <team@example.org>",
        },
    )
    args = ReplyToEmail(message_id=box.id(uid), body_text="Agreed.").model_dump()
    result = box.accounts.reply("test", box.id(uid), args)
    message = _sent(box)
    assert message["To"] == "Team <team@example.org>"
    assert message["Subject"] == "Re: Plan"
    assert message["In-Reply-To"] == "<orig@example.org>"
    assert message["References"] == "<older@example.org> <orig@example.org>"
    body = message.get_content()
    assert body.startswith("Agreed.\n\nOn Fri, 13 Nov 2026 10:00:00 +0000, Ann")
    assert "> Hi there" in body
    assert result["original_marked_answered"] is True
    assert "\\Answered" in box.imap.find("INBOX", uid).flags


def test_reply_all_skips_your_own_addresses_and_can_skip_quoting(box: Box) -> None:
    uid = _incoming(
        box,
        subject="Re: Lunch",
        to="me@example.org, Bob <bob@example.org>",
        cc="Cy <cy@example.org>, ann@example.org",
    )
    args = ReplyToEmail(
        message_id=box.id(uid),
        body_text="Count me in.",
        reply_all=True,
        quote_original=False,
    ).model_dump()
    box.accounts.reply("test", box.id(uid), args)
    message = _sent(box)
    assert message["To"] == "Ann <ann@example.org>"
    assert message["Cc"] == "Bob <bob@example.org>, Cy <cy@example.org>"
    assert message["Subject"] == "Re: Lunch"
    assert message.get_content() == "Count me in.\n"


def test_replying_to_your_own_message_goes_to_its_recipients(box: Box) -> None:
    uid = _incoming(box, sender="Me <me@example.org>", to="Zed <zed@example.org>")
    args = ReplyToEmail(message_id=box.id(uid), body_text="Ping").model_dump()
    box.accounts.reply("test", box.id(uid), args)
    assert _sent(box)["To"] == "Zed <zed@example.org>"


def test_reply_can_be_saved_as_a_draft(box: Box) -> None:
    uid = _incoming(box)
    args = ReplyToEmail(
        message_id=box.id(uid), body_text="Later", save_as_draft=True
    ).model_dump()
    result = box.accounts.reply("test", box.id(uid), args)
    assert result["saved"] is True
    assert box.smtp.sent == []
    [draft] = box.imap.folders["Drafts"]
    assert draft.parsed()["Subject"] == "Re: Plan"


def test_reply_survives_bookkeeping_failures_after_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    box = _build(monkeypatch, save_sent_copy=False)
    uid = _incoming(box)
    args = ReplyToEmail(message_id=box.id(uid), body_text="Ok").model_dump()

    box.imap.fail.add("select:write")
    assert (
        box.accounts.reply("test", box.id(uid), args)["original_marked_answered"]
        is False
    )
    box.imap.fail.clear()

    box.imap.fail.add("store")
    assert (
        box.accounts.reply("test", box.id(uid), args)["original_marked_answered"]
        is False
    )
    box.imap.fail.clear()

    offline = FakeIMAP()
    offline.login_error = imaplib.IMAP4.error("denied")
    in_sequence(monkeypatch, box.imap, offline)
    result = box.accounts.reply("test", box.id(uid), args)
    assert result["sent"] is True
    assert result["original_marked_answered"] is False


def test_forward_carries_the_message_and_its_attachments(box: Box) -> None:
    uid = _incoming(
        box,
        subject="Fwd: Invoice",
        html="<p>Invoice attached</p>",
        cc="Cy <cy@example.org>",
        attachments=[("invoice.txt", "text/plain", b"total 10")],
    )
    args = ForwardEmail(
        message_id=box.id(uid),
        to=["Dee <dee@example.org>"],
        bcc=["audit@example.org"],
        body_text="FYI",
    ).model_dump()
    box.accounts.forward("test", box.id(uid), args)
    message = _sent(box)
    assert message["Subject"] == "Fwd: Invoice"
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert body.startswith("FYI\n\n---------- Forwarded message ----------\nFrom: Ann")
    assert "Cc: Cy <cy@example.org>" in body
    [attachment] = list(message.iter_attachments())
    assert attachment.get_filename() == "invoice.txt"
    assert attachment.get_content() == "total 10"
    assert box.smtp.sent[0][2] == ["dee@example.org", "audit@example.org"]


def test_forward_options(box: Box) -> None:
    uid = _incoming(
        box,
        subject="Invoice",
        attachments=[("invoice.txt", "text/plain", b"total 10")],
    )
    plain = ForwardEmail(
        message_id=box.id(uid), to=["dee@example.org"], include_attachments=False
    ).model_dump()
    box.accounts.forward("test", box.id(uid), plain)
    message = _sent(box)
    assert message["Subject"] == "Fwd: Invoice"
    assert list(message.iter_attachments()) == []
    assert message.get_content().startswith("---------- Forwarded message")

    draft = ForwardEmail(
        message_id=box.id(uid), to=["dee@example.org"], save_as_draft=True
    ).model_dump()
    assert box.accounts.forward("test", box.id(uid), draft)["saved"] is True
    [stored] = box.imap.folders["Drafts"]
    assert [p.get_filename() for p in stored.parsed().iter_attachments()] == [
        "invoice.txt"
    ]


# -- drafts ----------------------------------------------------------------------


def test_save_draft_keeps_bcc_and_returns_a_draft_id(box: Box) -> None:
    result = box.accounts.save_draft("test", _args(bcc=["hidden@example.org"]))
    assert result["saved"] is True
    assert result["folder"] == "Drafts"
    assert result["subject"] == "Status"
    [stored] = box.imap.folders["Drafts"]
    assert "\\Draft" in stored.flags
    assert stored.parsed()["Bcc"] == "hidden@example.org"
    assert result["draft_id"] == message_id(box.account_id, "Drafts", stored.uid)


def test_save_draft_without_appenduid_has_no_draft_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    box = _build(monkeypatch)
    box.imap.capabilities = ("MOVE",)
    assert box.accounts.save_draft("test", _args())["draft_id"] is None


def test_save_draft_failures(box: Box) -> None:
    box.imap.fail.add("append")
    assert _code(box.accounts.save_draft, "test", _args()) == "email.draft_failed"
    del box.imap.folders["Drafts"]
    box.imap.fail.clear()
    assert _code(box.accounts.save_draft, "test", _args()) == "email.drafts_unsupported"


def _draft(box: Box, *, flags: tuple[str, ...] = ("\\Draft",), **fields: Any) -> int:
    fields.setdefault("headers", {"Bcc": "bcc@example.org"})
    return box.imap.add("Drafts", raw_message(**fields), *flags)


def test_send_draft_delivers_then_removes_it(box: Box) -> None:
    uid = _draft(box, to="Ann <ann@example.org>", cc="cc@example.org")
    result = box.accounts.send_draft("test", box.id(uid, "Drafts"))
    message = _sent(box)
    assert message["Bcc"] is None
    assert box.smtp.sent[0][2] == [
        "ann@example.org",
        "cc@example.org",
        "bcc@example.org",
    ]
    assert message["Date"] != "Fri, 13 Nov 2026 10:00:00 +0000"
    assert result["draft_removed"] is True
    assert box.imap.folders["Drafts"] == []
    assert result["subject"] == "Hello"


def test_send_draft_validates_the_draft(box: Box) -> None:
    not_draft = _draft(box, flags=("\\Seen",))
    assert _code(box.accounts.send_draft, "test", box.id(not_draft, "Drafts")) == (
        "email.not_a_draft"
    )
    empty = box.imap.add("Drafts", b"Subject: nobody\r\n\r\nbody", "\\Draft")
    assert _code(box.accounts.send_draft, "test", box.id(empty, "Drafts")) == (
        "email.no_recipients"
    )
    assert box.smtp.sent == []


def test_a_draft_that_cannot_be_removed_is_reported_not_raised(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid = _draft(box)
    box.imap.fail.add("select:write")
    assert (
        box.accounts.send_draft("test", box.id(uid, "Drafts"))["draft_removed"] is False
    )
    box.imap.fail.clear()

    box.imap.capabilities = ("MOVE",)
    other = _draft(box)
    assert (
        box.accounts.send_draft("test", box.id(other, "Drafts"))["draft_removed"]
        is False
    )
    box.imap.capabilities = ("UIDPLUS",)
    box.imap.fail.add("store")
    third = _draft(box)
    assert (
        box.accounts.send_draft("test", box.id(third, "Drafts"))["draft_removed"]
        is False
    )
    box.imap.fail.clear()

    fourth = _draft(box)
    offline = FakeIMAP()
    offline.login_error = imaplib.IMAP4.error("denied")
    in_sequence(monkeypatch, box.imap, box.imap, offline)
    result = box.accounts.send_draft("test", box.id(fourth, "Drafts"))
    assert result["sent"] is True
    assert result["draft_removed"] is False
