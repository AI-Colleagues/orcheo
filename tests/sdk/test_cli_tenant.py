"""Tenant CLI command tests."""

from __future__ import annotations
import httpx
import respx
from typer.testing import CliRunner
from orcheo_sdk.cli.main import app


def test_tenant_delete_with_force(runner: CliRunner, env: dict[str, str]) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.delete("http://api.test/api/admin/tenants/tenant-1").mock(
            return_value=httpx.Response(204)
        )
        result = runner.invoke(
            app,
            ["tenant", "delete", "tenant-1", "--force"],
            env=env,
        )
    assert result.exit_code == 0
    assert "Tenant tenant-1 deleted" in result.stdout


def test_tenant_purge_deleted_posts_retention_days(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(
            "http://api.test/api/admin/tenants/purge-deleted?retention_days=14"
        ).mock(return_value=httpx.Response(204))
        result = runner.invoke(
            app,
            ["tenant", "purge-deleted", "--retention-days", "14"],
            env=machine_env,
        )
    assert result.exit_code == 0
    assert '"status": "success"' in result.stdout


def test_tenant_audit_log_outputs_json(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    payload = {
        "audit_events": [
            {
                "id": "event-1",
                "tenant_id": "tenant-1",
                "action": "tenant.created",
                "actor": "alice",
                "subject": "alice",
                "resource_type": "tenant",
                "resource_id": "tenant-1",
                "details": {"source": "cli"},
                "created_at": "2026-05-05T12:00:00+00:00",
            }
        ]
    }
    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/admin/tenants/tenant-1/audit-events").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = runner.invoke(
            app,
            ["tenant", "audit-log", "tenant-1"],
            env=machine_env,
        )
    assert result.exit_code == 0
    assert '"tenant.created"' in result.stdout
    assert '"source": "cli"' in result.stdout
