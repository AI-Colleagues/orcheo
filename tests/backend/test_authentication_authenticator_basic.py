"""Authenticator tests split from the extended suite."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
import jwt
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
async def test_authenticator_with_static_jwks() -> None:
    """Authenticator can use static JWKS configuration."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import rsa
    from orcheo_backend.app.authentication import Authenticator

    # Generate RSA key pair
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    public_key = private_key.public_key()

    # Create JWK
    from jwt.algorithms import RSAAlgorithm

    jwk_dict = RSAAlgorithm(RSAAlgorithm.SHA256).to_jwk(public_key, as_dict=True)
    jwk_dict["kid"] = "static-key-1"
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

    # Create JWT signed with private key
    now = datetime.now(tz=UTC)
    token = jwt.encode(
        {
            "sub": "test-user",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "static-key-1"},
    )

    context = await authenticator.authenticate(token)

    assert context.subject == "test-user"


def test_authenticator_properties() -> None:
    """Authenticator exposes settings and service token manager."""

    settings = load_auth_settings(refresh=True)
    from orcheo_backend.app.service_token_repository import (
        InMemoryServiceTokenRepository,
    )

    repository = InMemoryServiceTokenRepository()
    token_manager = ServiceTokenManager(repository)
    authenticator = Authenticator(settings, token_manager)

    assert authenticator.settings == settings
    assert isinstance(authenticator.service_token_manager, ServiceTokenManager)


@pytest.mark.asyncio
async def test_authenticator_authenticate_empty_token() -> None:
    """Authenticator rejects empty tokens."""

    settings = load_auth_settings(refresh=True)
    from orcheo_backend.app.service_token_repository import (
        InMemoryServiceTokenRepository,
    )

    repository = InMemoryServiceTokenRepository()
    token_manager = ServiceTokenManager(repository)
    authenticator = Authenticator(settings, token_manager)

    with pytest.raises(AuthenticationError) as exc:
        await authenticator.authenticate("")
    assert exc.value.code == "auth.missing_token"


def test_claims_to_context_with_various_token_ids() -> None:
    """claims_to_context extracts token_id from various claim fields."""
    from orcheo_backend.app.authentication.jwt_helpers import claims_to_context

    # With jti
    context = claims_to_context({"sub": "user", "jti": "token-123"})
    assert context.token_id == "token-123"

    # With token_id
    context = claims_to_context({"sub": "user", "token_id": "token-456"})
    assert context.token_id == "token-456"

    # Fallback to subject
    context = claims_to_context({"sub": "user-789"})
    assert context.token_id == "user-789"
