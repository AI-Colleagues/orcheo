"""Postgres schema definitions for the workspace core tables."""

from __future__ import annotations


__all__ = ["POSTGRES_WORKSPACE_SCHEMA"]


POSTGRES_WORKSPACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id UUID PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    quotas JSONB NOT NULL DEFAULT '{}'::jsonb,
    deleted_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workspaces_status ON workspaces(status);

CREATE TABLE IF NOT EXISTS workspace_audit_events (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    actor TEXT,
    subject TEXT,
    resource_type TEXT,
    resource_id TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workspace_audit_events_workspace
    ON workspace_audit_events(workspace_id);
CREATE INDEX IF NOT EXISTS idx_workspace_audit_events_created_at
    ON workspace_audit_events(created_at);

CREATE TABLE IF NOT EXISTS workspace_memberships (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    UNIQUE (workspace_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_workspace_memberships_user
    ON workspace_memberships(user_id);
"""
