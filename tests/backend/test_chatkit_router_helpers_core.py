"""Tests for ChatKit router helper utilities that don't hit persistence."""

from __future__ import annotations
import importlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4
import jwt
import pytest
from fastapi import HTTPException, status
from orcheo_backend.app.authentication import AuthenticationError
from orcheo_backend.app.chatkit_tokens import ChatKitTokenConfigurationError
from orcheo_backend.app.routers import chatkit
from tests.backend.chatkit_router_helpers_support import (
    make_chatkit_request,
)


pytestmark = pytest.mark.usefixtures("reset_chatkit_limiters")


class _HeaderProxy(str):
    """Custom header type controlling the split output for branch coverage."""

    def __new__(cls, value: str, parts: list[str]) -> _HeaderProxy:
        obj = str.__new__(cls, value)
        obj._parts = parts
        return obj

    def split(self) -> list[str]:  # type: ignore[override]
        return list(self._parts)


def test_coerce_rate_limit_returns_default_for_invalid_values() -> None:
    config = {"limit": "not-an-int"}
    assert chatkit._coerce_rate_limit(config, "limit", 25) == 25


def test_chatkit_error_includes_auth_mode() -> None:
    exc = chatkit._chatkit_error(
        status.HTTP_401_UNAUTHORIZED,
        message="boom",
        code="chatkit.auth.test",
        auth_mode="jwt",
    )
    assert exc.detail["auth_mode"] == "jwt"


def test_build_chatkit_log_context_includes_optional_fields() -> None:
    auth_result = chatkit.ChatKitAuthResult(
        workflow_id=uuid4(),
        actor="tester",
        auth_mode="jwt",
        subject="bob",
    )
    parsed_request = SimpleNamespace(
        thread_id=uuid4(),
        type="response.create",
    )

    log_context = chatkit._build_chatkit_log_context(auth_result, parsed_request)
    assert log_context["thread_id"] == str(parsed_request.thread_id)
    assert log_context["request_type"] == "response.create"


def test_extract_bearer_token_requires_header() -> None:
    with pytest.raises(HTTPException) as excinfo:
        chatkit._extract_bearer_token(None)
    assert excinfo.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_extract_bearer_token_requires_bearer_scheme() -> None:
    with pytest.raises(HTTPException) as excinfo:
        chatkit._extract_bearer_token("Basic token")
    assert excinfo.value.detail["code"] == "chatkit.auth.invalid_scheme"


def test_extract_bearer_token_requires_token_value() -> None:
    header = _HeaderProxy("Bearer", ["Bearer", "   "])
    with pytest.raises(HTTPException) as excinfo:
        chatkit._extract_bearer_token(header)
    assert excinfo.value.detail["code"] == "chatkit.auth.missing_token"


def test_decode_chatkit_jwt_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise() -> None:
        raise ChatKitTokenConfigurationError("missing key")

    monkeypatch.setattr(chatkit, "load_chatkit_token_settings", _raise)

    with pytest.raises(HTTPException) as excinfo:
        chatkit._decode_chatkit_jwt("token")
    assert excinfo.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_decode_chatkit_jwt_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        signing_key="secret",
        algorithm="HS256",
        audience="aud",
        issuer="iss",
    )
    monkeypatch.setattr(chatkit, "load_chatkit_token_settings", lambda: settings)

    def _raise_decode(*args: Any, **kwargs: Any) -> None:
        raise jwt.PyJWTError("bad")

    monkeypatch.setattr(chatkit.jwt, "decode", _raise_decode)  # type: ignore[attr-defined]

    with pytest.raises(HTTPException) as excinfo:
        chatkit._decode_chatkit_jwt("token")
    assert excinfo.value.detail["code"] == "chatkit.auth.invalid_jwt"


def test_decode_chatkit_jwt_success(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        signing_key="secret",
        algorithm="HS256",
        audience="aud",
        issuer="iss",
    )
    monkeypatch.setattr(chatkit, "load_chatkit_token_settings", lambda: settings)
    monkeypatch.setattr(
        chatkit.jwt,
        "decode",
        lambda *args, **kwargs: {"sub": "alice"},
    )  # type: ignore[attr-defined]

    payload = chatkit._decode_chatkit_jwt("token")
    assert payload["sub"] == "alice"


def _proxy_auth_settings(
    *,
    secret: str | None = None,
    ips: tuple[str, ...] = (),
    dev_login_enabled: bool = False,
    dev_cookie_name: str | None = None,
    dev_workspace_ids: tuple[str, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        trusted_proxy_secret=secret,
        trusted_proxy_ips=ips,
        dev_login_enabled=dev_login_enabled,
        dev_login_cookie_name=dev_cookie_name,
        dev_login_workspace_ids=dev_workspace_ids,
    )


def test_request_from_trusted_proxy_requires_configuration() -> None:
    request = make_chatkit_request(headers={"X-Orcheo-Proxy-Secret": "anything"})
    assert chatkit._request_from_trusted_proxy(request, _proxy_auth_settings()) is False


def test_request_from_trusted_proxy_matches_secret() -> None:
    settings = _proxy_auth_settings(secret="s3cret")
    good = make_chatkit_request(headers={"X-Orcheo-Proxy-Secret": "s3cret"})
    bad = make_chatkit_request(headers={"X-Orcheo-Proxy-Secret": "nope"})
    missing = make_chatkit_request()
    assert chatkit._request_from_trusted_proxy(good, settings) is True
    assert chatkit._request_from_trusted_proxy(bad, settings) is False
    assert chatkit._request_from_trusted_proxy(missing, settings) is False


def test_request_from_trusted_proxy_matches_ip_allowlist() -> None:
    settings = _proxy_auth_settings(ips=("127.0.0.0/8",))
    request = make_chatkit_request()  # client host is 127.0.0.1
    assert chatkit._request_from_trusted_proxy(request, settings) is True
    assert (
        chatkit._request_from_trusted_proxy(
            request, _proxy_auth_settings(ips=("10.0.0.0/8",))
        )
        is False
    )


def test_extract_proxy_identity_ignores_untrusted_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chatkit, "load_auth_settings", _proxy_auth_settings)
    request = make_chatkit_request(
        headers={
            "X-Orcheo-OAuth-Subject": "attacker",
            "X-Orcheo-OAuth-Workspaces": "ws-1",
        }
    )
    assert chatkit._extract_proxy_identity(request) == (None, frozenset())


def test_extract_proxy_identity_reads_trusted_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chatkit, "load_auth_settings", lambda: _proxy_auth_settings(secret="s3cret")
    )
    request = make_chatkit_request(
        headers={
            "X-Orcheo-Proxy-Secret": "s3cret",
            "X-Orcheo-OAuth-Subject": "alice@example.com",
            "X-Orcheo-OAuth-Workspaces": "WS-1, ws-2",
        }
    )
    subject, workspaces = chatkit._extract_proxy_identity(request)
    assert subject == "alice@example.com"
    assert workspaces == frozenset({"ws-1", "ws-2"})


def test_extract_proxy_identity_dev_login_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chatkit,
        "load_auth_settings",
        lambda: _proxy_auth_settings(
            dev_login_enabled=True,
            dev_cookie_name="orcheo_dev_session",
            dev_workspace_ids=("WS-1",),
        ),
    )
    request = make_chatkit_request(
        cookies={"orcheo_dev_session": "dev@orcheo.local:abc123"}
    )
    subject, workspaces = chatkit._extract_proxy_identity(request)
    assert subject == "dev@orcheo.local"
    assert workspaces == frozenset({"ws-1"})


def _auth_result(subject: str | None) -> chatkit.ChatKitAuthResult:
    return chatkit.ChatKitAuthResult(
        workflow_id=uuid4(),
        actor="tester",
        auth_mode="publish",
        subject=subject,
    )


def test_resolve_owner_key_prefers_authenticated_subject() -> None:
    request = make_chatkit_request(headers={"X-Orcheo-Visitor-Id": "visitor-12345678"})
    owner = chatkit._resolve_owner_key(_auth_result("alice@example.com"), request)
    assert owner == "sub:alice@example.com"


def test_resolve_owner_key_uses_visitor_header_when_anonymous() -> None:
    request = make_chatkit_request(headers={"X-Orcheo-Visitor-Id": "visitor-12345678"})
    owner = chatkit._resolve_owner_key(_auth_result(None), request)
    assert owner == "visitor:visitor-12345678"


def test_resolve_owner_key_rejects_malformed_visitor_id() -> None:
    request = make_chatkit_request(headers={"X-Orcheo-Visitor-Id": "bad id!"})
    with pytest.raises(HTTPException) as excinfo:
        chatkit._resolve_owner_key(_auth_result(None), request)
    assert excinfo.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert excinfo.value.detail["code"] == "chatkit.auth.missing_visitor_id"


def test_resolve_owner_key_requires_visitor_id_without_identity() -> None:
    request = make_chatkit_request()
    with pytest.raises(HTTPException) as excinfo:
        chatkit._resolve_owner_key(_auth_result(None), request)
    assert excinfo.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert excinfo.value.detail["code"] == "chatkit.auth.missing_visitor_id"


def test_rate_limit_reraises_authentication_error() -> None:
    error = AuthenticationError(
        "denied",
        code="chatkit.rate",
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
    )

    class DummyLimiter:
        def hit(self, key: str, *, now: datetime) -> None:
            raise error

    with pytest.raises(HTTPException) as excinfo:
        chatkit._rate_limit(DummyLimiter(), "key", now=datetime.now(tz=UTC))
    assert excinfo.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_chatkit_router_import_does_not_require_vault_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Router import should stay lazy about settings-backed rate limits."""

    monkeypatch.delenv("ORCHEO_VAULT_ENCRYPTION_KEY", raising=False)

    reloaded = importlib.reload(chatkit)

    rate_limiters = reloaded._get_rate_limiters()
    assert rate_limiters.ip is not None
    assert rate_limiters.jwt is not None
    assert rate_limiters.workflow is not None
    assert rate_limiters.session is not None


def test_with_root_path_prepends_slash_to_relative_path() -> None:
    """When root_path exists and path lacks leading slash, one is added."""
    request = make_chatkit_request()
    # Inject a root_path into the request scope
    request.scope["root_path"] = "/api"

    result = chatkit._with_root_path(request, "chatkit/assets/ck1")
    assert result == "/api/chatkit/assets/ck1"


def test_with_root_path_absolute_path_with_root() -> None:
    """Absolute path is correctly combined with root_path."""
    request = make_chatkit_request()
    request.scope["root_path"] = "/api"

    result = chatkit._with_root_path(request, "/chatkit/assets/ck1")
    assert result == "/api/chatkit/assets/ck1"


def test_with_root_path_no_root() -> None:
    """Without root_path, path is returned unchanged."""
    request = make_chatkit_request()
    request.scope["root_path"] = ""

    result = chatkit._with_root_path(request, "/chatkit/assets/ck1")
    assert result == "/chatkit/assets/ck1"
