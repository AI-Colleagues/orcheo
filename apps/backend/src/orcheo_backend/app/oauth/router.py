"""HTTP endpoints of the OAuth authorization server used by MCP clients.

FastMCP's ``OAuthProvider`` supplies the protocol endpoints and discovery
documents as Starlette routes built for one public origin. The catch-all
routes below forward ``/api/oauth/*`` and the OAuth well-known paths to that
route set, built and cached per origin; the consent API that Studio calls is
declared first so it takes precedence.
"""

from __future__ import annotations
import logging
from datetime import datetime
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlparse
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.routing import Mount
from orcheo.identity import OAuthAuthorizationRequestNotFoundError
from orcheo_backend.app.asgi_delegate import ASGIDelegateResponse
from orcheo_backend.app.authentication import (
    RequestContext,
    authenticate_request,
    is_oauth_client_context,
)
from orcheo_backend.app.identity.dependencies import (
    get_identity_config,
    get_identity_service,
)
from orcheo_backend.app.identity.service import IdentityService
from orcheo_backend.app.oauth.provider import OrcheoOAuthProvider, client_id_of
from orcheo_backend.app.oauth.urls import (
    MCP_RESOURCE_PATH,
    OAUTH_PREFIX,
    public_origin,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth", tags=["oauth"])
well_known_router = APIRouter(include_in_schema=False)


@lru_cache(maxsize=16)
def _build(
    origin: str,
    service: IdentityService,
    signing_secret: str,
    consent_url: str,
) -> tuple[OrcheoOAuthProvider, Starlette]:
    provider = OrcheoOAuthProvider(
        service,
        public_url=origin,
        signing_secret=signing_secret,
        consent_url=consent_url,
    )
    routes = provider.get_routes(MCP_RESOURCE_PATH)
    operational = [
        route
        for route in routes
        if not getattr(route, "path", "").startswith("/.well-known/")
    ]
    app = Starlette(
        routes=[
            Mount(OAUTH_PREFIX, routes=operational),
            *provider.get_well_known_routes(MCP_RESOURCE_PATH),
        ]
    )
    return provider, app


def oauth_server(request: Request) -> tuple[OrcheoOAuthProvider, Starlette] | None:
    """Return the provider and its routes for the request's public origin.

    Returns None when first-party tokens are not configured or the origin is
    not HTTPS, in which case MCP clients must use service tokens.
    """
    try:
        config = get_identity_config()
        service = get_identity_service()
    except ValueError:
        return None
    origin = public_origin(request)
    try:
        return _build(
            origin,
            service,
            config.jwt_secret,
            f"{config.verify_base_url.rstrip('/')}/oauth/consent",
        )
    except ValueError:
        # RFC 8414 issuers must use https (plain http only on localhost).
        logger.warning(
            "MCP OAuth sign-in is unavailable at %s: the backend must be served "
            "over HTTPS or set ORCHEO_PUBLIC_URL to its HTTPS origin.",
            origin,
        )
        return None


def _require_server(request: Request) -> tuple[OrcheoOAuthProvider, Starlette]:
    server = oauth_server(request)
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OAuth is not configured on this server.",
        )
    return server


def _require_provider(request: Request) -> OrcheoOAuthProvider:
    return _require_server(request)[0]


ProviderDep = Annotated[OrcheoOAuthProvider, Depends(_require_provider)]


# -- consent (called by Studio for the signed-in user) ------------------------


class AuthorizationRequestView(BaseModel):
    """What Studio shows the user before they approve a client."""

    request_id: str
    client_id: str
    client_name: str | None = None
    client_uri: str | None = None
    redirect_uri: str
    redirect_host: str
    scopes: list[str]
    expires_at: datetime


class AuthorizationDecision(BaseModel):
    """The user's answer on the consent screen."""

    approve: bool


class AuthorizationDecisionResponse(BaseModel):
    """Where Studio should send the browser next."""

    redirect_url: str


def _consenting_user(auth: RequestContext) -> UUID:
    # Only a first-party Studio session may consent: an OAuth client must never
    # approve its own (possibly wider) authorization request.
    if auth.identity_type == "user" and not is_oauth_client_context(auth):
        try:
            return UUID(auth.subject)
        except ValueError:
            pass
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Sign in with an Orcheo user account to authorize applications.",
    )


def _redirect_target(redirect_uri: str) -> str:
    """Name where approval sends the user, keeping any custom app scheme.

    A native-app redirect such as ``myapp://claude.ai/cb`` opens ``myapp``,
    not claude.ai, so its scheme is shown rather than just the host.
    """
    parsed = urlparse(redirect_uri)
    if parsed.scheme in {"http", "https"}:
        return parsed.netloc
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else parsed.scheme


def _request_gone() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="This authorization request has expired or was already handled.",
    )


@router.get("/requests/{request_id}", response_model=AuthorizationRequestView)
async def get_authorization_request(
    request_id: str,
    provider: ProviderDep,
    auth: Annotated[RequestContext, Depends(authenticate_request)],
) -> AuthorizationRequestView:
    """Describe a pending authorization request for the consent screen."""
    _consenting_user(auth)
    pending = await provider.pending_request(request_id)
    if pending is None:
        raise _request_gone()
    request, client = pending
    return AuthorizationRequestView(
        request_id=request.id,
        client_id=client_id_of(client),
        client_name=client.client_name,
        client_uri=None if client.client_uri is None else str(client.client_uri),
        redirect_uri=request.redirect_uri,
        redirect_host=_redirect_target(request.redirect_uri),
        scopes=request.scopes,
        expires_at=request.expires_at,
    )


@router.post(
    "/requests/{request_id}/decision",
    response_model=AuthorizationDecisionResponse,
)
async def decide_authorization_request(
    request_id: str,
    body: AuthorizationDecision,
    provider: ProviderDep,
    auth: Annotated[RequestContext, Depends(authenticate_request)],
) -> AuthorizationDecisionResponse:
    """Approve or deny a pending request as the signed-in user."""
    user_id = _consenting_user(auth)
    try:
        redirect_url = await provider.decide(
            request_id, user_id=user_id, approve=body.approve
        )
    except OAuthAuthorizationRequestNotFoundError as exc:
        raise _request_gone() from exc
    return AuthorizationDecisionResponse(redirect_url=redirect_url)


# -- protocol endpoints and discovery (served by FastMCP) ---------------------

_METHODS = ["GET", "POST", "OPTIONS"]


@router.api_route("/{path:path}", methods=_METHODS, include_in_schema=False)
async def oauth_endpoint(request: Request) -> Response:
    """Serve authorize, token, register and revoke via FastMCP's routes."""
    server = await run_in_threadpool(_require_server, request)
    return ASGIDelegateResponse(server[1])


@well_known_router.api_route("/.well-known/oauth-{document:path}", methods=_METHODS)
@well_known_router.api_route(
    "/.well-known/openid-configuration{suffix:path}", methods=_METHODS
)
async def oauth_discovery(request: Request) -> Response:
    """Serve RFC 8414 and RFC 9728 discovery documents."""
    server = await run_in_threadpool(_require_server, request)
    return ASGIDelegateResponse(server[1])


__all__ = ["oauth_server", "router", "well_known_router"]
