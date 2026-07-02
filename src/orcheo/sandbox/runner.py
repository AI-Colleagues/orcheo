"""Server-side MicroPython-WASM runner for CodeNode bodies.

The runner wraps the ``micropython_wasm`` package. Each invocation starts a fresh
WASI WebAssembly instance with builtins only — no network, no filesystem, no
inherited environment — and per-call memory, fuel, and wall-clock limits. The
``CodeNode`` body is embedded in a trusted harness that binds ``self``/``state``/
``config`` from the JSON inputs envelope, runs the body, and prints a JSON
outputs envelope (``{"update": ...}`` on success or ``{"error": ...}`` when the
body raises). Limit breaches surface as wasm traps and are mapped to
:class:`SandboxLimitError`.
"""

from __future__ import annotations
import json
import textwrap
from collections.abc import Mapping
from typing import Any
from orcheo.sandbox.builtins import ARTIFACT_PACKAGE, ARTIFACT_VERSION
from orcheo.sandbox.exceptions import (
    SandboxError,
    SandboxExecutionError,
    SandboxLimitError,
    SandboxOutputError,
    SandboxRuntimeUnavailableError,
)


# Per-invocation linear-memory ceiling (256 MiB).
DEFAULT_MEMORY_BYTES = 256 * 1024 * 1024

# Per-invocation wasmtime fuel budget (bounds CPU work).
DEFAULT_FUEL = 600_000_000

# Per-invocation wall-clock timeout.
DEFAULT_WALL_TIMEOUT_SECONDS = 30.0

# Maximum bytes of sandbox stdout accepted as the outputs envelope.
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024 * 1024


class MicroPythonSandboxRunner:
    """Execute CodeNode bodies in an isolated MicroPython-WASM instance."""

    def __init__(
        self,
        *,
        memory_bytes: int = DEFAULT_MEMORY_BYTES,
        fuel: int = DEFAULT_FUEL,
        wall_timeout_seconds: float = DEFAULT_WALL_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        """Configure per-invocation resource limits."""
        self.memory_bytes = memory_bytes
        self.fuel = fuel
        self.wall_timeout_seconds = wall_timeout_seconds
        self.max_output_bytes = max_output_bytes

    def describe(self) -> dict[str, Any]:
        """Return diagnostics about the artifact and active limits."""
        return {
            "package": ARTIFACT_PACKAGE,
            "version": ARTIFACT_VERSION,
            "memory_bytes": self.memory_bytes,
            "fuel": self.fuel,
            "wall_timeout_seconds": self.wall_timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
        }

    def run(
        self,
        body: str,
        inputs: Mapping[str, Any],
        *,
        node_id: str | None = None,
    ) -> dict[str, Any]:
        """Run a CodeNode ``body`` against an ``inputs`` envelope.

        Returns:
            The parsed outputs envelope: ``{"update": <mapping>}`` on success or
            ``{"error": {"type", "message"}}`` when the body raised inside the
            sandbox.

        Raises:
            SandboxRuntimeUnavailableError: When the runtime cannot be loaded.
            SandboxLimitError: On a fuel/memory/wall-clock limit breach.
            SandboxOutputError: When output is missing, oversized, or not JSON.
            SandboxExecutionError: On an unexpected guest exit.
        """
        runtime = _load_runtime()
        program = self._build_program(body, inputs)
        try:
            result = runtime.run(
                program,
                memory_bytes=self.memory_bytes,
                fuel=self.fuel,
                wall_timeout_seconds=self.wall_timeout_seconds,
                host_functions=None,
                readonly_dir=None,
                host_result_bytes=self.max_output_bytes,
            )
        except runtime.MicroPythonWasmError as exc:
            raise self._map_runtime_error(exc, node_id) from exc
        return self._parse_output(result.stdout, node_id)

    def _build_program(self, body: str, inputs: Mapping[str, Any]) -> str:
        """Embed ``body`` and ``inputs`` in the trusted MicroPython harness."""
        inputs_json = json.dumps(inputs, ensure_ascii=True)
        literal = json.dumps(inputs_json)
        indented = textwrap.indent(body, "    ")
        return (
            "import json as _json\n"
            f"_DATA = _json.loads({literal})\n"
            "class _Cfg:\n    pass\n"
            "self = _Cfg()\n"
            "for _k in _DATA['configurable']:\n"
            "    setattr(self, _k, _DATA['configurable'][_k])\n"
            "state = _DATA['state']\n"
            "config = _DATA['config']\n"
            "def _run(self=self, state=state, config=config):\n"
            f"{indented}\n"
            "try:\n"
            "    _out = _run()\n"
            "    print(_json.dumps({'update': _out}))\n"
            "except Exception as _e:\n"
            "    print(_json.dumps("
            "{'error': {'type': type(_e).__name__, 'message': str(_e)}}))\n"
        )

    @staticmethod
    def _map_runtime_error(exc: Exception, node_id: str | None) -> SandboxError:
        """Map a wasm runtime error to a structured sandbox error.

        ``micropython_wasm`` raises a single ``MicroPythonWasmError`` for every
        runtime failure (no distinct limit-vs-execution subclasses), so limit
        breaches can only be told apart by message text. This token list is tied
        to the pinned artifact: re-probe and update it alongside
        :data:`~orcheo.sandbox.builtins.ARTIFACT_VERSION` when bumping the
        artifact, or a reworded limit message would be misreported as a generic
        :class:`SandboxExecutionError`.
        """
        message = str(exc)
        if any(token in message for token in ("trap", "fuel", "memory", "timed out")):
            return SandboxLimitError(
                "execution exceeded a fuel/memory/wall-clock limit", node_id=node_id
            )
        return SandboxExecutionError(
            f"sandbox runtime error: {message}", node_id=node_id
        )

    def _parse_output(self, stdout: str, node_id: str | None) -> dict[str, Any]:
        """Validate and parse the sandbox stdout as the outputs envelope."""
        if len(stdout.encode("utf-8")) > self.max_output_bytes:
            raise SandboxOutputError(
                "sandbox output exceeded the maximum size", node_id=node_id
            )
        text = stdout.strip()
        if not text:
            raise SandboxOutputError("sandbox produced no output", node_id=node_id)
        try:
            envelope = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SandboxOutputError(
                f"sandbox output was not valid JSON: {exc}", node_id=node_id
            ) from exc
        if not isinstance(envelope, dict):
            raise SandboxOutputError(
                "sandbox output envelope must be a JSON object", node_id=node_id
            )
        return envelope


def _load_runtime() -> Any:
    """Import the ``micropython_wasm`` runtime or raise a clear error."""
    try:
        import micropython_wasm
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SandboxRuntimeUnavailableError(
            f"the {ARTIFACT_PACKAGE} runtime is not installed; restricted-mode "
            "CodeNode execution requires it"
        ) from exc
    return micropython_wasm


__all__ = [
    "DEFAULT_FUEL",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_MEMORY_BYTES",
    "DEFAULT_WALL_TIMEOUT_SECONDS",
    "MicroPythonSandboxRunner",
]
