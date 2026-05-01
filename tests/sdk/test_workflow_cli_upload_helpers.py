"""Tests for workflow upload helpers related to LangGraph scripts."""

from __future__ import annotations
from pathlib import Path
from typing import Any
import pytest
from orcheo_sdk.cli.errors import APICallError, CLIError
from orcheo_sdk.cli.workflow import _upload_langgraph_script, upload_workflow
from tests.sdk.workflow_cli_test_utils import DummyCtx, StubClient, make_state


def test_upload_langgraph_script_fetch_failure() -> None:
    state = make_state()

    class FailingClient(StubClient):
        def get(self, url: str) -> Any:  # type: ignore[override]
            raise RuntimeError("boom")

    state.client = FailingClient()

    workflow_config = {"script": "print('hello')", "entrypoint": None}
    with pytest.raises(CLIError) as excinfo:
        _upload_langgraph_script(
            state,
            workflow_config,
            "wf-1",
            None,
            None,
            Path("demo.py"),
            None,
        )
    assert "Failed to fetch workflow" in str(excinfo.value)


def test_upload_langgraph_script_create_failure() -> None:
    state = make_state()

    class CreatingClient(StubClient):
        def post(self, url: str, **payload: Any) -> Any:  # type: ignore[override]
            if url.endswith("/api/workflows"):
                raise RuntimeError("cannot create")
            return {"version": 1}

    state.client = CreatingClient()

    workflow_config = {"script": "print('hello')", "entrypoint": None}
    with pytest.raises(CLIError) as excinfo:
        _upload_langgraph_script(
            state,
            workflow_config,
            None,
            None,
            None,
            Path("demo.py"),
            None,
        )
    assert "Failed to create workflow" in str(excinfo.value)


def test_upload_langgraph_script_rename_failure() -> None:
    state = make_state()

    class RenameFailingClient(StubClient):
        def get(self, url: str) -> Any:  # type: ignore[override]
            assert url.endswith("/api/workflows/wf-1")
            return {"id": "wf-1", "name": "existing"}

        def put(self, url: str, **payload: Any) -> Any:  # type: ignore[override]
            raise RuntimeError("cannot rename")

    state.client = RenameFailingClient()

    workflow_config = {"script": "print('hello')", "entrypoint": None}
    with pytest.raises(CLIError) as excinfo:
        _upload_langgraph_script(
            state,
            workflow_config,
            "wf-1",
            None,
            None,
            Path("demo.py"),
            "New Name",
        )
    assert "Failed to update workflow 'wf-1'" in str(excinfo.value)


def test_upload_langgraph_script_creates_workflow_with_handle() -> None:
    state = make_state()

    class HandleMissingClient(StubClient):
        def get(self, url: str) -> Any:  # type: ignore[override]
            raise APICallError("Workflow not found", status_code=404)

        def post(self, url: str, **payload: Any) -> Any:  # type: ignore[override]
            self.calls.append(("POST", url, payload))
            if url.endswith("/api/workflows"):
                assert payload["json_body"]["handle"] == "simple-agent"
                return {
                    "id": "wf-new",
                    "name": "simple-agent",
                    "handle": "simple-agent",
                }
            if url.endswith("/api/workflows/wf-new/versions/ingest"):
                return {"version": 1}
            raise AssertionError(f"Unexpected POST {url}")

    state.client = HandleMissingClient()

    workflow_config = {"script": "print('hello')", "entrypoint": None}
    result = _upload_langgraph_script(
        state,
        workflow_config,
        None,
        "simple_agent",
        "A simple agent workflow",
        Path("demo.py"),
        None,
    )

    assert result["id"] == "wf-new"
    assert result["latest_version"]["version"] == 1
    create_call = state.client.calls[0]
    assert create_call[2]["json_body"]["handle"] == "simple-agent"
    assert create_call[2]["json_body"]["description"] == "A simple agent workflow"


def test_upload_langgraph_script_creates_new_workflow_when_handle_is_archived() -> None:
    state = make_state()

    class ArchivedHandleClient(StubClient):
        def get(self, url: str) -> Any:  # type: ignore[override]
            assert url.endswith("/api/workflows/simple-agent")
            return {
                "id": "wf-archived",
                "name": "simple-agent",
                "handle": "simple-agent",
                "is_archived": True,
            }

        def post(self, url: str, **payload: Any) -> Any:  # type: ignore[override]
            self.calls.append(("POST", url, payload))
            if url.endswith("/api/workflows"):
                assert payload["json_body"]["handle"] == "simple-agent"
                return {
                    "id": "wf-new",
                    "name": "simple-agent",
                    "handle": "simple-agent",
                }
            if url.endswith("/api/workflows/wf-new/versions/ingest"):
                return {"version": 1}
            raise AssertionError(f"Unexpected POST {url}")

    state.client = ArchivedHandleClient()

    workflow_config = {"script": "print('hello')", "entrypoint": None}
    result = _upload_langgraph_script(
        state,
        workflow_config,
        None,
        "simple_agent",
        None,
        Path("demo.py"),
        None,
    )

    assert result["id"] == "wf-new"
    assert result["latest_version"]["version"] == 1


def test_upload_langgraph_script_updates_existing_workflow_by_handle() -> None:
    state = make_state()

    class ExistingHandleClient(StubClient):
        def get(self, url: str) -> Any:  # type: ignore[override]
            assert url.endswith("/api/workflows/simple-agent")
            return {"id": "wf-existing", "name": "demo", "handle": "simple-agent"}

        def put(self, url: str, **payload: Any) -> Any:  # type: ignore[override]
            raise AssertionError("rename should not be needed")

        def post(self, url: str, **payload: Any) -> Any:  # type: ignore[override]
            self.calls.append(("POST", url, payload))
            if url.endswith("/api/workflows/wf-existing/versions/ingest"):
                return {"version": 2}
            raise AssertionError(f"Unexpected POST {url}")

    state.client = ExistingHandleClient()

    workflow_config = {"script": "print('hello')", "entrypoint": None}
    result = _upload_langgraph_script(
        state,
        workflow_config,
        None,
        "simple_agent",
        None,
        Path("demo.py"),
        None,
    )

    assert result["id"] == "wf-existing"
    assert result["latest_version"]["version"] == 2


def test_upload_langgraph_script_updates_existing_workflow_description() -> None:
    state = make_state()

    class ExistingHandleClient(StubClient):
        def get(self, url: str) -> Any:  # type: ignore[override]
            assert url.endswith("/api/workflows/simple-agent")
            return {
                "id": "wf-existing",
                "name": "demo",
                "handle": "simple-agent",
                "description": "Old description",
            }

        def put(self, url: str, **payload: Any) -> Any:  # type: ignore[override]
            self.calls.append(("PUT", url, payload))
            return {
                "id": "wf-existing",
                "name": "demo",
                "handle": "simple-agent",
                "description": payload["json_body"]["description"],
            }

        def post(self, url: str, **payload: Any) -> Any:  # type: ignore[override]
            self.calls.append(("POST", url, payload))
            if url.endswith("/api/workflows/wf-existing/versions/ingest"):
                return {"version": 3}
            raise AssertionError(f"Unexpected POST {url}")

    state.client = ExistingHandleClient()

    workflow_config = {"script": "print('hello')", "entrypoint": None}
    result = _upload_langgraph_script(
        state,
        workflow_config,
        None,
        "simple_agent",
        "New description",
        Path("demo.py"),
        None,
    )

    assert result["description"] == "New description"
    assert result["latest_version"]["version"] == 3
    put_call = state.client.calls[0]
    assert put_call[2]["json_body"]["description"] == "New description"


def test_upload_workflow_overrides_entrypoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = make_state()
    dummy_path = tmp_path / "workflow.py"
    dummy_path.write_text("print('hello')", encoding="utf-8")

    loaded_config = {
        "_type": "langgraph_script",
        "script": "print('hello')",
        "entrypoint": None,
    }

    captured_config: dict[str, Any] | None = None

    def fake_loader(path: Path) -> dict[str, Any]:
        assert path == dummy_path
        return dict(loaded_config)

    def fake_uploader(
        state_arg: Any,
        workflow_config: dict[str, Any],
        workflow_id: str | None,
        workflow_handle: str | None,
        workflow_description: str | None,
        path: Path,
        name_override: str | None,
    ) -> dict[str, Any]:
        nonlocal captured_config
        captured_config = workflow_config
        assert workflow_id is None
        assert workflow_handle is None
        assert workflow_description is None
        assert path == dummy_path
        assert name_override is None
        return {"id": "wf-123"}

    def fake_render(console: Any, data: Any, title: Any = None) -> None:
        state.console.messages.append(f"render:{data}")

    monkeypatch.setattr(
        "orcheo_sdk.cli.workflow._load_workflow_from_python", fake_loader
    )
    monkeypatch.setattr(
        "orcheo_sdk.cli.workflow._upload_langgraph_script", fake_uploader
    )
    monkeypatch.setattr("orcheo_sdk.cli.workflow.render_json", fake_render)

    upload_workflow(
        DummyCtx(state),
        str(dummy_path),
        entrypoint="custom.entry",
    )

    assert captured_config is not None
    assert captured_config["entrypoint"] == "custom.entry"


def test_upload_workflow_rejects_directory_traversal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = make_state()
    outside_file = tmp_path.parent / "outside.py"
    outside_file.write_text("print('hi')", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    with pytest.raises(CLIError) as excinfo:
        upload_workflow(DummyCtx(state), "../outside.py")

    assert "escapes the current working directory" in str(excinfo.value)
