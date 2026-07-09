# Sandboxed Custom Workflows

Orcheo can accept user-uploaded `workflow.py` files safely by construction
through a two-layer model:

- **Definition layer.** In restricted mode an uploaded `workflow.py` is reduced
  to a frozen, validated **intermediate representation (IR)** by interpreting an
  allowlisted AST. *No author code runs at ingestion* — the IR is the only
  artifact persisted and executed.
- **Execution layer.** The only user-authored logic that survives is each
  `CodeNode` body. It runs per invocation inside a **MicroPython-WASM sandbox**
  with builtins only and JSON-coercible inputs/outputs, isolated from the host.

Enforcement is optional and controlled by
[`ORCHEO_WORKFLOW_DEFINITION_MODE`](environment_variables.md). The default is
`unrestricted` (today's behaviour) so local and self-hosted development keeps
zero friction.

## Definition modes

| Mode | Tenant-safe | Behaviour |
| --- | --- | --- |
| `restricted` | Yes | Every `workflow.py` must compile to the IR; ingestion runs no author code; execution rebuilds the graph from the IR and runs `CodeNode` bodies in the sandbox. |
| `unrestricted` (default) | **No** | `workflow.py` executes in-process with full builtins (`load_graph_from_script`); no IR, no sandbox. For local/self-hosted development only. |

Set the mode with the environment variable:

```bash
# Multi-tenant production
export ORCHEO_WORKFLOW_DEFINITION_MODE=restricted

# Local development (default)
export ORCHEO_WORKFLOW_DEFINITION_MODE=unrestricted
```

The active mode is logged once at startup. Unrestricted mode is logged as a
warning because it executes arbitrary author code in-process and provides **no
tenant isolation**. The runtime default (when the variable is unset) is
`unrestricted`. `orcheo install` now writes `restricted` automatically for
trusted HTTPS backends, while local-hosting and untrusted-HTTP deployments
still get `unrestricted`; see
[`ORCHEO_WORKFLOW_DEFINITION_MODE`](environment_variables.md) for the full
per-topology breakdown.

## The restricted grammar

A conforming `workflow.py` is purely declarative. Only these constructs are
allowed at module level:

- **Orcheo imports only.** Graph symbols come from the Orcheo re-export:
  `from orcheo.graph import StateGraph, START, END`. Non-Orcheo imports
  (including `langgraph`, `typing`, `__future__`, `collections.abc`, `datetime`,
  `json`, …) are rejected.
- **`CodeNode` subclasses** — `class X(CodeNode): ...` with a single `run`
  method and optional configurable fields (see below).
- **`BaseModel` schema classes** — `class X(BaseModel): ...` (from
  `from orcheo.schema import BaseModel, Field`) declaring workflow-tool input
  schemas. Field declarations are extracted with `ast.literal_eval` and lowered
  to JSON Schema; no author code is executed (see *Workflow tools* below).
- **The `orcheo_workflow` entrypoint** — a zero-argument function named
  `orcheo_workflow` (`def` or `async def`) is required. Additional zero-argument
  *graph-builder* functions may also be declared and referenced from a
  `WorkflowTool(graph=...)` (see below). An `async def` builder is *interpreted,
  not awaited*.

Inside `orcheo_workflow` only graph assembly is allowed:

| Construct | Becomes in the IR |
| --- | --- |
| `graph = StateGraph(State)` | the graph (state schema is fixed to `orcheo.graph.state.State`) |
| `child = StateGraph(State)` plus `graph.add_node("child", child.compile())` | `SubgraphNodeSpec{graph}` |
| `SomeBuiltinNode(name=..., **config)` | `BuiltinNodeSpec{type, config}` |
| `MyCodeNode(name=..., **config)` | `CodeNodeSpec{config, injected, body}` |
| `WorkflowTool(name=..., description=..., graph=child)` inside `workflow_tools=[...]` | an IR-backed workflow-tool config marker |
| `graph.add_node(id, node)` / `add_node(node)` | a node binding |
| `graph.add_edge(a, b)` | `EdgeSpec` |
| `graph.add_conditional_edges(src, {"path", "mapping", "default"})` | `ConditionalEdgeSpec` |
| `graph.set_entry_point(id)` / `add_edge(START, id)` | the entrypoint |
| `graph.set_finish_point(id)` | an `EdgeSpec` to `END` (same as `add_edge(id, END)`) |
| `return graph` / `return graph.compile()` | resolves the graph |

Everything else is rejected with an actionable, line-referenced error:
arbitrary statements, loops, conditionals, decorators, metaclasses,
`default_factory` callables, comprehensions, lambdas, dunder/underscore
attribute access (`__class__`, `__subclasses__`, …), dynamic subscripts, starred
arguments, and raw functions used as nodes.

Restricted workflow tools are intentionally narrow: `WorkflowTool` accepts
literal `name`, `description`, optional `output_path`, optional `return_direct`,
a `graph` that references either a `StateGraph` variable declared in the same
builder or a zero-argument graph-builder function called inline (e.g.
`graph=build_lookup()`), and an optional `args_schema` referencing a restricted
`BaseModel` schema class. The schema class is validated and lowered to JSON
Schema without executing author code; recursive/mutually-recursive graph-builder
references are rejected with a `WorkflowValidationError`.

> The boundary is the **allowlist**, not a denylist: anything not explicitly
> permitted is rejected, and no author code is executed during ingestion.

## Config-value vocabulary

Node config values may be:

| Form | Built-in node config | `CodeNode` config | Resolved by | When |
| --- | --- | --- | --- | --- |
| JSON literal | ✅ | ✅ | — | static |
| `{{state.path}}` template | ✅ | ✅ | trusted decoder | run time (before marshalling into the sandbox) |
| `[[credential]]` placeholder | ✅ | ❌ rejected at ingest | vault | run time (built-in only) |

`{{state.path}}` templates stay inert strings in the IR and are resolved at run
time. `[[credential]]` placeholders are **rejected anywhere in a `CodeNode`'s
config**, so the sandbox never receives resolved secrets.

## The `CodeNode` authoring contract

`CodeNode` is the only inheritable base class for custom logic. A body is a
**pure synchronous transform** over JSON-coercible data:

```python
from orcheo.graph import StateGraph, START, END
from orcheo.graph.state import State
from orcheo.nodes import CodeNode


class Grade(CodeNode):
    threshold: int = 5            # a configurable, sandbox-injected field

    async def run(self, state, config):
        score = state["node_results"]["score"]["value"]
        verdict = "pass" if score >= self.threshold else "fail"
        return {"verdict": verdict}   # stored under node_results.grade


async def orcheo_workflow() -> StateGraph:
    graph = StateGraph(State)
    graph.add_node("grade", Grade(name="grade", threshold=5))
    graph.add_edge(START, "grade")
    graph.add_edge("grade", END)
    return graph
```

Rules enforced at ingestion (line-referenced):

- **No imports** and **no `await`/async** constructs inside the body.
- **No dunder names or attributes** (`__builtins__`, `__class__`,
  `__subclasses__`, `__globals__`, …), which would otherwise let a body recover
  builtins/imports the allowlist blocks.
- The body **returns this node's result payload**. Runtime execution wraps it
  under `node_results.<name>`, matching `TaskNode`. At least one `return <value>` must
  appear in `run` itself, not only inside a nested helper function.
- It may reference only its **injected fields** (`self.<field>`) plus the passed
  `state` and `config`.
- It may use only **supported MicroPython builtins** (see below).

### Sandbox I/O envelope

The sandbox receives a JSON envelope and returns one:

```json
// input  (host -> sandbox)
{ "state": { ... }, "config": { "configurable": { ... } }, "configurable": { "threshold": 5 } }

// output (sandbox -> host)
{ "update": { "node_results": { "verdict": "pass" } } }      // success
{ "error":  { "type": "ValueError", "message": "..." } } // body raised
```

Non-JSON-serialisable top-level state values are dropped from the projection by
default (and logged); set the marshalling policy to raise instead if you prefer
to fail fast. Injected configurable fields must always be JSON-coercible.

### Supported builtins

`CodeNode` bodies are validated at ingestion against the builtins supported by
the pinned MicroPython-WASM artifact. Allowed names include common builtins and
exceptions (e.g. `len`, `range`, `sorted`, `int`, `str`, `dict`, `list`, `min`,
`max`, `sum`, `enumerate`, `isinstance`, `ValueError`, `KeyError`, …). The
following are **rejected**:

- Builtins absent from the artifact, e.g. `format`, `vars`.
- Dynamic/dangerous builtins, e.g. `eval`, `exec`, `compile`, `open`, `getattr`,
  `setattr`, `delattr`, `globals`, `locals`, `dir`.
- `print`/`input` (they would corrupt the sandbox's stdout JSON protocol).

## Migrating an existing `workflow.py` to restricted mode

Workflows written for unrestricted mode typically need these documented
authoring changes:

1. **Source graph symbols from Orcheo**:
   `from langgraph.graph import ...` → `from orcheo.graph import StateGraph, START, END`.
2. **Remove non-Orcheo imports** (`typing`, `__future__`, `collections.abc`,
   `datetime`, `json`, `html`, `asyncio`, …). Type annotations that need them
   should be dropped or written as plain strings.
3. **Replace raw-function nodes with `CodeNode` subclasses.** Plain functions and
   lambdas cannot be nodes.
4. **Use the fixed `State` schema** (`StateGraph(State)`); custom state classes
   such as `StateGraph(dict)` are not supported.
5. **Name the entrypoint `orcheo_workflow`** and remove any `if __name__ ==
   "__main__":` block or other top-level statements.

## Operating in multi-tenant production

The MicroPython-WASM sandbox is **one layer of defense, not a complete security
boundary**. For high-risk multi-tenant production:

- Set `ORCHEO_WORKFLOW_DEFINITION_MODE=restricted` explicitly.
- Add **OS-level hardening behind the sandbox** for the worker/backend
  processes: run them as an unprivileged user; apply `seccomp`/`AppArmor`/
  SELinux profiles; drop Linux capabilities; restrict the filesystem
  (read-only root, no `/proc` writes); deny outbound network from the sandboxed
  execution path; and apply cgroup CPU/memory limits. The sandbox already runs
  with no network, no filesystem, no inherited environment, and per-invocation
  memory/fuel/wall-clock limits, but OS isolation guards against an artifact-level
  escape.
- Treat the WASM artifact as untrusted-input infrastructure: keep it patched and
  re-validate the allowlist when upgrading.

### Artifact and allowlist version pinning

- The MicroPython-WASM artifact is pinned via the `micropython-wasm==0.1a2`
  dependency, and the builtin allowlist (`orcheo/sandbox/builtins.py`) is derived
  from — and tied to — that exact artifact (`ARTIFACT_VERSION`).
- **When you bump the artifact version, re-probe its builtins and update both
  `ARTIFACT_VERSION` and the allowlist sets together.** A drift between the
  artifact and the allowlist can invalidate previously valid bodies or admit
  unsupported usage.
- The active artifact version and per-invocation limits are surfaced by
  `MicroPythonSandboxRunner.describe()` for diagnostics.
