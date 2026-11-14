"""Searching and reading mail through the fake IMAP server."""

from __future__ import annotations
import imaplib
import json
from typing import Any, NamedTuple
import pytest
from orcheo_backend.app.mcp_server import email_client
from orcheo_backend.app.mcp_server.email_client import EmailAccounts, SearchEmails
from orcheo_backend.app.mcp_server.email_errors import ServiceError
from tests.backend.mcp_email_support import (
    FakeIMAP,
    FakeSMTP,
    add_account,
    attachment_id,
    b64,
    install_servers,
    make_pdf,
    message_id,
    new_accounts,
    raw_message,
)


class Box(NamedTuple):
    accounts: EmailAccounts
    imap: FakeIMAP
    account_id: str

    def id(self, uid: int, folder: str = "INBOX", validity: int = 12) -> str:
        return message_id(self.account_id, folder, uid, validity)


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch) -> Box:
    accounts = new_accounts()
    imap = FakeIMAP(
        {
            "INBOX": [],
            "Sent Items": [],
            "Drafts": [],
            "Trash": [],
            "Junk": [],
            "Archive": [],
        },
        special={
            "Sent Items": "\\Sent",
            "Drafts": "\\Drafts",
            "Trash": "\\Trash",
            "Junk": "\\Junk",
            "Archive": "\\Archive",
        },
    )
    install_servers(monkeypatch, imap, FakeSMTP())
    return Box(accounts, imap, add_account(accounts))


def _code(call: Any, *args: Any, **kwargs: Any) -> str:
    with pytest.raises(ServiceError) as raised:
        call(*args, **kwargs)
    return raised.value.code


def _search(box: Box, **filters: Any) -> dict[str, Any]:
    args = SearchEmails(**filters).model_dump(mode="python")
    return box.accounts.search("test", args)


# -- search --------------------------------------------------------------------


def test_search_pages_newest_first_with_a_folder_scoped_cursor(box: Box) -> None:
    uids = [
        box.imap.add("INBOX", raw_message(subject=f"mail {n}"), *flags)
        for n, flags in enumerate(
            [("\\Seen",), (), ("\\Flagged", "\\Answered"), ("\\Seen",), ()]
        )
    ]
    first = _search(box, limit=2)
    assert [item["message_id"] for item in first["items"]] == [
        box.id(uids[4]),
        box.id(uids[3]),
    ]
    assert first["items"][0]["unread"] is True
    assert first["items"][1]["unread"] is False
    assert first["next_cursor"] == f"12.{uids[3]}"
    assert first["folder"] == "INBOX"

    second = _search(box, limit=2, cursor=first["next_cursor"])
    assert [item["subject"] for item in second["items"]] == ["mail 2", "mail 1"]
    assert second["items"][0]["flagged"] is True
    assert second["items"][0]["answered"] is True
    assert second["items"][0]["size"] > 0
    assert second["items"][0]["has_attachments"] is False
    last = _search(box, limit=2, cursor=second["next_cursor"])
    assert [item["subject"] for item in last["items"]] == ["mail 0"]
    assert last["next_cursor"] is None

    unread = _search(box, unread_only=True)
    assert [item["subject"] for item in unread["items"]] == [
        "mail 4",
        "mail 2",
        "mail 1",
    ]


def test_search_translates_filters_into_imap_criteria(box: Box) -> None:
    box.imap.add("INBOX", raw_message())
    _search(
        box,
        after="2026-11-01",
        before="2026-11-13",
        flagged=True,
        answered=False,
        larger_than_kb=2,
        from_address="ann",
        to_address='me"@example.org',
        subject="Hello",
        text="line\nbreak",
    )
    [criteria] = [c[1:] for c in box.imap.commands if c[0] == "search"]
    assert criteria == (
        "ALL",
        "SINCE",
        "01-Nov-2026",
        "BEFORE",
        "14-Nov-2026",
        "FLAGGED",
        "UNANSWERED",
        "LARGER",
        "2048",
        "FROM",
        '"ann"',
        "TO",
        '"me\\"@example.org"',
        "SUBJECT",
        '"Hello"',
        "TEXT",
        '"line break"',
    )


def test_search_resolves_special_folder_aliases(box: Box) -> None:
    sent = box.imap.add("Sent Items", raw_message(subject="outgoing"))
    result = _search(box, folder="sent")
    assert result["folder"] == "Sent Items"
    assert result["items"][0]["message_id"] == box.id(sent, "Sent Items")
    assert _search(box, folder="inbox")["folder"] == "INBOX"
    box.imap.folders["Projects"] = []
    assert _search(box, folder="Projects")["folder"] == "Projects"


def test_search_failures_are_specific(box: Box) -> None:
    box.imap.add("INBOX", raw_message())
    accounts = box.accounts
    base = SearchEmails().model_dump(mode="python")

    assert _code(_search, box, subject="café") == "email.search_charset"
    assert _code(_search, box, folder="Nowhere") == "email.folder_not_found"
    assert _code(accounts.search, "test", {**base, "folder": "bad\x01"}) == (
        "email.invalid_folder"
    )
    box.imap.fail.add("search")
    assert _code(_search, box) == "email.search_failed"
    box.imap.fail.clear()

    assert _code(_search, box, cursor="99.5") == "email.cursor_expired"
    assert _code(accounts.search, "test", {**base, "cursor": "abc"}) == (
        "email.invalid_cursor"
    )
    # A bare numeric cursor carries no folder generation and is still honoured.
    assert _search(box, cursor="1000")["items"]

    for validity in (None, b"abc"):
        box.imap.validity = validity
        assert _code(_search, box) == "email.invalid_mailbox"


def test_search_by_attachment_uses_the_mime_structure(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = box.imap.add("INBOX", raw_message(subject="plain"))
    html = box.imap.add("INBOX", raw_message(subject="html", html="<p>hi</p>"))
    pdf = box.imap.add(
        "INBOX",
        raw_message(
            subject="pdf",
            html="<p>hi</p>",
            attachments=[("report.pdf", "application/pdf", b"%PDF")],
        ),
    )

    everything = _search(box)
    flags = {item["subject"]: item["has_attachments"] for item in everything["items"]}
    assert flags == {"plain": False, "html": False, "pdf": True}
    with_files = _search(box, has_attachments=True)
    assert [item["message_id"] for item in with_files["items"]] == [box.id(pdf)]
    without = _search(box, has_attachments=False, limit=1)
    assert [item["message_id"] for item in without["items"]] == [box.id(html)]
    assert without["next_cursor"] == f"12.{html}"

    # A UID the server will not describe is reported, not silently dropped.
    box.imap.search_results = [pdf, 9999, plain]
    skipped = _search(box)
    assert skipped["skipped_uids"] == [9999]
    assert len(skipped["items"]) == 2

    monkeypatch.setattr(email_client, "_bodystructure_has_attachment", lambda m: None)
    unknown = _search(box, has_attachments=True)
    assert unknown["items"] == []
    assert sorted(unknown["skipped_uids"]) == sorted([pdf, 9999, plain])


# -- reading a message -----------------------------------------------------------


def test_get_email_returns_bounded_text_html_attachments_and_invites(box: Box) -> None:
    calendar = (
        "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
        "SUMMARY:Sync\r\nORGANIZER:mailto:ann@example.org\r\nEND:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    uid = box.imap.add(
        "INBOX",
        raw_message(
            body="word " * 400,
            html="<p>" + "<b>bold</b> " * 400 + "</p>",
            attachments=[("notes.txt", "text/plain", b"hello")],
            calendar=calendar,
            headers={"Reply-To": "Support <support@example.org>"},
        ),
        "\\Seen",
    )
    full = box.accounts.get_email("test", box.id(uid), include_html=True)["email"]
    assert full["unread"] is False
    assert full["reply_to"] == "Support <support@example.org>"
    [attachment] = full["attachments"]
    assert {k: v for k, v in attachment.items() if k != "attachment_id"} == {
        "filename": "notes.txt",
        "content_type": "text/plain",
        "size": 5,
    }
    downloaded = box.accounts.download_attachment("test", attachment["attachment_id"])
    assert downloaded["content_base64"] == b64(b"hello")
    assert full["calendar_invites"][0]["uid"] == "u1"
    assert "body_text_truncated" not in full

    cut = box.accounts.get_email(
        "test", box.id(uid), include_html=True, max_body_chars=500
    )["email"]
    assert len(cut["body_text"]) == 500
    assert cut["body_text_truncated"] is True
    assert len(cut["body_html"]) == 1500
    assert cut["body_html_truncated"] is True
    assert (
        "body_html"
        not in box.accounts.get_email("test", box.id(uid), include_html=False)["email"]
    )


def test_get_email_falls_back_to_html_text_for_html_only_mail(box: Box) -> None:
    raw = (
        b"From: a@example.org\r\nTo: me@example.org\r\nSubject: html\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><head><title>x</title></head><body><script>bad()</script>"
        b"<p>Hello<br>world</p><style>p{}</style></body></html>"
    )
    uid = box.imap.add("INBOX", raw)
    text = box.accounts.get_email("test", box.id(uid))["email"]["body_text"]
    assert text == "Hello\nworld"
    assert "bad()" not in text


@pytest.mark.parametrize(
    "bad",
    [
        "***",
        b64(b"not json"),
        b64(b'{"a": 1}'),
        b64(b'["a", "INBOX", 12]'),
        b64(b'[1, "INBOX", 12, 3]'),
        b64(b'["a", 1, 12, 3]'),
        b64(b'["a", "INBOX", 0, 3]'),
        b64(b'["a", "INBOX", 12, 0]'),
        b64(b'["a", "INBOX", "x", 3]'),
    ],
)
def test_malformed_message_ids_are_rejected(box: Box, bad: str) -> None:
    assert _code(box.accounts.get_email, "test", bad) == "email.invalid_id"


def test_fetch_problems_become_clear_errors(box: Box) -> None:
    uid = box.imap.add("INBOX", raw_message())
    get = box.accounts.get_email

    assert _code(get, "test", box.id(uid, "Nowhere")) == "email.folder_not_found"
    assert _code(get, "test", box.id(uid, validity=7)) == "email.message_expired"
    box.imap.validity = None
    assert _code(get, "test", box.id(uid)) == "email.message_expired"
    box.imap.validity = b"12"

    assert _code(get, "test", box.id(uid + 500)) == "email.message_not_found"
    box.imap.fail.add("fetch:(UID FLAGS RFC822.SIZE)")
    assert _code(get, "test", box.id(uid)) == "email.message_not_found"
    box.imap.fail.clear()
    box.imap.fail.add("fetch:(BODY.PEEK[])")
    assert _code(get, "test", box.id(uid)) == "email.message_not_found"
    box.imap.fail.clear()
    box.imap.blank.add("fetch:(BODY.PEEK[])")
    assert _code(get, "test", box.id(uid)) == "email.message_not_found"

    huge = box.imap.add("INBOX", b"Subject: big\r\n\r\n" + b"x" * 4_000_000)
    assert _code(get, "test", box.id(huge)) == "email.message_too_large"


# -- attachments -----------------------------------------------------------------


def test_download_attachment_returns_the_bytes_and_enforces_limits(box: Box) -> None:
    uid = box.imap.add(
        "INBOX",
        raw_message(
            attachments=[
                ("a.bin", "application/octet-stream", b"\x00\x01payload"),
                ("big.bin", "application/octet-stream", b"x" * 500_001),
            ]
        ),
    )
    message = box.id(uid)
    listed = box.accounts.get_email("test", message)["email"]["attachments"]
    small, big = (item["attachment_id"] for item in listed)
    downloaded = box.accounts.download_attachment("test", small)
    assert downloaded == {
        "filename": "a.bin",
        "content_type": "application/octet-stream",
        "content_base64": b64(b"\x00\x01payload"),
    }
    assert _code(box.accounts.download_attachment, "test", big) == (
        "email.attachment_too_large"
    )
    out_of_range = attachment_id(message, 99)
    assert _code(box.accounts.download_attachment, "test", out_of_range) == (
        "email.attachment_not_found"
    )
    for bad in (
        "***",
        b64(b'["note", "x", 1]'),
        b64(json.dumps(["attachment", message, "1"]).encode()),
        b64(b"[1]"),
    ):
        assert _code(box.accounts.download_attachment, "test", bad) == (
            "email.invalid_attachment"
        )


def test_attachment_text_reads_text_html_json_and_pdf(box: Box) -> None:
    uid = box.imap.add(
        "INBOX",
        raw_message(
            attachments=[
                ("a.txt", "text/plain", b"plain words " * 100),
                ("b.html", "text/html", b"<p>Hello <b>html</b></p>"),
                ("c.json", "application/json", b'{"key": "value"}'),
                ("d.pdf", "application/pdf", make_pdf("Hello PDF")),
                ("e.png", "image/png", b"\x89PNG"),
            ]
        ),
    )
    ids = [
        item["attachment_id"]
        for item in box.accounts.get_email("test", box.id(uid))["email"]["attachments"]
    ]
    read = box.accounts.attachment_text
    text, html, data, pdf, image = ids

    cut = read("test", text, 500)
    assert len(cut["text"]) == 500
    assert cut["truncated"] is True
    assert read("test", html, 500)["text"] == "Hello html"
    assert read("test", data, 500)["text"] == '{"key": "value"}'
    assert read("test", pdf, 500)["text"] == "Hello PDF"
    assert read("test", pdf, 500)["truncated"] is False
    assert _code(read, "test", image, 500) == "email.attachment_not_text"
    assert _code(read, "test", attachment_id(box.id(uid), 99), 500) == (
        "email.attachment_not_found"
    )
    assert _code(read, "test", b64(b"[1]"), 500) == "email.invalid_attachment"


def test_unreadable_pdfs_are_reported(box: Box) -> None:
    uid = box.imap.add(
        "INBOX", raw_message(attachments=[("bad.pdf", "application/pdf", b"nope")])
    )
    [item] = box.accounts.get_email("test", box.id(uid))["email"]["attachments"]
    assert _code(box.accounts.attachment_text, "test", item["attachment_id"], 500) == (
        "email.attachment_unreadable"
    )


# -- folders, links and contacts ---------------------------------------------------


def test_list_folders_reports_roles_and_counts(box: Box) -> None:
    box.imap.add("INBOX", raw_message(), "\\Seen")
    box.imap.add("INBOX", raw_message(subject="unread"))
    box.imap.list_rows = [
        b'(\\HasNoChildren) "/" "INBOX"',
        b'(\\HasNoChildren \\Sent) "/" "Sent Items"',
        b'(\\Noselect \\HasChildren) "/" "[Gmail]"',
        b'(\\HasNoChildren) "/" "Deleted Items"',
        b'(\\HasNoChildren) "/" "Projects"',
        b'(\\HasNoChildren) "/" "Missing"',
        b'(\\HasNoChildren) "/" "Archive"',
        None,
        b"garbage",
    ]
    box.imap.folders["Deleted Items"] = []
    box.imap.folders["Projects"] = []
    folders = {
        item["name"]: item
        for item in box.accounts.list_folders("test", None)["folders"]
    }
    assert "[Gmail]" not in folders
    assert folders["INBOX"] == {
        "name": "INBOX",
        "special_use": "inbox",
        "total": 2,
        "unread": 1,
    }
    assert folders["Sent Items"]["special_use"] == "sent"
    assert folders["Deleted Items"]["special_use"] == "trash"
    assert folders["Projects"]["special_use"] is None
    # Folders STATUS cannot describe still appear, without counts.
    assert folders["Missing"] == {"name": "Missing", "special_use": None}
    assert folders["Archive"] == {
        "name": "Archive",
        "special_use": "archive",
        "total": 0,
        "unread": 0,
    }

    box.imap.status_error = imaplib.IMAP4.error("denied")
    again = box.accounts.list_folders("test", box.account_id)["folders"]
    assert all("total" not in item for item in again)


def test_list_folders_is_capped(box: Box) -> None:
    box.imap.list_rows = [f'(\\HasNoChildren) "/" "f{n}"'.encode() for n in range(80)]
    assert len(box.accounts.list_folders("test", None)["folders"]) == 60


def test_event_links_rank_known_event_sites_first(box: Box) -> None:
    uid = box.imap.add(
        "INBOX",
        raw_message(
            body="See https://example.com/a and https://lu.ma/event-1 today.",
            html='<a href="https://www.eventbrite.com/e/9">go</a>',
        ),
    )
    result = box.accounts.event_links("test", box.id(uid))
    assert result["urls"] == [
        "https://lu.ma/event-1",
        "https://www.eventbrite.com/e/9",
        "https://example.com/a",
    ]
    assert result["calendar_invites"] == []


def test_find_contacts_matches_names_and_addresses_across_inbox_and_sent(
    box: Box,
) -> None:
    box.imap.add(
        "INBOX", raw_message(sender="Ann Lee <ann@example.org>", to="me@example.org")
    )
    box.imap.add(
        "INBOX", raw_message(sender="ann@example.org", cc="Bob <bob@example.org>")
    )
    box.imap.add(
        "Sent Items", raw_message(sender="me@example.org", to="Anna Ray <anna@x.org>")
    )
    found = box.accounts.find_contacts("test", None, "ann", 10)["contacts"]
    assert found[0] == {"email": "ann@example.org", "name": "Ann Lee", "messages": 2}
    assert {item["email"] for item in found} == {"ann@example.org", "anna@x.org"}
    assert box.accounts.find_contacts("test", None, "ann", 1)["contacts"] == [found[0]]
    assert box.accounts.find_contacts("test", box.account_id, "zzz", 10) == {
        "contacts": []
    }
    assert _code(box.accounts.find_contacts, "test", None, "café", 10) == (
        "email.search_charset"
    )


def test_find_contacts_skips_folders_it_cannot_read(box: Box) -> None:
    box.imap.add("INBOX", raw_message(sender="Ann <ann@example.org>"))
    box.imap.fail.add("select")
    assert box.accounts.find_contacts("test", None, "ann", 5) == {"contacts": []}
    box.imap.fail.clear()
    box.imap.fail.add("fetch")
    assert box.accounts.find_contacts("test", None, "ann", 5) == {"contacts": []}
    box.imap.fail.clear()
    box.imap.search_results = []
    assert box.accounts.find_contacts("test", None, "ann", 5) == {"contacts": []}


# -- threads ---------------------------------------------------------------------


def test_thread_collects_the_conversation_from_inbox_and_sent(box: Box) -> None:
    root = box.imap.add(
        "INBOX",
        raw_message(subject="Plan", headers={"Message-ID": "<root@example.org>"}),
    )
    box.imap.add(
        "Sent Items",
        raw_message(
            subject="Re: Plan",
            sender="me@example.org",
            headers={
                "Message-ID": "<reply@example.org>",
                "In-Reply-To": "<root@example.org>",
                "References": "<root@example.org> not-an-id",
                "Date": "Fri, 13 Nov 2026 11:00:00 +0000",
            },
        ),
        "\\Seen",
    )
    box.imap.add(
        "INBOX",
        raw_message(
            subject="Re: Re: Plan",
            headers={
                "Message-ID": "<third@example.org>",
                "References": "<root@example.org> <reply@example.org>",
                "Date": "garbled date",
            },
        ),
    )
    box.imap.add("INBOX", raw_message(subject="Unrelated"))

    thread = box.accounts.thread("test", box.id(root), limit=10)
    assert thread["account_id"] == box.account_id
    subjects = [item["subject"] for item in thread["messages"]]
    assert subjects == ["Re: Re: Plan", "Plan", "Re: Plan"]
    assert {item["folder"] for item in thread["messages"]} == {"INBOX", "Sent Items"}
    assert thread["messages"][2]["unread"] is False

    assert [
        item["subject"]
        for item in box.accounts.thread("test", box.id(root), 1)["messages"]
    ] == ["Re: Plan"]


def test_thread_tolerates_unreadable_folders_and_headers(box: Box) -> None:
    root = box.imap.add("INBOX", raw_message(headers={"Message-ID": "<r@example.org>"}))
    box.imap.add("Sent Items", raw_message(headers={"In-Reply-To": "<r@example.org>"}))
    only_root = box.accounts.thread("test", box.id(root))["messages"]
    assert len(only_root) == 2

    box.imap.fail.add(
        "fetch:(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID)])"
    )
    assert box.accounts.thread("test", box.id(root))["messages"] == []
    box.imap.fail.clear()

    # A folder that cannot be selected, or has no UIDVALIDITY, is skipped; the
    # original folder still answers.
    original_select = box.imap.select

    def select(folder: str, readonly: bool = False) -> tuple[str, list]:
        if "Sent" in folder:
            return "NO", []
        return original_select(folder, readonly)

    box.imap.select = select  # type: ignore[method-assign]
    assert len(box.accounts.thread("test", box.id(root))["messages"]) == 1
    box.imap.select = original_select  # type: ignore[method-assign]


def test_thread_needs_a_stable_mailbox_identifier(box: Box) -> None:
    root = box.imap.add("INBOX", raw_message(headers={"Message-ID": "<r@example.org>"}))
    box.imap.validity = None
    # The message itself cannot be located without a UIDVALIDITY.
    assert _code(box.accounts.thread, "test", box.id(root)) == "email.message_expired"


# -- robustness ------------------------------------------------------------------


def test_mail_with_an_unknown_charset_is_still_readable(box: Box) -> None:
    body = "caf\xe9".encode("latin-1")
    raw = (
        b"From: a@example.org\r\nTo: me@example.org\r\nSubject: odd\r\n"
        b"Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\n"
        b"Content-Type: text/plain; charset=made-up\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n\r\n" + body + b"\r\n--b\r\n"
        b'Content-Type: text/plain; charset=made-up; name="n.txt"\r\n'
        b'Content-Disposition: attachment; filename="n.txt"\r\n'
        b"Content-Transfer-Encoding: 8bit\r\n\r\n" + body + b"\r\n--b\r\n"
        b'Content-Type: text/html; charset=made-up; name="n.html"\r\n'
        b'Content-Disposition: attachment; filename="n.html"\r\n'
        b"Content-Transfer-Encoding: 8bit\r\n\r\n<p>" + body + b"</p>\r\n--b--\r\n"
    )
    uid = box.imap.add("INBOX", raw)
    email = box.accounts.get_email("test", box.id(uid))["email"]
    assert email["body_text"].startswith("caf")
    texts = [
        box.accounts.attachment_text("test", item["attachment_id"], 500)["text"]
        for item in email["attachments"]
    ]
    assert [t.strip() for t in texts] == ["caf�", "caf�"]


def test_a_server_that_hangs_up_on_logout_does_not_break_operations(box: Box) -> None:
    pdf = box.imap.add(
        "INBOX", raw_message(attachments=[("a.bin", "application/octet-stream", b"x")])
    )
    message = box.id(pdf)
    box.imap.logout_error = OSError("connection reset")
    _search(box)
    attachment = box.accounts.get_email("test", message)["email"]["attachments"][0]
    box.accounts.download_attachment("test", attachment["attachment_id"])
    box.accounts.mark_read("test", [message], True)
    box.accounts.list_folders("test", None)
    box.imap.logout_error = imaplib.IMAP4.error("bye")
    box.accounts.mark_read("test", [message], False)
    assert len(box.accounts.thread("test", message)["messages"]) == 1
    assert box.imap.logouts >= 5


def test_fetch_ignores_protocol_noise_around_the_size_line(box: Box) -> None:
    uid = box.imap.add("INBOX", raw_message(subject="noisy"))
    original = box.imap.uid

    def uid_with_noise(command: str, *args: str) -> tuple[str, list[Any]]:
        kind, data = original(command, *args)
        if args and args[-1] == "(UID FLAGS RFC822.SIZE)":
            data = [None, *data]
        return kind, data

    box.imap.uid = uid_with_noise  # type: ignore[method-assign]
    assert box.accounts.get_email("test", box.id(uid))["email"]["subject"] == "noisy"


def test_attachment_ids_must_name_an_attachment(box: Box) -> None:
    for bad in (b64(b'["note", "x", 1]'), b64(b'["attachment", "x", "1"]')):
        assert _code(box.accounts.attachment_text, "test", bad, 500) == (
            "email.invalid_attachment"
        )


def test_thread_skips_a_folder_whose_validity_disappears(box: Box) -> None:
    root = box.imap.add("INBOX", raw_message(headers={"Message-ID": "<r@example.org>"}))
    original = box.imap.response
    calls: list[str] = []

    def response(name: str) -> tuple[str, list]:
        calls.append(name)
        # The message is located normally; the thread search then sees no validity.
        return original(name) if len(calls) == 1 else (name, [])

    box.imap.response = response  # type: ignore[method-assign]
    assert box.accounts.thread("test", box.id(root))["messages"] == []


@pytest.mark.parametrize(
    "index",
    [-1, True, "1", 1.5, None],
    ids=["negative", "bool", "str", "float", "none"],
)
def test_attachment_ids_reject_indexes_that_are_not_part_numbers(
    box: Box, index: Any
) -> None:
    uid = box.imap.add(
        "INBOX", raw_message(attachments=[("a.txt", "text/plain", b"hello")])
    )
    bad = b64(json.dumps(["attachment", box.id(uid), index]).encode())
    assert _code(box.accounts.download_attachment, "test", bad) == (
        "email.invalid_attachment"
    )
    assert _code(box.accounts.attachment_text, "test", bad, 500) == (
        "email.invalid_attachment"
    )
    wrong_owner = b64(json.dumps(["attachment", 7, 1]).encode())
    assert _code(box.accounts.download_attachment, "test", wrong_owner) == (
        "email.invalid_attachment"
    )


def test_a_non_numeric_uidvalidity_is_a_stale_identifier(box: Box) -> None:
    uid = box.imap.add("INBOX", raw_message())
    box.imap.validity = b"not-a-number"
    assert _code(box.accounts.get_email, "test", box.id(uid)) == "email.message_expired"
    assert _code(_search, box) == "email.invalid_mailbox"


def test_thread_searches_and_fetches_once_per_folder(box: Box) -> None:
    root = box.imap.add(
        "INBOX",
        raw_message(
            subject="Plan",
            headers={
                "Message-ID": "<root@example.org>",
                "In-Reply-To": "<parent@example.org>",
                "References": "<parent@example.org> <root@example.org>",
            },
        ),
    )
    for number in range(4):
        box.imap.add(
            "INBOX",
            raw_message(
                subject=f"Re: Plan {number}",
                headers={
                    "Message-ID": f"<r{number}@example.org>",
                    "In-Reply-To": "<root@example.org>",
                },
            ),
        )
    box.imap.add("Sent Items", raw_message(subject="Unrelated"))
    box.imap.commands.clear()

    thread = box.accounts.thread("test", box.id(root), limit=10)

    assert sorted(item["subject"] for item in thread["messages"]) == [
        "Plan",
        "Re: Plan 0",
        "Re: Plan 1",
        "Re: Plan 2",
        "Re: Plan 3",
    ]
    searches = [c for c in box.imap.commands if c[0] == "search"]
    fetches = [c for c in box.imap.commands if c[0] == "fetch" and "," in c[1]]
    # INBOX and the one Sent folder: a single OR search each, one batched fetch.
    assert len(searches) == 2
    assert len(fetches) == 1
    criteria = searches[0][1:]
    # Three header fields for each of the two Message-IDs, nested pairwise.
    assert criteria.count("HEADER") == 6
    assert criteria.count("OR") == 5
    assert criteria[:2] == ("OR", "HEADER")
    assert criteria[-3] == "HEADER"


def test_thread_of_a_message_with_no_ids_finds_nothing_to_search_for(box: Box) -> None:
    uid = box.imap.add(
        "INBOX", b"From: a@example.org\r\nSubject: bare\r\n\r\nNo identifiers here."
    )
    box.imap.commands.clear()
    assert box.accounts.thread("test", box.id(uid))["messages"] == []
    assert [c for c in box.imap.commands if c[0] == "search"] == []


def test_thread_skips_fetch_responses_it_cannot_attribute_to_a_uid(box: Box) -> None:
    root = box.imap.add("INBOX", raw_message(headers={"Message-ID": "<r@example.org>"}))
    original = box.imap.uid

    def uid_without_uids(command: str, *args: str) -> tuple[str, list[Any]]:
        kind, data = original(command, *args)
        if command == "fetch" and "," in args[0]:
            data = [
                (part[0].replace(b"UID", b"XXX"), part[1])
                if isinstance(part, tuple)
                else part
                for part in data
            ]
        return kind, data

    box.imap.add("INBOX", raw_message(headers={"In-Reply-To": "<r@example.org>"}))
    box.imap.uid = uid_without_uids  # type: ignore[method-assign]
    assert box.accounts.thread("test", box.id(root))["messages"] == []
