"""Tests for the sign-in email-domain allowlist in the identity service."""

from __future__ import annotations
from collections.abc import Iterator
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from orcheo.identity import (
    IdentityEmailDomainNotAllowedError,
    IdentitySessionNotFoundError,
    InMemoryIdentityRepository,
)
from orcheo.workspace.email import AuthChallengeEmail
from orcheo_backend.app.authentication import reset_authentication_state
from orcheo_backend.app.identity import dependencies
from orcheo_backend.app.identity.config import IdentityConfig
from orcheo_backend.app.identity.dependencies import (
    reset_identity_state,
    set_identity_service,
)
from orcheo_backend.app.identity.service import IdentityService
from tests.backend.authentication_test_utils import create_test_client


SECRET = "identity-domain-secret"  # noqa: S105 - test fixture
ISSUER = "https://auth.orcheo.test"


class CapturingSender:
    def __init__(self) -> None:
        self.sent: list[AuthChallengeEmail] = []

    def send_auth_challenge(self, email: AuthChallengeEmail) -> None:
        self.sent.append(email)


def _service(
    allowed: tuple[str, ...] = ("example.com",),
) -> tuple[IdentityService, CapturingSender, InMemoryIdentityRepository]:
    repo = InMemoryIdentityRepository()
    sender = CapturingSender()
    service = IdentityService(
        repo,
        email_sender=sender,
        config=IdentityConfig(
            jwt_secret=SECRET,
            issuer=ISSUER,
            audience="orcheo-api",
            allowed_email_domains=allowed,
        ),
    )
    return service, sender, repo


def _allow(service: IdentityService, domains: tuple[str, ...]) -> None:
    service._config = IdentityConfig(  # noqa: SLF001 - simulate a config change
        jwt_secret=SECRET,
        issuer=ISSUER,
        audience="orcheo-api",
        allowed_email_domains=domains,
    )


def test_start_challenge_rejects_disallowed_domain_without_sending() -> None:
    service, sender, _ = _service()

    with pytest.raises(IdentityEmailDomainNotAllowedError):
        service.start_challenge("mallory@other.org")

    assert sender.sent == []
    service.start_challenge("Alice@Example.com")
    assert sender.sent[-1].to == "alice@example.com"


def test_empty_allowlist_allows_every_domain() -> None:
    service, sender, _ = _service(allowed=())
    service.start_challenge("anyone@other.org")
    assert sender.sent[-1].to == "anyone@other.org"


def test_verification_rechecks_allowlist() -> None:
    service, sender, _ = _service()
    service.start_challenge("alice@example.com")
    _allow(service, ("company.com",))

    with pytest.raises(IdentityEmailDomainNotAllowedError):
        service.verify_code("alice@example.com", sender.sent[-1].otp_code)


def test_refresh_revokes_sessions_outside_allowlist() -> None:
    service, sender, _ = _service()
    service.start_challenge("alice@example.com")
    result = service.verify_code("alice@example.com", sender.sent[-1].otp_code)
    rotated = service.refresh(result.tokens.refresh_token)
    _allow(service, ("company.com",))

    with pytest.raises(IdentitySessionNotFoundError):
        service.refresh(rotated.refresh_token)
    _allow(service, ("example.com",))
    with pytest.raises(IdentitySessionNotFoundError):
        service.refresh(rotated.refresh_token)


def test_get_identity_config_parses_allowed_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dependencies,
        "load_auth_settings",
        lambda: SimpleNamespace(jwt_secret="secret", issuer=None, audiences=()),
    )
    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda: {"AUTH_ALLOWED_EMAIL_DOMAINS": "Example.com, @b.org"},
    )

    config = dependencies.get_identity_config()

    assert config.allowed_email_domains == ("example.com", "b.org")


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, IdentityService, CapturingSender]]:
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", SECRET)
    monkeypatch.setenv("ORCHEO_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("ORCHEO_AUTH_AUDIENCE", "orcheo-api")
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")
    reset_authentication_state()
    reset_identity_state()
    service, sender, _ = _service()
    set_identity_service(service)
    try:
        yield create_test_client(), service, sender
    finally:
        set_identity_service(None)
        reset_identity_state()
        reset_authentication_state()


def test_email_start_returns_forbidden_for_disallowed_domain(client) -> None:
    test_client, _, sender = client

    response = test_client.post(
        "/api/auth/email/start", json={"email": "mallory@other.org"}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "code": "auth.email_domain_not_allowed",
        "message": "Sign-in is limited to approved email domains.",
    }
    assert sender.sent == []


def test_email_verify_returns_forbidden_after_allowlist_change(client) -> None:
    test_client, service, sender = client
    started = test_client.post(
        "/api/auth/email/start", json={"email": "alice@example.com"}
    )
    assert started.status_code == 200
    _allow(service, ("company.com",))

    response = test_client.post(
        "/api/auth/email/verify",
        json={"email": "alice@example.com", "code": sender.sent[-1].otp_code},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "auth.email_domain_not_allowed"
