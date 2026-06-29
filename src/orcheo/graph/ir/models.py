"""Frozen intermediate representation (IR) for Orcheo workflow graphs.

The IR is the single validated, JSON-coercible artifact that is persisted and
executed in restricted definition mode. It is produced by *interpreting* a
conforming ``workflow.py`` (never by executing it) and rebuilt into a runnable
``StateGraph`` by the trusted IR graph builder. The only author code carried in
the IR is each ``CodeNode`` body, stored as a string and executed solely inside
the MicroPython-WASM sandbox.

See ``project/initiatives/sandboxed_custom_workflows/2_design.md`` for the full
design rationale.
"""

from __future__ import annotations
from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field


# Schema version emitted for newly compiled IR documents.
CURRENT_SCHEMA_VERSION = 1

# Fixed state schema referenced (never redefined) by every workflow IR.
DEFAULT_STATE_REF = "orcheo.graph.state.State"

# Sentinel edge endpoint mapping to LangGraph's ``START``.
START_VERTEX = "__start__"

# Sentinel edge endpoint mapping to LangGraph's ``END``.
END_VERTEX = "__end__"


class _FrozenSpec(BaseModel):
    """Base for every IR spec: immutable and rejecting unknown fields.

    ``extra="forbid"`` makes malformed specs (stray or misspelled keys) fail
    fast during re-validation, and ``frozen=True`` signals that the IR is a
    read-only artifact once compiled.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class BuiltinNodeSpec(_FrozenSpec):
    """A registered built-in node (``AgentNode``, ``RSSNode``, …).

    The node implementation lives in Orcheo and is therefore trusted; only its
    ``type`` (registry name) and JSON-coercible ``config`` cross the trust
    boundary.
    """

    kind: Literal["builtin"] = "builtin"
    """Discriminator tag for the node-spec union."""
    id: str
    """Unique node identifier; the ``add_node`` key."""
    type: str
    """Registry name of the built-in node, e.g. ``"AgentNode"``."""
    config: dict[str, Any] = Field(default_factory=dict)
    """Node configuration: literals, ``{{state}}`` templates, or ``[[cred]]``."""


class CodeNodeSpec(_FrozenSpec):
    """User logic — the only place author code exists in the IR.

    The ``body`` is the dedented source of the ``CodeNode.run`` method and runs
    exclusively inside the MicroPython-WASM sandbox. ``[[credential]]``
    placeholders are rejected in ``config`` so the sandbox never receives
    resolved secrets.
    """

    kind: Literal["code"] = "code"
    """Discriminator tag for the node-spec union."""
    id: str
    """Unique node identifier; the ``add_node`` key."""
    config: dict[str, Any] = Field(default_factory=dict)
    """Configurable fields (no ``[[credential]]`` placeholders permitted)."""
    injected: list[str] = Field(default_factory=list)
    """Config field names exposed to the body as ``self.<field>``."""
    body: str
    """Dedented source of ``run()`` executed in the sandbox."""


# Discriminated union of built-in and code node specs.
NodeSpec = Annotated[BuiltinNodeSpec | CodeNodeSpec, Field(discriminator="kind")]


class EdgeSpec(_FrozenSpec):
    """A direct edge between two vertices.

    ``source``/``target`` are node ids or the ``__start__``/``__end__``
    sentinels.
    """

    source: str
    """Source node id or ``__start__``."""
    target: str
    """Target node id or ``__end__``."""


class ConditionalEdgeSpec(_FrozenSpec):
    """A declarative conditional edge — no Python callable.

    Mirrors the existing declarative conditional-edge configuration: a dotted
    state ``path`` is resolved at run time and its value mapped to a target.
    """

    source: str
    """Source node id whose successors branch."""
    path: str
    """Dotted state path (or named edge instance) resolved at run time."""
    mapping: dict[str, str]
    """Condition value -> target node id (or ``__end__``)."""
    default: str | None = None
    """Fallback target when no mapping key matches."""


class GraphIR(_FrozenSpec):
    """The frozen, JSON-coercible representation of a workflow graph."""

    schema_version: int = CURRENT_SCHEMA_VERSION
    """IR schema version for forward-compatibility checks."""
    state_ref: str = DEFAULT_STATE_REF
    """Dotted import path of the fixed state schema (referenced, not redefined)."""
    entrypoint: str
    """Node id wired from ``START`` when no explicit ``__start__`` edge exists."""
    nodes: list[NodeSpec] = Field(default_factory=list)
    """All node specs in the graph."""
    edges: list[EdgeSpec] = Field(default_factory=list)
    """Direct edges between vertices."""
    conditional_edges: list[ConditionalEdgeSpec] = Field(default_factory=list)
    """Declarative conditional edges."""


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "DEFAULT_STATE_REF",
    "END_VERTEX",
    "START_VERTEX",
    "BuiltinNodeSpec",
    "CodeNodeSpec",
    "ConditionalEdgeSpec",
    "EdgeSpec",
    "GraphIR",
    "NodeSpec",
]
