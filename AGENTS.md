# Repository Guidelines

This file is the single source of truth for all AI coding agents working in this repository.
Both `CLAUDE.md` and `GEMINI.md` reference this file — do not duplicate instructions elsewhere.

## Project Overview

Orcheo is a workflow orchestration platform built on LangGraph with a node-based architecture.
It supports both low-code (JSON config) and vibe-coding-first (AI agents build workflows via SDK) approaches.

The project is a monorepo containing:
- **Core Engine & Backend** (`src/orcheo/`, `apps/backend/`): Python — FastAPI, LangGraph, Celery + Redis.
- **SDK** (`packages/sdk/`): Python SDK and CLI (`orcheo` / `horcheo`).
- **Studio** (`apps/studio/`): Web interface for monitoring and managing workflows — React 19, Vite, Radix UI, Tailwind CSS, @xyflow/react. Workflow authoring is done via the SDK or AI coding agents.

## Project Structure & Module Organization

- Source: `src/orcheo/` — core package. Key areas: `graph/` (state, builder), `nodes/` (task/AI/integrations), `main.py` (FastAPI app/WebSocket).
- Tests: `tests/` — mirrors package layout (e.g., `tests/graph/`, `tests/nodes/`).
- Docs & examples: `docs/`, `examples/`, experimental `playground/`.
- Contributors: `CONTRIBUTORS.md` — list of project contributors.
- Config: `pyproject.toml` (tooling), `.pre-commit-config.yaml`, `.env` (local secrets), `Makefile` (common tasks).

## Architecture

### Core Components
- **Nodes**: Individual workflow units inheriting from BaseNode, AINode, or TaskNode.
- **Graph Builder**: Constructs workflows from JSON configurations using StateGraph.
- **State Management**: Centralized state passing between nodes with variable interpolation (`{{path.to.value}}`).
- **Node Registry**: Dynamic registration system for node types.
- Built-in nodes: AI, Code, MongoDB, RSS, Slack, Telegram.

### Technology Stack
- **Backend**: FastAPI + uvicorn
- **Workflow Engine**: LangGraph + LangChain
- **Task Queue**: Celery + Redis (for background execution)
- **Database**: SQLite checkpoints, PostgreSQL support
- **AI Integration**: OpenAI, various LangChain providers
- **External Services**: Telegram Bot, Slack, MongoDB, RSS feeds
- **Frontend**: React 19 + Vite, Radix UI, Tailwind CSS, @xyflow/react (React Flow)
- **Frontend Testing**: Vitest
- **Frontend Linting/Formatting**: ESLint, Prettier
- **MCP**: Model Context Protocol adapters for tool integration

## Build, Test, and Development Commands

### Python (Backend / Core / SDK)
- Install deps (all groups): `uv sync --all-groups`
- Lint/typecheck/format (check): `make lint`
- Auto-format and organize imports: `make format`
- Run tests with coverage: `make test`
- Serve docs locally: `make doc` (MkDocs at `http://localhost:8080`)

Tip: Prefix with `uv run` when invoking tools directly, e.g. `uv run pytest -k nodes`.

### TypeScript / JavaScript (Studio)
- Studio lint check: `make studio-lint`
- Studio auto-format: `make studio-format`
- Studio tests: `make studio-test`

### Execution Worker (Celery + Redis)
- `make redis` — Start Redis via Docker Compose
- `make worker` — Start Celery worker for background execution
- `make celery-beat` — Start Celery Beat scheduler for cron triggers

### Docker Compose (Full Stack)
- `make docker-up` — Start all services (backend, studio, redis, worker, celery-beat)
- `make docker-down` — Stop all Docker Compose services
- `make docker-build` — Build Docker images
- `make docker-logs` — Follow logs from all services

### Package Management
- Uses `uv` for dependency management (see uv.lock); Python 3.12+ required.
- Uses `npm` for Studio frontend.

### CLI Commands
Available when environment is active (defined in `pyproject.toml` scripts):
- `orcheo-dev-server`: Equivalent to `make dev-server`.
- `orcheo-seed-env`: Sets up development environment variables.

## Coding Style & Naming Conventions

### Python
- Python 3.12, type hints required (mypy: `disallow_untyped_defs = true`).
- Formatting/linting via Ruff; line length 88; Google-style docstrings.
- Import rules: no relative imports (TID252); always use absolute package paths (`from orcheo...`).
- Naming: modules/files `snake_case.py`; classes `PascalCase`; functions/vars `snake_case`.
- Keep functions focused; prefer small units with clear docstrings and types.
- Uses async/await patterns throughout.
- State flows through nodes via `decode_variables()` method.

### TypeScript / React (Studio)
- Functional components with Hooks.
- Styling: Tailwind CSS, avoiding raw CSS where possible.
- State: Local state + React Context for global needs.

## Testing Guidelines
- Framework: `pytest` with `pytest-asyncio` and `pytest-cov`.
- Location: place tests under `tests/` mirroring package paths.
- Names: test files `test_*.py`, tests `test_*` functions; include async tests where relevant.
- Run subsets: `uv run pytest tests/nodes -q`.

## Commit & Pull Request Guidelines
- Commits: concise, imperative subject; include scope/ticket where helpful (e.g., `AF-12 Add RSSNode`). Keep changes focused.
- PRs: clear description, rationale, and testing notes; link issues; include screenshots for UI (if any); update docs/examples when behavior changes.

## Security & Configuration Tips
- Load secrets from `.env` (via `python-dotenv`); never commit secrets.
- `[[credential_name]]` denotes a vault-backed or runtime-injected placeholder, not a hardcoded secret.
- Do not flag `[[...]]` placeholders as credential leaks or suggest env-var rewrites unless the file contains an actual secret value or explicitly requires env-var configuration.
- Prefer `uv run` for tooling parity with CI; ensure `uv.lock` stays updated when adding deps.
- When writing documents, set the author to the person or AI agent writing the document.
- Default document owner is ShaojieJiang unless explicitly stated otherwise.
- Multi-workspace is always on. The workspace header is hard-coded to `X-Orcheo-Workspace` across backend, worker, beat, studio, and stack templates — it is not configurable.
- WebSocket support for real-time workflow monitoring.
- Authentication is a **first-party, passwordless email IdP** (no Auth0). The identity service (`apps/backend/src/orcheo_backend/app/identity/`) issues magic-link + OTP challenges and mints HS256 access tokens signed with `ORCHEO_AUTH_JWT_SECRET`, validated by `authentication/` (sole accepted issuer = `ORCHEO_AUTH_ISSUER`). The Studio login UI lives in `apps/studio/src/features/auth/`. The generic OIDC/JWKS relying-party code is retained but **dormant** for a future SSO initiative. Set `VITE_ORCHEO_AUTH_DISABLED=true` to bypass the Studio login gate for local dev.
- Transactional email (auth challenges + workspace invitations) is delivered over **SMTP** (`ORCHEO_SMTP_*`) via the shared sender in `src/orcheo/workspace/email.py`; with no SMTP host configured the link/code is logged. The Resend integration has been removed.
- The one-time membership migration (Auth0 `sub` → internal user id) is run via `python -m orcheo.identity.cli backfill` (or `orcheo-identity-migrate backfill`); `coverage` reports readiness without mutating.
