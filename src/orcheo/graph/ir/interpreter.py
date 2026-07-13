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
from importlib import import_module
from typing import Any
from pydantic import BaseModel
from orcheo.graph.ir.builder import MAX_GRAPH_DEPTH, validate_ir
from orcheo.graph.ir.code_body import RunFunction, extract_run_body, validate_code_body
from orcheo.graph.ir.config_values import literal_from_ast, validate_config_value
from orcheo.graph.ir.exceptions import WorkflowValidationError
from orcheo.graph.ir.grammar import ENTRYPOINT_NAME, validate_grammar
from orcheo.graph.ir.models import (
    END_VERTEX,
    IR_CONFIG_KIND_KEY,
    PYDANTIC_MODEL_CONFIG_KIND,
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
from orcheo.graph.ir.schemas import is_schema_class, schema_json_schema


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
    graph_ref: str | GraphIR
    args_schema: dict[str, Any] | None = None
    output_path: str | None = None
    return_direct: bool = False


@dataclass
class _CompileContext:
    """Shared module-level context for restricted AST interpretation."""

    source: str
    code_classes: dict[str, _CodeNodeClass]
    graph_builders: dict[str, RunFunction]
    schema_classes: dict[str, ast.ClassDef]
    imported_symbols: dict[str, tuple[str, str]] = field(default_factory=dict)
    helper_graph_irs: dict[str, GraphIR] = field(default_factory=dict)
    helper_graph_stack: list[str] = field(default_factory=list)


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
    graph_builders = _collect_graph_builders(module)
    schema_classes = _collect_schema_classes(module)
    imported_symbols = _collect_imported_symbols(module)
    entrypoint = _find_entrypoint(graph_builders)
    ctx = _CompileContext(
        source=source,
        code_classes=code_classes,
        graph_builders=graph_builders,
        schema_classes=schema_classes,
        imported_symbols=imported_symbols,
    )

    root_name, graphs = _interpret_graph_builder(entrypoint, ctx)
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
        if not isinstance(stmt, ast.ClassDef) or is_schema_class(stmt):
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


def _collect_graph_builders(module: ast.Module) -> dict[str, RunFunction]:
    """Collect all validated zero-argument graph-builder functions."""
    return {
        stmt.name: stmt
        for stmt in module.body
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _collect_schema_classes(module: ast.Module) -> dict[str, ast.ClassDef]:
    """Collect restricted-mode ``BaseModel`` schema classes by name."""
    return {
        stmt.name: stmt
        for stmt in module.body
        if isinstance(stmt, ast.ClassDef) and is_schema_class(stmt)
    }


def _collect_imported_symbols(module: ast.Module) -> dict[str, tuple[str, str]]:
    """Map imported local names to their ``(module, attribute)`` origin.

    Grammar validation already restricts these imports to Orcheo modules, so
    resolving one here only ever imports trusted first-party code, never
    author-controlled workflow logic.
    """
    imports: dict[str, tuple[str, str]] = {}
    for stmt in module.body:
        if not isinstance(stmt, ast.ImportFrom) or stmt.module is None:
            continue
        for alias in stmt.names:
            imports[alias.asname or alias.name] = (stmt.module, alias.name)
    return imports


def _find_entrypoint(graph_builders: dict[str, RunFunction]) -> RunFunction:
    """Return the validated ``orcheo_workflow`` entrypoint function node."""
    entrypoint = graph_builders.get(ENTRYPOINT_NAME)
    if entrypoint is not None:
        return entrypoint
    raise WorkflowValidationError(  # pragma: no cover - guarded by grammar
        f"script must define a '{ENTRYPOINT_NAME}' entrypoint"
    )


def _interpret_graph_builder(
    builder: RunFunction,
    ctx: _CompileContext,
) -> tuple[str, dict[str, _Workflow]]:
    """Walk one graph-builder body, lifting ``StateGraph`` values into a workflow."""
    graphs: dict[str, _Workflow] = {}
    node_assignments: dict[str, ast.Call] = {}
    seen_ids: dict[str, set[str]] = {}
    root_name: str | None = None

    for stmt in builder.body:
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
                ctx,
                graphs,
                node_assignments,
                seen_ids,
            )
        elif isinstance(stmt, ast.Return):
            # Match Python execution semantics: statements after the entrypoint's
            # return are unreachable, so stop interpreting here. Folding trailing
            # add_node/add_edge calls into the IR would make restricted ingestion
            # persist a graph the authored script would never actually build.
            root_name = _return_graph_name(stmt)
            break
    if root_name is None:
        raise WorkflowValidationError(
            f"graph builder '{builder.name}' must return the assembled graph",
            lineno=getattr(builder, "lineno", None),
        )
    if root_name not in graphs:
        raise WorkflowValidationError(
            f"returned graph '{root_name}' was not assigned from StateGraph(...)",
            lineno=getattr(builder, "lineno", None),
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
    if len(stack) >= MAX_GRAPH_DEPTH:
        raise WorkflowValidationError(
            f"nested workflow depth exceeds the maximum of {MAX_GRAPH_DEPTH}"
        )
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
        graph = (
            _workflow_to_ir(value.graph_ref, graphs, stack=stack).model_dump()
            if isinstance(value.graph_ref, str)
            else value.graph_ref.model_dump()
        )
        config = {
            IR_CONFIG_KIND_KEY: WORKFLOW_TOOL_CONFIG_KIND,
            "name": value.name,
            "description": value.description,
            "graph": graph,
            "output_path": value.output_path,
            "return_direct": value.return_direct,
        }
        if value.args_schema is not None:
            config["args_schema"] = value.args_schema
        return config
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
    ctx: _CompileContext,
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
            ctx,
            graphs,
            node_assignments,
            seen_ids[graph_name],
            workflow,
        )
    elif method == "add_edge":
        workflow.edges.append(_interpret_add_edge(call))
    elif method == "add_conditional_edges":
        workflow.conditional_edges.append(_interpret_conditional_edge(call))
    elif method == "set_entry_point":
        workflow.entry_override = _string_arg(call, 0, "set_entry_point")
    elif method == "set_finish_point":
        # LangGraph's set_finish_point(n) is sugar for add_edge(n, END); mirror
        # it as an explicit END edge so the frozen IR carries the authored finish
        # wiring instead of silently dropping it.
        workflow.edges.append(
            EdgeSpec(
                source=_string_arg(call, 0, "set_finish_point"),
                target=END_VERTEX,
            )
        )


def _interpret_add_node(
    call: ast.Call,
    ctx: _CompileContext,
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

    if class_name in ctx.code_classes:
        workflow.nodes.append(
            _build_code_spec(
                node_id,
                node_call,
                ctx.code_classes[class_name],
                ctx.source,
            )
        )
    else:
        workflow.nodes.append(
            _build_builtin_spec(node_id, class_name, node_call, graphs, ctx)
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
    ctx: _CompileContext,
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
        ctx=ctx,
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
        ctx=None,
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
    ctx: _CompileContext | None,
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
            if ctx is None:  # pragma: no cover - only built-ins accept workflow tools
                raise WorkflowValidationError(
                    f"node '{node_id}' cannot use workflow_tools here",
                    lineno=getattr(kw, "lineno", None),
                )
            config[kw.arg] = _workflow_tools_from_ast(kw.value, graphs, ctx)
            continue
        if allow_credentials and kw.arg == "response_format":
            if ctx is not None:  # pragma: no branch - allow_credentials implies ctx
                config[kw.arg] = _schema_or_literal_from_ast(kw.value, ctx)
                continue
        config[kw.arg] = _config_value_from_ast(
            kw.value,
            allow_credentials=allow_credentials,
            where=f"node '{node_id}'",
            ctx=ctx,
        )
    return config


def _workflow_tools_from_ast(
    expr: ast.expr,
    graphs: dict[str, _Workflow],
    ctx: _CompileContext,
) -> list[_WorkflowToolRef]:
    """Parse ``workflow_tools=[...]`` without executing workflow author code."""
    if not isinstance(expr, ast.List | ast.Tuple):
        raise WorkflowValidationError(
            "workflow_tools must be a list of WorkflowTool(...) calls or dicts",
            lineno=getattr(expr, "lineno", None),
        )
    return [_workflow_tool_from_ast(item, graphs, ctx) for item in expr.elts]


def _workflow_tool_from_ast(
    expr: ast.expr,
    graphs: dict[str, _Workflow],
    ctx: _CompileContext,
) -> _WorkflowToolRef:
    """Parse one restricted workflow-tool declaration."""
    kwargs = _workflow_tool_kwargs(expr)
    name = _required_string_kwarg(kwargs, "name", "WorkflowTool")
    description = _required_string_kwarg(kwargs, "description", "WorkflowTool")
    graph_expr = _required_kwarg(kwargs, "graph", "WorkflowTool")
    graph_ref = _workflow_tool_graph_ref(graph_expr, graphs, ctx)

    output_path_expr = kwargs.get("output_path")
    output_path = (
        None
        if output_path_expr is None or _is_none(output_path_expr)
        else _as_string(output_path_expr, "WorkflowTool output_path")
    )
    args_schema_expr = kwargs.get("args_schema")
    args_schema = (
        None
        if args_schema_expr is None or _is_none(args_schema_expr)
        else _schema_or_literal_from_ast(args_schema_expr, ctx)
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
        graph_ref=graph_ref,
        args_schema=args_schema,
        output_path=output_path,
        return_direct=return_direct,
    )


def _workflow_tool_kwargs(expr: ast.expr) -> dict[str, ast.expr]:
    """Return workflow-tool keyword mappings from ``WorkflowTool(...)`` or dicts."""
    allowed = {
        "name",
        "description",
        "graph",
        "args_schema",
        "output_path",
        "return_direct",
    }
    if (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        and expr.func.id == "WorkflowTool"
    ):
        if expr.args:
            raise WorkflowValidationError(
                "WorkflowTool must be constructed with keyword arguments only",
                lineno=getattr(expr, "lineno", None),
            )
        return _keyword_map(expr, allowed=allowed, what="WorkflowTool")
    if isinstance(expr, ast.Dict):
        return _dict_keyword_map(expr, allowed=allowed, what="WorkflowTool")
    raise WorkflowValidationError(
        "workflow_tools entries must be WorkflowTool(...) calls or dict literals",
        lineno=getattr(expr, "lineno", None),
    )


def _workflow_tool_graph_ref(
    expr: ast.expr,
    graphs: dict[str, _Workflow],
    ctx: _CompileContext,
) -> str | GraphIR:
    """Return the graph variable or helper graph IR for a workflow tool."""
    graph_name = _resolve_graph_ref_expr(expr, graphs)
    if graph_name is not None:
        return graph_name
    if (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        and expr.func.id in ctx.graph_builders
    ):
        if expr.args or expr.keywords:
            raise WorkflowValidationError(
                "workflow-tool graph builder calls may not pass arguments",
                lineno=getattr(expr, "lineno", None),
            )
        return _helper_graph_ir(expr.func.id, ctx)
    raise WorkflowValidationError(
        "WorkflowTool graph must reference a StateGraph variable or helper "
        "graph builder",
        lineno=getattr(expr, "lineno", None),
    )


def _helper_graph_ir(name: str, ctx: _CompileContext) -> GraphIR:
    """Compile and cache a helper graph-builder function to nested IR."""
    cached = ctx.helper_graph_irs.get(name)
    if cached is not None:
        return cached
    builder = ctx.graph_builders.get(name)
    if builder is None:  # pragma: no cover - guarded by caller's membership check
        raise WorkflowValidationError(f"unknown graph builder '{name}'")
    # Guard against self- or mutually-referencing helper builders: without an
    # in-progress marker the cache is only populated after interpretation
    # returns, so a cycle would recurse until an uncaught ``RecursionError``
    # instead of a clean validation error on the untrusted ingestion path.
    if name in ctx.helper_graph_stack:
        cycle = " -> ".join([*ctx.helper_graph_stack, name])
        raise WorkflowValidationError(
            f"recursive workflow-tool graph builder cycle detected: {cycle}"
        )
    ctx.helper_graph_stack.append(name)
    try:
        root_name, graphs = _interpret_graph_builder(builder, ctx)
        ir = _workflow_to_ir(root_name, graphs, stack=[])
    finally:
        ctx.helper_graph_stack.pop()
    ctx.helper_graph_irs[name] = ir
    return ir


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


def _dict_keyword_map(
    expr: ast.Dict,
    *,
    allowed: set[str],
    what: str,
) -> dict[str, ast.expr]:
    """Return dict-literal keys as keyword mappings with restricted validation."""
    values: dict[str, ast.expr] = {}
    for key, value in zip(expr.keys, expr.values, strict=True):
        if key is None:
            raise WorkflowValidationError(
                f"{what} may not use dict unpacking",
                lineno=getattr(expr, "lineno", None),
            )
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise WorkflowValidationError(
                f"{what} dict keys must be string literals",
                lineno=getattr(key, "lineno", None),
            )
        if key.value not in allowed:
            raise WorkflowValidationError(
                f"{what} key '{key.value}' is not supported in restricted mode",
                lineno=getattr(key, "lineno", None),
            )
        if key.value in values:
            raise WorkflowValidationError(
                f"{what} key '{key.value}' is duplicated",
                lineno=getattr(key, "lineno", None),
            )
        values[key.value] = value
    return values


def _schema_or_literal_from_ast(expr: ast.expr, ctx: _CompileContext) -> Any:
    """Return a schema JSON Schema mapping or a validated literal config value."""
    if isinstance(expr, ast.Name):
        if expr.id in ctx.schema_classes:
            return schema_json_schema(expr.id, ctx.schema_classes)
        imported_schema = _imported_schema_json_schema(expr.id, ctx)
        if imported_schema is not None:
            return imported_schema
    validate_config_value(expr, allow_credentials=False, where="schema config")
    return literal_from_ast(expr)


def _resolve_imported_symbol(module_name: str, attr_name: str) -> Any:
    """Import a grammar-approved Orcheo symbol, or raise a clean validation error.

    Grammar validation already restricts imports to the ``orcheo`` namespace, so
    this only ever imports trusted first-party modules. A missing or
    unimportable module is an invalid upload (author error), not a server fault:
    convert the ``ImportError`` into a :class:`WorkflowValidationError` so
    ingestion of an untrusted script fails as a clean rejection instead of an
    unhandled 500. Without this guard a script that imports a non-existent
    ``orcheo.<...>`` module and uses the name as a schema / model reference
    crashes restricted-mode ingestion.
    """
    try:
        module = import_module(module_name)
    except ImportError as exc:
        raise WorkflowValidationError(
            f"cannot import '{module_name}.{attr_name}'; "
            f"'{module_name}' is not an available Orcheo module"
        ) from exc
    return getattr(module, attr_name, None)


def _imported_schema_json_schema(
    name: str, ctx: _CompileContext
) -> dict[str, Any] | None:
    """Resolve ``name`` to a JSON Schema if it's a ``BaseModel`` imported from Orcheo.

    Only names imported from an Orcheo module are considered (grammar
    validation already forbids any other import source), so this imports
    trusted first-party classes only, never tenant-authored code.
    """
    origin = ctx.imported_symbols.get(name)
    if origin is None:
        return None
    module_name, attr_name = origin
    resolved = _resolve_imported_symbol(module_name, attr_name)
    if isinstance(resolved, type) and issubclass(resolved, BaseModel):
        return resolved.model_json_schema()
    return None


def _imported_model_ref_from_ast(
    expr: ast.expr, ctx: _CompileContext
) -> dict[str, str] | None:
    """Return an IR marker for a trusted Orcheo-imported Pydantic model class."""
    if not isinstance(expr, ast.Name):
        return None
    origin = ctx.imported_symbols.get(expr.id)
    if origin is None:
        return None
    module_name, attr_name = origin
    resolved = _resolve_imported_symbol(module_name, attr_name)
    if (
        isinstance(resolved, type)
        and resolved is not BaseModel
        and issubclass(resolved, BaseModel)
    ):
        return {
            IR_CONFIG_KIND_KEY: PYDANTIC_MODEL_CONFIG_KIND,
            "module": module_name,
            "name": attr_name,
        }
    return None


def _config_value_from_ast(
    expr: ast.expr,
    *,
    allow_credentials: bool,
    where: str,
    ctx: _CompileContext | None,
) -> Any:
    """Return a config value, allowing trusted model refs in built-in config."""
    if allow_credentials and ctx is not None:
        model_ref = _imported_model_ref_from_ast(expr, ctx)
        if model_ref is not None:
            return model_ref
    if isinstance(expr, ast.List):
        return [
            _config_value_from_ast(
                element,
                allow_credentials=allow_credentials,
                where=where,
                ctx=ctx,
            )
            for element in expr.elts
        ]
    if isinstance(expr, ast.Tuple):
        return tuple(
            _config_value_from_ast(
                element,
                allow_credentials=allow_credentials,
                where=where,
                ctx=ctx,
            )
            for element in expr.elts
        )
    if isinstance(expr, ast.Dict):
        return _config_dict_from_ast(
            expr,
            allow_credentials=allow_credentials,
            where=where,
            ctx=ctx,
        )
    validate_config_value(expr, allow_credentials=allow_credentials, where=where)
    return literal_from_ast(expr)


def _config_dict_from_ast(
    expr: ast.Dict,
    *,
    allow_credentials: bool,
    where: str,
    ctx: _CompileContext | None,
) -> dict[str, Any]:
    """Return a config dict, preserving validator errors for unsupported keys."""
    config: dict[str, Any] = {}
    for key, value in zip(expr.keys, expr.values, strict=True):
        if key is None:
            validate_config_value(
                expr, allow_credentials=allow_credentials, where=where
            )
            raise AssertionError("unreachable")  # pragma: no cover - defensive
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            validate_config_value(
                expr, allow_credentials=allow_credentials, where=where
            )
            raise AssertionError("unreachable")  # pragma: no cover - defensive
        config[key.value] = _config_value_from_ast(
            value,
            allow_credentials=allow_credentials,
            where=where,
            ctx=ctx,
        )
    return config


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
    """Derive the IR entrypoint from a START edge or ``set_entry_point``.

    An explicit ``START`` edge takes precedence over ``set_entry_point`` when
    both are present, matching LangGraph (the explicit edge wins).
    """
    for edge in workflow.edges:
        if edge.source == START_VERTEX:
            return edge.target
    if workflow.entry_override is not None:
        return workflow.entry_override
    raise WorkflowValidationError(
        "workflow has no entrypoint; add an edge from START or call set_entry_point"
    )


__all__ = ["compile_workflow_to_ir"]
