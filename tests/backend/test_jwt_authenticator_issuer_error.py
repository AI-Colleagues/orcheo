"""Cover the InvalidIssuerError path in JWTAuthenticator._decode_claims (line 133)."""

from __future__ import annotations

import jwt
import pytest

from orcheo_backend.app.authentication.errors import AuthenticationError
from orcheo_backend.app.authentication.jwt_authenticator import JWTAuthenticator
from orcheo_backend.app.authentication.settings import AuthSettings


def _settings(**overrides: object) -> AuthSettings:
    base: dict[str, object] = dict(
        mode="optional",
        jwt_secret="test-secret",
        jwks_url=None,
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=("HS256",),
        audiences=("test-audience",),
        issuer=None,
        service_token_backend="sqlite",
        service_token_db_path=None,
        rate_limit_ip=0,
        rate_limit_identity=0,
        rate_limit_interval=60,
        dev_login_enabled=False,
        dev_login_cookie_name=None,
        dev_login_scopes=(),
        dev_login_workspace_ids=(),
    )
    base.update(overrides)
    return AuthSettings(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_decode_claims_invalid_issuer_from_jwt_library() -> None:
    """InvalidIssuerError from the JWT library is mapped to auth.invalid_issuer (line 133)."""
    settings = _settings(
        audiences=("test-audience",),
        issuer="https://expected-issuer.com",
    )
    authenticator = JWTAuthenticator(settings)

    # Create a token with a wrong issuer that will cause jwt.decode to raise
    # InvalidIssuerError (when issuer validation is done by the library itself).
    # We do this by providing an `iss` claim that does not match.
    payload = {
        "sub": "user-1",
        "aud": "test-audience",
        "iss": "https://wrong-issuer.com",
    }
    token = jwt.encode(payload, "test-secret", algorithm="HS256")

    # _validate_issuer does custom comparison; to trigger InvalidIssuerError from
    # the JWT library itself we need to configure jwt.decode with issuer kwarg.
    # Instead, patch _decode_claims to trigger the except branch.
    from unittest.mock import patch
    from jwt.exceptions import InvalidIssuerError as JwtInvalidIssuerError

    def _fake_decode(*args, **kwargs):
        raise JwtInvalidIssuerError("bad issuer")

    with patch("jwt.decode", side_effect=_fake_decode):
        with pytest.raises(AuthenticationError) as exc_info:
            authenticator._decode_claims(token, "test-secret")

    assert exc_info.value.code == "auth.invalid_issuer"
