import pytest
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ValidationError
from orcheo.nodes.registry import registry
from orcheo.nodes.triggers import (
    CronTriggerNode,
    HttpPollingTriggerNode,
    ManualTriggerNode,
    TriggerNode,
    WebhookTriggerNode,
)
from orcheo.triggers.cron import CronTriggerConfig, CronValidationError
from orcheo.triggers.http_polling import (
    HttpPollingTriggerConfig,
    HttpPollingValidationError,
)
from orcheo.triggers.manual import ManualTriggerConfig, ManualTriggerValidationError
from orcheo.triggers.webhook import RateLimitConfig, WebhookTriggerConfig


@pytest.mark.asyncio
async def test_webhook_trigger_node_run_normalizes_config() -> None:
    node = WebhookTriggerNode(
        name="webhook",
        allowed_methods=["post", "get"],
        required_headers={"X-Signature": "abc123"},
        shared_secret_header="X-Secret",
        shared_secret="secret",
        rate_limit=RateLimitConfig(limit=10, interval_seconds=30),
    )

    result = await node.run({}, RunnableConfig())

    assert result["trigger_type"] == "webhook"
    config = WebhookTriggerConfig.model_validate(result["trigger_config"])
    assert config.allowed_methods == ["GET", "POST"]
    assert config.required_headers == {"x-signature": "abc123"}
    assert config.shared_secret_header == "x-secret"
    assert config.rate_limit.limit == 10


@pytest.mark.asyncio
async def test_cron_trigger_node_run_returns_expected_config() -> None:
    node = CronTriggerNode(
        name="cron",
        expression="0 9 * * *",
        timezone="America/New_York",
        allow_overlapping=True,
    )

    result = await node.run({}, RunnableConfig())

    assert result["trigger_type"] == "cron"
    config = CronTriggerConfig.model_validate(result["trigger_config"])
    assert config.expression == "0 9 * * *"
    assert config.allow_overlapping is True


@pytest.mark.asyncio
async def test_cron_trigger_node_invalid_expression_raises() -> None:
    node = CronTriggerNode(name="bad-cron", expression="not-a-cron")

    with pytest.raises(CronValidationError):
        await node.run({}, RunnableConfig())


@pytest.mark.asyncio
async def test_manual_trigger_node_deduplicates_actors() -> None:
    node = ManualTriggerNode(
        name="manual",
        allowed_actors=["Alice", "alice", "  Bob  "],
        default_payload={"foo": "bar"},
    )

    result = await node.run({}, RunnableConfig())

    assert result["trigger_type"] == "manual"
    config = ManualTriggerConfig.model_validate(result["trigger_config"])
    assert config.allowed_actors == ["Alice", "Bob"]
    assert config.default_payload == {"foo": "bar"}


@pytest.mark.asyncio
async def test_manual_trigger_node_rejects_empty_label() -> None:
    node = ManualTriggerNode(name="invalid", label="   ")

    with pytest.raises(ManualTriggerValidationError):
        await node.run({}, RunnableConfig())


@pytest.mark.asyncio
async def test_http_polling_trigger_node_builds_config() -> None:
    node = HttpPollingTriggerNode(
        name="poller",
        url="https://api.example.com/resource",
        method="post",
        headers={"Authorization": "Bearer token"},
        interval_seconds=600,
        deduplicate_on="/id",
    )

    result = await node.run({}, RunnableConfig())

    assert result["trigger_type"] == "http_polling"
    config = HttpPollingTriggerConfig.model_validate(result["trigger_config"])
    assert config.method == "POST"
    assert config.interval_seconds == 600
    assert config.deduplicate_on == "/id"


@pytest.mark.asyncio
async def test_http_polling_trigger_node_invalid_method() -> None:
    node = HttpPollingTriggerNode(name="bad", url="https://example.com", method="TRACE")

    with pytest.raises(HttpPollingValidationError):
        await node.run({}, RunnableConfig())


@pytest.mark.asyncio
async def test_trigger_node_call_preserves_runtime_state_config() -> None:
    """``__call__`` must not clobber the runtime ``state['config']`` field.

    ``config`` is a declared ``State`` field, so the trigger dump has to stay
    namespaced under ``node_results`` rather than being hoisted to the top level.
    """
    node = CronTriggerNode(name="cron", expression="0 9 * * *")
    runtime_config = {"credentials": {"token": "secret"}}

    update = await node({"config": runtime_config}, RunnableConfig())

    # The runtime config field is untouched (not overwritten by the trigger dump).
    assert "config" not in update
    trigger_payload = update["node_results"]["cron"]
    assert trigger_payload["trigger_type"] == "cron"
    assert trigger_payload["trigger_config"]["expression"] == "0 9 * * *"


def test_trigger_nodes_registered() -> None:
    assert registry.get_node("WebhookTriggerNode") is WebhookTriggerNode
    assert registry.get_node("CronTriggerNode") is CronTriggerNode
    assert registry.get_node("ManualTriggerNode") is ManualTriggerNode
    assert registry.get_node("HttpPollingTriggerNode") is HttpPollingTriggerNode


class DummyValidationModel(BaseModel):
    value: int


class FailingTriggerNode(TriggerNode):
    """TriggerNode subclass used to reproduce validation error edge cases."""

    trigger_type: str = "dummy"
    include_ctx: bool = False
    ctx_error: object = "not-an-exception"

    def build_config(self) -> None:
        if not self.include_ctx:
            DummyValidationModel.model_validate({"value": "invalid"})
        else:
            error = ValidationError.from_exception_data(
                title="DummyConfig",
                line_errors=[
                    {
                        "type": "value_error",
                        "loc": ("value",),
                        "msg": "invalid value",
                        "ctx": {"error": self.ctx_error},
                    }
                ],
            )
            raise error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("include_ctx", "ctx_error"),
    [(False, "unused"), (True, "not-an-exception")],
)
async def test_trigger_node_run_returns_original_validation_error(
    include_ctx: bool, ctx_error: object
) -> None:
    """Assert that TriggerNode surfaces the original ValidationError instance."""
    node = FailingTriggerNode(
        name="failing", include_ctx=include_ctx, ctx_error=ctx_error
    )

    with pytest.raises(ValidationError):
        await node.run({}, RunnableConfig())
