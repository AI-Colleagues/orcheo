"""Restricted-AST interpreter: conforming ``workflow.py`` -> frozen IR.

The interpreter compiles a conforming script into the IR by *interpreting* its
validated AST. It never ``compile``/``eval``/``exec``s author source, imports
tenant modules, or calls the entrypoint: it lifts data (node types, config
literals, edges, conditional edges, and ``CodeNode`` bodies as strings) out of
the tree and constructs the :class:`GraphIR` directly.

The pipeline is: parse -> :func:`~orcheo.graph.ir.grammar.validate_grammar` ->
collect ``CodeNode`` classes -> interpret the ``orcheo_workflow`` body -> build
and re-validate the IR.
"""

from __future__ import annotations
import ast
from dataclasses import dataclass, field
from typing import Any
from orcheo.graph.ir.builder import validate_ir
from orcheo.graph.ir.code_body import RunFunction, extract_run_body, validate_code_body
from orcheo.graph.ir.config_values import literal_from_ast, validate_config_value
from orcheo.graph.ir.exceptions import WorkflowValidationError
from orcheo.graph.ir.grammar import ENTRYPOINT_NAME, validate_grammar
from orcheo.graph.ir.models import (
    END_VERTEX,
    IR_CONFIG_KIND_KEY,
    START_VERTEX,
    WORKFLOW_TOOL_CONFIG_KIND,
    BuiltinNodeSpec,
    CodeNodeSpec,
    ConditionalEdgeSpec,
    EdgeSpec,
    GraphIR,
    NodeSpec,
    SubgraphNodeSpec,
)


_VERTEX_NAMES = {"START": START_VERTEX, "END": END_VERTEX}


@dataclass
class _CodeNodeClass:
    """Collected declaration of a ``CodeNode`` subclass."""

    run_func: RunFunction
    defaults: dict[str, Any] = field(default_factory=dict)
    declared: set[str] = field(default_factory=set)


@dataclass
class _Workflow:
    """Mutable accumulator for the interpreted graph."""

    nodes: list[NodeSpec | _SubgraphNodeRef] = field(default_factory=list)
    edges: list[EdgeSpec] = field(default_factory=list)
    conditional_edges: list[ConditionalEdgeSpec] = field(default_factory=list)
    entry_override: str | None = None


@dataclass(frozen=True)
class _SubgraphNodeRef:
    """Unresolved nested graph node recorded during entrypoint interpretation."""

    id: str
    graph_name: str


@dataclass(frozen=True)
class _WorkflowToolRef:
    """Unresolved AgentNode workflow-tool graph config."""

    name: str
    description: str
    graph_name: str
    output_path: str | None = None
    return_direct: bool = False


def compile_workflow_to_ir(source: str) -> GraphIR:
    """Compile a conforming ``workflow.py`` source string into a frozen IR.

    Raises:
        WorkflowValidationError: When the script violates the grammar or the
            config-value / ``CodeNode`` body contracts (line-referenced).
    """
    try:
        module = ast.parse(source)
    except SyntaxError as exc:
        raise WorkflowValidationError(
            f"could not parse workflow source: {exc.msg}", lineno=exc.lineno
        ) from exc

    validate_grammar(module)
    _ensure_nodes_registered()

    code_classes = _collect_code_node_classes(module)
    entrypoint = _find_entrypoint(module)

    root_name, graphs = _interpret_entrypoint(entrypoint, source, code_classes)
    ir = _workflow_to_ir(root_name, graphs, stack=[])
    validate_ir(ir)
    return ir


def _ensure_nodes_registered() -> None:
    """Import the node package so built-in types resolve in the registry."""
    import orcheo.nodes  # noqa: F401  (registration side effects)


def _collect_code_node_classes(module: ast.Module) -> dict[str, _CodeNodeClass]:
    """Index ``CodeNode`` subclasses by name with their fields and ``run``."""
    classes: dict[str, _CodeNodeClass] = {}
    for stmt in module.body:
        if not isinstance(stmt, ast.ClassDef):
            continue
        defaults: dict[str, Any] = {}
        declared: set[str] = set()
        run_func: RunFunction | None = None
        for member in stmt.body:
            if isinstance(member, ast.AnnAssign) and isinstance(
                member.target, ast.Name
            ):
                declared.add(member.target.id)
                if member.value is not None:
                    defaults[member.target.id] = literal_from_ast(member.value)
            elif isinstance(member, ast.Assign) and isinstance(
                member.targets[0], ast.Name
            ):
                name = member.targets[0].id
                declared.add(name)
                defaults[name] = literal_from_ast(member.value)
            elif isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                run_func = member
        if run_func is None:  # pragma: no cover - guarded by grammar
            raise WorkflowValidationError(
                f"class '{stmt.name}' must define a 'run' method", lineno=stmt.lineno
            )
        classes[stmt.name] = _CodeNodeClass(
            run_func=run_func, defaults=defaults, declared=declared
        )
    return classes


def _find_entrypoint(module: ast.Module) -> RunFunction:
    """Return the validated ``orcheo_workflow`` entrypoint function node."""
    for stmt in module.body:
        if (
            isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
            and stmt.name == ENTRYPOINT_NAME
        ):
            return stmt
    raise WorkflowValidationError(  # pragma: no cover - guarded by grammar
        f"script must define a '{ENTRYPOINT_NAME}' entrypoint"
    )


def _interpret_entrypoint(
    entrypoint: RunFunction,
    source: str,
    code_classes: dict[str, _CodeNodeClass],
) -> tuple[str, dict[str, _Workflow]]:
    """Walk the entrypoint body, lifting each ``StateGraph`` into a workflow."""
    graphs: dict[str, _Workflow] = {}
    node_assignments: dict[str, ast.Call] = {}
    seen_ids: dict[str, set[str]] = {}
    root_name: str | None = None

    for stmt in entrypoint.body:
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
            target = stmt.targets[0]
            if not isinstance(target, ast.Name):  # pragma: no cover - grammar guarded
                continue
            if _is_state_graph(stmt.value):
                if target.id in graphs:
                    raise WorkflowValidationError(
                        f"graph variable '{target.id}' is assigned more than once",
                        lineno=stmt.lineno,
                    )
                graphs[target.id] = _Workflow()
                seen_ids[target.id] = set()
            else:
                node_assignments[target.id] = stmt.value
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            _interpret_graph_call(
                stmt.value,
                source,
                code_classes,
                graphs,
                node_assignments,
                seen_ids,
            )
        elif isinstance(stmt, ast.Return):
            root_name = _return_graph_name(stmt)
    if root_name is None:
        raise WorkflowValidationError(
            "workflow entrypoint must return the assembled graph",
            lineno=getattr(entrypoint, "lineno", None),
        )
    if root_name not in graphs:
        raise WorkflowValidationError(
            f"returned graph '{root_name}' was not assigned from StateGraph(...)",
            lineno=getattr(entrypoint, "lineno", None),
        )
    return root_name, graphs


def _return_graph_name(stmt: ast.Return) -> str:
    """Return the graph variable referenced by ``return graph``/``graph.compile()``."""
    value = stmt.value
    if isinstance(value, ast.Name):
        return value.id
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == "compile"
        and isinstance(value.func.value, ast.Name)
    ):
        return value.func.value.id
    raise WorkflowValidationError(  # pragma: no cover - guarded by grammar
        "workflow entrypoint must return the graph or 'graph.compile()'",
        lineno=getattr(stmt, "lineno", None),
    )


def _workflow_to_ir(
    graph_name: str,
    graphs: dict[str, _Workflow],
    *,
    stack: list[str],
) -> GraphIR:
    """Resolve graph references and return frozen IR for one graph variable."""
    if graph_name in stack:
        cycle = " -> ".join([*stack, graph_name])
        raise WorkflowValidationError(f"nested workflow graph cycle detected: {cycle}")
    workflow = graphs.get(graph_name)
    if workflow is None:
        raise WorkflowValidationError(f"unknown nested workflow graph '{graph_name}'")

    nested_stack = [*stack, graph_name]
    nodes: list[NodeSpec] = []
    for node in workflow.nodes:
        if isinstance(node, _SubgraphNodeRef):
            nodes.append(
                SubgraphNodeSpec(
                    id=node.id,
                    graph=_workflow_to_ir(node.graph_name, graphs, stack=nested_stack),
                )
            )
        elif isinstance(node, BuiltinNodeSpec):
            nodes.append(
                BuiltinNodeSpec(
                    id=node.id,
                    type=node.type,
                    config=_resolve_config_graph_refs(
                        node.config,
                        graphs,
                        stack=nested_stack,
                    ),
                )
            )
        else:
            nodes.append(node)
    return GraphIR(
        entrypoint=_resolve_entrypoint_name(workflow),
        nodes=nodes,
        edges=workflow.edges,
        conditional_edges=workflow.conditional_edges,
    )


def _resolve_config_graph_refs(
    value: Any,
    graphs: dict[str, _Workflow],
    *,
    stack: list[str],
) -> Any:
    """Resolve workflow-tool graph references inside built-in node config."""
    if isinstance(value, _WorkflowToolRef):
        graph = _workflow_to_ir(value.graph_name, graphs, stack=stack).model_dump()
        return {
            IR_CONFIG_KIND_KEY: WORKFLOW_TOOL_CONFIG_KIND,
            "name": value.name,
            "description": value.description,
            "graph": graph,
            "output_path": value.output_path,
            "return_direct": value.return_direct,
        }
    if isinstance(value, dict):
        return {
            key: _resolve_config_graph_refs(nested, graphs, stack=stack)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_config_graph_refs(nested, graphs, stack=stack) for nested in value
        ]
    return value


def _is_state_graph(call: ast.Call) -> bool:
    """Return ``True`` for a ``StateGraph(...)`` construction call."""
    return isinstance(call.func, ast.Name) and call.func.id == "StateGraph"


def _interpret_graph_call(
    call: ast.Call,
    source: str,
    code_classes: dict[str, _CodeNodeClass],
    graphs: dict[str, _Workflow],
    node_assignments: dict[str, ast.Call],
    seen_ids: dict[str, set[str]],
) -> None:
    """Dispatch a ``graph.<method>(...)`` call to its IR translation."""
    func = call.func
    if not isinstance(func, ast.Attribute):  # pragma: no cover - guarded by grammar
        return
    if not isinstance(func.value, ast.Name):  # pragma: no cover - guarded by grammar
        return
    graph_name = func.value.id
    workflow = graphs.get(graph_name)
    if workflow is None:
        raise WorkflowValidationError(
            f"method '{func.attr}' targets '{graph_name}', which is not a "
            "StateGraph variable",
            lineno=getattr(call, "lineno", None),
        )
    method = func.attr
    if method == "add_node":
        _interpret_add_node(
            call,
            source,
            code_classes,
            graphs,
            node_assignments,
            seen_ids[graph_name],
            workflow,
        )
    elif method == "add_edge":
        workflow.edges.append(_interpret_add_edge(call))
    elif method == "add_conditional_edges":
        workflow.conditional_edges.append(_interpret_conditional_edge(call))
    elif method in {"set_entry_point", "set_finish_point"}:
        if method == "set_entry_point":
            workflow.entry_override = _string_arg(call, 0, "set_entry_point")


def _interpret_add_node(
    call: ast.Call,
    source: str,
    code_classes: dict[str, _CodeNodeClass],
    graphs: dict[str, _Workflow],
    node_assignments: dict[str, ast.Call],
    seen_ids: set[str],
    workflow: _Workflow,
) -> None:
    """Translate ``add_node(id, node)`` / ``add_node(node)`` to a node spec."""
    node_id, node_expr = _resolve_add_node_args(call, node_assignments)
    if node_id in seen_ids:
        raise WorkflowValidationError(
            f"duplicate node id '{node_id}'", lineno=getattr(call, "lineno", None)
        )
    seen_ids.add(node_id)

    graph_ref = _resolve_graph_ref_expr(node_expr, graphs)
    if graph_ref is not None:
        workflow.nodes.append(_SubgraphNodeRef(id=node_id, graph_name=graph_ref))
        return

    if not isinstance(node_expr, ast.Call):  # pragma: no cover - guarded below
        raise WorkflowValidationError(
            "add_node target must be a node instance or nested graph",
            lineno=getattr(node_expr, "lineno", None),
        )
    node_call = node_expr
    if not isinstance(node_call.func, ast.Name):
        raise WorkflowValidationError(
            f"node '{node_id}' must be constructed from a node class",
            lineno=getattr(node_call, "lineno", None),
        )
    class_name = node_call.func.id
    if node_call.args:
        raise WorkflowValidationError(
            f"node '{node_id}' must be constructed with keyword arguments only",
            lineno=getattr(node_call, "lineno", None),
        )

    if class_name in code_classes:
        workflow.nodes.append(
            _build_code_spec(node_id, node_call, code_classes[class_name], source)
        )
    else:
        workflow.nodes.append(
            _build_builtin_spec(node_id, class_name, node_call, graphs)
        )


def _resolve_add_node_args(
    call: ast.Call, node_assignments: dict[str, ast.Call]
) -> tuple[str, ast.expr]:
    """Return the ``(id, node_call)`` for an ``add_node`` call."""
    if len(call.args) == 2:
        node_id = _as_string(call.args[0], "add_node id")
        node_expr = _resolve_node_expr(call.args[1], node_assignments)
        return node_id, node_expr
    if len(call.args) == 1:
        node_call = _resolve_node_expr(call.args[0], node_assignments)
        if not isinstance(node_call, ast.Call):
            raise WorkflowValidationError(
                "add_node(node) requires a node constructor with name=...",
                lineno=getattr(node_call, "lineno", None),
            )
        node_id = _name_kwarg(node_call)
        return node_id, node_call
    raise WorkflowValidationError(
        "add_node expects (id, node) or (node)", lineno=getattr(call, "lineno", None)
    )


def _resolve_node_expr(
    expr: ast.expr, node_assignments: dict[str, ast.Call]
) -> ast.expr:
    """Resolve a node expression (inline call, graph ref, or assigned variable)."""
    if isinstance(expr, ast.Call):
        return expr
    if isinstance(expr, ast.Name) and expr.id in node_assignments:
        return node_assignments[expr.id]
    if isinstance(expr, ast.Name):
        return expr
    raise WorkflowValidationError(
        "add_node target must be a node instance or nested graph",
        lineno=getattr(expr, "lineno", None),
    )


def _resolve_graph_ref_expr(
    expr: ast.expr,
    graphs: dict[str, _Workflow],
) -> str | None:
    """Return the referenced graph variable for ``child`` or ``child.compile()``."""
    if isinstance(expr, ast.Name) and expr.id in graphs:
        return expr.id
    if (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and expr.func.attr == "compile"
        and isinstance(expr.func.value, ast.Name)
        and expr.func.value.id in graphs
    ):
        if expr.args or expr.keywords:
            raise WorkflowValidationError(
                "nested graph compile() calls may not pass arguments",
                lineno=getattr(expr, "lineno", None),
            )
        return expr.func.value.id
    return None


def _name_kwarg(node_call: ast.Call) -> str:
    """Return the ``name=`` kwarg value of a single-arg ``add_node`` node."""
    for kw in node_call.keywords:
        if kw.arg == "name":
            return _as_string(kw.value, "node name")
    raise WorkflowValidationError(
        "add_node(node) requires the node to set name=...",
        lineno=getattr(node_call, "lineno", None),
    )


def _build_builtin_spec(
    node_id: str,
    class_name: str,
    node_call: ast.Call,
    graphs: dict[str, _Workflow],
) -> BuiltinNodeSpec:
    """Build a :class:`BuiltinNodeSpec` from a registered node instantiation."""
    from orcheo.nodes.registry import registry

    if registry.get_node(class_name) is None:
        raise WorkflowValidationError(
            f"unknown node type '{class_name}' for node '{node_id}'",
            lineno=getattr(node_call, "lineno", None),
        )
    config = _config_from_kwargs(
        node_call,
        node_id,
        allow_credentials=True,
        graphs=graphs,
    )
    return BuiltinNodeSpec(id=node_id, type=class_name, config=config)


def _build_code_spec(
    node_id: str,
    node_call: ast.Call,
    code_class: _CodeNodeClass,
    source: str,
) -> CodeNodeSpec:
    """Build a :class:`CodeNodeSpec`, extracting and validating its body."""
    from orcheo.sandbox.builtins import validate_body_builtins

    kwargs = _config_from_kwargs(
        node_call,
        node_id,
        allow_credentials=False,
        graphs={},
    )
    config = {**code_class.defaults, **kwargs}
    injected = sorted(code_class.declared | set(kwargs))
    validate_code_body(code_class.run_func, injected=injected, node_id=node_id)
    validate_body_builtins(code_class.run_func, node_id=node_id)
    body = extract_run_body(source, code_class.run_func)
    return CodeNodeSpec(id=node_id, config=config, injected=injected, body=body)


def _config_from_kwargs(
    node_call: ast.Call,
    node_id: str,
    *,
    allow_credentials: bool,
    graphs: dict[str, _Workflow],
) -> dict[str, Any]:
    """Validate and literal-evaluate node constructor kwargs (excluding name)."""
    config: dict[str, Any] = {}
    for kw in node_call.keywords:
        if kw.arg is None:
            raise WorkflowValidationError(
                f"node '{node_id}' may not use ** keyword unpacking",
                lineno=getattr(node_call, "lineno", None),
            )
        if kw.arg == "name":
            continue
        if allow_credentials and kw.arg == "workflow_tools":
            config[kw.arg] = _workflow_tools_from_ast(kw.value, graphs)
            continue
        validate_config_value(
            kw.value, allow_credentials=allow_credentials, where=f"node '{node_id}'"
        )
        config[kw.arg] = literal_from_ast(kw.value)
    return config


def _workflow_tools_from_ast(
    expr: ast.expr,
    graphs: dict[str, _Workflow],
) -> list[_WorkflowToolRef]:
    """Parse ``workflow_tools=[WorkflowTool(...)]`` without executing it."""
    if not isinstance(expr, ast.List | ast.Tuple):
        raise WorkflowValidationError(
            "workflow_tools must be a list of WorkflowTool(...) calls",
            lineno=getattr(expr, "lineno", None),
        )
    return [_workflow_tool_from_ast(item, graphs) for item in expr.elts]


def _workflow_tool_from_ast(
    expr: ast.expr,
    graphs: dict[str, _Workflow],
) -> _WorkflowToolRef:
    """Parse one restricted ``WorkflowTool(...)`` constructor."""
    if not (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        and expr.func.id == "WorkflowTool"
    ):
        raise WorkflowValidationError(
            "workflow_tools entries must be WorkflowTool(...) calls",
            lineno=getattr(expr, "lineno", None),
        )
    if expr.args:
        raise WorkflowValidationError(
            "WorkflowTool must be constructed with keyword arguments only",
            lineno=getattr(expr, "lineno", None),
        )

    allowed = {"name", "description", "graph", "output_path", "return_direct"}
    kwargs = _keyword_map(expr, allowed=allowed, what="WorkflowTool")
    name = _required_string_kwarg(kwargs, "name", "WorkflowTool")
    description = _required_string_kwarg(kwargs, "description", "WorkflowTool")
    graph_expr = _required_kwarg(kwargs, "graph", "WorkflowTool")
    graph_name = _resolve_graph_ref_expr(graph_expr, graphs)
    if graph_name is None:
        raise WorkflowValidationError(
            "WorkflowTool graph must reference a StateGraph variable",
            lineno=getattr(graph_expr, "lineno", None),
        )

    output_path_expr = kwargs.get("output_path")
    output_path = (
        None
        if output_path_expr is None or _is_none(output_path_expr)
        else _as_string(output_path_expr, "WorkflowTool output_path")
    )
    return_direct_expr = kwargs.get("return_direct")
    return_direct = (
        False
        if return_direct_expr is None
        else _as_bool(return_direct_expr, "WorkflowTool return_direct")
    )
    return _WorkflowToolRef(
        name=name,
        description=description,
        graph_name=graph_name,
        output_path=output_path,
        return_direct=return_direct,
    )


def _interpret_add_edge(call: ast.Call) -> EdgeSpec:
    """Translate ``add_edge(source, target)`` to an :class:`EdgeSpec`."""
    if len(call.args) != 2:
        raise WorkflowValidationError(
            "add_edge expects exactly (source, target)",
            lineno=getattr(call, "lineno", None),
        )
    return EdgeSpec(
        source=_vertex_from_expr(call.args[0]),
        target=_vertex_from_expr(call.args[1]),
    )


def _interpret_conditional_edge(call: ast.Call) -> ConditionalEdgeSpec:
    """Translate ``add_conditional_edges(source, {...})`` to a spec."""
    if len(call.args) != 2 or not isinstance(call.args[1], ast.Dict):
        raise WorkflowValidationError(
            "add_conditional_edges expects (source, {path, mapping, default})",
            lineno=getattr(call, "lineno", None),
        )
    source = _as_string(call.args[0], "conditional edge source")
    path, mapping, default = _parse_conditional_config(call.args[1])
    return ConditionalEdgeSpec(
        source=source, path=path, mapping=mapping, default=default
    )


def _parse_conditional_config(
    config: ast.Dict,
) -> tuple[str, dict[str, str], str | None]:
    """Parse the declarative conditional-edge config dict."""
    path: str | None = None
    mapping: dict[str, str] | None = None
    default: str | None = None
    for key, value in zip(config.keys, config.values, strict=True):
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            raise WorkflowValidationError(
                "conditional edge config keys must be string literals",
                lineno=getattr(config, "lineno", None),
            )
        if key.value == "path":
            path = _as_string(value, "conditional edge path")
        elif key.value == "mapping":
            mapping = _parse_conditional_mapping(value)
        elif key.value == "default":
            default = None if _is_none(value) else _vertex_from_expr(value)
        else:
            raise WorkflowValidationError(
                f"unknown conditional edge config key '{key.value}'",
                lineno=getattr(key, "lineno", None),
            )
    if path is None or mapping is None:
        raise WorkflowValidationError(
            "conditional edge config requires 'path' and 'mapping'",
            lineno=getattr(config, "lineno", None),
        )
    return path, mapping, default


def _parse_conditional_mapping(value: ast.expr) -> dict[str, str]:
    """Parse the ``mapping`` dict of a conditional edge config."""
    if not isinstance(value, ast.Dict):
        raise WorkflowValidationError(
            "conditional edge 'mapping' must be a dict literal",
            lineno=getattr(value, "lineno", None),
        )
    mapping: dict[str, str] = {}
    for key, target in zip(value.keys, value.values, strict=True):
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            raise WorkflowValidationError(
                "conditional edge mapping keys must be string literals",
                lineno=getattr(value, "lineno", None),
            )
        mapping[key.value] = _vertex_from_expr(target)
    return mapping


def _vertex_from_expr(expr: ast.expr) -> str:
    """Resolve a vertex expression: a string id or a START/END name."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.Name) and expr.id in _VERTEX_NAMES:
        return _VERTEX_NAMES[expr.id]
    raise WorkflowValidationError(
        "edge endpoints must be node-id strings or START/END",
        lineno=getattr(expr, "lineno", None),
    )


def _as_string(expr: ast.expr, what: str) -> str:
    """Return the string value of a string-literal expression."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    raise WorkflowValidationError(
        f"{what} must be a string literal", lineno=getattr(expr, "lineno", None)
    )


def _as_bool(expr: ast.expr, what: str) -> bool:
    """Return the boolean value of a boolean-literal expression."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, bool):
        return expr.value
    raise WorkflowValidationError(
        f"{what} must be a boolean literal", lineno=getattr(expr, "lineno", None)
    )


def _keyword_map(
    call: ast.Call,
    *,
    allowed: set[str],
    what: str,
) -> dict[str, ast.expr]:
    """Return keyword arguments, rejecting unpacking, duplicates, and unknowns."""
    values: dict[str, ast.expr] = {}
    for kw in call.keywords:
        if kw.arg is None:
            raise WorkflowValidationError(
                f"{what} may not use ** keyword unpacking",
                lineno=getattr(call, "lineno", None),
            )
        if kw.arg not in allowed:
            raise WorkflowValidationError(
                f"{what} keyword '{kw.arg}' is not supported in restricted mode",
                lineno=getattr(kw, "lineno", None),
            )
        if kw.arg in values:
            raise WorkflowValidationError(
                f"{what} keyword '{kw.arg}' is duplicated",
                lineno=getattr(kw, "lineno", None),
            )
        values[kw.arg] = kw.value
    return values


def _required_kwarg(
    kwargs: dict[str, ast.expr],
    name: str,
    what: str,
) -> ast.expr:
    """Return a required keyword expression."""
    value = kwargs.get(name)
    if value is None:
        raise WorkflowValidationError(f"{what} requires '{name}'")
    return value


def _required_string_kwarg(
    kwargs: dict[str, ast.expr],
    name: str,
    what: str,
) -> str:
    """Return a required string keyword literal."""
    return _as_string(_required_kwarg(kwargs, name, what), f"{what} {name}")


def _string_arg(call: ast.Call, index: int, what: str) -> str:
    """Return a positional string-literal argument of ``call``."""
    if len(call.args) <= index:
        raise WorkflowValidationError(
            f"{what} requires a string argument", lineno=getattr(call, "lineno", None)
        )
    return _as_string(call.args[index], what)


def _is_none(expr: ast.expr) -> bool:
    """Return ``True`` for a ``None`` literal."""
    return isinstance(expr, ast.Constant) and expr.value is None


def _resolve_entrypoint_name(workflow: _Workflow) -> str:
    """Derive the IR entrypoint from a START edge or ``set_entry_point``."""
    for edge in workflow.edges:
        if edge.source == START_VERTEX:
            return edge.target
    if workflow.entry_override is not None:
        return workflow.entry_override
    raise WorkflowValidationError(
        "workflow has no entrypoint; add an edge from START or call set_entry_point"
    )


__all__ = ["compile_workflow_to_ir"]
