"""Smoke tests for the insight_reporter workflow."""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / "colleague-experts"
    / "colleagues"
    / "insight_reporter"
    / "workflow.py"
)

if not _WORKFLOW_PATH.exists():
    pytest.skip(
        "colleague-experts repo not checked out alongside orcheo",
        allow_module_level=True,
    )


def _load_workflow_module():
    module_name = "insight_reporter_workflow"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = spec_from_file_location(module_name, _WORKFLOW_PATH)
    if spec is None or spec.loader is None:
        msg = f"Unable to load workflow module from {_WORKFLOW_PATH}"
        raise RuntimeError(msg)
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules[module_name]
        raise
    return module


@pytest.mark.asyncio
async def test_insight_reporter_workflow_builds_and_compiles() -> None:
    workflow = _load_workflow_module()

    graph = await workflow.orcheo_workflow()
    compiled = graph.compile()

    assert compiled is not None
