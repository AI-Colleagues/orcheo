"""Tests for the public schema-authoring facade."""

from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field
from orcheo import schema


def test_facade_reexports_pydantic_and_typing_symbols() -> None:
    """The facade re-exports the exact objects authors need for schema classes."""
    assert schema.BaseModel is BaseModel
    assert schema.Field is Field
    assert schema.Any is Any
    assert schema.Literal is Literal
    assert schema.__all__ == ["Any", "BaseModel", "Field", "Literal"]
