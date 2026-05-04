"""Tests for tenant-slug-prefixed webhook routing."""

from __future__ import annotations
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from orcheo_backend.app.routers.triggers import _invoke_tenant_webhook


class _MockRequest:
    """Minimal stand-in for a FastAPI Request in webhook tests."""

    method = "POST"
    headers = {}
    query_params = {}

    async def body(self) -> bytes:
        return b""

    client = None


class _MockRepository:
    """Stub repository that resolves trigger_id only when tenant_id matches."""

    def __init__(self, *, tenant_id: str, workflow_ref: str) -> None:
        self._tenant_id = tenant_id
        self._workflow_ref = workflow_ref

    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        tenant_id: str | None = None,
    ) -> object:
        from orcheo_backend.app.repository import WorkflowNotFoundError

        if tenant_id != self._tenant_id or workflow_ref != self._workflow_ref:
            raise WorkflowNotFoundError(workflow_ref)
        return uuid4()


class _MockTenantService:
    """Stub tenant service that knows one tenant by slug."""

    def __init__(self, *, slug: str, tenant_id: str) -> None:
        self._slug = slug
        self._tenant_id = tenant_id
        self.repository = self

    def get_tenant_by_slug(self, slug: str) -> object:
        from orcheo.tenancy import TenantNotFoundError

        if slug != self._slug:
            raise TenantNotFoundError(slug)
        return SimpleNamespace(id=self._tenant_id)


class _MockVault:
    pass


@pytest.mark.asyncio
async def test_unknown_tenant_slug_returns_404() -> None:
    tenant_service = _MockTenantService(slug="acme", tenant_id=str(uuid4()))
    with pytest.raises(HTTPException) as exc_info:
        await _invoke_tenant_webhook(
            "unknown-slug",
            "some-trigger",
            _MockRequest(),  # type: ignore[arg-type]
            _MockRepository(tenant_id="irrelevant", workflow_ref="x"),  # type: ignore[arg-type]
            _MockVault(),  # type: ignore[arg-type]
            tenant_service,  # type: ignore[arg-type]
        )
    assert exc_info.value.status_code == 404
    assert "unknown-slug" in exc_info.value.detail


@pytest.mark.asyncio
async def test_unknown_trigger_id_returns_404() -> None:
    tenant_id = str(uuid4())
    tenant_service = _MockTenantService(slug="acme", tenant_id=tenant_id)
    repository = _MockRepository(tenant_id=tenant_id, workflow_ref="real-workflow")

    with pytest.raises(HTTPException) as exc_info:
        await _invoke_tenant_webhook(
            "acme",
            "wrong-trigger",
            _MockRequest(),  # type: ignore[arg-type]
            repository,  # type: ignore[arg-type]
            _MockVault(),  # type: ignore[arg-type]
            tenant_service,  # type: ignore[arg-type]
        )
    assert exc_info.value.status_code == 404
    assert "wrong-trigger" in exc_info.value.detail


@pytest.mark.asyncio
async def test_wrong_tenant_cannot_access_other_tenants_trigger() -> None:
    tenant_a_id = str(uuid4())
    tenant_b_id = str(uuid4())
    tenant_service_b = _MockTenantService(slug="tenant-b", tenant_id=tenant_b_id)
    repository = _MockRepository(tenant_id=tenant_a_id, workflow_ref="tenant-a-wf")

    with pytest.raises(HTTPException) as exc_info:
        await _invoke_tenant_webhook(
            "tenant-b",
            "tenant-a-wf",
            _MockRequest(),  # type: ignore[arg-type]
            repository,  # type: ignore[arg-type]
            _MockVault(),  # type: ignore[arg-type]
            tenant_service_b,  # type: ignore[arg-type]
        )
    assert exc_info.value.status_code == 404
