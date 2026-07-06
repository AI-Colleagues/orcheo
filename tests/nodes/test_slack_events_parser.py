"""Tests for SlackEventsParserNode."""

from __future__ import annotations
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any
import pytest
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel
from orcheo.graph.state import State
from orcheo.nodes.connectors.slack import SlackEventsParserNode


def _sign_slack(secret: str, timestamp: int, body: str) -> str:
    base = f"v0:{timestamp}:{body}".encode()
    digest = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def _build_state(raw_body: str, headers: dict[str, str]) -> State:
    return State(
        messages=[],
        inputs={"body": {"raw": raw_body}, "headers": headers},
        node_results={},
    )


def _build_script_state(raw_body: str, headers: dict[str, str]) -> dict[str, Any]:
    return {"body": {"raw": raw_body}, "headers": headers}


def _build_mixed_state(raw_body: str, headers: dict[str, str]) -> dict[str, Any]:
    return {
        "inputs": {},
        "body": {"raw": raw_body},
        "headers": headers,
    }


class DummyBaseState(BaseModel):
    inputs: dict[str, Any]


class DummyBodyState(BaseModel):
    body: dict[str, Any]


class RawBodyMapping(Mapping[str, str]):
    def __init__(self, raw: str) -> None:
        self._raw = raw

    def __getitem__(self, key: str) -> str:
        if key == "raw":
            return self._raw
        raise KeyError(key)

    def __iter__(self):
        return iter(("raw",))

    def __len__(self) -> int:
        return 1


def test_timestamp_tolerance_validator_rejects_invalid_values() -> None:
    with pytest.raises(
        ValueError, match="timestamp_tolerance_seconds must be an integer"
    ):
        SlackEventsParserNode(
            name="slack_events_parser",
            signing_secret="slack-secret",
            timestamp_tolerance_seconds="bad",
        )

    with pytest.raises(ValueError, match="timestamp_tolerance_seconds must be >= 0"):
        SlackEventsParserNode(
            name="slack_events_parser",
            signing_secret="slack-secret",
            timestamp_tolerance_seconds=-1,
        )


def test_extract_inputs_handles_base_model_and_mappings() -> None:
    node = SlackEventsParserNode(name="slack_events_parser", signing_secret="secret")

    state = State(
        messages=[],
        inputs={"body": {"raw": "{}"}, "headers": {"x": "y"}},
        node_results={},
    )
    assert node._extract_inputs(state) == {"body": {"raw": "{}"}, "headers": {"x": "y"}}

    merged_state = {
        "inputs": {"body": {"raw": "{}"}},
        "body": {"raw": "{}"},
        "headers": {"X-Test": "value"},
        "query_params": {"foo": "bar"},
        "source_ip": "127.0.0.1",
    }
    merged = node._extract_inputs(merged_state)
    assert merged["headers"] == {"X-Test": "value"}
    assert merged["query_params"] == {"foo": "bar"}
    assert merged["source_ip"] == "127.0.0.1"

    assert node._extract_inputs(123) == {}


def test_extract_inputs_from_base_model_mapping_inputs() -> None:
    node = SlackEventsParserNode(name="slack_events_parser", signing_secret="secret")
    state = DummyBaseState(
        inputs={"body": {"raw": "{}"}, "headers": {"x-test": "value"}},
    )

    assert node._extract_inputs(state) == {
        "body": {"raw": "{}"},
        "headers": {"x-test": "value"},
    }


def test_extract_inputs_from_base_model_without_inputs() -> None:
    node = SlackEventsParserNode(name="slack_events_parser", signing_secret="secret")
    state = DummyBodyState(body={"raw": "{}"})

    assert node._extract_inputs(state) == {"body": {"raw": "{}"}}


def test_extract_raw_body_handles_supported_formats() -> None:
    node = SlackEventsParserNode(name="slack_events_parser", signing_secret="secret")
    payload = {"type": "event_callback", "event": {"type": "app_mention"}}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)

    raw_from_mapping = node._extract_raw_body({"raw": raw})
    assert raw_from_mapping[0] == raw
    assert raw_from_mapping[1] == payload

    raw_from_bytes = node._extract_raw_body(raw.encode("utf-8"))
    assert raw_from_bytes[0] == raw
    assert raw_from_bytes[1] == payload

    raw_from_dict = node._extract_raw_body(payload)
    assert raw_from_dict[0] == json.dumps(
        payload, separators=(",", ":"), ensure_ascii=True
    )
    assert raw_from_dict[1] == payload

    with pytest.raises(
        ValueError, match="Slack event payload must be a dict, string, or bytes"
    ):
        node._extract_raw_body(1234)


def test_extract_raw_body_accepts_custom_mapping() -> None:
    node = SlackEventsParserNode(name="slack_events_parser", signing_secret="secret")
    payload = {"type": "event_callback"}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)

    raw_body, parsed = node._extract_raw_body(RawBodyMapping(raw))
    assert raw_body == raw
    assert parsed == payload


def test_parse_json_returns_empty_for_invalid_data() -> None:
    node = SlackEventsParserNode(name="slack_events_parser", signing_secret="secret")
    assert node._parse_json("not json") == {}
    assert node._parse_json(json.dumps(["not", "dict"])) == {}


def test_verify_signature_validates_headers_and_tolerance() -> None:
    secret = "slack-secret"
    timestamp = int(time.time())
    raw_body = json.dumps({"type": "event"}, separators=(",", ":"), ensure_ascii=True)

    node = SlackEventsParserNode(name="slack_events_parser", signing_secret=secret)

    with pytest.raises(ValueError, match="Missing Slack signature headers"):
        node._verify_signature(raw_body, {})

    headers = {
        "x-slack-signature": "v0=bad",
        "x-slack-request-timestamp": "invalid",
    }
    with pytest.raises(ValueError, match="Invalid Slack timestamp header"):
        node._verify_signature(raw_body, headers)

    node.timestamp_tolerance_seconds = "bad"
    valid_headers = {
        "x-slack-signature": _sign_slack(secret, timestamp, raw_body),
        "x-slack-request-timestamp": str(timestamp),
    }
    with pytest.raises(
        ValueError, match="timestamp_tolerance_seconds must resolve to an integer"
    ):
        node._verify_signature(raw_body, valid_headers)

    node.timestamp_tolerance_seconds = 1
    old_timestamp = timestamp - 10
    old_headers = {
        "x-slack-signature": _sign_slack(secret, old_timestamp, raw_body),
        "x-slack-request-timestamp": str(old_timestamp),
    }
    with pytest.raises(
        ValueError, match="Slack request timestamp outside tolerance window"
    ):
        node._verify_signature(raw_body, old_headers)


def test_verify_signature_accepts_valid_headers() -> None:
    secret = "slack-secret"
    timestamp = int(time.time())
    raw_body = json.dumps(
        {"type": "event_callback"}, separators=(",", ":"), ensure_ascii=True
    )

    headers = {
        "x-slack-signature": _sign_slack(secret, timestamp, raw_body),
        "x-slack-request-timestamp": str(timestamp),
    }

    node = SlackEventsParserNode(name="slack_events_parser", signing_secret=secret)
    node._verify_signature(raw_body, headers)


def test_slack_events_parser_allows_templated_tolerance() -> None:
    node = SlackEventsParserNode(
        name="slack_events_parser",
        signing_secret="slack-secret",
        timestamp_tolerance_seconds="{{config.configurable.signature_tolerance_seconds}}",
    )

    assert (
        node.timestamp_tolerance_seconds
        == "{{config.configurable.signature_tolerance_seconds}}"
    )


@pytest.mark.asyncio
async def test_slack_events_parser_accepts_valid_event() -> None:
    secret = "slack-secret"
    timestamp = int(time.time())
    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention",
            "channel": "C123",
            "user": "U123",
            "text": "hello",
        },
    }
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    headers = {
        "x-slack-signature": _sign_slack(secret, timestamp, raw_body),
        "x-slack-request-timestamp": str(timestamp),
    }

    node = SlackEventsParserNode(
        name="slack_events_parser",
        signing_secret=secret,
        channel_id="C123",
        allowed_event_types=["app_mention"],
    )
    result = await node.run(_build_state(raw_body, headers), RunnableConfig())

    assert result["should_process"] is True
    assert result["event_type"] == "app_mention"
    assert result["channel"] == "C123"
    assert result["user"] == "U123"
    assert result["text"] == "hello"


@pytest.mark.asyncio
async def test_slack_events_parser_accepts_script_state_inputs() -> None:
    secret = "slack-secret"
    timestamp = int(time.time())
    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention",
            "channel": "C123",
            "user": "U123",
            "text": "hello",
        },
    }
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    headers = {
        "x-slack-signature": _sign_slack(secret, timestamp, raw_body),
        "x-slack-request-timestamp": str(timestamp),
    }

    node = SlackEventsParserNode(
        name="slack_events_parser",
        signing_secret=secret,
        channel_id="C123",
        allowed_event_types=["app_mention"],
    )
    result = await node.run(_build_script_state(raw_body, headers), RunnableConfig())

    assert result["should_process"] is True
    assert result["event_type"] == "app_mention"


@pytest.mark.asyncio
async def test_slack_events_parser_merges_top_level_payload() -> None:
    secret = "slack-secret"
    timestamp = int(time.time())
    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention",
            "channel": "C123",
        },
    }
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    headers = {
        "x-slack-signature": _sign_slack(secret, timestamp, raw_body),
        "x-slack-request-timestamp": str(timestamp),
    }

    node = SlackEventsParserNode(name="slack_events_parser", signing_secret=secret)
    result = await node.run(_build_mixed_state(raw_body, headers), RunnableConfig())

    assert result["should_process"] is True
    assert result["event_type"] == "app_mention"


@pytest.mark.asyncio
async def test_slack_events_parser_url_verification() -> None:
    secret = "slack-secret"
    timestamp = int(time.time())
    payload = {"type": "url_verification", "challenge": "abc"}
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    headers = {
        "x-slack-signature": _sign_slack(secret, timestamp, raw_body),
        "x-slack-request-timestamp": str(timestamp),
    }

    node = SlackEventsParserNode(name="slack_events_parser", signing_secret=secret)
    result = await node.run(_build_state(raw_body, headers), RunnableConfig())

    assert result["is_verification"] is True
    assert result["challenge"] == "abc"
    assert result["should_process"] is False


@pytest.mark.asyncio
async def test_slack_events_parser_channel_filter_blocks() -> None:
    secret = "slack-secret"
    timestamp = int(time.time())
    payload = {
        "type": "event_callback",
        "event": {"type": "app_mention", "channel": "C999"},
    }
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    headers = {
        "x-slack-signature": _sign_slack(secret, timestamp, raw_body),
        "x-slack-request-timestamp": str(timestamp),
    }

    node = SlackEventsParserNode(
        name="slack_events_parser",
        signing_secret=secret,
        channel_id="C123",
    )
    result = await node.run(_build_state(raw_body, headers), RunnableConfig())

    assert result["should_process"] is False
    assert result["event_type"] == "app_mention"


@pytest.mark.asyncio
async def test_slack_events_parser_rejects_invalid_signature() -> None:
    secret = "slack-secret"
    timestamp = int(time.time())
    payload = {
        "type": "event_callback",
        "event": {"type": "app_mention", "channel": "C123"},
    }
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    headers = {
        "x-slack-signature": _sign_slack("wrong", timestamp, raw_body),
        "x-slack-request-timestamp": str(timestamp),
    }

    node = SlackEventsParserNode(name="slack_events_parser", signing_secret=secret)

    with pytest.raises(ValueError, match="Slack signature verification failed"):
        await node.run(_build_state(raw_body, headers), RunnableConfig())


@pytest.mark.asyncio
async def test_slack_events_parser_rejects_non_dict_headers() -> None:
    node = SlackEventsParserNode(name="slack_events_parser", signing_secret="")
    with pytest.raises(ValueError, match="Slack event headers must be a dictionary"):
        await node.run({"headers": "not a dict"}, RunnableConfig())


@pytest.mark.asyncio
async def test_slack_events_parser_requires_raw_payload_for_signature() -> None:
    node = SlackEventsParserNode(name="slack_events_parser", signing_secret="secret")
    state = {"body": {"raw": ""}, "headers": {}}
    with pytest.raises(
        ValueError, match="Slack signature verification requires raw payload"
    ):
        await node.run(state, RunnableConfig())


@pytest.mark.asyncio
async def test_slack_events_parser_handles_unknown_payload_types() -> None:
    node = SlackEventsParserNode(name="slack_events_parser", signing_secret="")
    payload = {"type": "app_rate_limit", "event": {"type": "app_rate_limit"}}
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)

    result = await node.run({"body": raw_body, "headers": {}}, RunnableConfig())
    assert result["should_process"] is False
    assert result["event_type"] == "app_rate_limit"


@pytest.mark.asyncio
async def test_slack_events_parser_filters_disallowed_event_types() -> None:
    secret = "slack-secret"
    timestamp = int(time.time())
    payload = {
        "type": "event_callback",
        "event": {"type": "reaction_added", "channel": "C123"},
    }
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    headers = {
        "x-slack-signature": _sign_slack(secret, timestamp, raw_body),
        "x-slack-request-timestamp": str(timestamp),
    }

    node = SlackEventsParserNode(
        name="slack_events_parser",
        signing_secret=secret,
        allowed_event_types=["app_mention"],
    )
    result = await node.run(_build_state(raw_body, headers), RunnableConfig())
    assert result["should_process"] is False
    assert result["event_type"] == "reaction_added"


@pytest.mark.asyncio
async def test_slack_events_parser_rejects_retry_requests() -> None:
    secret = "slack-secret"
    timestamp = int(time.time())
    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention",
            "channel": "C123",
            "user": "U123",
            "text": "hello",
        },
    }
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    headers = {
        "x-slack-signature": _sign_slack(secret, timestamp, raw_body),
        "x-slack-request-timestamp": str(timestamp),
        "X-Slack-Retry-Num": "1",
        "X-Slack-Retry-Reason": "http_timeout",
    }

    node = SlackEventsParserNode(
        name="slack_events_parser",
        signing_secret=secret,
        channel_id="C123",
        allowed_event_types=["app_mention"],
    )
    result = await node.run(_build_state(raw_body, headers), RunnableConfig())

    assert result["should_process"] is False
    assert result["event_type"] is None
    assert result["event"] is None


@pytest.mark.asyncio
async def test_slack_events_parser_accepts_first_delivery_without_retry_header() -> (
    None
):
    secret = "slack-secret"
    timestamp = int(time.time())
    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention",
            "channel": "C123",
            "user": "U123",
            "text": "hello",
        },
    }
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    headers = {
        "x-slack-signature": _sign_slack(secret, timestamp, raw_body),
        "x-slack-request-timestamp": str(timestamp),
    }

    node = SlackEventsParserNode(
        name="slack_events_parser",
        signing_secret=secret,
        channel_id="C123",
        allowed_event_types=["app_mention"],
    )
    result = await node.run(_build_state(raw_body, headers), RunnableConfig())

    assert result["should_process"] is True
    assert result["event_type"] == "app_mention"
