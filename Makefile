.PHONY: dev-server test lint format studio-lint studio-format studio-test redis worker celery-beat desktop-macos-check desktop-macos-dev desktop-macos desktop-macos-clean desktop-tauri-check desktop-tauri-dev desktop-tauri-build desktop-tauri-clean desktop-clean \
       docker-up docker-down docker-build docker-logs

UV ?= uv
UV_CACHE_DIR ?= .cache/uv
UV_RUN = UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run

lint:
	$(UV_RUN) ruff check --config pyproject.toml src/orcheo packages/sdk/src packages/agentensor/src apps/backend/src
	$(UV_RUN) mypy src/orcheo packages/sdk/src packages/agentensor/src apps/backend/src --install-types --non-interactive
	$(UV_RUN) ruff format --config pyproject.toml . --check

studio-lint:
	npm --prefix apps/studio run lint

studio-format:
	npx --prefix apps/studio prettier "apps/studio/src/**/*.{ts,tsx,js,jsx,css,md}" --write

studio-test:
	npm --prefix apps/studio run test -- --run

desktop-macos-check:
	bash apps/desktop/macos/scripts/check-prereqs.sh

desktop-macos-dev:
	bash apps/desktop/macos/scripts/dev.sh

desktop-macos:
	bash apps/desktop/macos/scripts/build-app.sh

desktop-macos-clean:
	bash apps/desktop/macos/scripts/clean.sh

desktop-tauri-check:
	npm --prefix apps/desktop/tauri run check:prereqs

desktop-tauri-dev:
	npm --prefix apps/desktop/tauri run dev

desktop-tauri-build:
	npm --prefix apps/desktop/tauri run build:app

desktop-tauri-clean:
	npm --prefix apps/desktop/tauri run clean

desktop-clean: desktop-tauri-clean desktop-macos-clean

format:
	ruff format --config pyproject.toml .
	ruff check --config pyproject.toml . --select I001 --fix
	ruff check --config pyproject.toml . --select F401 --fix

test:
	$(UV_RUN) pytest --cov --cov-report term-missing tests/

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

docker-logs:
	docker compose logs -f
