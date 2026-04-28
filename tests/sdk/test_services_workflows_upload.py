"""Workflow upload service tests."""

from __future__ import annotations
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
import pytest


ROOT = Path(__file__).resolve().parents[2]
UPLOAD_PATH = (
    ROOT
    / "packages"
    / "sdk"
    / "src"
    / "orcheo_sdk"
    / "services"
    / "workflows"
    / "upload.py"
)
UPLOAD_SPEC = importlib.util.spec_from_file_location(
    "orcheo_sdk.services.workflows.upload",
    UPLOAD_PATH,
)
assert UPLOAD_SPEC is not None and UPLOAD_SPEC.loader is not None
upload = importlib.util.module_from_spec(UPLOAD_SPEC)
sys.modules[UPLOAD_SPEC.name] = upload
UPLOAD_SPEC.loader.exec_module(upload)


class _RecordingConsole:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, message: str) -> None:
        self.messages.append(message)


class _FailingConsole:
    def print(self, message: str) -> None:
        del message
        raise ValueError("boom")


class _DummyFrontmatter:
    def __init__(
        self,
        *,
        is_empty: bool,
        name: str | None = None,
        workflow_id: str | None = None,
        config_path: str | None = None,
        entrypoint: str | None = None,
    ) -> None:
        self.is_empty = is_empty
        self.name = name
        self.workflow_id = workflow_id
        self.config_path = config_path
        self.entrypoint = entrypoint


def _frontmatter(**overrides: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "is_empty": False,
        "name": None,
        "workflow_id": None,
        "config_path": None,
        "entrypoint": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _install_workflow_package(
    monkeypatch: pytest.MonkeyPatch,
    *,
    load_workflow_from_python,
    normalize_workflow_name,
    upload_langgraph_script,
    validate_local_path,
    load_workflow_frontmatter,
) -> None:
    workflow_module = ModuleType("orcheo_sdk.cli.workflow")
    workflow_module.__path__ = []  # type: ignore[attr-defined]
    workflow_module._load_workflow_from_python = load_workflow_from_python
    workflow_module._normalize_workflow_name = normalize_workflow_name
    workflow_module._upload_langgraph_script = upload_langgraph_script
    workflow_module._validate_local_path = validate_local_path

    frontmatter_module = ModuleType("orcheo_sdk.cli.workflow.frontmatter")
    frontmatter_module.load_workflow_frontmatter = load_workflow_frontmatter

    workflow_module.frontmatter = frontmatter_module
    monkeypatch.setitem(sys.modules, "orcheo_sdk.cli.workflow", workflow_module)
    monkeypatch.setitem(
        sys.modules, "orcheo_sdk.cli.workflow.frontmatter", frontmatter_module
    )


def test_apply_frontmatter_defaults_returns_inputs_when_empty() -> None:
    frontmatter = _DummyFrontmatter(is_empty=True)
    console = _RecordingConsole()

    result = upload._apply_frontmatter_defaults(
        path_obj=Path("/tmp/workflow.py"),
        frontmatter=frontmatter,
        workflow_id="wf-1",
        workflow_name="My Workflow",
        entrypoint="build_graph",
        runnable_config={"tags": ["x"]},
        console=console,
    )

    assert result == (
        "wf-1",
        "My Workflow",
        "build_graph",
        {"tags": ["x"]},
    )
    assert console.messages == []


def test_apply_frontmatter_defaults_fills_missing_values_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frontmatter = _DummyFrontmatter(
        is_empty=False,
        name="Frontmatter Workflow",
        workflow_id="wf-frontmatter",
        config_path="./workflow.config.json",
        entrypoint="build_graph",
    )
    console = _RecordingConsole()
    resolved_config: dict[str, object] = {"max_concurrency": 4}
    called_with: list[tuple[Path, str]] = []

    def fake_resolve_frontmatter_config(
        path_obj: Path, config_path: str
    ) -> dict[str, object]:
        called_with.append((path_obj, config_path))
        return resolved_config

    _install_workflow_package(
        monkeypatch,
        load_workflow_from_python=lambda _path: {},
        normalize_workflow_name=lambda name: name,
        upload_langgraph_script=lambda *args, **kwargs: {},
        validate_local_path=lambda file_path, description: Path(file_path),
        load_workflow_frontmatter=lambda _path: _DummyFrontmatter(is_empty=True),
    )
    sys.modules["orcheo_sdk.cli.workflow.frontmatter"].resolve_frontmatter_config = (  # type: ignore[attr-defined]
        fake_resolve_frontmatter_config
    )

    result = upload._apply_frontmatter_defaults(
        path_obj=Path("/tmp/workflow.py"),
        frontmatter=frontmatter,
        workflow_id=None,
        workflow_name=None,
        entrypoint=None,
        runnable_config=None,
        console=console,
    )

    assert result == (
        "wf-frontmatter",
        "Frontmatter Workflow",
        "build_graph",
        resolved_config,
    )
    assert called_with == [(Path("/tmp/workflow.py"), "./workflow.config.json")]
    assert console.messages == [
        (
            "[dim]Loaded workflow frontmatter: id, name, entrypoint, "
            "config (./workflow.config.json).[/dim]"
        )
    ]


def test_apply_frontmatter_defaults_keeps_cli_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frontmatter = _DummyFrontmatter(
        is_empty=False,
        name="Frontmatter Workflow",
        workflow_id="wf-frontmatter",
        config_path="./workflow.config.json",
        entrypoint="build_graph",
    )
    console = _RecordingConsole()
    sentinel_config: dict[str, object] = {"tags": ["cli"]}

    _install_workflow_package(
        monkeypatch,
        load_workflow_from_python=lambda _path: {},
        normalize_workflow_name=lambda name: name,
        upload_langgraph_script=lambda *args, **kwargs: {},
        validate_local_path=lambda file_path, description: Path(file_path),
        load_workflow_frontmatter=lambda _path: _DummyFrontmatter(is_empty=True),
    )
    sys.modules["orcheo_sdk.cli.workflow.frontmatter"].resolve_frontmatter_config = (  # type: ignore[attr-defined]
        lambda _path_obj, _config_path: pytest.fail(
            "frontmatter config should not be loaded when CLI config is provided"
        )
    )

    result = upload._apply_frontmatter_defaults(
        path_obj=Path("/tmp/workflow.py"),
        frontmatter=frontmatter,
        workflow_id="wf-cli",
        workflow_name="CLI Workflow",
        entrypoint="cli_entrypoint",
        runnable_config=sentinel_config,
        console=console,
    )

    assert result == (
        "wf-cli",
        "CLI Workflow",
        "cli_entrypoint",
        sentinel_config,
    )
    assert console.messages == []


def test_apply_frontmatter_defaults_swallows_console_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frontmatter = _DummyFrontmatter(
        is_empty=False,
        name="Frontmatter Workflow",
        workflow_id="wf-frontmatter",
        config_path="./workflow.config.json",
        entrypoint="build_graph",
    )
    resolved_config: dict[str, object] = {"max_concurrency": 4}

    _install_workflow_package(
        monkeypatch,
        load_workflow_from_python=lambda _path: {},
        normalize_workflow_name=lambda name: name,
        upload_langgraph_script=lambda *args, **kwargs: {},
        validate_local_path=lambda file_path, description: Path(file_path),
        load_workflow_frontmatter=lambda _path: _DummyFrontmatter(is_empty=True),
    )
    sys.modules["orcheo_sdk.cli.workflow.frontmatter"].resolve_frontmatter_config = (  # type: ignore[attr-defined]
        lambda _path_obj, _config_path: resolved_config
    )

    result = upload._apply_frontmatter_defaults(
        path_obj=Path("/tmp/workflow.py"),
        frontmatter=frontmatter,
        workflow_id=None,
        workflow_name=None,
        entrypoint=None,
        runnable_config=None,
        console=_FailingConsole(),
    )

    assert result == (
        "wf-frontmatter",
        "Frontmatter Workflow",
        "build_graph",
        resolved_config,
    )


def test_load_workflow_config_from_path_rejects_non_python() -> None:
    with pytest.raises(upload.CLIError, match="Unsupported file type"):
        upload._load_workflow_config_from_path(
            Path("/tmp/workflow.txt"),
            load_python=lambda _path: {},
        )


def test_load_workflow_config_from_path_wraps_generic_errors() -> None:
    def raise_error(_path: Path) -> dict[str, object]:
        raise RuntimeError("boom")

    with pytest.raises(upload.CLIError, match="Failed to load workflow definition"):
        upload._load_workflow_config_from_path(
            Path("/tmp/workflow.py"),
            load_python=raise_error,
        )


def test_load_workflow_config_from_path_propagates_cli_errors() -> None:
    def raise_cli_error(_path: Path) -> dict[str, object]:
        raise upload.CLIError("nope")

    with pytest.raises(upload.CLIError, match="nope"):
        upload._load_workflow_config_from_path(
            Path("/tmp/workflow.py"),
            load_python=raise_cli_error,
        )


def test_load_workflow_config_from_path_returns_loaded_payload() -> None:
    payload = {"_type": "langgraph_script"}

    result = upload._load_workflow_config_from_path(
        Path("/tmp/workflow.py"),
        load_python=lambda _path: payload,
    )

    assert result is payload


def test_upload_langgraph_workflow_wraps_generic_errors() -> None:
    def raise_error(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        raise RuntimeError("boom")

    with pytest.raises(upload.CLIError, match="Failed to upload LangGraph"):
        upload._upload_langgraph_workflow(
            state=object(),
            workflow_config={},
            workflow_id=None,
            path_obj=Path("/tmp/workflow.py"),
            requested_name=None,
            uploader=raise_error,
        )


def test_upload_langgraph_workflow_propagates_cli_errors() -> None:
    def raise_cli_error(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        raise upload.CLIError("nope")

    with pytest.raises(upload.CLIError, match="nope"):
        upload._upload_langgraph_workflow(
            state=object(),
            workflow_config={},
            workflow_id=None,
            path_obj=Path("/tmp/workflow.py"),
            requested_name=None,
            uploader=raise_cli_error,
        )


def test_upload_langgraph_workflow_returns_uploader_result() -> None:
    result_payload = {"id": "wf-1"}

    result = upload._upload_langgraph_workflow(
        state={"client": object()},
        workflow_config={"script": "print('hello')"},
        workflow_id="wf-1",
        path_obj=Path("/tmp/workflow.py"),
        requested_name="workflow",
        uploader=lambda *args, **kwargs: result_payload,
    )

    assert result is result_payload


def test_upload_workflow_data_success_injects_entrypoint_and_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = Path("/tmp/workflow.py")
    validated_paths: list[tuple[str, str]] = []
    uploaded_payloads: list[dict[str, object]] = []

    def validate_local_path(file_path: str | Path, description: str) -> Path:
        validated_paths.append((str(file_path), description))
        return workflow_path

    def load_workflow_frontmatter(_path: Path) -> _DummyFrontmatter:
        return _DummyFrontmatter(is_empty=True)

    def load_workflow_from_python(_path: Path) -> dict[str, object]:
        return {
            "_type": "langgraph_script",
            "script": "print('hello')",
        }

    def upload_langgraph_script(
        state: object,
        workflow_config: dict[str, object],
        workflow_id: str | None,
        path_obj: Path,
        requested_name: str | None,
    ) -> dict[str, object]:
        uploaded_payloads.append(
            {
                "state": state,
                "workflow_config": workflow_config,
                "workflow_id": workflow_id,
                "path_obj": path_obj,
                "requested_name": requested_name,
            }
        )
        return {
            "id": workflow_id,
            "name": requested_name,
            "workflow_config": workflow_config,
        }

    _install_workflow_package(
        monkeypatch=monkeypatch,
        load_workflow_from_python=load_workflow_from_python,
        normalize_workflow_name=lambda name: name.strip() if name else None,
        upload_langgraph_script=upload_langgraph_script,
        validate_local_path=validate_local_path,
        load_workflow_frontmatter=load_workflow_frontmatter,
    )
    monkeypatch.setattr(
        upload,
        "_apply_frontmatter_defaults",
        lambda **kwargs: (
            "wf-1",
            "My Workflow",
            "build_graph",
            {"tags": ["x"]},
        ),
    )

    result = upload.upload_workflow_data(
        client=object(),
        file_path=workflow_path,
        workflow_id="wf-1",
        workflow_name="My Workflow",
        entrypoint="build_graph",
        runnable_config={"tags": ["x"]},
        console=_RecordingConsole(),
    )

    assert validated_paths == [(str(workflow_path), "workflow")]
    assert len(uploaded_payloads) == 1
    assert uploaded_payloads[0]["workflow_config"] == {
        "_type": "langgraph_script",
        "script": "print('hello')",
        "entrypoint": "build_graph",
        "runnable_config": {"tags": ["x"]},
    }
    assert result == {
        "id": "wf-1",
        "name": "My Workflow",
        "workflow_config": uploaded_payloads[0]["workflow_config"],
    }


def test_upload_workflow_data_rejects_non_langgraph_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = Path("/tmp/workflow.py")

    _install_workflow_package(
        monkeypatch,
        load_workflow_from_python=lambda _path: {"_type": "json"},
        normalize_workflow_name=lambda name: name,
        upload_langgraph_script=lambda *args, **kwargs: {},
        validate_local_path=lambda file_path, description: workflow_path,
        load_workflow_frontmatter=lambda _path: _DummyFrontmatter(is_empty=True),
    )
    monkeypatch.setattr(
        upload,
        "_apply_frontmatter_defaults",
        lambda **kwargs: (
            None,
            None,
            None,
            None,
        ),
    )

    with pytest.raises(upload.CLIError, match="Only LangGraph Python scripts"):
        upload.upload_workflow_data(
            client=object(),
            file_path=workflow_path,
        )


def test_upload_workflow_data_leaves_optional_fields_unset_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = Path("/tmp/workflow.py")
    uploaded_payloads: list[dict[str, object]] = []

    def load_workflow_from_python(_path: Path) -> dict[str, object]:
        return {
            "_type": "langgraph_script",
            "script": "print('hello')",
        }

    def upload_langgraph_script(
        state: object,
        workflow_config: dict[str, object],
        workflow_id: str | None,
        path_obj: Path,
        requested_name: str | None,
    ) -> dict[str, object]:
        uploaded_payloads.append(
            {
                "state": state,
                "workflow_config": workflow_config,
                "workflow_id": workflow_id,
                "path_obj": path_obj,
                "requested_name": requested_name,
            }
        )
        return {"id": workflow_id, "workflow_config": workflow_config}

    _install_workflow_package(
        monkeypatch,
        load_workflow_from_python=load_workflow_from_python,
        normalize_workflow_name=lambda name: name,
        upload_langgraph_script=upload_langgraph_script,
        validate_local_path=lambda file_path, description: workflow_path,
        load_workflow_frontmatter=lambda _path: _DummyFrontmatter(is_empty=True),
    )
    monkeypatch.setattr(
        upload,
        "_apply_frontmatter_defaults",
        lambda **kwargs: (None, None, None, None),
    )

    result = upload.upload_workflow_data(
        client=object(),
        file_path=workflow_path,
        console=_RecordingConsole(),
    )

    assert len(uploaded_payloads) == 1
    assert uploaded_payloads[0]["workflow_config"] == {
        "_type": "langgraph_script",
        "script": "print('hello')",
    }
    assert result == {
        "id": None,
        "workflow_config": uploaded_payloads[0]["workflow_config"],
    }
