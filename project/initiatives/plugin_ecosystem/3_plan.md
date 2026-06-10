# Project Plan

## For Orcheo Plugin Ecosystem

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-03-16
- **Status:** Approved

---

## Overview

Deliver a managed plugin mechanism for nodes, edges, agent tools, triggers, and listeners, with the CLI as the supported operator interface. The project removes the current `sitecustomize`-based extension workflow, replaces it with explicit plugin lifecycle management, and validates the listener abstraction by shipping WeCom and Lark listener plugins.

**Related Documents:**
- Requirements: [1_requirements.md](1_requirements.md)
- Design: [2_design.md](2_design.md)

---

## Milestones

### Milestone 1: Plugin Foundations and CLI Lifecycle

**Description:** Define the plugin contract, managed state, and CLI lifecycle commands so plugins can be installed and reconciled without manual environment manipulation.

#### Task Checklist

- [x] Task 1.1: Define `PluginManifest`, plugin API versioning, and entry point contract.
  - Dependencies: None
- [x] Task 1.2: Implement plugin state files for desired state and lock state.
  - Dependencies: Task 1.1
- [x] Task 1.3: Define and document the default plugin filesystem layout under `~/.orcheo/plugins/` plus cache placement under `~/.cache/orcheo/plugins/`.
  - Dependencies: Task 1.2
- [x] Task 1.4: Add CLI commands `orcheo plugin list`, `show`, `install`, `update`, `uninstall`, `enable`, `disable`, and `doctor`.
  - Dependencies: Task 1.2, Task 1.3
- [x] Task 1.5: Support package, local path, wheel, and Git install references in the CLI.
  - Dependencies: Task 1.4
- [x] Task 1.6: Implement compatibility checks for plugin API version and Orcheo version ranges.
  - Dependencies: Task 1.1
- [x] Task 1.7: Add impact analysis that classifies plugin changes as silent hot-reloadable, confirmation-required, or restart/reconcile-required.
  - Dependencies: Task 1.1, Task 1.2
- [x] Task 1.8: Add fixture plugins used by automated tests for nodes, edges, and listeners.
  - Dependencies: Task 1.1
- [x] Task 1.9: Implement `orcheo plugin doctor` with the full diagnostic check set specified in the design.
  - Dependencies: Task 1.4, Task 1.6
- [x] Task 1.10: Add integration tests for install, update, uninstall, enable, disable, broken-plugin handling, maintainer prompts for impactful changes, and `orcheo plugin doctor` output.
  - Dependencies: Task 1.4, Task 1.6, Task 1.7, Task 1.8, Task 1.9
- [x] Task 1.11: Implement transactional install and update semantics so a failed install or update leaves the previous locked state intact and the plugin venv in its pre-operation state.
  - Dependencies: Task 1.4, Task 1.5

---

### Milestone 2: Runtime Loading and Registry Expansion

**Description:** Load enabled plugins safely at startup and make all v1 component surfaces externally extensible.

#### Task Checklist

- [x] Task 2.1: Implement plugin loader with isolated failure handling and precise diagnostics.
  - Dependencies: Milestone 1
- [x] Task 2.2: Add stable plugin registration API for nodes, edges, and agent tools.
  - Dependencies: Task 2.1
- [x] Task 2.3: Introduce trigger and listener registries plus registration APIs for external plugins.
  - Dependencies: Task 2.1
- [x] Task 2.4a: Remove the closed `ListenerPlatform` enum and replace internal platform references with stable string identifiers.
  - Dependencies: Task 2.3
- [x] Task 2.4b: Refactor the listener compiler to look up compiler hooks from the listener registry rather than branching on hard-coded platform values.
  - Dependencies: Task 2.4a
- [x] Task 2.4c: Refactor the listener runtime/supervisor to instantiate adapters through the registry's adapter factory rather than built-in constructors.
  - Dependencies: Task 2.4b
- [x] Task 2.4d: Update API response shapes and serialization to use string platform identifiers consistently.
  - Dependencies: Task 2.4a
- [x] Task 2.5: Implement process-local, generation-aware activation for hot-reloadable node, edge, and agent-tool plugins so new runs can use the updated generation while older runs drain, without persisting generation counters across restart in v1.
  - Dependencies: Task 2.2, Milestone 1
- [x] Task 2.6: Expose plugin-provided components through existing discovery flows such as `orcheo node list` and `orcheo edge list`.
  - Dependencies: Task 2.2, Task 2.3
- [x] Task 2.7: Add startup and reconciliation tests covering compatible, incompatible, disabled, hot-reloadable, and restart-required plugins.
  - Dependencies: Task 2.1, Task 2.5, Task 2.6

---

### Milestone 3: Edge Naming and Compatibility Migration

**Description:** Standardize built-in edge naming with an `Edge` suffix while preserving backward compatibility for existing workflows and docs.

#### Task Checklist

- [x] Task 3.1: Rename core edge classes to canonical `*Edge` names such as `IfElseEdge`, `SwitchEdge`, and `WhileEdge`.
  - Dependencies: Task 2.2 (edge registration API)
- [x] Task 3.2: Add legacy aliases so existing graph definitions using old edge names continue to load.
  - Dependencies: Task 3.1
- [x] Task 3.3: Update edge catalog, docs, and scaffolds to emit canonical edge names.
  - Dependencies: Task 3.1
- [x] Task 3.4: Add tests covering legacy edge-name compatibility and canonical-name discovery.
  - Dependencies: Task 3.2
- [x] Task 3.5: Ensure plugin-authored edges follow the same naming guidance and discovery contract.
  - Dependencies: Task 3.3

---

### Milestone 4: WeCom Listener Plugin Validation

**Description:** Validate the listener plugin contract with a WeCom long-connection plugin package installed and managed through the plugin CLI.

#### Task Checklist

- [x] Task 4.1: Define the listener-plugin adapter contract for connection lifecycle, event normalization, cursor/state persistence, and health reporting.
  - Dependencies: Milestone 2
- [x] Task 4.2: Implement the WeCom listener plugin package against the new contract.
  - Dependencies: Task 4.1
- [x] Task 4.3: Make the WeCom plugin installable through `orcheo plugin install`.
  - Dependencies: Task 4.2, Milestone 1
- [x] Task 4.4: Add end-to-end tests covering plugin install, startup, listener dispatch, and uninstall for WeCom.
  - Dependencies: Task 4.3
- [x] Task 4.5: Document WeCom plugin installation and operations with the CLI as the primary workflow, including restart or reconcile guidance after impactful changes.
  - Dependencies: Task 4.3

---

### Milestone 5: Lark Listener Plugin Validation

**Description:** Validate the same listener contract with a second external integration inspired by the OpenClaw Lark plugin structure.

#### Task Checklist

- [x] Task 5.1: Implement the Lark listener plugin package against the shared listener-plugin contract.
  - Dependencies: Milestone 4
- [x] Task 5.2: Make the Lark plugin installable through `orcheo plugin install`.
  - Dependencies: Task 5.1, Milestone 1
- [x] Task 5.3: Add end-to-end tests covering install, startup, listener dispatch, update, disable, and uninstall for Lark.
  - Dependencies: Task 5.2
- [x] Task 5.4: Verify that WeCom and Lark can coexist as installed plugins without core code changes.
  - Dependencies: Task 4.4, Task 5.3
- [x] Task 5.5: Document Lark plugin installation and operations with the CLI as the primary workflow, including restart or reconcile guidance after impactful changes.
  - Dependencies: Task 5.2

---

### Milestone 6: Studio Validation Template

**Description:** Deliver a builder-facing template that uses both new listener plugins in one workflow and validates the shared downstream contract.

#### Task Checklist

- [x] Task 6.1: Add a Studio template that uses both WeCom and Lark listener plugins in one workflow.
  - Dependencies: Milestone 4, Milestone 5
- [x] Task 6.2: Ensure the template compiles into valid listener subscriptions for both plugin-provided platforms.
  - Dependencies: Task 6.1
- [x] Task 6.3: Validate shared downstream execution and reply-routing metadata in the template.
  - Dependencies: Task 6.2
- [x] Task 6.4: Add template import and runtime validation to the acceptance checklist or automated test suite.
  - Dependencies: Task 6.3

---

### Milestone 7: Docs and Release Readiness

**Description:** Finish plugin documentation, remove obsolete `sitecustomize` guidance, and finalize acceptance criteria for shipping the plugin ecosystem.

#### Task Checklist

- [x] Task 7.1: Publish plugin-author documentation for nodes, edges, tools, triggers, and listeners.
  - Dependencies: Milestone 2, Milestone 3
- [x] Task 7.2: Remove `sitecustomize` guidance from the current custom extension documentation once the plugin workflow lands.
  - Dependencies: Milestone 1, Milestone 2
- [x] Task 7.3: Add release checklist items requiring successful WeCom and Lark plugin validation plus the shared Studio template validation.
  - Dependencies: Milestone 4, Milestone 5, Milestone 6
- [x] Task 7.4: Add operator troubleshooting guidance for `orcheo plugin doctor`, broken-plugin disable flow, and compatibility errors.
  - Dependencies: Milestone 1, Milestone 2
- [x] Task 7.5: Add operator guidance describing when plugin changes apply silently, when confirmation is required, and when restart or reconcile is recommended.
  - Dependencies: Milestone 1, Milestone 2
- [x] Task 7.6: Run final acceptance validation for install/update/uninstall flows, impact-based prompts, edge compatibility migration, and the Studio validation template.
  - Dependencies: Milestone 3, Milestone 4, Milestone 5, Milestone 6

---

## Revision History

| Date | Author | Changes |
|---|---|---|
| 2026-03-16 | Codex | Initial draft |
| 2026-03-16 | Codex | Completed Milestone 1 plugin foundations, CLI lifecycle, diagnostics, fixture-plugin coverage, and transactional rebuild semantics |
| 2026-03-16 | Codex | Completed Milestone 3 edge naming migration tasks (canonical `*Edge` names, legacy aliases, discovery/docs updates, and compatibility tests) |
| 2026-03-16 | Codex | Completed Milestone 2 runtime loading, external registry expansion, string listener-platform migration, and generation-aware plugin reload coverage |
| 2026-03-16 | Codex | Completed Milestones 4-7 with WeCom and Lark validation plugins, shared Studio template coverage, plugin author/operator docs, and release-checklist updates |
