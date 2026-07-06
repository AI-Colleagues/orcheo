"""Tests for LinkedInPostNode."""

import base64
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from orcheo.nodes.connectors.linkedin import LinkedInPostNode, _LinkedInAuthError


def _make_jwt(payload: dict[str, Any]) -> str:
    """Return a minimal JWT string with the given payload."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.sig"


def _mock_http_node(return_value: dict[str, Any]) -> MagicMock:
    """Return a mock that replaces HttpRequestNode and returns the given value."""
    instance = AsyncMock(return_value=return_value)
    cls = MagicMock(return_value=instance)
    return cls


@pytest.fixture
def node() -> LinkedInPostNode:
    return LinkedInPostNode(
        name="post_linkedin",
        linkedin_access_token="access-tok",
        linkedin_refresh_token="refresh-tok",
        linkedin_id_token="",
    )


# ---------------------------------------------------------------------------
# Static / class method unit tests
# ---------------------------------------------------------------------------


def test_base64url_decode_no_padding() -> None:
    raw = base64.urlsafe_b64encode(b"hello").rstrip(b"=").decode()
    assert LinkedInPostNode.base64url_decode(raw) == b"hello"


def test_parse_jwt_payload_valid() -> None:
    jwt = _make_jwt({"sub": "abc123"})
    assert LinkedInPostNode.parse_jwt_payload(jwt) == {"sub": "abc123"}


def test_parse_jwt_payload_invalid_format() -> None:
    with pytest.raises(ValueError, match="Invalid id_token format"):
        LinkedInPostNode.parse_jwt_payload("only.two")


def test_parse_jwt_payload_non_object_payload() -> None:
    header = base64.urlsafe_b64encode(b"{}").rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(b'"string"').rstrip(b"=").decode()
    with pytest.raises(ValueError, match="must be a JSON object"):
        LinkedInPostNode.parse_jwt_payload(f"{header}.{body}.sig")


def test_read_configurable_defaults_when_not_mapping() -> None:
    result = LinkedInPostNode.read_configurable(None)  # type: ignore[arg-type]
    assert result["post_as"] == "person"
    assert result["visibility"] == "PUBLIC"


def test_read_configurable_defaults_when_configurable_not_mapping() -> None:
    result = LinkedInPostNode.read_configurable({"configurable": "not-a-mapping"})  # type: ignore[arg-type]
    assert result["post_as"] == "person"
    assert result["visibility"] == "PUBLIC"


def test_read_configurable_merges_overrides() -> None:
    config = {"configurable": {"commentary": "Custom post", "post_as": "organization"}}
    result = LinkedInPostNode.read_configurable(config)
    assert result["commentary"] == "Custom post"
    assert result["post_as"] == "organization"
    assert result["linkedin_version"] == "202604"


def test_linkedin_headers_with_json_content() -> None:
    h = LinkedInPostNode.linkedin_headers("tok", "202604")
    assert h["Authorization"] == "Bearer tok"
    assert h["Content-Type"] == "application/json"
    assert h["Linkedin-Version"] == "202604"


def test_linkedin_headers_without_json_content() -> None:
    h = LinkedInPostNode.linkedin_headers("tok", "202604", json_content=False)
    assert "Content-Type" not in h


def test_create_post_payload_structure() -> None:
    payload = LinkedInPostNode.create_post_payload(
        "urn:li:person:X", "Hello!", "PUBLIC"
    )
    assert payload["author"] == "urn:li:person:X"
    assert payload["commentary"] == "Hello!"
    assert payload["visibility"] == "PUBLIC"
    assert payload["lifecycleState"] == "PUBLISHED"


# ---------------------------------------------------------------------------
# resolve_person_identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_person_identity_from_id_token(node: LinkedInPostNode) -> None:
    jwt = _make_jwt({"sub": "person123"})
    person_id, author_urn = await node.resolve_person_identity({}, None, "tok", jwt)
    assert person_id == "person123"
    assert author_urn == "urn:li:person:person123"


@pytest.mark.asyncio
async def test_resolve_person_identity_id_token_missing_sub(
    node: LinkedInPostNode,
) -> None:
    jwt = _make_jwt({"name": "Alice"})
    with pytest.raises(ValueError, match="Could not resolve member id from id_token"):
        await node.resolve_person_identity({}, None, "tok", jwt)


@pytest.mark.asyncio
async def test_resolve_person_identity_from_userinfo(node: LinkedInPostNode) -> None:
    http_result = {
        "node_results": {
            "resolve_person_request": {
                "status_code": 200,
                "json": {"sub": "person456"},
            }
        }
    }
    with patch("orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(http_result)):
        person_id, author_urn = await node.resolve_person_identity({}, None, "tok", "")
    assert person_id == "person456"
    assert author_urn == "urn:li:person:person456"


@pytest.mark.asyncio
async def test_resolve_person_identity_userinfo_401_raises_auth_error(
    node: LinkedInPostNode,
) -> None:
    http_result = {
        "node_results": {
            "resolve_person_request": {
                "status_code": 401,
                "content": "Unauthorized",
            }
        }
    }
    with patch("orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(http_result)):
        with pytest.raises(_LinkedInAuthError):
            await node.resolve_person_identity({}, None, "tok", "")


@pytest.mark.asyncio
async def test_resolve_person_identity_userinfo_bad_status(
    node: LinkedInPostNode,
) -> None:
    http_result = {
        "node_results": {
            "resolve_person_request": {
                "status_code": 403,
                "content": "Forbidden",
            }
        }
    }
    with patch("orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(http_result)):
        with pytest.raises(ValueError, match="userinfo failed"):
            await node.resolve_person_identity({}, None, "tok", "")


@pytest.mark.asyncio
async def test_resolve_person_identity_userinfo_non_json(
    node: LinkedInPostNode,
) -> None:
    http_result = {
        "node_results": {
            "resolve_person_request": {
                "status_code": 200,
                "json": None,
            }
        }
    }
    with patch("orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(http_result)):
        with pytest.raises(ValueError, match="non-JSON response"):
            await node.resolve_person_identity({}, None, "tok", "")


@pytest.mark.asyncio
async def test_resolve_person_identity_userinfo_missing_sub(
    node: LinkedInPostNode,
) -> None:
    http_result = {
        "node_results": {
            "resolve_person_request": {
                "status_code": 200,
                "json": {"name": "Alice"},
            }
        }
    }
    with patch("orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(http_result)):
        with pytest.raises(ValueError, match="Could not resolve member id"):
            await node.resolve_person_identity({}, None, "tok", "")


# ---------------------------------------------------------------------------
# resolve_organization_urn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_organization_urn_success(node: LinkedInPostNode) -> None:
    http_result = {
        "node_results": {
            "resolve_organization_request": {
                "status_code": 200,
                "json": {
                    "elements": [
                        {"organization": "urn:li:organization:123", "state": "APPROVED"}
                    ]
                },
            }
        }
    }
    with patch("orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(http_result)):
        urn = await node.resolve_organization_urn({}, None, "tok", "202604")
    assert urn == "urn:li:organization:123"


@pytest.mark.asyncio
async def test_resolve_organization_urn_401_raises_auth_error(
    node: LinkedInPostNode,
) -> None:
    http_result = {
        "node_results": {
            "resolve_organization_request": {
                "status_code": 401,
                "content": "Unauthorized",
            }
        }
    }
    with patch("orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(http_result)):
        with pytest.raises(_LinkedInAuthError):
            await node.resolve_organization_urn({}, None, "tok", "202604")


@pytest.mark.asyncio
async def test_resolve_organization_urn_bad_status(node: LinkedInPostNode) -> None:
    http_result = {
        "node_results": {
            "resolve_organization_request": {
                "status_code": 403,
                "content": "Forbidden",
            }
        }
    }
    with patch("orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(http_result)):
        with pytest.raises(ValueError, match="organizationAcls failed"):
            await node.resolve_organization_urn({}, None, "tok", "202604")


@pytest.mark.asyncio
async def test_resolve_organization_urn_no_approved(node: LinkedInPostNode) -> None:
    http_result = {
        "node_results": {
            "resolve_organization_request": {
                "status_code": 200,
                "json": {"elements": []},
            }
        }
    }
    with patch("orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(http_result)):
        with pytest.raises(ValueError, match="No approved organization URNs"):
            await node.resolve_organization_urn({}, None, "tok", "202604")


@pytest.mark.asyncio
async def test_resolve_organization_urn_multiple(node: LinkedInPostNode) -> None:
    http_result = {
        "node_results": {
            "resolve_organization_request": {
                "status_code": 200,
                "json": {
                    "elements": [
                        {"organization": "urn:li:organization:1", "state": "APPROVED"},
                        {"organization": "urn:li:organization:2", "state": "APPROVED"},
                    ]
                },
            }
        }
    }
    with patch("orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(http_result)):
        with pytest.raises(ValueError, match="Multiple approved organization URNs"):
            await node.resolve_organization_urn({}, None, "tok", "202604")


@pytest.mark.asyncio
async def test_resolve_organization_urn_non_mapping_element_skipped(
    node: LinkedInPostNode,
) -> None:
    """Elements that are not Mappings should be skipped; a valid element still wins."""
    http_result = {
        "node_results": {
            "resolve_organization_request": {
                "status_code": 200,
                "json": {
                    "elements": [
                        "not-a-mapping",
                        {"organization": "urn:li:organization:99", "state": "APPROVED"},
                    ]
                },
            }
        }
    }
    with patch("orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(http_result)):
        urn = await node.resolve_organization_urn({}, None, "tok", "202604")
    assert urn == "urn:li:organization:99"


@pytest.mark.asyncio
async def test_resolve_organization_urn_element_wrong_state_skipped(
    node: LinkedInPostNode,
) -> None:
    """Elements with state != 'APPROVED' should not be included in the URN list."""
    http_result = {
        "node_results": {
            "resolve_organization_request": {
                "status_code": 200,
                "json": {
                    "elements": [
                        {"organization": "urn:li:organization:1", "state": "PENDING"},
                        {"organization": "urn:li:organization:2", "state": "APPROVED"},
                    ]
                },
            }
        }
    }
    with patch("orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(http_result)):
        urn = await node.resolve_organization_urn({}, None, "tok", "202604")
    assert urn == "urn:li:organization:2"


@pytest.mark.asyncio
async def test_resolve_organization_urn_non_json(node: LinkedInPostNode) -> None:
    http_result = {
        "node_results": {
            "resolve_organization_request": {
                "status_code": 200,
                "json": None,
            }
        }
    }
    with patch("orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(http_result)):
        with pytest.raises(ValueError, match="non-JSON response"):
            await node.resolve_organization_urn({}, None, "tok", "202604")


# ---------------------------------------------------------------------------
# resolve_author_urn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_author_urn_organization(node: LinkedInPostNode) -> None:
    http_result = {
        "node_results": {
            "resolve_organization_request": {
                "status_code": 200,
                "json": {
                    "elements": [
                        {"organization": "urn:li:organization:42", "state": "APPROVED"}
                    ]
                },
            }
        }
    }
    configurable = {"post_as": "organization", "linkedin_version": "202604"}
    with patch("orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(http_result)):
        author_urn, person_id, org_urn = await node.resolve_author_urn(
            {}, None, configurable, "tok"
        )
    assert author_urn == "urn:li:organization:42"
    assert person_id is None
    assert org_urn == "urn:li:organization:42"


@pytest.mark.asyncio
async def test_resolve_author_urn_organization_uses_configurable_urn(
    node: LinkedInPostNode,
) -> None:
    configurable = {
        "post_as": "organization",
        "linkedin_version": "202604",
        "organization_urn": "urn:li:organization:999",
    }
    with patch.object(
        LinkedInPostNode, "resolve_organization_urn", new_callable=AsyncMock
    ) as mock_resolve:
        author_urn, person_id, org_urn = await node.resolve_author_urn(
            {}, None, configurable, "tok"
        )

    mock_resolve.assert_not_awaited()
    assert author_urn == "urn:li:organization:999"
    assert person_id is None
    assert org_urn == "urn:li:organization:999"


@pytest.mark.asyncio
async def test_resolve_author_urn_invalid_post_as(node: LinkedInPostNode) -> None:
    configurable = {
        "post_as": "company",
        "linkedin_version": "202604",
    }
    with pytest.raises(ValueError, match="post_as must be either"):
        await node.resolve_author_urn({}, None, configurable, "tok")


# ---------------------------------------------------------------------------
# run — happy paths and error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_person_post_success(node: LinkedInPostNode) -> None:
    jwt = _make_jwt({"sub": "person789"})
    node.linkedin_id_token = jwt

    create_result = {
        "node_results": {
            "create_post_request": {
                "status_code": 201,
                "headers": {"x-restli-id": "post-abc"},
                "content": "",
            }
        }
    }
    config = {
        "configurable": {
            "commentary": "Hello LinkedIn!",
            "post_as": "person",
            "visibility": "PUBLIC",
            "linkedin_version": "202604",
        }
    }
    with patch("orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(create_result)):
        result = await node.run({}, config)

    assert result["post_id"] == "post-abc"
    assert result["post_as"] == "person"
    assert result["person_id"] == "person789"
    assert result["organization_urn"] is None
    assert result["refresh_token_configured"] is True
    assert result["status_code"] == 201


@pytest.mark.asyncio
async def test_run_missing_access_token_raises(node: LinkedInPostNode) -> None:
    node.linkedin_access_token = ""
    with pytest.raises(ValueError, match="linkedin_access_token"):
        await node.run({}, {})


@pytest.mark.asyncio
async def test_run_missing_refresh_token_raises(node: LinkedInPostNode) -> None:
    node.linkedin_refresh_token = ""
    with pytest.raises(ValueError, match="linkedin_refresh_token"):
        await node.run({}, {})


@pytest.mark.asyncio
async def test_run_empty_commentary_raises(node: LinkedInPostNode) -> None:
    config = {"configurable": {"commentary": "   "}}
    with pytest.raises(ValueError, match="commentary is required"):
        await node.run({}, config)


@pytest.mark.asyncio
async def test_run_invalid_visibility_raises(node: LinkedInPostNode) -> None:
    jwt = _make_jwt({"sub": "person1"})
    node.linkedin_id_token = jwt
    config = {"configurable": {"commentary": "Test", "visibility": "EVERYONE"}}
    with pytest.raises(ValueError, match="configurable.visibility must be one of"):
        await node.run({}, config)


@pytest.mark.asyncio
async def test_run_post_creation_failed_raises(node: LinkedInPostNode) -> None:
    jwt = _make_jwt({"sub": "person1"})
    node.linkedin_id_token = jwt

    create_result = {
        "node_results": {
            "create_post_request": {
                "status_code": 422,
                "headers": {},
                "content": "Unprocessable Entity",
            }
        }
    }
    config = {"configurable": {"commentary": "Test post"}}
    with patch("orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(create_result)):
        with pytest.raises(ValueError, match="Post creation failed"):
            await node.run({}, config)


# ---------------------------------------------------------------------------
# refresh_access_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_access_token_success(node: LinkedInPostNode) -> None:
    node.linkedin_client_id = "client-id"
    node.linkedin_client_secret = "client-secret"

    refresh_result = {
        "node_results": {
            "refresh_token_request": {
                "status_code": 200,
                "json": {
                    "access_token": "new-access-tok",
                    "refresh_token": "new-refresh-tok",
                },
                "content": "",
            }
        }
    }
    with patch(
        "orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(refresh_result)
    ):
        new_access, new_refresh = await node.refresh_access_token({}, None)
    assert new_access == "new-access-tok"
    assert new_refresh == "new-refresh-tok"


@pytest.mark.asyncio
async def test_refresh_access_token_no_new_refresh_token(
    node: LinkedInPostNode,
) -> None:
    node.linkedin_client_id = "client-id"
    node.linkedin_client_secret = "client-secret"

    refresh_result = {
        "node_results": {
            "refresh_token_request": {
                "status_code": 200,
                "json": {"access_token": "new-access-tok"},
                "content": "",
            }
        }
    }
    with patch(
        "orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(refresh_result)
    ):
        new_access, new_refresh = await node.refresh_access_token({}, None)
    assert new_access == "new-access-tok"
    assert new_refresh is None


@pytest.mark.asyncio
async def test_refresh_access_token_missing_client_credentials_raises(
    node: LinkedInPostNode,
) -> None:
    node.linkedin_client_id = ""
    node.linkedin_client_secret = ""
    with pytest.raises(ValueError, match="linkedin_client_id"):
        await node.refresh_access_token({}, None)


@pytest.mark.asyncio
async def test_refresh_access_token_bad_status_raises(node: LinkedInPostNode) -> None:
    node.linkedin_client_id = "client-id"
    node.linkedin_client_secret = "client-secret"

    refresh_result = {
        "node_results": {
            "refresh_token_request": {
                "status_code": 400,
                "json": None,
                "content": "invalid_grant",
            }
        }
    }
    with patch(
        "orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(refresh_result)
    ):
        with pytest.raises(ValueError, match="Token refresh failed"):
            await node.refresh_access_token({}, None)


@pytest.mark.asyncio
async def test_refresh_access_token_non_json_response_raises(
    node: LinkedInPostNode,
) -> None:
    node.linkedin_client_id = "client-id"
    node.linkedin_client_secret = "client-secret"

    refresh_result = {
        "node_results": {
            "refresh_token_request": {
                "status_code": 200,
                "json": None,
                "content": "",
            }
        }
    }
    with patch(
        "orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(refresh_result)
    ):
        with pytest.raises(ValueError, match="non-JSON response"):
            await node.refresh_access_token({}, None)


@pytest.mark.asyncio
async def test_refresh_access_token_missing_access_token_raises(
    node: LinkedInPostNode,
) -> None:
    node.linkedin_client_id = "client-id"
    node.linkedin_client_secret = "client-secret"

    refresh_result = {
        "node_results": {
            "refresh_token_request": {
                "status_code": 200,
                "json": {"refresh_token": "some-refresh"},
                "content": "",
            }
        }
    }
    with patch(
        "orcheo.nodes.linkedin.HttpRequestNode", _mock_http_node(refresh_result)
    ):
        with pytest.raises(ValueError, match="missing access_token"):
            await node.refresh_access_token({}, None)


# ---------------------------------------------------------------------------
# _update_vault_tokens — active resolver paths
# ---------------------------------------------------------------------------


def test_update_vault_tokens_resolver_persists_access_token(
    node: LinkedInPostNode,
) -> None:
    """With an active resolver, both tokens are persisted successfully."""
    resolver = MagicMock()
    resolver.persist_refreshed_tokens.return_value = True

    with patch(
        "orcheo.nodes.linkedin.get_active_credential_resolver", return_value=resolver
    ):
        node._update_vault_tokens("new-access", "new-refresh")

    assert resolver.persist_refreshed_tokens.call_count == 2
    first_call_kwargs = resolver.persist_refreshed_tokens.call_args_list[0].kwargs
    assert first_call_kwargs["new_access_token"] == "new-access"
    second_call_kwargs = resolver.persist_refreshed_tokens.call_args_list[1].kwargs
    assert second_call_kwargs["new_access_token"] == "new-refresh"


def test_update_vault_tokens_resolver_not_updated_logs_warning(
    node: LinkedInPostNode,
) -> None:
    """When persist_refreshed_tokens returns False, a warning is logged."""
    resolver = MagicMock()
    resolver.persist_refreshed_tokens.return_value = False

    with patch(
        "orcheo.nodes.linkedin.get_active_credential_resolver", return_value=resolver
    ):
        with patch("orcheo.nodes.linkedin.logger") as mock_logger:
            node._update_vault_tokens("new-access", None)

    mock_logger.warning.assert_called()
    warning_msg = mock_logger.warning.call_args_list[0].args[0]
    assert "not found in vault" in warning_msg


def test_update_vault_tokens_resolver_persist_raises_logs_warning(
    node: LinkedInPostNode,
) -> None:
    """When persist_refreshed_tokens raises, a warning is logged and no exception propagates."""  # noqa: E501
    resolver = MagicMock()
    resolver.persist_refreshed_tokens.side_effect = Exception("vault error")

    with patch(
        "orcheo.nodes.linkedin.get_active_credential_resolver", return_value=resolver
    ):
        with patch("orcheo.nodes.linkedin.logger") as mock_logger:
            node._update_vault_tokens("new-access", None)  # must not raise

    mock_logger.warning.assert_called()


def test_update_vault_tokens_refresh_token_persist_raises_logs_warning(
    node: LinkedInPostNode,
) -> None:
    """When persisting the refresh token raises, a warning is logged."""
    resolver = MagicMock()
    resolver.persist_refreshed_tokens.side_effect = [True, Exception("vault error")]

    with patch(
        "orcheo.nodes.linkedin.get_active_credential_resolver", return_value=resolver
    ):
        with patch("orcheo.nodes.linkedin.logger") as mock_logger:
            node._update_vault_tokens("new-access", "new-refresh")  # must not raise

    assert resolver.persist_refreshed_tokens.call_count == 2
    assert mock_logger.warning.called


# ---------------------------------------------------------------------------
# run — token refresh retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_retries_after_401_on_post_creation(node: LinkedInPostNode) -> None:
    """A 401 on post creation triggers token refresh and a successful retry."""
    jwt = _make_jwt({"sub": "person1"})
    node.linkedin_id_token = jwt
    node.linkedin_client_id = "client-id"
    node.linkedin_client_secret = "client-secret"

    call_count = 0

    async def _side_effect(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First post attempt → 401
            return {
                "node_results": {
                    "create_post_request": {
                        "status_code": 401,
                        "headers": {},
                        "content": "Unauthorized",
                    }
                }
            }
        if call_count == 2:
            # Token refresh call
            return {
                "node_results": {
                    "refresh_token_request": {
                        "status_code": 200,
                        "json": {"access_token": "new-tok"},
                        "content": "",
                    }
                }
            }
        # Second post attempt → success
        return {
            "node_results": {
                "create_post_request": {
                    "status_code": 201,
                    "headers": {"x-restli-id": "post-xyz"},
                    "content": "",
                }
            }
        }

    mock_instance = AsyncMock(side_effect=_side_effect)
    mock_cls = MagicMock(return_value=mock_instance)

    config = {"configurable": {"commentary": "Retry post"}}
    with patch("orcheo.nodes.linkedin.HttpRequestNode", mock_cls):
        with patch(
            "orcheo.nodes.linkedin.get_active_credential_resolver", return_value=None
        ):
            result = await node.run({}, config)

    assert result["post_id"] == "post-xyz"
    assert result["status_code"] == 201


@pytest.mark.asyncio
async def test_run_raises_after_401_persists_on_retry(node: LinkedInPostNode) -> None:
    """If the retry also returns 401 after refresh, a descriptive ValueError is raised."""  # noqa: E501
    jwt = _make_jwt({"sub": "person1"})
    node.linkedin_id_token = jwt
    node.linkedin_client_id = "client-id"
    node.linkedin_client_secret = "client-secret"

    call_count = 0

    async def _side_effect(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            return {
                "node_results": {
                    "refresh_token_request": {
                        "status_code": 200,
                        "json": {"access_token": "new-tok"},
                        "content": "",
                    }
                }
            }
        return {
            "node_results": {
                "create_post_request": {
                    "status_code": 401,
                    "headers": {},
                    "content": "Unauthorized",
                }
            }
        }

    mock_instance = AsyncMock(side_effect=_side_effect)
    mock_cls = MagicMock(return_value=mock_instance)

    config = {"configurable": {"commentary": "Retry post"}}
    with patch("orcheo.nodes.linkedin.HttpRequestNode", mock_cls):
        with patch(
            "orcheo.nodes.linkedin.get_active_credential_resolver", return_value=None
        ):
            with pytest.raises(ValueError, match="even after token refresh"):
                await node.run({}, config)
