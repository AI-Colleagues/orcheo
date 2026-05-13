"""Tests for the workspaces router covering all missing branches."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from orcheo.workspace import (
    Role,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceMembership,
    WorkspaceMembershipError,
    WorkspaceMembershipLimitError,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
    WorkspaceSlugConflictError,
    WorkspaceStatus,
)
from orcheo_backend.app.authentication import RequestContext
from orcheo_backend.app.workspace import WorkspaceHTTPError, raise_workspace_forbidden
from orcheo_backend.app.routers.workspaces import (
    _to_audit_event_response,
    _to_membership_response,
    _to_workspace_response,
    create_own_workspace,
    create_workspace,
    delete_workspace,
    get_workspace,
    list_my_memberships,
    list_workspace_audit_events,
    purge_deleted_workspaces,
    update_workspace_status,
    add_workspace_member,
    update_workspace_member_role,
    remove_workspace_member,
)
from orcheo_backend.app.schemas.workspaces import (
    MembershipCreateRequest,
    MembershipRoleUpdateRequest,
    WorkspaceCreateRequest,
    WorkspaceStatusUpdateRequest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _workspace(slug: str = "test", name: str = "Test") -> Workspace:
    return Workspace(slug=slug, name=name)


def _membership(workspace_id: UUID, user_id: str = "user-1") -> WorkspaceMembership:
    return WorkspaceMembership(
        workspace_id=workspace_id, user_id=user_id, role=Role.VIEWER
    )


def _service(
    *,
    workspace: Workspace | None = None,
    membership: WorkspaceMembership | None = None,
    create_raises: Exception | None = None,
    get_workspace_raises: Exception | None = None,
    get_workspace_by_slug_raises: Exception | None = None,
    status_raises: Exception | None = None,
    hard_delete_raises: Exception | None = None,
    invite_raises: Exception | None = None,
    update_role_raises: Exception | None = None,
    remove_raises: Exception | None = None,
    audit_events: list | None = None,
) -> SimpleNamespace:
    ws = workspace or _workspace()
    ms = membership or _membership(ws.id)

    def _create_workspace(**kwargs):
        if create_raises:
            raise create_raises
        return ws, ms

    def _get_workspace(workspace_id):
        if get_workspace_raises:
            raise get_workspace_raises
        return ws

    def _get_workspace_by_slug(slug):
        if get_workspace_by_slug_raises:
            raise get_workspace_by_slug_raises
        return ws

    def _deactivate(workspace_id):
        if status_raises:
            raise status_raises
        return ws

    def _reactivate(workspace_id):
        if status_raises:
            raise status_raises
        return ws

    def _soft_delete(workspace_id):
        if status_raises:
            raise status_raises
        return ws

    def _update_status(workspace_id, status):
        if status_raises:
            raise status_raises
        return ws

    def _hard_delete(workspace_id):
        if hard_delete_raises:
            raise hard_delete_raises

    def _list_audit_events(workspace_id, *, limit=100):
        return audit_events or []

    def _purge_deleted(**kwargs):
        pass

    def _memberships_for(user_id):
        return [(ws, ms)]

    def _list_workspaces(**kwargs):
        return [ws]

    def _invite_member(**kwargs):
        if invite_raises:
            raise invite_raises
        return ms

    def _update_member_role(**kwargs):
        if update_role_raises:
            raise update_role_raises
        return ms

    def _remove_member(**kwargs):
        if remove_raises:
            raise remove_raises

    repository = SimpleNamespace(
        get_workspace=_get_workspace,
        get_workspace_by_slug=_get_workspace_by_slug,
        list_audit_events=_list_audit_events,
        update_status=_update_status,
    )

    return SimpleNamespace(
        create_workspace=_create_workspace,
        list_workspaces=_list_workspaces,
        repository=repository,
        deactivate_workspace=_deactivate,
        reactivate_workspace=_reactivate,
        soft_delete_workspace=_soft_delete,
        hard_delete_workspace=_hard_delete,
        purge_deleted_workspaces=_purge_deleted,
        memberships_for=_memberships_for,
        invite_member=_invite_member,
        update_member_role=_update_member_role,
        remove_member=_remove_member,
    )


def _context(
    workspace_id: UUID | None = None, role: Role = Role.ADMIN
) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_id=workspace_id or uuid4(),
        user_id="admin-user",
        role=role,
    )


# ---------------------------------------------------------------------------
# create_workspace (admin) - WorkspaceMembershipLimitError and
# WorkspaceMembershipError handlers (lines 126-133)
# ---------------------------------------------------------------------------


def test_create_workspace_membership_limit_raises_http_error() -> None:
    service = _service(create_raises=WorkspaceMembershipLimitError("Too many members"))
    context = _context()
    payload = WorkspaceCreateRequest(slug="test", name="Test")

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        create_workspace(payload, service, context)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "workspace.membership_limit_reached"


def test_create_workspace_membership_conflict_raises_http_error() -> None:
    service = _service(create_raises=WorkspaceMembershipError("Already a member"))
    context = _context()
    payload = WorkspaceCreateRequest(slug="test", name="Test")

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        create_workspace(payload, service, context)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "workspace.membership_conflict"


# ---------------------------------------------------------------------------
# create_own_workspace - unauthenticated, owner mismatch, exception handlers
# (lines 153, 160, 172-185)
# ---------------------------------------------------------------------------


def test_create_own_workspace_unauthenticated_raises_401() -> None:
    service = _service()
    anon_auth = RequestContext.anonymous()
    payload = WorkspaceCreateRequest(slug="test", name="Test")

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        create_own_workspace(payload, service, anon_auth)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 401
    assert exc_info.value.error_code == "auth.authentication_required"


def test_create_own_workspace_owner_mismatch_raises_forbidden() -> None:
    service = _service()
    auth = RequestContext(subject="user-a", identity_type="user")
    payload = WorkspaceCreateRequest(slug="test", name="Test", owner_user_id="user-b")

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        create_own_workspace(payload, service, auth)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 403
    assert exc_info.value.error_code == "workspace.owner_mismatch"


def test_create_own_workspace_slug_conflict_raises_http_error() -> None:
    service = _service(create_raises=WorkspaceSlugConflictError("test"))
    auth = RequestContext(subject="user-a", identity_type="user")
    payload = WorkspaceCreateRequest(slug="test", name="Test")

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        create_own_workspace(payload, service, auth)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "workspace.slug_conflict"


def test_create_own_workspace_membership_limit_raises_http_error() -> None:
    service = _service(create_raises=WorkspaceMembershipLimitError("limit"))
    auth = RequestContext(subject="user-a", identity_type="user")
    payload = WorkspaceCreateRequest(slug="test", name="Test")

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        create_own_workspace(payload, service, auth)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "workspace.membership_limit_reached"


def test_create_own_workspace_membership_conflict_raises_http_error() -> None:
    service = _service(create_raises=WorkspaceMembershipError("conflict"))
    auth = RequestContext(subject="user-a", identity_type="user")
    payload = WorkspaceCreateRequest(slug="test", name="Test")

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        create_own_workspace(payload, service, auth)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "workspace.membership_conflict"


# ---------------------------------------------------------------------------
# get_workspace (admin) - entire body (lines 211-215)
# ---------------------------------------------------------------------------


def test_get_workspace_returns_response() -> None:
    ws = _workspace()
    service = _service(workspace=ws)

    result = get_workspace(ws.id, service)  # type: ignore[arg-type]

    assert result.id == ws.id
    assert result.slug == ws.slug


def test_get_workspace_not_found_raises_404() -> None:
    service = _service(get_workspace_raises=WorkspaceNotFoundError("missing"))

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        get_workspace(uuid4(), service)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404
    assert exc_info.value.error_code == "workspace.not_found"


# ---------------------------------------------------------------------------
# update_workspace_status - ACTIVE transition, else branch, error handler
# (lines 229, 233-235)
# ---------------------------------------------------------------------------


def test_update_workspace_status_active_reactivates() -> None:
    ws = _workspace()
    service = _service(workspace=ws)
    payload = WorkspaceStatusUpdateRequest(status=WorkspaceStatus.ACTIVE)

    result = update_workspace_status(ws.id, payload, service)  # type: ignore[arg-type]

    assert result.id == ws.id


def test_update_workspace_status_deleted_soft_deletes() -> None:
    ws = _workspace()
    service = _service(workspace=ws)
    payload = WorkspaceStatusUpdateRequest(status=WorkspaceStatus.DELETED)

    result = update_workspace_status(ws.id, payload, service)  # type: ignore[arg-type]

    assert result.id == ws.id


def test_update_workspace_status_not_found_raises_404() -> None:
    service = _service(status_raises=WorkspaceNotFoundError("missing"))
    payload = WorkspaceStatusUpdateRequest(status=WorkspaceStatus.SUSPENDED)

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        update_workspace_status(uuid4(), payload, service)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# delete_workspace - entire body (lines 245-248)
# ---------------------------------------------------------------------------


def test_delete_workspace_succeeds() -> None:
    service = _service()
    delete_workspace(uuid4(), service)  # type: ignore[arg-type]


def test_delete_workspace_not_found_raises_404() -> None:
    service = _service(hard_delete_raises=WorkspaceNotFoundError("missing"))

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        delete_workspace(uuid4(), service)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# list_workspace_audit_events - error path (lines 263-264)
# ---------------------------------------------------------------------------


def test_list_workspace_audit_events_not_found_raises_404() -> None:
    ws_id = uuid4()
    service = _service(get_workspace_raises=WorkspaceNotFoundError("missing"))

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        list_workspace_audit_events(ws_id, service)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404


def test_list_workspace_audit_events_returns_events() -> None:
    ws = _workspace()
    event = WorkspaceAuditEvent(workspace_id=ws.id, action="created", actor="admin")
    service = _service(workspace=ws, audit_events=[event])

    result = list_workspace_audit_events(ws.id, service)  # type: ignore[arg-type]

    assert len(result.audit_events) == 1
    assert result.audit_events[0].action == "created"


# ---------------------------------------------------------------------------
# purge_deleted_workspaces - entire body (line 281)
# ---------------------------------------------------------------------------


def test_purge_deleted_workspaces_calls_service() -> None:
    calls: list[int] = []

    def _purge(*, retention_days: int) -> None:
        calls.append(retention_days)

    service = _service()
    service.purge_deleted_workspaces = _purge

    purge_deleted_workspaces(service)  # type: ignore[arg-type]

    assert calls == [30]


def test_purge_deleted_workspaces_with_custom_retention() -> None:
    calls: list[int] = []

    def _purge(*, retention_days: int) -> None:
        calls.append(retention_days)

    service = _service()
    service.purge_deleted_workspaces = _purge

    purge_deleted_workspaces(service, retention_days=7)  # type: ignore[arg-type]

    assert calls == [7]


# ---------------------------------------------------------------------------
# list_my_memberships - unauthenticated (line ~290-295)
# ---------------------------------------------------------------------------


def test_list_my_memberships_unauthenticated_raises_401() -> None:
    service = _service()
    anon_auth = RequestContext.anonymous()

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        list_my_memberships(service, anon_auth)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 401
    assert exc_info.value.error_code == "auth.authentication_required"


# ---------------------------------------------------------------------------
# add_workspace_member - workspace not found, scope mismatch, exception handlers
# (lines 341-342, 345, 357-370)
# ---------------------------------------------------------------------------


def test_add_workspace_member_workspace_not_found_raises_404() -> None:
    service = _service(get_workspace_by_slug_raises=WorkspaceNotFoundError("missing"))
    context = _context()
    payload = MembershipCreateRequest(user_id="user-2", role=Role.VIEWER)

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        add_workspace_member("missing-slug", payload, service, context)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404


def test_add_workspace_member_scope_mismatch_raises_403() -> None:
    ws = _workspace()
    service = _service(workspace=ws)
    context = _context(workspace_id=uuid4())  # different workspace than ws.id
    payload = MembershipCreateRequest(user_id="user-2", role=Role.VIEWER)

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        add_workspace_member(ws.slug, payload, service, context)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 403
    assert exc_info.value.error_code == "workspace.scope_mismatch"


def test_add_workspace_member_membership_limit_raises_409() -> None:
    ws = _workspace()
    service = _service(
        workspace=ws,
        invite_raises=WorkspaceMembershipLimitError("limit"),
    )
    context = _context(workspace_id=ws.id)
    payload = MembershipCreateRequest(user_id="user-2", role=Role.VIEWER)

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        add_workspace_member(ws.slug, payload, service, context)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "workspace.membership_limit_reached"


def test_add_workspace_member_membership_conflict_raises_409() -> None:
    ws = _workspace()
    service = _service(
        workspace=ws,
        invite_raises=WorkspaceMembershipError("conflict"),
    )
    context = _context(workspace_id=ws.id)
    payload = MembershipCreateRequest(user_id="user-2", role=Role.VIEWER)

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        add_workspace_member(ws.slug, payload, service, context)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "workspace.membership_conflict"


def test_add_workspace_member_permission_error_raises_403() -> None:
    ws = _workspace()
    service = _service(
        workspace=ws,
        invite_raises=WorkspacePermissionError("forbidden"),
    )
    context = _context(workspace_id=ws.id)
    payload = MembershipCreateRequest(user_id="user-2", role=Role.OWNER)

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        add_workspace_member(ws.slug, payload, service, context)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# update_workspace_member_role - entire body (lines 387-407)
# ---------------------------------------------------------------------------


def test_update_workspace_member_role_succeeds() -> None:
    ws = _workspace()
    ms = _membership(ws.id, "user-2")
    service = _service(workspace=ws, membership=ms)
    context = _context(workspace_id=ws.id)
    payload = MembershipRoleUpdateRequest(role=Role.ADMIN)

    result = update_workspace_member_role(ws.slug, "user-2", payload, service, context)  # type: ignore[arg-type]

    assert result.workspace_id == ws.id


def test_update_workspace_member_role_workspace_not_found_raises_404() -> None:
    service = _service(get_workspace_by_slug_raises=WorkspaceNotFoundError("missing"))
    context = _context()
    payload = MembershipRoleUpdateRequest(role=Role.VIEWER)

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        update_workspace_member_role("missing", "user-2", payload, service, context)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404


def test_update_workspace_member_role_scope_mismatch_raises_403() -> None:
    ws = _workspace()
    service = _service(workspace=ws)
    context = _context(workspace_id=uuid4())  # different workspace
    payload = MembershipRoleUpdateRequest(role=Role.VIEWER)

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        update_workspace_member_role(ws.slug, "user-2", payload, service, context)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 403
    assert exc_info.value.error_code == "workspace.scope_mismatch"


def test_update_workspace_member_role_membership_not_found_raises_404() -> None:
    ws = _workspace()
    service = _service(
        workspace=ws,
        update_role_raises=WorkspaceMembershipError("not found"),
    )
    context = _context(workspace_id=ws.id)
    payload = MembershipRoleUpdateRequest(role=Role.VIEWER)

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        update_workspace_member_role(ws.slug, "user-2", payload, service, context)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404


def test_update_workspace_member_role_permission_error_raises_403() -> None:
    ws = _workspace()
    service = _service(
        workspace=ws,
        update_role_raises=WorkspacePermissionError("forbidden"),
    )
    context = _context(workspace_id=ws.id)
    payload = MembershipRoleUpdateRequest(role=Role.OWNER)

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        update_workspace_member_role(ws.slug, "user-2", payload, service, context)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# remove_workspace_member - entire body (lines 422-438)
# ---------------------------------------------------------------------------


def test_remove_workspace_member_succeeds() -> None:
    ws = _workspace()
    service = _service(workspace=ws)
    context = _context(workspace_id=ws.id)

    remove_workspace_member(ws.slug, "user-2", service, context)  # type: ignore[arg-type]


def test_remove_workspace_member_workspace_not_found_raises_404() -> None:
    service = _service(get_workspace_by_slug_raises=WorkspaceNotFoundError("missing"))
    context = _context()

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        remove_workspace_member("missing", "user-2", service, context)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404


def test_remove_workspace_member_scope_mismatch_raises_403() -> None:
    ws = _workspace()
    service = _service(workspace=ws)
    context = _context(workspace_id=uuid4())  # different workspace

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        remove_workspace_member(ws.slug, "user-2", service, context)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 403
    assert exc_info.value.error_code == "workspace.scope_mismatch"


def test_remove_workspace_member_membership_not_found_raises_404() -> None:
    ws = _workspace()
    service = _service(
        workspace=ws,
        remove_raises=WorkspaceMembershipError("not found"),
    )
    context = _context(workspace_id=ws.id)

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        remove_workspace_member(ws.slug, "user-2", service, context)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404


def test_remove_workspace_member_permission_error_raises_403() -> None:
    ws = _workspace()
    service = _service(
        workspace=ws,
        remove_raises=WorkspacePermissionError("forbidden"),
    )
    context = _context(workspace_id=ws.id)

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        remove_workspace_member(ws.slug, "user-2", service, context)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# create_workspace (admin) - slug conflict and success paths (lines 121, 138)
# ---------------------------------------------------------------------------


def test_create_workspace_slug_conflict_raises_http_error() -> None:
    service = _service(create_raises=WorkspaceSlugConflictError("test"))
    context = _context()
    payload = WorkspaceCreateRequest(slug="test", name="Test")

    with pytest.raises(WorkspaceHTTPError) as exc_info:
        create_workspace(payload, service, context)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "workspace.slug_conflict"


def test_create_workspace_success_returns_response() -> None:
    ws = _workspace()
    service = _service(workspace=ws)
    context = _context()
    payload = WorkspaceCreateRequest(slug="test", name="Test")

    result = create_workspace(payload, service, context)  # type: ignore[arg-type]

    assert result.id == ws.id
    assert result.slug == ws.slug


# ---------------------------------------------------------------------------
# create_own_workspace success path (line 190)
# ---------------------------------------------------------------------------


def test_create_own_workspace_success_returns_response() -> None:
    ws = _workspace()
    service = _service(workspace=ws)
    auth = RequestContext(subject="user-a", identity_type="user")
    payload = WorkspaceCreateRequest(slug="test", name="Test")

    result = create_own_workspace(payload, service, auth)  # type: ignore[arg-type]

    assert result.id == ws.id
    assert result.slug == ws.slug


# ---------------------------------------------------------------------------
# list_workspaces (lines 199-200)
# ---------------------------------------------------------------------------


def test_list_workspaces_returns_all() -> None:
    ws = _workspace()
    service = _service(workspace=ws)

    from orcheo_backend.app.routers.workspaces import list_workspaces

    result = list_workspaces(service)  # type: ignore[arg-type]

    assert len(result.workspaces) == 1
    assert result.workspaces[0].id == ws.id


# ---------------------------------------------------------------------------
# update_workspace_status else branch (line 233)
# ---------------------------------------------------------------------------


def test_update_workspace_status_else_branch_uses_update_status() -> None:
    from types import SimpleNamespace

    ws = _workspace()
    service = _service(workspace=ws)
    # Use a SimpleNamespace payload with an unrecognized status to hit the else branch
    fake_status = object()
    payload = SimpleNamespace(status=fake_status)

    result = update_workspace_status(ws.id, payload, service)  # type: ignore[arg-type]

    assert result.id == ws.id


# ---------------------------------------------------------------------------
# get_active_workspace (lines 317-318)
# ---------------------------------------------------------------------------


def test_get_active_workspace_returns_active_workspace() -> None:
    from orcheo_backend.app.routers.workspaces import get_active_workspace

    ws = _workspace()
    service = _service(workspace=ws)
    context = _context(workspace_id=ws.id)

    result = get_active_workspace(service, context)  # type: ignore[arg-type]

    assert result.workspace_id == ws.id
    assert result.slug == ws.slug
    assert result.role == context.role


# ---------------------------------------------------------------------------
# add_workspace_member success path (line 371)
# ---------------------------------------------------------------------------


def test_add_workspace_member_success_returns_membership() -> None:
    ws = _workspace()
    ms = _membership(ws.id, "user-2")
    service = _service(workspace=ws, membership=ms)
    context = _context(workspace_id=ws.id)
    payload = MembershipCreateRequest(user_id="user-2", role=Role.VIEWER)

    result = add_workspace_member(ws.slug, payload, service, context)  # type: ignore[arg-type]

    assert result.workspace_id == ws.id
    assert result.user_id == "user-2"
