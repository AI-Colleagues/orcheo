# Plugin Author Tutorial

This tutorial walks through creating an Orcheo plugin from scratch. By the end
you will have a working plugin that registers a custom node, and you will know
how to extend that pattern to edges, agent tools, triggers, and listeners.

## Prerequisites

- Orcheo SDK installed (`uv tool install -U orcheo-sdk`)
- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) for package management

## Start from the template

The fastest path is to clone the plugin template repository:

```bash
git clone https://github.com/AI-Colleagues/orcheo-plugin-template orcheo-plugin-acme
cd orcheo-plugin-acme
```

Before editing any code, rename the template package metadata so your local copy
uses your real plugin name instead of the example placeholder. Update the
distribution name, package directory, and entry-point target consistently across
the template files. A typical rename from `example` to `acme` looks like this:

```bash
mv src/orcheo_plugin_example src/orcheo_plugin_acme
```

Then update the remaining template placeholders:

- change the project name in `pyproject.toml` to `orcheo-plugin-acme`
- update the `orcheo.plugins` entry point in `pyproject.toml` to target
  `orcheo_plugin_acme:plugin`
- rename the plugin metadata in `src/orcheo_plugin_acme/orcheo_plugin.toml`
  to match the new package
- replace any remaining `example` references in the README, tests, and module
  docstrings

After that rename, the file paths in the rest of this tutorial will match your
plugin checkout.

## 1. Understand the plugin contract

An Orcheo plugin is a Python distribution package that:

1. Declares `plugin_api_version`, `orcheo_version`, and `exports` in an
   `orcheo_plugin.toml` manifest bundled inside the package.
2. Exposes an entry point in the `orcheo.plugins` group pointing to an object
   with a `register(api)` method.
3. Calls only the stable `PluginAPI` surface inside `register` — never mutates
   Orcheo internals directly.

Orcheo discovers and loads plugins through the managed plugin directory at
`~/.orcheo/plugins/`. The CLI is the only supported install path.

## 2. Register a custom node

Edit `src/orcheo_plugin_acme/__init__.py`:

```python
from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata
from orcheo.plugins import PluginAPI


class AcmeNode(TaskNode):
    """Example node that returns a greeting."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, str]:
        del state, config
        return {"greeting": "Hello from the Acme plugin!"}


class AcmePlugin:
    def register(self, api: PluginAPI) -> None:
        api.register_node(
            NodeMetadata(
                name="AcmeNode",
                description="Returns a greeting from the Acme plugin.",
                category="acme",
            ),
            AcmeNode,
        )


plugin = AcmePlugin()
```

The module-level `plugin` object is the entry-point target referenced in
`pyproject.toml`.

## 3. Install the plugin locally

```bash
# from the orcheo-plugin-acme directory
orcheo plugin install .
```

Verify it is registered:

```bash
orcheo plugin list
orcheo node list  # AcmeNode should appear
```

## 4. Test the plugin

### Smoke check with `doctor`

```bash
orcheo plugin doctor
```

`doctor` validates the managed plugin venv, manifest integrity, API
compatibility, and importability without making any changes.

### Unit tests

Add a test that registers the plugin in isolation so you catch interface
regressions before users do:

```python
import pytest
from orcheo.plugins import PluginAPI
from orcheo_plugin_acme import AcmeNode, AcmePlugin


def test_plugin_registers_node() -> None:
    api = PluginAPI()
    AcmePlugin().register(api)
    assert "AcmeNode" in api.registrations.nodes


@pytest.mark.asyncio()
async def test_acme_node_returns_greeting() -> None:
    node = AcmeNode(name="test")
    result = await node.run({}, {})
    assert "greeting" in result
```

Run with:

```bash
uv run pytest
```

The [plugin template repository](https://github.com/AI-Colleagues/orcheo-plugin-template)
ships with equivalent tests you can adapt.

## 5. Register additional component types

### Edge

```python
from orcheo.edges.base import BaseEdge
from orcheo.edges.registry import EdgeMetadata
from orcheo.graph.state import State
from langchain_core.runnables import RunnableConfig


class AcmeEdge(BaseEdge):
    async def run(self, state: State, config: RunnableConfig) -> str:
        del state, config
        return "default"
```

```python
api.register_edge(
    EdgeMetadata(
        name="AcmeEdge",
        description="Routes all traffic to the default branch.",
        category="acme",
    ),
    AcmeEdge,
)
```

Add `"edges"` to `exports` in `orcheo_plugin.toml`.

### Agent tool

```python
from langchain_core.tools import tool
from orcheo.nodes.agent_tools.registry import ToolMetadata


@tool
def acme_lookup(query: str) -> str:
    """Look up information from the Acme service."""
    return f"Acme result for: {query}"
```

```python
api.register_agent_tool(
    ToolMetadata(
        name="acme_lookup",
        description="Look up information from the Acme service.",
        category="acme",
    ),
    acme_lookup,
)
```

Add `"agent_tools"` to `exports` in `orcheo_plugin.toml`.

### Trigger

```python
from orcheo.triggers.registry import TriggerMetadata

api.register_trigger(
    TriggerMetadata(
        id="acme-webhook",
        display_name="Acme Webhook",
        description="Fires a workflow when the Acme webhook fires.",
    ),
    lambda **kwargs: {"config": kwargs},
)
```

Add `"triggers"` to `exports` in `orcheo_plugin.toml`.

### Listener

Listener plugins are the most involved. See the WeCom and Lark reference
plugins for complete, tested implementations:

- [orcheo-plugin-wecom-listener](https://github.com/AI-Colleagues/orcheo-plugin-wecom-listener)
- [orcheo-plugin-lark-listener](https://github.com/AI-Colleagues/orcheo-plugin-lark-listener)

A listener must supply:

- a `ListenerMetadata` with a stable platform `id`
- a compiler hook (`default_listener_compiler` covers most cases)
- an adapter factory that creates a per-subscription adapter

```python
import asyncio
from datetime import datetime
from orcheo.listeners.models import ListenerHealthSnapshot, ListenerSubscription
from orcheo.listeners.registry import ListenerMetadata, default_listener_compiler


class AcmeListenerAdapter:
    """Manages one long-lived connection for the Acme platform."""

    def __init__(
        self,
        *,
        repository: object,
        subscription: ListenerSubscription,
        runtime_id: str,
    ) -> None:
        self._repository = repository
        self._subscription = subscription
        self._runtime_id = runtime_id

    async def run(self, stop_event: asyncio.Event) -> None:
        """Hold the connection until the stop event is set."""
        # Connect to the Acme platform, receive events, dispatch payloads.
        await stop_event.wait()

    def health(self) -> ListenerHealthSnapshot:
        return ListenerHealthSnapshot(
            subscription_id=self._subscription.id,
            runtime_id=self._runtime_id,
            status="healthy",
            platform=self._subscription.platform,
            last_polled_at=datetime.now(),
        )
```

```python
api.register_listener(
    ListenerMetadata(
        id="acme-listener",
        display_name="Acme Listener",
        description="Listens for events from the Acme platform.",
    ),
    default_listener_compiler,
    lambda *, repository, subscription, runtime_id: AcmeListenerAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id=runtime_id,
    ),
)
```

Add `"listeners"` to `exports` in `orcheo_plugin.toml` so Orcheo can
classify listener installs and updates correctly.

!!! note
    Trigger and listener changes always require a process restart to become
    active. The CLI surfaces this guidance when you install or update a plugin
    that exports these component types.

## 6. Manage the plugin lifecycle

| Command | What it does |
|---|---|
| `orcheo plugin list` | Show all installed plugins and their status. |
| `orcheo plugin show orcheo-plugin-acme` | Show manifest, exports, and resolved install state. |
| `orcheo plugin install .` | Install from the local source directory. |
| `orcheo plugin update orcheo-plugin-acme` | Re-install from the stored source reference. |
| `orcheo plugin update --all` | Re-install all plugins. |
| `orcheo plugin disable orcheo-plugin-acme` | Prevent Orcheo from loading the plugin at startup. |
| `orcheo plugin enable orcheo-plugin-acme` | Re-enable a previously disabled plugin. |
| `orcheo plugin uninstall orcheo-plugin-acme` | Remove the plugin from the managed environment. |
| `orcheo plugin doctor` | Run diagnostics against all installed plugins. |

### Impact classification

When you update or uninstall a plugin, the CLI classifies the change:

- **Silent hot reload** — additive node, edge, or agent-tool change. Applies
  to new runs without a restart.
- **Confirmation-required hot reload** — a component is replaced or removed.
  The CLI shows an impact summary and asks for confirmation.
- **Restart or reconcile required** — any trigger or listener change. Restart
  the affected processes after the operation completes.

Use `--force` to skip interactive confirmation in scripted environments:

```bash
orcheo plugin update orcheo-plugin-acme --force
```

## 7. Distribute the plugin

Build a wheel:

```bash
uv build
# or: python -m build
```

Publish to PyPI (or a private index) with standard tooling. Users install by
package name, pinned version, or Git URL:

```bash
orcheo plugin install orcheo-plugin-acme
orcheo plugin install "orcheo-plugin-acme==0.1.0"
orcheo plugin install "git+https://github.com/acme-corp/orcheo-plugin-acme.git"
```

## 8. Multi-process deployments

In a Docker Compose or multi-worker deployment, backend and Celery workers run
in separate processes. Plugin state lives under `~/.orcheo/plugins/` (or
`ORCHEO_PLUGIN_DIR`). To propagate a plugin change:

1. Run `orcheo plugin install`, `update`, `uninstall`, `enable`, or `disable`
   on each host. When using the managed stack from the host CLI, pass
   `--runtime stack` to target the backend runtime directly. Or mount a shared
   `ORCHEO_PLUGIN_DIR`.
2. Restart the affected backend and worker processes. Nodes, edges, and agent
   tools support per-process hot reload for additive changes, but triggers and
   listeners always require a restart.

## 9. Reference: validation plugins

Two production-grade listener plugins are available as reference
implementations:

- [orcheo-plugin-wecom-listener](https://github.com/AI-Colleagues/orcheo-plugin-wecom-listener) —
  WeCom long-connection listener
- [orcheo-plugin-lark-listener](https://github.com/AI-Colleagues/orcheo-plugin-lark-listener) —
  Lark listener

Read their source before building a listener plugin of your own. The Studio
template `template-wecom-lark-shared-listener` shows how both listeners feed
into one shared downstream workflow — the recommended pattern when normalising
events from multiple platforms.

## Related references

- [Plugin Template](https://github.com/AI-Colleagues/orcheo-plugin-template) —
  standalone starter repository
- [Plugin Reference](custom_nodes_and_tools.md) — full authoring API reference
- [CLI Reference](cli_reference.md) — complete `orcheo plugin` commands
- [SDK Reference](sdk_reference.md) — programmatic access to plugin services
- [Releasing](releasing.md) — release process for this repository's own plugins
