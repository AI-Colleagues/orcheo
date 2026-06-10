# Design Document

## For Orcheo Plugin Ecosystem

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-03-16
- **Status:** Approved

---

## Overview

This design turns Orcheo's current registry-based extension seams into a managed plugin system operated through the CLI. Today, extensions are discovered only if their modules are imported before the registries are queried, and the documented path depends on `sitecustomize`. That approach is workable for local experiments but too fragile for production operations, third-party distribution, and cross-team ownership.

The new model introduces a versioned plugin contract, a managed loader, and a CLI lifecycle under `orcheo plugin`. Plugins declare metadata and an entry point, the CLI installs them into the active Orcheo environment, and startup loads only enabled, compatible plugins. V1 covers nodes, edges, agent tools, triggers, and listeners so all supported extension surfaces use the same lifecycle. Listener extensibility is validated with two independently packaged integrations: a WeCom long-connection listener plugin and a Lark listener plugin.

The operator story is intentionally CLI-first. Users should not need to edit `PYTHONPATH`, hand-write `sitecustomize.py`, or manually align package state across services. The supported path is `orcheo plugin install`, `orcheo plugin update`, `orcheo plugin uninstall`, and related diagnostics. Because no known Orcheo extensions currently rely on `sitecustomize`, the design removes that support outright rather than preserving a migration or deprecation track.

Full in-process hot-reload across every plugin surface is not part of the v1 design. It is substantially riskier for listener and trigger plugins because they own long-lived sessions, background tasks, and persisted cursor state. The design should instead support impact-based handling: nodes, edges, and agent tools may activate for new runs without restart when the change is hot-reloadable, while triggers and listeners continue to require restart or reconcile semantics.

## Components

- **Plugin Manifest and Metadata (SDK/runtime)**
  - Responsibility: define plugin identity, compatibility, exported component kinds, and operational metadata.
  - Key interfaces: `PluginManifest`, plugin API version, compatible Orcheo version range.

- **Plugin Entrypoint Contract (SDK/runtime)**
  - Responsibility: expose a stable `register(api)` hook for plugin packages.
  - Key interfaces: Python entry point group such as `orcheo.plugins`, registration API object, plugin context.

- **Plugin Loader (runtime)**
  - Responsibility: discover enabled installed plugins, validate compatibility, isolate failures, and invoke registration.
  - Key dependencies: plugin state store, package metadata, component registries.

- **Plugin State Store (CLI/runtime)**
  - Responsibility: persist desired plugin state, resolved versions, install source, enabled/disabled status, and compatibility decisions.
  - Key artifacts: `~/.orcheo/plugins/plugins.toml`, `~/.orcheo/plugins/plugin-lock.toml`, `~/.orcheo/plugins/venv/`, and cached manifests under `~/.cache/orcheo/plugins/`.

- **Plugin CLI (SDK/CLI)**
  - Responsibility: install, update, uninstall, enable, disable, inspect, and diagnose plugins.
  - Key commands: `orcheo plugin list/show/install/update/uninstall/enable/disable/doctor`.

- **Impact Analyzer (CLI/runtime)**
  - Responsibility: compare the installed and proposed plugin exports, classify the runtime effect, and choose silent apply versus confirmation versus restart guidance.
  - Key interfaces: export diffing, component-kind risk policy, generation activation rules.

- **Component Registries (runtime)**
  - Responsibility: receive plugin-provided nodes, edges, agent tools, triggers, and listeners.
  - Key dependencies: existing node/edge/tool registries plus new trigger/listener registries.

- **Compatibility Layer for Edge Renames (runtime/graph)**
  - Responsibility: rename built-in edges to `*Edge` classes without breaking existing graph definitions.
  - Key interfaces: alias table, warning surface, catalog display rules.

- **Validation Plugins**
  - **WeCom Listener Plugin**
    - Exercises listener registration, credential resolution, connection lifecycle, event normalization, and health surfaces.
  - **Lark Listener Plugin**
    - Exercises the same listener contract with a second provider and draws structural inspiration from the OpenClaw Lark plugin model.

- **Studio Validation Template**
  - Responsibility: prove that both new listener plugins can be used together in one builder-visible workflow.
  - Key interfaces: plugin-provided listener nodes, shared downstream logic contract, reply-routing metadata.

## Request Flows

### Flow 1: Install a plugin

1. The operator runs `orcheo plugin install <ref>`.
2. The CLI resolves the reference from a package index, Git URL, local directory, wheel, or source distribution.
3. The CLI downloads or builds the package in an Orcheo-managed plugin environment.
4. The CLI reads plugin metadata, validates the declared Orcheo/plugin API compatibility, and records the result in `plugins.toml` and `plugin-lock.toml`.
5. The CLI prints the plugin's exported component kinds and any restart/reconcile requirement.
6. On the next startup or explicit reconcile step, the runtime loads the enabled plugin.

### Flow 2: Load plugins during startup

1. Orcheo startup reads the plugin state store.
2. The loader enumerates enabled installed plugins.
3. For each plugin, the loader validates:
   - plugin API version
   - Orcheo version compatibility
   - dependency integrity
   - exported component declarations
4. The loader imports the plugin entry point and invokes `register(api)`.
5. The plugin registers nodes, edges, tools, triggers, and/or listeners into the appropriate registries.
6. If a plugin fails, the loader marks it unhealthy, emits a precise error, and continues loading unrelated plugins when safe.

### Flow 3: Update a plugin

1. The operator runs `orcheo plugin update <name>` or `orcheo plugin update --all`.
2. The CLI resolves a newer compatible version.
3. The impact analyzer compares old and new exported component identifiers and kinds.
4. If the change is additive and only affects hot-reloadable surfaces, the CLI installs silently and activates the new generation for new runs.
5. If the change replaces or removes existing nodes, edges, or agent tools, the CLI shows the impact summary and asks the maintainer whether to continue or abort.
6. If the change affects triggers or listeners, the CLI shows the impact summary and recommends restart or reconcile before the change becomes active.
7. The lockfile is updated only after the chosen action succeeds.
8. If the update cannot be applied safely, the previous locked version remains active.

**`--all` behavior with mixed impact levels:**

When `orcheo plugin update --all` encounters plugins with different impact levels, the CLI processes them in ascending impact order:

1. All silent hot-reloadable updates are applied first without prompting.
2. Plugins requiring maintainer confirmation are presented one at a time. The operator may `Continue`, `Skip this plugin`, or `Abort all remaining updates`.
3. Plugins requiring restart or reconcile are listed at the end with a consolidated restart advisory after all confirmations are resolved.
4. The lockfile records only the plugins that were successfully updated. Skipped or aborted plugins retain their previously locked version.

### Flow 4: Uninstall or disable a plugin

1. The operator runs `orcheo plugin uninstall <name>` or `orcheo plugin disable <name>`.
2. The impact analyzer determines whether exported components are additive-only, currently referenced, or tied to trigger/listener runtime state.
3. The CLI shows a prompt only when the change is non-trivial.
4. For uninstall, the CLI removes the installed package and prunes lock entries when no other plugin depends on them.
5. For disable, the package may remain installed but the loader skips it.
6. On the next startup, reconcile step, or hot-reload activation for eligible surfaces, the runtime no longer registers the plugin's components.

### Flow 5: Use a plugin-provided listener

1. The operator installs a listener plugin such as `orcheo-plugin-wecom-listener`.
2. The runtime loads the plugin and registers a new listener type with config schema, compiler hook, and runtime adapter factory.
3. A workflow uses the plugin-provided listener component.
4. Workflow activation compiles the listener subscription using the plugin's compiler contract.
5. The listener supervisor instantiates the plugin-provided adapter.
6. Incoming provider events are normalized into the shared listener payload and dispatched as workflow runs.

### Flow 6: Preserve compatibility during edge renaming

1. Core edges are renamed to classes such as `IfElseEdge`, `SwitchEdge`, and `WhileEdge`.
2. The edge registry records canonical names plus legacy aliases.
3. `orcheo edge list` displays canonical names and may annotate aliases.
4. Existing workflows using legacy names continue to load.
5. New docs and scaffolds emit the canonical `*Edge` names.

### Flow 7: Use both new listener plugins in one Studio template

1. The operator installs the WeCom and Lark plugins through `orcheo plugin install`.
2. Studio discovers the plugin-provided listener components from the backend catalog.
3. A template workflow declares both listeners and routes them into shared downstream logic such as a common agent or message-processing path.
4. Runtime listener metadata preserves the source platform and reply target so downstream nodes can branch only where transport-specific behavior is required.
5. The template serves as a builder-facing release artifact (it ships in the Studio template library) and as an automated acceptance test (it is imported and executed in CI as part of the release checklist). These roles are maintained separately: the template is versioned as a Studio asset, and the CI test imports and runs it via the Studio testing harness.

### Flow 8: Apply a hot-reloadable plugin change

1. The operator installs or updates a plugin that only adds or safely replaces nodes, edges, or agent tools.
2. The impact analyzer confirms that no trigger or listener surfaces are affected.
3. The runtime increments a monotonic in-memory plugin generation counter for that process and loads the new plugin version as the active generation for all new workflow runs in that process.
4. In-flight runs carry the generation number at which they were dispatched and continue to use their generation's registered components until they finish.
5. The runtime tracks the oldest active run generation. Once all runs referencing the previous generation complete, that generation's registered components are released.
6. Generation state is in-process only in v1; it is not written to `plugins.toml` or `plugin-lock.toml`, and it resets from the current loaded plugin set on restart. Full cross-process generation tracking is deferred to a future release.

**Note:** Hot-reload is not supported for trigger or listener plugins in v1. Any plugin change affecting those surfaces requires a restart or explicit reconcile step.

## API Contracts

### Plugin package contract

Each plugin package provides:

- A Python distribution installable by the Orcheo CLI.
- An entry point under a reserved group, for example:

```toml
[project.entry-points."orcheo.plugins"]
wecom_listener = "orcheo_plugin_wecom:plugin"
```

- A manifest file `orcheo_plugin.toml` in the package's data directory declaring the plugin-specific fields:

```toml
plugin_api_version = 1
orcheo_version = ">=0.8,<0.9"
exports = ["listeners"]
```

`name`, `version`, `description`, and `author` are read from the installed package metadata via `importlib.metadata` and must not be duplicated in `orcheo_plugin.toml`. This keeps the manifest small and prevents version drift between the package's declared version and the plugin manifest.

A `[tool.orcheo.plugin]` table in `pyproject.toml` is accepted as an alternative for packages that prefer to consolidate tooling config, but it must also contain only the three plugin-specific fields above — not `name`, `version`, `description`, or `author`.

### Runtime entry point

The entry point returns an object implementing a narrow contract:

```python
class OrcheoPlugin(Protocol):
    manifest: PluginManifest

    def register(self, api: PluginAPI) -> None: ...
```

The `PluginAPI` exposes only stable operations such as:

- `register_node(metadata, cls)`
- `register_edge(metadata, cls, aliases=())`
- `register_agent_tool(metadata, tool)`
- `register_trigger(metadata, factory)`
- `register_listener(metadata, compiler, adapter_factory)`

Plugins should not reach into internal registries directly.

### CLI contracts

Canonical command surface:

```text
orcheo plugin list
orcheo plugin show <name>
orcheo plugin install <ref>
orcheo plugin update <name>
orcheo plugin update --all
orcheo plugin uninstall <name>
orcheo plugin enable <name>
orcheo plugin disable <name>
orcheo plugin doctor
```

For non-trivial install, update, disable, or uninstall operations, the CLI may present:

1. `Continue and apply for new runs`
2. `Abort`
3. `Restart/reconcile now`

The prompt must include a concrete impact summary naming the affected exported component identifiers.

Optional P1 commands:

```text
orcheo plugin init <name>
orcheo plugin search <query>
```

`<ref>` supports:

- package name: `orcheo-plugin-wecom-listener`
- pinned package: `orcheo-plugin-wecom-listener==0.1.0`
- local path: `./plugins/orcheo-plugin-acme`
- wheel: `dist/orcheo_plugin_acme-0.1.0-py3-none-any.whl`
- Git URL: `git+https://github.com/acme/orcheo-plugin-acme.git`

### `orcheo plugin doctor` diagnostic spec

`orcheo plugin doctor` inspects the active plugin state and reports issues without making any changes. It checks:

| Check | Pass condition | Failure output |
|---|---|---|
| Plugin venv exists | `~/.orcheo/plugins/venv/` is present and contains a valid Python environment | `WARN: plugin venv missing or corrupt — run 'orcheo plugin install' to rebuild` |
| Plugin venv Python version | venv Python matches core Orcheo Python version | `ERROR: venv Python X.Y does not match core Python A.B` |
| All enabled plugins importable | Entry point modules load without error | `ERROR: plugin <name> failed to import: <exception>` |
| Manifest sha256 integrity | Installed manifest hash matches `plugin-lock.toml` | `ERROR: plugin <name> manifest hash mismatch — reinstall required` |
| Plugin API version compatibility | Each enabled plugin's declared `plugin_api_version` is supported by the running Orcheo | `ERROR: plugin <name> requires plugin API <v>, current is <v>` |
| Orcheo version range | Running Orcheo version is within each plugin's declared `orcheo_version` range | `WARN: plugin <name> declares orcheo_version <range>, running <version>` |
| No disabled-but-referenced plugins | Disabled plugins are not named as dependencies of enabled plugins | `WARN: plugin <name> is disabled but referenced by <other>` |
| Lockfile consistency | All entries in `plugin-lock.toml` have a corresponding installed wheel in the venv | `ERROR: plugin <name> is locked but not installed` |

Exit code: `0` if no ERRORs (WARNs are allowed), `1` if any ERROR is found.

### Plugin state files

`plugins.toml` stores desired state:

```toml
[[plugin]]
name = "orcheo-plugin-wecom-listener"
source = "orcheo-plugin-wecom-listener==0.1.0"
enabled = true
install_source = "cli"  # enum: cli | api | bootstrap
```

`plugins.toml` does not store any runtime generation counter. It persists desired plugin lifecycle state only, so CLI-managed install state survives restart while per-process hot-reload generations do not.

`plugin-lock.toml` stores resolved state:

```toml
[[plugin]]
name = "orcheo-plugin-wecom-listener"
version = "0.1.0"
plugin_api_version = 1
orcheo_version = "0.8.0"
location = "/Users/example/.orcheo/plugins/venv"
wheel_sha256 = "abc123..."  # SHA-256 of the downloaded wheel or sdist archive
manifest_sha256 = "def456..."  # SHA-256 of the installed orcheo_plugin.toml or [tool.orcheo.plugin] block
exports = ["listeners"]
```

Two hashes are recorded: `wheel_sha256` covers the archive as downloaded (tamper detection for cached wheels) and `manifest_sha256` covers the installed manifest (change detection for compatibility re-validation on startup).

### Filesystem layout

By default, plugin-managed runtime files live under `~/.orcheo/plugins/`, which is consistent with Orcheo's existing use of `~/.orcheo` for runtime-managed SQLite databases and stack assets. CLI profile and user-preference data should remain under `~/.config/orcheo/`, but plugin installation state belongs with runtime-managed artifacts because it directly affects backend and worker behavior.

Recommended layout:

```text
~/.orcheo/
  plugins/
    plugins.toml
    plugin-lock.toml
    venv/          # managed virtual environment for all plugin packages
    wheels/
    manifests/
```

```text
~/.cache/orcheo/
  plugins/
    downloads/
    metadata/
```

Override model:

- `ORCHEO_PLUGIN_DIR` relocates `~/.orcheo/plugins/`
- `ORCHEO_CACHE_DIR` continues to control the cache root, including `plugins/`

The design intentionally does not place installed plugin code under `~/.config/orcheo/` because that directory is already reserved for CLI configuration such as `cli.toml`, not executable runtime-managed artifacts.

### Plugin environment isolation model

All plugins share a single `uv`-managed virtual environment at `~/.orcheo/plugins/venv/`. The Orcheo runtime prepends this venv's `site-packages` path to `sys.path` after core startup, giving plugins access to both their own dependencies and the core Orcheo packages.

**Rationale for a shared plugin venv over alternatives:**

| Option | Pros | Cons | Decision |
|---|---|---|---|
| Flat append to core `sys.path` | Simplest | Plugin deps pollute core env; no isolation | Rejected |
| Shared plugin venv | Clean separation from core; `uv` resolves cross-plugin conflicts at install time | Cross-plugin dep conflicts possible | **Selected** |
| Per-plugin venv | Full isolation | High overhead; complicates shared dep deduplication | Deferred to v2 |

**Consequences:**

- `orcheo plugin install` invokes `uv pip install --python ~/.orcheo/plugins/venv` to add the plugin and its dependencies into the shared venv.
- `orcheo plugin uninstall` invokes `uv pip uninstall` and prunes any dependencies no longer referenced by other installed plugins.
- If two plugins declare conflicting transitive dependencies, `uv` reports the conflict at install time and the install is aborted with a diagnostic. The operator must resolve the conflict (e.g., by pinning a compatible version) before proceeding.
- The venv's Python version must match the core Orcheo Python version. The CLI validates this at install time.
- `ORCHEO_PLUGIN_DIR` relocates the entire `~/.orcheo/plugins/` tree, including the venv.

### Multi-service deployment considerations

Orcheo runs as separate processes: an API backend and one or more Celery workers. Both processes load plugins independently at startup from the shared `~/.orcheo/plugins/` state.

**Consequences for operators:**

- `orcheo plugin install` and `orcheo plugin uninstall` modify the shared plugin state on the host where the command runs. If the backend and workers run on the same host, they share the same `~/.orcheo/plugins/` directory and pick up the change on next restart.
- If backend and workers run on separate hosts, the operator must run `orcheo plugin install` on each host (or provision all hosts from the same shared filesystem or image).
- Hot-reloadable plugin changes (nodes, edges, agent tools) activate independently per process. Each process increments its own in-memory generation counter; there is no cross-process generation coordination in v1.
- Listener and trigger plugin changes require restarting all affected processes. `orcheo plugin update` reports which process types need restarting when the change affects those surfaces.
- `ORCHEO_PLUGIN_DIR` can point to a shared network filesystem if all processes have consistent access, but filesystem-level locking is not provided in v1; install/update operations should not run concurrently across hosts.

### Listener registration contract

Listener plugins register:

- a stable listener type identifier such as `wecom` or `lark`
- a user-facing metadata object
- a config schema
- a workflow-compilation hook
- an adapter factory for runtime session ownership
- optional health and diagnostics helpers

This design removes the need for a closed `ListenerPlatform` enum for all future platforms. Core listener payloads should use stable string identifiers for platform keys.

### Trigger registration contract

Trigger plugins follow the same registration pattern as listener plugins. A trigger plugin registers:

- a stable trigger type identifier
- a user-facing metadata object
- a config schema
- a factory that produces trigger runtime instances

The `PluginAPI` exposes `register_trigger(metadata, factory)` for this purpose. A dedicated trigger validation plugin (analogous to the WeCom/Lark listener plugins) is not included in the v1 acceptance criteria but the `register_trigger` interface is part of the v1 plugin contract so trigger plugins can be authored against it. Trigger-specific end-to-end validation is planned for a follow-on release.

### Plugin API version policy

The plugin API version is a single positive integer (for example, `1` in TOML and the runtime manifest). It increments when the `PluginAPI` contract changes in a way that is not backward-compatible with existing plugins.

**Breaking changes that increment the version:**

- Adding required parameters to `register_*` methods.
- Removing or renaming `PluginAPI` methods.
- Changing the signature of `OrcheoPlugin.register()`.
- Removing or renaming `PluginManifest` required fields.

**Non-breaking changes that do not increment the version:**

- Adding optional keyword arguments to `register_*` methods with defaults.
- Adding new `PluginAPI` methods.
- Adding optional `PluginManifest` fields.

Plugins declare a single integer: `plugin_api_version = 1`. A plugin is compatible if its declared version equals the current runtime API version. Orcheo does not support loading plugins built for a different API version. This keeps the compatibility check simple and avoids a plugin needing to support multiple API generations simultaneously.

When the plugin API version increments, all existing plugins must release a new version declaring the new API version before they can be loaded by the updated Orcheo runtime.

### Restart and reload contract

- Nodes, edges, and agent tools are hot-reloadable only in a generation-aware mode where new runs use the new plugin generation and existing runs continue on the old generation.
- Generation counters are process-local in v1. They are derived from runtime load events, not persisted in plugin state files, and may diverge across backend and worker processes until each process restarts or reloads independently.
- Silent apply is allowed only for additive or otherwise policy-approved changes to hot-reloadable surfaces.
- Replacing or removing existing exported nodes, edges, or agent tools must show a maintainer-facing impact summary before applying.
- Triggers and listeners are not hot-reloadable in v1 and must recommend restart or reconcile semantics.
- `orcheo plugin install`, `orcheo plugin update`, `orcheo plugin disable`, and `orcheo plugin uninstall` must report the required action clearly when a change is not silently applicable.

## Data Models / Schemas

### PluginManifest

The runtime `PluginManifest` object is assembled from two sources: package metadata (via `importlib.metadata`) and the `orcheo_plugin.toml` file (or `[tool.orcheo.plugin]` table).

| Field | Type | Source | Description |
|---|---|---|---|
| name | string | package metadata | Plugin package name |
| version | string | package metadata | Plugin version |
| description | string | package metadata | Human-readable summary |
| author | string | package metadata | Plugin author |
| plugin_api_version | integer | `orcheo_plugin.toml` | Supported Orcheo plugin API version |
| orcheo_version | string | `orcheo_plugin.toml` | Compatible Orcheo version range |
| exports | list[string] | `orcheo_plugin.toml` | Exported component kinds |

Fields sourced from package metadata are never written to `orcheo_plugin.toml`, eliminating version drift.

### InstalledPluginRecord

| Field | Type | Description |
|---|---|---|
| name | string | Plugin name |
| source | string | Install source reference |
| enabled | bool | Whether the plugin should be loaded |
| installed_version | string | Resolved installed version |
| status | string | `installed`, `disabled`, `incompatible`, `error` |
| last_error | string | Most recent load/install error |
| exports | list[string] | Component kinds contributed |

### PluginImpactSummary

| Field | Type | Description |
|---|---|---|
| change_type | string | `additive`, `replace`, `remove`, `mixed` |
| affected_component_kinds | list[string] | Such as `nodes`, `edges`, `agent_tools`, `triggers`, `listeners` |
| affected_component_ids | list[string] | Exported identifiers affected by the change |
| activation_mode | string | `silent_hot_reload`, `confirm_hot_reload`, `restart_required` |
| prompt_required | bool | Whether the CLI must ask the maintainer before proceeding |

### PluginStoragePaths

| Field | Type | Description |
|---|---|---|
| plugin_dir | string | Root plugin directory, defaulting to `~/.orcheo/plugins/` |
| state_file | string | Desired-state file, default `plugins.toml` under `plugin_dir` |
| lock_file | string | Resolved-state file, default `plugin-lock.toml` under `plugin_dir` |
| install_dir | string | Managed environment directory for installed plugin code |
| cache_dir | string | Download and metadata cache directory, default under `~/.cache/orcheo/plugins/` |

### Edge alias model

```json
{
  "canonical_name": "IfElseEdge",
  "aliases": ["IfElse"],
  "deprecated_aliases": ["IfElse"],
  "category": "logic"
}
```

**Deprecation lifecycle for legacy edge names:**

- When a workflow loads an edge by a legacy alias, the runtime emits a `DeprecationWarning` log line: `Edge alias 'IfElse' is deprecated; use 'IfElseEdge' instead.`
- The warning is emitted once per unique alias per process lifetime (not on every run) to avoid log spam.
- `orcheo edge list` displays only canonical names by default. Aliases are shown with a `(deprecated alias of IfElseEdge)` annotation when `--show-aliases` is passed.
- Legacy aliases will be removed in the next major Orcheo release after the `Edge` suffix migration ships. The planned removal version should be noted in the alias registry entry and in the migration guide.
- New plugin-authored edges must use only canonical names; the plugin loader rejects edge registrations that conflict with a known canonical name or deprecated alias.

### Listener plugin metadata sketch

```json
{
  "id": "wecom",
  "display_name": "WeCom Listener",
  "component_kind": "listener",
  "connection_mode": "long_connection",
  "config_schema": {
    "type": "object"
  }
}
```

### Studio validation template sketch

```text
WeComListenerPluginNode --\
                          --> SharedAgentNode --> transport-aware reply routing
LarkListenerPluginNode ---/
```

The template should prove that both plugin-provided listeners can:

- appear in the Studio catalog
- compile into valid runtime listener subscriptions
- dispatch into shared downstream workflow logic
- preserve enough metadata for correct reply transport

## Security Considerations

- Plugins are arbitrary code and must be treated as trusted-by-operator extensions, not sandboxed content.
- Operators should install plugins only from vetted sources they control or explicitly trust, such as an internal package index, a pinned wheel artifact, or a VCS reference fixed to an immutable commit.
- The install flow should present enough information for an explicit operator review before enablement: source URL or index, package name, exact version, hashes, declared exports, entry point, and resolved dependency set.
- Operator vetting guidance should require checking plugin ownership/maintainer identity, release provenance, declared dependencies, exported component kinds, and any credentials, network access, or external services the plugin will use.
- Operational guidance should recommend staging new or updated plugins in a non-production environment first, validating `orcheo plugin doctor`, and only then enabling the plugin in production.
- The loader should never execute disabled plugins.
- Secrets used by plugin listeners or triggers must continue to flow through Orcheo's credential system rather than ad hoc env vars.
- Install/update operations should use deterministic resolution, exact version pins, and recorded hashes so later audits can confirm the installed artifact matches what the operator approved.
- `orcheo plugin doctor` should surface suspicious state such as missing files, incompatible API versions, partial installs, manifest hash drift, or packages whose resolved dependencies no longer match the lockfile.

## Performance Considerations

- Plugin discovery should be based on explicit enabled state, not a full environment scan.
- Startup should cache plugin metadata and only import enabled plugins.
- Plugin failures should not force repeated expensive retry loops during a single startup.
- Listener plugins should plug into the existing supervisor model so long-lived connections remain bounded and observable.

## Testing Strategy

- **Unit tests**: manifest parsing, compatibility checks, loader behavior, edge alias resolution, CLI state transitions.
- **Integration tests**: install/update/uninstall flows in disposable environments; runtime startup with fixture plugins; plugin-provided node/edge/listener discovery; impact classification and generation-aware activation for hot-reloadable surfaces.
- **Manual QA checklist**:
  - install a fixture node plugin through CLI
  - install a fixture edge plugin and verify `orcheo edge list`
  - update an additive node or edge plugin and confirm it applies silently for new runs
  - update a replacing node or edge plugin and confirm the CLI prompts with affected component identifiers
  - disable a broken plugin and confirm healthy startup
  - install WeCom and Lark listener plugins and validate end-to-end dispatch
  - import and run a Studio template that uses both WeCom and Lark listeners

## Rollout Plan

1. Phase 1: Add plugin metadata, loader, state store, and CLI lifecycle.
2. Phase 2: Add trigger/listener registries and edge rename compatibility.
3. Phase 3: Validate with WeCom and Lark listener plugins.
4. Phase 4: Publish plugin author and operator documentation for the CLI-managed plugin workflow.

Include feature flags or config guards for the loader during early rollout if startup risk is high.

---

## Revision History

| Date | Author | Changes |
|---|---|---|
| 2026-03-16 | Codex | Initial draft |
