"""OAuth 2.1 authorization server backed by the first-party identity service.

MCP clients register dynamically, send the user through the authorize
endpoint, and land on Studio's consent page, which reuses the normal
passwordless login. Approval there issues a single-use code bound to the user;
the token endpoint exchanges it (with PKCE) for the same HS256 access tokens
Studio uses, plus a refresh token tied to the client and approved scopes.
"""

from __future__ import annotations
import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, cast
from urllib.parse import urlencode, urlparse
from uuid import UUID
from fastmcp.server.auth import AccessToken, OAuthProvider
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.concurrency import run_in_threadpool
from starlette.routing import Route
from orcheo.identity import (
    IdentitySessionNotFoundError,
    OAuthAuthorizationRequest,
    OAuthAuthorizationRequestNotFoundError,
    OAuthClient,
    OAuthClientNotFoundError,
)
from orcheo.models.base import _utcnow
from orcheo_backend.app.identity.service import IdentityService, IssuedTokens
from orcheo_backend.app.identity.tokens import hash_secret
from orcheo_backend.app.oauth.handlers import registration_endpoint, revocation_endpoint
from orcheo_backend.app.oauth.urls import (
    DEFAULT_OAUTH_SCOPES,
    MCP_RESOURCE_PATH,
    OAUTH_PREFIX,
    SUPPORTED_SCOPES,
)


AUTHORIZATION_REQUEST_TTL = timedelta(minutes=10)
AUTHORIZATION_CODE_TTL = timedelta(minutes=5)
_REQUEST_ID_BYTES = 24
_CODE_BYTES = 32


class OrcheoAuthorizationCode(AuthorizationCode):
    """Authorization code carrying the approving user and its request record."""

    request_id: str
    user_id: UUID


def derive_client_secret(signing_secret: str, client_id: str) -> str:
    """Return the deterministic secret of a confidential client.

    Secrets are derived from the server's signing secret instead of stored,
    so a database leak does not expose them.
    """
    message = f"oauth-client-secret:{client_id}".encode()
    return hmac.new(signing_secret.encode(), message, hashlib.sha256).hexdigest()


def client_id_of(client: OAuthClientInformationFull) -> str:
    """Return a registered client's ID (always set for clients issued here)."""
    return cast(str, client.client_id)


def _to_token(tokens: IssuedTokens, scopes: list[str]) -> OAuthToken:
    return OAuthToken(
        access_token=tokens.access_token,
        expires_in=tokens.expires_in,
        refresh_token=tokens.refresh_token,
        scope=" ".join(scopes),
    )


class OrcheoOAuthProvider(OAuthProvider):
    """FastMCP OAuth authorization server backed by the identity service.

    FastMCP builds the standard endpoints (authorize, token, discovery
    metadata); registration and revocation are swapped for Orcheo's own
    handlers (see ``get_routes``).
    """

    def __init__(
        self,
        service: IdentityService,
        *,
        public_url: str,
        signing_secret: str,
        consent_url: str,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        """Bind the provider to identity storage and the Studio consent page.

        Args:
            service: Identity service that stores grants and mints tokens.
            public_url: Origin clients use to reach the backend; endpoints
                live under ``/api/oauth`` and the MCP resource at ``/api/mcp``.
            signing_secret: Secret confidential clients' secrets derive from.
            consent_url: Studio page that asks the user to approve a client.
            clock: Time source, overridable in tests.
        """
        super().__init__(
            base_url=f"{public_url}{OAUTH_PREFIX}",
            resource_base_url=public_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=list(SUPPORTED_SCOPES),
                default_scopes=list(DEFAULT_OAUTH_SCOPES),
            ),
            revocation_options=RevocationOptions(enabled=True),
        )
        self._service = service
        self._repository = service.repository
        self._signing_secret = signing_secret
        self._consent_url = consent_url
        self._clock = clock

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """Return FastMCP's OAuth routes with Orcheo's registration and revocation.

        Registration issues derived (never stored) client secrets and vets
        redirect URIs; revocation accepts public clients, which the MCP SDK's
        handler rejects for lacking a ``client_secret`` field.
        """
        replacements = {
            "/register": Route(
                "/register", registration_endpoint(self), methods=["POST"]
            ),
            "/revoke": Route("/revoke", revocation_endpoint(self), methods=["POST"]),
        }
        return [
            replacements.get(route.path, route)
            for route in super().get_routes(mcp_path)
        ]

    # -- clients --------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Return a registered client, re-deriving its secret when it has one."""
        try:
            record = await run_in_threadpool(
                self._repository.get_oauth_client, client_id
            )
        except OAuthClientNotFoundError:
            return None
        client = OAuthClientInformationFull.model_validate(record.metadata)
        if client.token_endpoint_auth_method != "none":
            client.client_secret = derive_client_secret(self._signing_secret, client_id)
        return client

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Persist a client registration without its secret."""
        metadata = client_info.model_dump(
            mode="json", exclude={"client_secret"}, exclude_none=True
        )
        await run_in_threadpool(
            self._repository.add_oauth_client,
            OAuthClient(client_id=client_id_of(client_info), metadata=metadata),
        )

    def client_secret_for(self, client_id: str) -> str:
        """Return the secret a new confidential client should be issued."""
        return derive_client_secret(self._signing_secret, client_id)

    # -- authorization --------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Record the request and send the user to Studio for consent."""
        if params.resource is not None:
            parsed = urlparse(params.resource)
            if parsed.path.rstrip("/") != MCP_RESOURCE_PATH:
                raise AuthorizeError(
                    error="invalid_request",
                    error_description=(
                        "Tokens can only be requested for this server's MCP "
                        f"endpoint ({MCP_RESOURCE_PATH})."
                    ),
                )
        now = self._clock()
        scopes = params.scopes or (client.scope or "").split()
        request = OAuthAuthorizationRequest(
            id=secrets.token_urlsafe(_REQUEST_ID_BYTES),
            client_id=client_id_of(client),
            redirect_uri=str(params.redirect_uri),
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            code_challenge=params.code_challenge,
            state=params.state,
            scopes=scopes,
            resource=params.resource,
            created_at=now,
            expires_at=now + AUTHORIZATION_REQUEST_TTL,
        )
        await run_in_threadpool(self._repository.add_authorization_request, request)
        return f"{self._consent_url}?{urlencode({'request': request.id})}"

    async def pending_request(
        self, request_id: str
    ) -> tuple[OAuthAuthorizationRequest, OAuthClientInformationFull] | None:
        """Return a request still awaiting consent, with its client."""
        try:
            request = await run_in_threadpool(
                self._repository.get_authorization_request, request_id
            )
        except OAuthAuthorizationRequestNotFoundError:
            return None
        client = await self.get_client(request.client_id)
        if client is None or not request.is_pending(now=self._clock()):
            return None
        return request, client

    async def decide(self, request_id: str, *, user_id: UUID, approve: bool) -> str:
        """Record the user's consent decision and return the client redirect.

        Raises:
            OAuthAuthorizationRequestNotFoundError: If the request is unknown,
                expired or already decided.
        """
        pending = await self.pending_request(request_id)
        if pending is None:
            raise OAuthAuthorizationRequestNotFoundError(request_id)
        request, _ = pending
        now = self._clock()
        if not approve:
            await run_in_threadpool(
                self._repository.update_authorization_request,
                request.model_copy(update={"decided_at": now}),
            )
            return construct_redirect_uri(
                request.redirect_uri,
                error="access_denied",
                error_description="The user denied the authorization request.",
                state=request.state,
            )
        code = secrets.token_urlsafe(_CODE_BYTES)
        await run_in_threadpool(
            self._repository.update_authorization_request,
            request.model_copy(
                update={
                    "decided_at": now,
                    "user_id": user_id,
                    "code_hash": hash_secret(code),
                    "code_expires_at": now + AUTHORIZATION_CODE_TTL,
                }
            ),
        )
        return construct_redirect_uri(
            request.redirect_uri, code=code, state=request.state
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> OrcheoAuthorizationCode | None:
        """Return the unconsumed code issued to ``client``, if any."""
        try:
            request = await run_in_threadpool(
                self._repository.get_authorization_request_by_code_hash,
                hash_secret(authorization_code),
            )
        except OAuthAuthorizationRequestNotFoundError:
            return None
        if (
            request.client_id != client_id_of(client)
            or request.consumed_at is not None
            or request.user_id is None
            or request.code_expires_at is None
        ):
            return None
        return OrcheoAuthorizationCode(
            code=authorization_code,
            scopes=request.scopes,
            expires_at=request.code_expires_at.timestamp(),
            client_id=request.client_id,
            code_challenge=request.code_challenge,
            redirect_uri=request.redirect_uri,  # type: ignore[arg-type]
            redirect_uri_provided_explicitly=request.redirect_uri_provided_explicitly,
            resource=request.resource,
            request_id=request.id,
            user_id=request.user_id,
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        """Consume the code once and start a session for the approving user."""
        # Always the subclass returned by ``load_authorization_code``.
        authorization_code = cast(OrcheoAuthorizationCode, authorization_code)
        try:
            await run_in_threadpool(
                self._repository.consume_authorization_code,
                authorization_code.request_id,
                consumed_at=self._clock(),
            )
        except OAuthAuthorizationRequestNotFoundError as exc:
            raise TokenError(
                error="invalid_grant",
                error_description="authorization code has already been used",
            ) from exc
        try:
            tokens = await run_in_threadpool(
                self._service.issue_oauth_session,
                authorization_code.user_id,
                client_id=client_id_of(client),
                scopes=authorization_code.scopes,
            )
        except IdentitySessionNotFoundError as exc:
            raise TokenError(error="invalid_grant", error_description=str(exc)) from exc
        return _to_token(tokens, authorization_code.scopes)

    # -- refresh & revocation -------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        """Return the active grant behind ``refresh_token`` for ``client``."""
        session = await run_in_threadpool(
            self._service.find_oauth_session,
            refresh_token,
            client_id=client_id_of(client),
        )
        if session is None:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=client_id_of(client),
            scopes=session.scopes or [],
            expires_at=int(session.expires_at.timestamp()),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Rotate the refresh token, keeping or narrowing its scopes."""
        try:
            tokens = await run_in_threadpool(
                self._service.refresh_oauth_session,
                refresh_token.token,
                client_id=client_id_of(client),
                scopes=scopes,
            )
        except IdentitySessionNotFoundError as exc:
            raise TokenError(error="invalid_grant", error_description=str(exc)) from exc
        return _to_token(tokens, scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Access tokens are self-contained JWTs, so none are tracked here.

        ``/api/mcp`` authenticates bearer tokens through Orcheo's own auth
        stack instead of this provider, so FastMCP never verifies with it.
        """
        del token
        return None

    async def revoke_token(self, token: Any) -> None:
        """Revoke the grant behind a refresh token (RFC 7009)."""
        if isinstance(token, RefreshToken):
            await run_in_threadpool(
                self._service.revoke_oauth_session,
                token.token,
                client_id=token.client_id,
            )


__all__ = [
    "AUTHORIZATION_CODE_TTL",
    "AUTHORIZATION_REQUEST_TTL",
    "OrcheoAuthorizationCode",
    "OrcheoOAuthProvider",
    "derive_client_secret",
]
