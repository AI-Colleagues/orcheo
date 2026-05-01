"""Unit tests for workflow ingest helpers."""

from __future__ import annotations
import importlib.util
import sys
import types
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[2]
SDK_SRC = ROOT / "packages" / "sdk" / "src"
SRC = ROOT / "src"
for path in (SRC, SDK_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

INGEST_PATH = (
    ROOT / "packages" / "sdk" / "src" / "orcheo_sdk" / "cli" / "workflow" / "ingest.py"
)
INGEST_SPEC = importlib.util.spec_from_file_location(
    "orcheo_sdk.cli.workflow.ingest",
    INGEST_PATH,
)
assert INGEST_SPEC is not None and INGEST_SPEC.loader is not None

saved_modules = {
    name: sys.modules[name]
    for name in (
        "orcheo",
        "orcheo.models",
        "orcheo.models.workflow_refs",
        "orcheo.plugins",
        "orcheo.plugins.paths",
    )
    if name in sys.modules
}

orcheo_module = types.ModuleType("orcheo")
orcheo_module.__path__ = []  # type: ignore[attr-defined]
models_module = types.ModuleType("orcheo.models")
models_module.__path__ = []  # type: ignore[attr-defined]
workflow_refs_module = types.ModuleType("orcheo.models.workflow_refs")
workflow_refs_module.normalize_workflow_handle = lambda value: value
plugins_module = types.ModuleType("orcheo.plugins")
plugins_module.__path__ = []  # type: ignore[attr-defined]
paths_module = types.ModuleType("orcheo.plugins.paths")
paths_module.build_storage_paths = lambda: types.SimpleNamespace(install_dir=str(ROOT))

sys.modules["orcheo"] = orcheo_module
sys.modules["orcheo.models"] = models_module
sys.modules["orcheo.models.workflow_refs"] = workflow_refs_module
sys.modules["orcheo.plugins"] = plugins_module
sys.modules["orcheo.plugins.paths"] = paths_module

ingest = importlib.util.module_from_spec(INGEST_SPEC)
sys.modules[INGEST_SPEC.name] = ingest
try:
    INGEST_SPEC.loader.exec_module(ingest)
finally:
    for module_name in (
        "orcheo",
        "orcheo.models",
        "orcheo.models.workflow_refs",
        "orcheo.plugins",
        "orcheo.plugins.paths",
    ):
        sys.modules.pop(module_name, None)
    sys.modules.update(saved_modules)


class _RecordingConsole:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, message: str) -> None:
        self.messages.append(message)


class _Client:
    def __init__(
        self,
        *,
        get_result: object | None = None,
        get_exc: Exception | None = None,
        put_result: object | None = None,
        put_exc: Exception | None = None,
        post_result: object | None = None,
        post_exc: Exception | None = None,
    ) -> None:
        self.get_result = get_result
        self.get_exc = get_exc
        self.put_result = put_result
        self.put_exc = put_exc
        self.post_result = post_result
        self.post_exc = post_exc
        self.get_calls: list[tuple[str, object]] = []
        self.put_calls: list[tuple[str, dict[str, object]]] = []
        self.post_calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str) -> object:
        self.get_calls.append((url, self.get_result))
        if self.get_exc is not None:
            raise self.get_exc
        return self.get_result

    def put(self, url: str, *, json_body: dict[str, object]) -> object:
        self.put_calls.append((url, json_body))
        if self.put_exc is not None:
            raise self.put_exc
        return self.put_result

    def post(self, url: str, *, json_body: dict[str, object]) -> object:
        self.post_calls.append((url, json_body))
        if self.post_exc is not None:
            raise self.post_exc
        return self.post_result


class _State:
    def __init__(
        self, client: _Client, console: _RecordingConsole | None = None
    ) -> None:
        self.client = client
        self.console = console or _RecordingConsole()


def test_build_ingest_payload_includes_configurable_schema() -> None:
    """The ingest payload should embed configurable schema metadata when present."""
    payload = ingest._build_ingest_payload(
        script="print('hello')",
        entrypoint="build_graph",
        path=Path("/tmp/workflow.py"),
        workflow_config={
            "configurable_schema": {
                "mode": {"type": "string", "default": "draft"},
            },
            "runnable_config": {"tags": ["beta"]},
        },
    )

    assert payload == {
        "script": "print('hello')",
        "entrypoint": "build_graph",
        "metadata": {
            "source": "cli-upload",
            "filename": "workflow.py",
            "configurable_schema": {
                "mode": {"type": "string", "default": "draft"},
            },
        },
        "notes": "Uploaded from workflow.py via CLI",
        "created_by": "cli",
        "runnable_config": {"tags": ["beta"]},
    }


def test_generate_slug_covers_normalized_fallback_and_empty_value() -> None:
    """Slug generation should handle normalized, fallback, and empty inputs."""
    assert ingest._generate_slug("Hello, World!") == "hello-world"
    assert ingest._generate_slug("###") == "###"
    assert ingest._generate_slug("   ") == "   "


def test_normalize_workflow_name_handles_empty_and_trimmed_values() -> None:
    """Workflow names are trimmed and empty strings are rejected."""
    assert ingest._normalize_workflow_name(None) is None
    assert ingest._normalize_workflow_name("  My Workflow  ") == "My Workflow"
    with pytest.raises(ingest.CLIError, match="cannot be empty"):
        ingest._normalize_workflow_name("   ")


def test_normalize_workflow_handle_delegates_to_normalizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow handles should be slugged and normalized by the helper."""
    monkeypatch.setattr(
        ingest,
        "normalize_workflow_handle",
        lambda value: f"normalized:{value}",
    )

    assert ingest._normalize_workflow_handle(None) is None
    assert ingest._normalize_workflow_handle("  My Handle  ") == "normalized:my-handle"


def test_fetch_workflow_for_upload_handles_success_and_archived_workflows() -> None:
    """Archived workflows should be ignored when create-if-missing is enabled."""
    state = _State(_Client(get_result={"id": "wf-1"}))
    assert ingest._fetch_workflow_for_upload(
        state,
        "wf-1",
        allow_create_if_missing=False,
    ) == {"id": "wf-1"}

    archived_state = _State(_Client(get_result={"is_archived": True}))
    assert (
        ingest._fetch_workflow_for_upload(
            archived_state,
            "wf-2",
            allow_create_if_missing=True,
        )
        is None
    )


def test_fetch_workflow_for_upload_handles_api_and_generic_errors() -> None:
    """API failures should be wrapped in CLIError unless 404s are allowed."""
    missing_state = _State(
        _Client(get_exc=ingest.APICallError("missing", status_code=404))
    )
    assert (
        ingest._fetch_workflow_for_upload(
            missing_state,
            "wf-3",
            allow_create_if_missing=True,
        )
        is None
    )

    failing_state = _State(
        _Client(get_exc=ingest.APICallError("boom", status_code=500))
    )
    with pytest.raises(ingest.CLIError, match="Failed to fetch workflow 'wf-4'"):
        ingest._fetch_workflow_for_upload(
            failing_state,
            "wf-4",
            allow_create_if_missing=False,
        )

    generic_state = _State(_Client(get_exc=RuntimeError("broken")))
    with pytest.raises(ingest.CLIError, match="Failed to fetch workflow 'wf-5'"):
        ingest._fetch_workflow_for_upload(
            generic_state,
            "wf-5",
            allow_create_if_missing=False,
        )


def test_maybe_update_workflow_metadata_noops_when_payload_is_empty() -> None:
    """No-op updates should return the original workflow unchanged."""
    workflow = {"id": "wf-1", "name": "My Workflow"}
    state = _State(_Client())

    result = ingest._maybe_update_workflow_metadata(
        state,
        workflow,
        "wf-1",
        name_override="My Workflow",
        description_override=None,
    )

    assert result is workflow
    assert state.client.put_calls == []


def test_maybe_update_workflow_metadata_updates_and_wraps_errors() -> None:
    """Metadata updates should call the API and merge the response locally."""
    workflow = {"id": "wf-1", "name": "Old", "description": "Old desc"}
    state = _State(_Client(put_result={"ok": True}))

    result = ingest._maybe_update_workflow_metadata(
        state,
        workflow,
        "wf-1",
        name_override="New",
        description_override="New desc",
    )

    assert state.client.put_calls == [
        (
            "/api/workflows/wf-1",
            {"name": "New", "description": "New desc"},
        )
    ]
    assert result == {"id": "wf-1", "name": "New", "description": "New desc"}

    error_state = _State(_Client(put_exc=RuntimeError("boom")))
    with pytest.raises(ingest.CLIError, match="Failed to update workflow 'wf-2'"):
        ingest._maybe_update_workflow_metadata(
            error_state,
            workflow,
            "wf-2",
            name_override="New",
            description_override=None,
        )


def test_create_workflow_for_upload_prints_and_wraps_errors(tmp_path: Path) -> None:
    """Workflow creation should log success and wrap API failures."""
    console = _RecordingConsole()
    state = _State(_Client(post_result={"id": "wf-1"}), console)

    result = ingest._create_workflow_for_upload(
        state,
        workflow_name="My Workflow",
        workflow_slug="my-workflow",
        workflow_handle="my-workflow",
        workflow_description=None,
        path=tmp_path / "workflow.py",
    )

    assert result == {"id": "wf-1"}
    assert state.client.post_calls == [
        (
            "/api/workflows",
            {
                "name": "My Workflow",
                "slug": "my-workflow",
                "description": "LangGraph workflow from workflow.py",
                "tags": ["langgraph", "cli-upload"],
                "actor": "cli",
                "handle": "my-workflow",
            },
        )
    ]
    assert console.messages == ["[green]Created workflow 'wf-1' (My Workflow)[/green]"]

    error_state = _State(_Client(post_exc=RuntimeError("boom")), _RecordingConsole())
    with pytest.raises(ingest.CLIError, match="Failed to create workflow"):
        ingest._create_workflow_for_upload(
            error_state,
            workflow_name="My Workflow",
            workflow_slug="my-workflow",
            workflow_handle=None,
            workflow_description="Desc",
            path=tmp_path / "workflow.py",
        )


def test_plugin_site_packages_uses_install_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The managed plugin path should be derived from build storage paths."""
    monkeypatch.setattr(
        ingest,
        "build_storage_paths",
        lambda: types.SimpleNamespace(install_dir=str(tmp_path / "install")),
    )

    expected = (
        tmp_path
        / "install"
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    assert ingest._plugin_site_packages() == expected


def test_managed_plugin_sys_path_adds_and_removes_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The managed plugin sys.path context should clean up after itself."""
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    monkeypatch.setattr(ingest, "_plugin_site_packages", lambda: site_packages)
    monkeypatch.setattr(
        sys, "path", [entry for entry in sys.path if entry != str(site_packages)]
    )

    with ingest._managed_plugin_sys_path():
        assert str(site_packages) in sys.path

    assert str(site_packages) not in sys.path


def test_upload_langgraph_script_creates_workflow_when_missing(
    tmp_path: Path,
) -> None:
    """A missing workflow should be created before ingesting a version."""
    console = _RecordingConsole()
    state = _State(
        _Client(
            get_exc=ingest.APICallError("missing", status_code=404),
        ),
        console,
    )
    calls: list[tuple[str, object]] = []

    def post(url: str, *, json_body: dict[str, object]) -> object:
        calls.append((url, json_body))
        if url == "/api/workflows":
            return {"id": "wf-new"}
        if url == "/api/workflows/wf-new/versions/ingest":
            return {"id": "v-1", "version": 1}
        raise AssertionError(url)

    state.client.post = post  # type: ignore[method-assign]

    result = ingest._upload_langgraph_script(
        state,
        workflow_config={"script": "print('hello')"},
        workflow_id=None,
        workflow_handle="Simple Handle",
        workflow_description=None,
        path=tmp_path / "workflow.py",
        name_override=None,
    )

    assert result["id"] == "wf-new"
    assert result["latest_version"] == {"id": "v-1", "version": 1}
    assert calls[0][0] == "/api/workflows"
    assert calls[1][0] == "/api/workflows/wf-new/versions/ingest"
    assert (
        console.messages[-1] == "[green]Ingested LangGraph script as version 1[/green]"
    )


def test_upload_langgraph_script_updates_existing_workflow(tmp_path: Path) -> None:
    """Existing workflows should be updated and then ingested."""
    console = _RecordingConsole()

    def get(url: str) -> object:
        assert url == "/api/workflows/wf-1"
        return {"id": "wf-1", "name": "Old", "description": "Old desc"}

    def put(url: str, *, json_body: dict[str, object]) -> object:
        assert url == "/api/workflows/wf-1"
        assert json_body == {"name": "New", "description": "New desc"}
        return {"ok": True}

    def post(url: str, *, json_body: dict[str, object]) -> object:
        assert url == "/api/workflows/wf-1/versions/ingest"
        assert json_body["script"] == "print('hello')"
        return {"id": "v-2", "version": 2, "workflow_id": "wf-1"}

    state = _State(_Client(), console)
    state.client.get = get  # type: ignore[method-assign]
    state.client.put = put  # type: ignore[method-assign]
    state.client.post = post  # type: ignore[method-assign]

    result = ingest._upload_langgraph_script(
        state,
        workflow_config={"script": "print('hello')", "entrypoint": "build_graph"},
        workflow_id="wf-1",
        workflow_handle=None,
        workflow_description="New desc",
        path=tmp_path / "workflow.py",
        name_override="New",
    )

    assert result["id"] == "wf-1"
    assert result["name"] == "New"
    assert result["latest_version"] == {
        "id": "v-2",
        "version": 2,
        "workflow_id": "wf-1",
    }


def test_upload_langgraph_script_wraps_ingest_errors(tmp_path: Path) -> None:
    """Ingest API failures should raise CLIError."""
    console = _RecordingConsole()
    state = _State(_Client(), console)
    call_count = {"count": 0}

    def post(url: str, *, json_body: dict[str, object]) -> object:
        call_count["count"] += 1
        if call_count["count"] == 1:
            assert url == "/api/workflows"
            return {"id": "wf-1"}
        raise RuntimeError("boom")

    state.client.post = post  # type: ignore[method-assign]

    with pytest.raises(ingest.CLIError, match="Failed to ingest LangGraph script"):
        ingest._upload_langgraph_script(
            state,
            workflow_config={"script": "print('hello')"},
            workflow_id=None,
            workflow_handle=None,
            workflow_description=None,
            path=tmp_path / "workflow.py",
            name_override=None,
        )


def test_strip_main_block_handles_both_main_block_styles() -> None:
    """Main guards should be stripped for both quote styles."""
    assert (
        ingest._strip_main_block(
            "print('start')\nif __name__ == \"__main__\":\n    run()\nprint('end')"
        )
        == "print('start')"
    )
    assert (
        ingest._strip_main_block(
            "print('start')\nif __name__ == '__main__':\n    run()\nprint('end')"
        )
        == "print('start')"
    )
    assert (
        ingest._strip_main_block("print('start')\nprint('end')")
        == "print('start')\nprint('end')"
    )


def test_load_workflow_from_python_rejects_missing_spec(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A missing import spec should raise a CLIError."""
    monkeypatch.setattr(
        importlib.util,
        "spec_from_file_location",
        lambda name, path: None,
    )

    with pytest.raises(ingest.CLIError, match="Failed to load Python module"):
        ingest._load_workflow_from_python(tmp_path / "workflow.py")


def test_load_workflow_from_python_wraps_exec_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Loader execution failures should be wrapped."""

    class _Loader:
        def exec_module(self, module: types.ModuleType) -> None:
            raise RuntimeError("boom")

    class _Spec:
        loader = _Loader()

    monkeypatch.setattr(
        importlib.util,
        "spec_from_file_location",
        lambda name, path: _Spec(),
    )
    monkeypatch.setattr(
        importlib.util,
        "module_from_spec",
        lambda spec: types.ModuleType("workflow_module"),
    )

    with pytest.raises(ingest.CLIError, match="Failed to execute Python file"):
        ingest._load_workflow_from_python(tmp_path / "workflow.py")


def test_load_workflow_from_python_rejects_invalid_workflow_object(
    tmp_path: Path,
) -> None:
    """The workflow variable must expose deployment payload generation."""
    py_file = tmp_path / "workflow.py"
    py_file.write_text("workflow = object()\n", encoding="utf-8")

    with pytest.raises(
        ingest.CLIError,
        match="must be an orcheo_sdk.Workflow instance",
    ):
        ingest._load_workflow_from_python(py_file)


def test_load_workflow_from_python_wraps_payload_generation_errors(
    tmp_path: Path,
) -> None:
    """Deployment payload generation failures should be wrapped."""
    py_file = tmp_path / "workflow.py"
    py_file.write_text(
        """
class _Workflow:
    def to_deployment_payload(self):
        raise RuntimeError('boom')

workflow = _Workflow()
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ingest.CLIError, match="Failed to generate deployment payload"):
        ingest._load_workflow_from_python(py_file)


def test_load_workflow_from_python_falls_back_to_source_content(
    tmp_path: Path,
) -> None:
    """When no workflow object exists, the original script should be returned."""
    py_file = tmp_path / "workflow.py"
    py_file.write_text(
        "print('start')\nif __name__ == '__main__':\n    run()\nprint('end')\n",
        encoding="utf-8",
    )

    assert ingest._load_workflow_from_python(py_file) == {
        "_type": "langgraph_script",
        "script": "print('start')",
        "entrypoint": None,
    }
