# Orcheo Backend

This package exposes the FastAPI application that powers the Orcheo runtime. It wraps the core `orcheo` package so that deployment targets can import a lightweight entrypoint (`orcheo_backend.app`).

## Local development

```bash
uv sync --all-groups
uv run uvicorn orcheo_backend.app:app --reload --host 0.0.0.0 --port 2025
```

## Testing & linting

The shared repository `Makefile` includes convenience targets:

```bash
uv run make lint
uv run make test
```

These commands ensure Ruff, MyPy, and pytest with coverage run in CI as well.

## ChatKit integration

The backend now exposes helper endpoints for the Studio ChatKit experience:

- `POST /api/chatkit/session` — returns a ChatKit client secret.
- `POST /api/chatkit/workflows/{workflow_id}/trigger` — dispatches a workflow run.

Set `CHATKIT_TOKEN_SIGNING_KEY` (HS or RSA private key material) to enable session
issuance. Without a signing key configured the ChatKit endpoints will respond with
`503 Service Unavailable`.
