# Project Plan

## For Sandboxed Custom Workflows

- **Version:** 0.1
- **Author:** Claude
- **Date:** 2026-06-29
- **Status:** Draft

---

## Overview

Deliver both layers that make user-uploaded workflows safe by construction. The **definition layer** reduces every uploaded `workflow.py` to a frozen, validated IR via a restricted-AST interpreter that runs no author code at ingestion; the IR is the only artifact persisted and executed. The **execution layer** runs the only user-authored logic — `CodeNode` bodies — per invocation inside a MicroPython-WASM sandbox with builtins-only, JSON-coercible I/O. Enforcement is optional via a definition-mode environment variable, defaulting to `unrestricted` at this stage (flipping the default to `restricted` is a separate follow-up task), with restricted as an explicit opt-in and unrestricted equal to today's behaviour for local/self-hosted development.

Out of scope: converting arbitrary, non-conforming `workflow.py` files into the IR via isolated execution (a translation/importer path).

**Related Documents:**
- Requirements: `project/initiatives/sandboxed_custom_workflows/1_requirements.md`
- Design: `project/initiatives/sandboxed_custom_workflows/2_design.md`

---

## Milestones

### Milestone 1: Frozen IR Foundation

**Description:** Establish the IR as the canonical workflow artifact and the trusted path to rebuild a runnable graph from it. Success criterion: a hand-written IR validates, round-trips as JSON, and builds/executes a graph of built-in nodes without any author code.

#### Task Checklist

- [ ] Task 1.1: Define the frozen IR Pydantic models (`GraphIR`, `BuiltinNodeSpec`, `CodeNodeSpec`, `EdgeSpec`, `ConditionalEdgeSpec`) with `schema_version` and JSON round-trip tests
  - Dependencies: None
- [ ] Task 1.2: Add an Orcheo re-export of `StateGraph`, `START`, and `END` so graph-construction imports stay Orcheo-only
  - Dependencies: None
- [ ] Task 1.3: Implement the trusted IR → `StateGraph` rebuilder using registered node constructors and the declarative conditional-edge builder (built-in nodes; `CodeNode` wiring added in Milestone 3)
  - Dependencies: Task 1.1
- [ ] Task 1.4: Re-validate the IR before build/run and surface clear errors for unknown node types or malformed specs
  - Dependencies: Task 1.3
- [ ] Task 1.5: Add tests proving a built-in-node IR builds and runs without re-executing any script
  - Dependencies: Task 1.3

---

### Milestone 2: Restricted Grammar Validator & AST Interpreter

**Description:** Compile a conforming `workflow.py` into the IR by interpreting an allowlisted AST, executing no author code. Success criterion: conforming scripts produce a correct IR; non-conforming scripts are rejected with line-referenced errors; ingestion performs no `compile`/`eval`/`exec`.

#### Task Checklist

- [ ] Task 2.1: Implement the restricted grammar validator (allow Orcheo imports, `CodeNode` subclasses, node/edge instantiation, `add_node`/`add_edge`/`add_conditional_edges`/`compile`, and a single zero-argument `orcheo_workflow` entrypoint — `def` or `async def`, never awaited — return)
  - Dependencies: Task 1.2
- [ ] Task 2.2: Reject non-declarative constructs — arbitrary statements, decorators, metaclasses, `default_factory` callables, dunder/gadget access, dynamic attr/subscript, non-Orcheo imports, raw-callable/lambda nodes, and non-`CodeNode` subclasses
  - Dependencies: Task 2.1
- [ ] Task 2.3: Implement the AST interpreter that maps constructor calls to node specs (`literal_eval` config), `add_edge`/`add_conditional_edges` to edge specs, and the `orcheo_workflow` entrypoint body to the resolved graph
  - Dependencies: Task 2.1, Task 1.1
- [ ] Task 2.4: Extract each `CodeNode.run` body as a dedented string via AST source slicing (not `inspect.getsource`) and populate `CodeNodeSpec.body`/`injected`
  - Dependencies: Task 2.3
- [ ] Task 2.5: Implement the config-value validator (JSON literals, `{{state.path}}` anywhere, `[[credential]]` in built-in config only — rejected in `CodeNode` injected config)
  - Dependencies: Task 2.3
- [ ] Task 2.6: Implement structural `CodeNode` body validation (no imports, no `await`/async, returns a state-update mapping, references only injected fields plus state/config)
  - Dependencies: Task 2.4
- [ ] Task 2.7: Add tests for malicious-shaped scripts, gadget chains, lambda nodes, non-`CodeNode` subclasses, credential placeholders in `CodeNode` config, and correct IR output for conforming scripts
  - Dependencies: Task 2.2, Task 2.3, Task 2.5, Task 2.6

---

### Milestone 3: CodeNode Sandboxed Execution (MicroPython-WASM)

**Description:** Run `CodeNode` bodies per invocation inside the MicroPython-WASM sandbox with builtins-only, JSON-coercible I/O, isolated from the host. Success criterion: a `CodeNode` body executes end-to-end in the sandbox, returns an update merged via the state reducers, and unsupported builtins are rejected at ingestion.

#### Task Checklist

- [ ] Task 3.1: Integrate the `micropython_wasm` package as a server-side runner taking `(body, inputs_json)` and returning `outputs_json`, with no network/filesystem/env and memory/fuel/timeout limits; pin the artifact version
  - Dependencies: None
- [ ] Task 3.2: Define and implement the I/O envelope and state marshaller — JSON projection of state (drop/raise on non-serialisable values per contract), config, and injected configurable fields
  - Dependencies: Task 3.1, Task 1.1
- [ ] Task 3.3: Merge the returned update back through the state channel reducers like a vanilla LangGraph node update
  - Dependencies: Task 3.2
- [ ] Task 3.4: Build and maintain the MicroPython builtin allowlist tied to the pinned artifact; add `CodeNode` body validation against it at ingest
  - Dependencies: Task 3.1, Task 2.6
- [ ] Task 3.5: Wire the IR graph builder's `CodeNode` specs to the sandbox runner so restricted-mode execution invokes the sandbox
  - Dependencies: Task 1.3, Task 3.3
- [ ] Task 3.6: Add structured, node-attributed error handling and metrics for timeouts, limit breaches, and non-JSON outputs
  - Dependencies: Task 3.3
- [ ] Task 3.7: Add tests — end-to-end transform, merge-back semantics, limit enforcement (infinite loop / oversized output), unsupported-builtin rejection, and credential-free inputs
  - Dependencies: Task 3.4, Task 3.5, Task 3.6

---

### Milestone 4: Definition-Mode Toggle & Ingestion/Execution Wiring

**Description:** Make enforcement optional and wire both paths. Success criterion: restricted mode ingests to the IR and executes from it with sandboxed `CodeNode` bodies; unrestricted mode (the default at this stage) reproduces today's in-process `workflow.py` behaviour unchanged.

#### Task Checklist

- [ ] Task 4.1: Add `ORCHEO_WORKFLOW_DEFINITION_MODE` parsing defaulting to `unrestricted` at this stage (a later task flips the default to `restricted`), with `restricted` as opt-in, plus a startup log of the active mode
  - Dependencies: None
- [ ] Task 4.2: Branch ingestion — restricted runs validator + interpreter and stores the IR; unrestricted keeps `load_graph_from_script` unchanged with an explicit not-tenant-safe log
  - Dependencies: Task 2.3, Task 4.1
- [ ] Task 4.3: Branch execution — restricted builds from the IR via the trusted rebuilder (with sandboxed `CodeNode`); unrestricted keeps the current in-process script path
  - Dependencies: Task 3.5, Task 4.1
- [ ] Task 4.4: Add integration tests for mode defaulting, restricted ingest→store→run (built-in + `CodeNode`), and unrestricted parity with today's behaviour
  - Dependencies: Task 4.2, Task 4.3

---

### Milestone 5: Validation, Hardening & Documentation

**Description:** Prove both layers against real workflows and make the feature safe to operate and easy to author for. Success criterion: representative candidate/example workflows compile to the IR and run (including `CodeNode`), rejections are actionable, and the grammar, `CodeNode` contract, and modes are documented.

#### Task Checklist

- [ ] Task 5.1: Pressure-test the validator/interpreter and sandbox against representative candidate and example workflows; record which constructs/bodies need documented authoring changes
  - Dependencies: Milestone 3
- [ ] Task 5.2: Finalise actionable, line-referenced validation error messages for grammar, config-value, and `CodeNode` body (structural + builtin) failures
  - Dependencies: Milestone 2, Milestone 3
- [ ] Task 5.3: Add audit logs/metrics for ingestion rejections, the active definition mode, and sandbox execution failures
  - Dependencies: Task 4.2, Task 3.6
- [ ] Task 5.4: Document the restricted grammar, the config-value vocabulary, the `CodeNode` authoring contract (builtins-only, sync, JSON I/O), the definition-mode toggle, and the not-tenant-safe nature of unrestricted mode
  - Dependencies: Milestone 4
- [ ] Task 5.5: Document OS-level hardening guidance for multi-tenant production behind the WASM sandbox, and the artifact/allowlist version-pinning policy
  - Dependencies: Task 3.1

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-06-29 | Claude | Initial draft |
