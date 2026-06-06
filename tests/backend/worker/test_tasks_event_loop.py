"""Tests for event loop handling in tasks.py."""

from __future__ import annotations
import asyncio
from unittest.mock import patch
import pytest


class TestGetEventLoop:
    """Tests for _get_event_loop function."""

    def test_returns_existing_open_loop(self) -> None:
        """Test that existing open event loop is returned."""
        from orcheo_backend.worker.tasks import _get_event_loop

        existing_loop = asyncio.new_event_loop()
        try:
            with patch("asyncio.get_event_loop", return_value=existing_loop):
                loop = _get_event_loop()
                assert loop is existing_loop
        finally:
            existing_loop.close()

    def test_creates_new_loop_when_closed(self) -> None:
        """Test that new loop is created when existing loop is closed."""
        from orcheo_backend.worker.tasks import _get_event_loop

        closed_loop = asyncio.new_event_loop()
        closed_loop.close()
        new_loop = asyncio.new_event_loop()

        with patch("asyncio.get_event_loop", return_value=closed_loop):
            with patch("asyncio.new_event_loop", return_value=new_loop):
                with patch("asyncio.set_event_loop") as mock_set:
                    loop = _get_event_loop()

                    assert loop is new_loop
                    mock_set.assert_called_once_with(new_loop)

        new_loop.close()

    def test_creates_new_loop_on_runtime_error(self) -> None:
        """Test that new loop is created when get_event_loop raises RuntimeError."""
        from orcheo_backend.worker.tasks import _get_event_loop

        new_loop = asyncio.new_event_loop()

        def raise_runtime_error() -> None:
            raise RuntimeError("No running event loop")

        with patch("asyncio.get_event_loop", side_effect=raise_runtime_error):
            with patch("asyncio.new_event_loop", return_value=new_loop):
                with patch("asyncio.set_event_loop") as mock_set:
                    loop = _get_event_loop()

                    assert loop is new_loop
                    mock_set.assert_called_once_with(new_loop)

        new_loop.close()

    def test_loop_is_set_when_created(self) -> None:
        """Test that set_event_loop is called when creating new loop."""
        from orcheo_backend.worker.tasks import _get_event_loop

        new_loop = asyncio.new_event_loop()

        with patch("asyncio.get_event_loop", side_effect=RuntimeError("No loop")):
            with patch("asyncio.new_event_loop", return_value=new_loop):
                with patch("asyncio.set_event_loop") as mock_set:
                    _get_event_loop()
                    mock_set.assert_called_once_with(new_loop)

        new_loop.close()
