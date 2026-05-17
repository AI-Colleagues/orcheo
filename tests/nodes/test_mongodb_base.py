"""Unit tests for MongoDB client utilities."""

from __future__ import annotations
import re
from decimal import Decimal
from uuid import UUID
import pytest
from bson.binary import Binary
from bson.decimal128 import Decimal128
from bson.regex import Regex
from bson.timestamp import Timestamp
from orcheo.nodes.storage.mongodb.base import (
    MongoDBClientNode,
    MongoDBNode,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (Decimal128("1.23"), "1.23"),
        (Decimal("4.56"), "4.56"),
        (Timestamp(1, 2), {"time": 1, "inc": 2}),
        (Regex("pattern", "i"), {"pattern": "pattern", "flags": re.IGNORECASE}),
        (UUID(int=1), str(UUID(int=1))),
        (Binary(b"abc"), "YWJj"),
    ],
)
def test_encode_special_bson_value_handles_known_types(value, expected) -> None:
    handled, encoded = MongoDBClientNode._encode_special_bson_value(value)
    assert handled
    assert encoded == expected


def test_mongodb_node_resolve_projection_requires_value() -> None:
    node = MongoDBNode(
        name="projection_test",
        connection_string="mongodb://localhost",
        database="db",
        collection="col",
        operation="find",
    )

    with pytest.raises(ValueError, match="projection is not set for this operation"):
        node._resolve_projection()
