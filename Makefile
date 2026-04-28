.PHONY: dev-server test lint format canvas-lint canvas-format canvas-test redis worker celery-beat \
       docker-up docker-down docker-build docker-logs staging-env staging-up staging-down staging-restart \
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
	$(UV_RUN) ruff check src/orcheo packages/sdk/src packages/agentensor/src apps/backend/src
	$(UV_RUN) mypy src/orcheo packages/sdk/src packages/agentensor/src apps/backend/src --install-types --non-interactive
	$(UV_RUN) ruff format . --check

canvas-lint:
	npm --prefix apps/canvas run lint

canvas-format:
	npx --prefix apps/canvas prettier "apps/canvas/src/**/*.{ts,tsx,js,jsx,css,md}" --write

canvas-test:
	npm --prefix apps/canvas run test -- --run

format:
	ruff format .
	ruff check . --select I001 --fix
	ruff check . --select F401 --fix

test:
	$(UV_RUN) pytest --cov --cov-report term-missing -n auto tests/

doc:
	mkdocs serve --dev-addr=0.0.0.0:8080 --livereload

dev-server:
	uvicorn --app-dir apps/backend/src orcheo_backend.app:app --reload --port 8000

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

docker-logs:
	docker compose logs -f

staging-env:
	$(UV_RUN) orcheo install ensure-stack-env --env-file "$(STACK_ENV_FILE)" --env-template "$(STACK_ENV_TEMPLATE)"

staging-up: staging-env
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
