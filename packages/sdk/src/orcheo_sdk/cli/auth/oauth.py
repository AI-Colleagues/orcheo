"""OAuth 2.0 PKCE flow implementation for CLI."""

from __future__ import annotations
import base64
import hashlib
import http.server
import json
import secrets
import socketserver
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from typing import Any
import httpx
from rich.console import Console
from orcheo_sdk.cli.auth.config import OAuthConfig, get_oauth_config
from orcheo_sdk.cli.auth.tokens import AuthTokens, clear_oauth_tokens, set_oauth_tokens
from orcheo_sdk.cli.errors import CLIError


# Constants matching Studio implementation
STATE_BYTES = 32
VERIFIER_BYTES = 64
AUTH_STATE_TTL_SECONDS = 600  # 10 minutes
DEFAULT_CALLBACK_PORT = 8085


@dataclass(slots=True)
class OidcDiscovery:
    """OIDC discovery document endpoints."""

    authorization_endpoint: str
    token_endpoint: str
    end_session_endpoint: str | None = None


def _base64_url_encode(data: bytes) -> str:
    """Encode bytes to base64url without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _create_random_string(length: int) -> str:
    """Generate a cryptographically secure random string."""
    return _base64_url_encode(secrets.token_bytes(length))


def _create_code_challenge(verifier: str) -> str:
    """Create S256 code challenge from verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _base64_url_encode(digest)


def _load_discovery(issuer: str) -> OidcDiscovery:
    """Fetch OIDC discovery document."""
    url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        response = httpx.get(url, timeout=30.0)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        raise CLIError(f"Failed to load OAuth discovery: {exc}") from exc

    return OidcDiscovery(
        authorization_endpoint=data["authorization_endpoint"],
        token_endpoint=data["token_endpoint"],
        end_session_endpoint=data.get("end_session_endpoint"),
    )


def _parse_jwt_expiry(token: str) -> int | None:
    """Extract expiry timestamp from JWT without validation."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        # Add padding if needed
        payload_b64 = parts[1]
        padding_needed = 4 - len(payload_b64) % 4
        if padding_needed != 4:
            payload_b64 += "=" * padding_needed
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        if isinstance(exp, int | float):
            return int(exp * 1000)  # Convert to milliseconds
    except Exception:
        pass
    return None


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback."""

    authorization_code: str | None = None
    callback_state: str | None = None
    error: str | None = None

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress HTTP server logging."""

    def do_GET(self) -> None:  # noqa: N802
        """Handle OAuth callback GET request."""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "error" in params:
            _CallbackHandler.error = params["error"][0]
            self._send_response("Authentication failed. You can close this window.")
            return

        if "code" in params and "state" in params:
            _CallbackHandler.authorization_code = params["code"][0]
            _CallbackHandler.callback_state = params["state"][0]
            self._send_response("Authentication successful! You can close this window.")
        else:
            self._send_response("Invalid callback. Missing code or state.")

    def _send_response(self, message: str) -> None:
        """Send HTML response to browser."""
        html = f"""
        <!DOCTYPE html>
        <html>
        <head><title>Orcheo CLI</title></head>
        <body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h1>Orcheo CLI</h1>
            <p>{message}</p>
        </body>
        </html>
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


def start_oauth_login(
    *,
    console: Console,
    profile: str | None,
    no_browser: bool = False,
    port: int = DEFAULT_CALLBACK_PORT,
) -> None:
    """Execute browser-based OAuth login flow."""
    config = get_oauth_config(profile=profile)
    discovery = _load_discovery(config.issuer)

    # Generate PKCE parameters
    state = _create_random_string(STATE_BYTES)
    verifier = _create_random_string(VERIFIER_BYTES)
    challenge = _create_code_challenge(verifier)

    # Build authorization URL
    redirect_uri = f"http://localhost:{port}/callback"
    auth_params: dict[str, str] = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "scope": config.scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if config.audience:  # pragma: no branch
        auth_params["audience"] = config.audience
    if config.organization:  # pragma: no branch
        auth_params["organization"] = config.organization

    auth_url = (
        f"{discovery.authorization_endpoint}?{urllib.parse.urlencode(auth_params)}"
    )

    # Reset handler state
    _CallbackHandler.authorization_code = None
    _CallbackHandler.callback_state = None
    _CallbackHandler.error = None

    # Start callback server
    with socketserver.TCPServer(("", port), _CallbackHandler) as server:
        server.timeout = AUTH_STATE_TTL_SECONDS

        if no_browser:
            console.print(
                f"\nOpen this URL in your browser:\n[cyan]{auth_url}[/cyan]\n"
            )
        else:
            console.print("Opening browser for authentication...")
            webbrowser.open(auth_url)

        console.print(f"Waiting for callback on port {port}...")

        # Wait for callback with timeout
        start_time = time.time()
        while (time.time() - start_time) < AUTH_STATE_TTL_SECONDS:
            server.handle_request()
            if _CallbackHandler.authorization_code or _CallbackHandler.error:
                break

        if _CallbackHandler.error:
            raise CLIError(f"OAuth error: {_CallbackHandler.error}")

        if not _CallbackHandler.authorization_code:
            raise CLIError("Authentication timed out. Please try again.")

        if _CallbackHandler.callback_state != state:
            raise CLIError("OAuth state mismatch. Possible CSRF attack.")

        # Exchange code for tokens
        console.print("Exchanging authorization code for tokens...")
        tokens = _exchange_code(
            config=config,
            discovery=discovery,
            code=_CallbackHandler.authorization_code,
            verifier=verifier,
            redirect_uri=redirect_uri,
        )

        set_oauth_tokens(profile=profile, tokens=tokens)
        console.print("[green]Successfully authenticated![/green]")


def _exchange_code(
    *,
    config: OAuthConfig,
    discovery: OidcDiscovery,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> AuthTokens:
    """Exchange authorization code for tokens."""
    body = {
        "grant_type": "authorization_code",
        "client_id": config.client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }

    try:
        response = httpx.post(
            discovery.token_endpoint,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        raise CLIError(f"Token exchange failed: {exc}") from exc

    access_token = data.get("access_token")
    if not access_token:
        raise CLIError("Token response missing access_token")

    # Calculate expiry
    expires_at: int | None
    expires_in = data.get("expires_in")
    if isinstance(expires_in, int | float):
        expires_at = int((time.time() + expires_in) * 1000)
    else:
        expires_at = _parse_jwt_expiry(access_token) or _parse_jwt_expiry(
            data.get("id_token", "")
        )

    return AuthTokens(
        access_token=access_token,
        id_token=data.get("id_token"),
        refresh_token=data.get("refresh_token"),
        token_type=data.get("token_type", "Bearer"),
        expires_at=expires_at,
    )


def logout_oauth(*, profile: str | None) -> None:
    """Clear OAuth tokens for the given profile."""
    clear_oauth_tokens(profile=profile)
