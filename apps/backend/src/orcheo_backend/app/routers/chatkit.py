"""ChatKit-related FastAPI routes."""

from __future__ import annotations
import hmac
import inspect
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from importlib import import_module
from ipaddress import ip_address, ip_network
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, NamedTuple, cast
from uuid import UUID
import jwt
from chatkit.server import StreamingResult
from chatkit.types import ChatKitReq
from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import TypeAdapter, ValidationError
from starlette.responses import JSONResponse, StreamingResponse
import orcheo.config as orcheo_config
from orcheo.config.defaults import _DEFAULTS
from orcheo.models import WorkflowRun
from orcheo.vault.oauth import CredentialHealthError
from orcheo_backend.app.authentication import (
    AuthenticationError,
    AuthorizationPolicy,
    get_authorization_policy,
    load_auth_settings,
)
from orcheo_backend.app.authentication.rate_limit import SlidingWindowRateLimiter
from orcheo_backend.app.authentication.settings import AuthSettings
from orcheo_backend.app.authentication.utils import coerce_str_items
from orcheo_backend.app.chatkit import ChatKitRequestContext
from orcheo_backend.app.chatkit_asset_proxy import proxy_chatkit_asset
from orcheo_backend.app.chatkit_runtime import resolve_chatkit_token_issuer
from orcheo_backend.app.chatkit_tokens import (
    ChatKitSessionTokenIssuer,
    ChatKitTokenConfigurationError,
    load_chatkit_token_settings,
)
from orcheo_backend.app.dependencies import RepositoryDep, resolve_workflow_ref_id
from orcheo_backend.app.errors import raise_not_found
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.schemas.chatkit import (
    ChatKitSessionRequest,
    ChatKitSessionResponse,
    ChatKitWorkflowTriggerRequest,
)


router = APIRouter()

logger = logging.getLogger(__name__)


def _load_rate_limit_config() -> Mapping[str, Any]:
    settings = orcheo_config.get_settings()
    config = settings.get("CHATKIT_RATE_LIMITS")
    return config if isinstance(config, Mapping) else {}


def _coerce_rate_limit(config: Mapping[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_rate_limiter(
    config: Mapping[str, Any],
    *,
    limit_key: str,
    interval_key: str,
    default_limit: int,
    default_interval: int,
    code: str,
    message_template: str,
) -> SlidingWindowRateLimiter:
    limit = _coerce_rate_limit(config, limit_key, default_limit)
    interval = _coerce_rate_limit(config, interval_key, default_interval)
    return SlidingWindowRateLimiter(
        limit=limit,
        interval_seconds=interval,
        code=code,
        message_template=message_template,
    )


class _ChatKitRateLimiters(NamedTuple):
    ip: SlidingWindowRateLimiter
    jwt: SlidingWindowRateLimiter
    workflow: SlidingWindowRateLimiter
    session: SlidingWindowRateLimiter


_RATE_LIMITER_CACHE: dict[str, Any] = {
    "signature": None,
    "limiters": None,
}


def _rate_limiter_signature(
    config: Mapping[str, Any],
) -> tuple[tuple[str, int, int], ...]:
    """Return a hashable signature for the ChatKit rate limit configuration."""
    return (
        (
            "ip",
            _coerce_rate_limit(config, "ip_limit", 120),
            _coerce_rate_limit(config, "ip_interval_seconds", 60),
        ),
        (
            "jwt",
            _coerce_rate_limit(config, "jwt_limit", 120),
            _coerce_rate_limit(config, "jwt_interval_seconds", 60),
        ),
        (
            "workflow",
            _coerce_rate_limit(config, "publish_limit", 60),
            _coerce_rate_limit(config, "publish_interval_seconds", 60),
        ),
        (
            "session",
            _coerce_rate_limit(config, "session_limit", 60),
            _coerce_rate_limit(config, "session_interval_seconds", 60),
        ),
    )


def _build_rate_limiters(config: Mapping[str, Any]) -> _ChatKitRateLimiters:
    """Construct all ChatKit rate limiters from the supplied configuration."""
    return _ChatKitRateLimiters(
        ip=_build_rate_limiter(
            config,
            limit_key="ip_limit",
            interval_key="ip_interval_seconds",
            default_limit=120,
            default_interval=60,
            code="chatkit.rate_limit.ip",
            message_template="Too many ChatKit requests from {key}",
        ),
        jwt=_build_rate_limiter(
            config,
            limit_key="jwt_limit",
            interval_key="jwt_interval_seconds",
            default_limit=120,
            default_interval=60,
            code="chatkit.rate_limit.identity",
            message_template="Too many ChatKit requests for identity {key}",
        ),
        workflow=_build_rate_limiter(
            config,
            limit_key="publish_limit",
            interval_key="publish_interval_seconds",
            default_limit=60,
            default_interval=60,
            code="chatkit.rate_limit.publish",
            message_template="Too many ChatKit requests for workflow {key}",
        ),
        session=_build_rate_limiter(
            config,
            limit_key="session_limit",
            interval_key="session_interval_seconds",
            default_limit=60,
            default_interval=60,
            code="chatkit.rate_limit.session",
            message_template="Too many ChatKit requests for session {key}",
        ),
    )


def _get_rate_limiters() -> _ChatKitRateLimiters:
    """Return the cached ChatKit rate limiters, rebuilding when config changes."""
    config = _load_rate_limit_config()
    signature = _rate_limiter_signature(config)
    cached_signature = _RATE_LIMITER_CACHE["signature"]
    cached_limiters = _RATE_LIMITER_CACHE["limiters"]
    if cached_limiters is None or cached_signature != signature:
        cached_limiters = _build_rate_limiters(config)
        _RATE_LIMITER_CACHE["signature"] = signature
        _RATE_LIMITER_CACHE["limiters"] = cached_limiters
    return cached_limiters


def _reset_rate_limiters() -> None:
    """Clear cached rate limiters for tests and configuration refreshes."""
    _RATE_LIMITER_CACHE["signature"] = None
    _RATE_LIMITER_CACHE["limiters"] = None


@dataclass(slots=True)
class ChatKitAuthResult:
    """Resolved authentication context for a ChatKit invocation."""

    workflow_id: UUID
    actor: str
    auth_mode: Literal["jwt", "publish"]
    subject: str | None
    workspace_id: str | None = None


@lru_cache(maxsize=1)
def _resolve_backend_app_module() -> ModuleType:
    """Load the exported backend app module once for dependency lookups."""
    return import_module("orcheo_backend.app")


def _build_chatkit_request_adapter() -> TypeAdapter[ChatKitReq]:
    """Construct the ChatKit request adapter using the backend exports."""
    backend_app = _resolve_backend_app_module()
    adapter_factory = backend_app.TypeAdapter
    return cast(TypeAdapter[ChatKitReq], adapter_factory(ChatKitReq))


def _pop_snake_or_camel(d: dict[str, Any], snake: str, camel: str) -> Any:
    """Pop a field by snake_case name, falling back to its camelCase alias."""
    return d.pop(snake, None) or d.pop(camel, None)


def _resolve_chatkit_server() -> Any:
    """Retrieve the ChatKit server instance from backend exports."""
    backend_app = _resolve_backend_app_module()
    return backend_app.get_chatkit_server()


def _build_chatkit_log_context(
    auth_result: ChatKitAuthResult, parsed_request: Any
) -> dict[str, Any]:
    """Construct structured log context for ChatKit requests."""
    thread_id = getattr(parsed_request, "thread_id", None)
    request_type = getattr(parsed_request, "type", None)

    log_context: dict[str, Any] = {
        "workflow_id": str(auth_result.workflow_id),
        "auth_mode": auth_result.auth_mode,
        "actor": auth_result.actor,
    }
    if auth_result.subject is not None:
        log_context["subject"] = auth_result.subject
    if thread_id is not None:
        log_context["thread_id"] = str(thread_id)
    if request_type is not None:
        log_context["request_type"] = request_type
    return log_context


def _with_root_path(request: Request, path: str) -> str:
    root_path = request.scope.get("root_path", "").rstrip("/")
    if not root_path:
        return path
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{root_path}{path}"


def _chatkit_error(
    status_code: int,
    *,
    message: str,
    code: str,
    auth_mode: str | None = None,
) -> HTTPException:
    detail: dict[str, Any] = {"message": message, "code": code}
    if auth_mode:
        detail["auth_mode"] = auth_mode
    return HTTPException(status_code=status_code, detail=detail)


def _extract_bearer_token(header_value: str | None) -> str:
    if not header_value:
        raise _chatkit_error(
            status.HTTP_401_UNAUTHORIZED,
            message=(
                "ChatKit session token authentication failed: missing bearer token."
            ),
            code="chatkit.auth.missing_token",
            auth_mode="jwt",
        )
    parts = header_value.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise _chatkit_error(
            status.HTTP_401_UNAUTHORIZED,
            message=(
                "ChatKit session token authentication failed: "
                "Authorization header must use the Bearer scheme."
            ),
            code="chatkit.auth.invalid_scheme",
            auth_mode="jwt",
        )
    token = parts[1].strip()
    if not token:
        raise _chatkit_error(
            status.HTTP_401_UNAUTHORIZED,
            message=(
                "ChatKit session token authentication failed: missing bearer token."
            ),
            code="chatkit.auth.missing_token",
            auth_mode="jwt",
        )
    return token


def _decode_chatkit_jwt(token: str) -> Mapping[str, Any]:
    try:
        settings = load_chatkit_token_settings()
    except ChatKitTokenConfigurationError as exc:
        detail = {
            "message": str(exc),
            "code": "chatkit.signing_key_missing",
        }
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail) from exc

    try:
        payload = jwt.decode(
            token,
            settings.signing_key,
            algorithms=[settings.algorithm],
            audience=settings.audience,
            issuer=settings.issuer,
        )
    except jwt.PyJWTError as exc:
        raise _chatkit_error(
            status.HTTP_401_UNAUTHORIZED,
            message="ChatKit session token authentication failed: invalid token.",
            code="chatkit.auth.invalid_jwt",
            auth_mode="jwt",
        ) from exc
    return payload


def _ip_in_allowlist(client_host: str | None, allowed: tuple[str, ...]) -> bool:
    """Return True when ``client_host`` falls within any allowed IP/CIDR entry."""
    if not client_host:
        return False
    try:
        address = ip_address(client_host)
    except ValueError:
        return False
    for entry in allowed:
        try:
            network = ip_network(entry, strict=False)
        except ValueError:
            continue
        if address in network:
            return True
    return False


def _request_from_trusted_proxy(request: Request, auth_settings: AuthSettings) -> bool:
    """Return True only when the request demonstrably originates from the proxy.

    Trust is fail-closed: if no proxy secret or IP allowlist is configured, the
    forwarded identity headers are never honored.
    """
    secret = auth_settings.trusted_proxy_secret
    allowed_ips = auth_settings.trusted_proxy_ips
    if not secret and not allowed_ips:
        return False
    if secret:
        provided = request.headers.get("X-Orcheo-Proxy-Secret")
        if not provided or not hmac.compare_digest(provided, secret):
            return False
    if allowed_ips:
        client_host = request.client.host if request.client else None
        if not _ip_in_allowlist(client_host, allowed_ips):
            return False
    return True


def _parse_dev_session_subject(raw_value: str) -> str | None:
    candidate = raw_value.strip()
    if not candidate:
        return None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        subject = parsed.get("subject")
        if isinstance(subject, str) and subject.strip():
            return subject.strip()
    return candidate.split(":", 1)[0].strip() or None


def _extract_dev_identity(
    request: Request, auth_settings: AuthSettings
) -> tuple[str, frozenset[str]] | None:
    """Resolve a published-chat identity from the local dev-login session."""
    if not auth_settings.dev_login_enabled:
        return None
    raw_value = request.headers.get("x-orcheo-dev-session")
    if raw_value is None:
        cookie_name = auth_settings.dev_login_cookie_name
        if not cookie_name:
            return None
        raw_value = request.cookies.get(cookie_name)
    if not raw_value:
        return None
    subject = _parse_dev_session_subject(str(raw_value))
    if not subject:
        return None
    workspaces = frozenset(
        workspace_id.strip().lower()
        for workspace_id in auth_settings.dev_login_workspace_ids
        if workspace_id.strip()
    )
    return subject, workspaces


def _extract_proxy_identity(request: Request) -> tuple[str | None, frozenset[str]]:
    """Return the authenticated ``(subject, authorized_workspace_ids)``.

    Forwarded ``X-Orcheo-OAuth-*`` headers are honored only when the request
    originates from a trusted proxy; otherwise client-supplied copies are
    ignored. A local dev-login session is accepted as a fallback.
    """
    auth_settings = load_auth_settings()
    if _request_from_trusted_proxy(request, auth_settings):
        header_subject = request.headers.get("X-Orcheo-OAuth-Subject")
        subject = header_subject.strip() if header_subject else None
        if subject:
            workspaces = frozenset(
                workspace_id.strip().lower()
                for workspace_id in coerce_str_items(
                    request.headers.get("X-Orcheo-OAuth-Workspaces")
                )
                if workspace_id.strip()
            )
            return subject, workspaces
    dev_identity = _extract_dev_identity(request, auth_settings)
    if dev_identity is not None:
        return dev_identity
    return None, frozenset()


def _rate_limit(
    limiter: SlidingWindowRateLimiter,
    key: str | None,
    *,
    now: datetime,
) -> None:
    if not key:
        return
    try:
        limiter.hit(key, now=now)
    except AuthenticationError as exc:
        raise exc.as_http_exception() from exc


def _resolve_jwt_workspace_id(
    chatkit_claims: Mapping[str, Any],
    repository_workspace_id: str | None,
) -> str | None:
    """Return the workspace ID authorized by the JWT or the workflow row."""
    authorized_workspace_ids = {
        workspace_id.strip().lower()
        for workspace_id in coerce_str_items(chatkit_claims.get("workspace_ids"))
        if workspace_id.strip()
    }
    if repository_workspace_id is not None:
        if repository_workspace_id.strip().lower() not in authorized_workspace_ids:
            raise _chatkit_error(
                status.HTTP_403_FORBIDDEN,
                message=(
                    "ChatKit session token authentication failed: "
                    "workflow workspace is not authorized by the token."
                ),
                code="chatkit.auth.workspace_mismatch",
                auth_mode="jwt",
            )
        return repository_workspace_id

    claimed_workspace_id = chatkit_claims.get("workspace_id")
    if claimed_workspace_id is None:
        if authorized_workspace_ids:
            raise _chatkit_error(
                status.HTTP_400_BAD_REQUEST,
                message=(
                    "ChatKit session token authentication failed: "
                    "workspace selection is required."
                ),
                code="chatkit.auth.workspace_required",
                auth_mode="jwt",
            )
        return None

    candidate = str(claimed_workspace_id).strip()
    if not candidate:
        if authorized_workspace_ids:
            raise _chatkit_error(
                status.HTTP_400_BAD_REQUEST,
                message=(
                    "ChatKit session token authentication failed: "
                    "workspace selection is required."
                ),
                code="chatkit.auth.workspace_required",
                auth_mode="jwt",
            )
        return None

    if candidate.lower() not in authorized_workspace_ids:
        raise _chatkit_error(
            status.HTTP_403_FORBIDDEN,
            message=(
                "ChatKit session token authentication failed: "
                "workspace_id claim is not authorized by the token."
            ),
            code="chatkit.auth.workspace_mismatch",
            auth_mode="jwt",
        )
    return candidate


async def authenticate_chatkit_invocation(
    *,
    request: Request,
    payload: Mapping[str, Any],
    repository: RepositoryDep,
) -> ChatKitAuthResult:
    """Validate authentication for the ChatKit gateway request."""
    workflow_value = payload.get("workflow_id")
    if not workflow_value:
        raise _chatkit_error(
            status.HTTP_400_BAD_REQUEST,
            message="workflow_id is required.",
            code="chatkit.workflow_id_missing",
        )
    try:
        workflow_id = UUID(str(workflow_value))
    except ValueError as exc:
        raise _chatkit_error(
            status.HTTP_400_BAD_REQUEST,
            message="workflow_id must be a valid UUID.",
            code="chatkit.workflow_id_invalid",
        ) from exc

    now = datetime.now(tz=UTC)
    client_host = request.client.host if request.client else None
    _rate_limit(_get_rate_limiters().ip, client_host, now=now)

    jwt_result = await _authenticate_jwt_request(
        request=request,
        workflow_id=workflow_id,
        now=now,
        repository=repository,
    )
    if jwt_result is not None:
        return jwt_result

    return await _authenticate_publish_request(
        request=request,
        workflow_id=workflow_id,
        now=now,
        repository=repository,
    )


@router.get("/chatkit/assets/ck1/{asset_path:path}", include_in_schema=False)
@router.head("/chatkit/assets/ck1/{asset_path:path}", include_in_schema=False)
async def proxy_chatkit_ck1_asset(
    request: Request,
    asset_path: str,
) -> Response:
    return await proxy_chatkit_asset(
        request,
        prefix="assets/ck1",
        asset_path=asset_path,
    )


@router.get("/chatkit/assets/{asset_path:path}", include_in_schema=False)
@router.head("/chatkit/assets/{asset_path:path}", include_in_schema=False)
async def proxy_chatkit_deployment_asset(
    request: Request,
    asset_path: str,
) -> Response:
    return await proxy_chatkit_asset(
        request,
        prefix="deployments/chatkit",
        asset_path=asset_path,
        rewrite_prefix=_with_root_path(request, "/api/chatkit/assets/ck1"),
    )


@router.post("/chatkit", include_in_schema=False)
async def chatkit_gateway(request: Request, repository: RepositoryDep) -> Response:
    """Proxy ChatKit SDK requests to the Orcheo-backed server."""
    raw_body = await request.body()
    try:
        payload_dict = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid JSON payload.", "errors": [str(exc)]},
        ) from exc

    if not isinstance(payload_dict, dict):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Invalid ChatKit payload.",
                "errors": ["Input payload must be a JSON object."],
            },
        )

    workflow_id_value = _pop_snake_or_camel(payload_dict, "workflow_id", "workflowId")
    upload_session_id_value = _pop_snake_or_camel(
        payload_dict, "upload_session_id", "uploadSessionId"
    )

    try:
        adapter = _build_chatkit_request_adapter()
        parsed_request = adapter.validate_python(payload_dict)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Invalid ChatKit payload.",
                "errors": exc.errors(),
            },
        ) from exc

    auth_result = await authenticate_chatkit_invocation(
        request=request,
        payload={"workflow_id": workflow_id_value},
        repository=repository,
    )

    sanitized_payload = json.dumps(payload_dict).encode("utf-8")

    context: ChatKitRequestContext = {
        "chatkit_request": parsed_request,
        "workflow_id": str(auth_result.workflow_id),
        "workspace_id": auth_result.workspace_id,
        "actor": auth_result.actor,
        "auth_mode": auth_result.auth_mode,
    }
    if auth_result.subject is not None:
        context["subject"] = auth_result.subject
    if upload_session_id_value:
        context["upload_session_id"] = str(upload_session_id_value)

    server = _resolve_chatkit_server()
    result = await server.process(sanitized_payload, context)

    logger.info(
        "Processed ChatKit request",
        extra=_build_chatkit_log_context(auth_result, parsed_request),
    )

    if isinstance(result, StreamingResult):
        return StreamingResponse(result, media_type="text/event-stream")
    if hasattr(result, "json"):
        json_payload = result.json
        status_code = getattr(result, "status_code", status.HTTP_200_OK)
        headers = getattr(result, "headers", None)
        media_type = getattr(result, "media_type", "application/json")

        payload_value: Any
        if callable(json_payload):
            payload_value = json_payload()
        else:
            payload_value = json_payload

        header_mapping = dict(headers) if headers else None

        if isinstance(payload_value, str | bytes | bytearray):
            return Response(
                content=payload_value,
                status_code=status_code,
                media_type=media_type,
                headers=header_mapping,
            )

        return JSONResponse(
            payload_value,
            status_code=status_code,
            headers=header_mapping,
            media_type=media_type,
        )
    return JSONResponse(result)


def _resolve_chatkit_workspace_id(
    policy: AuthorizationPolicy, request: ChatKitSessionRequest
) -> str | None:
    metadata = request.metadata or {}
    for key in ("workspace_id", "workspaceId", "workspace"):
        value = metadata.get(key)
        if value:
            return str(value)
    if policy.context.workspace_ids:
        if len(policy.context.workspace_ids) == 1:
            return next(iter(policy.context.workspace_ids))
        raise _chatkit_error(
            status.HTTP_400_BAD_REQUEST,
            message=(
                "ChatKit session token authentication failed: "
                "workspace selection is required."
            ),
            code="chatkit.auth.workspace_required",
            auth_mode="jwt",
        )
    return None


@router.post(
    "/chatkit/session",
    response_model=ChatKitSessionResponse,
    status_code=status.HTTP_200_OK,
)
async def create_chatkit_session_endpoint(
    request: ChatKitSessionRequest,
    policy: AuthorizationPolicy = Depends(get_authorization_policy),  # noqa: B008
    issuer: ChatKitSessionTokenIssuer = Depends(resolve_chatkit_token_issuer),  # noqa: B008
) -> ChatKitSessionResponse:
    """Issue a signed ChatKit session token scoped to the caller."""
    try:
        policy.require_authenticated()
        policy.require_scopes("chatkit:session")
    except AuthenticationError as exc:
        raise exc.as_http_exception() from exc

    workspace_id = _resolve_chatkit_workspace_id(policy, request)
    if workspace_id:
        try:
            policy.require_workspace(workspace_id)
        except AuthenticationError as exc:
            raise exc.as_http_exception() from exc

    context = policy.context
    extra: dict[str, Any] = {}
    if request.workflow_label:
        extra["workflow_label"] = request.workflow_label
    if request.current_client_secret:
        extra["previous_secret"] = request.current_client_secret
    extra_payload: dict[str, Any] | None = extra or None

    try:
        token, expires_at = issuer.mint_session(
            subject=context.subject,
            identity_type=context.identity_type,
            token_id=context.token_id,
            workspace_ids=context.workspace_ids,
            primary_workspace_id=workspace_id,
            workflow_id=request.workflow_id,
            scopes=context.scopes,
            metadata=request.metadata,
            user=request.user,
            assistant=request.assistant,
            extra=extra_payload,
        )
    except ChatKitTokenConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": str(exc),
                "hint": (
                    "Set CHATKIT_TOKEN_SIGNING_KEY to enable ChatKit session issuance."
                ),
            },
        ) from exc
    except CredentialHealthError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": str(exc),
                "hint": (
                    "Set CHATKIT_TOKEN_SIGNING_KEY to enable ChatKit session issuance."
                ),
            },
        ) from exc

    logger.info(
        "Issued ChatKit session token for subject %s workspace=%s workflow=%s",
        context.subject,
        workspace_id or "<unspecified>",
        request.workflow_id or "<none>",
    )
    return ChatKitSessionResponse(client_secret=token, expires_at=expires_at)


@router.post(
    "/chatkit/workflows/{workflow_ref}/trigger",
    response_model=WorkflowRun,
    status_code=status.HTTP_201_CREATED,
)
async def trigger_chatkit_workflow(
    workflow_ref: str,
    request: ChatKitWorkflowTriggerRequest,
    repository: RepositoryDep,
    policy: AuthorizationPolicy = Depends(get_authorization_policy),  # noqa: B008
) -> WorkflowRun:
    """Create a workflow run initiated from the ChatKit interface."""
    workflow_uuid = await resolve_workflow_ref_id(repository, workflow_ref)
    try:
        policy.require_authenticated()
    except AuthenticationError as exc:
        if load_auth_settings().enforce:
            raise exc.as_http_exception() from exc

    try:
        latest_version = await repository.get_latest_version(workflow_uuid)
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    except WorkflowVersionNotFoundError as exc:
        raise_not_found("Workflow version not found", exc)

    payload = {
        "source": "chatkit",
        "message": request.message,
        "client_thread_id": request.client_thread_id,
        "metadata": request.metadata,
    }

    try:
        run = await repository.create_run(
            workflow_uuid,
            workflow_version_id=latest_version.id,
            triggered_by=request.actor,
            input_payload=payload,
        )
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    except WorkflowVersionNotFoundError as exc:
        raise_not_found("Workflow version not found", exc)
    except CredentialHealthError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": str(exc), "failures": exc.report.failures},
        ) from exc

    logger.info(
        "Dispatched ChatKit workflow run",
        extra={"workflow_id": str(workflow_uuid), "run_id": str(run.id)},
    )
    return run


def _sanitize_filename(filename: str | None) -> str:
    """Return a safe filename stripped of path traversal components."""
    if not filename:
        return "uploaded_file"

    candidate = Path(filename).name
    stripped = candidate.strip().lstrip(".")
    safe = "".join(ch for ch in stripped if ch.isalnum() or ch in {".", "_", "-", " "})
    normalized = safe.strip().replace(" ", "_")
    if not normalized:
        return "uploaded_file"
    return normalized[:255]


@router.post("/chatkit/upload", include_in_schema=False)
async def upload_chatkit_file(  # noqa: C901
    file: UploadFile,
    request: Request,
    repository: RepositoryDep,
    workflow_id: str | None = Form(default=None),  # noqa: B008
    thread_id: str | None = Form(default=None),  # noqa: B008
    upload_session_id: str | None = Form(default=None),  # noqa: B008
) -> JSONResponse:
    """Handle file uploads from ChatKit composer with direct upload strategy.

    Accepts ``workflow_id`` (required) as a form field or ``?workflow_id=``
    query parameter (used when the chatkit library's direct upload strategy
    does not support injecting extra form data). ``thread_id`` and
    ``upload_session_id`` are accepted as optional form fields.  Bytes are
    persisted in ``chat_attachment_blobs``; no server filesystem path is
    returned.
    """
    # Accept workflow_id from query params when the chatkit direct-upload
    # strategy cannot inject form fields (e.g., public-chat-widget).
    if not workflow_id:
        workflow_id = request.query_params.get("workflow_id") or None

    if not workflow_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "workflow_id is required for file uploads.",
                "code": "chatkit.upload.workflow_id_missing",
            },
        )

    auth_result = await authenticate_chatkit_invocation(
        request=request,
        payload={"workflow_id": workflow_id},
        repository=repository,
    )

    try:
        settings = orcheo_config.get_settings()
        max_upload_size = int(
            settings.get(
                "CHATKIT_MAX_UPLOAD_SIZE_BYTES",
                _DEFAULTS["CHATKIT_MAX_UPLOAD_SIZE_BYTES"],
            )
        )

        content = await file.read(max_upload_size + 1)
        if len(content) > max_upload_size:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={
                    "message": "File exceeds maximum allowed size",
                    "code": "chatkit.upload.too_large",
                },
            )

        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                content.decode("latin-1")
            except UnicodeDecodeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "message": "File must be a text file with valid encoding",
                        "code": "chatkit.upload.invalid_encoding",
                    },
                ) from exc

        safe_name = _sanitize_filename(file.filename)
        mime_type = file.content_type or "text/plain"

        server = _resolve_chatkit_server()
        ensure_initialized = getattr(server.store, "_ensure_initialized", None)
        if callable(ensure_initialized):
            initialized = ensure_initialized()
            if inspect.isawaitable(initialized):
                await initialized
        attachment_service = server.store.attachment_service

        resolved_workspace = auth_result.workspace_id or ""
        resolved_workflow = str(auth_result.workflow_id)
        actor_subject = auth_result.subject

        attachment_id, minted_session_id = await attachment_service.save_attachment(
            workspace_id=resolved_workspace,
            workflow_id=resolved_workflow,
            thread_id=thread_id or None,
            upload_session_id=upload_session_id or None,
            auth_mode=auth_result.auth_mode,
            actor_subject=actor_subject,
            attachment_type="file",
            name=safe_name,
            mime_type=mime_type,
            content=content,
            blob_backend=attachment_service.blob_backend,
        )

        response_payload: dict[str, object] = {
            "id": attachment_id,
            "name": safe_name,
            "mime_type": mime_type,
            "type": "file",
            "size": len(content),
        }
        if minted_session_id:
            response_payload["upload_session_id"] = minted_session_id

        logger.info(
            "Stored ChatKit file upload via blob backend",
            extra={
                "attachment_id": attachment_id,
                "workflow_id": resolved_workflow,
                "workspace_id": resolved_workspace,
                "thread_id": thread_id,
                "upload_session_id": upload_session_id or minted_session_id,
                "size_bytes": len(content),
            },
        )
        return JSONResponse(content=response_payload, status_code=status.HTTP_200_OK)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Failed to process ChatKit file upload",
            extra={
                "file_name": file.filename,
                "content_type": file.content_type,
                "error": str(exc),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Failed to process file upload",
                "code": "chatkit.upload.processing_error",
            },
        ) from exc


@router.get("/chatkit/attachments/{attachment_id}", include_in_schema=False)
async def download_chatkit_attachment(
    attachment_id: str,
    request: Request,  # noqa: ARG001  # kept for middleware / logging consistency
) -> Response:
    """Serve a workflow-produced attachment by id.

    No session authentication is required.  Security relies on the attachment
    id being unguessable (``atc_`` + 32 hex chars of UUID4).  This matches the
    security model of presigned object-storage URLs.
    """
    server = _resolve_chatkit_server()
    attachment_service = getattr(server.store, "attachment_service", None)
    if attachment_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "Attachment storage is not available.",
                "code": "chatkit.attachments.unavailable",
            },
        )

    ensure_initialized = getattr(server.store, "_ensure_initialized", None)
    if callable(ensure_initialized):
        initialized = ensure_initialized()
        if inspect.isawaitable(initialized):
            await initialized

    from orcheo_backend.app.chatkit_store_postgres.attachment_service import (
        AttachmentNotFoundError,
    )

    try:
        payload = await attachment_service.load_attachment_bytes_public(attachment_id)
    except AttachmentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "message": "Attachment not found.",
                "code": "chatkit.attachments.not_found",
            },
        ) from None
    except Exception as exc:
        logger.error(
            "Failed to load ChatKit attachment for download",
            extra={"attachment_id": attachment_id, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Failed to retrieve attachment.",
                "code": "chatkit.attachments.load_error",
            },
        ) from exc

    safe_name = _sanitize_filename(payload.name) or "download"
    return Response(
        content=payload.content,
        media_type=payload.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


__all__ = ["router"]


async def _authenticate_jwt_request(
    *,
    request: Request,
    workflow_id: UUID,
    now: datetime,
    repository: RepositoryDep,
) -> ChatKitAuthResult | None:
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None

    token = _extract_bearer_token(auth_header)
    claims = _decode_chatkit_jwt(token)
    chatkit_claims = claims.get("chatkit")
    if not isinstance(chatkit_claims, Mapping):
        raise _chatkit_error(
            status.HTTP_401_UNAUTHORIZED,
            message=(
                "ChatKit session token authentication failed: missing required claims."
            ),
            code="chatkit.auth.invalid_jwt_claims",
            auth_mode="jwt",
        )

    claimed_workflow_id = chatkit_claims.get("workflow_id")
    if claimed_workflow_id:
        try:
            claimed_uuid = UUID(str(claimed_workflow_id))
        except ValueError as exc:
            raise _chatkit_error(
                status.HTTP_401_UNAUTHORIZED,
                message=(
                    "ChatKit session token authentication failed: workflow_id "
                    "claim is invalid."
                ),
                code="chatkit.auth.invalid_jwt_claims",
                auth_mode="jwt",
            ) from exc
        if claimed_uuid != workflow_id:
            raise _chatkit_error(
                status.HTTP_403_FORBIDDEN,
                message=(
                    "ChatKit session token authentication failed: "
                    "token does not authorize this workflow."
                ),
                code="chatkit.auth.workflow_mismatch",
                auth_mode="jwt",
            )

    identity = chatkit_claims.get("token_id") or claims.get("sub")
    _rate_limit(_get_rate_limiters().jwt, str(identity) if identity else None, now=now)

    try:
        workflow = await repository.get_workflow(workflow_id)
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    if workflow.is_archived:
        raise_not_found("Workflow not found", WorkflowNotFoundError(str(workflow_id)))

    actor_subject = str(claims.get("sub") or "chatkit")
    repository_workspace_id = await repository.get_workflow_workspace_id(workflow_id)
    workspace_id = _resolve_jwt_workspace_id(chatkit_claims, repository_workspace_id)
    return ChatKitAuthResult(
        workflow_id=workflow_id,
        actor=f"jwt:{actor_subject}",
        auth_mode="jwt",
        subject=actor_subject,
        workspace_id=workspace_id,
    )


async def _authenticate_publish_request(
    *,
    request: Request,
    workflow_id: UUID,
    now: datetime,
    repository: RepositoryDep,
) -> ChatKitAuthResult:
    try:
        workflow = await repository.get_workflow(workflow_id)
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    if workflow.is_archived:
        raise_not_found("Workflow not found", WorkflowNotFoundError(str(workflow_id)))

    if not workflow.is_public:
        raise _chatkit_error(
            status.HTTP_403_FORBIDDEN,
            message="Publish authentication failed: workflow is not published.",
            code="chatkit.auth.not_published",
            auth_mode="publish",
        )

    _rate_limit(_get_rate_limiters().workflow, str(workflow_id), now=now)

    session_subject, authorized_workspaces = _extract_proxy_identity(request)
    workspace_id = await repository.get_workflow_workspace_id(workflow_id)
    if workflow.require_login:
        if not session_subject:
            raise _chatkit_error(
                status.HTTP_401_UNAUTHORIZED,
                message=(
                    "Publish authentication failed: OAuth login is required "
                    "to access this workflow."
                ),
                code="chatkit.auth.oauth_required",
                auth_mode="publish",
            )
        if (
            workspace_id is not None
            and workspace_id.strip().lower() not in authorized_workspaces
        ):
            raise _chatkit_error(
                status.HTTP_403_FORBIDDEN,
                message=(
                    "Publish authentication failed: workflow workspace is not "
                    "authorized for this user."
                ),
                code="chatkit.auth.workspace_mismatch",
                auth_mode="publish",
            )

    _rate_limit(_get_rate_limiters().session, session_subject, now=now)

    actor = f"workflow:{workflow_id}"
    return ChatKitAuthResult(
        workflow_id=workflow_id,
        actor=actor,
        auth_mode="publish",
        subject=session_subject,
        workspace_id=workspace_id,
    )
