"""Marking and managing messages through the fake IMAP server."""

from __future__ import annotations
from typing import Any, NamedTuple
import pytest
from orcheo_backend.app.mcp_server.email_client import EmailActions, EmailAccounts
from orcheo_backend.app.mcp_server.email_errors import ServiceError
from tests.backend.mcp_email_support import (
    FakeIMAP,
    FakeSMTP,
    add_account,
    install_servers,
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


def _build(
    monkeypatch: pytest.MonkeyPatch,
    *,
    capabilities: tuple[str, ...] = ("UIDPLUS", "MOVE"),
    folders: tuple[str, ...] = ("Sent", "Trash", "Junk", "Archive"),
) -> Box:
    accounts = new_accounts()
    special = {
        "Sent": "\\Sent",
        "Trash": "\\Trash",
        "Junk": "\\Junk",
        "Archive": "\\Archive",
    }
    imap = FakeIMAP(
        {"INBOX": [], **{name: [] for name in folders}},
        special={name: special[name] for name in folders},
        capabilities=capabilities,
    )
    install_servers(monkeypatch, imap, FakeSMTP())
    return Box(accounts, imap, add_account(accounts))


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch) -> Box:
    return _build(monkeypatch)


def _code(call: Any, *args: Any, **kwargs: Any) -> str:
    with pytest.raises(ServiceError) as raised:
        call(*args, **kwargs)
    return raised.value.code


def _manage(box: Box, ids: list[str], action: str, **extra: Any) -> dict[str, Any]:
    args = EmailActions(message_ids=ids, action=action, **extra).model_dump()
    return box.accounts.manage_messages("test", args)


# -- read state ------------------------------------------------------------------


def test_mark_read_and_unread_report_exactly_what_changed(box: Box) -> None:
    first = box.imap.add("INBOX", raw_message(subject="one"))
    second = box.imap.add("INBOX", raw_message(subject="two"), "\\Seen")
    ids = [box.id(first), box.id(second)]

    read = box.accounts.mark_read("test", ids, True)
    assert read == {"is_read": True, "affected_count": 2, "message_ids": ids}
    assert "\\Seen" in box.imap.find("INBOX", first).flags
    unread = box.accounts.mark_read("test", ids[:1], False)
    assert unread["message_ids"] == ids[:1]
    assert "\\Seen" not in box.imap.find("INBOX", first).flags


def test_mark_read_reports_messages_it_could_not_touch(box: Box) -> None:
    uid = box.imap.add("INBOX", raw_message())
    sent = box.imap.add("Sent", raw_message())
    ids = [box.id(uid), box.id(sent, "Sent"), box.id(uid + 500)]
    result = box.accounts.mark_read("test", ids, True)
    assert result["message_ids"] == ids[:2]
    assert result["affected_count"] == 2
    assert result["failed"] == [{"message_id": ids[2], "code": "email.action_failed"}]

    # A renumbered folder or one that cannot be opened is reported, not skipped.
    stale_id = box.id(uid, validity=7)
    stale = box.accounts.mark_read("test", [stale_id], True)
    expired = [{"message_id": stale_id, "code": "email.message_expired"}]
    assert stale == {
        "is_read": True,
        "affected_count": 0,
        "message_ids": [],
        "failed": expired,
    }
    box.imap.fail.add("select")
    gone = box.accounts.mark_read("test", [box.id(uid)], True)
    assert gone["failed"] == [
        {"message_id": box.id(uid), "code": "email.message_expired"}
    ]
    assert gone["affected_count"] == 0


def test_batch_operations_span_at_most_five_folders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    box = _build(monkeypatch)
    ids = []
    for index in range(6):
        folder = f"Folder{index}"
        box.imap.folders[folder] = []
        ids.append(box.id(box.imap.add(folder, raw_message()), folder))
    assert (
        _code(box.accounts.mark_read, "test", ids, True) == "email.too_many_mailboxes"
    )
    assert _code(_manage, box, ids, "flag") == "email.too_many_mailboxes"


# -- actions ---------------------------------------------------------------------


def test_flags_are_set_and_cleared(box: Box) -> None:
    uid = box.imap.add("INBOX", raw_message())
    ids = [box.id(uid)]
    for action, flag, present in (
        ("flag", "\\Flagged", True),
        ("unflag", "\\Flagged", False),
        ("mark_answered", "\\Answered", True),
    ):
        result = _manage(box, ids, action)
        assert result == {"action": action, "affected_count": 1, "message_ids": ids}
        assert (flag in box.imap.find("INBOX", uid).flags) is present


def test_move_copy_and_archive_report_the_new_message_ids(box: Box) -> None:
    uid = box.imap.add("INBOX", raw_message(subject="movable"))
    copied = _manage(box, [box.id(uid)], "copy", target_folder="Archive")
    [copy] = copied["moved"]
    assert copy["message_id"] == box.id(uid)
    assert box.imap.find("INBOX", uid) is not None
    [archived_copy] = box.imap.folders["Archive"]
    assert copy["new_message_id"] == box.id(archived_copy.uid, "Archive")

    moved = _manage(box, [box.id(uid)], "move", target_folder="Projects")
    assert moved["affected_count"] == 1
    assert box.imap.find("INBOX", uid) is None

    other = box.imap.add("INBOX", raw_message(subject="old"))
    archived = _manage(box, [box.id(other)], "archive")
    assert archived["moved"][0]["new_message_id"].startswith("W")
    assert [m.parsed()["Subject"] for m in box.imap.folders["Archive"]][-1] == "old"


def test_move_falls_back_to_copy_and_expunge_without_the_move_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    box = _build(monkeypatch, capabilities=("UIDPLUS",))
    uid = box.imap.add("INBOX", raw_message())
    result = _manage(box, [box.id(uid)], "move", target_folder="Archive")
    assert result["affected_count"] == 1
    assert box.imap.find("INBOX", uid) is None
    assert len(box.imap.folders["Archive"]) == 1

    box.imap.fail.add("copy")
    other = box.imap.add("INBOX", raw_message())
    failed = _manage(box, [box.id(other)], "move", target_folder="Archive")
    assert failed["failed"] == [
        {"message_id": box.id(other), "code": "email.action_failed"}
    ]
    assert "moved" not in failed
    box.imap.fail.clear()
    box.imap.fail.add("store")
    stuck = _manage(box, [box.id(other)], "move", target_folder="Archive")
    assert stuck["failed"][0]["code"] == "email.action_failed"


def test_move_needs_a_server_that_can_move_one_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    box = _build(monkeypatch, capabilities=())
    uid = box.imap.add("INBOX", raw_message())
    assert _code(_manage, box, [box.id(uid)], "move", target_folder="Archive") == (
        "email.move_unsupported"
    )
    assert box.imap.find("INBOX", uid) is not None


def test_delete_goes_to_trash_and_never_expunges_by_default(box: Box) -> None:
    uid = box.imap.add("INBOX", raw_message())
    result = _manage(box, [box.id(uid)], "delete")
    assert result["permanent"] is False
    assert box.imap.find("INBOX", uid) is None
    [trashed] = box.imap.folders["Trash"]

    # Deleting from Trash would be permanent, so it needs an explicit opt-in.
    assert _code(_manage, box, [box.id(trashed.uid, "Trash")], "delete") == (
        "email.already_in_trash"
    )
    purged = _manage(box, [box.id(trashed.uid, "Trash")], "delete", permanent=True)
    assert purged["permanent"] is True
    assert box.imap.folders["Trash"] == []


def test_delete_without_a_trash_folder_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    box = _build(monkeypatch, folders=("Sent",))
    uid = box.imap.add("INBOX", raw_message())
    assert _code(_manage, box, [box.id(uid)], "delete") == "email.trash_unsupported"
    assert box.imap.find("INBOX", uid) is not None


def test_permanent_delete_requires_uidplus(monkeypatch: pytest.MonkeyPatch) -> None:
    box = _build(monkeypatch, capabilities=("MOVE",))
    uid = box.imap.add("INBOX", raw_message())
    assert _code(_manage, box, [box.id(uid)], "delete", permanent=True) == (
        "email.delete_unsupported"
    )
    assert box.imap.find("INBOX", uid) is not None


def test_junk_and_not_junk(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    uid = box.imap.add("INBOX", raw_message())
    _manage(box, [box.id(uid)], "junk")
    [junked] = box.imap.folders["Junk"]
    back = _manage(box, [box.id(junked.uid, "Junk")], "not_junk")
    assert back["affected_count"] == 1
    assert len(box.imap.folders["INBOX"]) == 1

    bare = _build(monkeypatch, folders=("Sent",))
    other = bare.imap.add("INBOX", raw_message())
    assert _code(_manage, bare, [bare.id(other)], "junk") == "email.junk_unsupported"


def test_archive_needs_an_archive_folder_or_gmail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = _build(monkeypatch, folders=("Sent",))
    uid = plain.imap.add("INBOX", raw_message())
    assert _code(_manage, plain, [plain.id(uid)], "archive") == (
        "email.archive_unsupported"
    )

    gmail = _build(
        monkeypatch, folders=("Sent",), capabilities=("UIDPLUS", "X-GM-EXT-1")
    )
    inbox_uid = gmail.imap.add("INBOX", raw_message())
    result = _manage(gmail, [gmail.id(inbox_uid)], "archive")
    assert result["affected_count"] == 1
    assert ("store", str(inbox_uid), "-X-GM-LABELS", "(\\Inbox)") in [
        command for command in gmail.imap.commands
    ]
    # Outside the Inbox there is no label to remove.
    gmail.imap.folders["Elsewhere"] = []
    elsewhere = gmail.imap.add("Elsewhere", raw_message())
    assert _code(_manage, gmail, [gmail.id(elsewhere, "Elsewhere")], "archive") == (
        "email.archive_unsupported"
    )


def test_labels_are_gmail_labels_or_plain_keywords(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid = box.imap.add("INBOX", raw_message())
    _manage(box, [box.id(uid)], "add_labels", labels=["Needs Review"] and ["urgent"])
    assert ("store", str(uid), "+FLAGS.SILENT", "(urgent)") in box.imap.commands
    _manage(box, [box.id(uid)], "remove_labels", labels=["\\Draft"])
    assert ("store", str(uid), "-FLAGS.SILENT", "(_Draft)") in box.imap.commands

    gmail = _build(monkeypatch, capabilities=("UIDPLUS", "X-GM-EXT-1"))
    g_uid = gmail.imap.add("INBOX", raw_message())
    _manage(gmail, [gmail.id(g_uid)], "add_labels", labels=["work"])
    assert ("store", str(g_uid), "+X-GM-LABELS", '("work")') in gmail.imap.commands
    _manage(gmail, [gmail.id(g_uid)], "remove_labels", labels=["work"])
    assert ("store", str(g_uid), "-X-GM-LABELS", '("work")') in gmail.imap.commands


def test_manage_reports_failures_per_message(box: Box) -> None:
    uid = box.imap.add("INBOX", raw_message())
    missing = box.id(uid + 500)
    result = _manage(box, [box.id(uid), missing], "flag")
    assert result["message_ids"] == [box.id(uid)]
    assert result["failed"] == [{"message_id": missing, "code": "email.action_failed"}]

    stale = _manage(box, [box.id(uid, validity=7)], "flag")
    assert stale["failed"] == [
        {"message_id": box.id(uid, validity=7), "code": "email.message_expired"}
    ]
    assert stale["affected_count"] == 0


def test_copyuid_is_only_trusted_for_a_single_message(box: Box) -> None:
    copied_id = box.accounts._copied_id  # noqa: SLF001
    plan = {"kind": "move", "target": "Archive"}
    assert copied_id("a", {"kind": "store"}, [b"[COPYUID 12 1 2]"], 1) is None
    assert copied_id("a", plan, [b"Moved"], 1) is None
    assert copied_id("a", plan, None, 1) is None
    assert copied_id("a", plan, [b"[COPYUID 12 1:3 4:6]"], 1) is None
    assert copied_id("a", plan, [None, b"[COPYUID 12 1 7]"], 1) == message_id(
        "a", "Archive", 7
    )


def test_a_server_that_hangs_up_on_logout_does_not_break_actions(box: Box) -> None:
    uid = box.imap.add("INBOX", raw_message())
    box.imap.logout_error = OSError("connection reset")
    result = _manage(box, [box.id(uid)], "flag")
    assert result["affected_count"] == 1
    assert box.imap.logouts == 1


def test_a_batch_resolves_each_mailbox_once(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    uids = [box.imap.add("INBOX", raw_message(subject=f"m{n}")) for n in range(6)]
    ids = [box.id(uid) for uid in uids]
    lookups: list[str | None] = []
    original = box.accounts._selected_account  # noqa: SLF001

    def spy(actor_id: str, account_id: str | None) -> dict[str, Any]:
        lookups.append(account_id)
        return original(actor_id, account_id)

    monkeypatch.setattr(box.accounts, "_selected_account", spy)
    assert box.accounts.mark_read("test", ids, True)["affected_count"] == 6
    assert lookups == [box.account_id]
    lookups.clear()
    assert _manage(box, ids, "flag")["affected_count"] == 6
    assert lookups == [box.account_id]


def test_a_non_numeric_uidvalidity_expires_the_batch(box: Box) -> None:
    uid = box.imap.add("INBOX", raw_message())
    box.imap.validity = b"not-a-number"
    expired = [{"message_id": box.id(uid), "code": "email.message_expired"}]
    assert box.accounts.mark_read("test", [box.id(uid)], True)["failed"] == expired
    assert _manage(box, [box.id(uid)], "flag")["failed"] == expired
