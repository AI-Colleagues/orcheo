# Extending Orcheo with Plugins

This guide documents the supported extension path for Orcheo v1: managed
plugins installed and operated through `orcheo plugin ...`.

The old `sitecustomize` and `PYTHONPATH` import-hook workflow is no longer the
supported path. Plugins must be installed into the Orcheo-managed plugin
environment so the CLI, backend, workers, and Studio discovery flows all see
the same component set.

## Install and Operate Plugins

Install a plugin from a package name, local path, wheel, or Git URL:

```bash
orcheo plugin install "git+https://github.com/AI-Colleagues/orcheo-plugin-wecom-listener.git"
orcheo plugin install "git+https://github.com/AI-Colleagues/orcheo-plugin-lark-listener.git"
```

Inspect or manage installed plugins:

```bash
orcheo plugin list
orcheo plugin show orcheo-plugin-wecom-listener
orcheo plugin doctor
orcheo plugin disable orcheo-plugin-wecom-listener
orcheo plugin enable orcheo-plugin-wecom-listener
orcheo plugin uninstall orcheo-plugin-wecom-listener
```

Plugins are stored under `~/.orcheo/plugins/` by default:

```text
~/.orcheo/
  plugins/
    plugins.toml
    plugin-lock.toml
    venv/
    wheels/
    manifests/
```

Set `ORCHEO_PLUGIN_DIR` to relocate the managed plugin tree.

## Author a Plugin Package

Each plugin package must expose:

- A Python distribution installable through `orcheo plugin install`.
- A manifest declaring `plugin_api_version`, `orcheo_version`, and `exports`.
- An entry point in the `orcheo.plugins` group.
- A `register(api)` hook that uses the stable `PluginAPI`.

Minimal package structure:

```toml
[project]
name = "orcheo-plugin-acme"
version = "0.1.0"

[project.entry-points."orcheo.plugins"]
acme = "orcheo_plugin_acme:plugin"
```

```toml
# src/orcheo_plugin_acme/orcheo_plugin.toml
plugin_api_version = 1
orcheo_version = ">=0.0.0"
exports = ["nodes", "listeners"]
```

```python
from orcheo.plugins import PluginAPI


class AcmePlugin:
    def register(self, api: PluginAPI) -> None:
        ...


plugin = AcmePlugin()
```

## Registration API

The v1 plugin surface is intentionally narrow:

- `api.register_node(metadata, cls)`
- `api.register_edge(metadata, cls)`
- `api.register_agent_tool(metadata, tool)`
- `api.register_trigger(metadata, factory)`
- `api.register_listener(metadata, compiler, adapter_factory, aliases=())`

Plugins should not mutate Orcheo internals directly. Register through the
`PluginAPI` so the loader can track, unload, and reload plugin-owned
components safely.

### Node example

```python
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata


class AcmeNode(TaskNode):
    async def run(self, state: State, config: RunnableConfig) -> dict[str, str]:
        del state, config
        return {"value": "hello"}
```

```python
api.register_node(
    NodeMetadata(
        name="AcmeNode",
        description="Example plugin node",
        category="plugin",
    ),
    AcmeNode,
)
```

### Edge example

```python
from orcheo.edges import BaseEdge, EdgeMetadata


class AcmeEdge(BaseEdge):
    def route(self, state: dict[str, object]) -> str:
        del state
        return "default"


api.register_edge(
    EdgeMetadata(
        name="AcmeEdge",
        description="Example plugin edge",
        category="plugin",
    ),
    AcmeEdge,
)
```

### Agent tool example

```python
from langchain_core.tools import tool
from orcheo.nodes.agent_tools.registry import ToolMetadata


@tool
def say_hello(name: str) -> str:
    return f"Hello, {name}!"


api.register_agent_tool(
    ToolMetadata(
        name="say_hello",
        description="Example plugin tool",
        category="plugin",
    ),
    say_hello,
)
```

### Trigger example

```python
from orcheo.triggers.registry import TriggerMetadata


api.register_trigger(
    TriggerMetadata(
        id="acme-trigger",
        display_name="Acme Trigger",
        description="Example plugin trigger",
    ),
    lambda **kwargs: {"config": kwargs},
)
```

## Listener Plugin Contract

Listener plugins register:

- A stable platform identifier such as `wecom` or `lark`.
- User-facing `ListenerMetadata`.
- A compiler hook that turns indexed workflow listener nodes into
  `ListenerSubscription` records.
- An adapter factory that owns the long-lived runtime connection and dispatches
  normalized `ListenerDispatchPayload` objects through the repository.

For most plugins, `default_listener_compiler` is sufficient:

```python
from orcheo.listeners.registry import ListenerMetadata, default_listener_compiler

api.register_listener(
    ListenerMetadata(
        id="acme-listener",
        display_name="Acme Listener",
        description="Example listener plugin",
    ),
    default_listener_compiler,
    adapter_factory,
)
```

Adapters should:

- Accept `repository`, `subscription`, and `runtime_id`.
- Own connection lifecycle inside `run(stop_event)`.
- Normalize provider events into `ListenerDispatchPayload`.
- Report health through `ListenerHealthSnapshot`.
- Persist any provider cursor/state through the repository when needed.
- Resolve secrets through subscription config and Orcheo credentials, not ad hoc
  environment variables.

## Validation Plugins

Two reference listener plugins are available as standalone repositories and
prove the v1 listener-plugin contract end to end:

- [orcheo-plugin-wecom-listener](https://github.com/AI-Colleagues/orcheo-plugin-wecom-listener)
- [orcheo-plugin-lark-listener](https://github.com/AI-Colleagues/orcheo-plugin-lark-listener)

They:

- install through `orcheo plugin install`
- register plugin-provided listener nodes plus listener runtimes
- compile from the shared Studio template
- dispatch normalized payloads through the runtime adapter contract

The shared builder artifact is the Studio template
`template-wecom-lark-shared-listener`, backed by:

- `apps/studio/src/features/workflow/data/templates/wecom-lark-shared-listener.ts`
- `apps/studio/src/features/workflow/data/templates/assets/wecom-lark-shared-listener/workflow.py`

Install both plugins before importing that template into a runtime environment.

## Runtime Impact Rules

Plugin lifecycle commands classify changes before applying them:

- Silent hot reload: additive node, edge, or agent-tool changes for new runs.
- Confirmation-required hot reload: replacing or removing node, edge, or
  agent-tool exports.
- Restart or reconcile required: any trigger or listener change.

Nodes, edges, and agent tools reload per process. Trigger and listener plugins
own long-lived runtime state, so operators should restart or reconcile affected
processes after install, update, disable, or uninstall.

## Troubleshooting

Use `orcheo plugin doctor` first. It checks:

- plugin venv presence and Python version
- manifest hash integrity
- plugin API compatibility
- Orcheo version compatibility
- importability of enabled plugins
- disabled-plugin dependency references
- lockfile consistency

Common recovery flows:

- Broken plugin import: `orcheo plugin disable <name>` to restore healthy
  startup without deleting the package reference.
- Compatibility mismatch: update the plugin for the current
  `plugin_api_version`, or pin/downgrade Orcheo to a compatible range.
- Missing discovery entries: confirm the plugin is enabled with
  `orcheo plugin list`, then restart or reconcile if the plugin exports
  listeners or triggers.
- Suspicious local state: run `orcheo plugin doctor`; if integrity drift is
  reported, reinstall the plugin.

## Related References

- [CLI Reference](cli_reference.md)
- [SDK Reference](sdk_reference.md)
- [Releasing](releasing.md)
