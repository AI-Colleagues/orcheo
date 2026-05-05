"""PostgreSQL-backed credential vault implementation."""

from __future__ import annotations
import json
import logging
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from orcheo.models import (
    CredentialCipher,
    CredentialMetadata,
    CredentialTemplate,
    SecretGovernanceAlert,
)
from orcheo.vault.base import BaseCredentialVault
from orcheo.vault.errors import (
    CredentialNotFoundError,
    CredentialTemplateNotFoundError,
    DuplicateCredentialNameError,
    GovernanceAlertNotFoundError,
)


logger = logging.getLogger(__name__)

POSTGRES_VAULT_SCHEMA = """
CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    tenant_id TEXT,
    name TEXT NOT NULL,
    provider TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_credentials_workflow ON credentials(workflow_id);
CREATE INDEX IF NOT EXISTS idx_credentials_tenant ON credentials(tenant_id);
CREATE INDEX IF NOT EXISTS idx_credentials_tenant_name_lower
    ON credentials(tenant_id, lower(name));

CREATE TABLE IF NOT EXISTS credential_templates (
    id TEXT PRIMARY KEY,
    scope_hint TEXT NOT NULL,
    tenant_id TEXT,
    name TEXT NOT NULL,
    provider TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_templates_scope ON credential_templates(scope_hint);
CREATE INDEX IF NOT EXISTS idx_templates_tenant ON credential_templates(tenant_id);

CREATE TABLE IF NOT EXISTS governance_alerts (
    id TEXT PRIMARY KEY,
    scope_hint TEXT NOT NULL,
    tenant_id TEXT,
    acknowledged BOOLEAN NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_scope ON governance_alerts(scope_hint);
CREATE INDEX IF NOT EXISTS idx_alerts_tenant ON governance_alerts(tenant_id);
"""


class PostgresCredentialVault(BaseCredentialVault):
    """PostgreSQL-backed credential vault."""

    def __init__(
        self,
        dsn: str,
        *,
        cipher: CredentialCipher | None = None,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
    ) -> None:
        """Initialize the PostgreSQL-backed vault."""
        super().__init__(cipher=cipher)
        self._dsn = dsn
        self._pool = ConnectionPool(
            dsn,
            min_size=pool_min_size,
            max_size=pool_max_size,
            open=True,
            kwargs={"row_factory": dict_row, "autocommit": True},
        )
        self._initialized = False
        self._init_lock = threading.Lock()

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            with self._pool.connection() as conn:
                for stmt in POSTGRES_VAULT_SCHEMA.strip().split(";"):
                    if stmt.strip():
                        conn.execute(stmt)
                conn.execute(
                    "ALTER TABLE credentials ADD COLUMN IF NOT EXISTS tenant_id TEXT"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_credentials_tenant "
                    "ON credentials(tenant_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_credentials_tenant_name_lower "
                    "ON credentials(tenant_id, lower(name))"
                )
                conn.execute(
                    "ALTER TABLE credential_templates ADD COLUMN "
                    "IF NOT EXISTS tenant_id TEXT"
                )
                conn.execute(
                    "ALTER TABLE governance_alerts ADD COLUMN "
                    "IF NOT EXISTS tenant_id TEXT"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_templates_tenant "
                    "ON credential_templates(tenant_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_alerts_tenant "
                    "ON governance_alerts(tenant_id)"
                )
            self._initialized = True

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        self._ensure_initialized()
        with self._pool.connection() as conn:
            yield conn

    # Credential methods
    def _persist_metadata(self, metadata: CredentialMetadata) -> None:
        payload = metadata.model_dump_json()
        with self._connection() as conn:
            # Check for name duplicates
            cursor = conn.execute(
                """
                SELECT id
                  FROM credentials
                 WHERE lower(name) = lower(%s)
                   AND (tenant_id IS NOT DISTINCT FROM %s)
                """,
                (metadata.name, metadata.tenant_id),
            )
            rows = cursor.fetchall()
            duplicates = [row["id"] for row in rows if row["id"] != str(metadata.id)]
            if duplicates:
                msg = f"Credential name '{metadata.name}' is already in use."
                raise DuplicateCredentialNameError(msg)

            conn.execute(
                """
                INSERT INTO credentials (
                    id,
                    workflow_id,
                    tenant_id,
                    name,
                    provider,
                    created_at,
                    updated_at,
                    payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    workflow_id = EXCLUDED.workflow_id,
                    tenant_id = EXCLUDED.tenant_id,
                    name = EXCLUDED.name,
                    provider = EXCLUDED.provider,
                    created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at,
                    payload = EXCLUDED.payload
                """,
                (
                    str(metadata.id),
                    metadata.scope.scope_hint(),
                    metadata.tenant_id,
                    metadata.name,
                    metadata.provider,
                    metadata.created_at,
                    metadata.updated_at,
                    payload,
                ),
            )

    def _load_metadata(self, credential_id: UUID) -> CredentialMetadata:
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT payload FROM credentials WHERE id = %s",
                (str(credential_id),),
            )
            row = cursor.fetchone()
        if row is None:
            raise CredentialNotFoundError("Credential was not found.")
        p = row["payload"]
        if not isinstance(p, str):
            p = json.dumps(p)
        return CredentialMetadata.model_validate_json(p)

    def _iter_metadata(
        self, *, tenant_id: str | None = None
    ) -> Iterable[CredentialMetadata]:
        with self._connection() as conn:
            if tenant_id is None:
                cursor = conn.execute(
                    "SELECT payload FROM credentials ORDER BY created_at ASC"
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT payload
                      FROM credentials
                     WHERE tenant_id IS NULL OR tenant_id = %s
                  ORDER BY created_at ASC
                    """,
                    (tenant_id,),
                )
            rows = cursor.fetchall()
        for row in rows:
            p = row["payload"]
            if not isinstance(p, str):
                p = json.dumps(p)
            yield CredentialMetadata.model_validate_json(p)

    def _remove_credential(self, credential_id: UUID) -> None:
        with self._connection() as conn:
            result = conn.execute(
                "DELETE FROM credentials WHERE id = %s",
                (str(credential_id),),
            )
            if result.rowcount == 0:
                raise CredentialNotFoundError("Credential was not found.")

    # Template methods
    def _persist_template(self, template: CredentialTemplate) -> None:
        payload = template.model_dump_json()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO credential_templates (
                    id,
                    scope_hint,
                    tenant_id,
                    name,
                    provider,
                    created_at,
                    updated_at,
                    payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    scope_hint = EXCLUDED.scope_hint,
                    tenant_id = EXCLUDED.tenant_id,
                    name = EXCLUDED.name,
                    provider = EXCLUDED.provider,
                    created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at,
                    payload = EXCLUDED.payload
                """,
                (
                    str(template.id),
                    template.scope.scope_hint(),
                    template.tenant_id,
                    template.name,
                    template.provider,
                    template.created_at,
                    template.updated_at,
                    payload,
                ),
            )

    def _load_template(self, template_id: UUID) -> CredentialTemplate:
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT payload FROM credential_templates WHERE id = %s",
                (str(template_id),),
            )
            row = cursor.fetchone()
        if row is None:
            raise CredentialTemplateNotFoundError("Credential template was not found.")
        p = row["payload"]
        if not isinstance(p, str):
            p = json.dumps(p)
        return CredentialTemplate.model_validate_json(p)

    def _iter_templates(
        self, *, tenant_id: str | None = None
    ) -> Iterable[CredentialTemplate]:
        with self._connection() as conn:
            if tenant_id is None:
                cursor = conn.execute(
                    "SELECT payload FROM credential_templates ORDER BY created_at ASC"
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT payload
                      FROM credential_templates
                     WHERE tenant_id IS NULL OR tenant_id = %s
                  ORDER BY created_at ASC
                    """,
                    (tenant_id,),
                )
            rows = cursor.fetchall()
        for row in rows:
            p = row["payload"]
            if not isinstance(p, str):
                p = json.dumps(p)
            yield CredentialTemplate.model_validate_json(p)

    def _remove_template(self, template_id: UUID) -> None:
        with self._connection() as conn:
            result = conn.execute(
                "DELETE FROM credential_templates WHERE id = %s",
                (str(template_id),),
            )
            if result.rowcount == 0:
                raise CredentialTemplateNotFoundError(
                    "Credential template was not found."
                )

    # Alert methods
    def _persist_alert(self, alert: SecretGovernanceAlert) -> None:
        payload = alert.model_dump_json()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO governance_alerts (
                    id,
                    scope_hint,
                    tenant_id,
                    acknowledged,
                    created_at,
                    updated_at,
                    payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    scope_hint = EXCLUDED.scope_hint,
                    tenant_id = EXCLUDED.tenant_id,
                    acknowledged = EXCLUDED.acknowledged,
                    created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at,
                    payload = EXCLUDED.payload
                """,
                (
                    str(alert.id),
                    alert.scope.scope_hint(),
                    alert.tenant_id,
                    alert.is_acknowledged,
                    alert.created_at,
                    alert.updated_at,
                    payload,
                ),
            )

    def _load_alert(self, alert_id: UUID) -> SecretGovernanceAlert:
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT payload FROM governance_alerts WHERE id = %s",
                (str(alert_id),),
            )
            row = cursor.fetchone()
        if row is None:
            raise GovernanceAlertNotFoundError("Governance alert was not found.")
        p = row["payload"]
        if not isinstance(p, str):
            p = json.dumps(p)
        return SecretGovernanceAlert.model_validate_json(p)

    def _iter_alerts(
        self, *, tenant_id: str | None = None
    ) -> Iterable[SecretGovernanceAlert]:
        with self._connection() as conn:
            if tenant_id is None:
                cursor = conn.execute(
                    "SELECT payload FROM governance_alerts ORDER BY created_at ASC"
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT payload
                      FROM governance_alerts
                     WHERE tenant_id IS NULL OR tenant_id = %s
                  ORDER BY created_at ASC
                    """,
                    (tenant_id,),
                )
            rows = cursor.fetchall()
        for row in rows:
            p = row["payload"]
            if not isinstance(p, str):
                p = json.dumps(p)
            yield SecretGovernanceAlert.model_validate_json(p)

    def _remove_alert(self, alert_id: UUID) -> None:
        with self._connection() as conn:
            conn.execute(
                "DELETE FROM governance_alerts WHERE id = %s", (str(alert_id),)
            )

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._pool.close()


__all__ = ["PostgresCredentialVault"]
