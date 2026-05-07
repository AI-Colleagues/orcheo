"""Template-oriented vault helpers and operations."""

from __future__ import annotations
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID
from orcheo.models import (
    CredentialAccessContext,
    CredentialIssuancePolicy,
    CredentialKind,
    CredentialScope,
    CredentialTemplate,
)
from orcheo.vault.errors import WorkflowScopeError
from orcheo.vault.template_utils import (
    normalize_template_kind,
    update_template_kind,
    update_template_policy,
    update_template_scope,
    update_template_scopes,
    update_template_simple_field,
)


if TYPE_CHECKING:
    from orcheo.models import SecretGovernanceAlert  # pragma: no cover

    class _AlertStore(Protocol):
        def _iter_alerts(
            self, *, workspace_id: str | None = None
        ) -> Iterable[SecretGovernanceAlert]: ...

        def _remove_alert(self, alert_id: UUID) -> None: ...


class TemplateOperationsMixin:
    """Mixin implementing credential template operations."""

    def create_template(
        self,
        *,
        name: str,
        provider: str,
        scopes: Sequence[str],
        actor: str,
        description: str | None = None,
        scope: CredentialScope | None = None,
        kind: CredentialKind | str = CredentialKind.SECRET,
        issuance_policy: CredentialIssuancePolicy | None = None,
        workspace_id: str | None = None,
    ) -> CredentialTemplate:
        """Persist and return a new credential template."""
        normalized_kind = normalize_template_kind(kind) or CredentialKind.SECRET
        template = CredentialTemplate.create(
            name=name,
            provider=provider,
            scopes=scopes,
            actor=actor,
            description=description,
            scope=scope,
            kind=normalized_kind,
            issuance_policy=issuance_policy,
            workspace_id=workspace_id,
        )
        self._persist_template(template)
        return template.model_copy(deep=True)

    def update_template(
        self,
        template_id: UUID,
        *,
        actor: str,
        name: str | None = None,
        description: str | None = None,
        scopes: Sequence[str] | None = None,
        scope: CredentialScope | None = None,
        kind: CredentialKind | str | None = None,
        issuance_policy: CredentialIssuancePolicy | None = None,
        context: CredentialAccessContext | None = None,
    ) -> CredentialTemplate:
        """Update template properties and persist the result."""
        template = self._get_template(template_id=template_id, context=context)
        changes: dict[str, object] = {}

        update_template_simple_field(template, "name", name, changes)
        update_template_simple_field(template, "description", description, changes)
        update_template_scopes(template, scopes, changes)
        update_template_scope(template, scope, changes)
        update_template_kind(template, kind, changes)
        update_template_policy(template, issuance_policy, changes)

        if changes:
            template.record_event(
                actor=actor,
                action="template_updated",
                metadata=changes,
            )
            self._persist_template(template)

        return template.model_copy(deep=True)

    def delete_template(
        self,
        template_id: UUID,
        *,
        context: CredentialAccessContext | None = None,
    ) -> None:
        """Remove a credential template from the vault."""
        self._get_template(template_id=template_id, context=context)
        self._remove_template(template_id)
        alert_store = cast("_AlertStore", self)
        for alert in list(alert_store._iter_alerts()):
            if alert.template_id == template_id:
                alert_store._remove_alert(alert.id)

    def get_template(
        self,
        *,
        template_id: UUID,
        context: CredentialAccessContext | None = None,
    ) -> CredentialTemplate:
        """Return a credential template ensuring scope restrictions."""
        template = self._load_template(template_id)
        access_context = context or CredentialAccessContext()
        if not self._template_matches_workspace(template.workspace_id, access_context):
            msg = "Credential template cannot be accessed with the provided context."
            raise WorkflowScopeError(msg)
        if not template.scope.allows(access_context):
            msg = "Credential template cannot be accessed with the provided context."
            raise WorkflowScopeError(msg)
        return template.model_copy(deep=True)

    def list_templates(
        self,
        *,
        context: CredentialAccessContext | None = None,
        workspace_id: str | None = None,
    ) -> list[CredentialTemplate]:
        """Return credential templates available to the context."""
        access_context = context or CredentialAccessContext()
        workspace_filter = (
            workspace_id if workspace_id is not None else access_context.workspace_id
        )
        return [
            template.model_copy(deep=True)
            for template in self._iter_templates(workspace_id=workspace_filter)
            if self._template_matches_workspace(template.workspace_id, access_context)
            and template.scope.allows(access_context)
        ]

    def record_template_issuance(
        self,
        *,
        template_id: UUID,
        actor: str,
        credential_id: UUID,
        context: CredentialAccessContext | None = None,
    ) -> CredentialTemplate:
        """Append audit metadata on the template for an issuance event."""
        template = self._get_template(template_id=template_id, context=context)
        template.record_issuance(actor=actor, credential_id=credential_id)
        self._persist_template(template)
        return template.model_copy(deep=True)

    def _get_template(
        self,
        *,
        template_id: UUID,
        context: CredentialAccessContext | None = None,
    ) -> CredentialTemplate:
        template = self._load_template(template_id)
        access_context = context or CredentialAccessContext()
        if not self._template_matches_workspace(template.workspace_id, access_context):
            msg = "Credential template cannot be accessed with the provided context."
            raise WorkflowScopeError(msg)
        if not template.scope.allows(access_context):
            msg = "Credential template cannot be accessed with the provided context."
            raise WorkflowScopeError(msg)
        return template

    def _persist_template(self, template: CredentialTemplate) -> None:
        """Persist a credential template to storage."""
        raise NotImplementedError  # pragma: no cover

    def _load_template(self, template_id: UUID) -> CredentialTemplate:
        """Load a credential template from storage."""
        raise NotImplementedError  # pragma: no cover

    def _iter_templates(
        self, *, workspace_id: str | None = None
    ) -> Iterable[CredentialTemplate]:
        """Iterate over stored credential templates."""
        raise NotImplementedError  # pragma: no cover

    def _remove_template(self, template_id: UUID) -> None:
        """Remove a credential template from storage."""
        raise NotImplementedError  # pragma: no cover

    @staticmethod
    def _template_matches_workspace(
        template_workspace_id: str | None, context: CredentialAccessContext
    ) -> bool:
        """Return whether a template belongs to the active workspace."""
        if context.workspace_id is None:
            return True
        return template_workspace_id is None or template_workspace_id == str(
            context.workspace_id
        )


__all__ = ["TemplateOperationsMixin"]
