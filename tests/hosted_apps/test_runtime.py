"""Security and idempotency tests for app-scoped workflow runtime."""

from uuid import uuid4

import pytest

from orcheo.hosted_apps import (
    AppBinding,
    AppRuntimeConflictError,
    AppRuntimeError,
    AppRuntimeService,
)


def _binding(*, authenticated: bool = False) -> AppBinding:
    workspace_id = uuid4()
    app_id = uuid4()
    return AppBinding(
        workspace_id=workspace_id,
        app_id=app_id,
        name="lookup",
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        workflow_execution_sha256="a" * 64,
        access_mode="authenticated" if authenticated else "anonymous",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1}},
            "required": ["query"],
            "additionalProperties": False,
        },
        output_projection={"fields": ["answer"]},
        visitor_can_read_output=True,
        visitor_can_read_sanitized_errors=True,
    )


def _accept(
    service: AppRuntimeService,
    binding: AppBinding,
    *,
    payload: object | None = None,
    key: str = "request-1",
    user: str | None = None,
    session_id=None,
):
    return service.accept(
        binding,
        workspace_id=binding.workspace_id,
        app_id=binding.app_id,
        release_id=uuid4(),
        deployment_id=uuid4(),
        binding_snapshot_sha256="b" * 64,
        payload={"query": "hello"} if payload is None else payload,
        idempotency_key=key,
        runtime_generation=7,
        visitor_user_id=user,
        session_id=session_id,
    )


def test_accept_is_idempotent_and_rejects_changed_payload() -> None:
    service = AppRuntimeService()
    binding = _binding()
    release_id = uuid4()
    kwargs = {
        "workspace_id": binding.workspace_id,
        "app_id": binding.app_id,
        "release_id": release_id,
        "deployment_id": uuid4(),
        "binding_snapshot_sha256": "b" * 64,
        "idempotency_key": "same",
        "runtime_generation": 2,
        "visitor_user_id": None,
        "session_id": None,
    }
    first = service.accept(binding, payload={"query": "a"}, **kwargs)
    replay = service.accept(binding, payload={"query": "a"}, **kwargs)
    assert replay.handle == first.handle
    with pytest.raises(AppRuntimeConflictError):
        service.accept(binding, payload={"query": "b"}, **kwargs)


def test_input_schema_and_authentication_are_enforced() -> None:
    service = AppRuntimeService()
    anonymous = _binding()
    with pytest.raises(AppRuntimeError):
        _accept(service, anonymous, payload={"unexpected": True})
    protected = _binding(authenticated=True)
    with pytest.raises(AppRuntimeError):
        _accept(service, protected)


def test_result_is_session_bound_projected_and_opaque() -> None:
    service = AppRuntimeService()
    binding = _binding(authenticated=True)
    session_id = uuid4()
    accepted = _accept(service, binding, user="member-1", session_id=session_id)
    service.complete(
        accepted.handle, output={"answer": "yes", "internal_secret": "hidden"}
    )
    result = service.status(
        accepted.handle,
        workspace_id=binding.workspace_id,
        app_id=binding.app_id,
        runtime_generation=7,
        visitor_user_id="member-1",
        session_id=session_id,
    )
    assert result.output == {"answer": "yes"}
    assert "workflow" not in repr(result).lower()
    with pytest.raises(AppRuntimeError):
        service.status(
            accepted.handle,
            workspace_id=binding.workspace_id,
            app_id=binding.app_id,
            runtime_generation=7,
            visitor_user_id="member-2",
            session_id=uuid4(),
        )


def test_generation_revocation_and_sanitized_errors() -> None:
    service = AppRuntimeService()
    binding = _binding()
    accepted = _accept(service, binding)
    service.complete(accepted.handle, error="vault token was invalid: secret")
    result = service.status(
        accepted.handle,
        workspace_id=binding.workspace_id,
        app_id=binding.app_id,
        runtime_generation=7,
        visitor_user_id=None,
        session_id=None,
    )
    assert result.error == "Workflow execution failed."
    with pytest.raises(AppRuntimeError):
        service.status(
            accepted.handle,
            workspace_id=binding.workspace_id,
            app_id=binding.app_id,
            runtime_generation=8,
            visitor_user_id=None,
            session_id=None,
        )


def test_output_defaults_to_no_fields() -> None:
    service = AppRuntimeService()
    binding = _binding()
    binding.output_projection = {}
    accepted = _accept(service, binding)
    service.complete(accepted.handle, output={"secret": "not returned"})
    result = service.status(
        accepted.handle,
        workspace_id=binding.workspace_id,
        app_id=binding.app_id,
        runtime_generation=7,
        visitor_user_id=None,
        session_id=None,
    )
    assert result.output is None
