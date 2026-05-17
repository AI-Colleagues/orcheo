"""Authenticator JWT/JWKS tests split from the extended suite."""

from __future__ import annotations
import pytest
from orcheo_backend.app.authentication import (
    Authenticator,
    AuthSettings,
    ServiceTokenManager,
)
from orcheo_backend.app.service_token_repository import InMemoryServiceTokenRepository
from tests.backend.authentication_test_utils import reset_auth_state


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication state is cleared between tests."""

    yield from reset_auth_state(monkeypatch)


@pytest.mark.asyncio
async def test_authenticator_resolve_signing_key_with_jwks_cache() -> None:
    """Authenticator._resolve_signing_key fetches from JWKS cache."""
    import jwt as jwt_lib
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    # Convert to JWK dict properly
    jwk_dict = jwt_lib.algorithms.RSAAlgorithm.to_jwk(public_key, as_dict=True)
    if isinstance(jwk_dict, str):
        import json

        jwk_dict = json.loads(jwk_dict)

    jwk_dict["kid"] = "test-key-id"
    jwk_dict["alg"] = "RS256"

    # Mock JWKS fetcher
    async def mock_fetcher():
        return [jwk_dict], 300

    from orcheo_backend.app.authentication import JWKSCache

    jwks_cache = JWKSCache(mock_fetcher, ttl_seconds=300)

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
    authenticator._jwt_authenticator.jwks_cache = jwks_cache  # noqa: SLF001

    # Create token with matching kid
    token = jwt_lib.encode(
        {"sub": "test-user"},
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key-id"},
    )

    # Should successfully resolve the key and authenticate
    context = await authenticator.authenticate(token)
    assert context.subject == "test-user"


def test_authenticator_static_jwks_with_non_string_algorithm() -> None:
    """Authenticator handles JWKS entries where alg is not a string (line 564)."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    public_key = private_key.public_key()

    # Create two JWKS entries - one valid, one with non-string alg
    jwk_dict_valid = RSAAlgorithm(RSAAlgorithm.SHA256).to_jwk(public_key, as_dict=True)
    jwk_dict_valid["kid"] = "valid-key"
    jwk_dict_valid["alg"] = "RS256"

    # Create a valid JWK but without alg field (will be None in entry.get("alg"))
    jwk_dict_no_alg = RSAAlgorithm(RSAAlgorithm.SHA256).to_jwk(public_key, as_dict=True)
    jwk_dict_no_alg["kid"] = "no-alg-key"
    # Explicitly remove alg field
    if "alg" in jwk_dict_no_alg:
        del jwk_dict_no_alg["alg"]

    settings = AuthSettings(
        mode="required",
        jwt_secret=None,
        jwks_url=None,
        jwks_static=(jwk_dict_valid, jwk_dict_no_alg),
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

    repository = InMemoryServiceTokenRepository()
    token_manager = ServiceTokenManager(repository)
    authenticator = Authenticator(settings, token_manager)

    # Both should be added successfully
    # First has algorithm_str="RS256", second has algorithm_str=None (line 564)
    static_jwks = authenticator._jwt_authenticator.static_jwks  # noqa: SLF001
    assert len(static_jwks) == 2
    assert static_jwks[0][1] == "RS256"
    assert static_jwks[1][1] is None  # line 564 else branch
