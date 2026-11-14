"""Mailbox settings, selection and the pinned IMAP/SMTP connections."""

from __future__ import annotations
import imaplib
import importlib
import json
import smtplib
import socket
import ssl
from typing import Any
import pytest
from orcheo.workspace import WorkspaceAuditEvent
from orcheo_backend.app.mcp_server import email_client
from orcheo_backend.app.mcp_server.email_client import EmailAccounts
from orcheo_backend.app.mcp_server.email_errors import ServiceError
from tests.backend.mcp_email_support import (
    ACCOUNT_SECRET,
    SMTP_SECRET,
    FakeIMAP,
    FakeSMTP,
    account_payload,
    add_account,
    install_servers,
    new_accounts,
)


@pytest.fixture
def accounts() -> EmailAccounts:
    return new_accounts()


# ``orcheo_backend.app`` names the FastAPI instance, so resolve the module directly.
workspace_module = importlib.import_module("orcheo_backend.app.workspace")


def _code(call: Any, *args: Any, **kwargs: Any) -> str:
    with pytest.raises(ServiceError) as raised:
        call(*args, **kwargs)
    return raised.value.code


def test_first_mailbox_becomes_default_and_secrets_stay_private(
    accounts: EmailAccounts,
) -> None:
    first = add_account(accounts)
    second = add_account(accounts, email_address="two@example.org")

    listed = accounts.list_accounts("test")
    assert [item["account_id"] for item in listed] == [first, second]
    assert [item["is_default"] for item in listed] == [True, False]
    public = accounts.list_agent_accounts("test")
    assert public[0]["health"] == {
        "imap": {"status": "not-tested", "tested_at": None},
        "smtp": {"status": "not-tested", "tested_at": None},
    }
    assert public[0]["has_signature"] is False
    serialized = json.dumps([listed, public, accounts.get_account_settings("t", first)])
    assert ACCOUNT_SECRET not in serialized
    assert SMTP_SECRET not in serialized
    assert "host" not in json.dumps(public)


def test_editing_keeps_saved_passwords_until_the_server_changes(
    accounts: EmailAccounts,
) -> None:
    account_id = add_account(accounts)
    keep = account_payload(
        account_id=account_id, display_name="Renamed", signature_text="Cheers"
    )
    keep["imap"].pop("password")
    keep["smtp"].pop("password")
    saved = accounts.save_account("test", keep)
    assert saved["display_name"] == "Renamed"
    assert saved["signature_text"] == "Cheers"
    row = accounts._row("test", account_id)  # noqa: SLF001
    assert row["imap_secret"] == ACCOUNT_SECRET
    assert row["smtp_secret"] == SMTP_SECRET

    moved = account_payload(account_id=account_id)
    moved["imap"].update(host="other.example.org")
    moved["imap"].pop("password")
    assert _code(accounts.save_account, "test", moved) == "email.password_required"

    first = account_payload()
    first["imap"].pop("password")
    assert _code(accounts.save_account, "test", first) == "email.password_required"


def test_default_mailbox_rules(accounts: EmailAccounts) -> None:
    first = add_account(accounts)
    second = add_account(accounts, email_address="two@example.org")
    assert accounts.set_default("test", second) == {
        "account_id": second,
        "is_default": True,
    }
    assert [a["is_default"] for a in accounts.list_accounts("test")] == [True, False]
    assert accounts.list_accounts("test")[0]["account_id"] == second

    disabled = add_account(accounts, email_address="off@example.org", enabled=False)
    assert _code(accounts.set_default, "test", disabled) == "email.account_disabled"
    wants_default = account_payload(
        email_address="off2@example.org", enabled=False, is_default=True
    )
    assert _code(accounts.save_account, "test", wants_default) == (
        "email.account_disabled"
    )
    assert _code(accounts.set_default, "test", "missing") == "email.account_not_found"

    saved = accounts.save_account(
        "test", account_payload(email_address="new@example.org", is_default=True)
    )
    assert saved["is_default"] is True
    assert accounts.get_account_settings("test", second)["is_default"] is False

    # Deleting the default promotes the oldest enabled mailbox.
    assert accounts.delete_account("test", saved["account_id"]) == {
        "account_id": saved["account_id"],
        "deleted": True,
    }
    defaults = {
        a["account_id"] for a in accounts.list_accounts("test") if a["is_default"]
    }
    assert defaults == {first}
    assert (
        _code(accounts.delete_account, "test", "missing") == "email.account_not_found"
    )


def test_selecting_a_mailbox(accounts: EmailAccounts) -> None:
    assert _code(accounts._selected_account, "test", None) == "email.account_required"  # noqa: SLF001
    off = add_account(accounts, enabled=False)
    assert _code(accounts._selected_account, "test", None) == "email.account_required"  # noqa: SLF001
    assert _code(accounts._selected_account, "test", off) == "email.account_disabled"  # noqa: SLF001
    first = add_account(accounts, email_address="a@example.org")
    add_account(accounts, email_address="b@example.org")
    assert accounts._selected_account("test", None)["id"] == first  # noqa: SLF001
    assert accounts._selected_account("test", first)["id"] == first  # noqa: SLF001


@pytest.mark.parametrize("secret", ["not json", "[1, 2]"])
def test_unreadable_vault_entries_are_reported_without_leaking(
    accounts: EmailAccounts, secret: str
) -> None:
    accounts.vault.create_credential(
        name="broken",
        provider=accounts.ACCOUNT_PROVIDER,
        scopes=[],
        secret=secret,
        actor="test",
        workspace_id=accounts.workspace_id,
    )
    with pytest.raises(ServiceError) as raised:
        accounts.list_accounts("test")
    assert raised.value.code == "email.credential_unavailable"
    assert secret not in raised.value.message


def test_workspace_must_be_a_uuid() -> None:
    with pytest.raises(ServiceError) as raised:
        EmailAccounts(new_accounts().vault, "not-a-uuid")
    assert raised.value.code == "email.workspace_unavailable"


def test_credential_use_is_audited_and_audit_failure_never_blocks_mail(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = accounts._row("test", add_account(accounts))  # noqa: SLF001
    events: list[WorkspaceAuditEvent] = []

    class Repository:
        def record_audit_event(self, event: WorkspaceAuditEvent) -> None:
            events.append(event)

    monkeypatch.setattr(
        workspace_module, "get_workspace_repository", lambda: Repository()
    )
    settings = accounts._credentials(row, "imap")  # noqa: SLF001
    assert settings["password"] == ACCOUNT_SECRET
    [event] = events
    assert event.action == "vault.email_account_used"
    assert event.resource_id == row["id"]
    assert event.details == {"protocol": "imap"}

    def broken() -> None:
        raise RuntimeError("audit store down")

    monkeypatch.setattr(workspace_module, "get_workspace_repository", broken)
    assert accounts._credentials(row, "smtp")["username"] == "me"  # noqa: SLF001


# -- IMAP connections ----------------------------------------------------------


def _factory(*steps: Any) -> Any:
    """A _PinnedIMAP/_PinnedSMTP stand-in that plays one step per address."""
    remaining = list(steps)
    addresses: list[str] = []

    def build(host: str, port: int, address: str, use_ssl: bool) -> Any:
        addresses.append(address)
        step = remaining.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    build.addresses = addresses  # type: ignore[attr-defined]
    return build


def _row(accounts: EmailAccounts, **overrides: Any) -> dict[str, Any]:
    return accounts._row("test", add_account(accounts, **overrides))  # noqa: SLF001


def test_imap_uses_the_pinned_address_and_logs_in(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    imap = FakeIMAP()
    install_servers(monkeypatch, imap)
    assert accounts._imap(_row(accounts)) is imap  # noqa: SLF001
    assert ("login", "me", ACCOUNT_SECRET) in imap.commands
    assert imap.starttls_calls == 0


def test_imap_starttls_and_failover_to_the_next_address(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = FakeIMAP()
    broken.starttls_error = imaplib.IMAP4.abort("dropped")
    broken.logout_error = OSError("already closed")
    working = FakeIMAP()
    factory = _factory(broken, OSError("refused"), working)
    install_servers(monkeypatch, ips=["1.1.1.1", "2.2.2.2", "3.3.3.3"])
    monkeypatch.setattr(email_client, "_PinnedIMAP", factory)
    row = _row(accounts)
    row["_config"]["imap"]["security"] = "starttls"

    assert accounts._imap(row) is working  # noqa: SLF001
    assert factory.addresses == ["1.1.1.1", "2.2.2.2", "3.3.3.3"]
    assert broken.logouts == 1
    assert working.starttls_calls == 1


def test_imap_protocol_errors_are_reported_without_the_server_text(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _row(accounts)
    install_servers(monkeypatch)
    row["_config"]["imap"]["security"] = "starttls"

    refused = FakeIMAP()
    refused.starttls_error = imaplib.IMAP4.error("STARTTLS refused: secret detail")
    refused.logout_error = imaplib.IMAP4.error("bye")
    monkeypatch.setattr(email_client, "_PinnedIMAP", _factory(refused))
    with pytest.raises(ServiceError) as raised:
        accounts._imap(row)  # noqa: SLF001
    assert raised.value.code == "email.imap_failed"
    assert "secret detail" not in raised.value.message

    bad_login = FakeIMAP()
    bad_login.login_error = imaplib.IMAP4.error("AUTHENTICATIONFAILED")
    bad_login.logout_error = OSError("closed")
    monkeypatch.setattr(email_client, "_PinnedIMAP", _factory(bad_login))
    assert _code(accounts._imap, row) == "email.imap_failed"  # noqa: SLF001
    assert bad_login.logouts == 1


def test_imap_gives_up_after_every_address_fails(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _row(accounts)
    install_servers(monkeypatch, ips=["1.1.1.1", "2.2.2.2"])
    dropped = FakeIMAP()
    dropped.login_error = ssl.SSLError("handshake")
    dropped.logout_error = OSError("closed")
    monkeypatch.setattr(
        email_client, "_PinnedIMAP", _factory(dropped, socket.timeout("slow"))
    )
    with pytest.raises(ServiceError) as raised:
        accounts._imap(row)  # noqa: SLF001
    assert raised.value.code == "email.imap_failed"
    assert isinstance(raised.value.__cause__, socket.timeout)


# -- SMTP connections ----------------------------------------------------------


def test_smtp_ssl_and_starttls_connections(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    smtp = FakeSMTP()
    install_servers(monkeypatch, smtp=smtp)
    row = _row(accounts)
    assert accounts._smtp(row) is smtp  # noqa: SLF001
    assert smtp.calls == ["login"]

    row["_config"]["smtp"]["security"] = "starttls"
    starttls = FakeSMTP()
    monkeypatch.setattr(email_client, "_PinnedSMTP", _factory(starttls))
    assert accounts._smtp(row) is starttls  # noqa: SLF001
    assert starttls.calls == ["ehlo", "starttls", "ehlo", "login"]


def test_smtp_disconnects_fail_over_and_rejections_stop(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _row(accounts)
    install_servers(monkeypatch, ips=["1.1.1.1", "2.2.2.2", "3.3.3.3"])
    row["_config"]["smtp"]["security"] = "starttls"
    dropped = FakeSMTP(
        starttls_error=smtplib.SMTPServerDisconnected("gone"),
        quit_error=smtplib.SMTPServerDisconnected("gone"),
    )
    login_lost = FakeSMTP(login_error=OSError("reset"), quit_error=OSError("reset"))
    working = FakeSMTP()
    factory = _factory(dropped, login_lost, working)
    monkeypatch.setattr(email_client, "_PinnedSMTP", factory)
    assert accounts._smtp(row) is working  # noqa: SLF001
    assert factory.addresses == ["1.1.1.1", "2.2.2.2", "3.3.3.3"]

    for failing in (
        FakeSMTP(starttls_error=smtplib.SMTPNotSupportedError("no STARTTLS")),
        FakeSMTP(login_error=smtplib.SMTPAuthenticationError(535, b"bad password")),
    ):
        failing.quit_error = OSError("closed")
        monkeypatch.setattr(email_client, "_PinnedSMTP", _factory(failing))
        with pytest.raises(ServiceError) as raised:
            accounts._smtp(row)  # noqa: SLF001
        assert raised.value.code == "email.smtp_failed"
        assert "bad password" not in raised.value.message

    install_servers(monkeypatch, ips=["1.1.1.1"])
    monkeypatch.setattr(email_client, "_PinnedSMTP", _factory(OSError("refused")))
    with pytest.raises(ServiceError) as raised:
        accounts._smtp(row)  # noqa: SLF001
    assert raised.value.code == "email.smtp_failed"
    assert isinstance(raised.value.__cause__, OSError)


# -- connection tests ----------------------------------------------------------


def test_connection_test_records_each_protocols_status(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    account_id = add_account(accounts)
    imap, smtp = FakeIMAP(), FakeSMTP()
    install_servers(monkeypatch, imap, smtp)

    result = accounts.test_account("test", account_id, "both")
    assert result["connections"] == {
        "imap": {"status": "connected", "message": "Connection successful."},
        "smtp": {"status": "connected", "message": "Connection successful."},
    }
    settings = accounts.get_account_settings("test", account_id)
    assert settings["imap"]["status"] == settings["smtp"]["status"] == "connected"
    assert settings["imap"]["tested_at"]

    imap.login_error = imaplib.IMAP4.error("denied")
    only_imap = accounts.test_account("test", account_id, "imap")
    assert list(only_imap["connections"]) == ["imap"]
    assert only_imap["connections"]["imap"]["status"] == "failed"
    assert accounts.get_account_settings("test", account_id)["smtp"]["status"] == (
        "connected"
    )

    smtp.login_error = smtplib.SMTPAuthenticationError(535, b"denied")
    only_smtp = accounts.test_account("test", account_id, "smtp")
    assert only_smtp["connections"]["smtp"]["status"] == "failed"

    off = add_account(accounts, email_address="off@example.org", enabled=False)
    assert _code(accounts.test_account, "test", off, "both") == "email.account_disabled"


def test_connection_test_survives_a_server_that_hangs_up_on_logout(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    account_id = add_account(accounts)
    imap = FakeIMAP()
    imap.logout_error = OSError("closed")
    install_servers(monkeypatch, imap)
    result = accounts.test_account("test", account_id, "imap")
    assert result["connections"]["imap"]["status"] == "connected"


# -- pinned sockets ------------------------------------------------------------


class _Sock:
    def __init__(self) -> None:
        self.wrapped_for: str | None = None


def test_pinned_connections_dial_the_vetted_address_not_the_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialed: list[tuple[str, int, float | None]] = []

    def create_connection(address: tuple[str, int], timeout: float | None) -> _Sock:
        dialed.append((*address, timeout))
        return _Sock()

    class Context:
        def wrap_socket(self, sock: _Sock, server_hostname: str) -> _Sock:
            sock.wrapped_for = server_hostname
            return sock

    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(ssl, "create_default_context", lambda: Context())
    monkeypatch.setattr(imaplib.IMAP4, "__init__", lambda self, **kwargs: None)
    monkeypatch.setattr(smtplib.SMTP, "__init__", lambda self, **kwargs: None)

    secure = email_client._PinnedIMAP("imap.example.org", 993, "1.2.3.4", True)  # noqa: SLF001
    secure.port = 993
    secure.host = "imap.example.org"
    wrapped = secure._create_socket(15)  # noqa: SLF001
    assert wrapped.wrapped_for == "imap.example.org"
    plain = email_client._PinnedIMAP("imap.example.org", 143, "1.2.3.4", False)  # noqa: SLF001
    plain.port = 143
    assert plain._create_socket(15).wrapped_for is None  # noqa: SLF001

    smtp = email_client._PinnedSMTP("smtp.example.org", 465, "5.6.7.8", True)  # noqa: SLF001
    assert smtp._get_socket("smtp.example.org", 465, 15).wrapped_for == (  # noqa: SLF001
        "smtp.example.org"
    )
    plain_smtp = email_client._PinnedSMTP("smtp.example.org", 25, "5.6.7.8", False)  # noqa: SLF001
    assert plain_smtp._get_socket("smtp.example.org", 25, 15).wrapped_for is None  # noqa: SLF001
    assert dialed == [
        ("1.2.3.4", 993, 15),
        ("1.2.3.4", 143, 15),
        ("5.6.7.8", 465, 15),
        ("5.6.7.8", 25, 15),
    ]


# -- connection tests racing with edits ------------------------------------------


def test_a_slow_connection_test_does_not_revert_a_save_made_meanwhile(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    account_id = add_account(accounts)
    imap, smtp = FakeIMAP(), FakeSMTP()
    install_servers(monkeypatch, imap, smtp)
    original_login = imap.login

    def login_then_edit(username: str, password: str) -> tuple[str, list]:
        result = original_login(username, password)
        # While the IMAP connection is open, the user saves new settings.
        edited = account_payload(account_id=account_id, display_name="Renamed")
        edited["imap"]["password"] = "rotated-imap-secret"  # noqa: S105
        accounts.save_account("test", edited)
        return result

    imap.login = login_then_edit  # type: ignore[method-assign]
    result = accounts.test_account("test", account_id, "both")

    assert result["connections"]["imap"]["status"] == "connected"
    row = accounts._row("test", account_id)  # noqa: SLF001
    assert row["display_name"] == "Renamed"
    assert row["imap_secret"] == "rotated-imap-secret"  # noqa: S105
    # The outcomes are recorded on the edited settings, not a stale copy.
    assert row["imap_status"] == "connected"
    assert row["smtp_status"] == "connected"


def test_a_mailbox_removed_during_a_connection_test_is_reported_cleanly(
    accounts: EmailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    account_id = add_account(accounts)
    imap = FakeIMAP()
    install_servers(monkeypatch, imap, FakeSMTP())
    original_login = imap.login

    def login_then_delete(username: str, password: str) -> tuple[str, list]:
        result = original_login(username, password)
        accounts.delete_account("test", account_id)
        return result

    imap.login = login_then_delete  # type: ignore[method-assign]
    assert _code(accounts.test_account, "test", account_id, "imap") == (
        "email.account_not_found"
    )
