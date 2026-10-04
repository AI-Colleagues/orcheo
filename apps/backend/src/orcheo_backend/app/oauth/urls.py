"""Public URLs and discovery documents for the OAuth authorization server.

The backend is both the MCP resource server (``/api/mcp``) and the OAuth
authorization server that lets MCP clients obtain user tokens through the
Studio login. Its endpoints sit under ``/api/oauth`` next to the rest of the
API, while the RFC 8414 / RFC 9728 discovery documents live at the
path-aware well-known locations on the origin root.
"""

from __future__ import annotations
from starlette.requests import Request
from orcheo.config import get_settings
from orcheo_backend.app.identity.dependencies import trusted_proxy_enabled
from orcheo_backend.app.identity.tokens import DEFAULT_USER_SCOPES


MCP_RESOURCE_PATH = "/api/mcp"
OAUTH_PREFIX = "/api/oauth"
PROTECTED_RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource"
SUPPORTED_SCOPES: tuple[str, ...] = DEFAULT_USER_SCOPES
# Publishing is available to first-party sessions, but third-party clients must
# request it explicitly when registering and obtain the user's consent.
DEFAULT_OAUTH_SCOPES: tuple[str, ...] = tuple(
    scope for scope in SUPPORTED_SCOPES if scope != "apps:publish"
)


def _first_forwarded(value: str | None) -> str | None:
    if not value:
        return None
    first = value.split(",", 1)[0].strip()
    return first or None


def public_origin(request: Request) -> str:
    """Return the origin clients use to reach this backend.

    ``ORCHEO_PUBLIC_URL`` wins when set. Otherwise ``X-Forwarded-Proto`` and
    ``X-Forwarded-Host`` are honored only behind a trusted proxy
    (``ORCHEO_TRUSTED_PROXY``), falling back to the request's own origin.
    """
    configured = get_settings().get("PUBLIC_URL")
    if configured and str(configured).strip():
        return str(configured).strip().rstrip("/")
    scheme = request.url.scheme
    host = request.url.netloc
    if trusted_proxy_enabled():
        scheme = _first_forwarded(request.headers.get("x-forwarded-proto")) or scheme
        host = _first_forwarded(request.headers.get("x-forwarded-host")) or host
    return f"{scheme}://{host}"


def resource_metadata_url(origin: str) -> str:
    """Return where the MCP server's protected resource metadata lives."""
    return f"{origin}{PROTECTED_RESOURCE_METADATA_PATH}{MCP_RESOURCE_PATH}"


__all__ = [
    "DEFAULT_OAUTH_SCOPES",
    "MCP_RESOURCE_PATH",
    "OAUTH_PREFIX",
    "PROTECTED_RESOURCE_METADATA_PATH",
    "SUPPORTED_SCOPES",
    "public_origin",
    "resource_metadata_url",
]
