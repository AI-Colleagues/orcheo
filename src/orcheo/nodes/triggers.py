"""Trigger node implementations providing SDK/UI parity."""

from __future__ import annotations
from datetime import datetime
from typing import Any
from langchain_core.runnables import RunnableConfig
from pydantic import AnyHttpUrl, Field, ValidationError
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.http_polling import HttpPollingTriggerConfig
from orcheo.triggers.manual import ManualTriggerConfig
from orcheo.triggers.webhook import RateLimitConfig, WebhookTriggerConfig


class TriggerNode(TaskNode):
    """Common helper implementing trigger node execution contract."""

    trigger_type: str

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return the trigger configuration payload."""
        try:
            config_model = self.build_config()
        except ValidationError as exc:
            raise self._unwrap_validation_error(exc) from exc

        # NB: use ``trigger_config`` rather than ``config`` — ``config`` is a
        # declared ``State`` field, so returning it here would hoist the trigger
        # dump to the top level and clobber the runtime ``state["config"]`` used
        # for credential/ChatKit resolution (see ``build_task_state_update``).
        return {
            "trigger_type": self.trigger_type,
            "trigger_config": config_model.model_dump(mode="json"),
        }

    def build_config(self) -> Any:
        """Return the trigger configuration model."""
        raise NotImplementedError  # pragma: no cover

    @staticmethod
    def _unwrap_validation_error(error: ValidationError) -> Exception:
        """Return domain-specific validation errors when available."""
        for details in error.errors():
            context = details.get("ctx")
            if isinstance(context, dict):
                candidate = context.get("error")
                if isinstance(candidate, Exception):
                    return candidate
        return error


@registry.register(
    NodeMetadata(
        name="WebhookTriggerNode",
        description="Configure an HTTP webhook trigger.",
        category="trigger",
    )
)
class WebhookTriggerNode(TriggerNode):
    """Node encapsulating webhook trigger configuration."""

    trigger_type: str = "webhook"
    allowed_methods: list[str] = Field(default_factory=lambda: ["POST"])
    required_headers: dict[str, str] = Field(default_factory=dict)
    required_query_params: dict[str, str] = Field(default_factory=dict)
    shared_secret_header: str | None = None
    shared_secret: str | None = None
    rate_limit: RateLimitConfig | None = None

    def build_config(self) -> WebhookTriggerConfig:
        """Construct the webhook trigger configuration model."""
        return WebhookTriggerConfig(
            allowed_methods=self.allowed_methods,
            required_headers=self.required_headers,
            required_query_params=self.required_query_params,
            secret_header=self.shared_secret_header,
            shared_secret=self.shared_secret,
            rate_limit=self.rate_limit,
        )


@registry.register(
    NodeMetadata(
        name="CronTriggerNode",
        description="Configure a cron-based schedule trigger.",
        category="trigger",
    )
)
class CronTriggerNode(TriggerNode):
    """Node encapsulating cron trigger configuration."""

    trigger_type: str = "cron"
    expression: str = Field(default="0 * * * *")
    timezone: str = Field(default="UTC")
    allow_overlapping: bool = True
    start_at: datetime | None = None
    end_at: datetime | None = None

    def build_config(self) -> CronTriggerConfig:
        """Construct the cron trigger configuration model."""
        return CronTriggerConfig(
            expression=self.expression,
            timezone=self.timezone,
            allow_overlapping=self.allow_overlapping,
            start_at=self.start_at,
            end_at=self.end_at,
        )


@registry.register(
    NodeMetadata(
        name="ManualTriggerNode",
        description="Trigger workflows manually from the dashboard.",
        category="trigger",
    )
)
class ManualTriggerNode(TriggerNode):
    """Node encapsulating manual trigger configuration."""

    trigger_type: str = "manual"
    label: str = Field(default="manual")
    allowed_actors: list[str] = Field(default_factory=list)
    require_comment: bool = False
    default_payload: dict[str, Any] = Field(default_factory=dict)
    cooldown_seconds: int = Field(default=0)

    def build_config(self) -> ManualTriggerConfig:
        """Construct the manual trigger configuration model."""
        return ManualTriggerConfig(
            label=self.label,
            allowed_actors=self.allowed_actors,
            require_comment=self.require_comment,
            default_payload=self.default_payload,
            cooldown_seconds=self.cooldown_seconds,
        )


@registry.register(
    NodeMetadata(
        name="HttpPollingTriggerNode",
        description="Poll an HTTP endpoint on an interval to trigger runs.",
        category="trigger",
    )
)
class HttpPollingTriggerNode(TriggerNode):
    """Node encapsulating HTTP polling trigger configuration."""

    trigger_type: str = "http_polling"
    url: AnyHttpUrl
    method: str = Field(default="GET")
    headers: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] | None = None
    interval_seconds: int = Field(default=300)
    timeout_seconds: int = Field(default=30)
    verify_tls: bool = True
    follow_redirects: bool = False
    deduplicate_on: str | None = None

    def build_config(self) -> HttpPollingTriggerConfig:
        """Construct the HTTP polling trigger configuration model."""
        return HttpPollingTriggerConfig(
            url=self.url,
            method=self.method,
            headers=self.headers,
            query_params=self.query_params,
            body=self.body,
            interval_seconds=self.interval_seconds,
            timeout_seconds=self.timeout_seconds,
            verify_tls=self.verify_tls,
            follow_redirects=self.follow_redirects,
            deduplicate_on=self.deduplicate_on,
        )


__all__ = [
    "CronTriggerNode",
    "HttpPollingTriggerNode",
    "ManualTriggerNode",
    "TriggerNode",
    "WebhookTriggerNode",
]
