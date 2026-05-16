"""Service token tests split from the extended suite."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch
import pytest
from orcheo_backend.app.authentication import (
    AuthenticationError,
    Authenticator,
    AuthSettings,
    ServiceTokenManager,
    ServiceTokenRecord,
)
from orcheo_backend.app.service_token_repository import InMemoryServiceTokenRepository
from tests.backend.authentication_test_utils import reset_auth_state


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication state is cleared between tests."""

    yield from reset_auth_state(monkeypatch)


@pytest.mark.asyncio
async def test_authenticate_service_token_reraises_non_invalid_errors() -> None:
    """ServiceTokenManager re-raises non-invalid_token errors like token_expired."""
    import hashlib

    token = "expired-token"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()

    from orcheo_backend.app.authentication import (
        ServiceTokenManager,
        ServiceTokenRecord,
    )

    record = ServiceTokenRecord(
        identifier="expired",
        secret_hash=digest,
        expires_at=datetime.now(tz=UTC) - timedelta(hours=1),
    )

    # Create in-memory repository with the expired token
    from orcheo_backend.app.service_token_repository import (
        InMemoryServiceTokenRepository,
    )

    repository = InMemoryServiceTokenRepository()
    await repository.create(record)
    token_manager = ServiceTokenManager(repository)

    # Should raise token_expired error (not invalid_token)
    with pytest.raises(AuthenticationError) as exc:
        await token_manager.authenticate(token)
    assert exc.value.code == "auth.token_expired"


# Note: Lines 625-626 (key_unavailable error) are defensive error handling
# that's difficult to trigger without extensive mocking of JWKS resolution


@pytest.mark.asyncio
async def test_authenticate_service_token_reraises_revoked_error() -> None:
    """Test _authenticate_service_token re-raises non-invalid_token (line 606)."""
    import hashlib

    # Create TWO tokens - one valid, one revoked
    # We need at least one token to exist for line 597-599 check to pass
    valid_token = "valid-token"
    valid_digest = hashlib.sha256(valid_token.encode("utf-8")).hexdigest()
    valid_record = ServiceTokenRecord(
        identifier="valid",
        secret_hash=valid_digest,
    )

    revoked_token = "revoked-token"
    revoked_digest = hashlib.sha256(revoked_token.encode("utf-8")).hexdigest()
    revoked_record = ServiceTokenRecord(
        identifier="revoked",
        secret_hash=revoked_digest,
        revoked_at=datetime.now(tz=UTC),
    )

    repository = InMemoryServiceTokenRepository()
    await repository.create(valid_record)
    await repository.create(revoked_record)
    token_manager = ServiceTokenManager(repository)

    settings = AuthSettings(
        mode="required",
        jwt_secret=None,
        jwks_url=None,
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=(),
        audiences=(),
        issuer=None,
        service_token_backend="inmemory",
        service_token_db_path=None,
        rate_limit_ip=0,
        rate_limit_identity=0,
        rate_limit_interval=60,
    )
    authenticator = Authenticator(settings, token_manager)

    # When token is revoked, authenticate raises with token_revoked code
    # (not invalid_token). Re-raised by _authenticate_service_token line 606
    with pytest.raises(AuthenticationError) as exc:
        await authenticator.authenticate(revoked_token)
    assert exc.value.code == "auth.token_revoked"


def test_get_service_token_manager_with_refresh() -> None:
    """Test get_service_token_manager with refresh parameter (line 1110-1111)."""
    from orcheo_backend.app.authentication import get_service_token_manager

    # Get manager first time
    manager1 = get_service_token_manager()
    assert manager1 is not None

    # Get with refresh=True should reinitialize
    manager2 = get_service_token_manager(refresh=True)
    assert manager2 is not None


def test_get_service_token_manager_runtime_error() -> None:
    """Test get_service_token_manager RuntimeError (line 1116)."""
    from orcheo_backend.app.authentication import (
        _token_manager_cache,
        get_service_token_manager,
        reset_authentication_state,
    )

    # Clear all caches
    reset_authentication_state()
    _token_manager_cache["manager"] = None

    # Mock get_authenticator to not initialize the token manager
    with patch("orcheo_backend.app.authentication.get_authenticator") as mock_auth:
        # Ensure get_authenticator doesn't set the manager
        mock_auth.return_value = Mock()
        _token_manager_cache["manager"] = None

        with pytest.raises(RuntimeError) as exc:
            get_service_token_manager()
        assert "ServiceTokenManager not initialized" in str(exc.value)
