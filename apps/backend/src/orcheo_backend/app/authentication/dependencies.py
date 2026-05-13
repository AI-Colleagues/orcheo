from __future__ import annotations
import json
from datetime import UTC, datetime
from sys import modules
from typing import Any
from fastapi import Request, WebSocket
from orcheo.config import get_settings
from .authenticator import Authenticator
from .context import RequestContext
from .errors import AuthenticationError
from .rate_limit import AuthRateLimiter
from .service_tokens import ServiceTokenManager
from .settings import AuthSettings, load_auth_settings
from .telemetry import auth_telemetry


_authenticator_cache: dict[str, Authenticator | None] = {"authenticator": None}
_auth_rate_limiter_cache: dict[str, AuthRateLimiter | None] = {"limiter": None}
_token_manager_cache: dict[str, ServiceTokenManager | None] = {"manager": None}


def get_authenticator(*, refresh: bool = False) -> Authenticator:
    """Return a cached Authenticator instance, reloading settings when required."""
    if refresh:
        _authenticator_cache["authenticator"] = None
        _auth_rate_limiter_cache["limiter"] = None
        _token_manager_cache["manager"] = None
    authenticator = _authenticator_cache.get("authenticator")
    if authenticator is None:
        settings = load_auth_settings(refresh=refresh)

        from orcheo_backend.app.service_token_repository import (
            InMemoryServiceTokenRepository,
            PostgresServiceTokenRepository,
            SqliteServiceTokenRepository,
        )

        if settings.service_token_backend == "postgres":
            dsn = get_settings().get("POSTGRES_DSN")
            if not dsn:
                msg = "ORCHEO_POSTGRES_DSN must be set when using the postgres backend."
                raise ValueError(msg)
            repository: Any = PostgresServiceTokenRepository(dsn)
        elif settings.service_token_db_path:
            repository = SqliteServiceTokenRepository(settings.service_token_db_path)
        else:
            repository = InMemoryServiceTokenRepository()

        token_manager = ServiceTokenManager(repository)
        _token_manager_cache["manager"] = token_manager

        authenticator = Authenticator(settings, token_manager)
        _authenticator_cache["authenticator"] = authenticator
        _auth_rate_limiter_cache["limiter"] = AuthRateLimiter(
            ip_limit=settings.rate_limit_ip,
            identity_limit=settings.rate_limit_identity,
            interval_seconds=settings.rate_limit_interval,
        )
    return authenticator


def get_service_token_manager(*, refresh: bool = False) -> ServiceTokenManager:
    """Return the cached ServiceTokenManager instance."""
    if refresh:
        _token_manager_cache["manager"] = None
    api = modules["orcheo_backend.app.authentication"]
    api.get_authenticator(refresh=refresh)
    manager = _token_manager_cache.get("manager")
    if manager is None:
        raise RuntimeError("ServiceTokenManager not initialized")
    return manager


def get_auth_rate_limiter(*, refresh: bool = False) -> AuthRateLimiter:
    """Return the configured authentication rate limiter."""
    if refresh:
        _auth_rate_limiter_cache["limiter"] = None
    limiter = _auth_rate_limiter_cache.get("limiter")
    if limiter is None:
        settings = load_auth_settings(refresh=refresh)
        limiter = AuthRateLimiter(
            ip_limit=settings.rate_limit_ip,
            identity_limit=settings.rate_limit_identity,
            interval_seconds=settings.rate_limit_interval,
        )
        _auth_rate_limiter_cache["limiter"] = limiter
    return limiter


def reset_authentication_state() -> None:
    """Clear cached authentication state and refresh Dynaconf settings."""
    _authenticator_cache["authenticator"] = None
    _auth_rate_limiter_cache["limiter"] = None
    get_settings(refresh=True)


def _extract_bearer_token(header_value: str | None) -> str:
    if not header_value:
        raise AuthenticationError("Missing bearer token", code="auth.missing_token")
    parts = header_value.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthenticationError(
            "Authorization header must use the Bearer scheme",
            code="auth.invalid_scheme",
        )
    token = parts[1].strip()
    if not token:
        raise AuthenticationError("Missing bearer token", code="auth.missing_token")
    return token


def _extract_websocket_protocol_token(
    header_value: str | None,
) -> tuple[str | None, str | None]:
    if not header_value:
        return None, None
    protocols = [value.strip() for value in header_value.split(",") if value.strip()]
    if not protocols:
        return None, None
    selected = "orcheo-auth" if "orcheo-auth" in protocols else None
    token = None
    for protocol in protocols:
        if protocol.startswith("bearer."):
            token = protocol.removeprefix("bearer.").strip()
            if token:
                break
            token = None
    return token, selected


def _resolve_websocket_token(websocket: WebSocket) -> tuple[str | None, str | None]:
    protocol_header = websocket.headers.get("sec-websocket-protocol")
    protocol_token, subprotocol = _extract_websocket_protocol_token(protocol_header)
    query_params = getattr(websocket, "query_params", None)
    query_token = None
    if query_params is not None:
        raw_query_token = query_params.get("access_token")
        if isinstance(raw_query_token, str) and raw_query_token.strip():
            query_token = raw_query_token.strip()

    header_value = websocket.headers.get("authorization")
    if header_value:
        return _extract_bearer_token(header_value), subprotocol
    if protocol_token:
        return protocol_token, subprotocol
    if query_token:
        return query_token, subprotocol

    return None, subprotocol


def _build_dev_context(identity: str, settings: AuthSettings) -> RequestContext:
    scopes = (
        frozenset(settings.dev_login_scopes)
        if settings.dev_login_scopes
        else frozenset()
    )
    if not scopes:
        scopes = frozenset(
            [
                "workflows:read",
                "workflows:write",
                "workflows:execute",
                "vault:read",
                "vault:write",
            ]
        )
    workspace_ids = (
        frozenset(settings.dev_login_workspace_ids)
        if settings.dev_login_workspace_ids
        else frozenset()
    )
    return RequestContext(
        subject=identity,
        identity_type="developer",
        scopes=scopes,
        workspace_ids=workspace_ids,
    )


def _parse_dev_identity(raw_value: object) -> str | None:
    if not isinstance(raw_value, str):
        return None
    candidate = raw_value.strip()
    if not candidate:
        return None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        subject = parsed.get("subject")
        if isinstance(subject, str):
            subject = subject.strip()
            if subject:
                return subject
    return candidate.split(":", 1)[0].strip() or None


def _try_dev_login_session(
    scope: Request | WebSocket, settings: AuthSettings
) -> RequestContext | None:
    if not settings.dev_login_enabled:
        return None
    headers = getattr(scope, "headers", None) or {}
    raw_value = headers.get("x-orcheo-dev-session")
    if raw_value is None:
        cookie_name = settings.dev_login_cookie_name
        if not cookie_name:
            return None
        cookies = getattr(scope, "cookies", None) or {}
        raw_value = cookies.get(cookie_name)
    if not raw_value:
        return None
    identity = _parse_dev_identity(raw_value)
    if not identity:
        return None
    return _build_dev_context(identity, settings)


async def authenticate_request(request: Request) -> RequestContext:
    """FastAPI dependency that enforces authentication on HTTP requests."""
    api = modules["orcheo_backend.app.authentication"]
    authenticator = api.get_authenticator()
    limiter = api.get_auth_rate_limiter()
    ip = request.client.host if request.client else None
    now = datetime.now(tz=UTC)
    _enforce_ip_limit(limiter, ip, now)

    auth_header = request.headers.get("Authorization")
    token, auth_error = _parse_authorization_header(auth_header)

    context = await _attempt_bearer_auth_optional(
        authenticator, limiter, token, ip, now
    )
    if context is not None:
        request.state.auth = context
        return context

    dev_context = _try_dev_login_session(request, authenticator.settings)
    if dev_context:
        request.state.auth = dev_context
        return dev_context

    if not authenticator.settings.enforce:
        context = RequestContext.anonymous()
        request.state.auth = context
        return context

    if auth_error is not None:
        auth_telemetry.record_auth_failure(reason=auth_error.code, ip=ip)
        raise auth_error.as_http_exception() from auth_error

    missing_error = AuthenticationError(
        "Missing bearer token", code="auth.missing_token"
    )
    auth_telemetry.record_auth_failure(reason=missing_error.code, ip=ip)
    raise missing_error.as_http_exception() from missing_error


def _enforce_ip_limit(limiter: AuthRateLimiter, ip: str | None, now: datetime) -> None:
    try:
        limiter.check_ip(ip, now=now)
    except AuthenticationError as exc:
        raise exc.as_http_exception() from exc


def _parse_authorization_header(
    header_value: str | None,
) -> tuple[str | None, AuthenticationError | None]:
    if not header_value:
        return None, None
    try:
        return _extract_bearer_token(header_value), None
    except AuthenticationError as exc:
        return None, exc


async def _attempt_bearer_auth_optional(
    authenticator: Authenticator,
    limiter: AuthRateLimiter,
    token: str | None,
    ip: str | None,
    now: datetime,
) -> RequestContext | None:
    if not token:
        return None
    try:
        context = await authenticator.authenticate(token)
    except AuthenticationError as exc:
        if authenticator.settings.enforce:
            auth_telemetry.record_auth_failure(reason=exc.code, ip=ip)
            raise exc.as_http_exception() from exc
        return None
    try:
        limiter.check_identity(context.token_id or context.subject, now=now)
    except AuthenticationError as exc:
        raise exc.as_http_exception() from exc
    auth_telemetry.record_auth_success(context, ip=ip)
    return context


async def authenticate_websocket(websocket: WebSocket) -> RequestContext:  # noqa: PLR0915
    """Authenticate a WebSocket connection before accepting it."""
    api = modules["orcheo_backend.app.authentication"]
    authenticator = api.get_authenticator()
    if not authenticator.settings.enforce:
        context = RequestContext.anonymous()
        websocket.state.auth = context
        return context

    limiter = api.get_auth_rate_limiter()
    ip = websocket.client.host if websocket.client else None
    now = datetime.now(tz=UTC)
    try:
        limiter.check_ip(ip, now=now)
    except AuthenticationError as exc:
        auth_telemetry.record_auth_failure(reason=exc.code, ip=ip)
        await websocket.close(code=exc.websocket_code, reason=exc.message)
        raise

    token: str | None = None
    try:
        token, subprotocol = _resolve_websocket_token(websocket)
        if subprotocol:
            websocket.state.subprotocol = subprotocol
    except AuthenticationError as exc:
        auth_telemetry.record_auth_failure(reason=exc.code, ip=ip)
        await websocket.close(code=exc.websocket_code, reason=exc.message)
        raise

    if not token:
        dev_context = _try_dev_login_session(websocket, authenticator.settings)
        if dev_context:
            websocket.state.auth = dev_context
            return dev_context
        auth_telemetry.record_auth_failure(reason="auth.missing_token", ip=ip)
        await websocket.close(code=4401, reason="Missing bearer token")
        raise AuthenticationError("Missing bearer token", code="auth.missing_token")

    try:
        context = await authenticator.authenticate(token)
    except AuthenticationError as exc:
        auth_telemetry.record_auth_failure(reason=exc.code, ip=ip)
        await websocket.close(code=exc.websocket_code, reason=exc.message)
        raise

    try:
        limiter.check_identity(context.token_id or context.subject, now=now)
    except AuthenticationError as exc:
        auth_telemetry.record_auth_failure(reason=exc.code, ip=ip)
        await websocket.close(code=exc.websocket_code, reason=exc.message)
        raise
    auth_telemetry.record_auth_success(context, ip=ip)
    websocket.state.auth = context
    return context


async def get_request_context(request: Request) -> RequestContext:
    """Retrieve the RequestContext associated with the current request."""
    context = getattr(request.state, "auth", None)
    if isinstance(context, RequestContext):
        return context
    return await authenticate_request(request)
