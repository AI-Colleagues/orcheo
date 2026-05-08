"""Tests for the workspace backfill utility."""

from __future__ import annotations
import argparse
import builtins
import sys
import types
from pathlib import Path
import pytest
from orcheo.tooling import workspace_backfill as backfill


class FakeCursor:
    """Minimal DB cursor used by the backfill tests."""

    def __init__(
        self, *, row: dict[str, object] | None = None, rowcount: int = 1
    ) -> None:
        self._row = row
        self.rowcount = rowcount

    def fetchone(self) -> dict[str, object] | None:
        return self._row


class FakeConnection:
    """Minimal connection object with transaction support."""

    def __init__(self, responses: list[object] | None = None) -> None:
        self.responses = list(responses or [])
        self.queries: list[tuple[str, object | None]] = []
        self.in_transaction = False

    def execute(self, query: str, params: object | None = None) -> FakeCursor:
        self.queries.append((query, params))
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, FakeCursor):
                return response
            if isinstance(response, dict):
                return FakeCursor(
                    row=response.get("row")
                    if isinstance(response.get("row"), dict)
                    else response.get("row"),
                    rowcount=int(response.get("rowcount", 1)),
                )
        return FakeCursor()

    def transaction(self) -> FakeConnection:
        self.in_transaction = True
        return self

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        return None


def test_stack_env_path_uses_override_and_home_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stack_dir = tmp_path / "stack"
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))
    assert backfill._stack_env_path() == stack_dir / ".env"

    monkeypatch.delenv("ORCHEO_STACK_DIR", raising=False)
    monkeypatch.setattr(backfill.Path, "home", lambda: tmp_path / "home")
    assert (
        backfill._stack_env_path() == tmp_path / "home" / ".orcheo" / "stack" / ".env"
    )


def test_load_psycopg_returns_module_and_dict_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    psycopg_module = types.ModuleType("psycopg")
    psycopg_module.__path__ = []  # type: ignore[attr-defined]
    rows_module = types.ModuleType("psycopg.rows")
    dict_row = object()
    rows_module.dict_row = dict_row  # type: ignore[attr-defined]
    psycopg_module.rows = rows_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "psycopg", psycopg_module)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows_module)

    loaded_psycopg, loaded_dict_row = backfill._load_psycopg()

    assert loaded_psycopg is psycopg_module
    assert loaded_dict_row is dict_row


def test_load_psycopg_exits_when_dependency_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(sys.modules, "psycopg", raising=False)
    monkeypatch.delitem(sys.modules, "psycopg.rows", raising=False)

    original_import = builtins.__import__

    def fail_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] | list[str] = (),
        level: int = 0,
    ) -> object:
        if name == "psycopg" or name.startswith("psycopg."):
            raise ModuleNotFoundError(name)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_import)

    with pytest.raises(SystemExit, match="psycopg is required"):
        backfill._load_psycopg()


def test_parse_env_file_and_build_dsn(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "# comment",
                "ORCHEO_POSTGRES_USER=alice smith",
                "",
                "ORCHEO_POSTGRES_PASSWORD=pa:ss",
                "ORCHEO_POSTGRES_DB=orcheo",
                "ORCHEO_POSTGRES_LOCAL_PORT=6543",
                "IGNORED_LINE",
            ]
        ),
        encoding="utf-8",
    )

    parsed = backfill._parse_env_file(env_path)

    assert parsed == {
        "ORCHEO_POSTGRES_USER": "alice smith",
        "ORCHEO_POSTGRES_PASSWORD": "pa:ss",
        "ORCHEO_POSTGRES_DB": "orcheo",
        "ORCHEO_POSTGRES_LOCAL_PORT": "6543",
    }
    assert backfill._build_dsn(parsed) == (
        "postgresql://alice+smith:pa%3Ass@localhost:6543/orcheo"
    )


def test_resolve_dsn_reads_stack_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("ORCHEO_POSTGRES_DB=test-db\n", encoding="utf-8")
    monkeypatch.setattr(backfill, "_stack_env_path", lambda: env_path)

    assert backfill._resolve_dsn() == "postgresql://orcheo:@localhost:5432/test-db"


def test_resolve_dsn_exits_when_stack_env_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(backfill, "_stack_env_path", lambda: tmp_path / "missing.env")

    with pytest.raises(SystemExit, match="stack \\.env not found"):
        backfill._resolve_dsn()


def test_resolve_workspace_and_count_and_assign() -> None:
    conn = FakeConnection(
        [
            {"row": {"id": "workspace-1"}},
            {"row": {"n": 7}},
            {"rowcount": 3},
        ]
    )

    assert backfill._resolve_workspace(conn, "shared") == "workspace-1"
    assert backfill._count_unscoped(conn, "workflows") == 7
    assert backfill._assign(conn, "workflows", "workspace-1") == 3


def test_resolve_workspace_exits_when_slug_is_missing() -> None:
    conn = FakeConnection([{"row": None}])

    with pytest.raises(SystemExit, match="workspace 'missing' not found"):
        backfill._resolve_workspace(conn, "missing")


def test_build_parser_accepts_workspace_slug_and_dry_run() -> None:
    parser = backfill._build_parser()
    args = parser.parse_args(["shared", "--dry-run"])

    assert args.workspace_slug == "shared"
    assert args.dry_run is True


def test_print_unscoped_counts_handles_missing_tables_and_columns(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeErrors:
        class UndefinedTable(Exception):
            pass

        class UndefinedColumn(Exception):
            pass

    psycopg = types.SimpleNamespace(errors=FakeErrors)
    counts = {
        "workflows": 2,
        "workflow_versions": FakeErrors.UndefinedTable(),
        "workflow_runs": FakeErrors.UndefinedColumn(),
        "credentials": 0,
        "credential_templates": 1,
        "governance_alerts": 3,
    }

    def fake_count(conn: object, table: str) -> int:
        result = counts[table]
        if isinstance(result, Exception):
            raise result
        return int(result)

    monkeypatch.setattr(backfill, "_count_unscoped", fake_count)

    total = backfill._print_unscoped_counts(object(), psycopg)
    captured = capsys.readouterr()

    assert total == 6
    assert "table does not exist, skipping" in captured.out
    assert "workspace_id column missing, skipping" in captured.out


def test_apply_assignments_aborts_when_user_declines(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeErrors:
        class UndefinedTable(Exception):
            pass

        class UndefinedColumn(Exception):
            pass

    psycopg = types.SimpleNamespace(errors=FakeErrors)
    conn = FakeConnection()
    monkeypatch.setattr(builtins, "input", lambda prompt: "n")

    with pytest.raises(SystemExit) as excinfo:
        backfill._apply_assignments(conn, psycopg, "workspace-1", "shared", 5)

    captured = capsys.readouterr()
    assert excinfo.value.code == 0
    assert "Aborted." in captured.out


def test_apply_assignments_updates_rows(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeErrors:
        class UndefinedTable(Exception):
            pass

        class UndefinedColumn(Exception):
            pass

    psycopg = types.SimpleNamespace(errors=FakeErrors)
    conn = FakeConnection()
    monkeypatch.setattr(builtins, "input", lambda prompt: "y")

    assignments = {
        "workflows": 2,
        "workflow_versions": FakeErrors.UndefinedTable(),
        "workflow_runs": 0,
        "credentials": 1,
        "credential_templates": FakeErrors.UndefinedColumn(),
        "governance_alerts": 4,
    }

    def fake_assign(conn_obj: object, table: str, workspace_id: str) -> int:
        result = assignments[table]
        if isinstance(result, Exception):
            raise result
        return int(result)

    monkeypatch.setattr(backfill, "_assign", fake_assign)

    backfill._apply_assignments(conn, psycopg, "workspace-1", "shared", 7)
    captured = capsys.readouterr()

    assert conn.in_transaction is True
    assert "row(s) updated" in captured.out
    assert "  workflows" in captured.out
    assert "  governance_alerts" in captured.out


def test_run_handles_zero_total_without_applying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row_factory = object()
    conn = FakeConnection()
    connect_calls: list[tuple[str, object]] = []

    def fake_connect(dsn: str, row_factory: object) -> FakeConnection:
        connect_calls.append((dsn, row_factory))
        return conn

    monkeypatch.setattr(
        backfill,
        "_load_psycopg",
        lambda: (
            types.SimpleNamespace(errors=types.SimpleNamespace(), connect=fake_connect),
            row_factory,
        ),
    )
    monkeypatch.setattr(backfill, "_resolve_dsn", lambda: "postgresql://example")
    monkeypatch.setattr(
        backfill, "_resolve_workspace", lambda conn_obj, slug: "workspace-1"
    )
    monkeypatch.setattr(
        backfill, "_print_unscoped_counts", lambda conn_obj, psycopg_obj: 0
    )

    backfill._run(argparse.Namespace(workspace_slug="shared", dry_run=False))

    assert connect_calls == [("postgresql://example", row_factory)]


def test_run_handles_dry_run_and_apply_paths(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeErrors:
        UndefinedTable = type("UndefinedTable", (Exception,), {})
        UndefinedColumn = type("UndefinedColumn", (Exception,), {})

    conn = FakeConnection()
    row_factory = object()
    connect_calls: list[tuple[str, object]] = []

    def fake_connect(dsn: str, row_factory: object) -> FakeConnection:
        connect_calls.append((dsn, row_factory))
        return conn

    psycopg = types.SimpleNamespace(errors=FakeErrors, connect=fake_connect)
    monkeypatch.setattr(backfill, "_load_psycopg", lambda: (psycopg, row_factory))
    monkeypatch.setattr(backfill, "_resolve_dsn", lambda: "postgresql://example")
    monkeypatch.setattr(
        backfill, "_resolve_workspace", lambda conn_obj, slug: "workspace-1"
    )
    monkeypatch.setattr(
        backfill, "_print_unscoped_counts", lambda conn_obj, psycopg_obj: 4
    )

    applied: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        backfill,
        "_apply_assignments",
        lambda conn_obj,
        psycopg_obj,
        workspace_id,
        workspace_slug,
        total: applied.append((workspace_id, workspace_slug, total)),
    )

    backfill._run(argparse.Namespace(workspace_slug="shared", dry_run=True))
    backfill._run(argparse.Namespace(workspace_slug="shared", dry_run=False))
    captured = capsys.readouterr()

    assert connect_calls == [
        ("postgresql://example", row_factory),
        ("postgresql://example", row_factory),
    ]
    assert applied == [("workspace-1", "shared", 4)]
    assert "Dry-run: would assign 4 row(s)." in captured.out
    assert "Done. All unscoped rows have been assigned." in captured.out


def test_main_parses_arguments_and_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[argparse.Namespace] = []
    monkeypatch.setattr(backfill, "_run", lambda args: seen.append(args))
    monkeypatch.setattr(backfill.sys, "argv", ["orcheo-workspace-backfill", "shared"])

    backfill.main()

    assert seen and seen[0].workspace_slug == "shared"
