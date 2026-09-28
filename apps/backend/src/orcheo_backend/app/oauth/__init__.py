"""OAuth 2.1 authorization server that lets MCP clients sign in via Studio."""

from orcheo_backend.app.oauth.router import (
    oauth_server,
    router,
    well_known_router,
)
from orcheo_backend.app.oauth.urls import public_origin, resource_metadata_url


__all__ = [
    "oauth_server",
    "public_origin",
    "resource_metadata_url",
    "router",
    "well_known_router",
]
