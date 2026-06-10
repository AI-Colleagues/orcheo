# Requirements Document: Orcheo Plugin Ecosystem

## METADATA

- **Authors:** Codex
- **Project/Feature Name:** Orcheo Plugin Ecosystem
- **Type:** Feature
- **Summary:** Add a managed plugin mechanism for nodes, edges, agent tools, triggers, and listeners, with `orcheo plugin ...` as the primary lifecycle and management path.
- **Owner (if different than authors):** Shaojie Jiang
- **Date Started:** 2026-03-16

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|---|---|---|---|
| Existing custom extension guide | `docs/custom_nodes_and_tools.md` | Shaojie Jiang | Current Custom Nodes and Tools Guide |
| CLI design | `project/architecture/cli_tool_design.md` | Shaojie Jiang | Orcheo CLI Design |
| Node registry | `src/orcheo/nodes/registry.py` | Shaojie Jiang | Node Registry |
| Edge registry | `src/orcheo/edges/registry.py` | Shaojie Jiang | Edge Registry |
| Agent tool registry | `src/orcheo/nodes/agent_tools/registry.py` | Shaojie Jiang | Agent Tool Registry |
| Listener models | `src/orcheo/listeners/models.py` | Shaojie Jiang | Listener Platform Models |
| Listener compiler | `src/orcheo/listeners/compiler.py` | Shaojie Jiang | Listener Subscription Compiler |
| WeCom OpenClaw docs | https://work.weixin.qq.com/nl/index/openclaw | Tencent WeCom | OpenClaw Long Connection Documentation |
| Lark OpenClaw plugin | https://github.com/larksuite/openclaw-lark | Lark | Reference Plugin Implementation |
| Lark OpenClaw guide | https://www.feishu.cn/content/article/7613711414611463386 | Lark | OpenClaw Feishu Plugin Features, Installation & FAQ |
| Lark Open Platform API docs | https://open.larksuite.com/document/ukTMukTMukTM/uETO1YjLxkTN24SM5UjN#1c227849 | Lark | Lark Open Platform API Documentation |
| Requirements | [1_requirements.md](1_requirements.md) | Shaojie Jiang | Plugin Ecosystem Requirements |
| Design | [2_design.md](2_design.md) | Shaojie Jiang | Plugin Ecosystem Design |
| Plan | [3_plan.md](3_plan.md) | Shaojie Jiang | Plugin Ecosystem Plan |

## PROBLEM DEFINITION

### Objectives

Remove the current import-time, `sitecustomize`-based extension workflow and replace it with a managed plugin mechanism that Orcheo can discover, install, update, enable, disable, and remove through the CLI. Make nodes, edges, agent tools, triggers, and listeners externally extensible without requiring core-code edits or private forks.

### Target users

- Orcheo integrators building provider-specific workflow components.
- Internal teams and partners shipping private Orcheo extensions.
- Operators managing self-hosted Orcheo deployments.
- Workflow authors who need third-party or company-specific nodes, edges, triggers, and listeners.

### User Stories

| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---|---|---|---|---|
| Plugin developer | publish a node, edge, tool, trigger, or listener package against a documented Orcheo plugin API | I can extend Orcheo without modifying core | P0 | A plugin package can register supported component types and become visible after CLI installation |
| Operator | run `orcheo plugin install <ref>` | I can add a plugin through one supported command path | P0 | The command installs the plugin, validates compatibility, and makes it available to the active Orcheo installation |
| Operator | run `orcheo plugin update <name>` or `orcheo plugin update --all` | I can keep installed plugins current | P0 | The command updates installed plugins, refreshes the lock state, and reports any restart or compatibility actions required |
| Operator | run `orcheo plugin uninstall <name>` | I can remove plugins cleanly | P0 | The command removes the plugin, cleans Orcheo-managed state, and prevents the plugin from loading again |
| Operator | list and inspect installed plugins | I can understand what is installed and what each plugin contributes | P0 | `orcheo plugin list` and `orcheo plugin show <name>` surface version, compatibility, status, and exported component kinds |
| Workflow author | use externally installed edge plugins | I can adopt custom control-flow behavior without patching Orcheo core | P0 | Edge plugins appear in edge discovery flows and can be used in workflow definitions |
| Plugin developer | follow a consistent naming convention for edges | I can reason about edge classes the same way I reason about node classes | P0 | Core edges adopt `Edge` suffixes with backward-compatible aliases |
| Orcheo maintainer | validate the listener plugin interface with independently packaged integrations | I can confirm the interface works for external packages before expanding plugin coverage further | P0 | WeCom and Lark listener plugins install and run through the plugin mechanism without core patches |
| Operator | disable a broken plugin without uninstalling it | I can recover service health quickly | P1 | `orcheo plugin disable <name>` prevents plugin loading until re-enabled |
| Plugin developer | scaffold a new plugin package from the CLI | I can start from a supported template instead of reverse-engineering internals | P1 | `orcheo plugin init` generates a package skeleton and manifest |

### Context, Problems, Opportunities

Orcheo already has registry-oriented extension seams for nodes, edges, and agent tools. However, the current documented customization flow relies on package import side effects and `sitecustomize`, which is operationally fragile, difficult to support, and not aligned with the rest of Orcheo's CLI-based installation workflow. There is no managed plugin lifecycle, no compatibility contract, no supported operator workflow, and no stable way to externalize listener or trigger runtimes. Because no Orcheo extensions currently depend on `sitecustomize`, this project can remove that support directly instead of carrying a migration or deprecation phase.

Listeners and triggers are currently the least extensible surfaces because listener platforms and compilation behavior are hard-coded around a closed enum and built-in modules. Adding a new platform therefore requires a core-code change. Edge extensibility also needs to be part of the first release so custom control-flow behavior can ship through the same mechanism as other components. The project should establish a documented, versioned plugin API for nodes, edges, agent tools, triggers, and listeners in the first release.

The opportunity is to turn Orcheo's existing registries into a supported extension platform. The primary operator workflow should be explicit CLI management through `orcheo plugin install`, `orcheo plugin update`, `orcheo plugin uninstall`, and related commands rather than undocumented `PYTHONPATH` setup.

### Product Goals and Non-goals

Goals:

- Ship a managed plugin mechanism for nodes, edges, agent tools, triggers, and listeners.
- Make `orcheo plugin ...` the supported path for installing and managing plugins.
- Remove the existing `sitecustomize` extension mechanism and make the managed plugin workflow the only supported extension path.
- Support local package, wheel, source tree, and Git/plugin registry references through the CLI.
- Add compatibility checks, enable/disable controls, and clear failure handling.
- Rename core edges with an `Edge` suffix while preserving backward compatibility.
- Validate the listener plugin contract with a WeCom long-connection listener plugin and a Lark listener plugin.
- Make successful WeCom and Lark listener-plugin operation part of feature acceptance.
- Deliver a Studio template that uses both the WeCom and Lark listener plugins in one workflow so the shared listener contract is validated in a builder-facing artifact.

Non-goals:

- Building a public plugin marketplace in v1.
- Sandboxing untrusted plugin code beyond process-level and dependency-level controls already used by Orcheo deployments.
- Supporting remote SaaS-style plugin installation into a backend the operator does not control.
- Defining a TypeScript or Studio runtime plugin API in the first release.
- Full hot-reload across every plugin surface in v1. Only targeted, generation-aware activation for hot-reloadable node, edge, and agent-tool changes is in scope; trigger and listener changes still require restart or reconcile semantics.

## PRODUCT DEFINITION

### Requirements

**P0 (must have)**

- Add a versioned plugin contract that supports:
  - node plugins
  - edge plugins
  - agent tool plugins
  - trigger plugins
  - listener plugins
- Introduce a plugin loader that discovers only enabled, compatible plugins and registers their exported components during Orcheo startup.
- Replace the current "import something before the registry is read" workflow with explicit plugin discovery and lifecycle management.
- Provide a CLI command group as the primary user path:
  - `orcheo plugin list`
  - `orcheo plugin show <name>`
  - `orcheo plugin install <ref>`
  - `orcheo plugin update <name>`
  - `orcheo plugin update --all`
  - `orcheo plugin uninstall <name>`
  - `orcheo plugin enable <name>`
  - `orcheo plugin disable <name>`
  - `orcheo plugin doctor`
- Support installation references from:
  - package names from a configured package index
  - wheel or source distribution files
  - local directories
  - Git URLs
- Persist plugin installation intent and resolved versions in Orcheo-managed state so environments can be reconciled consistently.
- Store runtime-managed plugin artifacts and state under `~/.orcheo/plugins/` by default, with a dedicated override such as `ORCHEO_PLUGIN_DIR` for operators that need relocation.
- Use the plugin storage layout consistently:
  - `~/.orcheo/plugins/plugins.toml` for desired plugin state
  - `~/.orcheo/plugins/plugin-lock.toml` for resolved plugin state
  - `~/.orcheo/plugins/site-packages/` or an equivalent managed environment for installed plugin code
  - `~/.cache/orcheo/plugins/` for downloaded package caches and refreshable metadata
- Refuse to load incompatible plugins and report the reason through CLI output and startup logs.
- Classify plugin changes by runtime impact and handle them accordingly:
  - additive changes to hot-reloadable surfaces may apply silently
  - changes that replace existing exported nodes, edges, or agent tools must warn and require maintainer confirmation
  - changes affecting triggers or listeners must warn and recommend restart or reconcile
- Support targeted, generation-aware activation for hot-reloadable node, edge, and agent-tool plugins so additive or otherwise policy-approved changes can apply to new runs without forcing older runs onto the new generation.
- Keep v1 generation tracking process-local and non-persistent: plugin desired state and lock state survive restart, but hot-reload generation counters do not.
- Present maintainers with an impact summary that names the affected exported component identifiers before applying non-trivial plugin changes.
- Rename core edges to `IfElseEdge`, `SwitchEdge`, `WhileEdge`, and similar `Edge`-suffixed names, while retaining compatibility aliases for existing graph definitions.
- Include edge plugins in plugin documentation, component discovery flows, and validation coverage in the first release.
- Replace the current listener platform hard-coding with a listener-plugin registration path so external packages can introduce new platforms.
- Deliver a WeCom listener plugin that uses the plugin contract and exercises long-connection lifecycle, credential handling, event normalization, and health reporting.
- Deliver a Lark listener plugin that uses the plugin contract and validates the same interface from a second external integration.
- Deliver a Studio template that wires WeCom and Lark listeners into one workflow and proves they can share downstream logic.
- Make WeCom and Lark listener plugins part of the acceptance test suite and release checklist.

**P1 (nice to have)**

- `orcheo plugin init` scaffolding for plugin authors.
- `orcheo plugin search <query>` against a curated registry or index.
- Signed-plugin metadata and trust-policy configuration.
- Built-in component migration so core listeners/triggers also load through the same plugin abstraction.
- Per-plugin metrics and health views in API/UI.

### Designs (if applicable)

See [2_design.md](2_design.md) for the plugin package contract, runtime loading model, and CLI management flows.

### [Optional] Other Teams Impacted

- **Backend/runtime:** Plugin discovery, compatibility checks, trigger/listener loading, and startup behavior.
- **SDK/CLI:** Plugin lifecycle commands, lock state, and diagnostics.
- **Studio/catalog:** Component discovery must reflect plugin-provided nodes, edges, tools, triggers, and listeners.
- **Operations:** New installation, upgrade, rollback, and troubleshooting flows.

## TECHNICAL CONSIDERATIONS

### Architecture Overview

Orcheo should load plugins through a managed plugin subsystem rather than through ad hoc imports. Plugin packages declare metadata and entry points, the CLI installs them into the active Orcheo environment, and startup loads only enabled plugins that match the current Orcheo plugin API version. Loaded plugins register components into the existing registries or the new trigger/listener registries, then Orcheo exposes those components through existing discovery and execution paths.

### Technical Requirements

- Define plugin metadata with:
  - plugin name, version, author, and description
  - supported Orcheo plugin API version
  - compatible Orcheo version range
  - exported component kinds
  - optional capabilities and health hooks
- Define stable registration interfaces for nodes, edges, agent tools, triggers, and listeners.
- Define a plugin API version policy: use a single positive integer that increments only on breaking changes to the plugin contract; document what constitutes a breaking change; require all plugins to declare a compatible API version and refuse to load plugins declaring a different version.
- Introduce trigger and listener registries so those surfaces can be extended without hard-coded enums.
- Add a managed plugin state store and lockfile.
- Define the filesystem layout for managed plugin state, installed artifacts, and cache locations.
- Integrate plugin reconciliation with the existing Orcheo CLI installation model.
- Track whether a plugin change is additive, replacing, or removing existing exported component identifiers.
- Support generation-aware activation for hot-reloadable surfaces so new runs can use updated node, edge, and agent-tool implementations while older runs finish on the previous generation.
- Define the operator contract for multi-process deployments clearly: generation-aware hot-reload is per process in v1 and does not imply cross-process continuity after restart.
- Ensure the runtime can isolate plugin load failures and continue booting core functionality where safe.
- Guarantee that plugin installation and upgrade paths are deterministic and scriptable.
- Document behavior for multi-process deployments where the API backend and Celery workers run as separate processes, including guidance on propagating plugin changes across processes and hosts.
- Add compatibility handling for the edge renaming migration.
- Add automated tests that install sample plugins into a clean test environment and validate component discovery and execution.

### AI/ML Considerations (if applicable)

Not applicable to the plugin mechanism itself. Plugins may provide AI-related nodes or tools, but the core initiative is about extension contracts and lifecycle.

## MARKET DEFINITION (for products or large features)

Not applicable; this is an internal platform and ecosystem capability.

## LAUNCH/ROLLOUT PLAN

### Success metrics

| KPIs | Target & Rationale |
|---|---|
| [Primary] Plugin lifecycle success rate | 95%+ successful install/update/uninstall flows in automated test environments and internal staging |
| [Primary] External listener validation | WeCom and Lark listener plugins install and dispatch successfully through the plugin contract |
| [Primary] Studio validation artifact | A Studio template using both WeCom and Lark listeners imports and runs against the shared listener contract |
| [Guardrail] Impact classification accuracy | CLI impact summaries correctly distinguish silent hot-reloadable changes from changes that require maintainer confirmation or restart/reconcile |
| [Guardrail] Startup resilience | A single broken plugin must not crash unrelated core startup paths |
| [Guardrail] Edge compatibility | Existing workflows using legacy edge names continue to load after edge renaming |

### Rollout Strategy

Ship the plugin loader and CLI lifecycle first, then validate it with external listener plugins before broadening usage guidance. Once `orcheo plugin install` is available, position the plugin workflow as the supported extension path.

### Experiment Plan (if applicable)

Not applicable. This is a platform capability with acceptance driven by integration validation and automated coverage.

### Estimated Launch Phases (if applicable)

| Phase | Target | Description |
|---|---|---|
| **Phase 1** | Internal plugin foundations | Loader, metadata contract, CLI lifecycle commands, and sample fixture plugins |
| **Phase 2** | Core surface rollout | Nodes, edges, agent tools, triggers, and listener interfaces exposed through the plugin API; edge rename compatibility lands |
| **Phase 3** | Validation plugins | WeCom and Lark listener plugins are installable and operable via CLI |
| **Phase 4** | General documentation | Plugin author guide, operator runbooks, and rollout documentation |

## HYPOTHESIS & RISKS

- **Hypothesis:** If Orcheo exposes a documented plugin contract and a CLI-based lifecycle, extension authors will stop relying on import hacks and private forks.
- **Confidence:** High, because Orcheo already has registries for nodes, edges, and agent tools; the missing pieces are lifecycle management, compatibility checks, and operator support.
- **Risk:** Plugin APIs could be frozen too early and leak unstable core internals.
  - **Mitigation:** Keep the v1 plugin API narrow, explicit, and versioned.
- **Risk:** Plugin installation could drift from the active runtime environment.
  - **Mitigation:** Make `orcheo plugin ...` manage Orcheo-owned plugin state and reconciliation rather than relying on undocumented package installs.
- **Risk:** The listener-plugin abstraction could still encode core assumptions that only fit built-in platforms.
  - **Mitigation:** Use WeCom and Lark as required acceptance targets, not optional demos.
- **Risk:** Renaming edges could break existing workflows and docs.
  - **Mitigation:** Preserve aliases and validate old graph definitions in tests.

## APPENDIX

### Acceptance checklist

- `orcheo plugin install <ref>` installs compatible plugins and rejects incompatible ones with an explicit error message.
- `orcheo plugin update <name>` and `orcheo plugin uninstall <name>` work without manual environment manipulation.
- Additive changes to nodes, edges, and agent tools can apply without service restart when they do not replace existing exported identifiers.
- Changes that replace existing nodes, edges, or agent tools show maintainers an impact summary and require explicit confirmation before applying.
- Changes affecting triggers or listeners show maintainers an impact summary and recommend restart or reconcile before the change becomes active.
- Installed plugin components appear in catalog/discovery flows such as `orcheo node list` and `orcheo edge list`.
- WeCom and Lark listener plugins are installed and managed only through the plugin CLI flow.
- WeCom and Lark listener plugins both dispatch listener events end to end through the shared plugin interface.
- A Studio template using both WeCom and Lark listeners imports cleanly and validates the shared downstream workflow contract.
- Legacy edge names continue to work after the `Edge` suffix migration.
- The plugin workflow is documented as the supported extension path once the feature ships.
