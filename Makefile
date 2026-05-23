.PHONY: dev-server test lint format canvas-lint canvas-format canvas-test redis worker celery-beat \
       docker-up docker-down docker-build docker-logs workspace-sandbox-build \
       staging-env staging-egress-config staging-up staging-down staging-restart \
       staging-build staging-logs staging-config

UV ?= uv
UV_CACHE_DIR ?= .cache/uv
UV_RUN = UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run
STACK_DIR ?= deploy/stack
STACK_ENV_DIR ?= $(HOME)/.orcheo/stack
STACK_ENV_FILE ?= $(STACK_ENV_DIR)/.env
STACK_ENV_TEMPLATE ?= $(STACK_DIR)/.env.example
STAGING_COMPOSE = ORCHEO_STACK_ENV_FILE=$(STACK_ENV_FILE) docker compose --env-file $(STACK_ENV_FILE) -f $(STACK_DIR)/docker-compose.yml -f $(STACK_DIR)/docker-compose.staging.yml --project-directory $(STACK_DIR)

lint:
	$(UV_RUN) ruff check --config pyproject.toml src/orcheo packages/sdk/src packages/agentensor/src apps/backend/src
	$(UV_RUN) mypy src/orcheo packages/sdk/src packages/agentensor/src apps/backend/src --install-types --non-interactive
	$(UV_RUN) ruff format --config pyproject.toml . --check

canvas-lint:
	npm --prefix apps/canvas run lint

canvas-format:
	npx --prefix apps/canvas prettier "apps/canvas/src/**/*.{ts,tsx,js,jsx,css,md}" --write

canvas-test:
	npm --prefix apps/canvas run test -- --run

format:
	ruff format --config pyproject.toml .
	ruff check --config pyproject.toml . --select I001 --fix
	ruff check --config pyproject.toml . --select F401 --fix

test:
	$(UV_RUN) pytest --cov --cov-report term-missing -n auto tests/

doc:
	mkdocs serve --dev-addr=0.0.0.0:8080 --livereload

dev-server:
	uvicorn --app-dir apps/backend/src orcheo_backend.app:app --reload --port 2025

redis:
	docker compose up -d redis

worker:
	$(UV_RUN) celery -A orcheo_backend.worker.celery_app worker --loglevel=info

celery-beat:
	$(UV_RUN) celery -A orcheo_backend.worker.celery_app beat --loglevel=info

# Docker Compose commands for full-stack development
docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-restart:
	docker compose restart

docker-build:
	docker compose build

# Build just the workspace-sandbox image that the sandbox-runtime service
# spawns on demand. `docker-build` already covers this; keep the target for
# operators who only want to rebuild this one image after editing
# Dockerfile.workspace-sandbox.
workspace-sandbox-build:
	docker compose build workspace-sandbox

docker-logs:
	docker compose logs -f

staging-env:
	$(UV_RUN) orcheo install ensure-stack-env --env-file "$(STACK_ENV_FILE)" --env-template "$(STACK_ENV_TEMPLATE)"

# Materialize the Envoy forward-proxy config from $STACK_ENV_FILE so the
# egress-proxy container mounts a YAML that reflects
# ORCHEO_SANDBOX_EGRESS_ALLOWED_HOSTS without any extra operator action.
# The renderer parses the env file itself (no shell sourcing — quoted
# values with spaces in .env would otherwise confuse /bin/sh).
staging-egress-config: staging-env
	$(UV_RUN) python -m orcheo.sandbox.egress.render \
		--env-file "$(STACK_ENV_FILE)" \
		--output "$(STACK_DIR)/envoy-forward-proxy.yaml"

staging-up: staging-env staging-egress-config
	$(STAGING_COMPOSE) up -d

staging-down: staging-env
	$(STAGING_COMPOSE) down

staging-restart: staging-env
	$(STAGING_COMPOSE) restart

staging-build: staging-env
	$(STAGING_COMPOSE) build --pull

staging-logs: staging-env
	$(STAGING_COMPOSE) logs -f

staging-config: staging-env
	$(STAGING_COMPOSE) config
