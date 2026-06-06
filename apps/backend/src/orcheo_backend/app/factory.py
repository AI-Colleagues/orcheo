"""Application factory for the Orcheo FastAPI service."""

from __future__ import annotations
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from orcheo.agentensor.checkpoints import AgentensorCheckpointStore
from orcheo.plugins import load_enabled_plugins
from orcheo.vault.oauth import OAuthCredentialService
from orcheo_backend.app.authentication import (
    AuthenticationError,
    authenticate_request,
    load_auth_settings,
)
from orcheo_backend.app.chatkit_runtime import (
    cancel_chatkit_cleanup_task,
    ensure_chatkit_cleanup_task,
    get_chatkit_server,
    sensitive_logging_enabled,
)
from orcheo_backend.app.dependencies import (
    ListenerRuntimeStore,
    _create_repository,
    _repository_ref,
    get_checkpoint_store,
    get_credential_service,
    get_history_store,
    get_listener_runtime_store,
    get_plugin_installation_store,
    get_repository,
    get_vault,
    set_checkpoint_store,
    set_credential_service,
    set_history_store,
    set_listener_runtime_store,
    set_plugin_installation_store,
    set_repository,
    set_vault,
)
from orcheo_backend.app.history import RunHistoryStore
from orcheo_backend.app.listener_runtime_service import ListenerRuntimeService
from orcheo_backend.app.logging_config import configure_logging
from orcheo_backend.app.managed_workflows import ensure_managed_vibe_workflow
from orcheo_backend.app.plugin_installation_store import PluginInstallationStore
from orcheo_backend.app.repository import WorkflowRepository
from orcheo_backend.app.routers import (
    agentensor,
    auth,
    candidates,
    chatkit_assets,
    credential_alerts,
    credential_health,
    credential_templates,
    credentials,
    listeners,
    nodes,
    remediations,
    runs,
    system,
    triggers,
    websocket,
    workflows,
)
from orcheo_backend.app.routers import (
    chatkit as chatkit_router,
)
from orcheo_backend.app.routers import (
    workspaces as workspaces_router,
)
from orcheo_backend.app.service_token_endpoints import router as service_token_router
from orcheo_backend.app.workflow_execution import configure_sensitive_logging
from orcheo_backend.app.workspace import (
    get_workspace_service,
    resolve_workspace_context,
)


load_dotenv()
configure_logging()

configure_sensitive_logging(
    enable_sensitive_debug=sensitive_logging_enabled(),
)


async def _authentication_error_handler(request: Request, exc: Exception) -> Response:
    """Translate AuthenticationError instances into structured HTTP responses."""
    auth_error = cast(AuthenticationError, exc)
    http_exc = auth_error.as_http_exception()
    return await http_exception_handler(request, http_exc)


async def _robots_txt() -> PlainTextResponse:
    """Expose crawl policy for public backend deployments."""
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


@asynccontextmanager
async def _app_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifespan with startup and shutdown logic."""
    from orcheo.tracing import configure_tracing

    if not _repository_ref.get("repository"):
        _create_repository()
    configure_tracing()
    load_auth_settings(refresh=True)
    load_enabled_plugins(force=True)
    workspace_service = get_workspace_service()
    for workspace in workspace_service.list_workspaces(include_inactive=False):
        try:
            await ensure_managed_vibe_workflow(get_repository(), workspace)
        except (RuntimeError, Exception):
            # Managed vibe workflow may not exist in production / fresh deployments.
            # The check is a no-op when not applicable.
            pass
    listener_runtime = ListenerRuntimeService(
        repository=get_repository(),
        vault=get_vault(),
        runtime_store=get_listener_runtime_store(),
    )
    app.state.listener_runtime = listener_runtime
    try:
        get_chatkit_server()
        await ensure_chatkit_cleanup_task()
    except Exception:
        pass
    await listener_runtime.start()
    try:
        yield
    finally:
        await listener_runtime.stop()
        await cancel_chatkit_cleanup_task()


def _build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    protected_router = APIRouter(
        dependencies=[
            Depends(authenticate_request),
            Depends(resolve_workspace_context),
        ]
    )
    protected_router.include_router(service_token_router)
    protected_router.include_router(workflows.router)
    protected_router.include_router(credentials.router)
    protected_router.include_router(credential_templates.router)
    protected_router.include_router(credential_alerts.router)
    protected_router.include_router(credential_health.router)
    protected_router.include_router(listeners.router)
    protected_router.include_router(remediations.router)
    protected_router.include_router(runs.router)
    protected_router.include_router(triggers.router)
    protected_router.include_router(nodes.router)
    protected_router.include_router(agentensor.router)
    protected_router.include_router(system.router)
    protected_router.include_router(workspaces_router.admin_router)
    protected_router.include_router(workspaces_router.router)

    router.include_router(workflows.public_router)
    router.include_router(candidates.router)
    router.include_router(chatkit_router.router)
    router.include_router(auth.router)
    router.include_router(system.public_router)
    router.include_router(workspaces_router.self_service_router)
    # Public webhook invocation routes - external services (Slack, GitHub, etc.)
    # cannot provide Orcheo auth tokens. Security is enforced via webhook-level
    # validation (HMAC signatures, shared secrets) configured per workflow.
    router.include_router(triggers.public_webhook_router)
    router.include_router(protected_router)
    return router


api_router = _build_api_router()


_DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost:2026",
    "http://127.0.0.1:2026",
]


def _load_allowed_origins() -> list[str]:
    """Return the list of CORS-allowed origins based on environment configuration."""
    raw = os.getenv("ORCHEO_CORS_ALLOW_ORIGINS")
    if not raw:
        return list(_DEFAULT_ALLOWED_ORIGINS)
    candidates: list[str] = []
    parsed: list[str] | str | None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw

    if isinstance(parsed, str):
        candidates = [entry.strip() for entry in parsed.split(",")]
    elif isinstance(parsed, list):
        candidates = [str(entry).strip() for entry in parsed]

    origins = [origin for origin in candidates if origin]  # pragma: no cover
    return origins or list(_DEFAULT_ALLOWED_ORIGINS)


def _configure_dependency_overrides(
    application: FastAPI,
    repository: WorkflowRepository | None = None,
    *,
    history_store: RunHistoryStore | None = None,
    checkpoint_store: AgentensorCheckpointStore | None = None,
    plugin_installation_store: PluginInstallationStore | None = None,
    credential_service: OAuthCredentialService | None = None,
) -> ListenerRuntimeStore:
    """Apply optional dependency overrides used by tests and integrations."""
    if repository is not None:
        set_repository(repository)  # pragma: no mutate - override for tests
        application.dependency_overrides[get_repository] = lambda: repository
    if history_store is not None:
        set_history_store(history_store)
        application.dependency_overrides[get_history_store] = lambda: history_store
    if checkpoint_store is not None:
        set_checkpoint_store(checkpoint_store)
        application.dependency_overrides[get_checkpoint_store] = (
            lambda: checkpoint_store
        )
    if plugin_installation_store is not None:
        set_plugin_installation_store(plugin_installation_store)
        application.dependency_overrides[get_plugin_installation_store] = (
            lambda: plugin_installation_store
        )

    listener_runtime_store = get_listener_runtime_store()
    set_listener_runtime_store(listener_runtime_store)

    if credential_service is not None:
        set_credential_service(credential_service)
        set_vault(getattr(credential_service, "_vault", None))
        application.dependency_overrides[get_credential_service] = lambda: (
            credential_service
        )
    elif repository is not None:
        inferred_service = getattr(repository, "_credential_service", None)
        if inferred_service is not None:
            set_credential_service(inferred_service)
            application.dependency_overrides[get_credential_service] = lambda: (
                inferred_service
            )

    application.state.listener_runtime_store = listener_runtime_store
    return listener_runtime_store


def _configure_application(application: FastAPI) -> None:
    """Install routes, handlers, and middleware on the FastAPI app."""
    application.include_router(api_router)
    # Workspace-slug-prefixed webhook routes at /hooks/{workspace_slug}/{trigger_id}.
    # Mounted at the application root (not under /api) so external services
    # can reach them without an /api prefix.
    application.include_router(triggers.workspace_webhook_router)
    application.include_router(chatkit_assets.router)
    application.include_router(websocket.router)
    application.add_exception_handler(
        AuthenticationError, _authentication_error_handler
    )


def create_app(
    repository: WorkflowRepository | None = None,
    *,
    history_store: RunHistoryStore | None = None,
    checkpoint_store: AgentensorCheckpointStore | None = None,
    plugin_installation_store: PluginInstallationStore | None = None,
    credential_service: OAuthCredentialService | None = None,
) -> FastAPI:
    """Instantiate and configure the FastAPI application."""
    application = FastAPI(lifespan=_app_lifespan)
    application.add_api_route("/robots.txt", _robots_txt, include_in_schema=False)
    allowed_origins = _load_allowed_origins()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _configure_dependency_overrides(
        application,
        repository,
        history_store=history_store,
        checkpoint_store=checkpoint_store,
        plugin_installation_store=plugin_installation_store,
        credential_service=credential_service,
    )
    _configure_application(application)
    return application


__all__ = ["create_app"]
