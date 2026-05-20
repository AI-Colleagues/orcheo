"""Pydantic schemas for service token management endpoints."""

from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field


class CreateServiceTokenRequest(BaseModel):
    """Request payload for creating a new service token.

    The token is always scoped to the workspace that mints it, so the caller
    does not supply workspace identifiers.
    """

    identifier: str | None = Field(
        default=None,
        description="Optional identifier for the token (auto-generated if omitted)",
    )
    scopes: list[str] = Field(
        default_factory=list,
        description="Scopes/permissions granted to the token",
    )
    expires_in_seconds: int | None = Field(
        default=None,
        ge=60,
        description="Optional expiration time in seconds (no expiration if omitted)",
    )


class ServiceTokenResponse(BaseModel):
    """Response payload describing a service token."""

    identifier: str = Field(description="Unique identifier for the token")
    secret: str | None = Field(
        default=None,
        description="Raw token secret (only shown once on creation)",
    )
    secret_preview: str | None = Field(
        default=None,
        description="Last characters of the secret for display in listings",
    )
    scopes: list[str] = Field(description="Scopes granted to the token")
    workspace_ids: list[str] = Field(description="Workspaces the token can access")
    issued_at: datetime | None = Field(description="Token issuance timestamp")
    expires_at: datetime | None = Field(description="Token expiration timestamp")
    last_used_at: datetime | None = Field(
        default=None,
        description="Last usage timestamp",
    )
    use_count: int | None = Field(
        default=None,
        description="Number of times token was used",
    )
    revoked_at: datetime | None = Field(
        default=None,
        description="Revocation timestamp",
    )
    revocation_reason: str | None = Field(
        default=None,
        description="Reason for revocation",
    )
    rotated_to: str | None = Field(
        default=None,
        description="Identifier of replacement token",
    )
    message: str | None = Field(default=None, description="Additional information")


class RotateServiceTokenRequest(BaseModel):
    """Request payload for rotating a service token."""

    overlap_seconds: int = Field(
        default=300,
        ge=0,
        description="Grace period where both old and new tokens are valid",
    )
    expires_in_seconds: int | None = Field(
        default=None,
        ge=60,
        description="Optional expiration time for new token in seconds",
    )


class RevokeServiceTokenRequest(BaseModel):
    """Request payload for revoking a service token."""

    reason: str = Field(description="Reason for revoking the token")


class ServiceTokenListResponse(BaseModel):
    """Response payload wrapping a collection of service tokens."""

    tokens: list[ServiceTokenResponse] = Field(description="List of service tokens")
    total: int = Field(description="Total number of tokens")


__all__ = [
    "CreateServiceTokenRequest",
    "ServiceTokenListResponse",
    "ServiceTokenResponse",
    "RevokeServiceTokenRequest",
    "RotateServiceTokenRequest",
]
