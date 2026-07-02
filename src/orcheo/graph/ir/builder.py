"""Trusted rebuilder: frozen workflow IR -> runnable LangGraph ``StateGraph``.

This is the only path that turns a stored IR into an executable graph. It runs
no author code: built-in nodes are constructed from trusted registered
constructors with JSON-coercible config, edges and declarative conditional
edges are wired from data, and each ``CodeNode`` spec is bound to a
sandbox-backed factory supplied by the caller (added in Milestone 3). The
original ``workflow.py`` is never re-executed.
"""

from __future__ import annotations
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError
from orcheo.graph.conditional import add_conditional_edges
from orcheo.graph.ir.exceptions import IRValidationError
from orcheo.graph.ir.models import (
    CURRENT_SCHEMA_VERSION,
    END_VERTEX,
    IR_CONFIG_KIND_KEY,
    START_VERTEX,
    WORKFLOW_TOOL_CONFIG_KIND,
    BuiltinNodeSpec,
    CodeNodeSpec,
    GraphIR,
    SubgraphNodeSpec,
)
from orcheo.graph.state import State
from orcheo.nodes.registry import registry


# IR schema versions this builder can rebuild.
SUPPORTED_SCHEMA_VERSIONS = frozenset({CURRENT_SCHEMA_VERSION})

# Maximum subgraph / workflow-tool-graph nesting depth. Bounds recursion during
# validation (and, via the validate-before-build contract, the build) so a
# deeply nested but acyclic IR cannot overflow the Python call stack — a DoS
# vector for restricted multi-tenant ingestion.
MAX_GRAPH_DEPTH = 32

# Builds a runnable node from a ``CodeNodeSpec`` (sandbox runner in M3).
CodeNodeFactory = Callable[[CodeNodeSpec], Any]

_SENTINELS = {START_VERTEX: START, END_VERTEX: END}


def _ensure_builtin_nodes_registered() -> None:
    """Import the node package so built-in types populate the registry.

    Rebuilding an IR may run before any node module has been imported (e.g. a
    fresh worker process). Importing ``orcheo.nodes`` is idempotent and cheap
    after the first call, and guarantees ``registry.get_node`` can resolve every
    built-in type.
    """
    import orcheo.nodes  # noqa: F401  (import for registration side effects)


def _vertex(name: str) -> Any:
    """Map a sentinel endpoint to the LangGraph constant, else return ``name``."""
    return _SENTINELS.get(name, name)


def coerce_ir(ir: GraphIR | Mapping[str, Any]) -> GraphIR:
    """Return a validated :class:`GraphIR`, parsing a mapping if needed.

    Raises:
        IRValidationError: When ``ir`` is a mapping that fails schema validation.
    """
    if isinstance(ir, GraphIR):
        return ir
    try:
        return GraphIR.model_validate(ir)
    except ValidationError as exc:
        msg = f"Malformed workflow IR: {exc}"
        raise IRValidationError(msg) from exc


def validate_ir(ir: GraphIR | Mapping[str, Any]) -> GraphIR:
    """Re-validate an IR before build/run and return the parsed model.

    Performs the schema parse plus semantic checks the schema cannot express:
    supported version, non-empty unique ids, registered built-in node types, a
    resolvable entrypoint, and edges/conditional edges that only reference known
    vertices.

    Raises:
        IRValidationError: On any structural or referential problem.
    """
    model = coerce_ir(ir)
    _ensure_builtin_nodes_registered()

    _validate_graph_model(model)
    return model


def _validate_graph_model(model: GraphIR, *, path: str = "IR", depth: int = 0) -> None:
    """Validate one graph model and all nested subgraph models."""
    if depth > MAX_GRAPH_DEPTH:
        msg = f"{path} nesting depth exceeds the maximum of {MAX_GRAPH_DEPTH}"
        raise IRValidationError(msg)
    if model.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        msg = (
            f"Unsupported {path} schema_version {model.schema_version}; "
            f"supported versions: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )
        raise IRValidationError(msg)

    node_ids = _validate_nodes(model, path=path)
    _validate_entrypoint(model, node_ids, path=path)
    _validate_edges(model, node_ids, path=path)
    _validate_conditional_edges(model, node_ids, path=path)
    for spec in model.nodes:
        if isinstance(spec, BuiltinNodeSpec):
            _validate_config_graphs(
                spec.config, path=f"{path} node '{spec.id}' config", depth=depth
            )
        if isinstance(spec, SubgraphNodeSpec):
            _validate_graph_model(
                spec.graph, path=f"{path} subgraph '{spec.id}'", depth=depth + 1
            )


def _validate_config_graphs(value: Any, *, path: str, depth: int) -> None:
    """Validate nested graph IR carried inside built-in config values."""
    if isinstance(value, Mapping):
        if value.get(IR_CONFIG_KIND_KEY) == WORKFLOW_TOOL_CONFIG_KIND:
            graph_value = value.get("graph")
            if not isinstance(graph_value, Mapping | GraphIR):
                msg = f"{path} workflow tool requires a nested graph IR mapping"
                raise IRValidationError(msg)
            _validate_graph_model(
                coerce_ir(graph_value),
                path=f"{path} workflow tool",
                depth=depth + 1,
            )
            return
        for key, nested in value.items():
            _validate_config_graphs(nested, path=f"{path}.{key}", depth=depth)
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for index, nested in enumerate(value):
            _validate_config_graphs(nested, path=f"{path}[{index}]", depth=depth)


def _validate_nodes(model: GraphIR, *, path: str) -> set[str]:
    """Validate node ids/types and return the set of declared node ids."""
    node_ids: set[str] = set()
    for spec in model.nodes:
        if not spec.id or not spec.id.strip():
            msg = f"{path} node id must be a non-empty string: {spec!r}"
            raise IRValidationError(msg)
        if spec.id in node_ids:
            msg = f"Duplicate node id '{spec.id}' in {path}"
            raise IRValidationError(msg)
        if spec.id in _SENTINELS:
            msg = f"{path} node id '{spec.id}' collides with a reserved sentinel"
            raise IRValidationError(msg)
        if isinstance(spec, BuiltinNodeSpec) and registry.get_node(spec.type) is None:
            msg = (
                f"Unknown built-in node type '{spec.type}' for {path} node '{spec.id}'"
            )
            raise IRValidationError(msg)
        node_ids.add(spec.id)
    return node_ids


def _validate_entrypoint(model: GraphIR, node_ids: set[str], *, path: str) -> None:
    """Ensure the entrypoint references a declared node."""
    if not model.entrypoint:
        raise IRValidationError(f"{path} entrypoint must be a non-empty node id")
    if model.entrypoint not in node_ids:
        msg = f"{path} entrypoint '{model.entrypoint}' does not reference a known node"
        raise IRValidationError(msg)


def _is_known_vertex(name: str, node_ids: set[str]) -> bool:
    """Return ``True`` for a known node id or a recognised sentinel."""
    return name in node_ids or name in _SENTINELS


def _validate_edges(model: GraphIR, node_ids: set[str], *, path: str) -> None:
    """Ensure every edge endpoint references a known vertex."""
    for edge in model.edges:
        if not _is_known_vertex(edge.source, node_ids):
            msg = f"{path} edge source '{edge.source}' references an unknown node"
            raise IRValidationError(msg)
        if not _is_known_vertex(edge.target, node_ids):
            msg = f"{path} edge target '{edge.target}' references an unknown node"
            raise IRValidationError(msg)


def _validate_conditional_edges(
    model: GraphIR, node_ids: set[str], *, path: str
) -> None:
    """Ensure conditional-edge sources, targets, and defaults are known."""
    for cond in model.conditional_edges:
        if cond.source not in node_ids:
            msg = (
                f"{path} conditional edge source '{cond.source}' references an "
                "unknown node"
            )
            raise IRValidationError(msg)
        if not cond.mapping:
            msg = (
                f"{path} conditional edge from '{cond.source}' requires a "
                "non-empty mapping"
            )
            raise IRValidationError(msg)
        for value, target in cond.mapping.items():
            if not _is_known_vertex(target, node_ids):
                msg = (
                    f"{path} conditional edge from '{cond.source}' maps '{value}' to "
                    f"unknown target '{target}'"
                )
                raise IRValidationError(msg)
        if cond.default is not None and not _is_known_vertex(cond.default, node_ids):
            msg = (
                f"{path} conditional edge from '{cond.source}' has an unknown "
                f"default target '{cond.default}'"
            )
            raise IRValidationError(msg)


def build_state_graph_from_ir(
    ir: GraphIR | Mapping[str, Any],
    *,
    code_node_factory: CodeNodeFactory | None = None,
) -> StateGraph:
    """Rebuild a runnable :class:`StateGraph` from a validated IR.

    Args:
        ir: The frozen IR (model or mapping). Re-validated before building.
        code_node_factory: Builds a runnable node from a ``CodeNodeSpec``. The
            sandbox-backed factory is wired in Milestone 3; until then an IR that
            contains a ``CodeNodeSpec`` requires this argument.

    Returns:
        An uncompiled ``StateGraph`` (the caller compiles it), matching the
        return contract of ``load_graph_from_script``.

    Raises:
        IRValidationError: When the IR fails validation or a node cannot be
            constructed from its config.
    """
    model = validate_ir(ir)
    return _build_graph_model(model, code_node_factory=code_node_factory)


def _build_graph_model(
    model: GraphIR,
    *,
    code_node_factory: CodeNodeFactory | None,
) -> StateGraph:
    """Build a graph model that has already passed recursive validation."""
    graph: StateGraph = StateGraph(State)

    for spec in model.nodes:
        if isinstance(spec, BuiltinNodeSpec):
            graph.add_node(spec.id, _build_builtin_node(spec, code_node_factory))
        elif isinstance(spec, CodeNodeSpec):
            graph.add_node(spec.id, _build_code_node(spec, code_node_factory))
        else:
            graph.add_node(
                spec.id,
                _build_graph_model(
                    spec.graph,
                    code_node_factory=code_node_factory,
                ).compile(),
            )

    _wire_edges(graph, model)
    _wire_conditional_edges(graph, model)
    return graph


def _build_builtin_node(
    spec: BuiltinNodeSpec,
    code_node_factory: CodeNodeFactory | None,
) -> Any:
    """Construct a built-in node instance from its registered constructor."""
    constructor = registry.get_node(spec.type)
    if constructor is None:  # pragma: no cover - guarded by validate_ir
        msg = f"Unknown built-in node type '{spec.type}' for node '{spec.id}'"
        raise IRValidationError(msg)
    try:
        return constructor(
            name=spec.id,
            **_materialise_config_value(
                spec.config,
                code_node_factory=code_node_factory,
            ),
        )
    except (ValidationError, TypeError, ValueError) as exc:
        msg = f"Failed to construct node '{spec.id}' of type '{spec.type}': {exc}"
        raise IRValidationError(msg) from exc


def _materialise_config_value(
    value: Any,
    *,
    code_node_factory: CodeNodeFactory | None,
) -> Any:
    """Convert IR-only config markers into runtime objects."""
    if isinstance(value, Mapping):
        if value.get(IR_CONFIG_KIND_KEY) == WORKFLOW_TOOL_CONFIG_KIND:
            return _build_workflow_tool(value, code_node_factory=code_node_factory)
        return {
            str(key): _materialise_config_value(
                nested,
                code_node_factory=code_node_factory,
            )
            for key, nested in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [
            _materialise_config_value(nested, code_node_factory=code_node_factory)
            for nested in value
        ]
    return value


def _build_workflow_tool(
    value: Mapping[str, Any],
    *,
    code_node_factory: CodeNodeFactory | None,
) -> Any:
    """Build an AgentNode ``WorkflowTool`` from its nested IR marker."""
    from orcheo.nodes.ai import WorkflowTool

    name = value.get("name")
    description = value.get("description")
    graph_value = value.get("graph")
    if not isinstance(name, str) or not isinstance(description, str):
        msg = "Workflow tool IR config requires string 'name' and 'description'"
        raise IRValidationError(msg)
    if not isinstance(graph_value, Mapping | GraphIR):
        msg = "Workflow tool IR config requires a nested graph IR mapping"
        raise IRValidationError(msg)
    kwargs: dict[str, Any] = {
        "name": name,
        "description": description,
        "graph": _build_graph_model(
            coerce_ir(graph_value),
            code_node_factory=code_node_factory,
        ),
    }
    args_schema = value.get("args_schema")
    if args_schema is not None:
        kwargs["args_schema"] = args_schema
    output_path = value.get("output_path")
    if output_path is not None:
        kwargs["output_path"] = output_path
    return_direct = value.get("return_direct")
    if return_direct is not None:
        kwargs["return_direct"] = return_direct
    return WorkflowTool(**kwargs)


def _build_code_node(
    spec: CodeNodeSpec,
    code_node_factory: CodeNodeFactory | None,
) -> Any:
    """Build a sandbox-backed runnable for a ``CodeNodeSpec``."""
    if code_node_factory is None:
        msg = (
            f"CodeNode '{spec.id}' requires a sandbox runner; no code_node_factory "
            "was supplied (CodeNode execution is wired in Milestone 3)"
        )
        raise IRValidationError(msg)
    return code_node_factory(spec)


def _wire_edges(graph: StateGraph, model: GraphIR) -> None:
    """Add direct edges and the implicit ``START`` -> entrypoint edge."""
    has_start_edge = False
    for edge in model.edges:
        graph.add_edge(_vertex(edge.source), _vertex(edge.target))
        if edge.source == START_VERTEX:
            has_start_edge = True
    if not has_start_edge:
        graph.add_edge(START, model.entrypoint)


def _wire_conditional_edges(graph: StateGraph, model: GraphIR) -> None:
    """Add declarative conditional edges via the shared builder."""
    for cond in model.conditional_edges:
        config: dict[str, Any] = {
            "source": cond.source,
            "path": cond.path,
            "mapping": dict(cond.mapping),
            "default": cond.default,
        }
        add_conditional_edges(graph, config, {})


__all__ = [
    "MAX_GRAPH_DEPTH",
    "SUPPORTED_SCHEMA_VERSIONS",
    "CodeNodeFactory",
    "build_state_graph_from_ir",
    "coerce_ir",
    "validate_ir",
]
