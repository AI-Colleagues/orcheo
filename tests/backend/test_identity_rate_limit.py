"""HTTP abuse tests: per-IP rate limiting on the email entry point."""

from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from orcheo.identity import InMemoryIdentityRepository
from orcheo.workspace.email import AuthChallengeEmail
from orcheo_backend.app.authentication import (
    get_auth_rate_limiter,
    reset_authentication_state,
)
from orcheo_backend.app.identity import (
    IdentityConfig,
    IdentityService,
    reset_identity_state,
    set_identity_service,
)
from tests.backend.authentication_test_utils import create_test_client

SECRET = "rl-secret"  # noqa: S105 - test fixture


class _NullSender:
    def send_auth_challenge(self, email: AuthChallengeEmail) -> None:
        return None


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", SECRET)
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")
    # Allow two attempts per IP per window; the third should be rejected.
    monkeypatch.setenv("ORCHEO_AUTH_RATE_LIMIT_IP", "2")
    monkeypatch.setenv("ORCHEO_AUTH_RATE_LIMIT_INTERVAL", "60")
    reset_authentication_state()
    reset_identity_state()
    get_auth_rate_limiter(refresh=True)

    set_identity_service(
        IdentityService(
            InMemoryIdentityRepository(),
            email_sender=_NullSender(),
            config=IdentityConfig(jwt_secret=SECRET),
        )
    )
    test_client = create_test_client()
    try:
        yield test_client
    finally:
        set_identity_service(None)
        reset_identity_state()
        reset_authentication_state()
        get_auth_rate_limiter(refresh=True)


def test_email_start_is_rate_limited_per_ip(client: TestClient) -> None:
    ok1 = client.post("/api/auth/email/start", json={"email": "a@example.com"})
    ok2 = client.post("/api/auth/email/start", json={"email": "b@example.com"})
    assert ok1.status_code == 200
    assert ok2.status_code == 200

    # Third attempt within the window from the same IP is throttled.
    throttled = client.post("/api/auth/email/start", json={"email": "c@example.com"})
    assert throttled.status_code == 429
