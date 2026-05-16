"""Credential vault implementations with AES-256 encryption support."""

from orcheo.vault.base import BaseCredentialVault
from orcheo.vault.errors import (
    CredentialNotFoundError,
    CredentialTemplateNotFoundError,
    DuplicateCredentialNameError,
    GovernanceAlertNotFoundError,
    RotationPolicyError,
    VaultError,
    WorkflowScopeError,
)
from orcheo.vault.in_memory import InMemoryCredentialVault
from orcheo.vault.postgres import PostgresCredentialVault


__all__ = [
    "VaultError",
    "CredentialNotFoundError",
    "CredentialTemplateNotFoundError",
    "GovernanceAlertNotFoundError",
    "DuplicateCredentialNameError",
    "WorkflowScopeError",
    "RotationPolicyError",
    "BaseCredentialVault",
    "InMemoryCredentialVault",
    "PostgresCredentialVault",
]
