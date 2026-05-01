"""Tests for `_infer_credential_access` helper."""

from __future__ import annotations
from uuid import uuid4
from orcheo.models import CredentialScope
from orcheo_backend.app import _infer_credential_access


def test_infer_credential_access_shared_unrestricted() -> None:
    """Credential access inference returns 'shared' for unrestricted scopes."""

    scope = CredentialScope()
    label = _infer_credential_access(scope)

    assert label == "shared"


def test_infer_credential_access_scoped_single_workflow() -> None:
    """Credential access inference returns 'scoped' for a workflow restriction."""

    scope = CredentialScope(workflow_ids=[uuid4()])
    label = _infer_credential_access(scope)

    assert label == "scoped"


def test_infer_credential_access_scoped_single_workspace() -> None:
    """Credential access inference returns 'scoped' for a workspace restriction."""

    scope = CredentialScope(workspace_ids=[uuid4()])
    label = _infer_credential_access(scope)

    assert label == "scoped"


def test_infer_credential_access_scoped_single_role() -> None:
    """Credential access inference returns 'scoped' for a role restriction."""

    scope = CredentialScope(roles=["admin"])
    label = _infer_credential_access(scope)

    assert label == "scoped"


def test_infer_credential_access_scoped_multiple_workflows() -> None:
    """Credential access inference returns 'scoped' for multi-workflow restrictions."""

    scope = CredentialScope(workflow_ids=[uuid4(), uuid4()])
    label = _infer_credential_access(scope)

    assert label == "scoped"


def test_infer_credential_access_scoped_mixed_restrictions() -> None:
    """Credential access inference returns 'scoped' when mixing identifiers."""

    scope = CredentialScope(workflow_ids=[uuid4()], workspace_ids=[uuid4()])
    label = _infer_credential_access(scope)

    assert label == "scoped"
