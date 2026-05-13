"""Positive ChatKit session endpoint behaviours."""

from __future__ import annotations
import jwt
import pytest
from orcheo_backend.app import create_chatkit_session_endpoint
from orcheo_backend.app.authentication import AuthorizationPolicy, RequestContext
from orcheo_backend.app.chatkit_tokens import (
    ChatKitSessionTokenIssuer,
    ChatKitTokenSettings,
)
from orcheo_backend.app.schemas.chatkit import ChatKitSessionRequest


@pytest.mark.asyncio()
async def test_create_chatkit_session_endpoint_returns_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ChatKit session endpoint returns a signed token for the caller."""

    monkeypatch.setenv("CHATKIT_TOKEN_SIGNING_KEY", "test-signing-key")

    policy = AuthorizationPolicy(
        RequestContext(
            subject="tester",
            identity_type="user",
            scopes=frozenset({"chatkit:session"}),
            workspace_ids=frozenset({"ws-1"}),
        )
    )
    issuer = ChatKitSessionTokenIssuer(
        ChatKitTokenSettings(
            signing_key="test-signing-key",
            issuer="test-issuer",
            audience="chatkit-client",
            ttl_seconds=120,
        )
    )
    request = ChatKitSessionRequest(workflow_id=None, metadata={})
    response = await create_chatkit_session_endpoint(
        request, policy=policy, issuer=issuer
    )

    decoded = jwt.decode(
        response.client_secret,
        "test-signing-key",
        algorithms=["HS256"],
        audience="chatkit-client",
        issuer="test-issuer",
    )
    assert decoded["sub"] == "tester"
    assert decoded["chatkit"]["workspace_id"] == "ws-1"
    assert decoded["chatkit"]["workflow_id"] is None


@pytest.mark.asyncio()
async def test_create_chatkit_session_endpoint_workflow_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow-specific metadata should appear in the signed token."""

    monkeypatch.setenv("CHATKIT_TOKEN_SIGNING_KEY", "workflow-signing-key")

    policy = AuthorizationPolicy(
        RequestContext(
            subject="tester",
            identity_type="user",
            scopes=frozenset({"chatkit:session"}),
            workspace_ids=frozenset({"ws-2"}),
        )
    )
    issuer = ChatKitSessionTokenIssuer(
        ChatKitTokenSettings(
            signing_key="workflow-signing-key",
            issuer="workflow-issuer",
            audience="workflow-client",
            ttl_seconds=60,
        )
    )
    request = ChatKitSessionRequest(
        workflow_id=None,
        workflow_label="demo-workflow",
        metadata={"channel": "alpha"},
    )
    response = await create_chatkit_session_endpoint(
        request, policy=policy, issuer=issuer
    )

    decoded = jwt.decode(
        response.client_secret,
        "workflow-signing-key",
        algorithms=["HS256"],
        audience="workflow-client",
        issuer="workflow-issuer",
    )
    assert decoded["chatkit"]["workflow_label"] == "demo-workflow"
    assert decoded["chatkit"]["metadata"]["channel"] == "alpha"


@pytest.mark.asyncio()
async def test_create_chatkit_session_endpoint_requires_explicit_workspace_when_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller with multiple workspaces must choose one explicitly."""

    monkeypatch.setenv("CHATKIT_TOKEN_SIGNING_KEY", "ambiguous-signing-key")

    policy = AuthorizationPolicy(
        RequestContext(
            subject="tester",
            identity_type="user",
            scopes=frozenset({"chatkit:session"}),
            workspace_ids=frozenset({"ws-1", "ws-2"}),
        )
    )
    issuer = ChatKitSessionTokenIssuer(
        ChatKitTokenSettings(
            signing_key="ambiguous-signing-key",
            issuer="ambiguous-issuer",
            audience="ambiguous-client",
            ttl_seconds=120,
        )
    )
    request = ChatKitSessionRequest(
        workflow_id=None,
        metadata={"workspace_id": "ws-2"},
    )

    response = await create_chatkit_session_endpoint(
        request, policy=policy, issuer=issuer
    )

    decoded = jwt.decode(
        response.client_secret,
        "ambiguous-signing-key",
        algorithms=["HS256"],
        audience="ambiguous-client",
        issuer="ambiguous-issuer",
    )
    assert decoded["chatkit"]["workspace_id"] == "ws-2"


@pytest.mark.asyncio()
async def test_create_chatkit_session_endpoint_with_current_client_secret() -> None:
    """ChatKit session endpoint includes previous secret in extra payload."""

    policy = AuthorizationPolicy(
        RequestContext(
            subject="test-user",
            identity_type="user",
            scopes=frozenset({"chatkit:session"}),
            workspace_ids=frozenset({"ws-1"}),
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
        metadata={},
        current_client_secret="old-secret-token",
    )

    response = await create_chatkit_session_endpoint(
        request, policy=policy, issuer=issuer
    )

    decoded = jwt.decode(
        response.client_secret,
        "test-key",
        algorithms=["HS256"],
        audience="test-audience",
        issuer="test-issuer",
    )
    assert "previous_secret" in decoded["chatkit"]
    assert decoded["chatkit"]["previous_secret"] == "old-secret-token"
