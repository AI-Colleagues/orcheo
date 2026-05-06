"""Schemas for system version metadata responses."""

from __future__ import annotations
from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel


class PackageVersionStatus(BaseModel):
    """Version metadata for one package/component."""

    package: str
    current_version: str | None = None
    latest_version: str | None = None
    minimum_recommended_version: str | None = None
    release_notes_url: str | None = None
    update_available: bool


class SystemInfoResponse(BaseModel):
    """Combined backend/CLI/canvas version metadata."""

    backend: PackageVersionStatus
    cli: PackageVersionStatus
    canvas: PackageVersionStatus
    checked_at: datetime


class SystemPluginStatus(BaseModel):
    """Plugin status as observed by the current backend process."""

    name: str
    enabled: bool
    status: str
    version: str
    exports: list[str]
    loaded: bool
    load_error: str | None = None


class SystemPluginsResponse(BaseModel):
    """Installed plugin inventory for the current backend process."""

    plugins: list[SystemPluginStatus]


class ExternalAgentProviderName(StrEnum):
    """Supported worker-scoped external agent providers."""

    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    GEMINI = "gemini"


class ExternalAgentProviderState(StrEnum):
    """Availability states for a worker-scoped external agent provider."""

    UNKNOWN = "unknown"
    CHECKING = "checking"
    INSTALLING = "installing"
    NOT_INSTALLED = "not_installed"
    NEEDS_LOGIN = "needs_login"
    AUTHENTICATING = "authenticating"
    READY = "ready"
    ERROR = "error"


class ExternalAgentLoginSessionState(StrEnum):
    """Lifecycle states for one OAuth login session."""

    PENDING = "pending"
    INSTALLING = "installing"
    AWAITING_OAUTH = "awaiting_oauth"
    AUTHENTICATED = "authenticated"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class ExternalAgentProviderStatus(BaseModel):
    """Current workspace-scoped readiness for one external agent provider."""

    provider: ExternalAgentProviderName
    display_name: str
    state: ExternalAgentProviderState
    installed: bool
    authenticated: bool
    workspace_id: str | None = None
    supports_oauth: bool = True
    resolved_version: str | None = None
    executable_path: str | None = None
    checked_at: datetime | None = None
    last_auth_ok_at: datetime | None = None
    detail: str | None = None
    active_session_id: str | None = None


class ExternalAgentLoginSession(BaseModel):
    """Workspace-scoped OAuth login session surfaced to Canvas."""

    session_id: str
    provider: ExternalAgentProviderName
    display_name: str
    state: ExternalAgentLoginSessionState
    workspace_id: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    auth_url: str | None = None
    device_code: str | None = None
    detail: str | None = None
    recent_output: str | None = None
    resolved_version: str | None = None
    executable_path: str | None = None


class ExternalAgentLoginInputRequest(BaseModel):
    """Operator-provided input forwarded to a worker login session."""

    input_text: str


class ExternalAgentsResponse(BaseModel):
    """Combined worker-scoped status for all external agent providers."""

    providers: list[ExternalAgentProviderStatus]
