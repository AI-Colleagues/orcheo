"""Credential-related schemas for CRUD, templates, and governance."""

from __future__ import annotations
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field
from orcheo.models import (
    CredentialHealthStatus,
    CredentialKind,
)


class CredentialValidationRequest(BaseModel):
    """Request body for on-demand credential validation."""

    actor: str = Field(default="system")


class CredentialCreateRequest(BaseModel):
    """Request payload for creating a credential entry."""

    name: str
    provider: str
    secret: str
    actor: str = Field(default="system")
    scopes: list[str] = Field(default_factory=list)
    access: Literal["scoped", "shared"] = "scoped"
    workflow_id: str | None = None
    kind: CredentialKind = CredentialKind.SECRET


class CredentialUpdateRequest(BaseModel):
    """Request payload for updating a credential entry."""

    name: str | None = None
    provider: str | None = None
    secret: str | None = None
    actor: str = Field(default="system")
    access: Literal["scoped", "shared"] | None = None
    workflow_id: str | None = None


class CredentialHealthItem(BaseModel):
    """Represents the health state for an individual credential."""

    credential_id: str
    name: str
    provider: str
    status: CredentialHealthStatus
    last_checked_at: datetime | None = None
    failure_reason: str | None = None


class CredentialHealthResponse(BaseModel):
    """Response payload describing workflow credential health."""

    workflow_id: str
    status: CredentialHealthStatus
    checked_at: datetime | None = None
    credentials: list[CredentialHealthItem] = Field(default_factory=list)


class CredentialReadinessItem(BaseModel):
    """Represents whether a referenced credential is available to a workflow."""

    name: str
    placeholders: list[str] = Field(default_factory=list)
    available: bool
    credential_id: str | None = None
    provider: str | None = None


class CredentialReadinessResponse(BaseModel):
    """Response payload describing workflow credential availability."""

    workflow_id: str
    status: Literal["ready", "missing", "not_required"]
    referenced_credentials: list[CredentialReadinessItem] = Field(default_factory=list)
    available_credentials: list[str] = Field(default_factory=list)
    missing_credentials: list[str] = Field(default_factory=list)


class CredentialScopePayload(BaseModel):
    """Schema describing credential scoping configuration."""

    workflow_ids: list[UUID] = Field(default_factory=list)
    workspace_ids: list[UUID] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)


class CredentialIssuancePolicyPayload(BaseModel):
    """Schema describing issuance policy defaults for a template."""

    require_refresh_token: bool = False
    rotation_period_days: int | None = Field(default=None, ge=1)
    expiry_threshold_minutes: int = Field(default=60, ge=1)


class OAuthTokenRequest(BaseModel):
    """Plaintext OAuth token payload submitted by clients."""

    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: datetime | None = None


class CredentialTemplateCreateRequest(BaseModel):
    """Request payload for creating a credential template."""

    name: str
    provider: str
    scopes: list[str] = Field(default_factory=list)
    description: str | None = None
    kind: CredentialKind = CredentialKind.SECRET
    scope: CredentialScopePayload | None = None
    issuance_policy: CredentialIssuancePolicyPayload | None = None
    actor: str = Field(default="system")


class CredentialTemplateUpdateRequest(BaseModel):
    """Request payload for mutating credential template metadata."""

    name: str | None = None
    scopes: list[str] | None = None
    description: str | None = None
    kind: CredentialKind | None = None
    scope: CredentialScopePayload | None = None
    issuance_policy: CredentialIssuancePolicyPayload | None = None
    actor: str = Field(default="system")


class CredentialTemplateResponse(BaseModel):
    """Response schema describing a credential template."""

    id: str
    name: str
    provider: str
    scopes: list[str]
    description: str | None
    kind: CredentialKind
    scope: CredentialScopePayload
    issuance_policy: CredentialIssuancePolicyPayload
    created_at: datetime
    updated_at: datetime


class CredentialIssuanceRequest(BaseModel):
    """Request payload for issuing a credential from a template."""

    template_id: UUID
    secret: str
    actor: str = Field(default="system")
    name: str | None = None
    scopes: list[str] | None = None
    workflow_id: str | None = None
    oauth_tokens: OAuthTokenRequest | None = None


class CredentialIssuanceResponse(BaseModel):
    """Response describing the issued credential metadata."""

    credential_id: str
    name: str
    provider: str
    kind: CredentialKind
    template_id: str | None
    created_at: datetime
    updated_at: datetime


class CredentialVaultEntryResponse(BaseModel):
    """Response payload describing a credential stored in the vault."""

    id: str
    name: str
    provider: str
    kind: CredentialKind
    created_at: datetime
    updated_at: datetime
    last_rotated_at: datetime | None
    owner: str | None
    access: Literal["scoped", "shared"]
    workflow_id: str | None = None
    status: CredentialHealthStatus
    secret_preview: str | None = None


class CredentialSecretResponse(BaseModel):
    """Response payload describing a revealed credential secret."""

    id: str
    secret: str
