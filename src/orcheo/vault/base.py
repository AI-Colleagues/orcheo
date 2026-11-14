"""Shared base class for credential vault implementations."""

from __future__ import annotations
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock, RLock
from typing import Any
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
        self._credential_locks: dict[str, list[Any]] = {}
        self._credential_lock_registry = Lock()

    @contextmanager
    def credential_lock(self, key: str) -> Iterator[None]:
        """Serialize a short read and write of credentials sharing ``key``.

        Holders of different keys do not wait for each other, and the same
        thread may re-enter a key it already holds. These locks only exist
        inside this process, which is exactly the reach of an in-process vault:
        a backend whose data is shared with other processes must override this
        with a lock its store enforces, as the PostgreSQL vault does. Do not
        hold two keys at once in conflicting orders.
        """
        with self._credential_lock_registry:
            entry = self._credential_locks.setdefault(key, [RLock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._credential_lock_registry:
                entry[1] -= 1
                if not entry[1]:
                    del self._credential_locks[key]

    @property
    def cipher(self) -> CredentialCipher:
        """Expose the credential cipher for services that need direct access."""
        return self._cipher


__all__ = ["BaseCredentialVault"]
