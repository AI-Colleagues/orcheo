"""Tests for issuing credentials from templates."""

from __future__ import annotations
from datetime import UTC, datetime
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from orcheo.models import (
    CredentialKind,
    CredentialMetadata,
    CredentialScope,
    EncryptionEnvelope,
)
from orcheo.vault import WorkflowScopeError
from orcheo_backend.app import issue_credential_from_template
from orcheo_backend.app.schemas.credentials import CredentialIssuanceRequest


class _Repository:
    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        tenant_id: str | None = None,
    ) -> UUID:
        del include_archived
        return UUID(str(workflow_ref))


@pytest.mark.asyncio()
async def test_issue_credential_from_template_success() -> None:
    """Issue credential from template endpoint creates credential."""

    template_id = uuid4()
    cred_id = uuid4()

    class Service:
        def issue_from_template(
            self,
            template_id,
            secret,
            actor,
            name,
            scopes,
            context,
            oauth_tokens,
        ):
            return CredentialMetadata(
                id=cred_id,
                name=name or "Issued Credential",
                provider="slack",
                kind=CredentialKind.OAUTH,
                scope=CredentialScope(),
                template_id=template_id,
                encryption=EncryptionEnvelope(
                    algorithm="aes-256-gcm",
                    key_id="test-key",
                    ciphertext="encrypted",
                ),
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = CredentialIssuanceRequest(
        template_id=template_id,
        actor="admin",
        name="Issued Credential",
        secret="test-secret",
    )

    result = await issue_credential_from_template(
        template_id,
        request,
        _Repository(),
        Service(),
    )

    assert result.credential_id == str(cred_id)
    assert result.template_id == str(template_id)


@pytest.mark.asyncio()
async def test_issue_credential_from_template_not_configured() -> None:
    """Issue credential requires configured service."""

    template_id = uuid4()

    request = CredentialIssuanceRequest(
        template_id=template_id,
        actor="admin",
        secret="test-secret",
    )

    with pytest.raises(HTTPException) as exc_info:
        await issue_credential_from_template(
            template_id,
            request,
            _Repository(),
            None,
        )

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio()
async def test_issue_credential_from_template_not_found() -> None:
    """Issue credential raises 404 for missing template."""
    from orcheo.vault import CredentialTemplateNotFoundError

    template_id = uuid4()

    class Service:
        def issue_from_template(
            self,
            template_id,
            secret,
            actor,
            name,
            scopes,
            context,
            oauth_tokens,
        ):
            raise CredentialTemplateNotFoundError("not found")

    request = CredentialIssuanceRequest(
        template_id=template_id,
        actor="admin",
        secret="test-secret",
    )

    with pytest.raises(HTTPException) as exc_info:
        await issue_credential_from_template(
            template_id,
            request,
            _Repository(),
            Service(),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_issue_credential_from_template_scope_error() -> None:
    """Issue credential raises 403 for scope violations."""

    template_id = uuid4()

    class Service:
        def issue_from_template(
            self,
            template_id,
            secret,
            actor,
            name,
            scopes,
            context,
            oauth_tokens,
        ):
            raise WorkflowScopeError("Access denied")

    request = CredentialIssuanceRequest(
        template_id=template_id,
        actor="admin",
        secret="test-secret",
    )

    with pytest.raises(HTTPException) as exc_info:
        await issue_credential_from_template(
            template_id,
            request,
            _Repository(),
            Service(),
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio()
async def test_issue_credential_from_template_validation_error() -> None:
    """Issue credential raises 400 for validation errors."""

    template_id = uuid4()

    class Service:
        def issue_from_template(
            self,
            template_id,
            secret,
            actor,
            name,
            scopes,
            context,
            oauth_tokens,
        ):
            raise ValueError("Invalid secret format")

    request = CredentialIssuanceRequest(
        template_id=template_id,
        actor="admin",
        secret="invalid",
    )

    with pytest.raises(HTTPException) as exc_info:
        await issue_credential_from_template(
            template_id,
            request,
            _Repository(),
            Service(),
        )

    assert exc_info.value.status_code == 400
