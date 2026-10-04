"""Orcheo's replacements for FastMCP's registration and revocation endpoints."""

from __future__ import annotations
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from uuid import uuid4
from mcp.server.auth.middleware.client_auth import (
    AuthenticationError as ClientAuthenticationError,
)
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from orcheo_backend.app.authentication import (
    AuthenticationError,
    get_auth_rate_limiter,
)
from orcheo_backend.app.identity.dependencies import get_client_ip
from orcheo_backend.app.oauth.urls import DEFAULT_OAUTH_SCOPES, SUPPORTED_SCOPES


if TYPE_CHECKING:
    from orcheo_backend.app.oauth.provider import OrcheoOAuthProvider

Endpoint = Callable[[Request], Awaitable[Response]]

TOKEN_ENDPOINT_AUTH_METHODS = ("none", "client_secret_post", "client_secret_basic")
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
_FORBIDDEN_REDIRECT_SCHEMES = {
    "javascript",
    "data",
    "file",
    "vbscript",
    "about",
    "blob",
}
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _registration_error(description: str) -> JSONResponse:
    return JSONResponse(
        {"error": "invalid_client_metadata", "error_description": description},
        status_code=400,
    )


def _redirect_uri_problem(uri: str) -> str | None:
    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()
    if parsed.fragment:
        return f"Redirect URI '{uri}' must not contain a fragment."
    if scheme == "https":
        return None
    if scheme == "http":
        if (parsed.hostname or "") in _LOOPBACK_HOSTS:
            return None
        return f"Redirect URI '{uri}' must use https unless it targets loopback."
    if scheme in _FORBIDDEN_REDIRECT_SCHEMES:
        return f"Redirect URI scheme '{scheme}' is not allowed."
    # Private-use schemes (e.g. ``cursor://``) identify native apps (RFC 8252).
    return None


def _metadata_problem(metadata: OAuthClientMetadata) -> str | None:
    for uri in metadata.redirect_uris or []:
        problem = _redirect_uri_problem(str(uri))
        if problem is not None:
            return problem
    method = metadata.token_endpoint_auth_method
    if method is not None and method not in TOKEN_ENDPOINT_AUTH_METHODS:
        return f"token_endpoint_auth_method '{method}' is not supported."
    grant_types = set(metadata.grant_types)
    if "authorization_code" not in grant_types or not grant_types <= {
        "authorization_code",
        "refresh_token",
    }:
        return "grant_types must be authorization_code, optionally with refresh_token."
    if "code" not in metadata.response_types:
        return "response_types must include 'code'."
    unsupported = set((metadata.scope or "").split()) - set(SUPPORTED_SCOPES)
    if unsupported:
        return f"Unsupported scopes: {', '.join(sorted(unsupported))}."
    return None


def registration_endpoint(provider: OrcheoOAuthProvider) -> Endpoint:
    """Return the dynamic client registration endpoint (RFC 7591).

    Confidential clients get a secret derived from the server's signing
    secret, so client secrets never need to be stored.
    """

    async def register(request: Request) -> Response:
        try:
            get_auth_rate_limiter().check_ip(
                get_client_ip(request), now=datetime.now(tz=UTC)
            )
        except AuthenticationError as exc:
            return JSONResponse(
                {"error": "slow_down", "error_description": exc.message},
                status_code=exc.status_code,
            )
        try:
            metadata = OAuthClientMetadata.model_validate(await request.json())
        except (ValueError, ValidationError) as exc:
            return _registration_error(str(exc))
        problem = _metadata_problem(metadata)
        if problem is not None:
            return _registration_error(problem)

        client_id = str(uuid4())
        method = metadata.token_endpoint_auth_method or "client_secret_post"
        fields: dict[str, Any] = metadata.model_dump(exclude_none=True)
        fields.update(
            client_id=client_id,
            client_id_issued_at=int(time.time()),
            token_endpoint_auth_method=method,
            scope=metadata.scope or " ".join(DEFAULT_OAUTH_SCOPES),
        )
        if method != "none":
            fields.update(
                client_secret=provider.client_secret_for(client_id),
                client_secret_expires_at=0,
            )
        client = OAuthClientInformationFull.model_validate(fields)
        await provider.register_client(client)
        return JSONResponse(
            client.model_dump(mode="json", exclude_none=True), status_code=201
        )

    return register


def revocation_endpoint(provider: OrcheoOAuthProvider) -> Endpoint:
    """Return the token revocation endpoint (RFC 7009)."""

    async def revoke(request: Request) -> Response:
        try:
            client = await ClientAuthenticator(provider).authenticate_request(request)
        except ClientAuthenticationError as exc:
            return JSONResponse(
                {"error": "invalid_client", "error_description": exc.message},
                status_code=401,
            )
        token_value = (await request.form()).get("token")
        if not isinstance(token_value, str) or not token_value:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "token is required"},
                status_code=400,
            )
        refresh_token = await provider.load_refresh_token(client, token_value)
        if refresh_token is not None:
            await provider.revoke_token(refresh_token)
        # Unknown tokens and access tokens (self-contained JWTs that expire on
        # their own) still get 200, per RFC 7009.
        return Response(status_code=200, headers=_NO_STORE)

    return revoke


__all__ = ["registration_endpoint", "revocation_endpoint"]
