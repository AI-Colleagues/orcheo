"""Service token authentication helpers."""

from __future__ import annotations
import hmac
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from orcheo_backend.app.authentication.context import RequestContext
from orcheo_backend.app.authentication.errors import AuthenticationError
from orcheo_backend.app.authentication.service_tokens import ServiceTokenManager
from orcheo_backend.app.authentication.settings import AuthSettings
from orcheo_backend.app.authentication.telemetry import AuthEvent, auth_telemetry


if TYPE_CHECKING:
    from orcheo_backend.app.authentication.service_tokens import ServiceTokenRecord


logger = logging.getLogger(__name__)


class ServiceTokenAuthenticator:
    """Authenticate bearer tokens against service or bootstrap tokens."""

    def __init__(
        self, settings: AuthSettings, token_manager: ServiceTokenManager
    ) -> None:
        """Store the configuration and token manager dependencies."""
        self._settings = settings
        self._token_manager = token_manager

    async def authenticate(self, token: str) -> RequestContext | None:
        """Return a request context if ``token`` matches service credentials."""
        service_record = await self._try_authenticate_service_token(token)
        if service_record is not None:
            return self._service_record_to_context(service_record)
        return await self._authenticate_bootstrap_token(token)

    async def _try_authenticate_service_token(
        self, token: str
    ) -> ServiceTokenRecord | None:
        try:
            return await self._token_manager.authenticate(token)
        except AuthenticationError as exc:
            if exc.code == "auth.invalid_token":
                return None
            raise

    async def _authenticate_bootstrap_token(self, token: str) -> RequestContext | None:
        bootstrap_token = self._settings.bootstrap_service_token
        if not (bootstrap_token and hmac.compare_digest(token, bootstrap_token)):
            return None

        expires_at = self._settings.bootstrap_token_expires_at
        if expires_at and datetime.now(tz=UTC) >= expires_at:
            logger.warning("Bootstrap service token has expired and will be rejected")
            auth_telemetry.record_auth_failure(
                reason="bootstrap_service_token_expired",
            )
            raise AuthenticationError(
                "Bootstrap service token has expired",
                code="auth.token_expired",
            )

        auth_telemetry.record(
            AuthEvent(
                event="authenticate",
                status="success",
                subject="bootstrap",
                identity_type="bootstrap_service",
                token_id="bootstrap",
                detail="Bootstrap service token used",
            )
        )
        claims = {
            "token_type": "bootstrap_service",
            "token_id": "bootstrap",
            "scopes": sorted(self._settings.bootstrap_token_scopes),
        }
        if expires_at:
            claims["expires_at"] = expires_at.isoformat()
        return RequestContext(
            subject="bootstrap",
            identity_type="service",
            scopes=self._settings.bootstrap_token_scopes,
            workspace_ids=frozenset(),
            token_id="bootstrap",
            issued_at=None,
            expires_at=expires_at,
            claims=claims,
        )

    @staticmethod
    def _service_record_to_context(record: ServiceTokenRecord) -> RequestContext:
        claims = {
            "token_type": "service",
            "token_id": record.identifier,
            "scopes": sorted(record.scopes),
            "workspace_ids": sorted(record.workspace_ids),
            "rotated_to": record.rotated_to,
            "revoked_at": record.revoked_at.isoformat() if record.revoked_at else None,
        }
        return RequestContext(
            subject=record.identifier,
            identity_type="service",
            scopes=record.scopes,
            workspace_ids=record.workspace_ids,
            token_id=record.identifier,
            issued_at=record.issued_at,
            expires_at=record.expires_at,
            claims=claims,
        )


__all__ = ["ServiceTokenAuthenticator"]
