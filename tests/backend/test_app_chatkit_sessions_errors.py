"""Error-handling tests for ChatKit session endpoint."""

from __future__ import annotations
from datetime import datetime
from typing import Any
from uuid import uuid4
import pytest
from fastapi import HTTPException
from orcheo.models import CredentialHealthStatus
from orcheo.vault.oauth import (
    CredentialHealthError,
    CredentialHealthReport,
    CredentialHealthResult,
)
from orcheo_backend.app import create_chatkit_session_endpoint
from orcheo_backend.app.authentication import AuthorizationPolicy, RequestContext
from orcheo_backend.app.chatkit_tokens import (
    ChatKitSessionTokenIssuer,
    ChatKitTokenConfigurationError,
    ChatKitTokenSettings,
)
from orcheo_backend.app.schemas.chatkit import ChatKitSessionRequest


@pytest.mark.asyncio()
async def test_create_chatkit_session_endpoint_missing_secret() -> None:
    """ChatKit session issuance raises a 503 when configuration is missing."""

    policy = AuthorizationPolicy(
        RequestContext(
            subject="tester",
            identity_type="user",
            scopes=frozenset({"chatkit:session"}),
            workspace_ids=frozenset({"ws-1"}),
        )
    )

    class FailingIssuer:
        def mint_session(self, **_: Any) -> tuple[str, datetime]:
            raise ChatKitTokenConfigurationError("ChatKit not configured")

    request = ChatKitSessionRequest(workflow_id=None)
    with pytest.raises(HTTPException) as exc_info:
        await create_chatkit_session_endpoint(
            request, policy=policy, issuer=FailingIssuer()
        )

    assert exc_info.value.status_code == 503
    assert "ChatKit not configured" in exc_info.value.detail["message"]


@pytest.mark.asyncio()
async def test_create_chatkit_session_endpoint_credential_health_error() -> None:
    """ChatKit session endpoint maps credential health failures to HTTP 503."""

    workflow_id = uuid4()
    report = CredentialHealthReport(
        workflow_id=workflow_id,
        results=[
            CredentialHealthResult(
                credential_id=uuid4(),
                name="slack",
                provider="slack",
                status=CredentialHealthStatus.UNHEALTHY,
                last_checked_at=datetime.now(),
                failure_reason="token expired",
            )
        ],
        checked_at=datetime.now(),
    )

    class UnhealthyIssuer:
        def mint_session(self, **_: Any) -> tuple[str, datetime]:
            raise CredentialHealthError(report)

    policy = AuthorizationPolicy(
        RequestContext(
            subject="tester",
            identity_type="user",
            scopes=frozenset({"chatkit:session"}),
            workspace_ids=frozenset({"ws-1"}),
        )
    )
    request = ChatKitSessionRequest(workflow_id=None)

    with pytest.raises(HTTPException) as exc_info:
        await create_chatkit_session_endpoint(
            request, policy=policy, issuer=UnhealthyIssuer()
        )

    assert exc_info.value.status_code == 503
    assert "unhealthy credentials" in exc_info.value.detail["message"].lower()


@pytest.mark.asyncio()
async def test_create_chatkit_session_endpoint_authentication_errors() -> None:
    """ChatKit session endpoint handles authentication errors properly."""

    issuer = ChatKitSessionTokenIssuer(
        ChatKitTokenSettings(
            signing_key="test-key",
            issuer="test-issuer",
            audience="test-audience",
            ttl_seconds=120,
        )
    )

    policy = AuthorizationPolicy(
        RequestContext(
            subject="",
            identity_type="anonymous",
            scopes=frozenset(),
            workspace_ids=frozenset(),
        )
    )
    request = ChatKitSessionRequest(workflow_id=None)

    with pytest.raises(HTTPException) as exc_info:
        await create_chatkit_session_endpoint(request, policy=policy, issuer=issuer)

    assert exc_info.value.status_code in (401, 403)


@pytest.mark.asyncio()
async def test_create_chatkit_session_endpoint_workspace_error() -> None:
    """ChatKit session endpoint handles workspace authorization errors."""

    policy = AuthorizationPolicy(
        RequestContext(
            subject="test-user",
            identity_type="user",
            scopes=frozenset({"chatkit:session"}),
            workspace_ids=frozenset({"ws-allowed"}),
        )
    )

    issuer = ChatKitSessionTokenIssuer(
        ChatKitTokenSettings(
            signing_key="test-key",
            issuer="test-issuer",
            audience="test-audience",
            ttl_seconds=120,
        )
    )

    request = ChatKitSessionRequest(
        workflow_id=None,
        metadata={"workspace_id": "ws-different"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_chatkit_session_endpoint(request, policy=policy, issuer=issuer)

    assert exc_info.value.status_code in (401, 403)


@pytest.mark.asyncio()
async def test_create_chatkit_session_endpoint_requires_workspace_selection() -> None:
    """ChatKit session endpoint rejects ambiguous multi-workspace sessions."""

    policy = AuthorizationPolicy(
        RequestContext(
            subject="test-user",
            identity_type="user",
            scopes=frozenset({"chatkit:session"}),
            workspace_ids=frozenset({"ws-allowed", "ws-other"}),
        )
    )

    issuer = ChatKitSessionTokenIssuer(
        ChatKitTokenSettings(
            signing_key="test-key",
            issuer="test-issuer",
            audience="test-audience",
            ttl_seconds=120,
        )
    )

    request = ChatKitSessionRequest(workflow_id=None, metadata={})

    with pytest.raises(HTTPException) as exc_info:
        await create_chatkit_session_endpoint(request, policy=policy, issuer=issuer)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "chatkit.auth.workspace_required"
