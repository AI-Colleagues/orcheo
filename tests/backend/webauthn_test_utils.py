"""A software passkey authenticator for exercising real WebAuthn verification.

It holds a P-256 key and builds the same JSON a browser returns from
``navigator.credentials.create()`` / ``.get()`` (as serialized by
``@simplewebauthn/browser``), with ``none`` attestation. Keyword overrides let
tests forge specific defects: a wrong origin or RP ID, missing user
verification, a stale signature counter, another user's handle, and so on.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

FLAG_UP = 0x01
FLAG_UV = 0x04
FLAG_BE = 0x08
FLAG_BS = 0x10
FLAG_AT = 0x40

_UNSET: Any = object()


class SoftAuthenticator:
    """A discoverable-credential authenticator backed by an in-memory key."""

    def __init__(
        self,
        *,
        rp_id: str = "localhost",
        origin: str = "http://localhost:2026",
        aaguid: str = "00000000-0000-0000-0000-000000000000",
        backup_eligible: bool = True,
        backed_up: bool = True,
        sign_count: int = 0,
        transports: list[str] | None = None,
    ) -> None:
        self.rp_id = rp_id
        self.origin = origin
        self.aaguid = bytes.fromhex(aaguid.replace("-", ""))
        self.backup_eligible = backup_eligible
        self.backed_up = backed_up
        self.sign_count = sign_count
        self.transports = ["internal", "hybrid"] if transports is None else transports
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.user_handle: bytes | None = None

    @property
    def credential_id_b64(self) -> str:
        return bytes_to_base64url(self.credential_id)

    def _flags(self, *, user_verified: bool, attested: bool) -> int:
        flags = FLAG_UP
        if user_verified:
            flags |= FLAG_UV
        if self.backup_eligible:
            flags |= FLAG_BE
        if self.backed_up:
            flags |= FLAG_BS
        if attested:
            flags |= FLAG_AT
        return flags

    def _cose_public_key(self) -> bytes:
        numbers = self.private_key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    @staticmethod
    def _client_data(kind: str, challenge: str, origin: str) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": challenge,
                "origin": origin,
                "crossOrigin": False,
            }
        ).encode()

    def register(
        self,
        options: dict[str, Any],
        *,
        origin: str | None = None,
        rp_id: str | None = None,
        user_verified: bool = True,
        challenge: str | None = None,
    ) -> dict[str, Any]:
        """Answer registration options like ``navigator.credentials.create()``."""
        self.user_handle = base64url_to_bytes(options["user"]["id"])
        rp_hash = hashlib.sha256((rp_id or options["rp"]["id"]).encode()).digest()
        attested = (
            self.aaguid
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + self._cose_public_key()
        )
        auth_data = (
            rp_hash
            + bytes([self._flags(user_verified=user_verified, attested=True)])
            + struct.pack(">I", self.sign_count)
            + attested
        )
        client_data = self._client_data(
            "webauthn.create",
            challenge or options["challenge"],
            origin or self.origin,
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": self.credential_id_b64,
            "rawId": self.credential_id_b64,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "attestationObject": bytes_to_base64url(attestation),
                "transports": list(self.transports),
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }

    def sign_in(
        self,
        options: dict[str, Any],
        *,
        origin: str | None = None,
        rp_id: str | None = None,
        user_verified: bool = True,
        sign_count: int | None = None,
        user_handle: Any = _UNSET,
    ) -> dict[str, Any]:
        """Answer sign-in options like ``navigator.credentials.get()``."""
        if sign_count is None:
            self.sign_count += 1 if self.sign_count else 0
            sign_count = self.sign_count
        rp_hash = hashlib.sha256((rp_id or options["rpId"]).encode()).digest()
        auth_data = (
            rp_hash
            + bytes([self._flags(user_verified=user_verified, attested=False)])
            + struct.pack(">I", sign_count)
        )
        client_data = self._client_data(
            "webauthn.get", options["challenge"], origin or self.origin
        )
        signature = self.private_key.sign(
            auth_data + hashlib.sha256(client_data).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        handle = self.user_handle if user_handle is _UNSET else user_handle
        return {
            "id": self.credential_id_b64,
            "rawId": self.credential_id_b64,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
                "userHandle": (
                    bytes_to_base64url(handle) if isinstance(handle, bytes) else handle
                ),
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }
