"""Streamable HTTP endpoint for the backend-hosted MCP server."""

from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from orcheo_backend.app.asgi_delegate import ASGIDelegateResponse
from orcheo_backend.app.authentication import (
    PREAUTHENTICATED_SCOPE_KEY,
    RequestContext,
    authenticate_oauth_resource_request,
)
from orcheo_backend.app.mcp_server.api_client import API_APP_SCOPE_KEY
from orcheo_backend.app.oauth import (
    oauth_server,
    public_origin,
    resource_metadata_url,
)


router = APIRouter()


async def authenticate_mcp_request(request: Request) -> RequestContext:
    """Authenticate an MCP request, pointing 401s at OAuth discovery.

    Per the MCP authorization spec, the ``WWW-Authenticate`` challenge names
    the protected resource metadata so clients can find the authorization
    server and start the Studio sign-in flow on their own. When the MCP server
    is disabled the endpoint 404s before authenticating, so clients are not
    sent through a sign-in they cannot use.
    """
    if getattr(request.app.state, "mcp_http_app", None) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The MCP server is disabled on this backend.",
        )
    try:
        return await authenticate_oauth_resource_request(request)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED and oauth_server(request):
            metadata_url = resource_metadata_url(public_origin(request))
            exc.headers = {
                **(exc.headers or {}),
                "WWW-Authenticate": f'Bearer resource_metadata="{metadata_url}"',
            }
        raise


@router.api_route(
    "/mcp",
    methods=["GET", "POST", "DELETE"],
    include_in_schema=False,
)
async def mcp_endpoint(
    request: Request,
    auth: Annotated[RequestContext, Depends(authenticate_mcp_request)],
) -> Response:
    """Serve MCP Streamable HTTP traffic for authenticated callers.

    The caller is authenticated once here. Tool calls re-enter the REST API
    in-process carrying this context, so they are authorized per workspace by
    the routes they proxy to without validating the token again.
    """
    request.scope[PREAUTHENTICATED_SCOPE_KEY] = auth
    request.scope[API_APP_SCOPE_KEY] = request.app
    return ASGIDelegateResponse(request.app.state.mcp_http_app)


__all__ = ["authenticate_mcp_request", "router"]
