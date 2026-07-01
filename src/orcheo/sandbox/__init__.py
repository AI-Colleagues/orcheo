"""MicroPython-WASM sandbox for CodeNode bodies (restricted-mode execution).

Submodules are imported explicitly to avoid loading the WASM runtime (and its
native ``wasmtime`` dependency) unless the sandbox is actually used:

* :mod:`orcheo.sandbox.builtins` — the builtin allowlist tied to the pinned
  artifact (lightweight; safe to import at ingestion).
* :mod:`orcheo.sandbox.runner` — the MicroPython-WASM runner.
* :mod:`orcheo.sandbox.marshalling` — JSON I/O envelope and state projection.
* :mod:`orcheo.sandbox.code_node` — the sandbox-backed CodeNode runnable and the
  IR graph-builder wiring.
"""
