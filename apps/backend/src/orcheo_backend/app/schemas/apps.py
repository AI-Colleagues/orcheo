"""Request and response contracts for the Hosted Apps control plane."""

from __future__ import annotations
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from orcheo.hosted_apps import (
    AppManifest,
    AppVisibility,
    HostedApp,
    PublicationState,
)


class AppCreateRequest(BaseModel):
    """Body used to atomically create an app and reserve its first alias."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    alias: str = Field(min_length=3, max_length=48)
    description: str | None = Field(default=None, max_length=4000)


class AppUpdateRequest(BaseModel):
    """Mutable draft metadata; privileged fields are enforced by route policy."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    visibility: AppVisibility | None = None


class AppAliasRequest(BaseModel):
    """Admin-only replacement alias."""

    model_config = ConfigDict(extra="forbid")

    alias: str = Field(min_length=3, max_length=48)


class HostedAppResponse(BaseModel):
    """Safe app metadata returned to current workspace members."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    alias: str
    name: str
    description: str | None
    visibility: AppVisibility
    publication_state: PublicationState
    state: str
    is_archived: bool
    active_release_id: UUID | None
    active_deployment_id: UUID | None
    permission_revision: int
    published_permission_revision: int | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(
        cls,
        app: HostedApp,
        *,
        alias: str,
        active_deployment_id: UUID | None = None,
    ) -> HostedAppResponse:
        """Build a response without exposing any storage or runtime secrets."""
        return cls(
            id=app.id,
            workspace_id=app.workspace_id,
            alias=alias,
            name=app.name,
            description=app.description,
            visibility=app.visibility,
            publication_state=app.publication_state,
            state=app.derived_state,
            is_archived=app.is_archived,
            active_release_id=app.active_release_id,
            active_deployment_id=active_deployment_id,
            permission_revision=app.permission_revision,
            published_permission_revision=app.published_permission_revision,
            created_at=app.created_at,
            updated_at=app.updated_at,
        )


class HostedAppListResponse(BaseModel):
    """Cursor-ready list response for workspace-scoped app summaries."""

    model_config = ConfigDict(extra="forbid")

    apps: list[HostedAppResponse]
    next_cursor: str | None = None


class AppPublishRequest(BaseModel):
    """Exact draft capability revision acknowledged by an administrator."""

    model_config = ConfigDict(extra="forbid")

    acknowledged_permission_revision: int = Field(ge=1)


class AppPublishResponse(BaseModel):
    """Canonical immutable release selected by a publish or rollback."""

    model_config = ConfigDict(extra="forbid")

    app_id: UUID
    active_release_id: UUID
    active_deployment_id: UUID
    published_permission_revision: int
    state: str
    url: str


class AppDeploymentResponse(BaseModel):
    """Safe deployment status without provider object keys."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    status: str
    archive_sha256: str | None
    manifest_sha256: str | None
    app_manifest: AppManifest | None
    validation_error_code: str | None
    validation_error_message: str | None
    created_at: datetime


class AppBindingRequest(BaseModel):
    """Draft binding contract copied into an immutable release on publish."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=63)
    workflow_id: UUID
    workflow_version_id: UUID
    access_mode: str = Field(pattern="^(anonymous|authenticated)$")
    input_schema: dict = Field(default_factory=dict)
    output_projection: dict = Field(default_factory=dict)
    visitor_can_read_output: bool = False
    visitor_can_read_sanitized_errors: bool = False
    limits: dict[str, int] = Field(default_factory=dict)

    @field_validator("limits")
    @classmethod
    def validate_limits(cls, value: dict[str, int]) -> dict[str, int]:
        """Accept only documented positive bounded runtime limits."""
        maxima = {
            "per_ip_per_minute": 10_000,
            "per_session_per_minute": 10_000,
            "per_app_per_minute": 100_000,
            "max_concurrency": 100,
            "timeout_seconds": 3_600,
            "input_max_bytes": 1_048_576,
            "output_max_bytes": 1_048_576,
        }
        if set(value) - set(maxima) or any(
            amount <= 0 or amount > maxima[name] for name, amount in value.items()
        ):
            raise ValueError("Binding limits are invalid or unsupported.")
        return value

    @field_validator("output_projection")
    @classmethod
    def validate_projection(cls, value: dict) -> dict:
        """Support only an explicit bounded top-level field allowlist."""
        if not value:
            return value
        fields = value.get("fields")
        if (
            set(value) != {"fields"}
            or not isinstance(fields, list)
            or len(fields) > 100
            or any(not isinstance(item, str) or not item for item in fields)
        ):
            raise ValueError(
                "Output projection must contain only a bounded 'fields' list."
            )
        return value


class AppBindingResponse(BaseModel):
    """Safe draft binding including immutable executable evidence."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    workflow_id: UUID
    workflow_version_id: UUID
    workflow_execution_sha256: str
    runnable_config_snapshot: dict
    access_mode: str
    input_schema: dict
    output_projection: dict
    visitor_can_read_output: bool
    visitor_can_read_sanitized_errors: bool
    limits: dict[str, int]


class AppCollectionRequest(BaseModel):
    """Stable app-data collection definition."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=63)
    scope: str = Field(pattern="^(shared|user)$")
    read_access: str = Field(pattern="^(anonymous|authenticated)$")
    write_access: str = Field(pattern="^(anonymous|authenticated)$")
    max_document_bytes: int = Field(gt=0, le=1_048_576)
    max_records: int = Field(gt=0, le=1_000_000)

    @model_validator(mode="after")
    def validate_user_scope(self) -> AppCollectionRequest:
        """User-scoped records can only be accessed by authenticated visitors."""
        if self.scope == "user" and (
            self.read_access != "authenticated" or self.write_access != "authenticated"
        ):
            raise ValueError("User-scoped collections require authenticated access.")
        return self


class AppCollectionResponse(BaseModel):
    """Safe stable-id collection metadata."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    scope: str
    read_access: str
    write_access: str
    max_document_bytes: int
    max_records: int


class AppAuditResponse(BaseModel):
    """Workspace-safe app mutation evidence."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    action: str
    actor: str
    created_at: datetime
