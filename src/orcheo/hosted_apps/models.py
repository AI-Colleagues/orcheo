"""Framework-independent Hosted Apps domain models and validation."""

from __future__ import annotations
import re
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4
from pydantic import Field, field_validator
from orcheo.hosted_apps.errors import AliasValidationError, ReservedAliasError
from orcheo.models.base import OrcheoBaseModel, _utcnow


__all__ = [
    "AliasLifecycle",
    "AppAlias",
    "AppBinding",
    "AppCollection",
    "AppDeployment",
    "AppRelease",
    "AppRuntimeRun",
    "AppSession",
    "AppUpload",
    "AppVisibility",
    "AuthorizationCode",
    "BundleFile",
    "BundleManifest",
    "DEFAULT_RESERVED_ALIASES",
    "DeploymentStatus",
    "DispatchOutbox",
    "HostedApp",
    "IdempotencyRecord",
    "LoginTransaction",
    "ModerationBlock",
    "PlatformAuditEvent",
    "PublicationState",
    "QuotaLease",
    "RuntimeGeneration",
    "normalize_alias",
    "normalize_logical_name",
]


DEFAULT_RESERVED_ALIASES = frozenset(
    {
        "api",
        "admin",
        "auth",
        "cdn",
        "mail",
        "metrics",
        "status",
        "studio",
        "support",
        "www",
    }
)
_ALIAS_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,46}[a-z0-9])$")
_LOGICAL_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")


def normalize_alias(
    value: str, *, reserved: frozenset[str] = DEFAULT_RESERVED_ALIASES
) -> str:
    """Normalize and validate a globally routable Hosted Apps alias."""
    candidate = value.strip().lower()
    if not _ALIAS_RE.fullmatch(candidate):
        msg = (
            "App aliases must be 3-48 lowercase ASCII characters, start and end "
            "with an alphanumeric character, and use only internal hyphens."
        )
        raise AliasValidationError(msg)
    if candidate in reserved:
        msg = "This app alias is reserved by the platform."
        raise ReservedAliasError(msg)
    return candidate


def normalize_logical_name(value: str) -> str:
    """Normalize a binding or collection name without changing its identity."""
    candidate = value.strip().lower()
    if not _LOGICAL_NAME_RE.fullmatch(candidate):
        msg = (
            "Names must start with a lowercase letter and contain only lowercase "
            "letters, digits, underscores, or hyphens (maximum 63 characters)."
        )
        raise ValueError(msg)
    return candidate


class AppVisibility(str, Enum):
    """Asset visibility captured in draft policy and immutable releases."""

    PUBLIC = "public"
    PRIVATE = "private"


class PublicationState(str, Enum):
    """Publication lifecycle independent from archive and suspension overlays."""

    DRAFT = "draft"
    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"


class AliasLifecycle(str, Enum):
    """State of a globally unique alias reservation."""

    APP = "app"
    PLATFORM = "platform"
    TOMBSTONE = "tombstone"


class DeploymentStatus(str, Enum):
    """Validation status for immutable deployment candidates."""

    PENDING = "pending"
    VALIDATING = "validating"
    READY = "ready"
    FAILED = "failed"
    EXPIRED = "expired"


class HostedApp(OrcheoBaseModel):
    """Workspace-owned application and its mutable draft policy state."""

    id: UUID = Field(default_factory=uuid4)
    workspace_id: UUID
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    visibility: AppVisibility = AppVisibility.PUBLIC
    publication_state: PublicationState = PublicationState.DRAFT
    is_archived: bool = False
    active_release_id: UUID | None = None
    permission_revision: int = Field(default=1, ge=1)
    published_permission_revision: int | None = Field(default=None, ge=1)
    external_origins: tuple[str, ...] = ()
    suspended_at: datetime | None = None
    suspended_reason: str | None = None
    suspended_by: str | None = None
    created_by: str
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    published_at: datetime | None = None
    archived_at: datetime | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        candidate = str(value).strip()
        if not candidate:
            msg = "App name must not be empty."
            raise ValueError(msg)
        return candidate

    @property
    def derived_state(self) -> str:
        """Return the safe display state after lifecycle overlay precedence."""
        if self.suspended_at is not None:
            return "suspended"
        if self.is_archived:
            return "archived"
        return self.publication_state.value


class AppAlias(OrcheoBaseModel):
    """A globally unique app, platform, or tombstoned alias reservation."""

    alias: str
    app_id: UUID | None = None
    workspace_id: UUID | None = None
    reserved_kind: AliasLifecycle = AliasLifecycle.APP
    tombstoned_until: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @field_validator("alias", mode="before")
    @classmethod
    def _normalize_alias(cls, value: object) -> str:
        return normalize_alias(str(value))


class AppUpload(OrcheoBaseModel):
    """One-time staged archive upload prior to asynchronous validation."""

    id: UUID = Field(default_factory=uuid4)
    deployment_id: UUID
    app_id: UUID
    workspace_id: UUID
    status: str = "pending"
    staging_key: str
    expected_size_bytes: int = Field(gt=0)
    expected_sha256: str | None = None
    actual_size_bytes: int | None = Field(default=None, ge=0)
    actual_sha256: str | None = None
    expires_at: datetime
    completed_at: datetime | None = None
    created_by: str
    created_at: datetime = Field(default_factory=_utcnow)


class AppDeployment(OrcheoBaseModel):
    """Immutable extracted deployment metadata; object keys remain server-only."""

    id: UUID = Field(default_factory=uuid4)
    app_id: UUID
    workspace_id: UUID
    status: DeploymentStatus = DeploymentStatus.PENDING
    archive_sha256: str | None = None
    manifest_sha256: str | None = None
    validation_error_code: str | None = None
    validation_error_message: str | None = None
    created_by: str
    created_at: datetime = Field(default_factory=_utcnow)
    validated_at: datetime | None = None


class AppBinding(OrcheoBaseModel):
    """Mutable draft workflow grant whose content is copied into releases."""

    id: UUID = Field(default_factory=uuid4)
    app_id: UUID
    workspace_id: UUID
    name: str
    workflow_id: UUID
    workflow_version_id: UUID
    workflow_execution_sha256: str
    runnable_config_snapshot: dict[str, Any] = Field(default_factory=dict)
    access_mode: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_projection: dict[str, Any] = Field(default_factory=dict)
    visitor_can_read_output: bool = False
    visitor_can_read_sanitized_errors: bool = False
    limits: dict[str, int] = Field(default_factory=dict)
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        return normalize_logical_name(str(value))


class AppCollection(OrcheoBaseModel):
    """Stable declared app-data collection identity."""

    id: UUID = Field(default_factory=uuid4)
    app_id: UUID
    workspace_id: UUID
    name: str
    scope: str
    read_access: str
    write_access: str
    max_document_bytes: int = Field(gt=0)
    max_records: int = Field(gt=0)
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        return normalize_logical_name(str(value))


class AppRelease(OrcheoBaseModel):
    """Append-only published snapshot of deployment and capabilities."""

    id: UUID = Field(default_factory=uuid4)
    workspace_id: UUID
    app_id: UUID
    deployment_id: UUID
    permission_revision: int = Field(ge=1)
    visibility: AppVisibility
    capability_snapshot: dict[str, Any]
    csp_snapshot: dict[str, Any]
    snapshot_sha256: str
    created_by: str
    created_at: datetime = Field(default_factory=_utcnow)


class BundleFile(OrcheoBaseModel):
    """Authoritative metadata for one deployment asset."""

    size_bytes: int = Field(ge=0)
    sha256: str
    content_type: str


class BundleManifest(OrcheoBaseModel):
    """Validator-generated asset and HTML CSP policy manifest."""

    version: int = 1
    index: str = "index.html"
    files: dict[str, BundleFile]
    html_policy: dict[str, dict[str, tuple[str, ...]]] = Field(default_factory=dict)


class AuthorizationCode(OrcheoBaseModel):
    """Hashed short-lived single-use code for an app-session exchange."""

    id: UUID = Field(default_factory=uuid4)
    code_hash: str
    app_id: UUID
    workspace_id: UUID
    user_id: str
    redirect_uri: str
    code_challenge: str
    expires_at: datetime
    consumed_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class LoginTransaction(OrcheoBaseModel):
    """Gateway-side server record backing a host-only opaque login cookie."""

    id: UUID = Field(default_factory=uuid4)
    secret_hash: str
    app_id: UUID
    app_host: str
    state_hash: str
    pkce_verifier: str
    return_to: str
    expires_at: datetime
    created_at: datetime = Field(default_factory=_utcnow)


class AppSession(OrcheoBaseModel):
    """Hashed host-bound session for a publisher-workspace member."""

    id: UUID = Field(default_factory=uuid4)
    secret_hash: str
    app_id: UUID
    workspace_id: UUID
    app_host: str
    user_id: str
    runtime_generation: int = Field(ge=0)
    expires_at: datetime
    idle_expires_at: datetime
    revoked_at: datetime | None = None
    last_seen_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class AppRuntimeRun(OrcheoBaseModel):
    """Opaque app-facing mapping to one internal workflow run."""

    id: UUID = Field(default_factory=uuid4)
    public_handle: str
    workspace_id: UUID
    app_id: UUID
    release_id: UUID
    deployment_id: UUID
    binding_id: UUID
    binding_snapshot_sha256: str
    workflow_run_id: UUID
    idempotency_key_hash: str
    expires_at: datetime
    visitor_user_id: str | None = None
    originating_session_id: UUID | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class IdempotencyRecord(OrcheoBaseModel):
    """Scoped replay record for one durable runtime request."""

    id: UUID = Field(default_factory=uuid4)
    scope_hash: str
    request_hash: str
    public_handle: str
    expires_at: datetime
    created_at: datetime = Field(default_factory=_utcnow)


class QuotaLease(OrcheoBaseModel):
    """A distributed reservation whose expiry supports crash reconciliation."""

    id: UUID = Field(default_factory=uuid4)
    workspace_id: UUID
    operation: str
    amount: int = Field(gt=0)
    expires_at: datetime
    settled_at: datetime | None = None
    released_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class DispatchOutbox(OrcheoBaseModel):
    """Transactional event used to dispatch validated deployments and app runs."""

    id: UUID = Field(default_factory=uuid4)
    workspace_id: UUID
    kind: str
    aggregate_id: UUID
    payload: dict[str, Any] = Field(default_factory=dict)
    attempts: int = Field(default=0, ge=0)
    next_attempt_at: datetime = Field(default_factory=_utcnow)
    delivered_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class ModerationBlock(OrcheoBaseModel):
    """Platform-level block that overrides workspace-controlled lifecycle state."""

    id: UUID = Field(default_factory=uuid4)
    target_kind: str
    target_id: str
    reason_code: str
    reason_detail: str | None = None
    created_by: str
    created_at: datetime = Field(default_factory=_utcnow)
    lifted_by: str | None = None
    lifted_at: datetime | None = None


class PlatformAuditEvent(OrcheoBaseModel):
    """Audit event for globally scoped Hosted Apps moderation mutations."""

    id: UUID = Field(default_factory=uuid4)
    action: str
    actor: str
    target_kind: str
    target_id: str
    reason_code: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class RuntimeGeneration(OrcheoBaseModel):
    """Durable cross-plane runtime state; not a process-local feature flag."""

    generation: int = Field(default=0, ge=0)
    enabled: bool = False
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=_utcnow)
