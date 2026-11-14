"""Scheduled sending: queueing, dispatch, failure handling and cancellation."""

from __future__ import annotations
import json
import smtplib
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple
from uuid import UUID, uuid4
import pytest
from orcheo.vault import CredentialNotFoundError
from orcheo_backend.app.mcp_server.email_client import EmailAccounts, SendEmail
from orcheo_backend.app.mcp_server.email_errors import ServiceError
from tests.backend.mcp_email_support import (
    FakeIMAP,
    FakeSMTP,
    add_account,
    install_servers,
    new_accounts,
)


class Box(NamedTuple):
    accounts: EmailAccounts
    smtp: FakeSMTP
    account_id: str


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch) -> Box:
    accounts = new_accounts()
    smtp = FakeSMTP()
    install_servers(
        monkeypatch,
        FakeIMAP({"Sent": []}, special={"Sent": "\\Sent"}),
        smtp,
    )
    return Box(accounts, smtp, add_account(accounts))


def _queue(box: Box, minutes: int, **fields: Any) -> str:
    fields.setdefault("subject", f"in {minutes}")
    fields.setdefault("to", ["you@example.org"])
    fields.setdefault("body_text", "Body")
    args = SendEmail(**fields).model_dump(mode="python")
    when = (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()
    return box.accounts.schedule("test", args, when)["scheduled_id"]


def _record(box: Box, scheduled_id: str) -> dict[str, Any]:
    secret = box.accounts.vault.reveal_secret(
        credential_id=UUID(scheduled_id), context=box.accounts.context
    )
    return json.loads(secret)


def test_only_due_messages_are_sent_and_their_records_are_removed(box: Box) -> None:
    due = _queue(box, -2, bcc=["hidden@example.org"])
    later = _queue(box, 60)
    assert box.accounts.process_scheduled() == {"sent": 1, "failed": 0, "recovered": 0}

    [(message, _, envelope)] = box.smtp.sent
    assert message["Subject"] == "in -2"
    assert envelope == ["you@example.org", "hidden@example.org"]
    assert [item["scheduled_id"] for item in box.accounts.list_scheduled("test")] == [
        later
    ]
    assert box.accounts.process_scheduled() == {"sent": 0, "failed": 0, "recovered": 0}
    assert _record(box, later)["status"] == "scheduled"
    with pytest.raises(CredentialNotFoundError):
        _record(box, due)


def test_the_batch_limit_and_send_order(box: Box) -> None:
    _queue(box, -1, subject="second")
    _queue(box, -3, subject="first")
    _queue(box, -2, subject="middle")
    assert box.accounts.process_scheduled(limit=2) == {
        "sent": 2,
        "failed": 0,
        "recovered": 0,
    }
    assert [m["Subject"] for m, _, _ in box.smtp.sent] == ["first", "middle"]
    assert box.accounts.process_scheduled() == {"sent": 1, "failed": 0, "recovered": 0}


def test_a_rejected_message_is_kept_as_failed_and_not_retried(box: Box) -> None:
    box.smtp.send_error = smtplib.SMTPDataError(554, b"5.7.1 spam suspected")
    scheduled_id = _queue(box, -1)
    assert box.accounts.process_scheduled() == {"sent": 0, "failed": 1, "recovered": 0}
    [failed] = box.accounts.list_scheduled("test")
    assert failed["status"] == "failed"
    assert failed["account_id"] == box.account_id
    assert "rejected the message content" in failed["last_error"]
    assert "spam suspected" not in failed["last_error"]
    box.smtp.send_error = None
    assert box.accounts.process_scheduled() == {"sent": 0, "failed": 0, "recovered": 0}
    assert box.smtp.sent == []
    # The failure can be acknowledged and cleared; it is never sent again.
    assert box.accounts.cancel_scheduled("test", scheduled_id) == {
        "scheduled_id": scheduled_id,
        "status": "dismissed",
    }
    assert box.accounts.list_scheduled("test") == []
    with pytest.raises(ServiceError, match="cancelled or dismissed"):
        box.accounts.cancel_scheduled("test", scheduled_id)


def test_unexpected_errors_are_recorded_without_their_details(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("token=hunter2")

    monkeypatch.setattr(box.accounts, "send", explode)
    _queue(box, -1)
    assert box.accounts.process_scheduled() == {"sent": 0, "failed": 1, "recovered": 0}
    [failed] = box.accounts.list_scheduled("test")
    assert failed["last_error"] == "Scheduled email failed (RuntimeError)."


def test_a_deleted_mailbox_fails_the_message_with_a_clear_reason(box: Box) -> None:
    _queue(box, -1)
    box.accounts.delete_account("test", box.account_id)
    assert box.accounts.process_scheduled() == {"sent": 0, "failed": 1, "recovered": 0}
    [failed] = box.accounts.list_scheduled("test")
    assert failed["last_error"] == "Email account not found."


@pytest.mark.parametrize(
    "send_at", ["not a date", "2026-12-01T09:00:00", None, 12], ids=repr
)
def test_records_with_an_unusable_time_fail_instead_of_looping(
    box: Box, send_at: Any
) -> None:
    secret: dict[str, Any] = {"status": "scheduled", "args": {}, "subject": "odd"}
    if send_at is not None:
        secret["send_at"] = send_at
    box.accounts.vault.create_credential(
        name="odd",
        provider=box.accounts.SCHEDULE_PROVIDER,
        scopes=[],
        secret=json.dumps(secret),
        actor="test",
        workspace_id=box.accounts.workspace_id,
    )
    assert box.accounts.process_scheduled() == {"sent": 0, "failed": 1, "recovered": 0}
    [failed] = box.accounts.list_scheduled("test")
    assert failed["last_error"] == "Scheduled time is invalid."
    assert box.accounts.process_scheduled() == {"sent": 0, "failed": 0, "recovered": 0}


def test_cancelling_removes_the_message_and_rejects_anything_else(box: Box) -> None:
    scheduled_id = _queue(box, 30)
    assert box.accounts.cancel_scheduled("test", scheduled_id) == {
        "scheduled_id": scheduled_id,
        "status": "cancelled",
    }
    assert box.accounts.list_scheduled("test") == []
    for bad in (scheduled_id, "not-a-uuid", str(uuid4()), box.account_id):
        with pytest.raises(ServiceError) as raised:
            box.accounts.cancel_scheduled("test", bad)
        assert raised.value.code == "email.scheduled_not_found"


def test_records_that_vanish_or_change_under_the_dispatcher_are_skipped(
    box: Box,
) -> None:
    missing = uuid4()
    now = datetime.now(UTC)
    assert box.accounts._claim_schedule(missing) is None  # noqa: SLF001
    assert box.accounts._recover_schedule(missing, now) is False  # noqa: SLF001
    assert box.accounts._finish_schedule(missing, None, "gone") is None  # noqa: SLF001
    # A mailbox credential is never mistaken for a scheduled message.
    mailbox = UUID(box.account_id)
    assert box.accounts._email_record(mailbox, box.accounts.SCHEDULE_PROVIDER) is None  # noqa: SLF001
    assert box.accounts._claim_schedule(mailbox) is None  # noqa: SLF001

    scheduled_id = _queue(box, -1)
    claimed = box.accounts._claim_schedule(UUID(scheduled_id))  # noqa: SLF001
    assert claimed is not None
    assert box.accounts._claim_schedule(UUID(scheduled_id)) is None  # noqa: SLF001
    assert box.accounts._recover_schedule(UUID(scheduled_id), now) is False  # noqa: SLF001


def test_schedules_are_listed_in_send_order_with_partial_details(box: Box) -> None:
    late = _queue(box, 90, subject="late")
    early = _queue(box, 10, subject="early")
    listed = box.accounts.list_scheduled("test")
    assert [item["scheduled_id"] for item in listed] == [early, late]
    assert listed[0]["status"] == "scheduled"
    assert listed[0]["subject"] == "early"
    assert listed[0]["last_error"] is None


def test_partly_delivered_mail_can_be_dismissed(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduled_id = _queue(box, -1)
    outcome = {
        "sent": False,
        "delivery_status": "partial",
        "refused_recipients": ["bad@example.org"],
        "message_id": "<m@example.org>",
        "saved_to_sent": True,
    }
    monkeypatch.setattr(box.accounts, "send", lambda *args, **kwargs: outcome)
    assert box.accounts.process_scheduled()["failed"] == 1
    assert box.accounts.list_scheduled("test")[0]["status"] == "partial"
    assert box.accounts.cancel_scheduled("test", scheduled_id)["status"] == "dismissed"
    assert box.accounts.list_scheduled("test") == []


def test_a_message_being_sent_cannot_be_cancelled(box: Box) -> None:
    scheduled_id = _queue(box, -1)
    assert box.accounts._claim_schedule(UUID(scheduled_id)) is not None  # noqa: SLF001
    assert box.accounts.list_scheduled("test")[0]["status"] == "sending"
    with pytest.raises(ServiceError, match="cancelled or dismissed"):
        box.accounts.cancel_scheduled("test", scheduled_id)
    assert _record(box, scheduled_id)["status"] == "sending"


def test_an_interrupted_send_is_recovered_not_counted_as_a_failed_delivery(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orcheo_backend.app.mcp_server import email_client

    scheduled_id = _queue(box, -1)
    box.accounts._claim_schedule(UUID(scheduled_id))  # noqa: SLF001
    now = datetime.now(UTC)

    class Later(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return now + timedelta(hours=1)

    monkeypatch.setattr(email_client, "datetime", Later)
    assert box.accounts.process_scheduled() == {
        "sent": 0,
        "failed": 0,
        "recovered": 1,
    }
    [failed] = box.accounts.list_scheduled("test")
    assert failed["status"] == "failed"
    assert "Interrupted while sending" in failed["last_error"]
    assert box.smtp.sent == []
