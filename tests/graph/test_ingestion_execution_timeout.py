"""Tests for the ingestion execution timeout context manager."""

from __future__ import annotations
import itertools
from types import SimpleNamespace
import pytest
from orcheo.graph.ingestion.loader import execution_timeout


def test_execution_timeout_disabled_for_non_positive_values() -> None:
    with execution_timeout(0):
        assert True


def test_execution_timeout_trace_fallback_enforces_deadline() -> None:
    class FakeSys:
        def __init__(self) -> None:
            self._trace: object | None = None
            self.calls: list[object | None] = []

        def gettrace(self) -> object | None:
            return self._trace

        def settrace(self, trace: object | None) -> None:
            self.calls.append(trace)
            self._trace = trace

    class FakeThreading:
        def __init__(self) -> None:
            self._trace: object | None = None
            self._current_thread = object()
            self._main_thread = object()
            self.calls: list[object | None] = []

        def current_thread(self) -> object:
            return self._current_thread

        def main_thread(self) -> object:
            return self._main_thread

        def gettrace(self) -> object | None:
            return self._trace

        def settrace(self, trace: object | None) -> None:
            self.calls.append(trace)
            self._trace = trace

    fake_sys = FakeSys()
    fake_threading = FakeThreading()
    perf_counter_values = itertools.chain([0.0, 0.2], itertools.repeat(0.2))
    fake_time = SimpleNamespace(perf_counter=lambda: next(perf_counter_values))

    original_trace = fake_sys.gettrace()
    original_thread_trace = fake_threading.gettrace()

    with pytest.raises(TimeoutError):
        with execution_timeout(
            0.1,
            sys_module=fake_sys,
            threading_module=fake_threading,
            time_module=fake_time,
        ):
            trace = fake_sys.gettrace()
            assert callable(trace)
            next_trace = trace(None, "call", None)
            assert next_trace is trace
            next_trace(None, "line", None)

    assert fake_sys.gettrace() is original_trace
    assert fake_threading.gettrace() is original_thread_trace
    assert fake_sys.calls[-1] is None
    assert fake_threading.calls[-1] is None


def test_execution_timeout_restores_existing_traces() -> None:
    class FakeSys:
        def __init__(self) -> None:
            self._trace: object | None = object()
            self.calls: list[object | None] = []

        def gettrace(self) -> object | None:
            return self._trace

        def settrace(self, trace: object | None) -> None:
            self.calls.append(trace)
            self._trace = trace

    class FakeThreading:
        def __init__(self) -> None:
            self._trace: object | None = object()
            self._current_thread = object()
            self._main_thread = object()
            self.calls: list[object | None] = []

        def current_thread(self) -> object:
            return self._current_thread

        def main_thread(self) -> object:
            return self._main_thread

        def gettrace(self) -> object | None:
            return self._trace

        def settrace(self, trace: object | None) -> None:
            self.calls.append(trace)
            self._trace = trace

    fake_sys = FakeSys()
    fake_threading = FakeThreading()
    fake_time = SimpleNamespace(perf_counter=lambda: 0.0)

    original_trace = fake_sys.gettrace()
    original_thread_trace = fake_threading.gettrace()

    with execution_timeout(
        0.1,
        sys_module=fake_sys,
        threading_module=fake_threading,
        time_module=fake_time,
    ):
        trace = fake_sys.gettrace()
        assert callable(trace)
        returned = trace(None, "call", None)
        assert returned is trace

    assert fake_sys.gettrace() is original_trace
    assert fake_threading.gettrace() is original_thread_trace
    assert fake_sys.calls[-1] is original_trace
    assert fake_threading.calls[-1] is original_thread_trace


def test_execution_timeout_signal_path_restores_alarm_handler() -> None:
    """Signal-based timeout mode should arm and restore signal state."""
    import signal as _signal
    from orcheo.graph.ingestion.loader import execution_timeout

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

    fake_signal_obj = FakeSignal()
    fake_threading = FakeThreading()

    import orcheo.graph.ingestion.loader as loader_module

    original_signal = loader_module.signal
    try:
        loader_module.signal = fake_signal_obj  # type: ignore[assignment]
        with execution_timeout(
            0.25,
            threading_module=fake_threading,
            sys_module=SimpleNamespace(gettrace=lambda: None, settrace=lambda _: None),
            time_module=SimpleNamespace(perf_counter=lambda: 0.0),
        ):
            assert callable(fake_signal_obj.installed_handler)
    finally:
        loader_module.signal = original_signal  # type: ignore[assignment]

    assert fake_signal_obj.itimer_calls == [0.25, 0]
    assert fake_signal_obj.signal_calls[-1] is fake_signal_obj.previous_handler
