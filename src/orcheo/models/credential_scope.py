"""Scope and access control helpers for credential usage."""

from __future__ import annotations
from uuid import UUID
from pydantic import Field, field_validator
from orcheo.models.base import OrcheoBaseModel


__all__ = [
    "CredentialAccessContext",
    "CredentialScope",
]


class CredentialAccessContext(OrcheoBaseModel):
    """Describes the caller attempting to access a credential."""

    workflow_id: UUID | None = None
    workspace_id: UUID | None = None
    roles: list[str] = Field(default_factory=list)

    @field_validator("roles", mode="after")
    @classmethod
    def _normalize_roles(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for role in value:
            candidate = role.strip().lower()
            if candidate and candidate not in seen:
                seen.add(candidate)
                normalized.append(candidate)
        return normalized


class CredentialScope(OrcheoBaseModel):
    """Scope configuration declaring which callers may access a credential."""

    workflow_ids: list[UUID] = Field(default_factory=list)
    workspace_ids: list[UUID] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)

    @field_validator("workflow_ids", "workspace_ids", mode="after")
    @classmethod
    def _dedupe_uuid_list(cls, value: list[UUID]) -> list[UUID]:
        seen: set[UUID] = set()
        deduped: list[UUID] = []
        for item in value:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped

    @field_validator("roles", mode="after")
    @classmethod
    def _normalize_roles(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for role in value:
            candidate = role.strip().lower()
            if candidate and candidate not in seen:
                seen.add(candidate)
                normalized.append(candidate)
        return normalized

    @classmethod
    def unrestricted(cls) -> CredentialScope:
        """Return a scope that allows access from any context."""
        return cls()

    @classmethod
    def for_workflows(cls, *workflow_ids: UUID) -> CredentialScope:
        """Create a scope limited to the provided workflow identifiers."""
        return cls(workflow_ids=list(workflow_ids))

    @classmethod
    def for_workspaces(cls, *workspace_ids: UUID) -> CredentialScope:
        """Create a scope limited to the provided workspace identifiers."""
        return cls(workspace_ids=list(workspace_ids))

    @classmethod
    def for_roles(cls, *roles: str) -> CredentialScope:
        """Create a scope limited to callers possessing at least one role."""
        return cls(roles=[role for role in roles])

    def allows(self, context: CredentialAccessContext) -> bool:
        """Return whether the provided access context satisfies the scope."""
        if self.workflow_ids:
            if (
                context.workflow_id is None
                or context.workflow_id not in self.workflow_ids
            ):
                return False
        if self.workspace_ids:
            if (
                context.workspace_id is None
                or context.workspace_id not in self.workspace_ids
            ):
                return False
        if self.roles:
            if not context.roles:
                return False
            context_roles = set(context.roles)
            if not context_roles.intersection(self.roles):
                return False
        return True

    def is_unrestricted(self) -> bool:
        """Return whether the scope allows access from any context."""
        return not (self.workflow_ids or self.workspace_ids or self.roles)

    def scope_hint(self) -> str:
        """Return a stable string hint representing the most specific scope."""
        if self.workflow_ids:
            return str(self.workflow_ids[0])
        if self.workspace_ids:
            return str(self.workspace_ids[0])
        if self.roles:
            return self.roles[0]
        return "GLOBAL"
