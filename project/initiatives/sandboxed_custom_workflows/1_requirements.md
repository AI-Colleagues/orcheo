# Requirements Document

## METADATA
- **Authors:** Claude
- **Project/Feature Name:** Sandboxed Custom Workflows
- **Type:** Feature
- **Summary:** Let any user upload custom workflows without threatening runtime safety, via a two-layer model. The **definition layer** reduces every uploaded `workflow.py` to a frozen, validated, non-executable intermediate representation (IR) using a restricted-AST interpreter that runs no author code at ingestion. The **execution layer** runs the only user-authored logic — `CodeNode` bodies — per invocation inside a MicroPython-WASM sandbox with builtins-only, JSON-coercible I/O. Enforcement is optional: a definition-mode environment variable lets local/self-hosted deployments keep today's zero-friction `workflow.py` execution unchanged.
- **Owner (if different than authors):** Shaojie Jiang
- **Date Started:** 2026-06-29

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| Related Initiative | `project/initiatives/containerless_trusted_workflows/1_requirements.md` | Shaojie Jiang | Containerless trusted workflows requirements |
| Related Initiative | `project/initiatives/python_only_workflow_composition/1_requirements.md` | Shaojie Jiang | Python-only workflow composition requirements |
| Related Initiative | `project/initiatives/workflow_upload_config/1_requirements.md` | Shaojie Jiang | Workflow upload config requirements |
| Eng Requirement Doc | `project/initiatives/sandboxed_custom_workflows/2_design.md` | Shaojie Jiang | Sandboxed custom workflows design |
| Project Plan | `project/initiatives/sandboxed_custom_workflows/3_plan.md` | Shaojie Jiang | Sandboxed custom workflows plan |

## PROBLEM DEFINITION
### Objectives
Allow untrusted users to author and upload custom workflows safely by persisting and executing only a validated declarative IR (derived from `workflow.py` without executing author code), and by running the only user-authored logic — `CodeNode` bodies — inside an isolated MicroPython-WASM sandbox. Preserve Python authoring ergonomics in the safe path, and keep an explicit, lower-friction unrestricted mode for local and self-hosted development.

### Target users
- Platform operators running Orcheo in shared/multi-tenant deployments who want to accept user-supplied workflows
- Workflow authors who want to keep writing `workflow.py` (including custom `CodeNode` logic) with familiar ergonomics
- Backend and SDK engineers maintaining workflow ingestion, the graph builder, and execution
- Self-hosted and local developers who prioritise zero-friction iteration over isolation

### User Stories
| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| Platform operator | Accept user-uploaded workflows without running their code on ingestion | A malicious or buggy upload cannot compromise the backend process | P0 | In restricted mode, ingestion never `compile`/`eval`/`exec`s author code, never imports tenant modules, and never calls a tenant entrypoint |
| Workflow author | Write `workflow.py` and have it accepted in the safe path | I keep Python ergonomics without learning a separate manifest format | P0 | A conforming `workflow.py` is interpreted into the IR and stored; non-conforming scripts are rejected with actionable, line-referenced errors |
| Security reviewer | Confine all author code to one place and isolate it at runtime | The graph cannot smuggle in arbitrary behaviour and `CodeNode` logic cannot touch the host | P0 | `CodeNode` is the only inheritable base for custom logic; its body executes only inside the MicroPython-WASM sandbox |
| Workflow author | Run custom transform logic in a `CodeNode` | I can add glue/processing without a built-in node for every case | P0 | A `CodeNode` body runs per invocation in the sandbox, receives JSON-coercible inputs, and returns a state update merged like a vanilla LangGraph node |
| Workflow author | Be told at upload time when my `CodeNode` body uses something unsupported | Failures surface at ingestion, not deep inside a run | P0 | `CodeNode` bodies are validated at ingest against the supported MicroPython builtin allowlist (and structurally: no imports, no `await`) |
| Platform operator | Ensure `CodeNode` bodies receive only JSON-coercible, credential-free inputs | Secrets and rich runtime objects cannot leak into or out of user code | P0 | `[[credential]]` placeholders are rejected in `CodeNode` injected config; non-JSON-serialisable state values are handled per the documented contract |
| Workflow author | Reference credentials and upstream state in node config | I can wire real workflows declaratively | P0 | `{{state.path}}` templates are accepted in any node config; `[[credential]]` placeholders are accepted in built-in node config |
| Runtime engineer | Build and run graphs from the IR only | Execution does not re-run author source | P0 | The runtime rebuilds the `StateGraph` from the stored IR via trusted constructors; the original `workflow.py` is never re-executed |
| Local developer | Keep running arbitrary `workflow.py` unchanged | I have the least friction while developing features and nodes | P0 | Setting the definition mode to unrestricted preserves today's in-process script execution with no new restrictions |

### Context, Problems, Opportunities
Today, an uploaded `workflow.py` is executed in-process in CPython with full builtins to build its graph (`load_graph_from_script`), and any custom node logic also runs in-process with full privileges. The only protections are a size limit, a wall-clock timeout, and an upload gate that — by default — blocks client uploads entirely. Blocking non-Orcheo imports would not be sufficient even if added, because construction-time code runs in-process: builtin gadget chains (`().__class__.__bases__`, `__subclasses__`, decorators/metaclasses, `default_factory` callables) are reachable without any import. As a result, the safe posture has been "do not accept untrusted uploads at all," which prevents an entire class of user-customised workflows.

The opportunity is a two-layer model that makes uploads safe by construction:
- **Definition layer.** If the legal vocabulary of a `workflow.py` is small enough to be purely declarative — Orcheo imports, `CodeNode` subclasses, node/edge instantiation, and graph assembly — the script can be *interpreted* from its AST into a frozen IR rather than *executed*. No author code runs in the trusted process.
- **Execution layer.** The only author logic that survives is each `CodeNode` body, carried in the IR as a string. At runtime each body executes per invocation in a MicroPython-WASM sandbox with builtins only and JSON-coercible inputs/outputs, isolated from the host. Because LangGraph and Orcheo node classes are not available (nor needed) inside the sandbox, bodies are pure synchronous transforms over state/config/configurable fields.

Together this keeps the Python authoring experience the project already standardised on, while removing both the in-process construction risk and the in-process execution risk.

### Product goals and Non-goals
Goals:
- Define a frozen, JSON-coercible workflow IR (nodes, edges, conditional edges, `CodeNode` bodies as strings, state reference) as the single persisted, validated, executed artifact.
- Build a restricted-AST interpreter that compiles a conforming `workflow.py` into the IR without executing author code.
- Make `CodeNode` the only inheritable base class for user customisation, and run its body only inside the MicroPython-WASM sandbox.
- Validate the script as declarative (allowlisted grammar), not merely import-filtered.
- Build the trusted IR → `StateGraph` rebuilder used at runtime, wiring `CodeNode` specs to the sandbox runner.
- Define the `CodeNode` sandbox I/O contract: injected configurable fields, JSON-coercible state/config inputs, returned state update merged like a vanilla node, pure synchronous bodies.
- Validate `CodeNode` bodies at definition time against the MicroPython builtin allowlist so unsupported usage fails at ingestion, not mid-run.
- Define the config-value vocabulary (literals, `{{state}}` templates, `[[credential]]` placeholders) and its per-layer rules.
- Make enforcement optional via a definition-mode environment variable, defaulting to unrestricted at this stage (flipping the default to restricted is a separate follow-up task), with restricted as an explicit opt-in and unrestricted equal to today's behaviour.

Non-goals:
- Converting arbitrary, non-conforming `workflow.py` files into the IR via isolated execution (a translation/importer path). Out of scope.
- Backward compatibility or migration for existing script-backed workflow versions; Orcheo is pre-GA and may break unreleased formats.
- Providing tenant isolation in unrestricted mode.
- Allowing non-builtin imports or asynchronous I/O inside `CodeNode` bodies; bodies are pure synchronous transforms. Any I/O is performed by built-in nodes.
- Building a general-purpose Python static analyser that claims to secure arbitrary code.

## PRODUCT DEFINITION
### Requirements

- **P0: Frozen workflow IR**
  - Define a Pydantic IR with: a fixed `state_ref`, an `entrypoint`, a list of nodes, a list of edges, and a list of conditional edges.
  - Nodes are a discriminated union of built-in node specs (`{type, config}`) and `CodeNode` specs (`{config, injected, body}` where `body` is the extracted `run` source as a string).
  - Conditional edges are declarative (`{source, path, mapping, default}`) — no Python callable.
  - The IR must be fully JSON-coercible and round-trippable, and is the only artifact persisted and executed.

- **P0: `CodeNode` is the only customisation port**
  - `CodeNode` is the only inheritable base class for user-defined logic.
  - Reject any class definition whose base is not `CodeNode`; reject any node added to the graph that is neither a registered built-in node nor a `CodeNode` subclass (no raw callables, lambdas, closures, or functions as nodes).

- **P0: Declarative script validation (restricted grammar)**
  - Validate `workflow.py` against an allowlisted grammar, not merely an import filter. Allow only: Orcheo imports, `class X(CodeNode): …`, node instantiation, `add_node` / `add_edge` / `add_conditional_edges` / `compile`, and the `orcheo_workflow` entrypoint body/return.
  - Reject all other top-level and construction-time code: arbitrary statements, decorators, metaclasses, `default_factory` callables, comprehensions calling non-allowlisted functions, dunder attribute access (`__bases__`, `__subclasses__`, `__mro__`, `__globals__`, etc.), and dynamic attribute/subscript gadgets.
  - Block all imports that are not from Orcheo. The permitted graph-construction symbols (`StateGraph`, `START`, `END`) must be sourced from an Orcheo re-export so the "Orcheo-only import" rule holds without exception.
  - Require exactly one workflow entrypoint: a zero-argument function named `orcheo_workflow` that assembles and returns/compiles the graph. `async def orcheo_workflow` is accepted for source-compatibility but is interpreted, not awaited — `await` is rejected as a non-declarative construct. Arbitrary, missing, or multiple entrypoint names are rejected.

- **P0: Restricted-AST interpreter (no author-code execution)**
  - Compile a conforming script into the IR by interpreting its allowlisted AST: map constructor calls to node specs (resolving the class against the registry/import allowlist and `literal_eval`-ing config kwargs), `add_edge` to edge specs, `add_conditional_edges` to conditional-edge specs, and extract each `CodeNode.run` body as a dedented string.
  - The interpreter must never `compile`/`eval`/`exec` author source, import tenant modules, or call tenant entrypoints. The trusted host runs trusted LangGraph/Orcheo, parameterised by data lifted from the AST.

- **P0: Trusted IR → `StateGraph` rebuilder**
  - At runtime, build the `StateGraph` from the stored IR using trusted registered node constructors and the declarative conditional-edge builder, wiring each `CodeNode` spec to the sandbox runner.
  - Validate the IR again before building/running it. The original `workflow.py` is never re-executed.

- **P0: `CodeNode` sandboxed execution (MicroPython-WASM)**
  - Each `CodeNode`'s `run` body executes per invocation inside a MicroPython-WASM sandbox (the `micropython_wasm` package), with builtins only — no imports inside the body and no non-builtin dependencies.
  - Bodies are pure synchronous transforms (no `await`). Any I/O is the responsibility of built-in nodes.
  - The sandbox runs with no network, no filesystem, no inherited environment, and per-invocation memory/fuel and wall-clock limits.
  - `CodeNode` execution applies in restricted mode. In unrestricted mode the whole script runs in-process and no sandbox is used.

- **P0: `CodeNode` sandbox I/O contract**
  - The sandbox receives, as JSON-coercible inputs: a JSON projection of the node state, the run config, and the `CodeNode`'s injected configurable fields (`self.<field>`).
  - The body returns an updated state mapping, which is merged back like a vanilla LangGraph node (respecting the state channel reducers).
  - Define explicitly which `self.<configurable>` fields are injected (the `injected` set in the IR).
  - Define the handling of non-JSON-serialisable state values: by default they are dropped from the projection and the omission is logged; the returned update must be JSON-coercible or the run fails. The drop-vs-raise behaviour is documented and configurable.
  - `[[credential]]` placeholders are rejected in `CodeNode` injected config, so the sandbox never receives resolved secrets.

- **P0: `CodeNode` builtin-allowlist validation at definition time**
  - Because MicroPython's builtins are not 1:1 with CPython's, validate each `CodeNode` body at ingestion against an allowlist of builtins supported by the pinned MicroPython-WASM artifact, so unsupported usage fails at ingestion rather than inside the sandbox.
  - Also validate structurally at ingest: no `import` statements, no `await`/async constructs, the body returns a state-update mapping, and it references only its injected fields plus the passed state/config.

- **P0: Optional enforcement via definition mode**
  - Introduce `ORCHEO_WORKFLOW_DEFINITION_MODE` with `restricted` and `unrestricted`. Default is `unrestricted` at this stage; flipping the default to `restricted` is a separate follow-up task.
  - `restricted`: uploads are accepted but every `workflow.py` must compile to the IR; non-conforming scripts are rejected; the runtime executes from the IR only, and `CodeNode` bodies run in the sandbox.
  - `unrestricted`: `workflow.py` executes in-process exactly as the current codebase does (`load_graph_from_script`), with no additional restriction and no sandbox. Intended for local/self-hosted/developer use; provides no tenant isolation.

- **P0: Config-value vocabulary**
  - Permit JSON literals everywhere.
  - Permit `{{state.path}}` templates in any node config; they remain inert strings in the IR and are resolved at run time by the trusted decoder (including before marshalling into a `CodeNode` sandbox).
  - Permit `[[credential]]` placeholders in built-in node config; resolve from the vault at run time so secrets never persist in the IR.
  - Reject `[[credential]]` placeholders in any field belonging to a `CodeNode`'s injected config.

- **P1: Sandbox defense-in-depth and operability**
  - Treat the WASM sandbox as one layer; recommend OS-level hardening (process limits/seccomp or equivalent) behind it for high-risk multi-tenant production, since the bundled artifact is not a complete security boundary by itself.
  - Pin the MicroPython-WASM artifact version and couple the builtin allowlist to it; surface artifact/allowlist version in diagnostics.
  - Provide actionable, line-referenced ingestion errors and audit logs/metrics for rejections, the active definition mode, and sandbox execution failures (timeouts, limit breaches).

### Designs (if applicable)
See `project/initiatives/sandboxed_custom_workflows/2_design.md`.

### Other Teams Impacted
- **Backend/Worker:** Ingestion gains a restricted path that produces the IR; execution builds the graph from the IR and runs `CodeNode` bodies through the sandbox runner.
- **SDK/CLI:** Upload remains `workflow.py`-first; the CLI should surface restricted-mode validation errors (grammar, config-value, builtin-allowlist) clearly and allow opting into unrestricted mode locally.
- **Studio:** Workflow detail/preview should read graph structure from the IR.
- **Deployment:** Stack templates choose the definition mode and pin the MicroPython-WASM artifact; multi-tenant deployments should set restricted explicitly and add OS-level hardening (the env default is unrestricted at this stage).
- **Docs/Examples:** Document the restricted grammar, the config-value vocabulary, the `CodeNode` authoring contract (builtins-only, sync, JSON I/O), and the definition-mode toggle; mark unrestricted mode as not tenant-safe.

## TECHNICAL CONSIDERATIONS
### Architecture Overview
Restricted-mode ingestion parses `workflow.py` to an AST, validates it against the declarative grammar, and interprets it into the frozen IR — running zero author code. `CodeNode` bodies are extracted as strings and validated against the MicroPython builtin allowlist. The IR is validated and persisted. At run time, a trusted builder reconstructs the `StateGraph` from the IR using registered node constructors and the declarative conditional-edge builder; built-in nodes execute in-process with normal vault credential resolution, while each `CodeNode` body is marshalled (JSON state projection + config + injected fields) into the MicroPython-WASM sandbox and its returned update merged back through the state reducers.

Unrestricted mode bypasses both layers and preserves today's in-process `load_graph_from_script` path for local/self-hosted convenience.

### Technical Requirements
- Add IR Pydantic models and a stable, versioned schema (`schema_version`).
- Add an Orcheo re-export of `StateGraph`, `START`, and `END` so graph-construction imports stay Orcheo-only.
- Implement the restricted grammar validator and the AST interpreter (`workflow.py` → IR) with no `compile`/`eval`/`exec` of author code.
- Implement the IR → `StateGraph` rebuilder, re-validation, and `CodeNode`-to-sandbox wiring.
- Integrate the `micropython_wasm` package server-side: a thin runner taking (body, inputs JSON) and returning outputs JSON under memory/fuel/timeout limits, with no network/filesystem/env.
- Marshal state→JSON projection, config, and injected configurable fields; merge the returned update through the state reducers.
- Maintain the MicroPython builtin allowlist tied to the pinned artifact version; validate `CodeNode` bodies against it (and structurally) at ingest.
- Implement config-value vocabulary validation, including the per-layer credential rule.
- Add `ORCHEO_WORKFLOW_DEFINITION_MODE` parsing defaulting to `unrestricted` at this stage (a later task flips the default to `restricted`); wire ingestion/execution to branch on it.
- Provide actionable, line-referenced validation errors and audit logs/metrics for rejections and sandbox failures.

## MARKET DEFINITION
Internal platform/runtime feature; no external market analysis required.

## LAUNCH/ROLLOUT PLAN

### Success metrics
| KPIs | Target & Rationale |
|------|--------------------|
| [Primary] In-process author-code execution in restricted mode | 0 `compile`/`eval`/`exec` of author source, 0 tenant-module imports, 0 entrypoint calls during restricted ingestion |
| [Primary] `CodeNode` host execution | 0 `CodeNode` bodies executed outside the MicroPython-WASM sandbox in restricted mode |
| [Primary] Runtime source re-execution | 0 re-executions of original `workflow.py`; runtime builds only from the IR |
| [Secondary] Authoring parity | Conforming candidate/example workflows compile to the IR without rewrites beyond the documented grammar and `CodeNode` contract |
| [Secondary] Local friction | Unrestricted mode reproduces today's behaviour with no added restriction |
| [Guardrail] Rejection quality | Non-conforming scripts, disallowed config values, and unsupported `CodeNode` builtins fail at ingestion with actionable, line-referenced errors |

> Note: the primary metrics above describe restricted-mode behaviour. Because the default at this stage is `unrestricted`, they are evaluated with `ORCHEO_WORKFLOW_DEFINITION_MODE=restricted` until a later task makes restricted the default.

### Rollout Strategy
Breaking, pre-GA internal change; no migration path required. Land in phases: define the IR and rebuilder, add the restricted grammar validator and AST interpreter, add the MicroPython-WASM `CodeNode` execution layer with builtin-allowlist validation, wire the definition-mode toggle (default unrestricted at this stage; restricted-by-default is a later task), then validate against real candidate/example workflows and document the grammar and `CodeNode` contract.

### Estimated Launch Phases

| Phase | Target | Description |
|-------|--------|-------------|
| **Phase 1** | Internal development | Define the frozen IR models, the Orcheo re-export of graph symbols, and the trusted IR → `StateGraph` rebuilder (built-in nodes) with re-validation |
| **Phase 2** | Internal development | Implement the restricted grammar validator and the AST interpreter (`workflow.py` → IR), including `CodeNode` extraction and config-value vocabulary |
| **Phase 3** | Internal development | Integrate the MicroPython-WASM `CodeNode` sandbox: I/O contract, marshalling/merge-back, builtin-allowlist + structural body validation, resource limits |
| **Phase 4** | Internal development | Add `ORCHEO_WORKFLOW_DEFINITION_MODE`, branch ingestion/execution, and keep unrestricted mode equal to today's behaviour |
| **Phase 5** | Internal staging | Pressure-test against candidate/example workflows; finalise error messages, audit logs/metrics, OS-level hardening guidance, and grammar/`CodeNode` documentation |

## HYPOTHESIS & RISKS
Hypothesis: a `workflow.py` restricted to a small declarative grammar can be interpreted into a frozen IR without executing author code, and the only surviving author logic (`CodeNode` bodies) can be confined to a MicroPython-WASM sandbox — making custom workflows safe by construction while preserving Python ergonomics.

Risks:
- The declarative grammar or `CodeNode` contract may be too narrow for some legitimate workflows, forcing authoring changes.
- The AST interpreter could contain a gap that lets a non-declarative construct through, reintroducing in-process risk.
- The MicroPython-WASM artifact is experimental and not a complete security boundary by itself; a sandbox escape or resource-exhaustion path could affect the host.
- MicroPython's builtin subset can drift across artifact versions, invalidating the allowlist or breaking previously valid bodies.
- Per-invocation sandbox startup/marshalling adds latency and concurrency considerations.
- Unrestricted mode may be misread as tenant-safe.

Risk mitigation:
- Fail closed: reject anything not explicitly allowed by the grammar or builtin allowlist; treat the interpreter and sandbox, not denylists, as the boundaries.
- Re-validate the IR at execution; build graphs only via trusted constructors; run `CodeNode` bodies only via the sandbox runner.
- Add OS-level hardening behind the WASM sandbox for multi-tenant production; enforce memory/fuel/timeout limits and no network/filesystem/env.
- Keep `CodeNode` inputs credential-free and JSON-coercible so the sandbox cannot receive or exfiltrate secrets/rich objects.
- Pin the artifact version, couple the allowlist to it, and surface versions in diagnostics; cover the allowlist with tests.
- Test the interpreter and sandbox against malicious-shaped scripts and bodies (gadget chains, decorators, lambda nodes, non-`CodeNode` subclasses, credential placeholders in `CodeNode` config, unsupported builtins, infinite loops, oversized outputs).
- Name and log unrestricted mode explicitly as providing no tenant isolation.

## APPENDIX
Definitions:
- **Definition layer:** Ingestion-time compilation of `workflow.py` into the frozen IR via the restricted-AST interpreter, executing no author code.
- **Execution layer:** Per-invocation execution of `CodeNode` bodies inside the MicroPython-WASM sandbox.
- **Frozen workflow IR:** The validated, JSON-coercible, non-executable representation of a workflow graph that is persisted and executed; the single source of truth for a workflow version.
- **Restricted-AST interpreter:** The component that compiles a conforming `workflow.py` into the IR by interpreting an allowlisted AST grammar, executing no author code.
- **`CodeNode`:** The only inheritable base class for user-defined logic; its `run` body is carried in the IR as a string and executed only in the MicroPython-WASM sandbox.
- **Definition mode:** `restricted` (IR-enforced, sandboxed `CodeNode` execution) or `unrestricted` (today's in-process `workflow.py` execution, no tenant isolation).
- **Config-value vocabulary:** The legal forms of node config values — JSON literals, `{{state.path}}` templates (anywhere), and `[[credential]]` placeholders (built-in node config only).
