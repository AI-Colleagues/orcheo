"""Regression coverage for the workflow-backed Hosted Apps example."""

from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from orcheo.graph.ingestion import load_graph_from_script_full_env
from orcheo.graph.ir import compile_workflow_to_ir
from orcheo.hosted_apps.zip_validation import validate_bundle


EXAMPLE_ROOT = (
    Path(__file__).resolve().parents[2] / "examples" / "hosted_apps" / "workflow-app"
)


@pytest.mark.parametrize(
    ("filename", "node_id"),
    [
        ("workflow.py", "create_greeting"),
        ("farewell_workflow.py", "create_farewell"),
    ],
)
def test_example_workflows_are_restricted_mode_compatible(
    filename: str, node_id: str
) -> None:
    """Both examples compile to frozen IR without executing author code."""
    source = (EXAMPLE_ROOT / filename).read_text()

    ir = compile_workflow_to_ir(source)

    assert ir.entrypoint == node_id
    assert [node.id for node in ir.nodes] == [node_id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "field", "expected"),
    [
        ("workflow.py", "greeting", "Hello, Ada!"),
        ("farewell_workflow.py", "farewell", "Goodbye, Ada!"),
    ],
)
async def test_example_workflows_return_structured_messages(
    filename: str, field: str, expected: str
) -> None:
    """Each uploaded workflow produces the response consumed by its UI action."""
    source = (EXAMPLE_ROOT / filename).read_text()
    graph = load_graph_from_script_full_env(
        source,
        entrypoint="orcheo_workflow",
        script_filename=str(EXAMPLE_ROOT / filename),
    )

    result = await graph.compile().ainvoke({"inputs": {"name": "  Ada  "}})

    assert result["structured_response"] == {field: expected, "name": "Ada"}


def test_example_browser_bundle_passes_hosted_app_validation() -> None:
    """The browser assets and private app manifest form a valid Hosted App ZIP."""
    archive_path = EXAMPLE_ROOT.parent / "workflow-app.zip"
    with archive_path.open("rb") as archive:
        manifest = validate_bundle(archive)

    assert manifest.index == "index.html"
    assert set(manifest.files) == {"index.html", "styles.css", "app.js"}
    assert manifest.app_manifest is not None
    assert set(manifest.app_manifest.bindings) == {"greet", "farewell"}

    with zipfile.ZipFile(archive_path) as bundle:
        assert set(bundle.namelist()) == {
            "index.html",
            "styles.css",
            "app.js",
            "orcheo.app.json",
        }
        for name in ("index.html", "styles.css", "app.js", "orcheo.app.json"):
            assert bundle.read(name) == (EXAMPLE_ROOT / name).read_bytes()


def test_example_client_uses_only_logical_workflow_bindings() -> None:
    """Browser code selects a logical binding and follows its opaque run handle."""
    client = (EXAMPLE_ROOT / "app.js").read_text()

    assert "`/__orcheo/workflows/${encodeURIComponent(binding)}/runs`" in client
    assert "`/__orcheo/runs/${encodeURIComponent(handle)}`" in client
    assert '"Idempotency-Key": createIdempotencyKey()' in client
    assert "workflow_id" not in client


def test_example_manifest_declares_two_distinct_workflows() -> None:
    """Each UI function maps to a portable, exact workflow-version request."""
    archive_path = EXAMPLE_ROOT.parent / "workflow-app.zip"
    with archive_path.open("rb") as archive:
        manifest = validate_bundle(archive)

    assert manifest.app_manifest is not None
    greet = manifest.app_manifest.bindings["greet"]
    farewell = manifest.app_manifest.bindings["farewell"]
    assert greet.workflow == "hosted-app-greeting"
    assert farewell.workflow == "hosted-app-farewell"
    assert greet.workflow != farewell.workflow
    assert greet.version == farewell.version == 1
    assert greet.output_projection == {"fields": ["final_state"]}
    assert farewell.visitor_can_read_output is True
