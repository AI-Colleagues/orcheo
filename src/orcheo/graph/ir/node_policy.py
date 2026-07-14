"""Restricted-mode node capability policy (untrusted-author node allowlist).

In restricted definition mode uploaded workflows are untrusted, yet built-in
nodes run **in-process with full host privileges** using author-controlled
config. Restricted mode exists precisely to isolate tenants, so node types whose
capability inherently breaks that isolation are rejected at ingestion for
untrusted authors:

* **Browser nodes** drive a real Playwright browser — arbitrary page rendering,
  ``page.evaluate`` JavaScript execution, and subresource/redirect egress that
  the HTTP SSRF guard cannot see (a different networking stack).
* **Database nodes** open connections to an author-supplied host and run
  author-supplied queries (arbitrary SQL / Mongo commands against arbitrary,
  possibly internal, endpoints).
* **Communication nodes** open author-controlled SMTP or webhook targets using
  networking stacks that are not protected by the restricted HTTP transport.
* **File-backed loader nodes** read author-supplied paths from the worker
  filesystem and can therefore expose files outside the workflow workspace.

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


# Registry categories blocked for untrusted restricted-mode authors. Blocking by
# category (not just by name) means a newly added node in one of these
# categories is denied by default — fail-closed for new dangerous capabilities.
RESTRICTED_BLOCKED_CATEGORIES = frozenset({"browser", "communication", "mongodb"})

# Individual node types blocked regardless of their registry category (e.g.
# ``PostgresNode`` lives in the mixed ``storage`` category).
_RESTRICTED_BLOCKED_NODE_REASONS = {
    "DatasetNode": "reads author-specified files from the worker filesystem",
    "DocumentLoaderNode": "reads author-specified files from the worker filesystem",
    "MultiDoc2DialCorpusLoaderNode": (
        "reads author-specified files from the worker filesystem"
    ),
    "MultiDoc2DialDatasetNode": (
        "reads author-specified files from the worker filesystem"
    ),
    "PostgresNode": (
        "opens connections to an author-specified database host and runs "
        "author-specified queries"
    ),
    "QReCCDatasetNode": "reads author-specified files from the worker filesystem",
}
RESTRICTED_BLOCKED_NODE_TYPES = frozenset(_RESTRICTED_BLOCKED_NODE_REASONS)


def restricted_mode_rejection_reason(node_type: str) -> str | None:
    """Return why ``node_type`` is disallowed for untrusted authors, else ``None``.

    Args:
        node_type: The registry name of the built-in node being instantiated.

    Returns:
        A human-readable reason string when the node type is blocked for
        untrusted restricted-mode authors, or ``None`` when it is permitted.
    """
    from orcheo.nodes.registry import registry

    if reason := _RESTRICTED_BLOCKED_NODE_REASONS.get(node_type):
        return f"node type '{node_type}' {reason}"
    metadata = registry.get_metadata(node_type)
    category = metadata.category if metadata is not None else None
    if category in RESTRICTED_BLOCKED_CATEGORIES:
        return (
            f"node type '{node_type}' (category '{category}') drives host "
            "resources that cannot be isolated from other tenants"
        )
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
    "RESTRICTED_BLOCKED_CATEGORIES",
    "RESTRICTED_BLOCKED_NODE_TYPES",
    "check_node_type_allowed",
    "restricted_mode_rejection_reason",
]
