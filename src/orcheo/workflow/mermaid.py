"""Render Mermaid diagrams from workflow graph payloads."""

from __future__ import annotations
import logging
from collections.abc import Callable, Mapping
from typing import Any
from orcheo.graph.ingestion.config import FROZEN_IR_FORMAT, LANGGRAPH_SCRIPT_FORMAT


logger = logging.getLogger(__name__)


def render_mermaid_from_graph_payload(graph_payload: dict[str, Any]) -> str | None:
    """Render a Mermaid diagram from a stored workflow graph payload.

    For ``langgraph-script`` payloads the script is executed and LangGraph's
    native ``draw_mermaid()`` is used.  For ``frozen-ir`` payloads (restricted
    mode) the IR is rebuilt structurally without running author code.  Returns
    ``None`` if the payload is not renderable or rendering fails.
    """
    return _render_from_payload(graph_payload, _render_mermaid_from_script)


def render_mermaid_from_graph_payload_full_env(
    graph_payload: dict[str, Any],
) -> str | None:
    """Render Mermaid from a stored graph payload using the full Python environment.

    Mirrors :func:`render_mermaid_from_graph_payload` but renders
    ``langgraph-script`` payloads via the full-environment loader, which skips
    the script size limit.  Only call this from trusted server-side contexts
    (e.g., ingest-time pre-computation) where the script has already been
    size-validated.  ``frozen-ir`` payloads render the same way regardless of
    the loader since no script is executed.
    """
    return _render_from_payload(graph_payload, _render_mermaid_from_script_full_env)


def render_mermaid_from_ir(ir: Mapping[str, Any]) -> str | None:
    """Render a Mermaid diagram from a frozen workflow IR.

    The IR is rebuilt into a structural ``StateGraph`` via the trusted IR
    builder with every ``CodeNode`` body replaced by an inert placeholder, so
    **no author code runs** and the sandbox runner is never imported.  Returns
    ``None`` if the IR is malformed or rendering otherwise fails.
    """
    from orcheo.graph.ir.builder import build_state_graph_from_ir

    try:
        graph = build_state_graph_from_ir(ir, code_node_factory=_ir_diagram_placeholder)
        return _render_graph_mermaid(graph)
    except Exception:
        logger.warning("IR mermaid rendering failed", exc_info=True)
        return None


def _render_from_payload(
    graph_payload: dict[str, Any],
    script_renderer: Callable[[str, str | None, str | None], str | None],
) -> str | None:
    """Dispatch a stored graph payload to the renderer for its format."""
    fmt = graph_payload.get("format", "")
    if fmt == FROZEN_IR_FORMAT:
        ir = graph_payload.get("ir")
        return render_mermaid_from_ir(ir) if isinstance(ir, Mapping) else None
    if fmt != LANGGRAPH_SCRIPT_FORMAT:
        return None
    source = graph_payload.get("source")
    if not isinstance(source, str) or not source.strip():
        return None
    entrypoint = graph_payload.get("entrypoint")
    filename = graph_payload.get("filename")
    return script_renderer(
        source,
        entrypoint if isinstance(entrypoint, str) else None,
        filename if isinstance(filename, str) else None,
    )


def _ir_diagram_placeholder(_spec: Any) -> Callable[..., Any]:
    """Return an inert stand-in node for a ``CodeNodeSpec``.

    Diagram rendering only needs graph structure, never ``CodeNode`` behaviour,
    so the body is never built or executed.  This keeps IR rendering free of the
    MicroPython-WASM sandbox and of any author code.
    """

    def _placeholder(state: Any, *_: Any, **__: Any) -> Any:
        return state

    return _placeholder


def _render_graph_mermaid(graph: Any) -> str | None:
    """Render Mermaid from a built ``StateGraph`` via the shared summary path."""
    from orcheo.graph.ingestion.summary import (
        _render_compact_mermaid,
        summarise_state_graph,
    )
    from orcheo.graph.mermaid import has_workflow_tool_subgraphs, render_summary_mermaid

    summary = summarise_state_graph(graph)
    if has_workflow_tool_subgraphs(summary) or summary.get("conditional_edges"):
        return render_summary_mermaid(summary)
    return _render_compact_mermaid(graph)


def _render_mermaid_from_script(
    source: str, entrypoint: str | None = None, script_filename: str | None = None
) -> str | None:
    """Execute ``source`` and render a Mermaid diagram from the graph."""
    from orcheo.graph.ingestion.exceptions import ScriptIngestionError
    from orcheo.graph.ingestion.loader import load_graph_from_script

    try:
        kwargs: dict[str, Any] = {"entrypoint": entrypoint}
        if script_filename is not None:
            kwargs["script_filename"] = script_filename
        graph = load_graph_from_script(source, **kwargs)
        return _render_graph_mermaid(graph)
    except ScriptIngestionError as exc:
        logger.warning("Mermaid rendering skipped: script ingestion error: %s", exc)
        return None
    except Exception:
        logger.warning("Mermaid rendering failed", exc_info=True)
        return None


def _render_mermaid_from_script_full_env(
    source: str, entrypoint: str | None = None, script_filename: str | None = None
) -> str | None:
    """Render a Mermaid diagram using the full Python environment.

    Mirrors :func:`_render_mermaid_from_script` but uses
    :func:`~orcheo.graph.ingestion.loader.load_graph_from_script_full_env`
    which skips the script size limit.  Only call this from trusted
    server-side contexts where the script has already been size-validated.
    """
    from orcheo.graph.ingestion.exceptions import ScriptIngestionError
    from orcheo.graph.ingestion.loader import load_graph_from_script_full_env

    try:
        kwargs: dict[str, Any] = {"entrypoint": entrypoint}
        if script_filename is not None:
            kwargs["script_filename"] = script_filename
        graph = load_graph_from_script_full_env(source, **kwargs)
        return _render_graph_mermaid(graph)
    except ScriptIngestionError as exc:
        logger.warning("Mermaid rendering skipped: script ingestion error: %s", exc)
        return None
    except Exception:
        logger.warning("Mermaid rendering failed", exc_info=True)
        return None


__all__ = [
    "render_mermaid_from_graph_payload",
    "render_mermaid_from_graph_payload_full_env",
    "render_mermaid_from_ir",
]
