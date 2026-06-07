"""Direct unit tests for the credential_alerts router."""

from __future__ import annotations
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from orcheo.models import (
    CredentialAccessContext,
    CredentialScope,
    GovernanceAlertKind,
    SecretGovernanceAlert,
    SecretGovernanceAlertSeverity,
)
from orcheo.vault import GovernanceAlertNotFoundError, WorkflowScopeError
from orcheo_backend.app.routers.credential_alerts import (
    acknowledge_governance_alert,
    list_governance_alerts,
)
from orcheo_backend.app.schemas.governance import AlertAcknowledgeRequest


def _make_alert(workflow_id: UUID | None = None) -> SecretGovernanceAlert:
    return SecretGovernanceAlert.create(
        scope=CredentialScope(),
        kind=GovernanceAlertKind.VALIDATION_FAILED,
        severity=SecretGovernanceAlertSeverity.CRITICAL,
        message="test alert",
        actor="system",
        credential_id=uuid4() if workflow_id else None,
    )


class _StubVault:
    def __init__(
        self,
        alerts: list[SecretGovernanceAlert] | None = None,
        ack_result: SecretGovernanceAlert | None = None,
        ack_error: Exception | None = None,
    ) -> None:
        self._alerts = alerts or []
        self._ack_result = ack_result
        self._ack_error = ack_error

    def list_alerts(
        self, *, context: object = None, include_acknowledged: bool = False
    ) -> list[SecretGovernanceAlert]:
        return self._alerts

    def acknowledge_alert(
        self, alert_id: UUID, *, actor: str, context: object = None
    ) -> SecretGovernanceAlert:
        if self._ack_error is not None:
            raise self._ack_error
        assert self._ack_result is not None
        return self._ack_result


class _StubRepository:
    async def resolve_workflow_ref(
        self,
        ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ) -> UUID:
        return UUID(ref)


_MOCK_WORKSPACE = SimpleNamespace(workspace_id=uuid4())


@pytest.mark.asyncio()
async def test_list_governance_alerts_returns_converted_alerts() -> None:
    """list_governance_alerts calls vault and converts each alert."""
    alert = _make_alert()
    vault = _StubVault(alerts=[alert])

    result = await list_governance_alerts(
        vault=vault,  # type: ignore[arg-type]
        repository=_StubRepository(),  # type: ignore[arg-type]
        workspace=_MOCK_WORKSPACE,  # type: ignore[arg-type]
        workflow_id=None,
        include_acknowledged=False,
    )

    assert len(result) == 1
    assert result[0].id == str(alert.id)
    assert result[0].kind == GovernanceAlertKind.VALIDATION_FAILED


@pytest.mark.asyncio()
async def test_list_governance_alerts_empty_returns_empty_list() -> None:
    """An empty vault returns an empty list."""
    vault = _StubVault(alerts=[])

    result = await list_governance_alerts(
        vault=vault,  # type: ignore[arg-type]
        repository=_StubRepository(),  # type: ignore[arg-type]
        workspace=_MOCK_WORKSPACE,  # type: ignore[arg-type]
        workflow_id=None,
        include_acknowledged=False,
    )

    assert result == []


@pytest.mark.asyncio()
async def test_acknowledge_governance_alert_raises_403_on_scope_error() -> None:
    """WorkflowScopeError during acknowledgement is mapped to HTTP 403."""
    alert_id = uuid4()
    vault = _StubVault(
        ack_error=WorkflowScopeError("Scope violation"),
    )
    request = AlertAcknowledgeRequest(actor="tester")

    with pytest.raises(HTTPException) as exc_info:
        await acknowledge_governance_alert(
            alert_id=alert_id,
            request=request,
            vault=vault,  # type: ignore[arg-type]
            repository=_StubRepository(),  # type: ignore[arg-type]
            workspace=_MOCK_WORKSPACE,  # type: ignore[arg-type]
            workflow_id=None,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio()
async def test_acknowledge_governance_alert_raises_404_on_not_found() -> None:
    """GovernanceAlertNotFoundError during acknowledgement is mapped to HTTP 404."""
    alert_id = uuid4()
    vault = _StubVault(
        ack_error=GovernanceAlertNotFoundError("not found"),
    )
    request = AlertAcknowledgeRequest(actor="tester")

    with pytest.raises(HTTPException) as exc_info:
        await acknowledge_governance_alert(
            alert_id=alert_id,
            request=request,
            vault=vault,  # type: ignore[arg-type]
            repository=_StubRepository(),  # type: ignore[arg-type]
            workspace=_MOCK_WORKSPACE,  # type: ignore[arg-type]
            workflow_id=None,
        )

    assert exc_info.value.status_code == 404
