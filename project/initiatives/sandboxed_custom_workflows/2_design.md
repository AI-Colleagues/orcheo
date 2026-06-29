# Design Document

## For Sandboxed Custom Workflows

- **Version:** 0.1
- **Author:** Claude
- **Date:** 2026-06-29
- **Status:** Draft

---

## Overview

This design makes it safe to accept user-uploaded workflows through a two-layer model. The **definition layer** persists and executes only a frozen, validated intermediate representation (IR) of the workflow graph, rather than re-executing the uploaded `workflow.py`: ingestion parses the script to an AST, validates it against a small declarative grammar, and *interprets* it into the IR, running no author code in the trusted backend process. The **execution layer** runs the only surviving author logic — each `CodeNode` body — per invocation inside a MicroPython-WASM sandbox with builtins only and JSON-coercible inputs/outputs, isolated from the host.

The key insight for the definition layer is that "data, not code" is a property of the artifact that crosses the trust boundary, not of the authoring format. Authors keep writing `workflow.py`, but the persisted and executed artifact is the IR. Because the grammar is restricted to Orcheo imports, `CodeNode` subclasses, node/edge instantiation, and graph assembly, the script can be interpreted from its AST rather than executed. The execution layer then closes the remaining gap: `CodeNode` bodies are author code, so they never run in the host — they run in a sandbox that has no LangGraph/Orcheo, no imports, no network, no filesystem, and per-invocation resource limits, operating purely as synchronous transforms over JSON-coercible state, config, and configurable fields.

Enforcement is optional. A definition-mode environment variable selects between restricted mode (both layers enforced, safe by construction) and unrestricted mode (today's in-process `workflow.py` execution, intended for local/self-hosted development and providing no tenant isolation). The default at this stage is unrestricted; making restricted the default is a separate follow-up task. This keeps developer friction at zero when iterating locally.

## Components

- **Frozen Workflow IR (Core)**
  - Pydantic models describing nodes, edges, conditional edges, `CodeNode` bodies (as strings), and a fixed state reference.
  - JSON-coercible, versioned (`schema_version`), and round-trippable; the single persisted/executed artifact.

- **Graph Symbol Re-export (Core)**
  - Orcheo re-exports `StateGraph`, `START`, and `END` (e.g. from `orcheo.graph`) so graph-construction imports remain Orcheo-only and the import allowlist holds without a `langgraph` exception.

- **Restricted Grammar Validator (Core)**
  - Validates a `workflow.py` AST against an allowlisted, declarative grammar, including a single zero-argument `orcheo_workflow` entrypoint (`def` or `async def`).
  - Rejects arbitrary statements, decorators, metaclasses, `default_factory` callables, dunder/gadget attribute access, dynamic subscript/attribute access, non-`CodeNode` subclasses, non-Orcheo imports, and entrypoints not named `orcheo_workflow`.

- **Restricted-AST Interpreter (Core)**
  - Compiles a conforming script into the IR by interpreting the validated AST.
  - Maps constructor calls to node specs (resolving classes via the registry/import allowlist, `literal_eval`-ing config), `add_edge`/`add_conditional_edges` to edge specs, and extracts `CodeNode.run` bodies as dedented strings.
  - Never calls `compile`/`eval`/`exec` on author source, imports tenant modules, or invokes tenant entrypoints.

- **Config-Value Validator (Core)**
  - Validates node config values against the vocabulary: JSON literals, `{{state.path}}` templates (anywhere), and `[[credential]]` placeholders (built-in node config only; rejected in `CodeNode` injected config).

- **CodeNode Body Validator (Core)**
  - Structurally validates each extracted body at ingest: no imports, no `await`/async, returns a state-update mapping, references only injected fields plus state/config.
  - Validates the body against the MicroPython builtin allowlist tied to the pinned WASM artifact, so unsupported usage fails at ingestion.

- **Trusted IR Graph Builder (Core / Worker)**
  - Builds a LangGraph `StateGraph` from the IR using trusted registered node constructors and the declarative conditional-edge builder.
  - Wires each `CodeNode` spec to the sandbox runner. Re-validates the IR before building/running. Never re-executes the original script.

- **CodeNode Sandbox Runtime (Core / Worker — MicroPython-WASM)**
  - Executes a `CodeNode` body per invocation via the `micropython_wasm` package.
  - Provides builtins only, no imports, no network, no filesystem, no inherited environment, and enforces memory/fuel and wall-clock limits.
  - Receives a JSON inputs envelope and returns a JSON outputs envelope (state update or error).

- **State Marshaller / Merger (Core / Worker)**
  - Projects node state to JSON (dropping or raising on non-serialisable values per contract), assembles the inputs envelope, and merges the returned update back through the state channel reducers.

- **Definition Mode Config (Backend / Config)**
  - `ORCHEO_WORKFLOW_DEFINITION_MODE` = `restricted` | `unrestricted` (default `unrestricted` at this stage; a later task flips the default to `restricted`).
  - Branches ingestion and execution. Restricted enforces the IR and sandboxed `CodeNode` execution; unrestricted preserves today's in-process `load_graph_from_script` path.

- **Unrestricted Loader (Existing)**
  - The current `load_graph_from_script` path, reachable only in unrestricted mode. Unchanged behaviour; not tenant-safe.

## Request Flows

### Flow 1: Restricted-Mode Ingestion (`workflow.py` → IR)

1. Client uploads a `workflow.py` that defines a zero-argument `orcheo_workflow` entrypoint assembling the graph.
2. Backend confirms `ORCHEO_WORKFLOW_DEFINITION_MODE=restricted`.
3. The script is parsed to an AST (parse only — no execution).
4. The restricted grammar validator rejects any non-declarative construct, non-`CodeNode` subclass, or non-Orcheo import with line-referenced errors.
5. The AST interpreter builds the IR: node specs, edges, conditional edges, and extracted `CodeNode` bodies.
6. The config-value validator and `CodeNode` body validator run (structural + builtin allowlist); violations are rejected.
7. The IR is validated against its schema and stored as the workflow version. No author code has executed.

### Flow 2: Restricted-Mode Execution (IR → run)

1. A trigger/API enqueues a workflow run.
2. The worker loads the stored IR.
3. The trusted IR graph builder re-validates the IR.
4. The builder constructs built-in node instances from registered constructors and JSON config, wires edges and declarative conditional edges, references the fixed `State` schema, and binds each `CodeNode` spec to the sandbox runner.
5. `{{state}}` templates and built-in-node `[[credential]]` placeholders resolve at run time through the normal decoder/vault.
6. Built-in nodes execute in-process as today; each `CodeNode` invocation is delegated to the sandbox (Flow 4).
7. Run history and output persist as today.

### Flow 3: Unrestricted-Mode Ingestion/Execution (local/self-host)

1. Operator sets `ORCHEO_WORKFLOW_DEFINITION_MODE=unrestricted`.
2. Ingestion logs that author code will execute in-process without tenant isolation.
3. `load_graph_from_script` runs the script in-process with full builtins, exactly as today.
4. Execution proceeds on the resulting graph with no IR and no sandbox involved.

### Flow 4: CodeNode Sandbox Invocation

1. The graph reaches a `CodeNode`. The host resolves `{{state}}` templates in the node's injected config (credentials are already disallowed here).
2. The marshaller projects the current node state to JSON, dropping non-serialisable values per contract (logged), and assembles the inputs envelope `{state, config, configurable}`.
3. The sandbox runner starts a fresh MicroPython-WASM session under memory/fuel/timeout limits, with no network/filesystem/env.
4. The runner executes the `CodeNode` body with the envelope bound, capturing the returned state-update mapping.
5. On success, the host validates the output is JSON-coercible and merges it back through the state channel reducers like a vanilla LangGraph node update.
6. On timeout/limit breach/error, the run fails with a structured error attributed to the node, and the failure is logged/metered.

## API Contracts

### Ingest Workflow Version (restricted mode produces an IR)

```http
POST /api/workflows/{workflow_ref}/versions/ingest
Headers:
  Authorization: Bearer <token>
  X-Orcheo-Workspace: <workspace>
Body:
  {
    "script": "python source"        // must define a zero-arg `orcheo_workflow` entrypoint
  }

Response:
  201 Created -> WorkflowVersion (graph stored as frozen IR in restricted mode)
  400 Bad Request -> grammar / config-value / CodeNode-body (structural or builtin) validation failure (line-referenced)
  403 Forbidden -> uploads not permitted by deployment policy
```

### Read Workflow Graph (IR)

```http
GET /api/workflows/{workflow_ref}/versions/{version_id}/graph
Headers:
  Authorization: Bearer <token>
  X-Orcheo-Workspace: <workspace>

Response:
  200 OK -> { "graph": <frozen-workflow-ir> }
  404 Not Found -> version missing
```

## Data Models / Schemas

### Frozen Workflow IR (Pydantic sketch)

```python
from typing import Annotated, Any, Literal, Union
from pydantic import BaseModel, Field


class BuiltinNodeSpec(BaseModel):
    """A registered node (AgentNode, RSSNode, …); code lives in Orcheo, trusted."""
    kind: Literal["builtin"] = "builtin"
    id: str                                   # add_node key
    type: str                                 # registry name, e.g. "AgentNode"
    config: dict[str, Any] = {}               # literal / {{state}} / [[cred]] values


class CodeNodeSpec(BaseModel):
    """User logic — the only place author code exists. Body runs only in the WASM sandbox."""
    kind: Literal["code"] = "code"
    id: str
    config: dict[str, Any] = {}               # configurable fields (no [[cred]] allowed)
    injected: list[str] = []                  # config fields exposed to the body
    body: str                                 # dedented source of run(); for the sandbox


NodeSpec = Annotated[Union[BuiltinNodeSpec, CodeNodeSpec], Field(discriminator="kind")]


class EdgeSpec(BaseModel):
    source: str                               # node id or "__start__"
    target: str                               # node id or "__end__"


class ConditionalEdgeSpec(BaseModel):
    """Mirrors the existing declarative conditional-edge config — no Python callable."""
    source: str
    path: str                                 # dotted state path or named edge instance
    mapping: dict[str, str]                   # condition value -> target id
    default: str | None = None


class GraphIR(BaseModel):
    schema_version: int = 1
    state_ref: str = "orcheo.graph.state.State"   # fixed schema, referenced not redefined
    entrypoint: str
    nodes: list[NodeSpec]
    edges: list[EdgeSpec] = []
    conditional_edges: list[ConditionalEdgeSpec] = []
```

### Example IR (compiled from a conforming `workflow.py`)

```json
{
  "schema_version": 1,
  "state_ref": "orcheo.graph.state.State",
  "entrypoint": "triage",
  "nodes": [
    {
      "kind": "builtin",
      "id": "triage",
      "type": "AgentNode",
      "config": { "ai_model": "claude-opus-4-8", "system_prompt": "Rate this request 0-10." }
    },
    {
      "kind": "code",
      "id": "verdict",
      "config": { "threshold": 8 },
      "injected": ["threshold"],
      "body": "score = state[\"results\"][\"triage\"][\"score\"]\nreturn {\"results\": {\"verdict\": \"pass\" if score >= self.threshold else \"fail\"}}"
    }
  ],
  "edges": [
    { "source": "__start__", "target": "triage" },
    { "source": "triage", "target": "verdict" }
  ],
  "conditional_edges": [
    {
      "source": "verdict",
      "path": "results.verdict",
      "mapping": { "pass": "approve", "fail": "__end__" }
    }
  ]
}
```

### CodeNode Sandbox I/O Envelope

```json
// Input (host -> sandbox)
{
  "state": { "results": { "triage": { "score": 9 } } },   // JSON projection of node state
  "config": { "configurable": {} },                        // run config (JSON-coercible)
  "configurable": { "threshold": 8 }                       // injected self.<field> values
}

// Output (sandbox -> host), success
{ "update": { "results": { "verdict": "pass" } } }         // merged back via state reducers

// Output (sandbox -> host), failure
{ "error": { "type": "TimeoutError", "message": "fuel/time limit exceeded" } }
```

### CodeNode I/O Contract Decisions

| Aspect | Decision |
|--------|----------|
| Injected fields | The `injected` set in `CodeNodeSpec`; exposed to the body as `self.<field>` |
| State input | JSON projection of node state; non-JSON-serialisable values dropped (logged) by default, raise optional |
| Config input | Run config, JSON-coercible |
| Output | A state-update mapping; must be JSON-coercible or the run fails; merged via state reducers |
| Concurrency model | Pure synchronous transform; no `await`, no imports, builtins only |
| Credentials | `[[credential]]` rejected in injected config; sandbox never receives resolved secrets |

### Definition Mode

| Mode | Tenant Safe | Behaviour |
|------|-------------|-----------|
| `restricted` | Yes | `workflow.py` must compile to the IR; runtime executes from the IR only; `CodeNode` bodies run in the MicroPython-WASM sandbox; no author code runs at ingestion |
| `unrestricted` (default at this stage) | No | `workflow.py` executes in-process with full builtins (today's `load_graph_from_script`); no IR, no sandbox; for local/self-hosted development |

> The default is `unrestricted` at this stage; flipping the default to `restricted` is a separate follow-up task.

### Config-Value Vocabulary

| Form | Built-in node config | `CodeNode` injected config | Resolved by | When |
|------|----------------------|----------------------------|-------------|------|
| JSON literal | Allowed | Allowed | — | n/a (static) |
| `{{state.path}}` | Allowed | Allowed | Trusted decoder | Run time (before marshalling into sandbox) |
| `[[credential]]` | Allowed | **Rejected at ingest** | Vault | Run time (built-in only) |

### Restricted Grammar (allowed constructs)

| Construct | Allowed | Becomes in IR |
|-----------|---------|---------------|
| `from orcheo… import …` (incl. re-exported `StateGraph`/`START`/`END`) | Yes | — (resolves names) |
| `class X(CodeNode): …` with a `run` method | Yes | `CodeNodeSpec` (body extracted) |
| Node instantiation `SomeNode(...)` | Yes | `BuiltinNodeSpec{type, config}` |
| `graph.add_node(id, node)` | Yes | node `id` binding |
| `graph.add_edge(a, b)` | Yes | `EdgeSpec` |
| `graph.add_conditional_edges(src, {...})` | Yes | `ConditionalEdgeSpec` |
| `orcheo_workflow` entrypoint (`def`/`async def`), `graph.compile()` / return | Yes | resolves the graph (name fixed to `orcheo_workflow`; `async def` accepted but interpreted, not awaited) |
| Arbitrary statements, decorators, metaclasses, `default_factory` callables | No | rejected |
| Dunder/gadget access (`__bases__`, `__subclasses__`, …), dynamic attr/subscript | No | rejected |
| Non-Orcheo imports, raw callables/lambdas as nodes, non-`CodeNode` subclasses | No | rejected |

## Security Considerations

- The definition-layer boundary in restricted mode is the AST interpreter, not an import denylist: anything not explicitly allowed by the grammar is rejected, and author code is never executed in-process.
- The execution-layer boundary is the MicroPython-WASM sandbox: `CodeNode` bodies run with builtins only, no imports, no network, no filesystem, no inherited environment, and memory/fuel/wall-clock limits. The bundled artifact is experimental and not a complete security boundary by itself; multi-tenant production should add OS-level hardening (process limits/seccomp or equivalent) behind it.
- Validation runs at both ingestion and execution because a stored IR could be created or modified outside the normal ingestion path during development/tests.
- The IR graph builder constructs nodes only via trusted registered constructors with `literal_eval`-ed config; it never evaluates author expressions.
- Credentials never enter `CodeNode` bodies: `[[credential]]` placeholders are rejected in `CodeNode` injected config at ingest, so the sandbox receives only credential-free, JSON-coercible inputs. This closes the data-egress path that the body's execution sandbox does not otherwise cover (the WASM boundary stops code escape, not data egress).
- `{{state.path}}` is a dotted-path lookup resolved by trusted code, not an expression evaluator; it adds no code-execution surface.
- `CodeNode` bodies receive only a JSON projection of their node state — never live host objects, other nodes' internals, or rich runtime types.
- Unrestricted mode executes arbitrary author code in-process and is explicitly not tenant-safe; it must be opt-in and clearly logged.

## Performance Considerations

- Restricted ingestion replaces in-process script execution with parse + AST validation + interpretation, all CPU-bound and bounded by script size; no process/container startup is introduced at the definition layer.
- Each `CodeNode` invocation incurs MicroPython-WASM session startup plus JSON marshalling. MicroPython-WASM is lightweight relative to full CPython/Pyodide, but the runner should support session reuse/pooling where safe and enforce small per-call limits to bound tail latency.
- The IR graph builder adds re-validation plus construction cost at execution, small relative to running the workflow itself.
- The IR is compact and JSON-coercible, cheap to store, diff, and read for previews.
- State projection cost scales with state size; large or deeply nested non-JSON values should be dropped early per contract.

## Testing Strategy

- **Unit tests**
  - IR model validation and JSON round-trip.
  - Grammar validator accepts conforming scripts; rejects decorators, metaclasses, `default_factory` callables, dunder/gadget access, dynamic attr/subscript, non-Orcheo imports, raw-callable/lambda nodes, and non-`CodeNode` subclasses (line-referenced).
  - AST interpreter maps constructors/edges/conditional edges correctly and extracts `CodeNode` bodies verbatim (dedented).
  - Config-value validator: `{{state}}` accepted everywhere; `[[credential]]` accepted in built-in config and rejected in `CodeNode` injected config.
  - `CodeNode` body validator: rejects imports, `await`/async, non-returning bodies, references to non-injected names, and unsupported MicroPython builtins.
  - State marshaller: JSON projection drops/raises non-serialisable values per contract; merge-back respects reducers.

- **Integration tests**
  - Restricted ingestion never `compile`/`eval`/`exec`s author code (assert via instrumentation) and stores an IR.
  - Restricted execution builds and runs a built-in-node graph from the IR without re-executing the script.
  - A `CodeNode` body runs end-to-end in the sandbox, returns an update, and merges into state correctly.
  - Sandbox enforces limits: an infinite-loop or oversized-output body fails with a structured, node-attributed error.
  - Unrestricted mode reproduces today's `load_graph_from_script` behaviour unchanged.
  - Definition-mode parsing defaults to unrestricted at this stage and branches ingestion/execution correctly.

- **Manual QA checklist**
  - A conforming candidate/example workflow ingests to an IR and runs (built-in nodes + a `CodeNode`).
  - A script with a non-`CodeNode` subclass or a lambda node is rejected with a clear message.
  - A `CodeNode` using an unsupported builtin is rejected at ingest; a `CodeNode` config containing `[[credential]]` is rejected at ingest.
  - Switching to unrestricted mode restores raw `workflow.py` execution locally.

## Rollout Plan

1. Add the frozen IR models, the Orcheo graph-symbol re-export, and the trusted IR → `StateGraph` rebuilder (built-in nodes) with re-validation.
2. Implement the restricted grammar validator and AST interpreter, including `CodeNode` extraction and config-value vocabulary.
3. Integrate the MicroPython-WASM `CodeNode` sandbox: I/O envelope, marshalling/merge-back, builtin-allowlist + structural body validation, and resource limits; wire the rebuilder's `CodeNode` specs to the sandbox runner.
4. Add `ORCHEO_WORKFLOW_DEFINITION_MODE` (default `unrestricted` at this stage; a later task flips it to `restricted`) and branch ingestion/execution; keep unrestricted mode equal to today's behaviour.
5. Pressure-test against candidate/example workflows; finalise error messages, audit logs/metrics, OS-level hardening guidance, and grammar/`CodeNode` documentation.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-06-29 | Claude | Initial draft |
