"""The ``CodeNode`` customisation port for user-authored logic.

``CodeNode`` is the *only* inheritable base class for custom workflow logic.
Subclasses implement :meth:`run` and may declare configurable fields as Pydantic
model fields. Two execution paths exist:

* **Unrestricted mode** runs the whole ``workflow.py`` in-process, so a
  ``CodeNode`` behaves like any :class:`~orcheo.nodes.base.TaskNode`.
* **Restricted mode** never executes the script. The restricted-AST interpreter
  extracts each ``run`` body to the frozen IR as a string, and the body executes
  per invocation inside the MicroPython-WASM sandbox (Milestone 3) with
  builtins only and JSON-coercible state/config/configurable inputs.

Because the body must run unchanged in the sandbox, it is a pure synchronous
transform: no imports, no ``await``, returning this node's result payload and
referencing only its injected fields plus the passed ``state``/``config``.
"""

from __future__ import annotations
from typing import Any
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


@registry.register(
    NodeMetadata(
        name="CodeNode",
        description=(
            "Base class for user-authored node logic; the sole customisation "
            "port. Do not use this node directly, but inherit from this with "
            "your own `run` method."
        ),
        category="data",
    )
)
class CodeNode(TaskNode):
    """Base class for user-authored node logic; the sole customisation port.

    Subclasses override :meth:`run` to return this node's result payload. Declared
    model fields become the node's configurable, sandbox-injected values
    (``self.<field>``).

    Like :class:`~orcheo.nodes.base.TaskNode`, the mapping ``run`` returns is
    stored under ``results.<name>``. This keeps custom node bodies focused on
    their own result payload instead of the graph state update contract.
    """

    async def run(
        self, state: State, config: RunnableConfig
    ) -> dict[str, Any] | list[Any]:
        """Transform ``state`` and return this node's result payload.

        Subclasses must override this method. The default raises so an
        unimplemented ``CodeNode`` fails loudly rather than silently no-op'ing.
        """
        del state, config
        msg = f"CodeNode subclass '{type(self).__name__}' must implement run()"
        raise NotImplementedError(msg)


__all__ = ["CodeNode"]
