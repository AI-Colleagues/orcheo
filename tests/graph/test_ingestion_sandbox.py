"""Sandbox helpers tests."""

from __future__ import annotations
import ast
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from orcheo.graph.ingestion import sandbox
from orcheo.graph.ingestion.exceptions import ScriptIngestionError


def test_resolve_compiler_prefers_ingestion_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_compiler should use a patched ingestion module compiler."""

    def fake_compiler(source: str, filename: str, mode: str):
        return ("compiled", source, filename, mode)

    monkeypatch.setitem(
        sys.modules,
        "orcheo.graph.ingestion",
        SimpleNamespace(compile_restricted=fake_compiler),
    )

    compiler = sandbox._resolve_compiler()

    assert compiler is fake_compiler


def test_inplacevar_supports_inplace_addition() -> None:
    assert sandbox._inplacevar_("+=", 1, 2) == 3


def test_inplacevar_rejects_unknown_operator() -> None:
    with pytest.raises(ScriptIngestionError, match="Unsupported in-place operator"):
        sandbox._inplacevar_("??=", 1, 2)


def test_async_allowing_transformer_visit_await() -> None:
    """AsyncAllowingTransformer should allow await expressions."""
    transformer = sandbox.AsyncAllowingTransformer()

    # Create an Await node
    await_node = ast.Await(value=ast.Name(id="foo", ctx=ast.Load()))

    # Visit the node
    result = transformer.visit_Await(await_node)

    # The result should be the visited node
    assert isinstance(result, ast.Await)


def test_async_allowing_transformer_visit_async_function_def() -> None:
    """AsyncAllowingTransformer should allow async function definitions."""
    transformer = sandbox.AsyncAllowingTransformer()

    function_node = ast.AsyncFunctionDef(
        name="do_work",
        args=ast.arguments(
            posonlyargs=[],
            args=[],
            vararg=None,
            kwonlyargs=[],
            kw_defaults=[],
            kwarg=None,
            defaults=[],
        ),
        body=[ast.Pass()],
        decorator_list=[],
        returns=None,
        type_comment=None,
    )

    result = transformer.visit_AsyncFunctionDef(function_node)

    assert isinstance(result, ast.AsyncFunctionDef)


def test_async_allowing_transformer_visit_annassign() -> None:
    """AsyncAllowingTransformer should allow annotated assignments (PEP 526)."""
    transformer = sandbox.AsyncAllowingTransformer()

    # Create an AnnAssign node: field: int = 42
    annassign_node = ast.AnnAssign(
        target=ast.Name(id="field", ctx=ast.Store()),
        annotation=ast.Name(id="int", ctx=ast.Load()),
        value=ast.Constant(value=42),
        simple=1,
    )

    # Visit the node
    result = transformer.visit_AnnAssign(annassign_node)

    # The result should be the visited node
    assert isinstance(result, ast.AnnAssign)


def test_async_allowing_transformer_visit_name_dunder_name() -> None:
    """AsyncAllowingTransformer should allow reading __name__ special variable."""
    transformer = sandbox.AsyncAllowingTransformer()

    # Create a Name node for reading __name__
    name_node = ast.Name(id="__name__", ctx=ast.Load())

    # Visit the node
    result = transformer.visit_Name(name_node)

    # The result should be the node itself (not transformed)
    assert isinstance(result, ast.Name)
    assert result.id == "__name__"


def test_async_allowing_transformer_visit_name_non_dunder() -> None:
    """AsyncAllowingTransformer should still process regular names."""
    transformer = sandbox.AsyncAllowingTransformer()

    name_node = ast.Name(id="regular_name", ctx=ast.Load())

    result = transformer.visit_Name(name_node)

    assert isinstance(result, ast.Name)
    assert result.id == "regular_name"


def test_create_sandbox_namespace_allows_common_builtins() -> None:
    """Ensure restricted namespace exposes safe aggregate builtins."""
    namespace = sandbox.create_sandbox_namespace()
    safe_builtins = namespace["__builtins__"]

    assert safe_builtins["max"](1, 3) == 3
    assert safe_builtins["min"](1, 3) == 1


def test_create_sandbox_namespace_allows_reversed() -> None:
    """Ensure restricted namespace exposes reversed for reverse iteration."""
    namespace = sandbox.create_sandbox_namespace()
    safe_builtins = namespace["__builtins__"]

    assert list(safe_builtins["reversed"]([1, 2, 3])) == [3, 2, 1]


def test_create_sandbox_namespace_allows_getattr() -> None:
    """Ensure restricted namespace exposes getattr for attribute fallback."""
    namespace = sandbox.create_sandbox_namespace()
    safe_builtins = namespace["__builtins__"]

    assert safe_builtins["getattr"](SimpleNamespace(value=7), "value", None) == 7


def test_create_sandbox_namespace_allows_html_import() -> None:
    """Ensure restricted imports allow the html module."""
    namespace = sandbox.create_sandbox_namespace()
    restricted_import = namespace["__builtins__"]["__import__"]

    module = restricted_import("html")

    assert module is importlib.import_module("html")


def test_create_sandbox_namespace_allows_re_import() -> None:
    """Ensure restricted imports allow the re module."""
    namespace = sandbox.create_sandbox_namespace()
    restricted_import = namespace["__builtins__"]["__import__"]

    module = restricted_import("re")

    assert module is importlib.import_module("re")


def test_create_sandbox_namespace_allows_asyncio_import() -> None:
    """Ensure restricted imports allow the asyncio module."""
    namespace = sandbox.create_sandbox_namespace()
    restricted_import = namespace["__builtins__"]["__import__"]

    module = restricted_import("asyncio")

    assert module is importlib.import_module("asyncio")


def test_create_sandbox_namespace_allows_base64_import() -> None:
    """Ensure restricted imports allow the base64 module."""
    namespace = sandbox.create_sandbox_namespace()
    restricted_import = namespace["__builtins__"]["__import__"]

    module = restricted_import("base64")

    assert module is importlib.import_module("base64")


def test_create_sandbox_namespace_allows_csv_import() -> None:
    """Ensure restricted imports allow the csv module."""
    namespace = sandbox.create_sandbox_namespace()
    restricted_import = namespace["__builtins__"]["__import__"]

    module = restricted_import("csv")

    assert module is importlib.import_module("csv")


def test_create_sandbox_namespace_allows_submodule_prefix_import() -> None:
    """Ensure allow-listed prefixes include submodule imports."""
    namespace = sandbox.create_sandbox_namespace()
    restricted_import = namespace["__builtins__"]["__import__"]

    module = restricted_import("html.parser")

    assert module is importlib.import_module("html.parser")


def test_create_sandbox_namespace_allows_graph_state_import() -> None:
    """Ensure workflow scripts can import the shared graph state model."""
    namespace = sandbox.create_sandbox_namespace()
    restricted_import = namespace["__builtins__"]["__import__"]

    module = restricted_import("orcheo.graph.state")

    assert module is importlib.import_module("orcheo.graph.state")


def test_create_sandbox_namespace_loads_plugin_site_packages_for_plugin_imports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Plugin imports should add the managed plugin venv to ``sys.path``."""
    site_packages = (
        tmp_path
        / "plugins"
        / "venv"
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    module_dir = site_packages / "orcheo_plugin_fixture_runtime"
    module_dir.mkdir(parents=True)
    (module_dir / "__init__.py").write_text("VALUE = 'ok'\n", encoding="utf-8")
    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(tmp_path / "plugins"))

    namespace = sandbox.create_sandbox_namespace()
    restricted_import = namespace["__builtins__"]["__import__"]

    before = list(sys.path)
    try:
        module = restricted_import("orcheo_plugin_fixture_runtime")
        assert module.VALUE == "ok"
        assert str(site_packages) in sys.path
    finally:
        sys.path[:] = before
        sys.modules.pop("orcheo_plugin_fixture_runtime", None)


def test_create_sandbox_namespace_rejects_relative_imports() -> None:
    """Relative imports should be denied in the sandbox."""
    namespace = sandbox.create_sandbox_namespace()
    restricted_import = namespace["__builtins__"]["__import__"]

    with pytest.raises(
        ScriptIngestionError,
        match="Relative imports are not supported in LangGraph scripts",
    ):
        restricted_import("html", level=1)


def test_create_sandbox_namespace_rejects_disallowed_import() -> None:
    """Imports outside the allow-list should be denied."""
    namespace = sandbox.create_sandbox_namespace()
    restricted_import = namespace["__builtins__"]["__import__"]

    with pytest.raises(
        ScriptIngestionError,
        match="Import of module 'os' is not permitted",
    ):
        restricted_import("os")


def test_validate_script_size_accepts_equal_limit() -> None:
    """Script size validation should allow payloads equal to the limit."""
    source = "hello"

    sandbox.validate_script_size(source, max_script_bytes=len(source.encode("utf-8")))


def test_validate_script_size_rejects_payload_above_limit() -> None:
    """Script size validation should reject payloads above the configured limit."""
    with pytest.raises(
        ScriptIngestionError,
        match="LangGraph script exceeds the permitted size of 1 bytes",
    ):
        sandbox.validate_script_size("ab", max_script_bytes=1)


def test_execution_timeout_signal_path_restores_alarm_handler() -> None:
    """Signal-based timeout mode should arm and restore signal state."""

    class FakeSignal:
        SIGALRM = 14
        ITIMER_REAL = 0

        def __init__(self) -> None:
            self.previous_handler = object()
            self.installed_handler: object | None = None
            self.itimer_calls: list[float] = []
            self.signal_calls: list[object] = []

        def getsignal(self, signum: int) -> object:
            assert signum == self.SIGALRM
            return self.previous_handler

        def signal(self, signum: int, handler: object) -> None:
            assert signum == self.SIGALRM
            self.signal_calls.append(handler)
            self.installed_handler = handler

        def setitimer(self, which: int, seconds: float) -> None:
            assert which == self.ITIMER_REAL
            self.itimer_calls.append(seconds)

    class FakeThreading:
        def __init__(self) -> None:
            self._main = object()

        def current_thread(self) -> object:
            return self._main

        def main_thread(self) -> object:
            return self._main

        def gettrace(self) -> object | None:
            return None

        def settrace(self, trace: object | None) -> None:
            del trace

    fake_signal = FakeSignal()
    fake_threading = FakeThreading()

    original_signal = sandbox.signal
    try:
        sandbox.signal = fake_signal  # type: ignore[assignment]
        with sandbox.execution_timeout(
            0.25,
            threading_module=fake_threading,
            sys_module=SimpleNamespace(gettrace=lambda: None, settrace=lambda _: None),
            time_module=SimpleNamespace(perf_counter=lambda: 0.0),
        ):
            assert callable(fake_signal.installed_handler)
    finally:
        sandbox.signal = original_signal  # type: ignore[assignment]

    assert fake_signal.itimer_calls == [0.25, 0]
    assert fake_signal.signal_calls[-1] is fake_signal.previous_handler
