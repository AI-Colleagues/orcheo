"""JWKS resolution tests split from the extended suite."""

from __future__ import annotations
import pytest
from orcheo_backend.app.authentication import (
    Authenticator,
    AuthSettings,
    ServiceTokenManager,
)
from tests.backend.authentication_test_utils import reset_auth_state


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication state is cleared between tests."""

    yield from reset_auth_state(monkeypatch)


@pytest.mark.asyncio
async def test_match_static_key_with_mismatched_kid() -> None:
    """Static JWKS matching skips keys with mismatched kid."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    public_key = private_key.public_key()

    jwk_dict = RSAAlgorithm(RSAAlgorithm.SHA256).to_jwk(public_key, as_dict=True)
    jwk_dict["kid"] = "expected-kid"
    jwk_dict["alg"] = "RS256"

    settings = AuthSettings(
        mode="required",
        jwt_secret=None,
        jwks_url=None,
        jwks_static=(jwk_dict,),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=("RS256",),
        audiences=(),
        issuer=None,
        service_token_backend="inmemory",
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

    # Try to match with wrong kid
    jwt_auth = authenticator._jwt_authenticator  # noqa: SLF001
    key = jwt_auth._match_static_key("wrong-kid", "RS256")  # noqa: SLF001

    # Should return None
    assert key is None


@pytest.mark.asyncio
async def test_match_static_key_with_mismatched_algorithm() -> None:
    """Static JWKS matching skips keys with mismatched algorithm."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    public_key = private_key.public_key()

    jwk_dict = RSAAlgorithm(RSAAlgorithm.SHA256).to_jwk(public_key, as_dict=True)
    jwk_dict["kid"] = "test-kid"
    jwk_dict["alg"] = "RS256"

    settings = AuthSettings(
        mode="required",
        jwt_secret=None,
        jwks_url=None,
        jwks_static=(jwk_dict,),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=("RS256", "RS384"),
        audiences=(),
        issuer=None,
        service_token_backend="inmemory",
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

    # Try to match with wrong algorithm
    jwt_auth = authenticator._jwt_authenticator  # noqa: SLF001
    key = jwt_auth._match_static_key("test-kid", "RS384")  # noqa: SLF001

    # Should return None
    assert key is None


@pytest.mark.asyncio
async def test_resolve_signing_key_returns_none_when_no_jwks_cache() -> None:
    """_resolve_signing_key returns None when no JWKS cache is configured."""

    settings = AuthSettings(
        mode="required",
        jwt_secret=None,
        jwks_url=None,
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=("RS256",),
        audiences=(),
        issuer=None,
        service_token_backend="inmemory",
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

    # Should return None when no static keys and no JWKS URL
    jwt_auth = authenticator._jwt_authenticator  # noqa: SLF001
    key = await jwt_auth._resolve_signing_key({"kid": "test", "alg": "RS256"})  # noqa: SLF001

    assert key is None


def test_resolve_signing_key_without_jwks_cache() -> None:
    """_resolve_signing_key returns None when no JWKS cache exists."""
    from orcheo_backend.app.authentication import Authenticator, AuthSettings

    settings = AuthSettings(
        mode="required",
        jwt_secret=None,
        jwks_url=None,
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=("RS256",),
        audiences=(),
        issuer=None,
        service_token_backend="inmemory",
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

    # Verify no cache exists
    assert authenticator._jwt_authenticator.jwks_cache is None  # noqa: SLF001
