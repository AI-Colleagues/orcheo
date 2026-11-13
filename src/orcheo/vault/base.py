"""Shared base class for credential vault implementations."""

from __future__ import annotations
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock
from orcheo.models import AesGcmCredentialCipher, CredentialCipher
from orcheo.vault.alerts import GovernanceAlertOperationsMixin
from orcheo.vault.credentials import CredentialOperationsMixin
from orcheo.vault.templates import TemplateOperationsMixin


class BaseCredentialVault(
    CredentialOperationsMixin,
    TemplateOperationsMixin,
    GovernanceAlertOperationsMixin,
):
    """Base helper that implements common credential vault workflows."""

    def __init__(self, *, cipher: CredentialCipher | None = None) -> None:
        """Initialize the vault with an encryption cipher."""
        self._cipher = cipher or AesGcmCredentialCipher(key=secrets.token_hex(32))
        self._credential_lock = RLock()

    @contextmanager
    def credential_lock(self, key: str) -> Iterator[None]:
        """Serialize a short read and write of related credentials."""
        with self._credential_lock:
            yield

    @property
    def cipher(self) -> CredentialCipher:
        """Expose the credential cipher for services that need direct access."""
        return self._cipher


__all__ = ["BaseCredentialVault"]
