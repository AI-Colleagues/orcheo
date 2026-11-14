"""The Celery Beat task that sends scheduled MCP emails."""

from __future__ import annotations
import importlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4
import pytest
from orcheo.vault import InMemoryCredentialVault
from orcheo_backend.app.mcp_server.email_client import EmailAccounts, SendEmail
from orcheo_backend.worker.tasks import dispatch_scheduled_emails
from tests.backend.mcp_email_support import (
    FakeIMAP,
    FakeSMTP,
    add_account,
    install_servers,
)


# ``orcheo_backend.app`` names the FastAPI instance, so resolve the module directly.
dependencies = importlib.import_module("orcheo_backend.app.dependencies")


def _store(
    vault: InMemoryCredentialVault, workspace: str | None, provider: str, **kw: Any
) -> None:
    vault.create_credential(
        name=f"{provider}-{uuid4().hex}",
        provider=provider,
        scopes=[],
        secret=json.dumps({"status": "scheduled"}),
        actor="test",
        workspace_id=workspace,
        **kw,
    )


def test_only_workspaces_with_email_records_are_processed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = InMemoryCredentialVault()
    with_schedule, with_claim, plain, templated = (str(uuid4()) for _ in range(4))
    _store(vault, with_schedule, EmailAccounts.SCHEDULE_PROVIDER)
    _store(vault, with_schedule, EmailAccounts.SCHEDULE_PROVIDER)
    _store(vault, with_claim, EmailAccounts.IDEMPOTENCY_PROVIDER)
    _store(vault, plain, EmailAccounts.ACCOUNT_PROVIDER)
    _store(vault, plain, "api")
    _store(vault, None, EmailAccounts.SCHEDULE_PROVIDER)
    _store(vault, templated, EmailAccounts.SCHEDULE_PROVIDER, template_id=uuid4())
    monkeypatch.setattr(dependencies, "get_vault", lambda: vault)

    cleaned: list[str] = []
    processed: list[tuple[str, str]] = []

    def cleanup(self: EmailAccounts) -> None:
        cleaned.append(self.workspace_id)

    def process(self: EmailAccounts, limit: int = 20) -> dict[str, int]:
        processed.append((self.workspace_id, self.actor))
        return {"sent": 2, "failed": 1, "recovered": 3}

    monkeypatch.setattr(EmailAccounts, "cleanup_idempotency", cleanup)
    monkeypatch.setattr(EmailAccounts, "process_scheduled", process)

    assert dispatch_scheduled_emails() == {"sent": 4, "failed": 2, "recovered": 6}
    expected = sorted([with_schedule, with_claim])
    assert cleaned == expected
    assert processed == [(workspace, "email-scheduler") for workspace in expected]


def test_the_task_sends_a_due_message_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = InMemoryCredentialVault()
    workspace = str(uuid4())
    accounts = EmailAccounts(vault, workspace)
    add_account(accounts)
    smtp = FakeSMTP()
    install_servers(
        monkeypatch, FakeIMAP({"Sent": []}, special={"Sent": "\\Sent"}), smtp
    )
    args = SendEmail(
        subject="Scheduled", to=["you@example.org"], body_text="Hello"
    ).model_dump(mode="python")
    accounts.schedule(
        "test", args, (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    )
    monkeypatch.setattr(dependencies, "get_vault", lambda: vault)

    assert dispatch_scheduled_emails() == {"sent": 1, "failed": 0, "recovered": 0}
    [(message, from_addr, _)] = smtp.sent
    assert message["Subject"] == "Scheduled"
    assert from_addr == "me@example.org"
    assert dispatch_scheduled_emails() == {"sent": 0, "failed": 0, "recovered": 0}


def test_an_empty_vault_has_nothing_to_do(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dependencies, "get_vault", lambda: InMemoryCredentialVault())
    assert dispatch_scheduled_emails() == {"sent": 0, "failed": 0, "recovered": 0}
