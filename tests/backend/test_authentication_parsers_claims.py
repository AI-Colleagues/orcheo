"""Authentication parser tests split from the extended suite."""

from __future__ import annotations
import pytest
from tests.backend.authentication_test_utils import reset_auth_state


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication state is cleared between tests."""

    yield from reset_auth_state(monkeypatch)


def test_infer_identity_type_from_claims() -> None:
    """_infer_identity_type determines identity type from JWT claims."""
    from orcheo_backend.app.authentication import _infer_identity_type

    assert _infer_identity_type({"token_use": "user"}) == "user"
    assert _infer_identity_type({"type": "service"}) == "service"
    assert _infer_identity_type({"typ": "client"}) == "service"  # client -> service
    assert _infer_identity_type({}) == "user"  # default


def test_infer_identity_type_with_lowercase_variations() -> None:
    """_infer_identity_type handles case-insensitive type values."""
    from orcheo_backend.app.authentication import _infer_identity_type

    assert _infer_identity_type({"token_use": "USER"}) == "user"
    assert _infer_identity_type({"type": "SERVICE"}) == "service"
    assert _infer_identity_type({"typ": "CLIENT"}) == "service"


def test_infer_identity_type_with_unrecognized_value() -> None:
    """_infer_identity_type continues loop for unrecognized type values."""
    from orcheo_backend.app.authentication import _infer_identity_type

    # Value that's a string but not one of the recognized types
    assert _infer_identity_type({"token_use": "machine"}) == "user"
    # Multiple unrecognized values
    assert _infer_identity_type({"token_use": "robot", "type": "bot"}) == "user"


def test_extract_scopes_from_various_claim_locations() -> None:
    """_extract_scopes finds scopes in multiple claim locations."""
    from orcheo_backend.app.authentication import _extract_scopes

    # String format (space-separated)
    scopes = set(_extract_scopes({"scope": "read write delete"}))
    assert scopes == {"read", "write", "delete"}

    # List format
    scopes = set(_extract_scopes({"scopes": ["read", "write"]}))
    assert scopes == {"read", "write"}

    # Nested in orcheo claim
    scopes = set(_extract_scopes({"orcheo": {"scopes": ["admin"]}}))
    assert scopes == {"admin"}

    # JSON string
    scopes = set(_extract_scopes({"scope": '["read", "write"]'}))
    assert scopes == {"read", "write"}


def test_extract_workspace_ids_from_claims() -> None:
    """_extract_workspace_ids finds workspace IDs in claims."""
    from orcheo_backend.app.authentication import _extract_workspace_ids

    # List format
    ids = set(_extract_workspace_ids({"workspace_ids": ["ws-1", "ws-2"]}))
    assert ids == {"ws-1", "ws-2"}

    # String format
    ids = set(_extract_workspace_ids({"workspace": "ws-1"}))
    assert ids == {"ws-1"}

    # Nested in orcheo claim
    ids = set(_extract_workspace_ids({"orcheo": {"workspace_ids": ["ws-3"]}}))
    assert ids == {"ws-3"}


def test_extract_workspace_ids_normalizes_case_and_whitespace() -> None:
    """_extract_workspace_ids normalizes claim values for comparisons."""
    from orcheo_backend.app.authentication import _extract_workspace_ids

    ids = set(_extract_workspace_ids({"workspace_ids": [" Team-A ", "TEAM-B"]}))
    assert ids == {"team-a", "team-b"}


def test_extract_scopes_with_nested_orcheo_claim() -> None:
    """_extract_scopes finds scopes in nested orcheo.scopes claim."""
    from orcheo_backend.app.authentication import _extract_scopes

    scopes = set(
        _extract_scopes({"orcheo": {"scopes": ["scope1", "scope2"]}, "scope": "scope3"})
    )

    # Should include both orcheo nested and top-level scopes
    assert "scope1" in scopes
    assert "scope2" in scopes
    assert "scope3" in scopes


def test_extract_workspace_ids_with_nested_orcheo_claim() -> None:
    """_extract_workspace_ids finds workspace IDs in nested orcheo claim."""
    from orcheo_backend.app.authentication import _extract_workspace_ids

    ids = set(
        _extract_workspace_ids(
            {"orcheo": {"workspace_ids": ["ws-1"]}, "workspace": "ws-2"}
        )
    )

    assert "ws-1" in ids
    assert "ws-2" in ids


def test_extract_scopes_with_non_string_non_list_value() -> None:
    """_extract_scopes handles claim values that can't be parsed."""
    from orcheo_backend.app.authentication import _extract_scopes

    # Orcheo nested with non-Mapping value
    scopes = set(_extract_scopes({"orcheo": "not-a-dict"}))
    assert len(scopes) == 0


def test_extract_workspace_ids_with_non_string_non_list_value() -> None:
    """_extract_workspace_ids handles claim values that can't be parsed."""
    from orcheo_backend.app.authentication import _extract_workspace_ids

    # Orcheo nested with non-Mapping value
    ids = set(_extract_workspace_ids({"orcheo": "not-a-dict"}))
    assert len(ids) == 0


def test_extract_scopes_with_null_nested_value() -> None:
    """_extract_scopes handles orcheo claim with None scopes value."""
    from orcheo_backend.app.authentication import _extract_scopes

    # orcheo is a Mapping but orcheo.scopes is None
    scopes = set(_extract_scopes({"orcheo": {"other_key": "value"}}))
    assert len(scopes) == 0


def test_extract_workspace_ids_with_null_nested_value() -> None:
    """_extract_workspace_ids handles orcheo claim with None workspace_ids value."""
    from orcheo_backend.app.authentication import _extract_workspace_ids

    # orcheo is a Mapping but orcheo.workspace_ids is None
    ids = set(_extract_workspace_ids({"orcheo": {"other_key": "value"}}))
    assert len(ids) == 0


def test_extract_identity_prefers_namespaced_access_token_claims() -> None:
    """Namespaced custom claims win over plain OIDC claims (access tokens)."""
    from orcheo_backend.app.authentication import extract_identity

    email, name = extract_identity(
        {
            "https://orcheo.ai/email": "ns@example.com",
            "https://orcheo.ai/name": "Namespaced User",
            "email": "plain@example.com",
            "name": "Plain User",
        }
    )
    assert email == "ns@example.com"
    assert name == "Namespaced User"


def test_extract_identity_falls_back_to_plain_oidc_claims() -> None:
    """Plain OIDC claims are used when namespaced ones are absent (ID tokens)."""
    from orcheo_backend.app.authentication import extract_identity

    assert extract_identity({"email": "a@b.com", "name": "Ann"}) == ("a@b.com", "Ann")
    assert extract_identity({"preferred_username": "ann"}) == (None, "ann")
    assert extract_identity({"given_name": "Ann", "family_name": "Lee"}) == (
        None,
        "Ann Lee",
    )


def test_extract_identity_returns_none_without_identity_claims() -> None:
    """A bare access token (sub/scope only) yields no identity."""
    from orcheo_backend.app.authentication import extract_identity

    assert extract_identity({"sub": "auth0|abc", "scope": "workflows:read"}) == (
        None,
        None,
    )
