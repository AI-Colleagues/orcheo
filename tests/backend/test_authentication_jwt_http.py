"""HTTP authentication tests for JWT bearer tokens."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
import jwt
import pytest
from fastapi.testclient import TestClient
from orcheo_backend.app.authentication import reset_authentication_state
from tests.backend.authentication_test_utils import (
    create_test_client,
    reset_auth_state,
)


def _client() -> TestClient:
    """Create a fresh TestClient for each JWT scenario."""

    return create_test_client()


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication state is cleared between tests."""

    yield from reset_auth_state(monkeypatch)


def test_jwt_with_audience_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWT with correct audience is accepted."""

    secret = "jwt-secret"
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", secret)
    monkeypatch.setenv("ORCHEO_AUTH_AUDIENCE", "orcheo-api")
    reset_authentication_state()

    now = datetime.now(tz=UTC)
    token = jwt.encode(
        {
            "sub": "tester",
            "aud": "orcheo-api",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )

    client = _client()
    response = client.get(
        "/api/workspaces/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


def test_jwt_with_invalid_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWT with wrong audience is rejected."""

    secret = "jwt-secret"
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", secret)
    monkeypatch.setenv("ORCHEO_AUTH_AUDIENCE", "orcheo-api")
    reset_authentication_state()

    now = datetime.now(tz=UTC)
    token = jwt.encode(
        {
            "sub": "tester",
            "aud": "wrong-audience",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )

    client = _client()
    response = client.get(
        "/api/workspaces/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "auth.invalid_audience"


def test_jwt_with_issuer_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWT with correct issuer is accepted."""

    secret = "jwt-secret"
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", secret)
    monkeypatch.setenv("ORCHEO_AUTH_ISSUER", "https://auth.orcheo.com")
    reset_authentication_state()

    now = datetime.now(tz=UTC)
    token = jwt.encode(
        {
            "sub": "tester",
            "iss": "https://auth.orcheo.com",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )

    client = _client()
    response = client.get(
        "/api/workspaces/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


def test_jwt_with_trailing_slash_issuer_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JWT issuer matching tolerates a trailing slash on the token claim."""

    secret = "jwt-secret"
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", secret)
    monkeypatch.setenv("ORCHEO_AUTH_ISSUER", "https://auth.orcheo.com")
    reset_authentication_state()

    now = datetime.now(tz=UTC)
    token = jwt.encode(
        {
            "sub": "tester",
            "iss": "https://auth.orcheo.com/",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )

    client = _client()
    response = client.get(
        "/api/workspaces/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


def test_jwt_with_invalid_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWT with wrong issuer is rejected."""

    secret = "jwt-secret"
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", secret)
    monkeypatch.setenv("ORCHEO_AUTH_ISSUER", "https://auth.orcheo.com")
    reset_authentication_state()

    now = datetime.now(tz=UTC)
    token = jwt.encode(
        {
            "sub": "tester",
            "iss": "https://evil.com",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )

    client = _client()
    response = client.get(
        "/api/workflows",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "auth.invalid_issuer"


def test_jwt_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expired JWT is rejected."""

    secret = "jwt-secret"
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", secret)
    reset_authentication_state()

    now = datetime.now(tz=UTC)
    token = jwt.encode(
        {
            "sub": "tester",
            "iat": int((now - timedelta(hours=2)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )

    client = _client()
    response = client.get(
        "/api/workflows",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    detail = response.json()["detail"]
    assert detail["code"] == "auth.token_expired"


def test_jwt_with_unsupported_algorithm(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWT with unsupported algorithm is rejected."""

    secret = "jwt-secret"
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", secret)
    monkeypatch.setenv("ORCHEO_AUTH_ALLOWED_ALGORITHMS", "HS256")
    reset_authentication_state()

    now = datetime.now(tz=UTC)
    token = jwt.encode(
        {
            "sub": "tester",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        secret,
        algorithm="HS384",
    )

    client = _client()
    response = client.get(
        "/api/workflows",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    detail = response.json()["detail"]
    assert detail["code"] == "auth.unsupported_algorithm"
