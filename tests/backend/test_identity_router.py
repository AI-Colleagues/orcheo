"""HTTP integration tests for the first-party auth endpoints."""

from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from orcheo.identity import InMemoryIdentityRepository
from orcheo.workspace.email import AuthChallengeEmail
from orcheo_backend.app.authentication import reset_authentication_state
from orcheo_backend.app.identity import (
    IdentityConfig,
    IdentityService,
    reset_identity_state,
    set_identity_service,
)
from tests.backend.authentication_test_utils import create_test_client

SECRET = "identity-http-secret"  # noqa: S105 - test fixture
ISSUER = "https://auth.orcheo.test"
AUDIENCE = "orcheo-api"


class CapturingSender:
    def __init__(self) -> None:
        self.sent: list[AuthChallengeEmail] = []

    def send_auth_challenge(self, email: AuthChallengeEmail) -> None:
        self.sent.append(email)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, CapturingSender]:
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", SECRET)
    monkeypatch.setenv("ORCHEO_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("ORCHEO_AUTH_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")
    reset_authentication_state()
    reset_identity_state()

    sender = CapturingSender()
    service = IdentityService(
        InMemoryIdentityRepository(),
        email_sender=sender,
        config=IdentityConfig(
            jwt_secret=SECRET,
            issuer=ISSUER,
            audience=AUDIENCE,
            verify_base_url="https://studio.test",
        ),
    )
    set_identity_service(service)

    test_client = create_test_client()
    try:
        yield test_client, sender
    finally:
        set_identity_service(None)
        reset_identity_state()
        reset_authentication_state()


def _start(client: TestClient, email: str = "alice@example.com") -> None:
    response = client.post("/api/auth/email/start", json={"email": email})
    assert response.status_code == 200
    assert response.json() == {"status": "sent"}


def test_email_start_is_constant_response(client) -> None:
    test_client, _ = client
    _start(test_client)
    # Malformed email still returns the identical "sent" response.
    bad = test_client.post("/api/auth/email/start", json={"email": "nope"})
    assert bad.status_code == 200
    assert bad.json() == {"status": "sent"}


def test_full_magic_link_login_and_me(client) -> None:
    test_client, sender = client
    _start(test_client)
    token = sender.sent[-1].magic_link_url.split("token=", 1)[1]

    verified = test_client.post("/api/auth/email/verify", json={"token": token})
    assert verified.status_code == 200
    body = verified.json()
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["email_verified"] is True
    access = body["access_token"]

    me = test_client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"


def test_otp_verify_and_refresh_and_logout(client) -> None:
    test_client, sender = client
    _start(test_client)
    code = sender.sent[-1].otp_code

    verified = test_client.post(
        "/api/auth/email/verify",
        json={"email": "alice@example.com", "code": code},
    )
    assert verified.status_code == 200
    tokens = verified.json()
    access, refresh = tokens["access_token"], tokens["refresh_token"]

    refreshed = test_client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != refresh

    # The original refresh token is now rotated away.
    replay = test_client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert replay.status_code == 401

    logout = test_client.post(
        "/api/auth/logout", headers={"Authorization": f"Bearer {access}"}
    )
    assert logout.status_code == 204

    # After logout, the rotated refresh token is revoked too.
    after = test_client.post(
        "/api/auth/refresh",
        json={"refresh_token": refreshed.json()["refresh_token"]},
    )
    assert after.status_code == 401


def test_verify_invalid_token_rejected(client) -> None:
    test_client, _ = client
    response = test_client.post("/api/auth/email/verify", json={"token": "bogus-token"})
    assert response.status_code == 400


def test_verify_requires_token_or_code(client) -> None:
    test_client, _ = client
    response = test_client.post("/api/auth/email/verify", json={})
    assert response.status_code == 400
