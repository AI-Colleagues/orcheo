"""Router-level tests for workspace invitation endpoints."""

from __future__ import annotations
import pytest
from orcheo.workspace import (
    InMemoryWorkspaceRepository,
    Role,
    WorkspaceContext,
    WorkspaceService,
)
from orcheo.workspace.email import InvitationEmail
from orcheo_backend.app.authentication import RequestContext
from orcheo_backend.app.routers import workspaces as workspaces_router
from orcheo_backend.app.routers.workspaces import (
    _request_bearer_token,
    _verified_email,
    accept_workspace_invitation,
    create_workspace_invitation,
    list_workspace_invitations,
    revoke_workspace_invitation,
)
from starlette.requests import Request
from orcheo_backend.app.schemas.workspaces import (
    InvitationAcceptRequest,
    InvitationCreateRequest,
)
from orcheo_backend.app.workspace import WorkspaceHTTPError


class _FakeSender:
    def __init__(self) -> None:
        self.sent: list[InvitationEmail] = []

    def send_invitation(self, email: InvitationEmail) -> None:
        self.sent.append(email)


@pytest.fixture
def setup():
    sender = _FakeSender()
    service = WorkspaceService(
        InMemoryWorkspaceRepository(),
        email_sender=sender,
        invitation_base_url="https://studio.example.com",
    )
    workspace, _ = service.create_workspace(
        slug="acme", name="Acme", owner_user_id="auth0|owner"
    )
    admin_ctx = WorkspaceContext(
        workspace_id=workspace.id,
        workspace_slug="acme",
        user_id="auth0|owner",
        role=Role.OWNER,
    )
    return sender, service, workspace, admin_ctx


def _auth(subject: str, **claims) -> RequestContext:
    return RequestContext(subject=subject, identity_type="user", claims=claims)


def test_create_list_and_accept_flow(setup) -> None:
    sender, service, workspace, admin_ctx = setup

    created = create_workspace_invitation(
        slug="acme",
        payload=InvitationCreateRequest(email="newbie@example.com", role=Role.EDITOR),
        service=service,
        context=admin_ctx,
        auth=_auth("auth0|owner"),
    )
    assert created.email == "newbie@example.com"
    assert created.status.value == "pending"

    listing = list_workspace_invitations(
        slug="acme", service=service, context=admin_ctx
    )
    assert len(listing.invitations) == 1

    token = sender.sent[0].accept_url.split("token=", 1)[1]
    result = accept_workspace_invitation(
        payload=InvitationAcceptRequest(token=token),
        service=service,
        auth=_auth(
            "auth0|newbie",
            email="newbie@example.com",
            email_verified=True,
        ),
    )
    assert result.slug == "acme"
    assert result.role is Role.EDITOR


def test_accept_with_mismatched_email_is_forbidden(setup) -> None:
    sender, service, workspace, admin_ctx = setup
    create_workspace_invitation(
        slug="acme",
        payload=InvitationCreateRequest(email="newbie@example.com", role=Role.EDITOR),
        service=service,
        context=admin_ctx,
        auth=_auth("auth0|owner"),
    )
    token = sender.sent[0].accept_url.split("token=", 1)[1]

    with pytest.raises(WorkspaceHTTPError) as exc:
        accept_workspace_invitation(
            payload=InvitationAcceptRequest(token=token),
            service=service,
            auth=_auth(
                "auth0|intruder",
                email="intruder@example.com",
                email_verified=True,
            ),
        )
    assert exc.value.status_code == 403


def test_accept_unknown_token_raises_not_found(setup) -> None:
    _, service, _, _ = setup
    with pytest.raises(WorkspaceHTTPError) as exc:
        accept_workspace_invitation(
            payload=InvitationAcceptRequest(token="missing"),
            service=service,
            auth=_auth("auth0|x", email="x@example.com", email_verified=True),
        )
    assert exc.value.status_code == 404


def test_revoke_invitation(setup) -> None:
    sender, service, workspace, admin_ctx = setup
    created = create_workspace_invitation(
        slug="acme",
        payload=InvitationCreateRequest(email="bye@example.com", role=Role.VIEWER),
        service=service,
        context=admin_ctx,
        auth=_auth("auth0|owner"),
    )
    revoked = revoke_workspace_invitation(
        slug="acme",
        invitation_id=created.id,
        service=service,
        context=admin_ctx,
        auth=_auth("auth0|owner"),
    )
    assert revoked.status.value == "revoked"


def _request_with_auth(header: str | None) -> Request:
    headers = [(b"authorization", header.encode())] if header is not None else []
    return Request({"type": "http", "headers": headers})


def test_request_bearer_token_parsing() -> None:
    assert _request_bearer_token(_request_with_auth("Bearer tok123")) == "tok123"
    assert _request_bearer_token(_request_with_auth("bearer tok123")) == "tok123"
    assert _request_bearer_token(_request_with_auth("Basic abc")) is None
    assert _request_bearer_token(_request_with_auth(None)) is None


def test_verified_email_prefers_token_claims() -> None:
    auth = _auth("auth0|x", email="claim@example.com", email_verified=True)
    # Claims win; no userinfo lookup attempted (access_token ignored).
    assert _verified_email(auth, access_token="tok") == ("claim@example.com", True)


def test_verified_email_falls_back_to_userinfo(monkeypatch) -> None:
    calls: list[str] = []

    def fake_userinfo(token: str) -> tuple[str, bool]:
        calls.append(token)
        return "userinfo@example.com", True

    monkeypatch.setattr(workspaces_router, "_fetch_userinfo_email", fake_userinfo)
    # Access token carries no email claim -> userinfo fallback is used.
    auth = _auth("auth0|x")
    assert _verified_email(auth, access_token="tok") == ("userinfo@example.com", True)
    assert calls == ["tok"]


def test_verified_email_rechecks_stale_unverified_claim(monkeypatch) -> None:
    # Token has the email but a stale email_verified=false; userinfo is live.
    monkeypatch.setattr(
        workspaces_router,
        "_fetch_userinfo_email",
        lambda token: ("claim@example.com", True),
    )
    auth = _auth("auth0|x", email="claim@example.com", email_verified=False)
    assert _verified_email(auth, access_token="tok") == ("claim@example.com", True)


def test_verified_email_stays_unverified_when_userinfo_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(workspaces_router, "_fetch_userinfo_email", lambda token: None)
    auth = _auth("auth0|x", email="claim@example.com", email_verified=False)
    assert _verified_email(auth, access_token="tok") == ("claim@example.com", False)


def test_accept_unverified_email_message(setup, monkeypatch) -> None:
    sender, service, workspace, admin_ctx = setup
    create_workspace_invitation(
        slug="acme",
        payload=InvitationCreateRequest(email="newbie@example.com", role=Role.EDITOR),
        service=service,
        context=admin_ctx,
        auth=_auth("auth0|owner"),
    )
    token = sender.sent[0].accept_url.split("token=", 1)[1]
    with pytest.raises(WorkspaceHTTPError) as exc:
        accept_workspace_invitation(
            payload=InvitationAcceptRequest(token=token),
            service=service,
            auth=_auth(
                "auth0|newbie",
                email="newbie@example.com",
                email_verified=False,
            ),
        )
    assert exc.value.status_code == 403
    assert "not verified" in str(exc.value.message)
