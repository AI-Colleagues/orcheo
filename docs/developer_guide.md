# Developer Guide

This guide is for developers contributing to the Orcheo project.

## Repository Layout

- `src/orcheo/` – core orchestration engine and FastAPI implementation
- `apps/backend/` – deployment wrapper exposing the FastAPI ASGI app
- `packages/sdk/` – lightweight Python SDK for composing workflow requests
- `apps/canvas/` – React + Vite web interface for monitoring and managing workflows

## Evaluation Node Imports

Evaluation nodes now live under `orcheo.nodes.evaluation`.

- Preferred import path: `from orcheo.nodes.evaluation import ...`

Use the new import path for all new code and examples.

## Development Environment Setup

### Prerequisites

- **Python 3.12+**
- **uv** for dependency management
- **Node.js 18+** for Canvas development
- **Docker** for running services

### Quick Setup

```bash
# Clone the repository
git clone https://github.com/AI-Colleagues/orcheo.git
cd orcheo

# Install Python dependencies
uv sync --all-groups

# Seed environment variables
orcheo-seed-env

# Activate the virtual environment (optional)
source .venv/bin/activate
```

Pass `--force` to `orcheo-seed-env` to overwrite an existing `.env` file.

### VS Code Dev Container

Opening the repository inside VS Code automatically offers to start the included dev container with uv and Node.js preinstalled.

## Running Tests

```bash
# Run all tests with coverage
make test

# Run specific test file
uv run pytest tests/nodes/test_ai_node.py

# Run with verbose output
uv run pytest -v tests/
```

## Code Quality

```bash
# Format code
make format

# Run linting (ruff + mypy)
make lint

# Canvas (TypeScript/JavaScript)
make canvas-format
make canvas-lint
make canvas-test
```

## Development Server

```bash
# Start backend with hot reload
make dev-server

# Start Redis (required for workers)
make redis

# Start Celery worker
make worker

# Start Celery Beat scheduler
make celery-beat
```

## Workflow Repository Configuration

The FastAPI backend uses PostgreSQL for workflow persistence. Set `ORCHEO_REPOSITORY_BACKEND=postgres` and `ORCHEO_POSTGRES_DSN` to connect the API to your database.

Environment variables:

- `ORCHEO_REPOSITORY_BACKEND`: `postgres`
- `ORCHEO_POSTGRES_DSN`: required when the repository backend is `postgres`

Refer to `.env.example` for sample values and to [Deployment Guide](deployment.md) for deployment-specific guidance.

## Examples

The `examples/` directory contains usage examples and notebooks:

- `examples/quickstart/` – Canvas and SDK user journeys
- `examples/ingest_langgraph.py` – push a Python LangGraph script directly to the backend importer, execute it, and stream live updates

## Further Reading

- [Plugin Author Guide](custom_nodes_and_tools.md) – extend Orcheo with managed plugins
- [Deployment Guide](deployment.md) – Docker Compose and PostgreSQL deployment recipes
- [Environment Variables](environment_variables.md) – complete configuration reference
