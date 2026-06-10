"""OAuth token storage and management."""

from __future__ import annotations
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from orcheo_sdk.cli.config import get_config_dir


TOKEN_EXPIRY_SKEW_MS = 60_000  # 60 seconds, matching Studio


@dataclass(slots=True)
class AuthTokens:
    """OAuth token set."""

    access_token: str
    id_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_at: int | None = None  # Unix timestamp in milliseconds


def _get_tokens_dir() -> Path:
    """Return the tokens storage directory."""
    return get_config_dir() / "tokens"


def _get_token_path(profile: str | None) -> Path:
    """Return the token file path for a profile."""
    profile_name = profile or "default"
    return _get_tokens_dir() / f"{profile_name}.json"


def get_oauth_tokens(*, profile: str | None) -> AuthTokens | None:
    """Load OAuth tokens from disk for the given profile."""
    path = _get_token_path(profile)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("access_token"):
            return None
        return AuthTokens(
            access_token=data["access_token"],
            id_token=data.get("id_token"),
            refresh_token=data.get("refresh_token"),
            token_type=data.get("token_type", "Bearer"),
            expires_at=data.get("expires_at"),
        )
    except (json.JSONDecodeError, KeyError):
        return None


def set_oauth_tokens(*, profile: str | None, tokens: AuthTokens) -> None:
    """Persist OAuth tokens to disk for the given profile."""
    tokens_dir = _get_tokens_dir()
    tokens_dir.mkdir(parents=True, exist_ok=True)

    path = _get_token_path(profile)
    data = {k: v for k, v in asdict(tokens).items() if v is not None}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # Set restrictive permissions (owner read/write only)
    path.chmod(0o600)


def clear_oauth_tokens(*, profile: str | None) -> None:
    """Remove OAuth tokens for the given profile."""
    path = _get_token_path(profile)
    if path.exists():
        path.unlink()


def is_oauth_token_valid(tokens: AuthTokens | None) -> bool:
    """Check if tokens exist and are not expired (with skew)."""
    if not tokens or not tokens.access_token:
        return False
    if tokens.expires_at is None:
        return True  # No expiry means valid
    now_ms = int(time.time() * 1000)
    return now_ms < (tokens.expires_at - TOKEN_EXPIRY_SKEW_MS)


def get_access_token_if_valid(*, profile: str | None) -> str | None:
    """Return access token if valid, None otherwise."""
    tokens = get_oauth_tokens(profile=profile)
    if tokens and is_oauth_token_valid(tokens):
        return tokens.access_token
    return None


def get_token_expiry_display(tokens: AuthTokens) -> str:
    """Format token expiry for display."""
    if tokens.expires_at is None:
        return "Never"

    expiry_dt = datetime.fromtimestamp(tokens.expires_at / 1000, tz=UTC)
    now = datetime.now(tz=UTC)

    if expiry_dt < now:
        return "[red]Expired[/red]"

    delta = expiry_dt - now
    if delta.days > 0:
        return f"in {delta.days} days"
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours > 0:
        return f"in {hours}h {minutes}m"
    return f"in {minutes}m"
