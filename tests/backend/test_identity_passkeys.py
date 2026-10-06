"""Tests for passkey registration and sign-in in the first-party identity service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from orcheo.identity import (
    IdentityEmailDomainNotAllowedError,
    InMemoryIdentityRepository,
    Passkey,
    PasskeyAlreadyRegisteredError,
    PasskeyChallengeNotFoundError,
    PasskeyNotFoundError,
    User,
    UserStatus,
)
from orcheo.workspace.email import AuthChallengeEmail, PasskeyNoticeEmail
from orcheo_backend.app.authentication.telemetry import AuthTelemetry
from orcheo_backend.app.identity import passkeys as passkeys_module
from orcheo_backend.app.identity.config import IdentityConfig
from orcheo_backend.app.identity.passkeys import (
    PasskeyLimitReachedError,
    PasskeyReauthenticationRequiredError,
    PasskeyRejectedError,
    PasskeyService,
    PasskeysUnavailableError,
    default_passkey_name,
    passkey_user_handle,
)
from orcheo_backend.app.identity.service import IdentityService
from tests.backend.webauthn_test_utils import SoftAuthenticator

SECRET = "passkey-test-secret"  # noqa: S105 - test fixture
ISSUER = "https://auth.test"
RP_ID = "localhost"
ORIGIN = "http://localhost:2026"
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
UNKNOWN_CHALLENGE = uuid4()
CHROME_ON_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


class Clock:
    """Controllable service clock."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class NullChallengeSender:
    def send_auth_challenge(self, email: AuthChallengeEmail) -> None:
        return None


class CapturingNotices:
    def __init__(self) -> None:
        self.sent: list[PasskeyNoticeEmail] = []

    def send_passkey_notice(self, email: PasskeyNoticeEmail) -> None:
        self.sent.append(email)


class Harness:
    """A passkey service over an in-memory store, with its collaborators."""

    def __init__(
        self,
        *,
        repo: InMemoryIdentityRepository | None = None,
        clock: Clock | None = None,
        notices: Any = None,
        **config: Any,
    ) -> None:
        self.repo = repo or InMemoryIdentityRepository()
        self.clock = clock or Clock()
        self.notices = CapturingNotices() if notices is None else notices
        self.telemetry = AuthTelemetry()
        config.setdefault("webauthn_rp_id", RP_ID)
        config.setdefault("webauthn_origins", (ORIGIN,))
        self.identity = IdentityService(
            self.repo,
            email_sender=NullChallengeSender(),
            config=IdentityConfig(
                jwt_secret=SECRET, issuer=ISSUER, audience="orcheo", **config
            ),
            clock=self.clock,
            telemetry=self.telemetry,
        )
        self.service = PasskeyService(
            self.identity, notice_sender=self.notices, telemetry=self.telemetry
        )

    def user(self, email: str = "alice@example.com", **fields: Any) -> User:
        return self.repo.create_user(User(email=email, email_verified=True, **fields))

    def register(
        self,
        user: User,
        authenticator: SoftAuthenticator,
        *,
        name: str | None = None,
        user_agent: str | None = None,
    ) -> Passkey:
        begun = self.service.begin_registration(
            user.id, auth_time=self.clock.now.timestamp()
        )
        credential = authenticator.register(begun.options)
        return self.service.finish_registration(
            user.id,
            begun.challenge_id,
            credential,
            name=name,
            user_agent=user_agent,
        )

    def sign_in(self, authenticator: SoftAuthenticator, **overrides: Any):
        begun = self.service.begin_authentication()
        credential = authenticator.sign_in(begun.options, **overrides)
        return self.service.finish_authentication(
            begun.challenge_id, credential, user_agent="pytest", ip="10.0.0.9"
        )

    def events(self) -> list[tuple[str, str, str | None]]:
        return [(e.event, e.status, e.detail) for e in self.telemetry.events()]


@pytest.fixture
def harness() -> Harness:
    return Harness()


def test_register_then_sign_in_with_a_passkey(harness: Harness) -> None:
    alice = harness.user(name="Alice")
    authenticator = SoftAuthenticator(sign_count=0)

    begun = harness.service.begin_registration(alice.id, auth_time=NOW.timestamp())
    options = begun.options
    assert options["rp"] == {"name": "Orcheo", "id": RP_ID}
    assert options["user"] == {
        "id": passkey_user_handle(alice.id),
        "name": "alice@example.com",
        "displayName": "Alice",
    }
    assert options["authenticatorSelection"]["residentKey"] == "required"
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["attestation"] == "none"
    assert options["excludeCredentials"] == []
    assert [p["alg"] for p in options["pubKeyCredParams"]] == [-7, -8, -257]
    assert options["timeout"] == 300_000

    passkey = harness.service.finish_registration(
        alice.id,
        begun.challenge_id,
        authenticator.register(options),
        user_agent=CHROME_ON_MAC,
    )
    assert passkey.user_id == alice.id
    assert passkey.credential_id == authenticator.credential_id_b64
    assert passkey.transports == ["internal", "hybrid"]
    assert passkey.backup_eligible is True
    assert passkey.backed_up is True
    assert passkey.name == "Chrome on macOS"
    assert passkey.created_at == NOW
    assert harness.repo.list_passkeys(alice.id) == [passkey]
    assert [(n.to, n.passkey_name, n.action) for n in harness.notices.sent] == [
        ("alice@example.com", "Chrome on macOS", "added")
    ]

    harness.clock.advance(minutes=5)
    login = harness.service.begin_authentication()
    assert login.options["rpId"] == RP_ID
    assert login.options["userVerification"] == "required"
    assert login.options["allowCredentials"] == []

    result = harness.service.finish_authentication(
        login.challenge_id,
        authenticator.sign_in(login.options),
        user_agent="pytest",
        ip="10.0.0.9",
    )
    assert result.user.id == alice.id
    assert result.user.last_login_at == harness.clock.now
    claims = jwt.decode(
        result.tokens.access_token,
        SECRET,
        algorithms=["HS256"],
        audience="orcheo",
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims["sub"] == str(alice.id)
    assert claims["auth_time"] == int(harness.clock.now.timestamp())
    stored = harness.repo.list_passkeys(alice.id)[0]
    assert stored.last_used_at == harness.clock.now
    assert ("auth.passkey_registration", "success", None) in harness.events()
    assert ("auth.login", "success", "passkey") in harness.events()


def test_registration_excludes_passkeys_the_user_already_has(
    harness: Harness,
) -> None:
    alice = harness.user()
    first = harness.register(alice, SoftAuthenticator(transports=["usb", "bogus"]))

    begun = harness.service.begin_registration(alice.id, auth_time=NOW.timestamp())

    assert begun.options["excludeCredentials"] == [
        {"id": first.credential_id, "type": "public-key", "transports": ["usb"]}
    ]


def test_registration_omits_unknown_transports_from_exclusions(
    harness: Harness,
) -> None:
    alice = harness.user()
    harness.register(alice, SoftAuthenticator(transports=["bogus"]))

    begun = harness.service.begin_registration(alice.id, auth_time=NOW.timestamp())

    assert "transports" not in begun.options["excludeCredentials"][0]


def test_registration_stores_a_custom_name_and_provider_names(
    harness: Harness,
) -> None:
    alice = harness.user()

    named = harness.register(
        alice, SoftAuthenticator(), name="  Work   laptop " + "x" * 80
    )
    provider = harness.register(
        alice,
        SoftAuthenticator(aaguid="bada5566-a7aa-401f-bd96-45619a55120d"),
        user_agent=CHROME_ON_MAC,
    )
    unnamed = harness.register(alice, SoftAuthenticator())

    assert named.name == ("Work laptop " + "x" * 80)[:64]
    assert provider.name == "1Password"
    assert unnamed.name == "Passkey"


def test_registration_ignores_transports_that_are_not_a_list(
    harness: Harness,
) -> None:
    alice = harness.user()
    authenticator = SoftAuthenticator()
    begun = harness.service.begin_registration(alice.id, auth_time=NOW.timestamp())
    credential = authenticator.register(begun.options)
    credential["response"]["transports"] = "internal"

    passkey = harness.service.finish_registration(
        alice.id, begun.challenge_id, credential
    )

    assert passkey.transports == []


def test_registration_records_single_device_passkeys(harness: Harness) -> None:
    alice = harness.user()

    passkey = harness.register(
        alice, SoftAuthenticator(backup_eligible=False, backed_up=False)
    )

    assert passkey.backup_eligible is False
    assert passkey.backed_up is False


@pytest.mark.parametrize(
    "auth_time",
    [None, "1759752000", True, NOW.timestamp() - 601],
    ids=["missing", "string", "bool", "stale"],
)
def test_adding_a_passkey_requires_a_recent_sign_in(
    harness: Harness, auth_time: object
) -> None:
    alice = harness.user()

    with pytest.raises(PasskeyReauthenticationRequiredError) as excinfo:
        harness.service.begin_registration(alice.id, auth_time=auth_time)

    assert excinfo.value.status_code == 403
    assert excinfo.value.code == "auth.reauthentication_required"


def test_a_sign_in_exactly_at_the_reauthentication_limit_is_recent(
    harness: Harness,
) -> None:
    alice = harness.user()

    begun = harness.service.begin_registration(
        alice.id, auth_time=int(NOW.timestamp()) - 600
    )

    assert begun.challenge_id


def test_registration_rejects_users_who_cannot_sign_in(harness: Harness) -> None:
    disabled = harness.user("off@example.com", status=UserStatus.DISABLED)

    with pytest.raises(PasskeyRejectedError) as excinfo:
        harness.service.begin_registration(disabled.id, auth_time=NOW.timestamp())

    assert excinfo.value.code == "auth.account_disabled"
    assert ("auth.passkey_registration", "failure", "account_disabled") in (
        harness.events()
    )

    restricted = Harness(allowed_email_domains=("example.org",))
    outsider = restricted.user()
    with pytest.raises(IdentityEmailDomainNotAllowedError):
        restricted.service.begin_registration(outsider.id, auth_time=NOW.timestamp())


def test_registration_challenge_is_bound_to_the_user(harness: Harness) -> None:
    alice = harness.user()
    mallory = harness.user("mallory@example.com")
    begun = harness.service.begin_registration(alice.id, auth_time=NOW.timestamp())
    credential = SoftAuthenticator().register(begun.options)

    with pytest.raises(PasskeyChallengeNotFoundError):
        harness.service.finish_registration(mallory.id, begun.challenge_id, credential)

    # A refused attempt by someone else does not burn Alice's challenge.
    passkey = harness.service.finish_registration(
        alice.id, begun.challenge_id, credential
    )
    assert passkey.user_id == alice.id


def test_registration_challenge_cannot_be_used_for_sign_in(harness: Harness) -> None:
    alice = harness.user()
    begun = harness.service.begin_registration(alice.id, auth_time=NOW.timestamp())

    with pytest.raises(PasskeyChallengeNotFoundError):
        harness.service.finish_authentication(begun.challenge_id, {"rawId": "AA"})


@pytest.mark.parametrize(
    "forge",
    [
        {"origin": "https://evil.example.com"},
        {"origin": "http://app.localhost:2026"},
        {"rp_id": "example.com"},
        {"user_verified": False},
        {"challenge": bytes_to_base64url(b"another challenge")},
    ],
    ids=["foreign-origin", "sibling-origin", "rp-id", "no-uv", "challenge"],
)
def test_registration_rejects_forged_responses(
    harness: Harness, forge: dict[str, Any]
) -> None:
    alice = harness.user()
    begun = harness.service.begin_registration(alice.id, auth_time=NOW.timestamp())
    credential = SoftAuthenticator().register(begun.options, **forge)

    with pytest.raises(PasskeyRejectedError) as excinfo:
        harness.service.finish_registration(alice.id, begun.challenge_id, credential)

    assert excinfo.value.status_code == 400
    assert harness.repo.list_passkeys(alice.id) == []
    assert ("auth.passkey_registration", "failure", "verification_failed") in (
        harness.events()
    )
    # The challenge was consumed by the failed attempt.
    with pytest.raises(PasskeyChallengeNotFoundError):
        harness.service.finish_registration(alice.id, begun.challenge_id, credential)


def test_registration_rejects_an_already_registered_credential(
    harness: Harness,
) -> None:
    alice = harness.user()
    authenticator = SoftAuthenticator()
    harness.register(alice, authenticator)

    with pytest.raises(PasskeyAlreadyRegisteredError):
        harness.register(alice, authenticator)


def test_registration_enforces_the_passkey_limit(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = harness.user()
    monkeypatch.setattr(passkeys_module, "MAX_PASSKEYS_PER_USER", 1)
    begun = harness.service.begin_registration(alice.id, auth_time=NOW.timestamp())
    harness.register(alice, SoftAuthenticator())

    # A ceremony started before the limit was reached cannot exceed it.
    with pytest.raises(PasskeyLimitReachedError) as excinfo:
        harness.service.finish_registration(
            alice.id, begun.challenge_id, SoftAuthenticator().register(begun.options)
        )
    assert excinfo.value.status_code == 409
    with pytest.raises(PasskeyLimitReachedError):
        harness.service.begin_registration(alice.id, auth_time=NOW.timestamp())


def test_sign_in_challenge_is_single_use(harness: Harness) -> None:
    alice = harness.user()
    authenticator = SoftAuthenticator()
    harness.register(alice, authenticator)
    begun = harness.service.begin_authentication()
    credential = authenticator.sign_in(begun.options)
    harness.service.finish_authentication(begun.challenge_id, credential)

    with pytest.raises(PasskeyChallengeNotFoundError):
        harness.service.finish_authentication(begun.challenge_id, credential)


def test_sign_in_challenge_expires(harness: Harness) -> None:
    alice = harness.user()
    authenticator = SoftAuthenticator()
    harness.register(alice, authenticator)
    begun = harness.service.begin_authentication()
    credential = authenticator.sign_in(begun.options)

    harness.clock.advance(seconds=300)

    with pytest.raises(PasskeyChallengeNotFoundError):
        harness.service.finish_authentication(begun.challenge_id, credential)


@pytest.mark.parametrize(
    "forge",
    [
        {"origin": "https://evil.example.com"},
        {"origin": "http://app.localhost:2026"},
        {"rp_id": "example.com"},
        {"user_verified": False},
    ],
    ids=["foreign-origin", "sibling-origin", "rp-id", "no-uv"],
)
def test_sign_in_rejects_forged_responses(
    harness: Harness, forge: dict[str, Any]
) -> None:
    alice = harness.user()
    authenticator = SoftAuthenticator()
    harness.register(alice, authenticator)

    with pytest.raises(PasskeyRejectedError) as excinfo:
        harness.sign_in(authenticator, **forge)

    assert excinfo.value.code == "auth.passkey_invalid"
    assert ("auth.passkey_login", "failure", "verification_failed") in (
        harness.events()
    )
    assert harness.repo.list_passkeys(alice.id)[0].last_used_at is None


def test_sign_in_rejects_a_tampered_signature(harness: Harness) -> None:
    alice = harness.user()
    authenticator = SoftAuthenticator()
    harness.register(alice, authenticator)
    begun = harness.service.begin_authentication()
    credential = authenticator.sign_in(begun.options)
    signature = bytearray(base64url_to_bytes(credential["response"]["signature"]))
    signature[-1] ^= 0x01
    credential["response"]["signature"] = bytes_to_base64url(bytes(signature))

    with pytest.raises(PasskeyRejectedError):
        harness.service.finish_authentication(begun.challenge_id, credential)


def test_sign_in_rejects_a_signature_counter_that_went_backwards(
    harness: Harness,
) -> None:
    alice = harness.user()
    authenticator = SoftAuthenticator(sign_count=5)
    harness.register(alice, authenticator)
    harness.sign_in(authenticator)
    assert harness.repo.list_passkeys(alice.id)[0].sign_count == 6

    with pytest.raises(PasskeyRejectedError):
        harness.sign_in(authenticator, sign_count=6)


def test_synced_passkeys_with_a_zero_counter_keep_working(harness: Harness) -> None:
    alice = harness.user()
    authenticator = SoftAuthenticator(sign_count=0)
    harness.register(alice, authenticator)

    harness.sign_in(authenticator)
    harness.sign_in(authenticator)

    assert harness.repo.list_passkeys(alice.id)[0].sign_count == 0


def test_sign_in_rejects_an_unknown_passkey(harness: Harness) -> None:
    authenticator = SoftAuthenticator()
    authenticator.user_handle = b"someone"

    with pytest.raises(PasskeyRejectedError) as excinfo:
        harness.sign_in(authenticator)

    assert excinfo.value.code == "auth.passkey_unknown"
    assert ("auth.passkey_login", "failure", "unknown") in harness.events()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.pop("rawId"),
        lambda c: c.update(rawId=""),
        lambda c: c.update(rawId=42),
        lambda c: c.update(rawId="A"),
        lambda c: c.update(rawId="ünïcode"),
    ],
    ids=["missing", "empty", "not-a-string", "invalid", "non-ascii"],
)
def test_sign_in_rejects_a_malformed_credential_id(
    harness: Harness, mutate: Callable[[dict[str, Any]], object]
) -> None:
    alice = harness.user()
    authenticator = SoftAuthenticator()
    harness.register(alice, authenticator)
    begun = harness.service.begin_authentication()
    credential = authenticator.sign_in(begun.options)
    mutate(credential)

    with pytest.raises(PasskeyRejectedError) as excinfo:
        harness.service.finish_authentication(begun.challenge_id, credential)

    assert excinfo.value.code == "auth.passkey_invalid"
    assert ("auth.passkey_login", "failure", "malformed") in harness.events()


@pytest.mark.parametrize(
    "user_handle",
    [None, b"another user", "%%%"],
    ids=["missing", "other-user", "malformed"],
)
def test_sign_in_requires_the_passkey_owners_user_handle(
    harness: Harness, user_handle: object
) -> None:
    alice = harness.user()
    authenticator = SoftAuthenticator()
    harness.register(alice, authenticator)

    with pytest.raises(PasskeyRejectedError):
        harness.sign_in(authenticator, user_handle=user_handle)

    assert ("auth.passkey_login", "failure", "user_handle_mismatch") in (
        harness.events()
    )


def test_sign_in_rejects_a_response_without_a_response_object(
    harness: Harness,
) -> None:
    alice = harness.user()
    authenticator = SoftAuthenticator()
    harness.register(alice, authenticator)
    begun = harness.service.begin_authentication()
    credential = authenticator.sign_in(begun.options)
    credential["response"] = "nope"

    with pytest.raises(PasskeyRejectedError):
        harness.service.finish_authentication(begun.challenge_id, credential)


def test_sign_in_rejects_a_disabled_account(harness: Harness) -> None:
    alice = harness.user()
    authenticator = SoftAuthenticator()
    harness.register(alice, authenticator)
    harness.repo.update_user(alice.model_copy(update={"status": UserStatus.DISABLED}))

    with pytest.raises(PasskeyRejectedError) as excinfo:
        harness.sign_in(authenticator)

    assert excinfo.value.code == "auth.account_disabled"
    assert ("auth.passkey_login", "failure", "account_disabled") in harness.events()


def test_sign_in_rechecks_the_email_domain_allowlist(harness: Harness) -> None:
    alice = harness.user()
    authenticator = SoftAuthenticator()
    harness.register(alice, authenticator)
    restricted = Harness(repo=harness.repo, allowed_email_domains=("example.org",))

    with pytest.raises(IdentityEmailDomainNotAllowedError):
        restricted.sign_in(authenticator)

    assert ("auth.email_domain_rejected", "failure", None) in restricted.events()


def test_passkeys_are_unavailable_without_a_relying_party() -> None:
    harness = Harness(webauthn_rp_id=None, webauthn_origins=())
    alice = harness.user()

    assert harness.service.rp_id is None
    for start in (
        harness.service.begin_authentication,
        lambda: harness.service.begin_registration(alice.id, auth_time=None),
        lambda: harness.service.finish_authentication(UNKNOWN_CHALLENGE, {}),
        lambda: harness.service.finish_registration(alice.id, UNKNOWN_CHALLENGE, {}),
    ):
        with pytest.raises(PasskeysUnavailableError) as excinfo:
            start()
        assert excinfo.value.status_code == 404
        assert excinfo.value.code == "auth.passkeys_unavailable"
    # Existing passkeys stay manageable.
    assert harness.service.list_passkeys(alice.id) == []


def test_rename_and_delete_passkeys(harness: Harness) -> None:
    alice = harness.user()
    bob = harness.user("bob@example.com")
    passkey = harness.register(alice, SoftAuthenticator())

    renamed = harness.service.rename_passkey(alice.id, passkey.id, "  Phone \n key ")
    assert renamed.name == "Phone key"
    with pytest.raises(PasskeyRejectedError) as excinfo:
        harness.service.rename_passkey(alice.id, passkey.id, "   ")
    assert excinfo.value.code == "auth.passkey_name_invalid"
    with pytest.raises(PasskeyNotFoundError):
        harness.service.rename_passkey(bob.id, passkey.id, "Mine now")
    with pytest.raises(PasskeyNotFoundError):
        harness.service.delete_passkey(bob.id, passkey.id)

    removed = harness.service.delete_passkey(alice.id, passkey.id)

    assert removed.id == passkey.id
    assert harness.service.list_passkeys(alice.id) == []
    assert [(n.action, n.passkey_name) for n in harness.notices.sent] == [
        ("added", "Passkey"),
        ("removed", "Phone key"),
    ]
    assert ("auth.passkey_removed", "success", None) in harness.events()


def test_a_failed_notice_does_not_undo_the_change(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenNotices:
        def send_passkey_notice(self, email: PasskeyNoticeEmail) -> None:
            raise RuntimeError("smtp down")

    harness = Harness(notices=BrokenNotices())
    alice = harness.user()

    passkey = harness.register(alice, SoftAuthenticator())

    assert harness.repo.list_passkeys(alice.id) == [passkey]
    assert "Failed to send the passkey added notice" in caplog.text


def test_notices_are_optional() -> None:
    harness = Harness()
    quiet = PasskeyService(harness.identity)
    alice = harness.user()
    begun = quiet.begin_registration(alice.id, auth_time=NOW.timestamp())

    passkey = quiet.finish_registration(
        alice.id, begun.challenge_id, SoftAuthenticator().register(begun.options)
    )
    quiet.delete_passkey(alice.id, passkey.id)

    assert harness.notices.sent == []
    assert quiet.identity is harness.identity


@pytest.mark.parametrize(
    ("aaguid", "user_agent", "expected"),
    [
        (
            "ea9b8d66-4d01-1d21-3ce4-b6b48cb575d4",
            CHROME_ON_MAC,
            "Google Password Manager",
        ),
        (None, CHROME_ON_MAC, "Chrome on macOS"),
        (
            None,
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, "
            "like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",
            "Edge on Windows",
        ),
        (
            None,
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
            "Safari on iPhone",
        ),
        (
            None,
            "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
            "Firefox on Linux",
        ),
        (None, "SomeBot/1.0 Chrome/1.0", "Chrome"),
        (None, "Mozilla/5.0 (Android 14)", "Android"),
        ("00000000-0000-0000-0000-000000000000", "curl/8.0", "Passkey"),
        (None, None, "Passkey"),
    ],
)
def test_default_passkey_name(
    aaguid: str | None, user_agent: str | None, expected: str
) -> None:
    assert default_passkey_name(aaguid, user_agent) == expected


def test_passkey_user_handle_is_the_user_id_bytes() -> None:
    harness = Harness()
    alice = harness.user()

    assert base64url_to_bytes(passkey_user_handle(alice.id)) == alice.id.bytes
