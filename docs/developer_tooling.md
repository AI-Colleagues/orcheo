# Developer Tooling Quickstart

This guide captures the baseline tooling shipped with Milestone 1 so that new
contributors can bootstrap the repository in minutes.

## Dev container

The repository now includes a [VS Code Dev Container](../.devcontainer) profile
that ships with Python 3.12, uv, and the Node.js runtime required for the React
studio. Open the folder in VS Code and select **Reopen in Container**. The
container will automatically run `uv sync --all-groups` to install Python
packages and configure recommended extensions.

## uv scripts

The `pyproject.toml` exposes a handful of scripts that wrap common tasks. After
installing dependencies, run them via `uv run <script>`:

- `uv run orcheo-seed-env` – copy `.env.example` to `.env` and create state
  directories.
- `uv run orcheo-dev-server` – launch the FastAPI development server with auto reload.
- `uv run orcheo-lint` – execute Ruff (lint + format check) and mypy.
- `uv run orcheo-format` – apply Ruff formatting and import sorting.
- `uv run orcheo-dev-studio` – launch the Studio dev server.

Run the test suite with `make test` (or `uv run pytest`).
- `uv run orcheo-studio-lint` – lint the React studio application.

These commands mirror the existing `make` targets but remove the need for GNU
Make on contributor machines.

## Seed environment script

`uv run orcheo-seed-env` invokes `orcheo.tooling.env.seed_env_file`, which copies the
example environment file into place and creates local state directories used by
ChatKit and the credential vault. Pass `--force` to overwrite an existing `.env`:

```bash
uv run orcheo-seed-env -- --force
```

An optional `--root` argument allows scripting across workspaces when the
repository lives outside the current working directory.

## Sample flows

Two quickstart flows live under `colleague-candidates/examples/quickstart`
(in the `colleague-candidates` git submodule) to demonstrate both onboarding
paths:

- `canvas_welcome.json` – graph configuration suited for the Studio interface.
- `sdk_quickstart.py` – Python script that builds the same graph and executes it
  locally using the SDK primitives.

Use these examples to validate new tooling, test dependencies, and demo the core
user journeys end-to-end.
