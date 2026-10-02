from __future__ import annotations
from .context import RequestContext
from .errors import AuthenticationError
from .jwks import JWKSFetcher
from .jwt_authenticator import JWTAuthenticator
from .service_token_authenticator import ServiceTokenAuthenticator
from .service_tokens import ServiceTokenManager
from .settings import AuthSettings


class Authenticator:
    """Validate bearer tokens using service tokens or JWT configuration."""

    def __init__(
        self, settings: AuthSettings, token_manager: ServiceTokenManager
    ) -> None:
        """Configure service token and JWT authenticators."""
        self._settings = settings
        self._token_manager = token_manager
        self._service_token_auth = ServiceTokenAuthenticator(settings, token_manager)
        self._jwt_authenticator = JWTAuthenticator(settings)

    @property
    def settings(self) -> AuthSettings:
        """Expose the resolved settings."""
        return self._settings

    @property
    def service_token_manager(self) -> ServiceTokenManager:
        """Expose the service token manager for lifecycle operations."""
        return self._token_manager

    async def authenticate(self, token: str) -> RequestContext:
        """Validate a bearer token and return the associated identity."""
        if not token:
            raise AuthenticationError("Missing bearer token", code="auth.missing_token")

        jwt_error: AuthenticationError | None = None
        if self._jwt_authenticator.configured and token.count(".") == 2:
            try:
                return await self._jwt_authenticator.authenticate(token)
            except AuthenticationError as exc:
                # An operator-supplied service or bootstrap token may contain dots.
                jwt_error = exc

        identity = await self._service_token_auth.authenticate(token)
        if identity is not None:
            return identity

        if jwt_error is not None:
            raise jwt_error

        if self._jwt_authenticator.configured:
            return await self._jwt_authenticator.authenticate(token)

        raise AuthenticationError("Invalid bearer token", code="auth.invalid_token")


__all__ = ["Authenticator", "JWKSFetcher"]
