"""Guard schemas that stores execute one `;`-separated statement at a time."""

from __future__ import annotations
import pytest
from orcheo.identity.postgres_schema import POSTGRES_IDENTITY_SCHEMA
from orcheo.vault.postgres import POSTGRES_VAULT_SCHEMA
from orcheo_backend.app.agentensor.checkpoint_store import (
    POSTGRES_CHECKPOINT_MIGRATION,
)
from orcheo_backend.app.chatkit_store_postgres.schema import POSTGRES_CHATKIT_SCHEMA
from orcheo_backend.app.history.postgres_store import POSTGRES_HISTORY_SCHEMA
from orcheo_backend.app.repository_postgres._base import POSTGRES_SCHEMA
from orcheo_backend.app.service_token_repository.postgres_repository import (
    POSTGRES_SERVICE_TOKEN_SCHEMA,
)


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(POSTGRES_IDENTITY_SCHEMA, id="identity"),
        pytest.param(POSTGRES_VAULT_SCHEMA, id="vault"),
        pytest.param(POSTGRES_CHECKPOINT_MIGRATION, id="agentensor"),
        pytest.param(POSTGRES_CHATKIT_SCHEMA, id="chatkit"),
        pytest.param(POSTGRES_HISTORY_SCHEMA, id="history"),
        pytest.param(POSTGRES_SCHEMA, id="repository"),
        pytest.param(POSTGRES_SERVICE_TOKEN_SCHEMA, id="service-tokens"),
    ],
)
def test_split_schema_comments_contain_no_semicolons(schema: str) -> None:
    """A `;` in a comment would split it and run the rest as SQL."""
    offending = [
        line
        for line in schema.splitlines()
        if line.strip().startswith("--") and ";" in line
    ]
    assert offending == []
