"""WebSocket authentication tests split from the extended suite."""

from __future__ import annotations
from unittest.mock import AsyncMock, Mock, patch
import pytest
from starlette.websockets import WebSocket
from orcheo_backend.app.authentication import (
    AuthenticationError,
    AuthSettings,
    authenticate_websocket,
)
from tests.backend.authentication_test_utils import (
    reset_auth_state,
)


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication state is cleared between tests."""

    yield from reset_auth_state(monkeypatch)


@pytest.mark.asyncio
async def test_authenticate_websocket_rate_limit_ip_exceeded() -> None:
    """WebSocket authentication closes on IP rate limit exceeded."""

    with patch("orcheo_backend.app.authentication.get_authenticator") as mock_get_auth:
        with patch(
            "orcheo_backend.app.authentication.get_auth_rate_limiter"
        ) as mock_get_limiter:
            mock_settings = AuthSettings(
                mode="required",
                jwt_secret="secret",
                jwks_url=None,
                jwks_static=(),
                jwks_cache_ttl=300,
                jwks_timeout=5.0,
                allowed_algorithms=("HS256",),
                audiences=(),
                issuer=None,
                service_token_backend="inmemory",
                service_token_db_path=None,
                rate_limit_ip=1,
                rate_limit_identity=10,
                rate_limit_interval=60,
            )

            mock_authenticator = Mock()
            mock_authenticator.settings = mock_settings
            mock_get_auth.return_value = mock_authenticator

            mock_limiter = Mock()
            mock_limiter.check_ip = Mock(
                side_effect=AuthenticationError(
                    "Rate limited",
                    code="auth.rate_limited.ip",
                    status_code=429,
                    websocket_code=4429,
                )
            )
            mock_get_limiter.return_value = mock_limiter

            websocket = Mock(spec=WebSocket)
            websocket.headers = {}
            websocket.query_params = {}
            websocket.client = Mock(host="1.2.3.4")
            websocket.state = Mock()
            websocket.close = AsyncMock()

            with pytest.raises(AuthenticationError):
                await authenticate_websocket(websocket)

            # Should close websocket
            websocket.close.assert_called_once()


@pytest.mark.asyncio
async def test_authenticate_websocket_rate_limit_identity_exceeded() -> None:
    """WebSocket authentication closes on identity rate limit exceeded."""
    from orcheo_backend.app.authentication import (
        AuthenticationError,
        RequestContext,
    )

    token = "valid-token"

    with patch("orcheo_backend.app.authentication.get_authenticator") as mock_get_auth:
        with patch(
            "orcheo_backend.app.authentication.get_auth_rate_limiter"
        ) as mock_get_limiter:
            mock_settings = AuthSettings(
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
                rate_limit_ip=10,
                rate_limit_identity=1,
                rate_limit_interval=60,
            )

            mock_authenticator = Mock()
            mock_authenticator.settings = mock_settings
            mock_authenticator.authenticate = AsyncMock(
                return_value=RequestContext(
                    subject="test",
                    identity_type="service",
                    token_id="test",
                )
            )
            mock_get_auth.return_value = mock_authenticator

            mock_limiter = Mock()
            mock_limiter.check_ip = Mock()
            mock_limiter.check_identity = Mock(
                side_effect=AuthenticationError(
                    "Rate limited",
                    code="auth.rate_limited.identity",
                    status_code=429,
                    websocket_code=4429,
                )
            )
            mock_get_limiter.return_value = mock_limiter

            websocket = Mock(spec=WebSocket)
            websocket.headers = {"authorization": f"Bearer {token}"}
            websocket.query_params = {}
            websocket.client = Mock(host="1.2.3.4")
            websocket.state = Mock()
            websocket.close = AsyncMock()

            with pytest.raises(AuthenticationError):
                await authenticate_websocket(websocket)

            # Should close websocket with rate limit error
            websocket.close.assert_called_once()


@pytest.mark.asyncio
async def test_authenticate_websocket_authentication_failure() -> None:
    """WebSocket closes and records telemetry on authentication failure."""
    with patch("orcheo_backend.app.authentication.get_authenticator") as mock_get_auth:
        with patch(
            "orcheo_backend.app.authentication.get_auth_rate_limiter"
        ) as mock_get_limiter:
            mock_settings = AuthSettings(
                mode="required",
                jwt_secret="secret",
                jwks_url=None,
                jwks_static=(),
                jwks_cache_ttl=300,
                jwks_timeout=5.0,
                allowed_algorithms=("HS256",),
                audiences=(),
                issuer=None,
                service_token_backend="inmemory",
                service_token_db_path=None,
                rate_limit_ip=10,
                rate_limit_identity=10,
                rate_limit_interval=60,
            )

            mock_authenticator = Mock()
            mock_authenticator.settings = mock_settings
            mock_authenticator.authenticate = AsyncMock(
                side_effect=AuthenticationError(
                    "Invalid token",
                    code="auth.invalid_token",
                    websocket_code=4401,
                )
            )
            mock_get_auth.return_value = mock_authenticator

            mock_limiter = Mock()
            mock_limiter.check_ip = Mock()
            mock_get_limiter.return_value = mock_limiter

            websocket = Mock(spec=WebSocket)
            websocket.headers = {"authorization": "Bearer bad-token"}
            websocket.query_params = {}
            websocket.client = Mock(host="1.2.3.4")
            websocket.state = Mock()
            websocket.close = AsyncMock()

            with pytest.raises(AuthenticationError):
                await authenticate_websocket(websocket)

            # Should close websocket
            websocket.close.assert_called_once()
