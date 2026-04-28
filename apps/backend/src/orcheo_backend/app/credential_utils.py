"""Helpers for credential-related API payloads."""

from __future__ import annotations
from typing import Literal
from uuid import UUID
from orcheo.models import (
    CredentialIssuancePolicy,
    CredentialKind,
    CredentialMetadata,
    CredentialScope,
    CredentialTemplate,
    OAuthTokenSecrets,
    SecretGovernanceAlert,
)
from orcheo_backend.app.schemas.credentials import (
    CredentialIssuancePolicyPayload,
    CredentialScopePayload,
    CredentialTemplateResponse,
    CredentialVaultEntryResponse,
    OAuthTokenRequest,
)
from orcheo_backend.app.schemas.governance import GovernanceAlertResponse


def scope_from_access(
    access: Literal["scoped", "shared"],
    workflow_id: UUID | None,
) -> CredentialScope | None:
    """Derive a credential scope from the requested access label."""
    if access == "scoped":
        if workflow_id is None:
            raise ValueError("workflow_id is required when access is set to scoped")
        return CredentialScope.for_workflows(workflow_id)
    return CredentialScope.unrestricted()


def infer_credential_access(
    scope: CredentialScope,
) -> Literal["scoped", "shared"]:
    """Return a simplified access label derived from the scope."""
    if scope.is_unrestricted():
        return "shared"
    return "scoped"


def credential_to_response(
    metadata: CredentialMetadata,
) -> CredentialVaultEntryResponse:
    """Convert stored credential metadata into an API response payload."""
    owner = metadata.audit_log[0].actor if metadata.audit_log else None
    secret_preview: str | None
    if metadata.kind is CredentialKind.OAUTH:
        secret_preview = "oauth-token"
    else:
        secret_preview = "••••••••"

    return CredentialVaultEntryResponse(
        id=str(metadata.id),
        name=metadata.name,
        provider=metadata.provider,
        kind=metadata.kind,
        created_at=metadata.created_at,
        updated_at=metadata.updated_at,
        last_rotated_at=metadata.last_rotated_at,
        owner=owner,
        access=infer_credential_access(metadata.scope),
        workflow_id=(
            str(metadata.scope.workflow_ids[0]) if metadata.scope.workflow_ids else None
        ),
        status=metadata.health.status,
        secret_preview=secret_preview,
    )


def alert_to_response(alert: SecretGovernanceAlert) -> GovernanceAlertResponse:
    """Convert a governance alert into an API response payload."""
    return GovernanceAlertResponse(
        id=str(alert.id),
        kind=alert.kind,
        severity=alert.severity,
        message=alert.message,
        credential_id=str(alert.credential_id) if alert.credential_id else None,
        template_id=str(alert.template_id) if alert.template_id else None,
        is_acknowledged=alert.is_acknowledged,
        acknowledged_at=alert.acknowledged_at,
        created_at=alert.created_at,
        updated_at=alert.updated_at,
    )


def scope_to_payload(scope: CredentialScope | None) -> CredentialScopePayload | None:
    """Convert an optional scope into a payload representation."""
    if scope is None:
        return None
    return CredentialScopePayload(
        workflow_ids=list(scope.workflow_ids),
        workspace_ids=list(scope.workspace_ids),
        roles=list(scope.roles),
    )


def policy_to_payload(
    policy: CredentialIssuancePolicy | None,
) -> CredentialIssuancePolicyPayload | None:
    """Convert an optional policy into a payload representation."""
    if policy is None:
        return None
    return CredentialIssuancePolicyPayload(
        require_refresh_token=policy.require_refresh_token,
        rotation_period_days=policy.rotation_period_days,
        expiry_threshold_minutes=policy.expiry_threshold_minutes,
    )


def template_to_response(template: CredentialTemplate) -> CredentialTemplateResponse:
    """Convert a credential template into an API response payload."""
    scope_payload = scope_to_payload(template.scope) or CredentialScopePayload(
        workflow_ids=[],
        workspace_ids=[],
        roles=[],
    )
    policy_payload = (
        policy_to_payload(template.issuance_policy) or CredentialIssuancePolicyPayload()
    )
    return CredentialTemplateResponse(
        id=str(template.id),
        name=template.name,
        provider=template.provider,
        scopes=list(template.scopes),
        description=template.description,
        kind=template.kind,
        scope=scope_payload,
        issuance_policy=policy_payload,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def build_scope(
    payload: CredentialScopePayload | None,
) -> CredentialScope | None:
    """Convert an optional payload into a credential scope."""
    if payload is None:
        return None
    return CredentialScope(
        workflow_ids=list(payload.workflow_ids),
        workspace_ids=list(payload.workspace_ids),
        roles=list(payload.roles),
    )


def build_policy(
    payload: CredentialIssuancePolicyPayload | None,
) -> CredentialIssuancePolicy | None:
    """Convert an optional payload into a credential issuance policy."""
    if payload is None:
        return None
    return CredentialIssuancePolicy(
        require_refresh_token=payload.require_refresh_token,
        rotation_period_days=payload.rotation_period_days,
        expiry_threshold_minutes=payload.expiry_threshold_minutes,
    )


def build_oauth_tokens(
    payload: OAuthTokenRequest | None,
) -> OAuthTokenSecrets | None:
    """Convert an optional token payload into OAuth secrets."""
    if payload is None:
        return None
    return OAuthTokenSecrets(
        access_token=payload.access_token,
        refresh_token=payload.refresh_token,
        expires_at=payload.expires_at,
    )


__all__ = [
    "alert_to_response",
    "build_oauth_tokens",
    "build_policy",
    "build_scope",
    "credential_to_response",
    "infer_credential_access",
    "policy_to_payload",
    "scope_from_access",
    "scope_to_payload",
    "template_to_response",
]
