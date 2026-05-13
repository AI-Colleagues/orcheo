"""Authenticator JWT/JWKS tests split from the extended suite."""

from __future__ import annotations
import pytest
from orcheo_backend.app.authentication import (
    AuthenticationError,
    Authenticator,
    AuthSettings,
    ServiceTokenManager,
    load_auth_settings,
)
from tests.backend.authentication_test_utils import reset_auth_state


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication state is cleared between tests."""

    yield from reset_auth_state(monkeypatch)


@pytest.mark.asyncio
async def test_authenticator_jwt_with_invalid_token_format() -> None:
    """Authenticator rejects malformed JWT tokens."""

    settings = load_auth_settings(refresh=True)
    from orcheo_backend.app.service_token_repository import (
        InMemoryServiceTokenRepository,
    )

    repository = InMemoryServiceTokenRepository()
    token_manager = ServiceTokenManager(repository)
    authenticator = Authenticator(settings, token_manager)

    with pytest.raises(AuthenticationError) as exc:
        await authenticator.authenticate("not-a-valid-jwt")
    assert exc.value.code == "auth.invalid_token"


@pytest.mark.asyncio
async def test_authenticator_jwt_key_resolution_returns_none() -> None:
    """Authenticator raises auth.key_unavailable when key not resolved."""
    from unittest.mock import AsyncMock, patch

    settings = AuthSettings(
        mode="required",
        jwt_secret=None,
        jwks_url="https://example.com/.well-known/jwks.json",
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=("RS256",),
        audiences=(),
        issuer=None,
        service_token_backend="sqlite",
        service_token_db_path=None,
        rate_limit_ip=0,
        rate_limit_identity=0,
        rate_limit_interval=60,
    )

    from orcheo_backend.app.service_token_repository import (
        InMemoryServiceTokenRepository,
    )

    repository = InMemoryServiceTokenRepository()
    token_manager = ServiceTokenManager(repository)
    authenticator = Authenticator(settings, token_manager)

    # Create a valid RS256 token
    import jwt as jwt_lib
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt_lib.encode({"sub": "test-user"}, private_key, algorithm="RS256")

    # Mock _resolve_signing_key to return None (key not found)
    with patch.object(
        authenticator._jwt_authenticator,  # noqa: SLF001
        "_resolve_signing_key",
        AsyncMock(return_value=None),
    ):
        with pytest.raises(AuthenticationError) as exc_info:
            await authenticator.authenticate(token)

        assert exc_info.value.code == "auth.key_unavailable"
        assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_authenticator_jwt_invalid_token_error() -> None:
    """Authenticator handles InvalidTokenError during JWT decode."""
    settings = AuthSettings(
        mode="required",
        jwt_secret="test-secret",
        jwks_url=None,
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=("HS256",),
        audiences=(),
        issuer=None,
        service_token_backend="sqlite",
        service_token_db_path=None,
        rate_limit_ip=0,
        rate_limit_identity=0,
        rate_limit_interval=60,
    )

    from orcheo_backend.app.service_token_repository import (
        InMemoryServiceTokenRepository,
    )

    repository = InMemoryServiceTokenRepository()
    token_manager = ServiceTokenManager(repository)
    authenticator = Authenticator(settings, token_manager)

    # Create a malformed token that will fail decode with InvalidTokenError
    # Use a valid JWT structure but signed with wrong secret
    import jwt as jwt_lib

    token = jwt_lib.encode({"sub": "test-user"}, "wrong-secret", algorithm="HS256")

    with pytest.raises(AuthenticationError) as exc_info:
        await authenticator.authenticate(token)

    assert exc_info.value.code == "auth.invalid_token"


@pytest.mark.asyncio
async def test_decode_claims_with_generic_invalid_token_error() -> None:
    """_decode_claims handles generic InvalidTokenError."""
    from orcheo_backend.app.authentication import (
        AuthenticationError,
        Authenticator,
        AuthSettings,
    )

    settings = AuthSettings(
        mode="required",
        jwt_secret="test-secret",
        jwks_url=None,
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=("HS256",),
        audiences=(),
        issuer=None,
        service_token_backend="sqlite",
        service_token_db_path=None,
        rate_limit_ip=0,
        rate_limit_identity=0,
        rate_limit_interval=60,
    )

    from orcheo_backend.app.service_token_repository import (
        InMemoryServiceTokenRepository,
    )

    repository = InMemoryServiceTokenRepository()
    token_manager = ServiceTokenManager(repository)
    authenticator = Authenticator(settings, token_manager)

    # Create a malformed JWT that will raise InvalidTokenError
    malformed_token = "not.a.valid.jwt.at.all"

    with pytest.raises(AuthenticationError) as exc:
        await authenticator.authenticate(malformed_token)

    assert exc.value.code == "auth.invalid_token"


@pytest.mark.asyncio
async def test_decode_claims_with_invalid_issuer_error() -> None:
    """_decode_claims translates InvalidIssuerError into auth.invalid_issuer."""

    settings = AuthSettings(
        mode="required",
        jwt_secret="test-secret",
        jwks_url=None,
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=("HS256",),
        audiences=(),
        issuer="https://auth.example.com",
        service_token_backend="sqlite",
        service_token_db_path=None,
        rate_limit_ip=0,
        rate_limit_identity=0,
        rate_limit_interval=60,
    )

    from orcheo_backend.app.service_token_repository import (
        InMemoryServiceTokenRepository,
    )

    repository = InMemoryServiceTokenRepository()
    token_manager = ServiceTokenManager(repository)
    authenticator = Authenticator(settings, token_manager)

    import jwt as jwt_lib

    token = jwt_lib.encode(
        {"sub": "test-user", "iss": "https://wrong.example.com"},
        "test-secret",
        algorithm="HS256",
    )

    with pytest.raises(AuthenticationError) as exc:
        await authenticator.authenticate(token)

    assert exc.value.code == "auth.invalid_issuer"


def test_validate_issuer_rejects_non_string_claim() -> None:
    """_validate_issuer rejects issuer claims that are not strings."""

    settings = AuthSettings(
        mode="required",
        jwt_secret="test-secret",
        jwks_url=None,
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=("HS256",),
        audiences=(),
        issuer="https://auth.example.com",
        service_token_backend="sqlite",
        service_token_db_path=None,
        rate_limit_ip=0,
        rate_limit_identity=0,
        rate_limit_interval=60,
    )

    from orcheo_backend.app.service_token_repository import (
        InMemoryServiceTokenRepository,
    )

    repository = InMemoryServiceTokenRepository()
    token_manager = ServiceTokenManager(repository)
    authenticator = Authenticator(settings, token_manager)

    with pytest.raises(AuthenticationError) as exc:
        authenticator._jwt_authenticator._validate_issuer({"iss": 123})  # noqa: SLF001

    assert exc.value.code == "auth.invalid_issuer"
