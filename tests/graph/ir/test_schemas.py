"""Unit tests for restricted-mode schema-class validation and lowering."""

from __future__ import annotations
import ast
import textwrap
import pytest
from orcheo.graph.ir.exceptions import WorkflowValidationError
from orcheo.graph.ir.schemas import (
    is_schema_class,
    schema_json_schema,
    validate_schema_class,
)


def _class_def(source: str) -> ast.ClassDef:
    """Parse a single class definition from dedented ``source``."""
    node = ast.parse(textwrap.dedent(source)).body[0]
    assert isinstance(node, ast.ClassDef)
    return node


def _classes(source: str) -> dict[str, ast.ClassDef]:
    """Parse all class definitions in ``source`` keyed by name."""
    return {
        stmt.name: stmt
        for stmt in ast.parse(textwrap.dedent(source)).body
        if isinstance(stmt, ast.ClassDef)
    }


def test_is_schema_class_detects_basemodel() -> None:
    assert is_schema_class(_class_def("class Foo(BaseModel):\n    x: int")) is True


def test_is_schema_class_rejects_other_base() -> None:
    assert is_schema_class(_class_def("class Foo(CodeNode):\n    x: int = 1")) is False


def test_is_schema_class_rejects_multiple_bases() -> None:
    assert is_schema_class(_class_def("class Foo(BaseModel, Mixin):\n    x: int")) is (
        False
    )


def test_validate_schema_class_accepts_valid_declaration() -> None:
    cls = _class_def(
        '''
        class Query(BaseModel):
            """A search query."""
            text: str
            limit: int = Field(default=10, description="Max rows")
            ratio: float = 1.0
        '''
    )
    validate_schema_class(cls)  # must not raise


@pytest.mark.parametrize(
    ("source", "match"),
    [
        ("@wrap\nclass Q(BaseModel):\n    x: int", "decorators are not allowed"),
        (
            "class Q(BaseModel, metaclass=Meta):\n    x: int",
            "metaclass / class keywords",
        ),
        ("class Q(Other):\n    x: int", "must inherit only from BaseModel"),
        (
            "class Q(BaseModel):\n    def run(self):\n        pass",
            "may only declare annotated fields",
        ),
        (
            "class Q(BaseModel):\n    x.y: int",
            "fields must be simple annotated assignments",
        ),
        ("class Q(BaseModel):\n    _x: int", "may not start with '_'"),
    ],
)
def test_validate_schema_class_rejects_invalid(source: str, match: str) -> None:
    with pytest.raises(WorkflowValidationError, match=match):
        validate_schema_class(_class_def(source))


def test_schema_json_schema_lowers_primitives_and_field_metadata() -> None:
    classes = _classes(
        """
        class Query(BaseModel):
            text: str
            limit: int = Field(default=5, description="Max rows")
            ratio: float = 1.0
            enabled: bool = True
        """
    )

    schema = schema_json_schema("Query", classes)

    assert schema["type"] == "object"
    assert schema["properties"]["text"]["type"] == "string"
    assert schema["required"] == ["text"]
    assert schema["properties"]["limit"]["default"] == 5
    assert schema["properties"]["limit"]["description"] == "Max rows"


def test_schema_json_schema_supports_containers_union_and_literal() -> None:
    classes = _classes(
        """
        class Query(BaseModel):
            tags: list[str]
            meta: dict[str, int]
            mode: Literal["fast", "slow"]
            note: str | None = None
            payload: Any = None
        """
    )

    props = schema_json_schema("Query", classes)["properties"]

    assert props["tags"]["type"] == "array"
    assert props["tags"]["items"]["type"] == "string"
    assert props["meta"]["type"] == "object"
    assert set(props["mode"]["enum"]) == {"fast", "slow"}


def test_schema_json_schema_supports_nested_schema_reference() -> None:
    classes = _classes(
        """
        class Inner(BaseModel):
            value: int

        class Outer(BaseModel):
            inner: Inner
        """
    )

    schema = schema_json_schema("Outer", classes)

    assert "Inner" in schema["$defs"]
    assert schema["$defs"]["Inner"]["properties"]["value"]["type"] == "integer"


def test_schema_json_schema_rejects_recursive_reference() -> None:
    classes = _classes(
        """
        class Node(BaseModel):
            child: Node
        """
    )

    with pytest.raises(WorkflowValidationError, match="recursive schema references"):
        schema_json_schema("Node", classes)


def test_schema_json_schema_rejects_unknown_schema() -> None:
    with pytest.raises(WorkflowValidationError, match="unknown schema"):
        schema_json_schema("Missing", {})


def test_schema_json_schema_rejects_unsupported_annotation() -> None:
    classes = _classes(
        """
        class Query(BaseModel):
            when: datetime
        """
    )

    with pytest.raises(WorkflowValidationError, match="unsupported schema annotation"):
        schema_json_schema("Query", classes)


def test_schema_field_rejects_non_field_call_default() -> None:
    classes = _classes(
        """
        class Query(BaseModel):
            text: str = make_default()
        """
    )

    with pytest.raises(WorkflowValidationError, match="only Field"):
        schema_json_schema("Query", classes)


def test_schema_field_rejects_multiple_positional_defaults() -> None:
    classes = _classes(
        """
        class Query(BaseModel):
            text: str = Field("a", "b")
        """
    )

    with pytest.raises(WorkflowValidationError, match="at most one positional default"):
        schema_json_schema("Query", classes)


def test_schema_json_schema_skips_class_docstring() -> None:
    """A schema class docstring is skipped, not treated as a field."""
    classes = _classes(
        '''
        class Query(BaseModel):
            """Describes a search query."""

            text: str
        '''
    )

    schema = schema_json_schema("Query", classes)

    assert list(schema["properties"]) == ["text"]


def test_schema_json_schema_reuses_cached_model_for_repeated_reference() -> None:
    """A schema referenced from two fields is only built once (cache hit)."""
    classes = _classes(
        """
        class Inner(BaseModel):
            value: int

        class Outer(BaseModel):
            first: Inner
            second: Inner
        """
    )

    schema = schema_json_schema("Outer", classes)

    assert (
        schema["properties"]["first"]["$ref"] == schema["properties"]["second"]["$ref"]
    )
    assert list(schema["$defs"]) == ["Inner"]


def test_schema_field_accepts_single_positional_default() -> None:
    """``Field(<default>)`` with one positional argument is a valid default."""
    classes = _classes(
        """
        class Query(BaseModel):
            text: str = Field("hello")
        """
    )

    schema = schema_json_schema("Query", classes)

    assert schema["properties"]["text"]["default"] == "hello"


def test_schema_field_rejects_keyword_unpacking() -> None:
    classes = _classes(
        """
        class Query(BaseModel):
            text: str = Field(**extra)
        """
    )

    with pytest.raises(WorkflowValidationError, match="may not use \\*\\* unpacking"):
        schema_json_schema("Query", classes)


def test_schema_json_schema_rejects_unsupported_annotation_shape() -> None:
    """A non-Name/Constant/BinOp/Subscript annotation node is unsupported."""
    classes = _classes(
        """
        class Query(BaseModel):
            value: "str"
        """
    )

    with pytest.raises(WorkflowValidationError, match="unsupported schema annotation"):
        schema_json_schema("Query", classes)


def test_schema_json_schema_rejects_subscript_on_non_name_base() -> None:
    """A subscript whose base isn't a bare name (e.g. ``a.b[int]``) is rejected."""
    classes = _classes(
        """
        class Query(BaseModel):
            value: a.b[int]
        """
    )

    with pytest.raises(WorkflowValidationError, match="unsupported schema annotation"):
        schema_json_schema("Query", classes)


def test_schema_json_schema_rejects_unsupported_subscript_base() -> None:
    """A subscript with an unsupported base type (e.g. ``tuple[int]``) is rejected."""
    classes = _classes(
        """
        class Query(BaseModel):
            value: tuple[int]
        """
    )

    with pytest.raises(
        WorkflowValidationError,
        match=r"unsupported schema annotation 'tuple\[\.\.\.\]'",
    ):
        schema_json_schema("Query", classes)


def test_schema_json_schema_rejects_malformed_dict_subscript() -> None:
    """``dict[...]`` without exactly two type arguments is rejected."""
    classes = _classes(
        """
        class Query(BaseModel):
            value: dict[str]
        """
    )

    with pytest.raises(WorkflowValidationError, match="unsupported schema annotation"):
        schema_json_schema("Query", classes)
