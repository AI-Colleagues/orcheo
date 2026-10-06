"""HTTP tests for the passkey (WebAuthn) endpoints of the first-party IdP."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from orcheo.identity import InMemoryIdentityRepository
from orcheo.workspace.email import AuthChallengeEmail, PasskeyNoticeEmail
from orcheo_backend.app.authentication import reset_authentication_state
from orcheo_backend.app.identity import (
    IdentityConfig,
    IdentityService,
    PasskeyService,
    reset_identity_state,
    set_identity_service,
    set_passkey_service,
)
from tests.backend.authentication_test_utils import create_test_client
from tests.backend.webauthn_test_utils import SoftAuthenticator

SECRET = "passkey-http-secret"  # noqa: S105 - test fixture
ISSUER = "https://auth.orcheo.test"
AUDIENCE = "orcheo-api"
ORIGIN = "http://localhost:2026"
FIREFOX_ON_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0"
)


class CapturingSender:
    def __init__(self) -> None:
        self.codes: list[AuthChallengeEmail] = []
        self.notices: list[PasskeyNoticeEmail] = []

    def send_auth_challenge(self, email: AuthChallengeEmail) -> None:
        self.codes.append(email)

    def send_passkey_notice(self, email: PasskeyNoticeEmail) -> None:
        self.notices.append(email)


@dataclass
class Api:
    client: TestClient
    repo: InMemoryIdentityRepository
    sender: CapturingSender
    install: Any
    services: dict[str, Any] = field(default_factory=dict)

    def sign_in_by_email(self, email: str = "alice@example.com") -> dict[str, Any]:
        self.client.post("/api/auth/email/start", json={"email": email})
        response = self.client.post(
            "/api/auth/email/verify",
            json={"email": email, "code": self.sender.codes[-1].otp_code},
        )
        assert response.status_code == 200
        return response.json()

    def register(
        self,
        token: str,
        authenticator: SoftAuthenticator,
        **body: Any,
    ) -> Any:
        headers = {"Authorization": f"Bearer {token}"}
        options = self.client.post(
            "/api/auth/passkey/register/options", headers=headers
        )
        assert options.status_code == 200, options.text
        begun = options.json()
        return self.client.post(
            "/api/auth/passkey/register/verify",
            headers={**headers, "User-Agent": FIREFOX_ON_WINDOWS},
            json={
                "challenge_id": begun["challenge_id"],
                "credential": authenticator.register(begun["options"]),
                **body,
            },
        )

    def sign_in(self, authenticator: SoftAuthenticator, **forge: Any) -> Any:
        begun = self.client.post("/api/auth/passkey/login/options").json()
        return self.client.post(
            "/api/auth/passkey/login/verify",
            json={
                "challenge_id": begun["challenge_id"],
                "credential": authenticator.sign_in(begun["options"], **forge),
            },
        )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _token(user_id: str, **claims: Any) -> str:
    now = datetime.now(tz=UTC)
    payload = {
        "sub": user_id,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "auth_time": int(now.timestamp()),
        **claims,
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[Api]:
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", SECRET)
    monkeypatch.setenv("ORCHEO_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("ORCHEO_AUTH_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")
    reset_authentication_state()
    reset_identity_state()

    repo = InMemoryIdentityRepository()
    sender = CapturingSender()

    def install(**config: Any) -> PasskeyService:
        config.setdefault("webauthn_rp_id", "localhost")
        config.setdefault("webauthn_origins", (ORIGIN,))
        identity = IdentityService(
            repo,
            email_sender=sender,
            config=IdentityConfig(
                jwt_secret=SECRET, issuer=ISSUER, audience=AUDIENCE, **config
            ),
        )
        passkeys = PasskeyService(identity, notice_sender=sender)
        set_identity_service(identity)
        set_passkey_service(passkeys)
        return passkeys

    install()
    client = create_test_client()
    try:
        yield Api(client=client, repo=repo, sender=sender, install=install)
    finally:
        set_passkey_service(None)
        set_identity_service(None)
        reset_identity_state()
        reset_authentication_state()


def test_passkey_lifecycle_over_http(api: Api) -> None:
    session = api.sign_in_by_email()
    claims = jwt.decode(
        session["access_token"],
        SECRET,
        algorithms=["HS256"],
        audience=AUDIENCE,
        options={"verify_exp": False, "verify_iat": False},
    )
    assert isinstance(claims["auth_time"], int)
    token = session["access_token"]
    authenticator = SoftAuthenticator()

    registered = api.register(token, authenticator, name="Laptop")
    assert registered.status_code == 201, registered.text
    summary = registered.json()
    assert summary["name"] == "Laptop"
    assert summary["credential_id"] == authenticator.credential_id_b64
    assert summary["transports"] == ["internal", "hybrid"]
    assert summary["backed_up"] is True
    assert summary["last_used_at"] is None

    listed = api.client.get("/api/auth/passkeys", headers=_bearer(token))
    assert listed.status_code == 200
    assert listed.json()["rp_id"] == "localhost"
    assert listed.json()["user_handle"]
    assert [p["id"] for p in listed.json()["passkeys"]] == [summary["id"]]

    renamed = api.client.patch(
        f"/api/auth/passkeys/{summary['id']}",
        headers=_bearer(token),
        json={"name": "Desk key"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Desk key"

    signed_in = api.sign_in(authenticator)
    assert signed_in.status_code == 200, signed_in.text
    body = signed_in.json()
    assert body["user"]["email"] == "alice@example.com"
    me = api.client.get("/api/auth/me", headers=_bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"
    refreshed = api.client.post(
        "/api/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert refreshed.status_code == 200

    removed = api.client.delete(
        f"/api/auth/passkeys/{summary['id']}", headers=_bearer(token)
    )
    assert removed.status_code == 204
    assert (
        api.client.get("/api/auth/passkeys", headers=_bearer(token)).json()["passkeys"]
        == []
    )
    assert [(n.action, n.passkey_name) for n in api.sender.notices] == [
        ("added", "Laptop"),
        ("removed", "Desk key"),
    ]


def test_registration_names_passkeys_after_the_browser(api: Api) -> None:
    token = api.sign_in_by_email()["access_token"]

    registered = api.register(token, SoftAuthenticator())

    assert registered.json()["name"] == "Firefox on Windows"


def test_adding_a_passkey_requires_a_recent_sign_in(api: Api) -> None:
    user_id = api.sign_in_by_email()["user"]["id"]
    stale = _token(
        user_id,
        auth_time=int((datetime.now(tz=UTC) - timedelta(hours=1)).timestamp()),
    )
    tokens_without_auth_time = _token(user_id, auth_time=None)

    for token in (stale, tokens_without_auth_time):
        response = api.client.post(
            "/api/auth/passkey/register/options", headers=_bearer(token)
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "auth.reauthentication_required"


def test_passkey_management_requires_a_studio_user(api: Api) -> None:
    anonymous = api.client.get("/api/auth/passkeys")
    assert anonymous.status_code == 401

    for token in (
        _token(str(uuid4()), token_use="service"),
        _token("not-a-uuid"),
    ):
        response = api.client.get("/api/auth/passkeys", headers=_bearer(token))
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "auth.user_required"


def test_registering_for_an_unknown_user_is_not_found(api: Api) -> None:
    response = api.client.post(
        "/api/auth/passkey/register/options", headers=_bearer(_token(str(uuid4())))
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "auth.passkey_not_found"


def test_sign_in_errors_map_to_http_statuses(api: Api) -> None:
    token = api.sign_in_by_email()["access_token"]
    authenticator = SoftAuthenticator()
    api.register(token, authenticator)

    stranger = SoftAuthenticator()
    stranger.user_handle = b"someone"
    unknown = api.sign_in(stranger)
    assert unknown.status_code == 400
    assert unknown.json()["detail"]["code"] == "auth.passkey_unknown"

    forged = api.sign_in(authenticator, origin="https://evil.example.com")
    assert forged.status_code == 400
    assert forged.json()["detail"]["code"] == "auth.passkey_invalid"

    expired = api.client.post(
        "/api/auth/passkey/login/verify",
        json={
            "challenge_id": str(uuid4()),
            "credential": authenticator.sign_in(
                {"rpId": "localhost", "challenge": "AA"}
            ),
        },
    )
    assert expired.status_code == 410
    assert expired.json()["detail"]["code"] == "auth.passkey_challenge_expired"


def test_registering_a_passkey_twice_is_a_conflict(api: Api) -> None:
    token = api.sign_in_by_email()["access_token"]
    authenticator = SoftAuthenticator()
    assert api.register(token, authenticator).status_code == 201

    duplicate = api.register(token, authenticator)

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "auth.passkey_already_registered"


def test_renaming_or_removing_an_unknown_passkey_is_not_found(api: Api) -> None:
    token = api.sign_in_by_email()["access_token"]
    missing = f"/api/auth/passkeys/{uuid4()}"

    renamed = api.client.patch(missing, headers=_bearer(token), json={"name": "x"})
    removed = api.client.delete(missing, headers=_bearer(token))

    assert renamed.status_code == 404
    assert removed.status_code == 404


def test_passkey_names_are_validated(api: Api) -> None:
    token = api.sign_in_by_email()["access_token"]
    passkey_id = api.register(token, SoftAuthenticator()).json()["id"]
    path = f"/api/auth/passkeys/{passkey_id}"

    empty = api.client.patch(path, headers=_bearer(token), json={"name": ""})
    blank = api.client.patch(path, headers=_bearer(token), json={"name": "   "})

    assert empty.status_code == 422
    assert blank.status_code == 400
    assert blank.json()["detail"]["code"] == "auth.passkey_name_invalid"


def test_sign_in_rechecks_the_email_domain_allowlist(api: Api) -> None:
    token = api.sign_in_by_email()["access_token"]
    authenticator = SoftAuthenticator()
    api.register(token, authenticator)
    api.install(allowed_email_domains=("example.org",))

    response = api.sign_in(authenticator)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "auth.email_domain_not_allowed"


def test_passkeys_unavailable_without_a_relying_party(api: Api) -> None:
    token = api.sign_in_by_email()["access_token"]
    api.install(webauthn_rp_id=None, webauthn_origins=())

    options = api.client.post("/api/auth/passkey/login/options")
    listed = api.client.get("/api/auth/passkeys", headers=_bearer(token))

    assert options.status_code == 404
    assert options.json()["detail"]["code"] == "auth.passkeys_unavailable"
    assert listed.status_code == 200
    assert listed.json()["rp_id"] is None


def test_passkey_sign_in_is_rate_limited(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORCHEO_AUTH_RATE_LIMIT_IP", "1")
    reset_authentication_state()

    first = api.client.post("/api/auth/passkey/login/options")
    second = api.client.post("/api/auth/passkey/login/options")
    verify = api.client.post(
        "/api/auth/passkey/login/verify",
        json={"challenge_id": first.json()["challenge_id"], "credential": {}},
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert verify.status_code == 429
