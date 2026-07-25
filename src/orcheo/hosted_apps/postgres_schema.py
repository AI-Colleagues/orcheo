"""Idempotent Postgres schema for Hosted Apps' tenant-scoped metadata."""

from __future__ import annotations


__all__ = ["POSTGRES_HOSTED_APPS_SCHEMA"]


POSTGRES_HOSTED_APPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS hosted_app_runtime_state (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    generation BIGINT NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    updated_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO hosted_app_runtime_state (singleton)
VALUES (TRUE)
ON CONFLICT (singleton) DO NOTHING;

CREATE TABLE IF NOT EXISTS hosted_apps (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    visibility TEXT NOT NULL CHECK (visibility IN ('public', 'private')),
    publication_state TEXT NOT NULL CHECK (
        publication_state IN ('draft', 'published', 'unpublished')
    ),
    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    active_release_id UUID,
    permission_revision BIGINT NOT NULL DEFAULT 1,
    published_permission_revision BIGINT,
    external_origins JSONB NOT NULL DEFAULT '[]'::jsonb,
    suspended_at TIMESTAMPTZ,
    suspended_reason TEXT,
    suspended_by TEXT,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ,
    archived_at TIMESTAMPTZ,
    UNIQUE (workspace_id, id)
);
CREATE INDEX IF NOT EXISTS idx_hosted_apps_workspace_list
    ON hosted_apps(workspace_id, is_archived, publication_state, updated_at DESC);

CREATE TABLE IF NOT EXISTS hosted_app_aliases (
    alias TEXT PRIMARY KEY,
    app_id UUID,
    workspace_id UUID,
    reserved_kind TEXT NOT NULL CHECK (
        reserved_kind IN ('app', 'platform', 'tombstone')
    ),
    tombstoned_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (workspace_id, app_id)
        REFERENCES hosted_apps(workspace_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_hosted_app_aliases_active_app
    ON hosted_app_aliases(app_id) WHERE app_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS hosted_app_deployments (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL,
    app_id UUID NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'validating', 'ready', 'failed', 'expired')
    ),
    archive_sha256 TEXT,
    manifest_sha256 TEXT,
    validation_error_code TEXT,
    validation_error_message TEXT,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    validated_at TIMESTAMPTZ,
    FOREIGN KEY (workspace_id, app_id)
        REFERENCES hosted_apps(workspace_id, id) ON DELETE CASCADE,
    UNIQUE (workspace_id, app_id, id)
);
CREATE INDEX IF NOT EXISTS idx_hosted_app_deployments_app
    ON hosted_app_deployments(workspace_id, app_id, created_at DESC);

CREATE TABLE IF NOT EXISTS hosted_app_uploads (
    id UUID PRIMARY KEY,
    deployment_id UUID UNIQUE NOT NULL,
    workspace_id UUID NOT NULL,
    app_id UUID NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'completed', 'expired', 'failed')
    ),
    staging_key TEXT NOT NULL,
    provider_object_version TEXT,
    expected_size_bytes BIGINT NOT NULL CHECK (expected_size_bytes > 0),
    expected_sha256 TEXT,
    actual_size_bytes BIGINT,
    actual_sha256 TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (workspace_id, app_id)
        REFERENCES hosted_apps(workspace_id, id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, app_id, deployment_id)
        REFERENCES hosted_app_deployments(workspace_id, app_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS hosted_app_bindings (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL,
    app_id UUID NOT NULL,
    name TEXT NOT NULL,
    workflow_id UUID NOT NULL,
    workflow_version_id UUID NOT NULL,
    workflow_execution_sha256 TEXT NOT NULL,
    runnable_config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    access_mode TEXT NOT NULL CHECK (access_mode IN ('anonymous', 'authenticated')),
    input_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_projection JSONB NOT NULL DEFAULT '{}'::jsonb,
    visitor_can_read_output BOOLEAN NOT NULL DEFAULT FALSE,
    visitor_can_read_sanitized_errors BOOLEAN NOT NULL DEFAULT FALSE,
    limits JSONB NOT NULL DEFAULT '{}'::jsonb,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (workspace_id, app_id)
        REFERENCES hosted_apps(workspace_id, id) ON DELETE CASCADE,
    UNIQUE (workspace_id, app_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_hosted_app_bindings_live_name
    ON hosted_app_bindings(app_id, name) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS hosted_app_collections (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL,
    app_id UUID NOT NULL,
    name TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('shared', 'user')),
    read_access TEXT NOT NULL CHECK (read_access IN ('anonymous', 'authenticated')),
    write_access TEXT NOT NULL CHECK (write_access IN ('anonymous', 'authenticated')),
    max_document_bytes INTEGER NOT NULL CHECK (max_document_bytes > 0),
    max_records INTEGER NOT NULL CHECK (max_records > 0),
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (workspace_id, app_id)
        REFERENCES hosted_apps(workspace_id, id) ON DELETE CASCADE,
    UNIQUE (workspace_id, app_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_hosted_app_collections_live_name
    ON hosted_app_collections(app_id, name) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS hosted_app_records (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL,
    app_id UUID NOT NULL,
    collection_id UUID NOT NULL,
    owner_subject TEXT NOT NULL DEFAULT '',
    key TEXT NOT NULL,
    value JSONB NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    version BIGINT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (workspace_id, app_id)
        REFERENCES hosted_apps(workspace_id, id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, app_id, collection_id)
        REFERENCES hosted_app_collections(workspace_id, app_id, id) ON DELETE RESTRICT,
    UNIQUE (app_id, collection_id, owner_subject, key)
);

CREATE TABLE IF NOT EXISTS hosted_app_releases (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL,
    app_id UUID NOT NULL,
    deployment_id UUID NOT NULL,
    permission_revision BIGINT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility IN ('public', 'private')),
    capability_snapshot JSONB NOT NULL,
    csp_snapshot JSONB NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (workspace_id, app_id)
        REFERENCES hosted_apps(workspace_id, id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, app_id, deployment_id)
        REFERENCES hosted_app_deployments(workspace_id, app_id, id) ON DELETE RESTRICT,
    UNIQUE (workspace_id, app_id, id)
);
CREATE INDEX IF NOT EXISTS idx_hosted_app_releases_snapshot
    ON hosted_app_releases(app_id, snapshot_sha256);

CREATE TABLE IF NOT EXISTS hosted_app_authorization_codes (
    id UUID PRIMARY KEY,
    code_hash TEXT UNIQUE NOT NULL,
    workspace_id UUID NOT NULL,
    app_id UUID NOT NULL,
    user_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (workspace_id, app_id)
        REFERENCES hosted_apps(workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS hosted_app_login_transactions (
    id UUID PRIMARY KEY,
    secret_hash TEXT UNIQUE NOT NULL,
    workspace_id UUID NOT NULL,
    app_id UUID NOT NULL,
    app_host TEXT NOT NULL,
    state_hash TEXT NOT NULL,
    pkce_verifier TEXT NOT NULL,
    return_to TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (workspace_id, app_id)
        REFERENCES hosted_apps(workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS hosted_app_sessions (
    id UUID PRIMARY KEY,
    secret_hash TEXT UNIQUE NOT NULL,
    workspace_id UUID NOT NULL,
    app_id UUID NOT NULL,
    app_host TEXT NOT NULL,
    user_id TEXT NOT NULL,
    runtime_generation BIGINT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    idle_expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (workspace_id, app_id)
        REFERENCES hosted_apps(workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS hosted_app_runtime_runs (
    id UUID PRIMARY KEY,
    public_handle TEXT UNIQUE NOT NULL,
    workspace_id UUID NOT NULL,
    app_id UUID NOT NULL,
    release_id UUID NOT NULL REFERENCES hosted_app_releases(id) ON DELETE RESTRICT,
    deployment_id UUID NOT NULL,
    binding_id UUID NOT NULL,
    binding_snapshot_sha256 TEXT NOT NULL,
    workflow_run_id UUID NOT NULL,
    visitor_user_id TEXT,
    originating_session_id UUID REFERENCES hosted_app_sessions(id) ON DELETE SET NULL,
    idempotency_key_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (workspace_id, app_id)
        REFERENCES hosted_apps(workspace_id, id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, app_id, release_id)
        REFERENCES hosted_app_releases(workspace_id, app_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, app_id, deployment_id)
        REFERENCES hosted_app_deployments(workspace_id, app_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, app_id, binding_id)
        REFERENCES hosted_app_bindings(workspace_id, app_id, id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS hosted_app_idempotency (
    id UUID PRIMARY KEY,
    scope_hash TEXT UNIQUE NOT NULL,
    request_hash TEXT NOT NULL,
    public_handle TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS hosted_app_quota_leases (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    operation TEXT NOT NULL,
    amount BIGINT NOT NULL CHECK (amount > 0),
    expires_at TIMESTAMPTZ NOT NULL,
    settled_at TIMESTAMPTZ,
    released_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS hosted_app_outbox (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    aggregate_id UUID NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL,
    delivered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS hosted_app_moderation_blocks (
    id UUID PRIMARY KEY,
    target_kind TEXT NOT NULL CHECK (
        target_kind IN ('app', 'alias', 'workspace', 'publisher')
    ),
    target_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    reason_detail TEXT,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    lifted_by TEXT,
    lifted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_hosted_app_moderation_active
    ON hosted_app_moderation_blocks(target_kind, target_id) WHERE lifted_at IS NULL;

CREATE TABLE IF NOT EXISTS hosted_app_platform_audit_events (
    id UUID PRIMARY KEY,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    reason_code TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key TEXT,
    created_at TIMESTAMPTZ NOT NULL
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_hosted_apps_active_release'
    ) THEN
        ALTER TABLE hosted_apps
        ADD CONSTRAINT fk_hosted_apps_active_release
        FOREIGN KEY (workspace_id, id, active_release_id)
        REFERENCES hosted_app_releases(workspace_id, app_id, id)
        DEFERRABLE INITIALLY DEFERRED;
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION validate_hosted_app_ready_release()
RETURNS TRIGGER AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM hosted_app_deployments
        WHERE workspace_id = NEW.workspace_id
          AND app_id = NEW.app_id
          AND id = NEW.deployment_id
          AND status = 'ready'
    ) THEN
        RAISE EXCEPTION 'Hosted app release requires a ready owned deployment';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS hosted_app_release_ready ON hosted_app_releases;
CREATE TRIGGER hosted_app_release_ready
BEFORE INSERT ON hosted_app_releases
FOR EACH ROW EXECUTE FUNCTION validate_hosted_app_ready_release();
"""
