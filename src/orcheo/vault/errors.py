"""Error types used by credential vault implementations."""


class VaultError(RuntimeError):
    """Base error type for vault operations."""


class CredentialNotFoundError(VaultError):
    """Raised when a credential cannot be found for the workflow."""


class CredentialTemplateNotFoundError(VaultError):
    """Raised when a credential template cannot be located."""


class GovernanceAlertNotFoundError(VaultError):
    """Raised when a governance alert cannot be located."""


class DuplicateCredentialNameError(VaultError):
    """Raised when attempting to create a credential with a duplicate name."""


class WorkflowScopeError(VaultError):
    """Raised when a credential scope denies access for the provided context."""


class CredentialWorkspaceMismatchError(CredentialNotFoundError, WorkflowScopeError):
    """Raised when a credential belongs to a different workspace.

    It is a scope violation, but callers should report it as not found so the
    credential's existence is not revealed across workspaces.
    """


class RotationPolicyError(VaultError):
    """Raised when a rotation violates configured policies."""


__all__ = [
    "VaultError",
    "CredentialNotFoundError",
    "CredentialWorkspaceMismatchError",
    "CredentialTemplateNotFoundError",
    "GovernanceAlertNotFoundError",
    "DuplicateCredentialNameError",
    "WorkflowScopeError",
    "RotationPolicyError",
]
