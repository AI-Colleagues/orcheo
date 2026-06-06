"""Tests for async function support in LangGraph script ingestion."""

from __future__ import annotations
from orcheo.graph.ingestion.sandbox import (
    compile_langgraph_script as _compile_langgraph_script,
)


def test_compile_script_with_async_function() -> None:
    source = """
async def run_demo():
    print("This is an async function")
    return 42

def build_graph():
    return "graph_instance"
"""
    bytecode = _compile_langgraph_script(source)
    assert bytecode is not None
    assert bytecode.co_name == "<module>"


def test_compile_script_with_name_main_block() -> None:
    """Scripts with if __name__ == '__main__' blocks should compile and the
    block should not execute (because __name__ is set to '__orcheo_ingest__')."""
    source = """
import asyncio

async def run_demo():
    return "demo_result"

def build_graph():
    return "graph_instance"

if __name__ == "__main__":
    asyncio.run(run_demo())
"""
    bytecode = _compile_langgraph_script(source)
    assert bytecode is not None
    assert bytecode.co_name == "<module>"


def test_compile_script_with_sync_functions() -> None:
    source = """
def helper_function():
    return "helper"

def build_graph():
    helper = helper_function()
    return f"graph_with_{helper}"
"""
    bytecode = _compile_langgraph_script(source)
    assert bytecode is not None


def test_async_function_does_not_execute_during_import() -> None:
    source = """
async def run_demo():
    raise RuntimeError("Async function should not execute!")

def build_graph():
    return "graph_instance"
"""
    bytecode = _compile_langgraph_script(source)
    assert bytecode is not None

    namespace = {"__name__": "__test__", "__builtins__": __builtins__}
    exec(bytecode, namespace)  # noqa: S102
    assert "run_demo" in namespace
    assert "build_graph" in namespace


def test_formerly_blocked_imports_now_work() -> None:
    """Imports that were blocked by the RP allow-list are now unrestricted."""
    import socket as _socket

    source = "import socket\n"
    bytecode = _compile_langgraph_script(source)
    namespace: dict = {"__builtins__": __builtins__}
    exec(bytecode, namespace)  # noqa: S102
    assert namespace["socket"] is _socket
