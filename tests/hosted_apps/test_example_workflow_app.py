"""Regression coverage for the workflow-backed Hosted Apps example."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4
import zipfile

import pytest

from orcheo.graph.ingestion import load_graph_from_script_full_env
from orcheo.graph.ir import compile_workflow_to_ir
from orcheo.hosted_apps.zip_validation import validate_bundle
from orcheo_backend.app.schemas.apps import AppBindingRequest


EXAMPLE_ROOT = (
    Path(__file__).resolve().parents[2] / "examples" / "hosted_apps" / "workflow-app"
)


def test_example_workflow_is_restricted_mode_compatible() -> None:
    """The example compiles to frozen IR without executing author code."""
    source = (EXAMPLE_ROOT / "workflow.py").read_text()

    ir = compile_workflow_to_ir(source)

    assert ir.entrypoint == "create_greeting"
    assert [node.id for node in ir.nodes] == ["create_greeting"]


@pytest.mark.asyncio
async def test_example_workflow_returns_a_structured_greeting() -> None:
    """The uploaded workflow produces the response consumed by the browser app."""
    source = (EXAMPLE_ROOT / "workflow.py").read_text()
    graph = load_graph_from_script_full_env(
        source,
        entrypoint="orcheo_workflow",
        script_filename=str(EXAMPLE_ROOT / "workflow.py"),
    )

    result = await graph.compile().ainvoke({"inputs": {"name": "  Ada  "}})

    assert result["structured_response"] == {
        "greeting": "Hello, Ada!",
        "name": "Ada",
    }


def test_example_browser_bundle_passes_hosted_app_validation() -> None:
    """The three browser assets form a valid, dependency-free Hosted App bundle."""
    archive_path = EXAMPLE_ROOT.parent / "workflow-app.zip"
    with archive_path.open("rb") as archive:
        manifest = validate_bundle(archive)

    assert manifest.index == "index.html"
    assert set(manifest.files) == {"index.html", "styles.css", "app.js"}

    with zipfile.ZipFile(archive_path) as bundle:
        for name in ("index.html", "styles.css", "app.js"):
            assert bundle.read(name) == (EXAMPLE_ROOT / name).read_bytes()


def test_example_client_uses_only_the_logical_workflow_binding() -> None:
    """Browser code addresses the logical binding and follows its opaque handle."""
    client = (EXAMPLE_ROOT / "app.js").read_text()

    assert 'fetch("/__orcheo/workflows/greet/runs"' in client
    assert "`/__orcheo/runs/${encodeURIComponent(handle)}`" in client
    assert '"Idempotency-Key": createIdempotencyKey()' in client
    assert "workflow_id" not in client


def test_example_binding_template_matches_the_control_plane_contract() -> None:
    """The documented binding policy remains accepted by the backend schema."""
    payload = json.loads((EXAMPLE_ROOT / "binding.example.json").read_text())
    payload["workflow_id"] = str(uuid4())
    payload["workflow_version_id"] = str(uuid4())

    binding = AppBindingRequest.model_validate(payload)

    assert binding.name == "greet"
    assert binding.output_projection == {"fields": ["final_state"]}
    assert binding.visitor_can_read_output is True
