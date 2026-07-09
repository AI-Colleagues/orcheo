from __future__ import annotations
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from orcheo_backend.app.routers import workflows


def test_required_plugins_from_metadata_prefers_required_plugins_key() -> None:
    """Legacy required_plugins list is honored when requiredPlugins is absent."""
    metadata = {"template": {"required_plugins": [" foo ", "", "bar"]}}
    result = workflows._required_plugins_from_metadata(metadata)
    assert result == ["foo", "bar"]


def test_required_plugins_from_metadata_rejects_non_list() -> None:
    """Non-list values are ignored rather than raising."""
    metadata = {"template": {"required_plugins": "not-a-list"}}
    assert workflows._required_plugins_from_metadata(metadata) == []


class DummyRunnableConfig:
    def __init__(self) -> None:
        self.called = False

    def model_dump(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.called = True
        return {"foo": "bar", "mode": args, "options": kwargs}


def test_serialize_runnable_config_invokes_model_dump() -> None:
    """Serializable runnable configs are normalized via json mode dumping."""
    config = DummyRunnableConfig()
    normalized = workflows._serialize_runnable_config(config)  # type: ignore[arg-type]
    assert normalized["foo"] == "bar"
    assert config.called
    assert workflows._serialize_runnable_config(None) is None


def test_merge_frontmatter_avatar_early_return_when_both_fields_present() -> None:
    """Returns metadata unchanged when both avatar and subtitle already present."""
    metadata = {"avatar": "existing-avatar", "subtitle": "existing-subtitle"}
    result = workflows._merge_frontmatter_avatar("# any script", metadata)
    assert result is metadata


def test_merge_frontmatter_avatar_returns_metadata_on_parse_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns metadata unchanged when parse_workflow_frontmatter raises."""

    def _bad_parse(script: str) -> None:
        raise ValueError("parse failed")

    monkeypatch.setattr(workflows, "parse_workflow_frontmatter", _bad_parse)
    metadata: dict[str, str] = {}
    result = workflows._merge_frontmatter_avatar("bad script", metadata)
    assert result is metadata


def test_merge_frontmatter_avatar_fills_avatar_and_subtitle_from_frontmatter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avatar and subtitle from frontmatter are merged when absent from metadata."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        workflows,
        "parse_workflow_frontmatter",
        lambda s: SimpleNamespace(avatar="avatar-01", subtitle="My Bot"),
    )
    result = workflows._merge_frontmatter_avatar("script", {})
    assert result["avatar"] == "avatar-01"
    assert result["subtitle"] == "My Bot"


def test_script_filename_from_metadata_returns_stripped_value() -> None:
    """A well-formed source_filename is returned trimmed."""
    result = workflows._script_filename_from_metadata(
        {"source_filename": "  candidate/workflow.py  "}
    )
    assert result == "candidate/workflow.py"


def test_script_filename_from_metadata_rejects_missing_or_non_string() -> None:
    """Absent or non-string values return None instead of raising."""
    assert workflows._script_filename_from_metadata({}) is None
    assert workflows._script_filename_from_metadata({"source_filename": 123}) is None
    assert workflows._script_filename_from_metadata({"source_filename": "   "}) is None


def test_script_filename_from_metadata_rejects_overlong_value() -> None:
    """Values past the length cap are rejected rather than passed through."""
    overlong = "a" * (workflows._MAX_SCRIPT_FILENAME_LENGTH + 1)
    assert (
        workflows._script_filename_from_metadata({"source_filename": overlong}) is None
    )


def test_script_filename_from_metadata_rejects_control_characters() -> None:
    """Control characters (e.g. embedded newlines) are rejected."""
    result = workflows._script_filename_from_metadata(
        {"source_filename": "workflow.py\nEvil-Header: 1"}
    )
    assert result is None


def test_merge_configurable_schema_merges_dict_existing() -> None:
    """When existing is a dict, inline schema is merged with existing winning."""
    inline = {"key1": "inline-val", "key2": "only-inline"}
    existing = {"key1": "existing-wins", "key3": "only-existing"}
    result = workflows._merge_configurable_schema(existing, inline)
    assert result["key1"] == "existing-wins"
    assert result["key2"] == "only-inline"
    assert result["key3"] == "only-existing"


def test_apply_configurable_schema_order_records_key_order() -> None:
    """The authored field order is captured as a sibling array before JSONB."""
    metadata = {
        "configurable_schema": {
            "zebra": {"type": "string"},
            "apple": {"type": "string"},
            "mango": {"type": "string"},
        }
    }
    result = workflows._apply_configurable_schema_order(metadata)
    assert result["configurable_schema_order"] == ["zebra", "apple", "mango"]


def test_apply_configurable_schema_order_noop_without_schema() -> None:
    """Metadata without a configurable_schema dict is returned unchanged."""
    assert workflows._apply_configurable_schema_order({}) == {}
    assert workflows._apply_configurable_schema_order({"configurable_schema": {}}) == {
        "configurable_schema": {}
    }


def test_apply_configurable_schema_order_preserves_caller_order() -> None:
    """An explicit caller-supplied order is left untouched."""
    metadata = {
        "configurable_schema": {"a": {"type": "string"}, "b": {"type": "string"}},
        "configurable_schema_order": ["b", "a"],
    }
    result = workflows._apply_configurable_schema_order(metadata)
    assert result["configurable_schema_order"] == ["b", "a"]


def test_resolve_ingest_configurable_schema_raises_400_on_schema_error() -> None:
    """ConfigurableSchemaError from split_configurable is re-raised as HTTP 400."""
    from orcheo.runtime.runnable_config import RunnableConfigModel as RC

    rc = RC(configurable={"model": {"type": "string", "properties": {}}})
    with pytest.raises(HTTPException) as exc_info:
        workflows._resolve_ingest_configurable_schema(rc, {})
    assert exc_info.value.status_code == 400


def test_resolve_ingest_configurable_schema_merges_inline_schema() -> None:
    """When split_configurable returns an inline schema, it is merged into metadata."""
    from orcheo.runtime.runnable_config import RunnableConfigModel as RC

    rc = RC(
        configurable={
            "model": {
                "type": "string",
                "enum": ["gpt-4", "gpt-3.5"],
                "default": "gpt-4",
            }
        }
    )
    result_rc, result_meta = workflows._resolve_ingest_configurable_schema(rc, {})
    assert result_meta.get("configurable_schema") is not None
    assert "model" in result_meta["configurable_schema"]
    assert result_rc is not None
    assert result_rc.configurable.get("model") == "gpt-4"


def test_attach_mermaid_falls_back_when_no_index_mermaid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Branch 233->236: when index mermaid is absent, render_mermaid_from_graph_payload is called."""
    from orcheo.models import WorkflowVersion

    called_with: list[object] = []

    def _fake_render(graph_payload: object) -> str:
        called_with.append(graph_payload)
        return "fallback-mermaid"

    monkeypatch.setattr(workflows, "render_mermaid_from_graph_payload", _fake_render)

    version = WorkflowVersion(
        workflow_id=uuid4(),
        version=1,
        graph={"index": {"nodes": []}, "format": "langgraph-script"},
        created_by="tester",
    )
    result = workflows._attach_mermaid(version)
    assert result.mermaid == "fallback-mermaid"
    assert len(called_with) == 1


@pytest.mark.asyncio()
async def test_get_workflow_schedule_summary_returns_true_when_config_exists() -> None:
    """Returns True when get_cron_trigger_config succeeds without raising."""

    class _CronSuccessRepo:
        async def get_cron_trigger_config(self, workflow_id: UUID) -> object:
            return object()

    result = await workflows._get_workflow_schedule_summary(
        _CronSuccessRepo(),  # type: ignore[arg-type]
        uuid4(),
    )
    assert result is True


def test_http_exception_detail_message_returns_str_detail_directly() -> None:
    """A plain string detail is returned as-is (line 389-390)."""
    exc = HTTPException(status_code=400, detail="plain string detail")
    assert workflows._http_exception_detail_message(exc) == "plain string detail"


def test_http_exception_detail_message_extracts_message_from_dict_detail() -> None:
    """Covers lines 391-394: dict detail with a string "message" key."""
    exc = HTTPException(status_code=400, detail={"message": "structured failure"})
    assert workflows._http_exception_detail_message(exc) == "structured failure"


def test_http_exception_detail_message_falls_back_when_dict_lacks_message() -> None:
    """Covers line 395: dict detail without a usable "message" key falls back
    to str(detail)."""
    exc = HTTPException(status_code=400, detail={"code": "missing_message"})
    assert workflows._http_exception_detail_message(exc) == str(
        {"code": "missing_message"}
    )


def test_http_exception_detail_message_falls_back_when_message_not_str() -> None:
    """Covers line 395: dict detail whose "message" value is not a string."""
    exc = HTTPException(status_code=400, detail={"message": 404})
    assert workflows._http_exception_detail_message(exc) == str({"message": 404})


def test_http_exception_detail_message_falls_back_for_non_str_non_dict_detail() -> None:
    """Covers line 395: detail is neither a string nor a dict."""
    exc = HTTPException(status_code=400, detail=["not", "a", "dict"])
    assert workflows._http_exception_detail_message(exc) == str(["not", "a", "dict"])
