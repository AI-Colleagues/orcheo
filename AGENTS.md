# Repository Guidelines

This file is the single source of truth for all AI coding agents working in this repository.
Both `CLAUDE.md` and `GEMINI.md` reference this file — do not duplicate instructions elsewhere.

## Project Overview

Orcheo is a workflow orchestration platform built on LangGraph with a node-based architecture.
It supports both low-code (JSON config) and vibe-coding-first (AI agents build workflows via SDK) approaches.

The project is a monorepo containing:
- **Core Engine & Backend** (`src/orcheo/`, `apps/backend/`): Python — FastAPI, LangGraph, Celery + Redis.
- **SDK** (`packages/sdk/`): Python SDK and CLI (`orcheo` / `horcheo`).
- **Canvas** (`apps/canvas/`): Web interface for monitoring and managing workflows — React 19, Vite, Radix UI, Tailwind CSS, @xyflow/react. Workflow authoring is done via the SDK or AI coding agents.

## Project Structure & Module Organization

- Source: `src/orcheo/` — core package. Key areas: `graph/` (state, builder), `nodes/` (task/AI/integrations), `main.py` (FastAPI app/WebSocket).
- Tests: `tests/` — mirrors package layout (e.g., `tests/graph/`, `tests/nodes/`).
- Docs & examples: `docs/`, `examples/`, experimental `playground/`.
- Contributors: `CONTRIBUTORS.md` — list of project contributors.
- Config: `pyproject.toml` (tooling), `.pre-commit-config.yaml`, `.env` (local secrets), `Makefile` (common tasks).
- Deploy: `deploy/systemd/` — systemd unit files for production deployment.

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

### TypeScript / JavaScript (Canvas)
- Canvas lint check: `make canvas-lint`
- Canvas auto-format: `make canvas-format`
- Canvas tests: `make canvas-test`

### Execution Worker (Celery + Redis)
- `make redis` — Start Redis via Docker Compose
- `make worker` — Start Celery worker for background execution
- `make celery-beat` — Start Celery Beat scheduler for cron triggers

### Docker Compose (Full Stack)
- `make docker-up` — Start all services (backend, canvas, redis, worker, celery-beat)
- `make docker-down` — Stop all Docker Compose services
- `make docker-build` — Build Docker images
- `make docker-logs` — Follow logs from all services

### Package Management
- Uses `uv` for dependency management (see uv.lock); Python 3.12+ required.
- Uses `npm` for Canvas frontend.

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

### TypeScript / React (Canvas)
- Functional components with Hooks.
- Styling: Tailwind CSS, avoiding raw CSS where possible.
- State: Local state + React Context for global needs.

## Testing Guidelines
- Framework: `pytest` with `pytest-asyncio` and `pytest-cov`.
- Location: place tests under `tests/` mirroring package paths.
- Names: test files `test_*.py`, tests `test_*` functions; include async tests where relevant.
- Coverage: CI enforces 95% project coverage and 100% diff coverage. Add tests for new code and branches.
- Run subsets: `uv run pytest tests/nodes -q`.

## Commit & Pull Request Guidelines
- Commits: concise, imperative subject; include scope/ticket where helpful (e.g., `AF-12 Add RSSNode`). Keep changes focused.
- PRs: clear description, rationale, and testing notes; link issues; include screenshots for UI (if any); update docs/examples when behavior changes.
- CI must pass: lint, type check, tests, and coverage thresholds.

## Security & Configuration Tips
- Load secrets from `.env` (via `python-dotenv`); never commit secrets.
- `[[credential_name]]` denotes a vault-backed or runtime-injected placeholder, not a hardcoded secret.
- Do not flag `[[...]]` placeholders as credential leaks or suggest env-var rewrites unless the file contains an actual secret value or explicitly requires env-var configuration.
- Prefer `uv run` for tooling parity with CI; ensure `uv.lock` stays updated when adding deps.
- When writing documents, set the author to the person or AI agent writing the document.
- Default document owner is ShaojieJiang unless explicitly stated otherwise.
- For multi-tenant deployments, keep `ORCHEO_MULTI_TENANCY_ENABLED`, `ORCHEO_DEFAULT_TENANT`, and the tenant header setting aligned across backend, worker, beat, and stack templates.
- WebSocket support for real-time workflow monitoring.
