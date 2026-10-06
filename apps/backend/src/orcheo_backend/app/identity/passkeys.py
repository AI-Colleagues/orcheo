"""Passkey (WebAuthn) registration and sign-in for the first-party IdP.

Passkeys are a second way to sign in to an existing account. A signed-in user
adds them (after a recent sign-in), and signing in with one ends in the same
session and tokens as an emailed code. Accounts are still created only by
email verification, which also remains the recovery path.

Sign-in is usernameless: it uses discoverable credentials and never asks for
an email first, so the options endpoint reveals nothing about which accounts
exist. Every ceremony challenge is stored, bound to its ceremony (and, when
adding a passkey, to the user), and consumed before the browser's response is
verified, so each response can be used once on any backend replica.
"""

from __future__ import annotations
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal
from uuid import UUID
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import (
    base64url_to_bytes,
    bytes_to_base64url,
    options_to_json_dict,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    CredentialDeviceType,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from orcheo.identity.errors import IdentityEmailDomainNotAllowedError
from orcheo.identity.models import (
    Passkey,
    PasskeyCeremony,
    PasskeyChallenge,
    User,
    UserStatus,
)
from orcheo.identity.repository import IdentityRepository
from orcheo.workspace.email import PasskeyNoticeEmail, PasskeyNoticeEmailSender
from orcheo_backend.app.authentication.telemetry import (
    AuthEvent,
    AuthTelemetry,
    auth_telemetry,
)
from orcheo_backend.app.identity.config import IdentityConfig
from orcheo_backend.app.identity.service import IdentityService, VerificationResult


__all__ = [
    "MAX_PASSKEYS_PER_USER",
    "PasskeyCeremonyOptions",
    "PasskeyLimitReachedError",
    "PasskeyReauthenticationRequiredError",
    "PasskeyRejectedError",
    "PasskeyService",
    "PasskeyServiceError",
    "PasskeysUnavailableError",
    "default_passkey_name",
    "passkey_user_handle",
]

logger = logging.getLogger(__name__)

MAX_PASSKEYS_PER_USER = 20
MAX_PASSKEY_NAME_LENGTH = 64
# Every mainstream authenticator supports at least one of these.
SUPPORTED_ALGORITHMS = [
    COSEAlgorithmIdentifier.ECDSA_SHA_256,
    COSEAlgorithmIdentifier.EDDSA,
    COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
]
_TRANSPORTS = {transport.value: transport for transport in AuthenticatorTransport}

_SIGN_IN_FAILED = "That passkey could not be verified. Please try again."
_REGISTRATION_FAILED = "Your passkey could not be verified. Please try again."
_REAUTHENTICATE = "Confirm it's you by signing in again before changing your passkeys."
_ACCOUNT_DISABLED = "This account can no longer sign in."

# Passkey providers by AAGUID, from the community-maintained registry at
# github.com/passkeydeveloper/passkey-authenticator-aaguids.
_PROVIDER_NAMES = {
    "fbfc3007-154e-4ecc-8c0b-6e020557d7bd": "Apple Passwords",
    "dd4ec289-e01d-41c9-bb89-70fa845d4bf2": "iCloud Keychain (Managed)",
    "ea9b8d66-4d01-1d21-3ce4-b6b48cb575d4": "Google Password Manager",
    "adce0002-35bc-c60a-648b-0b25f1f05503": "Chrome on Mac",
    "08987058-cadc-4b81-b6e1-30de50dcbe96": "Windows Hello",
    "9ddd1817-af5a-4672-a2b9-3e3dd95000a9": "Windows Hello",
    "6028b017-b1d4-4c02-b4b3-afcdafc96bb2": "Windows Hello",
    "d3452668-01fd-4c12-926c-83a4204853aa": "Microsoft Password Manager",
    "53414d53-554e-4700-0000-000000000000": "Samsung Pass",
    "bada5566-a7aa-401f-bd96-45619a55120d": "1Password",
    "d548826e-79b4-db40-a3d8-11116f7e8349": "Bitwarden",
    "531126d6-e717-415c-9320-3d9aa6981239": "Dashlane",
    "f3809540-7f14-49c1-a8b3-8f813b225541": "Enpass",
    "0ea242b4-43c4-4a1b-8b17-dd6d0b6baec6": "Keeper",
    "b78a0a55-6ef8-d246-a042-ba0f6d55050c": "LastPass",
    "b84e4048-15dc-4dd0-8640-f4f60813c8af": "NordPass",
    "50726f74-6f6e-5061-7373-50726f746f6e": "Proton Pass",
}
# User-Agent tokens, most specific first (Edge and Opera also say "Chrome").
_BROWSERS = (
    ("Edg", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox/", "Firefox"),
    ("FxiOS/", "Firefox"),
    ("CriOS/", "Chrome"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
_PLATFORMS = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("CrOS", "ChromeOS"),
    ("Windows", "Windows"),
    ("Macintosh", "macOS"),
    ("Linux", "Linux"),
)


class PasskeyServiceError(Exception):
    """A passkey request the API refuses, with its HTTP status and error code."""

    status_code = 400
    code = "auth.passkey_invalid"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        """Record the user-facing message and, optionally, a specific code."""
        super().__init__(message)
        if code is not None:
            self.code = code


class PasskeysUnavailableError(PasskeyServiceError):
    """Raised when the server has no usable passkey relying party."""

    status_code = 404
    code = "auth.passkeys_unavailable"


class PasskeyReauthenticationRequiredError(PasskeyServiceError):
    """Raised when adding or removing a passkey needs a more recent sign-in."""

    status_code = 403
    code = "auth.reauthentication_required"


class PasskeyRejectedError(PasskeyServiceError):
    """Raised when a passkey response fails verification or cannot be used."""


class PasskeyLimitReachedError(PasskeyServiceError):
    """Raised when a user already has the maximum number of passkeys."""

    status_code = 409
    code = "auth.passkey_limit_reached"


@dataclass(frozen=True)
class PasskeyCeremonyOptions:
    """Browser options for one passkey ceremony and the challenge behind it."""

    challenge_id: UUID
    options: dict[str, Any]


def passkey_user_handle(user_id: UUID) -> str:
    """Return the WebAuthn user handle (base64url) for an internal user id."""
    return bytes_to_base64url(user_id.bytes)


def default_passkey_name(aaguid: str | None, user_agent: str | None) -> str:
    """Name a new passkey after its provider, else the browser that made it."""
    provider = _PROVIDER_NAMES.get(aaguid or "")
    if provider:
        return provider
    agent = user_agent or ""
    browser = next((name for token, name in _BROWSERS if token in agent), None)
    platform = next((name for token, name in _PLATFORMS if token in agent), None)
    if browser and platform:
        return f"{browser} on {platform}"
    return browser or platform or "Passkey"


class PasskeyService:
    """Run passkey ceremonies and manage passkeys for the identity service."""

    def __init__(
        self,
        identity: IdentityService,
        *,
        notice_sender: PasskeyNoticeEmailSender | None = None,
        telemetry: AuthTelemetry | None = None,
    ) -> None:
        """Bind the service to the identity service it signs users in through."""
        self._identity = identity
        self._notice_sender = notice_sender
        self._telemetry = telemetry or auth_telemetry

    @property
    def identity(self) -> IdentityService:
        """Return the identity service passkey sign-in starts sessions with."""
        return self._identity

    @property
    def rp_id(self) -> str | None:
        """Return the relying party ID, or None when passkeys are unavailable."""
        return self._config.webauthn_rp_id

    @property
    def _config(self) -> IdentityConfig:
        return self._identity.config

    @property
    def _repository(self) -> IdentityRepository:
        return self._identity.repository

    # -- sign-in -------------------------------------------------------------

    def begin_authentication(self) -> PasskeyCeremonyOptions:
        """Start a usernameless passkey sign-in."""
        rp_id = self._require_rp_id()
        options = generate_authentication_options(
            rp_id=rp_id,
            timeout=self._config.passkey_challenge_ttl_seconds * 1000,
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        challenge = self._store_challenge(
            PasskeyCeremony.AUTHENTICATION, options.challenge, user_id=None
        )
        return PasskeyCeremonyOptions(
            challenge_id=challenge.id, options=options_to_json_dict(options)
        )

    def finish_authentication(
        self,
        challenge_id: UUID,
        credential: Mapping[str, Any],
        *,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> VerificationResult:
        """Verify a passkey sign-in and start a session for the passkey's owner.

        Raises:
            PasskeyChallengeNotFoundError: If the challenge expired or was used.
            PasskeyRejectedError: If the response cannot sign anyone in.
            IdentityEmailDomainNotAllowedError: If the owner's email domain is
                no longer allowed to sign in.
        """
        rp_id = self._require_rp_id()
        challenge = self._repository.consume_passkey_challenge(
            challenge_id,
            ceremony=PasskeyCeremony.AUTHENTICATION,
            user_id=None,
            now=self._identity.now(),
        )
        credential_id = _decoded_base64url(credential.get("rawId"))
        if credential_id is None:
            self._record("auth.passkey_login", "failure", ip=ip, detail="malformed")
            raise PasskeyRejectedError(_SIGN_IN_FAILED)
        passkey = self._repository.get_passkey_by_credential_id(
            bytes_to_base64url(credential_id)
        )
        if passkey is None:
            self._record("auth.passkey_login", "failure", ip=ip, detail="unknown")
            raise PasskeyRejectedError(
                "This passkey is not registered with Orcheo. Sign in with your "
                "email instead.",
                code="auth.passkey_unknown",
            )
        # Usernameless sign-in: the authenticator must say whose passkey it is.
        if _user_handle(credential) != passkey.user_id.bytes:
            self._record(
                "auth.passkey_login",
                "failure",
                subject=str(passkey.user_id),
                ip=ip,
                detail="user_handle_mismatch",
            )
            raise PasskeyRejectedError(_SIGN_IN_FAILED)
        try:
            verified = verify_authentication_response(
                credential=dict(credential),
                expected_challenge=base64url_to_bytes(challenge.challenge),
                expected_rp_id=rp_id,
                expected_origin=list(self._config.webauthn_origins),
                credential_public_key=base64url_to_bytes(passkey.public_key),
                credential_current_sign_count=passkey.sign_count,
                require_user_verification=True,
            )
        except Exception as exc:  # noqa: BLE001 - any bad response is a rejection
            logger.info("Rejected a passkey sign-in: %s", exc)
            self._record(
                "auth.passkey_login",
                "failure",
                subject=str(passkey.user_id),
                ip=ip,
                detail="verification_failed",
            )
            raise PasskeyRejectedError(_SIGN_IN_FAILED) from exc
        user = self._repository.get_user(passkey.user_id)
        self._ensure_can_sign_in(user, event="auth.passkey_login", ip=ip)
        self._repository.record_passkey_use(
            passkey.id,
            sign_count=verified.new_sign_count,
            backed_up=verified.credential_backed_up,
            used_at=self._identity.now(),
        )
        return self._identity.start_session(
            user, method="passkey", user_agent=user_agent, ip=ip
        )

    # -- adding passkeys -----------------------------------------------------

    def begin_registration(
        self, user_id: UUID, *, auth_time: object
    ) -> PasskeyCeremonyOptions:
        """Start adding a passkey to a signed-in user's account.

        Raises:
            PasskeyReauthenticationRequiredError: If ``auth_time`` (the access
                token's sign-in time) is missing or too old.
            PasskeyLimitReachedError: If the user has no passkey slots left.
        """
        rp_id = self._require_rp_id()
        self._require_recent_sign_in(auth_time)
        user = self._repository.get_user(user_id)
        self._ensure_can_sign_in(user, event="auth.passkey_registration")
        existing = self._repository.list_passkeys(user.id)
        _ensure_below_limit(existing)
        options = generate_registration_options(
            rp_id=rp_id,
            rp_name=self._config.webauthn_rp_name,
            user_name=user.email,
            user_id=user.id.bytes,
            user_display_name=user.name or user.email,
            timeout=self._config.passkey_challenge_ttl_seconds * 1000,
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[_descriptor(passkey) for passkey in existing],
            supported_pub_key_algs=SUPPORTED_ALGORITHMS,
        )
        challenge = self._store_challenge(
            PasskeyCeremony.REGISTRATION, options.challenge, user_id=user.id
        )
        return PasskeyCeremonyOptions(
            challenge_id=challenge.id, options=options_to_json_dict(options)
        )

    def finish_registration(
        self,
        user_id: UUID,
        challenge_id: UUID,
        credential: Mapping[str, Any],
        *,
        name: str | None = None,
        user_agent: str | None = None,
    ) -> Passkey:
        """Verify a new passkey and add it to the user's account.

        Raises:
            PasskeyChallengeNotFoundError: If the challenge expired, was used,
                or was issued to someone else.
            PasskeyRejectedError: If the browser response fails verification.
            PasskeyAlreadyRegisteredError: If the credential is registered.
        """
        rp_id = self._require_rp_id()
        now = self._identity.now()
        challenge = self._repository.consume_passkey_challenge(
            challenge_id,
            ceremony=PasskeyCeremony.REGISTRATION,
            user_id=user_id,
            now=now,
        )
        user = self._repository.get_user(user_id)
        self._ensure_can_sign_in(user, event="auth.passkey_registration")
        _ensure_below_limit(self._repository.list_passkeys(user.id))
        try:
            verified = verify_registration_response(
                credential=dict(credential),
                expected_challenge=base64url_to_bytes(challenge.challenge),
                expected_rp_id=rp_id,
                expected_origin=list(self._config.webauthn_origins),
                require_user_verification=True,
                supported_pub_key_algs=SUPPORTED_ALGORITHMS,
            )
        except Exception as exc:  # noqa: BLE001 - any bad response is a rejection
            logger.info("Rejected a passkey registration: %s", exc)
            self._record(
                "auth.passkey_registration",
                "failure",
                subject=str(user.id),
                detail="verification_failed",
            )
            raise PasskeyRejectedError(_REGISTRATION_FAILED) from exc
        passkey = self._repository.add_passkey(
            Passkey(
                user_id=user.id,
                credential_id=bytes_to_base64url(verified.credential_id),
                public_key=bytes_to_base64url(verified.credential_public_key),
                sign_count=verified.sign_count,
                transports=[t.value for t in _known_transports(credential)],
                aaguid=verified.aaguid,
                backup_eligible=(
                    verified.credential_device_type == CredentialDeviceType.MULTI_DEVICE
                ),
                backed_up=verified.credential_backed_up,
                name=_clean_name(name)
                or default_passkey_name(verified.aaguid, user_agent),
                created_at=now,
            )
        )
        self._record("auth.passkey_registration", "success", subject=str(user.id))
        self._notify(user, passkey, action="added")
        return passkey

    # -- managing passkeys ---------------------------------------------------

    def list_passkeys(self, user_id: UUID) -> list[Passkey]:
        """Return a user's passkeys, oldest first."""
        return self._repository.list_passkeys(user_id)

    def rename_passkey(self, user_id: UUID, passkey_id: UUID, name: str) -> Passkey:
        """Rename one of a user's passkeys."""
        cleaned = _clean_name(name)
        if not cleaned:
            raise PasskeyRejectedError(
                "Passkey names cannot be blank.", code="auth.passkey_name_invalid"
            )
        return self._repository.rename_passkey(user_id, passkey_id, cleaned)

    def delete_passkey(
        self, user_id: UUID, passkey_id: UUID, *, auth_time: object
    ) -> Passkey:
        """Remove a passkey after a recent sign-in and tell the account owner."""
        self._require_recent_sign_in(auth_time)
        removed = self._repository.delete_passkey(user_id, passkey_id)
        self._record("auth.passkey_removed", "success", subject=str(user_id))
        self._notify(self._repository.get_user(user_id), removed, action="removed")
        return removed

    # -- helpers -------------------------------------------------------------

    def _require_rp_id(self) -> str:
        rp_id = self._config.webauthn_rp_id
        if rp_id is None:
            raise PasskeysUnavailableError("Passkeys are not available on this server.")
        return rp_id

    def _require_recent_sign_in(self, auth_time: object) -> None:
        recent = (
            isinstance(auth_time, int | float)
            and not isinstance(auth_time, bool)
            and 0
            <= self._identity.now().timestamp() - auth_time
            <= self._config.passkey_reauth_max_age_seconds
        )
        if not recent:
            raise PasskeyReauthenticationRequiredError(_REAUTHENTICATE)

    def _ensure_can_sign_in(
        self, user: User, *, event: str, ip: str | None = None
    ) -> None:
        # Re-checked at sign-in so a disabled account or a tightened domain
        # allowlist also applies to passkeys added before the change.
        if user.status != UserStatus.ACTIVE:
            self._record(
                event,
                "failure",
                subject=str(user.id),
                ip=ip,
                detail="account_disabled",
            )
            raise PasskeyRejectedError(_ACCOUNT_DISABLED, code="auth.account_disabled")
        if not self._identity.is_email_allowed(user.email):
            self._record(
                "auth.email_domain_rejected", "failure", subject=str(user.id), ip=ip
            )
            raise IdentityEmailDomainNotAllowedError(
                "Sign-in is limited to approved email domains."
            )

    def _store_challenge(
        self, ceremony: PasskeyCeremony, challenge: bytes, *, user_id: UUID | None
    ) -> PasskeyChallenge:
        now = self._identity.now()
        return self._repository.add_passkey_challenge(
            PasskeyChallenge(
                ceremony=ceremony,
                challenge=bytes_to_base64url(challenge),
                user_id=user_id,
                created_at=now,
                expires_at=now
                + timedelta(seconds=self._config.passkey_challenge_ttl_seconds),
            )
        )

    def _notify(
        self, user: User, passkey: Passkey, *, action: Literal["added", "removed"]
    ) -> None:
        if self._notice_sender is None:
            return
        try:
            self._notice_sender.send_passkey_notice(
                PasskeyNoticeEmail(
                    to=user.email,
                    passkey_name=passkey.name,
                    action=action,
                    occurred_at=self._identity.now(),
                )
            )
        except Exception:  # noqa: BLE001 - a lost notice must not undo the change
            logger.exception("Failed to send the passkey %s notice", action)

    def _record(
        self,
        event: str,
        status: Literal["success", "failure"],
        *,
        subject: str | None = None,
        ip: str | None = None,
        detail: str | None = None,
    ) -> None:
        self._telemetry.record(
            AuthEvent(
                event=event,
                status=status,
                subject=subject,
                identity_type="user",
                token_id=None,
                ip=ip,
                detail=detail,
            )
        )


def _ensure_below_limit(existing: list[Passkey]) -> None:
    if len(existing) >= MAX_PASSKEYS_PER_USER:
        raise PasskeyLimitReachedError(
            f"You can have up to {MAX_PASSKEYS_PER_USER} passkeys. Remove one to "
            "add another."
        )


def _decoded_base64url(value: object) -> bytes | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return base64url_to_bytes(value)
    except ValueError:  # binascii.Error and non-ASCII input are ValueErrors
        return None


def _user_handle(credential: Mapping[str, Any]) -> bytes | None:
    response = credential.get("response")
    if not isinstance(response, Mapping):
        return None
    return _decoded_base64url(response.get("userHandle"))


def _known_transports(credential: Mapping[str, Any]) -> list[AuthenticatorTransport]:
    response = credential.get("response")
    reported = response.get("transports") if isinstance(response, Mapping) else None
    return _transports_from(reported if isinstance(reported, list) else [])


def _transports_from(values: Iterable[object]) -> list[AuthenticatorTransport]:
    return [_TRANSPORTS[v] for v in values if isinstance(v, str) and v in _TRANSPORTS]


def _descriptor(passkey: Passkey) -> PublicKeyCredentialDescriptor:
    return PublicKeyCredentialDescriptor(
        id=base64url_to_bytes(passkey.credential_id),
        transports=_transports_from(passkey.transports) or None,
    )


def _clean_name(name: str | None) -> str:
    return " ".join((name or "").split())[:MAX_PASSKEY_NAME_LENGTH]
