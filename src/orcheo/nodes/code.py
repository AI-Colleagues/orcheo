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
transform: no imports, no ``await``, returning a state-update mapping and
referencing only its injected fields plus the passed ``state``/``config``.
"""

from __future__ import annotations
from typing import Any, cast
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode


class CodeNode(TaskNode):
    """Base class for user-authored node logic; the sole customisation port.

    Subclasses override :meth:`run` to return a state-update mapping. Declared
    model fields become the node's configurable, sandbox-injected values
    (``self.<field>``).

    Unlike a generic :class:`~orcheo.nodes.base.TaskNode`, a ``CodeNode`` returns
    a **vanilla state update**: the mapping ``run`` returns is merged straight
    through the state channel reducers (it is *not* wrapped under
    ``results.<name>``). This keeps the in-process (unrestricted) and sandboxed
    (restricted) execution paths semantically identical, since the sandbox body
    likewise returns the update directly.
    """

    async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Resolve templates and return the run result as a vanilla update."""
        runnable = self.resolved_for_run(state, config=config)
        result = await runnable.run(state, config)
        return cast(dict[str, Any], runnable._serialize_result(result))

    async def run(
        self, state: State, config: RunnableConfig
    ) -> dict[str, Any] | list[Any]:
        """Transform ``state`` and return a state-update mapping.

        Subclasses must override this method. The default raises so an
        unimplemented ``CodeNode`` fails loudly rather than silently no-op'ing.
        """
        del state, config
        msg = f"CodeNode subclass '{type(self).__name__}' must implement run()"
        raise NotImplementedError(msg)


__all__ = ["CodeNode"]
