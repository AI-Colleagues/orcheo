"""ChatKit-related FastAPI routes."""

from __future__ import annotations
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, cast
from uuid import UUID, uuid4
import jwt
from chatkit.server import StreamingResult
from chatkit.types import ChatKitReq
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import TypeAdapter, ValidationError
from starlette.responses import JSONResponse, StreamingResponse
from orcheo.config import get_settings
from orcheo.config.defaults import _DEFAULTS
from orcheo.models.workflow import WorkflowRun
from orcheo.vault.oauth import CredentialHealthError
from orcheo_backend.app.authentication import (
    AuthenticationError,
    AuthorizationPolicy,
    get_authorization_policy,
    load_auth_settings,
)
from orcheo_backend.app.authentication.rate_limit import SlidingWindowRateLimiter
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
    settings = get_settings()
    config = settings.get("CHATKIT_RATE_LIMITS")
    return config if isinstance(config, Mapping) else {}


def _coerce_rate_limit(config: Mapping[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_rate_limiter(
    *,
    limit_key: str,
    interval_key: str,
    default_limit: int,
    default_interval: int,
    code: str,
    message_template: str,
) -> SlidingWindowRateLimiter:
    config = _load_rate_limit_config()
    limit = _coerce_rate_limit(config, limit_key, default_limit)
    interval = _coerce_rate_limit(config, interval_key, default_interval)
    return SlidingWindowRateLimiter(
        limit=limit,
        interval_seconds=interval,
        code=code,
        message_template=message_template,
    )


_IP_RATE_LIMITER = _build_rate_limiter(
    limit_key="ip_limit",
    interval_key="ip_interval_seconds",
    default_limit=120,
    default_interval=60,
    code="chatkit.rate_limit.ip",
    message_template="Too many ChatKit requests from {key}",
)
_JWT_RATE_LIMITER = _build_rate_limiter(
    limit_key="jwt_limit",
    interval_key="jwt_interval_seconds",
    default_limit=120,
    default_interval=60,
    code="chatkit.rate_limit.identity",
    message_template="Too many ChatKit requests for identity {key}",
)
_WORKFLOW_RATE_LIMITER = _build_rate_limiter(
    limit_key="publish_limit",
    interval_key="publish_interval_seconds",
    default_limit=60,
    default_interval=60,
    code="chatkit.rate_limit.publish",
    message_template="Too many ChatKit requests for workflow {key}",
)
_SESSION_RATE_LIMITER = _build_rate_limiter(
    limit_key="session_limit",
    interval_key="session_interval_seconds",
    default_limit=60,
    default_interval=60,
    code="chatkit.rate_limit.session",
    message_template="Too many ChatKit requests for session {key}",
)


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


def _extract_session_subject(request: Request) -> str | None:
    header_subject = request.headers.get("X-Orcheo-OAuth-Subject")
    if header_subject and header_subject.strip():
        return header_subject.strip()
    cookie_subject = request.cookies.get("orcheo_oauth_session")
    if cookie_subject and str(cookie_subject).strip():
        return str(cookie_subject).strip()
    return None


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
    _rate_limit(_IP_RATE_LIMITER, client_host, now=now)

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

    workflow_id_value = payload_dict.pop("workflow_id", None)
    camel_workflow_id_value = payload_dict.pop("workflowId", None)
    if workflow_id_value is None:
        workflow_id_value = camel_workflow_id_value

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
async def upload_chatkit_file(
    file: UploadFile,
    request: Request,
) -> JSONResponse:
    """Handle file uploads from ChatKit composer with direct upload strategy.

    This endpoint receives files uploaded via ChatKit's direct upload strategy,
    stores them on disk at CHATKIT_STORAGE_PATH, and returns attachment metadata.

    Files are persisted to disk and content extraction is deferred to DocumentLoaderNode
    during workflow execution to avoid redundant processing.

    The response must match ChatKit's FileAttachment or ImageAttachment format:
    - id: unique attachment identifier
    - name: filename
    - mime_type: content type
    - type: 'file' or 'image'
    - storage_path: path to the stored file (not part of standard ChatKit format)
    """
    try:
        settings = get_settings()
        max_upload_size = int(
            settings.get(
                "CHATKIT_MAX_UPLOAD_SIZE_BYTES",
                _DEFAULTS["CHATKIT_MAX_UPLOAD_SIZE_BYTES"],
            )
        )

        # Read file content with a size guard
        content = await file.read(max_upload_size + 1)
        if len(content) > max_upload_size:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={
                    "message": "File exceeds maximum allowed size",
                    "code": "chatkit.upload.too_large",
                },
            )

        # Validate it's a text file by attempting to decode
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            # If not UTF-8, try other common encodings
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

        # Generate a unique ID for this attachment
        attachment_id = f"atc_{uuid4().hex[:8]}"

        # Determine storage path from settings
        storage_base = Path(
            str(settings.get("CHATKIT_STORAGE_PATH", "~/.orcheo/chatkit"))
        ).expanduser()
        storage_base.mkdir(parents=True, exist_ok=True)

        # Store file on disk with attachment ID as filename
        safe_name = _sanitize_filename(file.filename)
        storage_path = storage_base / f"{attachment_id}_{safe_name}"
        resolved_storage_path = storage_path.resolve()
        if not resolved_storage_path.is_relative_to(storage_base.resolve()):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Invalid filename provided",
                    "code": "chatkit.upload.invalid_filename",
                },
            )
        storage_path.write_bytes(content)

        # Create attachment object matching ChatKit's FileAttachment type
        from chatkit.types import FileAttachment

        attachment = FileAttachment(
            id=attachment_id,
            name=safe_name,
            mime_type=file.content_type or "text/plain",
        )

        # Save attachment metadata to store with storage_path reference
        server = _resolve_chatkit_server()
        # Create minimal context - we don't have thread_id yet at upload time
        context: ChatKitRequestContext = {
            "chatkit_request": None,  # type: ignore
            "workflow_id": "",
            "actor": "chatkit",
            "auth_mode": "publish",
        }
        await server.store.save_attachment(
            attachment, context, storage_path=str(storage_path)
        )

        # Return attachment metadata in ChatKit's expected format
        # Note: We do NOT return content here - it will be read by DocumentLoaderNode
        return JSONResponse(
            content={
                "id": attachment_id,
                "name": safe_name,
                "mime_type": file.content_type or "text/plain",
                "type": "file",
                "size": len(content),
                "storage_path": str(storage_path),
            },
            status_code=status.HTTP_200_OK,
        )
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
    _rate_limit(_JWT_RATE_LIMITER, str(identity) if identity else None, now=now)

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

    _rate_limit(_WORKFLOW_RATE_LIMITER, str(workflow_id), now=now)

    session_subject = _extract_session_subject(request)
    if workflow.require_login and not session_subject:
        raise _chatkit_error(
            status.HTTP_401_UNAUTHORIZED,
            message=(
                "Publish authentication failed: OAuth login is required "
                "to access this workflow."
            ),
            code="chatkit.auth.oauth_required",
            auth_mode="publish",
        )

    _rate_limit(_SESSION_RATE_LIMITER, session_subject, now=now)

    workspace_id = await repository.get_workflow_workspace_id(workflow_id)
    actor = f"workflow:{workflow_id}"
    return ChatKitAuthResult(
        workflow_id=workflow_id,
        actor=actor,
        auth_mode="publish",
        subject=session_subject,
        workspace_id=workspace_id,
    )
