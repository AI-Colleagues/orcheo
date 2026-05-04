"""Tests for creating, updating, and deleting credential templates."""

from __future__ import annotations
from datetime import UTC, datetime
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from orcheo.models import (
    CredentialIssuancePolicy,
    CredentialKind,
    CredentialScope,
    CredentialTemplate,
)
from orcheo.vault import WorkflowScopeError
from orcheo_backend.app import (
    create_credential_template,
    delete_credential_template,
    update_credential_template,
)
from orcheo_backend.app.schemas.credentials import (
    CredentialTemplateCreateRequest,
    CredentialTemplateUpdateRequest,
)


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


def test_create_credential_template_success() -> None:
    """Create credential template endpoint creates template."""

    template_id = uuid4()

    class Vault:
        def create_template(
            self,
            name,
            provider,
            scopes,
            actor,
            description,
            scope,
            kind,
            issuance_policy,
        ):
            return CredentialTemplate(
                id=template_id,
                name=name,
                provider=provider,
                scopes=scopes,
                kind=kind,
                scope=scope or CredentialScope(),
                issuance_policy=issuance_policy or CredentialIssuancePolicy(),
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = CredentialTemplateCreateRequest(
        name="Test Template",
        provider="slack",
        scopes=["chat:write"],
        actor="admin",
        kind=CredentialKind.OAUTH,
    )

    result = create_credential_template(request, Vault())

    assert result.id == str(template_id)
    assert result.name == "Test Template"


@pytest.mark.asyncio()
async def test_update_credential_template_success() -> None:
    """Update credential template endpoint updates template."""

    template_id = uuid4()

    class Vault:
        def update_template(
            self,
            template_id,
            actor,
            name,
            scopes,
            description,
            scope,
            kind,
            issuance_policy,
            context,
        ):
            return CredentialTemplate(
                id=template_id,
                name=name or "Updated Template",
                provider="slack",
                scopes=scopes or ["chat:write"],
                kind=kind or CredentialKind.OAUTH,
                scope=scope or CredentialScope(),
                issuance_policy=issuance_policy or CredentialIssuancePolicy(),
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = CredentialTemplateUpdateRequest(
        actor="admin",
        name="Updated Template",
    )

    result = await update_credential_template(
        template_id,
        request,
        Vault(),
        _Repository(),
    )

    assert result.id == str(template_id)
    assert result.name == "Updated Template"


@pytest.mark.asyncio()
async def test_update_credential_template_not_found() -> None:
    """Update credential template raises 404 for missing template."""
    from orcheo.vault import CredentialTemplateNotFoundError

    template_id = uuid4()

    class Vault:
        def update_template(
            self,
            template_id,
            actor,
            name,
            scopes,
            description,
            scope,
            kind,
            issuance_policy,
            context,
        ):
            raise CredentialTemplateNotFoundError("not found")

    request = CredentialTemplateUpdateRequest(actor="admin")

    with pytest.raises(HTTPException) as exc_info:
        await update_credential_template(
            template_id,
            request,
            Vault(),
            _Repository(),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_update_credential_template_scope_error() -> None:
    """Update credential template raises 403 for scope violations."""

    template_id = uuid4()

    class Vault:
        def update_template(
            self,
            template_id,
            actor,
            name,
            scopes,
            description,
            scope,
            kind,
            issuance_policy,
            context,
        ):
            raise WorkflowScopeError("Access denied")

    request = CredentialTemplateUpdateRequest(actor="admin")

    with pytest.raises(HTTPException) as exc_info:
        await update_credential_template(
            template_id,
            request,
            Vault(),
            _Repository(),
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio()
async def test_delete_credential_template_success() -> None:
    """Delete credential template endpoint deletes template."""

    template_id = uuid4()
    deleted_id = None

    class Vault:
        def delete_template(self, template_id, context=None):
            nonlocal deleted_id
            deleted_id = template_id

    response = await delete_credential_template(template_id, Vault(), _Repository())

    assert response.status_code == 204
    assert deleted_id == template_id


@pytest.mark.asyncio()
async def test_delete_credential_template_not_found() -> None:
    """Delete credential template raises 404 for missing template."""
    from orcheo.vault import CredentialTemplateNotFoundError

    template_id = uuid4()

    class Vault:
        def delete_template(self, template_id, context=None):
            raise CredentialTemplateNotFoundError("not found")

    with pytest.raises(HTTPException) as exc_info:
        await delete_credential_template(template_id, Vault(), _Repository())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_delete_credential_template_scope_error() -> None:
    """Delete credential template raises 403 for scope violations."""

    template_id = uuid4()

    class Vault:
        def delete_template(self, template_id, context=None):
            raise WorkflowScopeError("Access denied")

    with pytest.raises(HTTPException) as exc_info:
        await delete_credential_template(template_id, Vault(), _Repository())

    assert exc_info.value.status_code == 403
