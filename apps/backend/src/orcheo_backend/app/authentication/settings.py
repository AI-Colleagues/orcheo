from __future__ import annotations
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from orcheo.config import get_settings
from .utils import coerce_str_items, parse_timestamp


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthSettings:
    """Resolved authentication configuration for the backend."""

    mode: str
    jwt_secret: str | None
    jwks_url: str | None
    jwks_static: tuple[Mapping[str, Any], ...]
    jwks_cache_ttl: int
    jwks_timeout: float
    allowed_algorithms: tuple[str, ...]
    audiences: tuple[str, ...]
    issuer: str | None
    rate_limit_ip: int
    rate_limit_identity: int
    rate_limit_interval: int
    service_token_backend: str
    service_token_db_path: str | None
    bootstrap_service_token: str | None = None
    bootstrap_token_scopes: frozenset[str] = field(default_factory=frozenset)
    bootstrap_token_expires_at: datetime | None = None
    dev_login_enabled: bool = False
    dev_login_cookie_name: str | None = None
    dev_login_scopes: tuple[str, ...] = field(default_factory=tuple)
    dev_login_workspace_ids: tuple[str, ...] = field(default_factory=tuple)
    configured_mode: str | None = None
    public_exposure_detected: bool = False
    public_exposure_sources: tuple[str, ...] = field(default_factory=tuple)

    @property
    def enforce(self) -> bool:
        """Return True when authentication should be enforced for requests."""
        if self.mode == "disabled":
            return False
        if self.mode == "required":
            return True
        return bool(
            self.jwt_secret
            or self.jwks_url
            or self.jwks_static
            or self.service_token_db_path
            or self.bootstrap_service_token
        )


_DEFAULT_ALGORITHMS: tuple[str, ...] = ("RS256", "HS256")
_DEV_DEFAULT_SCOPES: tuple[str, ...] = (
    "workflows:read",
    "workflows:write",
    "workflows:execute",
    "vault:read",
    "vault:write",
)


def load_auth_settings(*, refresh: bool = False) -> AuthSettings:
    """Load authentication settings from Dynaconf and environment variables."""
    settings = get_settings(refresh=refresh)
    configured_mode = _coerce_mode(settings.get("AUTH_MODE", "optional"))
    jwt_secret = _coerce_optional_str(settings.get("AUTH_JWT_SECRET"))
    jwks_url = _coerce_optional_str(settings.get("AUTH_JWKS_URL"))
    jwks_cache_ttl = _parse_int(settings.get("AUTH_JWKS_CACHE_TTL"), 300)
    jwks_timeout = _parse_float(settings.get("AUTH_JWKS_TIMEOUT"), 5.0)

    jwks_raw = settings.get("AUTH_JWKS") or settings.get("AUTH_JWKS_STATIC")
    jwks_static = tuple(dict(item) for item in _parse_jwks(jwks_raw))

    allowed_algorithms = _parse_str_sequence(settings.get("AUTH_ALLOWED_ALGORITHMS"))
    if not allowed_algorithms:
        allowed_algorithms = _DEFAULT_ALGORITHMS

    audiences = _parse_str_sequence(settings.get("AUTH_AUDIENCE"))
    issuer = _normalize_issuer(settings.get("AUTH_ISSUER"))
    service_token_backend = _coerce_mode_backend(
        settings.get("AUTH_SERVICE_TOKEN_BACKEND", "sqlite")
    )
    service_token_db_path = _resolve_service_token_db_path(settings)
    rate_limit_ip = _parse_int(settings.get("AUTH_RATE_LIMIT_IP"), 0)
    rate_limit_identity = _parse_int(settings.get("AUTH_RATE_LIMIT_IDENTITY"), 0)
    rate_limit_interval = _parse_int(settings.get("AUTH_RATE_LIMIT_INTERVAL"), 60)
    (
        bootstrap_service_token,
        bootstrap_token_scopes,
        bootstrap_token_expires_at,
    ) = _load_bootstrap_token_settings(settings)
    public_exposure_sources = _detect_public_exposure(settings)
    mode = _resolve_effective_mode(configured_mode, public_exposure_sources)
    has_auth_credentials = _has_auth_credentials(
        jwt_secret=jwt_secret,
        jwks_url=jwks_url,
        jwks_static=jwks_static,
        service_token_backend=service_token_backend,
        service_token_db_path=service_token_db_path,
        bootstrap_service_token=bootstrap_service_token,
    )
    _validate_required_auth_configuration(mode, has_auth_credentials)
    _validate_public_auth_configuration(public_exposure_sources, has_auth_credentials)
    (
        dev_login_enabled,
        dev_cookie_name,
        dev_scopes,
        dev_workspace_ids,
    ) = _load_dev_login_settings(settings, public_exposure_sources)

    return AuthSettings(
        mode=mode,
        configured_mode=configured_mode,
        jwt_secret=jwt_secret,
        jwks_url=jwks_url,
        jwks_static=tuple(jwks_static),
        jwks_cache_ttl=jwks_cache_ttl,
        jwks_timeout=jwks_timeout,
        allowed_algorithms=tuple(allowed_algorithms),
        audiences=tuple(audiences),
        issuer=issuer,
        service_token_backend=service_token_backend,
        service_token_db_path=service_token_db_path,
        bootstrap_service_token=bootstrap_service_token,
        bootstrap_token_scopes=bootstrap_token_scopes,
        bootstrap_token_expires_at=bootstrap_token_expires_at,
        rate_limit_ip=rate_limit_ip,
        rate_limit_identity=rate_limit_identity,
        rate_limit_interval=rate_limit_interval,
        dev_login_enabled=dev_login_enabled,
        dev_login_cookie_name=dev_cookie_name,
        dev_login_scopes=tuple(dev_scopes),
        dev_login_workspace_ids=tuple(dev_workspace_ids),
        public_exposure_detected=bool(public_exposure_sources),
        public_exposure_sources=public_exposure_sources,
    )


def _normalize_issuer(value: Any) -> str | None:
    """Return a canonical issuer URL without a trailing slash."""
    issuer = _coerce_optional_str(value)
    if issuer is None:
        return None
    return issuer.rstrip("/")


def _resolve_service_token_db_path(settings: Any) -> str | None:
    service_token_db_path = _coerce_optional_str(
        settings.get("AUTH_SERVICE_TOKEN_DB_PATH")
    )
    if service_token_db_path:
        return service_token_db_path

    repo_path = settings.get("ORCHEO_REPOSITORY_SQLITE_PATH")
    if not repo_path:
        return None

    db_path = Path(str(repo_path)).expanduser()
    return str(db_path.parent / "service_tokens.sqlite")


def _load_bootstrap_token_settings(
    settings: Any,
) -> tuple[str | None, frozenset[str], datetime | None]:
    bootstrap_service_token = _coerce_optional_str(
        settings.get("AUTH_BOOTSTRAP_SERVICE_TOKEN")
    )
    bootstrap_token_scopes = _resolve_bootstrap_token_scopes(
        settings.get("AUTH_BOOTSTRAP_TOKEN_SCOPES")
    )
    bootstrap_token_expires_at = _resolve_bootstrap_token_expiration(
        settings.get("AUTH_BOOTSTRAP_TOKEN_EXPIRES_AT")
    )

    if bootstrap_service_token:
        logger.warning(
            "Bootstrap service token is configured. This should only be used for "
            "initial setup and should be removed after creating persistent tokens."
        )

    return (
        bootstrap_service_token,
        bootstrap_token_scopes,
        bootstrap_token_expires_at,
    )


def _resolve_bootstrap_token_scopes(value: Any) -> frozenset[str]:
    if value:
        return frozenset(_parse_str_sequence(value))
    return frozenset(
        {
            "admin:tokens:read",
            "admin:tokens:write",
            "workflows:read",
            "workflows:write",
            "workflows:execute",
            "vault:read",
            "vault:write",
        }
    )


def _resolve_bootstrap_token_expiration(value: Any) -> datetime | None:
    expires_at = parse_timestamp(value)
    if value and expires_at is None:
        logger.warning(  # pragma: no cover - defensive
            "AUTH_BOOTSTRAP_TOKEN_EXPIRES_AT could not be parsed; expected ISO 8601 "
            "or UNIX timestamp"
        )
    return expires_at


def _resolve_effective_mode(
    configured_mode: str, public_exposure_sources: Sequence[str]
) -> str:
    if not public_exposure_sources or configured_mode == "required":
        return configured_mode

    logger.warning(
        "Public deployment detected via %s; overriding AUTH_MODE=%s to required.",
        ", ".join(public_exposure_sources),
        configured_mode,
    )
    return "required"


def _validate_required_auth_configuration(
    mode: str, has_auth_credentials: bool
) -> None:
    if mode == "required" and not has_auth_credentials:
        logger.warning(
            "AUTH_MODE=required but no authentication credentials are configured; "
            "all requests will be rejected",
        )


def _validate_public_auth_configuration(
    public_exposure_sources: Sequence[str], has_auth_credentials: bool
) -> None:
    if public_exposure_sources and not has_auth_credentials:
        sources = ", ".join(public_exposure_sources)
        raise RuntimeError(
            "Public deployment detected via "
            f"{sources} but no authentication credentials are configured."
        )


def _load_dev_login_settings(
    settings: Any,
    public_exposure_sources: Sequence[str],
) -> tuple[bool, str | None, tuple[str, ...], tuple[str, ...]]:
    dev_login_enabled = _parse_bool(settings.get("AUTH_DEV_LOGIN_ENABLED"), False)
    if public_exposure_sources and dev_login_enabled:
        sources = ", ".join(public_exposure_sources)
        raise RuntimeError(
            "Developer login cannot be enabled when public deployment is detected "
            f"via {sources}."
        )
    if not dev_login_enabled:
        return False, None, (), ()

    dev_cookie_name = (
        _coerce_optional_str(settings.get("AUTH_DEV_COOKIE_NAME"))
        or "orcheo_dev_session"
    )
    dev_scopes = _parse_str_sequence(settings.get("AUTH_DEV_SCOPES"))
    if not dev_scopes:
        dev_scopes = _DEV_DEFAULT_SCOPES
    dev_workspace_ids = _parse_str_sequence(settings.get("AUTH_DEV_WORKSPACE_IDS"))
    return True, dev_cookie_name, tuple(dev_scopes), tuple(dev_workspace_ids)


def _has_auth_credentials(
    *,
    jwt_secret: str | None,
    jwks_url: str | None,
    jwks_static: Sequence[Mapping[str, Any]],
    service_token_backend: str,
    service_token_db_path: str | None,
    bootstrap_service_token: str | None,
) -> bool:
    return bool(
        jwt_secret
        or jwks_url
        or jwks_static
        or service_token_db_path
        or service_token_backend == "postgres"
        or bootstrap_service_token
    )


def _detect_public_exposure(settings: Any) -> tuple[str, ...]:
    sources: list[str] = []
    chatkit_public_base_url = _coerce_optional_str(
        settings.get("CHATKIT_PUBLIC_BASE_URL")
    )
    if _is_public_url(chatkit_public_base_url):
        sources.append("CHATKIT_PUBLIC_BASE_URL")

    cors_allow_origins = os.getenv("ORCHEO_CORS_ALLOW_ORIGINS")
    if any(
        _is_public_url(origin) for origin in _parse_configured_urls(cors_allow_origins)
    ):
        sources.append("CORS_ALLOW_ORIGINS")

    return tuple(sources)


def _parse_configured_urls(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return ()
    candidate = raw.strip()
    if not candidate:
        return ()

    parsed: list[str] | str | None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        parsed = candidate

    if isinstance(parsed, list):
        return tuple(str(item).strip() for item in parsed if str(item).strip())
    if isinstance(parsed, str):
        return tuple(item.strip() for item in parsed.split(",") if item.strip())
    return ()


def _is_public_url(value: str | None) -> bool:
    if value is None:
        return False
    candidate = value.strip()
    if not candidate:
        return False
    if candidate == "*":
        return True

    parsed = urlparse(candidate)
    hostname = parsed.hostname
    if hostname is None:
        return False
    return _is_public_hostname(hostname)


def _is_public_hostname(hostname: str) -> bool:
    normalized = hostname.strip().strip("[]").rstrip(".").lower()
    if not normalized:
        return False
    if normalized in {"localhost"}:
        return False

    try:
        address = ip_address(normalized)
    except ValueError:
        if normalized.endswith((".local", ".localhost", ".localdomain", ".internal")):
            return False
        if "." not in normalized:
            return False
        return True

    return not (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


def _parse_jwks(raw: Any) -> list[Mapping[str, Any]]:
    """Parse JWKS configuration supporting string, mapping, or sequences."""
    data = raw
    if isinstance(raw, str):
        candidate = raw.strip()
        if not candidate:
            return []
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            logger.warning("Failed to parse AUTH_JWKS value as JSON")
            return []

    if isinstance(data, Mapping):
        keys = data.get("keys")
        return _normalize_jwk_list(keys)
    if isinstance(data, Sequence):
        return _normalize_jwk_list(data)
    return []


def _normalize_jwk_list(value: Any) -> list[Mapping[str, Any]]:
    """Return a normalized list of JWKS dictionaries."""
    if not isinstance(value, Sequence):
        return []
    normalized: list[Mapping[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            normalized.append(dict(item))
    return normalized


def _coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def _coerce_mode(value: Any) -> str:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"disabled", "required", "optional"}:
            return lowered
    return "optional"


def _coerce_mode_backend(value: Any) -> str:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"sqlite", "postgres", "inmemory"}:
            return lowered
    return "sqlite"


def _parse_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _parse_float(value: Any, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return default


def _parse_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return default


def _parse_str_sequence(value: Any) -> tuple[str, ...]:
    items = coerce_str_items(value)
    return tuple(item for item in items if item)
