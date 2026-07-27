"""Security regression tests for app authorization codes and sessions."""

from __future__ import annotations

from uuid import uuid4

import pytest

from orcheo.hosted_apps import AppAuthError, AppAuthService, pkce_challenge


def test_pkce_code_is_single_use_and_session_is_exact_host_bound() -> None:
    """Code replay and sibling-alias session transfer both fail closed."""
    service = AppAuthService()
    verifier = "v" * 64
    app_id = uuid4()
    workspace_id = uuid4()
    callback = "https://portal.apps.test/__orcheo/auth/callback"
    code = service.issue_code(
        app_id=app_id,
        workspace_id=workspace_id,
        user_id="member",
        redirect_uri=callback,
        code_challenge=pkce_challenge(verifier),
    )
    issued = service.exchange(
        raw_code=code,
        verifier=verifier,
        app_host="portal.apps.test",
        redirect_uri=callback,
        runtime_generation=3,
        current_member=True,
    )
    assert issued.session.secret_hash != issued.secret
    assert (
        service.introspect(
            issued.secret,
            app_host="portal.apps.test",
            runtime_generation=3,
            current_member=True,
        ).user_id
        == "member"
    )
    with pytest.raises(AppAuthError):
        service.exchange(
            raw_code=code,
            verifier=verifier,
            app_host="portal.apps.test",
            redirect_uri=callback,
            runtime_generation=3,
            current_member=True,
        )
    with pytest.raises(AppAuthError):
        service.introspect(
            issued.secret,
            app_host="other.apps.test",
            runtime_generation=3,
            current_member=True,
        )


def test_membership_generation_and_revocation_are_rechecked() -> None:
    """Session safety never depends only on best-effort lifecycle hooks."""
    service = AppAuthService()
    verifier = "x" * 64
    callback = "https://private.apps.test/__orcheo/auth/callback"
    code = service.issue_code(
        app_id=uuid4(),
        workspace_id=uuid4(),
        user_id="member",
        redirect_uri=callback,
        code_challenge=pkce_challenge(verifier),
    )
    issued = service.exchange(
        raw_code=code,
        verifier=verifier,
        app_host="private.apps.test",
        redirect_uri=callback,
        runtime_generation=5,
        current_member=True,
    )
    for generation, membership in ((6, True), (5, False)):
        with pytest.raises(AppAuthError):
            service.introspect(
                issued.secret,
                app_host="private.apps.test",
                runtime_generation=generation,
                current_member=membership,
            )
    service.revoke(issued.secret)
    with pytest.raises(AppAuthError):
        service.introspect(
            issued.secret,
            app_host="private.apps.test",
            runtime_generation=5,
            current_member=True,
        )
