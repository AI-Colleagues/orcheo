"""Tests for the email-based workspace invitation flow."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
import pytest
from orcheo.workspace import (
    InMemoryWorkspaceRepository,
    InvitationStatus,
    Role,
    WorkspaceInvitationEmailMismatchError,
    WorkspaceInvitationError,
    WorkspaceInvitationExpiredError,
    WorkspaceInvitationNotFoundError,
    WorkspaceMembershipLimitError,
    WorkspacePermissionError,
    WorkspaceService,
)
from orcheo.workspace.email import InvitationEmail


class _FakeSender:
    """Capture invitation emails instead of delivering them."""

    def __init__(self) -> None:
        self.sent: list[InvitationEmail] = []

    def send_invitation(self, email: InvitationEmail) -> None:
        self.sent.append(email)


def _service(sender: _FakeSender | None = None) -> WorkspaceService:
    return WorkspaceService(
        InMemoryWorkspaceRepository(),
        email_sender=sender or _FakeSender(),
        invitation_base_url="https://studio.example.com",
        invitation_ttl_hours=72,
    )


def _token_from_url(url: str) -> str:
    return url.split("token=", 1)[1]


def test_create_invitation_sends_email_and_persists_pending() -> None:
    sender = _FakeSender()
    svc = _service(sender)
    workspace, _ = svc.create_workspace(
        slug="acme", name="Acme", owner_user_id="auth0|owner"
    )

    invitation = svc.create_invitation(
        workspace_id=workspace.id,
        email="Newbie@Example.com",
        role=Role.EDITOR,
        invited_by="auth0|owner",
        actor_role=Role.OWNER,
        workspace_name="Acme",
    )

    assert invitation.email == "newbie@example.com"
    assert invitation.status is InvitationStatus.PENDING
    assert invitation.token_hash and "newbie" not in invitation.token_hash
    assert len(sender.sent) == 1
    email = sender.sent[0]
    assert email.to == "newbie@example.com"
    assert email.workspace_name == "Acme"
    assert "token=" in email.accept_url
    assert email.accept_url.startswith("https://studio.example.com/")


def test_create_invitation_requires_admin() -> None:
    svc = _service()
    workspace, _ = svc.create_workspace(
        slug="acme", name="Acme", owner_user_id="auth0|owner"
    )
    with pytest.raises(WorkspacePermissionError):
        svc.create_invitation(
            workspace_id=workspace.id,
            email="x@example.com",
            role=Role.EDITOR,
            actor_role=Role.EDITOR,
        )


def test_duplicate_pending_invitation_conflicts() -> None:
    svc = _service()
    workspace, _ = svc.create_workspace(
        slug="acme", name="Acme", owner_user_id="auth0|owner"
    )
    svc.create_invitation(
        workspace_id=workspace.id,
        email="dup@example.com",
        role=Role.EDITOR,
        actor_role=Role.OWNER,
    )
    with pytest.raises(WorkspaceInvitationError):
        svc.create_invitation(
            workspace_id=workspace.id,
            email="dup@example.com",
            role=Role.VIEWER,
            actor_role=Role.OWNER,
        )


def test_accept_invitation_creates_membership_bound_to_subject() -> None:
    sender = _FakeSender()
    svc = _service(sender)
    workspace, _ = svc.create_workspace(
        slug="acme", name="Acme", owner_user_id="auth0|owner"
    )
    svc.create_invitation(
        workspace_id=workspace.id,
        email="newbie@example.com",
        role=Role.EDITOR,
        actor_role=Role.OWNER,
    )
    token = _token_from_url(sender.sent[0].accept_url)

    membership = svc.accept_invitation(
        raw_token=token,
        user_id="auth0|newbie-sub",
        email="newbie@example.com",
        email_verified=True,
    )

    assert membership.user_id == "auth0|newbie-sub"
    assert membership.role is Role.EDITOR
    assert membership.email == "newbie@example.com"
    invitations = svc.list_invitations(workspace.id)
    assert invitations[0].status is InvitationStatus.ACCEPTED
    assert invitations[0].accepted_by == "auth0|newbie-sub"
    # The resolver now sees the new member in the workspace.
    assert any(
        m.workspace_id == workspace.id
        for m in svc.resolver.list_memberships("auth0|newbie-sub")
    )


def test_accept_invitation_preserves_membership_limit_error() -> None:
    sender = _FakeSender()
    svc = _service(sender)
    for index in range(3):
        svc.create_workspace(
            slug=f"existing-{index}",
            name=f"Existing {index}",
            owner_user_id="auth0|newbie",
        )
    workspace, _ = svc.create_workspace(
        slug="acme", name="Acme", owner_user_id="auth0|owner"
    )
    svc.create_invitation(
        workspace_id=workspace.id,
        email="newbie@example.com",
        role=Role.EDITOR,
        actor_role=Role.OWNER,
    )
    token = _token_from_url(sender.sent[0].accept_url)

    with pytest.raises(WorkspaceMembershipLimitError):
        svc.accept_invitation(
            raw_token=token,
            user_id="auth0|newbie",
            email="newbie@example.com",
            email_verified=True,
        )


def test_accept_requires_verified_matching_email() -> None:
    sender = _FakeSender()
    svc = _service(sender)
    workspace, _ = svc.create_workspace(
        slug="acme", name="Acme", owner_user_id="auth0|owner"
    )
    svc.create_invitation(
        workspace_id=workspace.id,
        email="newbie@example.com",
        role=Role.EDITOR,
        actor_role=Role.OWNER,
    )
    token = _token_from_url(sender.sent[0].accept_url)

    # Wrong email.
    with pytest.raises(WorkspaceInvitationEmailMismatchError):
        svc.accept_invitation(
            raw_token=token,
            user_id="auth0|x",
            email="other@example.com",
            email_verified=True,
        )
    # Right email but unverified.
    with pytest.raises(WorkspaceInvitationEmailMismatchError):
        svc.accept_invitation(
            raw_token=token,
            user_id="auth0|x",
            email="newbie@example.com",
            email_verified=False,
        )


def test_accept_is_idempotent_for_same_subject() -> None:
    sender = _FakeSender()
    svc = _service(sender)
    workspace, _ = svc.create_workspace(
        slug="acme", name="Acme", owner_user_id="auth0|owner"
    )
    svc.create_invitation(
        workspace_id=workspace.id,
        email="newbie@example.com",
        role=Role.EDITOR,
        actor_role=Role.OWNER,
    )
    token = _token_from_url(sender.sent[0].accept_url)
    kwargs = dict(
        raw_token=token,
        user_id="auth0|newbie",
        email="newbie@example.com",
        email_verified=True,
    )
    first = svc.accept_invitation(**kwargs)
    second = svc.accept_invitation(**kwargs)
    assert first.id == second.id
    # A different subject cannot claim an already-accepted invite.
    with pytest.raises(WorkspaceInvitationError):
        svc.accept_invitation(
            raw_token=token,
            user_id="auth0|intruder",
            email="newbie@example.com",
            email_verified=True,
        )


def test_accept_rejects_expired_invitation() -> None:
    sender = _FakeSender()
    svc = WorkspaceService(
        InMemoryWorkspaceRepository(),
        email_sender=sender,
        invitation_base_url="https://studio.example.com",
        invitation_ttl_hours=72,
    )
    workspace, _ = svc.create_workspace(
        slug="acme", name="Acme", owner_user_id="auth0|owner"
    )
    invitation = svc.create_invitation(
        workspace_id=workspace.id,
        email="late@example.com",
        role=Role.EDITOR,
        actor_role=Role.OWNER,
    )
    # Force expiry into the past.
    expired = invitation.model_copy(
        update={"expires_at": datetime.now(tz=UTC) - timedelta(hours=1)}
    )
    svc.repository.update_invitation(expired)
    token = _token_from_url(sender.sent[0].accept_url)

    with pytest.raises(WorkspaceInvitationExpiredError):
        svc.accept_invitation(
            raw_token=token,
            user_id="auth0|late",
            email="late@example.com",
            email_verified=True,
        )


def test_revoke_then_accept_is_rejected() -> None:
    sender = _FakeSender()
    svc = _service(sender)
    workspace, _ = svc.create_workspace(
        slug="acme", name="Acme", owner_user_id="auth0|owner"
    )
    invitation = svc.create_invitation(
        workspace_id=workspace.id,
        email="nope@example.com",
        role=Role.EDITOR,
        actor_role=Role.OWNER,
    )
    revoked = svc.revoke_invitation(
        workspace_id=workspace.id,
        invitation_id=invitation.id,
        actor_role=Role.OWNER,
        actor="auth0|owner",
    )
    assert revoked.status is InvitationStatus.REVOKED
    token = _token_from_url(sender.sent[0].accept_url)
    with pytest.raises(WorkspaceInvitationError):
        svc.accept_invitation(
            raw_token=token,
            user_id="auth0|nope",
            email="nope@example.com",
            email_verified=True,
        )
    # Revoking again is a conflict.
    with pytest.raises(WorkspaceInvitationError):
        svc.revoke_invitation(
            workspace_id=workspace.id,
            invitation_id=invitation.id,
            actor_role=Role.OWNER,
        )


def test_accept_unknown_token_raises_not_found() -> None:
    svc = _service()
    with pytest.raises(WorkspaceInvitationNotFoundError):
        svc.accept_invitation(
            raw_token="does-not-exist",
            user_id="auth0|x",
            email="x@example.com",
            email_verified=True,
        )
