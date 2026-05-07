"""In-memory credential vault used for tests and local workflows."""

from __future__ import annotations
from collections.abc import Iterable
from uuid import UUID
from orcheo.models import (
    CredentialCipher,
    CredentialMetadata,
    CredentialTemplate,
    SecretGovernanceAlert,
)
from orcheo.vault.base import BaseCredentialVault
from orcheo.vault.errors import (
    CredentialNotFoundError,
    CredentialTemplateNotFoundError,
    DuplicateCredentialNameError,
    GovernanceAlertNotFoundError,
)


class InMemoryCredentialVault(BaseCredentialVault):
    """Ephemeral credential vault backed by in-memory dictionaries."""

    def __init__(self, *, cipher: CredentialCipher | None = None) -> None:
        """Create a new in-memory vault instance."""
        super().__init__(cipher=cipher)
        self._store: dict[UUID, CredentialMetadata] = {}
        self._templates: dict[UUID, CredentialTemplate] = {}
        self._alerts: dict[UUID, SecretGovernanceAlert] = {}

    def _persist_metadata(self, metadata: CredentialMetadata) -> None:
        normalized = metadata.name.casefold()
        for stored_id, stored in self._store.items():
            if stored_id == metadata.id:
                continue
            if stored.name.casefold() != normalized:
                continue
            # Name uniqueness is scoped per workspace (NULL = global scope).
            same_workspace = (
                stored.workspace_id is None and metadata.workspace_id is None
            )
            same_workspace = (
                same_workspace or stored.workspace_id == metadata.workspace_id
            )
            if same_workspace:
                msg = f"Credential name '{metadata.name}' is already in use."
                raise DuplicateCredentialNameError(msg)
        self._store[metadata.id] = metadata.model_copy(deep=True)

    def _load_metadata(self, credential_id: UUID) -> CredentialMetadata:
        try:
            return self._store[credential_id].model_copy(deep=True)
        except KeyError as exc:
            msg = "Credential was not found."
            raise CredentialNotFoundError(msg) from exc

    def _iter_metadata(
        self, *, workspace_id: str | None = None
    ) -> Iterable[CredentialMetadata]:
        for metadata in self._store.values():
            if workspace_id is not None and metadata.workspace_id not in {
                None,
                workspace_id,
            }:
                continue
            yield metadata.model_copy(deep=True)

    def _remove_credential(self, credential_id: UUID) -> None:
        try:
            del self._store[credential_id]
        except KeyError as exc:
            msg = "Credential was not found."
            raise CredentialNotFoundError(msg) from exc

    def _persist_template(self, template: CredentialTemplate) -> None:
        self._templates[template.id] = template.model_copy(deep=True)

    def _load_template(self, template_id: UUID) -> CredentialTemplate:
        try:
            return self._templates[template_id].model_copy(deep=True)
        except KeyError as exc:
            msg = "Credential template was not found."
            raise CredentialTemplateNotFoundError(msg) from exc

    def _iter_templates(
        self, *, workspace_id: str | None = None
    ) -> Iterable[CredentialTemplate]:
        for template in self._templates.values():
            if workspace_id is not None and template.workspace_id not in {
                None,
                workspace_id,
            }:
                continue
            yield template.model_copy(deep=True)

    def _remove_template(self, template_id: UUID) -> None:
        try:
            del self._templates[template_id]
        except KeyError as exc:
            msg = "Credential template was not found."
            raise CredentialTemplateNotFoundError(msg) from exc

    def _persist_alert(self, alert: SecretGovernanceAlert) -> None:
        self._alerts[alert.id] = alert.model_copy(deep=True)

    def _load_alert(self, alert_id: UUID) -> SecretGovernanceAlert:
        try:
            return self._alerts[alert_id].model_copy(deep=True)
        except KeyError as exc:
            msg = "Governance alert was not found."
            raise GovernanceAlertNotFoundError(msg) from exc

    def _iter_alerts(
        self, *, workspace_id: str | None = None
    ) -> Iterable[SecretGovernanceAlert]:
        for alert in self._alerts.values():
            if workspace_id is not None and alert.workspace_id not in {
                None,
                workspace_id,
            }:
                continue
            yield alert.model_copy(deep=True)

    def _remove_alert(self, alert_id: UUID) -> None:
        self._alerts.pop(alert_id, None)


__all__ = ["InMemoryCredentialVault"]
