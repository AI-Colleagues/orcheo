"""Tests for credential CRUD endpoints."""

from __future__ import annotations
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from orcheo.models import CredentialKind, CredentialMetadata, CredentialScope
from orcheo.vault import (
    CredentialNotFoundError,
    DuplicateCredentialNameError,
    WorkflowScopeError,
)
from orcheo_backend.app.errors import WorkspaceQuotaExceededError


_MOCK_WORKSPACE = SimpleNamespace(
    workspace_id=uuid4(),
    user_id="test-user",
    slug="test-workspace",
    quotas=SimpleNamespace(
        max_credentials=1000,
        max_workflows=1000,
        max_storage_rows=1_000_000,
    ),
)


class _Repository:
    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ) -> UUID:
        del include_archived
        return UUID(str(workflow_ref))


@pytest.mark.asyncio()
async def test_list_credentials_success() -> None:
    """List credentials endpoint returns credentials."""
    from orcheo.models import EncryptionEnvelope
    from orcheo_backend.app import list_credentials

    cred1_id = uuid4()
    cred2_id = uuid4()

    creds = [
        CredentialMetadata(
            id=cred1_id,
            name="Cred 1",
            provider="slack",
            kind=CredentialKind.OAUTH,
            scope=CredentialScope(),
            encryption=EncryptionEnvelope(
                algorithm="aes-256-gcm",
                key_id="test-key",
                ciphertext="encrypted",
            ),
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        ),
        CredentialMetadata(
            id=cred2_id,
            name="Cred 2",
            provider="github",
            kind=CredentialKind.SECRET,
            scope=CredentialScope(),
            encryption=EncryptionEnvelope(
                algorithm="aes-256-gcm",
                key_id="test-key",
                ciphertext="encrypted",
            ),
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        ),
    ]

    class Vault:
        def list_credentials(self, context=None, *, workspace_id=None):
            return creds

        def list_all_credentials(self, *, workspace_id=None):
            return creds

    result = await list_credentials(Vault(), _Repository(), _MOCK_WORKSPACE)

    assert len(result) == 2
    assert result[0].id == str(cred1_id)
    assert result[1].id == str(cred2_id)


@pytest.mark.asyncio()
async def test_list_credentials_with_workflow_context() -> None:
    """List credentials uses workflow context for filtering."""
    from orcheo_backend.app import list_credentials

    workflow_id = uuid4()
    context_received = None

    class Vault:
        def list_credentials(self, context=None, *, workspace_id=None):
            nonlocal context_received
            context_received = context
            return []

    await list_credentials(
        Vault(), _Repository(), _MOCK_WORKSPACE, workflow_id=str(workflow_id)
    )

    assert context_received is not None
    assert context_received.workflow_id == workflow_id


@pytest.mark.asyncio()
async def test_create_credential_success() -> None:
    """Create credential endpoint creates and returns credential."""
    from orcheo.models import EncryptionEnvelope
    from orcheo_backend.app import create_credential
    from orcheo_backend.app.schemas.credentials import CredentialCreateRequest

    cred_id = uuid4()

    class Vault:
        def list_all_credentials(self, *, workspace_id=None):
            return []

        def create_credential(
            self,
            name,
            provider,
            scopes,
            secret,
            actor,
            scope,
            kind,
            *,
            workspace_id=None,
        ):
            return CredentialMetadata(
                id=cred_id,
                name=name,
                provider=provider,
                kind=kind,
                scope=scope or CredentialScope(),
                encryption=EncryptionEnvelope(
                    algorithm="aes-256-gcm",
                    key_id="test-key",
                    ciphertext="encrypted",
                ),
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = CredentialCreateRequest(
        name="Test Cred",
        provider="slack",
        scopes=["chat:write"],
        secret="test-secret",
        actor="user",
        access="shared",
        kind=CredentialKind.SECRET,
    )

    result = await create_credential(request, _Repository(), Vault(), _MOCK_WORKSPACE)

    assert result.id == str(cred_id)
    assert result.name == "Test Cred"


@pytest.mark.asyncio()
async def test_create_credential_validation_error() -> None:
    """Create credential handles validation errors."""
    from orcheo_backend.app import create_credential
    from orcheo_backend.app.schemas.credentials import CredentialCreateRequest

    class Vault:
        def list_all_credentials(self, *, workspace_id=None):
            return []

        def create_credential(
            self,
            name,
            provider,
            scopes,
            secret,
            actor,
            scope,
            kind,
            *,
            workspace_id=None,
        ):
            raise ValueError("Invalid credential")

    request = CredentialCreateRequest(
        name="Test Cred",
        provider="slack",
        scopes=[],
        secret="test-secret",
        actor="user",
        access="shared",
        kind=CredentialKind.SECRET,
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_credential(request, _Repository(), Vault(), _MOCK_WORKSPACE)

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio()
async def test_create_credential_quota_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create credential propagates workspace quota failures as HTTP errors."""

    from orcheo_backend.app.routers.credentials import create_credential
    from orcheo_backend.app.schemas.credentials import CredentialCreateRequest

    class Vault:
        def list_all_credentials(self, *, workspace_id=None):
            return []

    async def _raise_quota(*_: object, **__: object) -> None:
        raise WorkspaceQuotaExceededError(
            "Workspace quota reached",
            code="workspace.quota.credentials",
        )

    request = CredentialCreateRequest(
        name="Test Cred",
        provider="slack",
        scopes=[],
        secret="test-secret",
        actor="user",
        access="shared",
        kind=CredentialKind.SECRET,
    )

    from orcheo_backend.app.routers import credentials as credentials_router

    monkeypatch.setattr(
        credentials_router,
        "ensure_workspace_credential_quota",
        _raise_quota,
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_credential(request, _Repository(), Vault(), _MOCK_WORKSPACE)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["error"]["code"] == "workspace.quota.credentials"


@pytest.mark.asyncio()
async def test_create_credential_returns_inferred_access() -> None:
    """Create credential response reflects the persisted scope."""
    from orcheo.models import EncryptionEnvelope
    from orcheo_backend.app import create_credential
    from orcheo_backend.app.schemas.credentials import CredentialCreateRequest

    cred_id = uuid4()
    workflow_id = uuid4()

    class Vault:
        def list_all_credentials(self, *, workspace_id=None):
            return []

        def create_credential(
            self,
            name,
            provider,
            scopes,
            secret,
            actor,
            scope,
            kind,
            *,
            workspace_id=None,
        ):
            return CredentialMetadata(
                id=cred_id,
                name=name,
                provider=provider,
                kind=kind,
                scope=CredentialScope(workflow_ids=[workflow_id]),
                encryption=EncryptionEnvelope(
                    algorithm="aes-256-gcm",
                    key_id="test-key",
                    ciphertext="encrypted",
                ),
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = CredentialCreateRequest(
        name="Test Cred",
        provider="slack",
        scopes=["chat:write"],
        secret="test-secret",
        actor="user",
        access="scoped",
        workflow_id=str(workflow_id),
        kind=CredentialKind.SECRET,
    )

    result = await create_credential(request, _Repository(), Vault(), _MOCK_WORKSPACE)

    assert result.access == "scoped"


@pytest.mark.asyncio()
async def test_delete_credential_success() -> None:
    """Delete credential endpoint deletes credential."""
    from orcheo_backend.app import delete_credential

    cred_id = uuid4()
    deleted_id = None

    class Vault:
        def delete_credential(self, credential_id, context=None):
            nonlocal deleted_id
            deleted_id = credential_id

    response = await delete_credential(cred_id, Vault(), _Repository())

    assert response.status_code == 204
    assert deleted_id == cred_id


@pytest.mark.asyncio()
async def test_delete_credential_not_found() -> None:
    """Delete credential raises 404 for missing credential."""
    from orcheo.vault import CredentialNotFoundError
    from orcheo_backend.app import delete_credential

    cred_id = uuid4()

    class Vault:
        def delete_credential(self, credential_id, context=None):
            raise CredentialNotFoundError("not found")

    with pytest.raises(HTTPException) as exc_info:
        await delete_credential(cred_id, Vault(), _Repository())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_delete_credential_scope_error() -> None:
    """Delete credential raises 403 for scope violations."""
    from orcheo_backend.app import delete_credential

    cred_id = uuid4()

    class Vault:
        def delete_credential(self, credential_id, context=None):
            raise WorkflowScopeError("Access denied")

    with pytest.raises(HTTPException) as exc_info:
        await delete_credential(cred_id, Vault(), _Repository())

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio()
async def test_reveal_credential_secret_not_found() -> None:
    from orcheo_backend.app import reveal_credential_secret

    cred_id = uuid4()

    class Vault:
        def reveal_secret(self, credential_id, context=None):
            raise CredentialNotFoundError("not found")

    with pytest.raises(HTTPException) as exc_info:
        await reveal_credential_secret(cred_id, Vault(), _Repository(), _MOCK_WORKSPACE)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_reveal_credential_secret_scope_error() -> None:
    from orcheo_backend.app import reveal_credential_secret

    cred_id = uuid4()

    class Vault:
        def reveal_secret(self, credential_id, context=None):
            raise WorkflowScopeError("denied")

    with pytest.raises(HTTPException) as exc_info:
        await reveal_credential_secret(cred_id, Vault(), _Repository(), _MOCK_WORKSPACE)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio()
async def test_update_credential_duplicate_name_error() -> None:
    from orcheo_backend.app import update_credential
    from orcheo_backend.app.schemas.credentials import CredentialUpdateRequest

    cred_id = uuid4()

    class Vault:
        def update_credential(self, **kwargs):
            raise DuplicateCredentialNameError("name already in use")

    request = CredentialUpdateRequest(actor="tester", name="duplicate")
    with pytest.raises(HTTPException) as exc_info:
        await update_credential(cred_id, request, _Repository(), Vault())

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio()
async def test_update_credential_validation_error() -> None:
    from orcheo_backend.app import update_credential
    from orcheo_backend.app.schemas.credentials import CredentialUpdateRequest

    cred_id = uuid4()

    class Vault:
        def update_credential(self, **kwargs):
            raise ValueError("invalid")

    request = CredentialUpdateRequest(actor="tester", name="bad")
    with pytest.raises(HTTPException) as exc_info:
        await update_credential(cred_id, request, _Repository(), Vault())

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio()
async def test_update_credential_scoped_access_requires_workflow_id() -> None:
    from orcheo_backend.app import update_credential
    from orcheo_backend.app.schemas.credentials import CredentialUpdateRequest

    cred_id = uuid4()
    request = CredentialUpdateRequest(actor="tester", access="scoped")

    class Vault:
        def update_credential(self, **kwargs):
            raise AssertionError("update_credential should not be called")

    with pytest.raises(HTTPException) as exc_info:
        await update_credential(cred_id, request, _Repository(), Vault())

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == (
        "workflow_id is required when access is set to scoped"
    )


@pytest.mark.asyncio()
async def test_update_credential_returns_inferred_access() -> None:
    from orcheo.models import EncryptionEnvelope
    from orcheo_backend.app import update_credential
    from orcheo_backend.app.schemas.credentials import CredentialUpdateRequest

    cred_id = uuid4()
    workflow_id = uuid4()

    class Vault:
        def update_credential(self, **kwargs):
            return CredentialMetadata(
                id=cred_id,
                name="Updated",
                provider="api",
                kind=CredentialKind.SECRET,
                scope=CredentialScope(workflow_ids=[workflow_id]),
                encryption=EncryptionEnvelope(
                    algorithm="aes-256-gcm",
                    key_id="test-key",
                    ciphertext="encrypted",
                ),
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = CredentialUpdateRequest(actor="tester", access="scoped")
    result = await update_credential(
        cred_id,
        request,
        _Repository(),
        Vault(),
        workflow_id=str(workflow_id),
    )
    assert result.access == "scoped"


@pytest.mark.asyncio()
async def test_create_credential_scoped_access_requires_workflow_id() -> None:
    """Create credential validates that scoped access requires workflow_id."""
    from orcheo_backend.app import create_credential
    from orcheo_backend.app.schemas.credentials import CredentialCreateRequest

    class Vault:
        def create_credential(self, **kwargs):
            raise AssertionError("create_credential should not be called")

    request = CredentialCreateRequest(
        name="Test Cred",
        provider="slack",
        scopes=["chat:write"],
        secret="test-secret",
        actor="user",
        access="scoped",
        kind=CredentialKind.SECRET,
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_credential(request, _Repository(), Vault(), _MOCK_WORKSPACE)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == (
        "workflow_id is required when access is set to scoped"
    )
