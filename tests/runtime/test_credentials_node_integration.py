"""Integration tests between nodes and the credential resolver."""

from __future__ import annotations
import pytest
from orcheo.graph.state import State
from orcheo.nodes.logic import SetVariableNode
from orcheo.runtime.credentials import (
    CredentialResolver,
    CredentialResolverUnavailableError,
    credential_ref,
    credential_resolution,
)
from tests.runtime.credential_test_helpers import create_vault_with_secret


def test_node_decode_variables_injects_secret() -> None:
    vault = create_vault_with_secret(secret="token")
    resolver = CredentialResolver(vault)
    node = SetVariableNode(
        name="store_secret",
        variables={"token": "[[telegram_token]]"},
    )
    state = State({"node_results": {}, "messages": [], "inputs": {}})
    with credential_resolution(resolver):
        node.decode_variables(state)
    assert node.variables["token"] == "token"


def test_node_decode_variables_without_resolver_errors() -> None:
    node = SetVariableNode(
        name="store_secret",
        variables={"token": "[[telegram_token]]"},
    )
    state = State({"node_results": {}, "messages": [], "inputs": {}})
    with pytest.raises(CredentialResolverUnavailableError):
        node.decode_variables(state)


def test_node_accepts_explicit_credential_reference() -> None:
    vault = create_vault_with_secret(secret="token")
    resolver = CredentialResolver(vault)
    node = SetVariableNode(
        name="store_secret",
        variables={"token": credential_ref("telegram_token")},
    )
    state = State({"node_results": {}, "messages": [], "inputs": {}})
    with credential_resolution(resolver):
        node.decode_variables(state)
    assert node.variables["token"] == "token"
