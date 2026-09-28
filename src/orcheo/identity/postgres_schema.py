"""Postgres schema definitions for the first-party identity tables."""

from __future__ import annotations


__all__ = ["POSTGRES_IDENTITY_SCHEMA"]


POSTGRES_IDENTITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    name TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_login_at TIMESTAMP WITH TIME ZONE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS auth_email_challenges (
    id UUID PRIMARY KEY,
    email TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT 'login_or_signup',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    consumed_at TIMESTAMP WITH TIME ZONE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_email_challenges_token_hash
    ON auth_email_challenges(token_hash);
CREATE INDEX IF NOT EXISTS idx_auth_email_challenges_email
    ON auth_email_challenges(email);
CREATE INDEX IF NOT EXISTS idx_auth_email_challenges_expires_at
    ON auth_email_challenges(expires_at);

-- Sign-in is code-only, but the magic-link token column is kept for old rows.
ALTER TABLE auth_email_challenges ALTER COLUMN token_hash DROP NOT NULL;

CREATE TABLE IF NOT EXISTS auth_sessions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_token_hash TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE,
    user_agent TEXT,
    ip TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_sessions_refresh_token_hash
    ON auth_sessions(refresh_token_hash);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);

ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS oauth_client_id TEXT;
ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS scopes JSONB;

CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    metadata JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_authorization_requests (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
    redirect_uri TEXT NOT NULL,
    redirect_uri_provided_explicitly BOOLEAN NOT NULL,
    code_challenge TEXT NOT NULL,
    state TEXT,
    scopes JSONB NOT NULL,
    resource TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    decided_at TIMESTAMP WITH TIME ZONE,
    code_hash TEXT,
    code_expires_at TIMESTAMP WITH TIME ZONE,
    consumed_at TIMESTAMP WITH TIME ZONE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_oauth_authorization_requests_code_hash
    ON oauth_authorization_requests(code_hash);
CREATE INDEX IF NOT EXISTS idx_oauth_authorization_requests_expires_at
    ON oauth_authorization_requests(expires_at);
"""
