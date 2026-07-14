"""Restricted-mode policy for nodes marked unsafe for untrusted authors.

In restricted definition mode uploaded workflows are untrusted, yet built-in
nodes run **in-process with full host privileges** using author-controlled
config. Restricted mode exists precisely to isolate tenants, so node types whose
capability inherently breaks that isolation are rejected at ingestion for
untrusted authors:

Nodes opt into rejection with ``NodeMetadata(restricted=True)``. This keeps the
security decision next to each node's registry declaration instead of relying
on category names or a central class-name list. Browser, Postgres, and
file-backed loader nodes are currently marked restricted because they can reach
internal HTTP services, the Orcheo Postgres database, or worker filesystem paths
that are not otherwise isolated.

This mirrors the existing per-capability guard in ``nodes/ai/mcp_guard.py``
(local ``stdio`` MCP subprocess) and complements ``security/ssrf.py`` (which
keeps *HTTP* nodes usable with a guarded transport). ``CodeNode`` bodies are not
affected — they run in the MicroPython-WASM sandbox regardless.

Enforcement happens during ``compile_workflow_to_ir`` (ingestion), so a
disallowed workflow is rejected before it is ever stored or executed. Trusted
first-party sources (candidate onboarding) are curated, not untrusted uploads,
and bypass the policy via the ``enforce_node_policy`` flag threaded from the
ingestion entrypoint.
"""

from __future__ import annotations
from orcheo.graph.ir.exceptions import WorkflowValidationError


def restricted_mode_rejection_reason(node_type: str) -> str | None:
    """Return why ``node_type`` is disallowed for untrusted authors, else ``None``.

    Args:
        node_type: The registry name of the built-in node being instantiated.

    Returns:
        A human-readable reason string when the node type is blocked for
        untrusted restricted-mode authors, or ``None`` when it is permitted.
    """
    from orcheo.nodes.registry import registry

    metadata = registry.get_metadata(node_type)
    if metadata is not None and metadata.restricted:
        return f"node type '{node_type}' is marked restricted for untrusted authors"
    return None


def check_node_type_allowed(
    node_type: str, node_id: str, *, lineno: int | None = None
) -> None:
    """Reject a node type that untrusted restricted-mode authors may not use.

    Args:
        node_type: The registry name of the built-in node being instantiated.
        node_id: The workflow-local node id, used in the error message.
        lineno: 1-based source line of the node construction, if known.

    Raises:
        WorkflowValidationError: When ``node_type`` is blocked for untrusted
            restricted-mode authors.
    """
    reason = restricted_mode_rejection_reason(node_type)
    if reason is not None:
        raise WorkflowValidationError(
            f"node '{node_id}': {reason}. This capability is disabled in "
            "restricted mode because it cannot be isolated from other tenants.",
            lineno=lineno,
        )


__all__ = [
    "check_node_type_allowed",
    "restricted_mode_rejection_reason",
]
