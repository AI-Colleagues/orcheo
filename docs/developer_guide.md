# Developer Guide

This guide is for developers contributing to the Orcheo project.

## Repository Layout

- `src/orcheo/` – core orchestration engine (graph builder, nodes, triggers, listeners, vault, workspace, identity, sandbox)
- `apps/backend/` – the FastAPI application (`orcheo_backend.app`), WebSocket endpoints, identity service, and Celery worker
- `packages/sdk/` – Python SDK and the `orcheo` / `horcheo` CLI
- `packages/agentensor/` – agent prompt tensors, modules, and optimizers
- `apps/studio/` – React + Vite web interface for monitoring and managing workflows

## Evaluation Node Imports

Evaluation nodes now live under `orcheo.nodes.evaluation`.

- Preferred import path: `from orcheo.nodes.evaluation import ...`

Use the new import path for all new code and examples.

## Development Environment Setup

### Prerequisites

- **Python 3.12+**
- **uv** for dependency management
- **Node.js 18+** for Studio development
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

# Run a specific test directory or file
uv run pytest tests/nodes/ai

# Run with verbose output
uv run pytest -v tests/
```

## Code Quality

```bash
# Format code
make format

# Run linting (ruff + mypy)
make lint

# Studio (TypeScript/JavaScript)
make studio-format
make studio-lint
make studio-test
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

The `examples/` directory contains ChatKit widget examples
(`examples/chatkit_widgets/`, including `sdk_quickstart.py`). A broader set of
example workflows — quickstart journeys, ingestion scripts, messaging bots, and
more — lives in the
[`colleague-candidates`](https://github.com/AI-Colleagues/colleague-candidates)
repository's `examples/` directory (vendored here as the `colleague-candidates/`
git submodule).

## Further Reading

- [Plugin Author Guide](custom_nodes_and_tools.md) – extend Orcheo with managed plugins
- [Deployment Guide](deployment.md) – Docker Compose and PostgreSQL deployment recipes
- [Environment Variables](environment_variables.md) – complete configuration reference
