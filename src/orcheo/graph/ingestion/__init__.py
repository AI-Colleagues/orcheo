"""Public entrypoints for LangGraph script ingestion."""

from __future__ import annotations
import ast
import logging
from typing import Any
from orcheo.graph.ingestion.ast_extraction import extract_graph_index
from orcheo.graph.ingestion.config import (
    DEFAULT_SCRIPT_SIZE_LIMIT,
    FROZEN_IR_FORMAT,
    LANGGRAPH_SCRIPT_FORMAT,
)
from orcheo.graph.ingestion.exceptions import ScriptIngestionError
from orcheo.graph.ingestion.loader import (
    load_graph_from_script,
    load_graph_from_script_full_env,
)
from orcheo.graph.ingestion.sandbox import validate_script_size


logger = logging.getLogger(__name__)


def ingest_langgraph_script(
    source: str,
    *,
    entrypoint: str | None = None,
    script_filename: str | None = None,
    max_script_bytes: int | None = DEFAULT_SCRIPT_SIZE_LIMIT,
) -> dict[str, Any]:
    """Validate and index a LangGraph Python script without executing it.

    The returned payload stores the source alongside a lightweight index of
    derived metadata (cron triggers, listener subscriptions). Mermaid
    rendering is deferred to the dedicated endpoint which builds the graph
    on demand.
    """
    validate_script_size(source, max_script_bytes)
    try:
        compile(source, "<langgraph-script>", "exec", ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)  # noqa: S307
    except SyntaxError as exc:
        raise ScriptIngestionError(f"Compilation error: {exc}") from exc

    index = extract_graph_index(source)
    return {
        "format": LANGGRAPH_SCRIPT_FORMAT,
        "source": source,
        "entrypoint": entrypoint,
        "index": index,
        **({"filename": script_filename} if script_filename else {}),
    }


def ingest_workflow(
    source: str,
    *,
    entrypoint: str | None = None,
    script_filename: str | None = None,
    max_script_bytes: int | None = DEFAULT_SCRIPT_SIZE_LIMIT,
    trusted_source: bool = False,
) -> dict[str, Any]:
    """Ingest a ``workflow.py`` per the active definition mode.

    In ``restricted`` mode the source is compiled to the frozen IR (no author
    code runs) and the IR is the stored graph payload. In ``unrestricted`` mode
    today's :func:`ingest_langgraph_script` behaviour is preserved and an
    explicit not-tenant-safe warning is logged.

    Args:
        source: The ``workflow.py`` source string.
        entrypoint: Optional entrypoint name (unrestricted mode only).
        script_filename: Optional compiled filename (unrestricted mode only).
        max_script_bytes: Optional upload size limit.
        trusted_source: ``True`` for curated first-party sources (candidate
            onboarding), which bypass the restricted-mode node capability policy.
            Client uploads leave this ``False`` so the policy is enforced.

    Returns:
        The graph payload to persist: a ``frozen-ir`` payload in restricted mode
        or a ``langgraph-script`` payload in unrestricted mode.
    """
    from orcheo.graph.ir.definition_mode import (
        is_restricted_mode,
        log_active_definition_mode,
    )

    log_active_definition_mode()

    if is_restricted_mode():
        from orcheo.graph.ir import WorkflowValidationError, compile_workflow_to_ir

        validate_script_size(source, max_script_bytes)
        try:
            ir = compile_workflow_to_ir(source, enforce_node_policy=not trusted_source)
        except WorkflowValidationError as exc:
            # Audit log for ingestion rejections (line-referenced where known).
            logger.warning(
                "Rejected restricted-mode workflow ingestion at line %s: %s",
                exc.lineno,
                exc.raw_message,
            )
            raise
        return {
            "format": FROZEN_IR_FORMAT,
            "ir": ir.model_dump(),
            "entrypoint": ir.entrypoint,
            "index": extract_graph_index(source),
        }

    logger.warning(
        "Ingesting workflow in unrestricted mode: author code will execute "
        "in-process at build time with no tenant isolation."
    )
    return ingest_langgraph_script(
        source,
        entrypoint=entrypoint,
        script_filename=script_filename,
        max_script_bytes=max_script_bytes,
    )


__all__ = [
    "DEFAULT_SCRIPT_SIZE_LIMIT",
    "FROZEN_IR_FORMAT",
    "LANGGRAPH_SCRIPT_FORMAT",
    "ScriptIngestionError",
    "extract_graph_index",
    "ingest_langgraph_script",
    "ingest_workflow",
    "load_graph_from_script",
    "load_graph_from_script_full_env",
    "validate_script_size",
]
