"""First-party token acceptance and sole-issuer enforcement at the auth layer."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
import jwt
import pytest
from orcheo.identity import InMemoryIdentityRepository
from orcheo.identity.models import User
from orcheo.workspace.email import AuthChallengeEmail
from orcheo_backend.app.authentication import reset_authentication_state
from orcheo_backend.app.identity import (
    IdentityConfig,
    IdentityService,
    reset_identity_state,
    set_identity_service,
)
from orcheo_backend.app.identity.tokens import mint_access_token
from tests.backend.authentication_test_utils import create_test_client

SECRET = "first-party-secret"  # noqa: S105 - test fixture
ISSUER = "https://auth.orcheo.test"
AUDIENCE = "orcheo-api"


class _NullSender:
    def send_auth_challenge(self, email: AuthChallengeEmail) -> None:  # noqa: D102
        return None


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", SECRET)
    monkeypatch.setenv("ORCHEO_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("ORCHEO_AUTH_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")
    reset_authentication_state()
    reset_identity_state()
    repo = InMemoryIdentityRepository()
    service = IdentityService(
        repo,
        email_sender=_NullSender(),
        config=IdentityConfig(jwt_secret=SECRET, issuer=ISSUER, audience=AUDIENCE),
    )
    set_identity_service(service)
    client = create_test_client()
    try:
        yield client, repo
    finally:
        set_identity_service(None)
        reset_identity_state()
        reset_authentication_state()


def test_first_party_token_is_accepted(env) -> None:
    client, repo = env
    user = repo.create_user(User(email="carol@example.com", email_verified=True))
    token, _ = mint_access_token(
        user=user,
        secret=SECRET,
        issuer=ISSUER,
        audience=AUDIENCE,
        ttl_seconds=300,
        now=datetime.now(tz=UTC),
    )
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == "carol@example.com"


def test_foreign_issuer_token_is_rejected(env) -> None:
    client, _ = env
    now = datetime.now(tz=UTC)
    # A token signed with the same secret but a *different* issuer (e.g. a
    # stale Auth0-style token) must not be accepted — first-party is the sole
    # issuer.
    forged = jwt.encode(
        {
            "sub": "auth0|legacy",
            "aud": AUDIENCE,
            "iss": "https://orcheo.us.auth0.com/",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "auth.invalid_issuer"
