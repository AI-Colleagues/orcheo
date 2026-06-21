"""Authentication subsystem public API exports."""

from __future__ import annotations
import httpx
from .authenticator import (
    Authenticator,
    JWKSFetcher,
)
from .context import RequestContext
from .dependencies import (
    _auth_rate_limiter_cache,
    _authenticator_cache,
    _extract_bearer_token,
    _token_manager_cache,
    authenticate_request,
    authenticate_websocket,
    get_auth_rate_limiter,
    get_authenticator,
    get_request_context,
    get_service_token_manager,
    reset_authentication_state,
)
from .errors import AuthenticationError, AuthorizationError
from .jwks import JWKSCache
from .jwt_authenticator import JWTAuthenticator
from .jwt_helpers import (
    _extract_scopes,
    _extract_workspace_ids,
    _infer_identity_type,
    _parse_max_age,
    claims_to_context,
    extract_email_verified,
    extract_identity,
)
from .policy import (
    AuthorizationPolicy,
    ensure_scopes,
    ensure_workspace_access,
    get_authorization_policy,
    require_scopes,
    require_workspace_access,
)
from .rate_limit import AuthRateLimiter, SlidingWindowRateLimiter
from .service_tokens import ServiceTokenManager, ServiceTokenRecord
from .settings import (
    AuthSettings,
    _coerce_mode,
    _coerce_mode_backend,
    _coerce_optional_str,
    _normalize_jwk_list,
    _parse_float,
    _parse_int,
    _parse_jwks,
    _parse_str_sequence,
    load_auth_settings,
)
from .telemetry import AuthEvent, AuthTelemetry, auth_telemetry
from .utils import (
    _coerce_from_mapping,
    _coerce_from_sequence,
    _coerce_from_string,
    coerce_str_items,
    parse_string_items,
)
from .utils import parse_timestamp as _parse_timestamp


_coerce_str_items = coerce_str_items
_parse_string_items = parse_string_items


__all__ = [
    "AuthEvent",
    "AuthRateLimiter",
    "AuthTelemetry",
    "AuthSettings",
    "AuthenticationError",
    "AuthorizationError",
    "AuthorizationPolicy",
    "Authenticator",
    "JWKSCache",
    "JWKSFetcher",
    "JWTAuthenticator",
    "RequestContext",
    "ServiceTokenManager",
    "ServiceTokenRecord",
    "SlidingWindowRateLimiter",
    "_auth_rate_limiter_cache",
    "_authenticator_cache",
    "_coerce_from_mapping",
    "_coerce_from_sequence",
    "_coerce_from_string",
    "_coerce_mode",
    "_coerce_mode_backend",
    "_coerce_optional_str",
    "_coerce_str_items",
    "_extract_bearer_token",
    "_extract_scopes",
    "_extract_workspace_ids",
    "_infer_identity_type",
    "_parse_float",
    "_parse_int",
    "_parse_jwks",
    "_parse_max_age",
    "_parse_str_sequence",
    "_parse_string_items",
    "_parse_timestamp",
    "_normalize_jwk_list",
    "_token_manager_cache",
    "claims_to_context",
    "extract_email_verified",
    "extract_identity",
    "auth_telemetry",
    "authenticate_request",
    "authenticate_websocket",
    "ensure_scopes",
    "ensure_workspace_access",
    "get_authorization_policy",
    "get_auth_rate_limiter",
    "get_authenticator",
    "get_request_context",
    "get_service_token_manager",
    "httpx",
    "load_auth_settings",
    "require_scopes",
    "require_workspace_access",
    "reset_authentication_state",
]
