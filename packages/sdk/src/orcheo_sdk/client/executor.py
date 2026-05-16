"""HTTP helpers for running workflows against the Orcheo backend."""

from __future__ import annotations
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
import httpx
from httpx import Response
from orcheo_sdk.client.orcheo_client import OrcheoClient


def _default_sleep(delay: float) -> None:
    from time import sleep  # pragma: no cover - import for delegated sleep

    sleep(delay)  # pragma: no cover - simple delegation to time.sleep


@dataclass(slots=True)
class HttpWorkflowExecutor:
    """Synchronous helper that executes workflows via the HTTP API."""

    client: OrcheoClient
    auth_token: str | None = None
    timeout: float = 30.0
    max_retries: int = 3
    backoff_factor: float = 0.5
    transport: httpx.BaseTransport | None = None
    http_client: httpx.Client | None = field(default=None, repr=False)
    sleep: Callable[[float], None] = field(
        default=_default_sleep, repr=False, compare=False
    )
    retry_statuses: tuple[int, ...] = field(default=(500, 502, 503, 504), repr=False)

    def trigger_run(
        self,
        workflow_id: str,
        *,
        workflow_version_id: str,
        triggered_by: str,
        inputs: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        runnable_config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a workflow run and return the backend payload."""
        url = self.client.workflow_trigger_url(workflow_id)
        request_headers = self._build_headers(headers)
        payload: dict[str, Any] = {
            "workflow_version_id": workflow_version_id,
            "triggered_by": triggered_by,
            "input_payload": dict(inputs or {}),
        }
        if runnable_config is not None:
            payload["runnable_config"] = runnable_config

        attempt = 0
        delay = self.backoff_factor
        last_exception: Exception | None = None
        while attempt <= self.max_retries:
            try:
                response = self._post(url, payload, request_headers)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                last_exception = exc
                status_code = exc.response.status_code
                if not self._should_retry(status_code) or attempt == self.max_retries:
                    msg = f"Failed to trigger workflow run (status {status_code})"
                    raise RuntimeError(msg) from exc
            except httpx.HTTPError as exc:
                last_exception = exc
                if attempt == self.max_retries:
                    raise RuntimeError("Failed to trigger workflow run") from exc

            attempt += 1
            if attempt <= self.max_retries and delay > 0:
                self.sleep(delay)
                delay *= 2

        raise RuntimeError(
            "Failed to trigger workflow run"
        ) from last_exception  # pragma: no cover - defensive fallback

    def _build_headers(self, overrides: Mapping[str, str] | None) -> dict[str, str]:
        headers = self.client.prepare_headers(overrides or {})
        if self.auth_token and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def validate_credentials(
        self,
        workflow_id: str,
        *,
        actor: str = "system",
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Trigger credential validation and return the backend response."""
        workflow_id = workflow_id.strip()
        if not workflow_id:
            msg = "workflow_id cannot be empty"
            raise ValueError(msg)
        url = (
            f"{self.client.base_url.rstrip('/')}/api/workflows/"
            f"{workflow_id}/credentials/validate"
        )
        payload = {"actor": actor}
        response = self._post(url, payload, self._build_headers(headers))
        response.raise_for_status()
        return response.json()

    def _post(
        self,
        url: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> Response:
        client = self.http_client
        if client is not None:
            return client.post(url, json=payload, headers=headers, timeout=self.timeout)

        request_kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport is not None:
            request_kwargs["transport"] = self.transport

        base_url = self.client.base_url.rstrip("/")
        request_kwargs["base_url"] = base_url
        relative_url = self._relative_url(url, base_url)

        with httpx.Client(**request_kwargs) as http_client:
            return http_client.post(relative_url, json=payload, headers=headers)

    def _get(
        self,
        url: str,
        headers: Mapping[str, str],
    ) -> Response:
        client = self.http_client
        if client is not None:
            return client.get(url, headers=headers, timeout=self.timeout)

        request_kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport is not None:
            request_kwargs["transport"] = self.transport

        base_url = self.client.base_url.rstrip("/")
        request_kwargs["base_url"] = base_url
        relative_url = self._relative_url(url, base_url)

        with httpx.Client(**request_kwargs) as http_client:
            return http_client.get(relative_url, headers=headers)

    @staticmethod
    def _relative_url(url: str, base_url: str) -> str:
        if url.startswith(base_url):
            suffix = url[len(base_url) :]
            return suffix or "/"
        return url

    def _should_retry(self, status_code: int) -> bool:
        return status_code in self.retry_statuses
