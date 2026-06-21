"""Tests for invitation email senders and the provider factory."""

from __future__ import annotations
from datetime import UTC, datetime
from typing import Any
import httpx
import pytest
from orcheo.workspace.email import (
    DEFAULT_INVITE_FROM_EMAIL,
    InvitationEmail,
    LoggingInvitationEmailSender,
    ResendInvitationEmailSender,
    build_invitation_email_sender,
)


def _email() -> InvitationEmail:
    return InvitationEmail(
        to="invitee@example.com",
        workspace_name="Acme",
        role="editor",
        accept_url="https://studio.example.com/invitations/accept?token=abc123",
        expires_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )


def test_factory_returns_logging_sender_without_key() -> None:
    assert isinstance(
        build_invitation_email_sender(api_key=None), LoggingInvitationEmailSender
    )
    assert isinstance(
        build_invitation_email_sender(api_key="   "), LoggingInvitationEmailSender
    )


def test_factory_returns_resend_sender_with_key() -> None:
    sender = build_invitation_email_sender(api_key="re_test", from_email="a@b.com")
    assert isinstance(sender, ResendInvitationEmailSender)
    assert sender._from_email == "a@b.com"
    # Blank from-email falls back to the default sender identity.
    fallback = build_invitation_email_sender(api_key="re_test", from_email="  ")
    assert fallback._from_email == DEFAULT_INVITE_FROM_EMAIL


def test_resend_sender_posts_expected_payload(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        captured["json"] = kwargs["json"]
        return httpx.Response(
            200, request=httpx.Request("POST", url), json={"id": "email_1"}
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    ResendInvitationEmailSender(
        api_key="re_test", from_email="Orcheo <i@orcheo.cloud>"
    ).send_invitation(_email())

    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Authorization"] == "Bearer re_test"
    body = captured["json"]
    assert body["from"] == "Orcheo <i@orcheo.cloud>"
    assert body["to"] == ["invitee@example.com"]
    assert "Acme" in body["subject"]
    assert "token=abc123" in body["html"]


def test_resend_sender_raises_on_error_response(monkeypatch) -> None:
    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            422, request=httpx.Request("POST", url), json={"message": "bad"}
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(httpx.HTTPStatusError):
        ResendInvitationEmailSender(api_key="re_test").send_invitation(_email())
