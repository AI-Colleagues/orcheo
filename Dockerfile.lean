# Lean single-image Orcheo: the backend and Studio, both built from source.
#
# The backend serves the Studio bundle itself (ORCHEO_STUDIO_DIST_DIR). By
# default it also runs executions and cron triggers in-process, so a single
# container plus PostgreSQL is enough; deploy/lean/docker-compose.yml instead
# runs the same image as backend, Celery worker, and Celery Beat with Redis.

# Stage 1: build Studio with placeholder values. The entrypoint replaces them
# with runtime environment variable values at container startup.
FROM node:24-alpine AS studio-build

WORKDIR /studio

COPY apps/studio/package.json apps/studio/package-lock.json ./
RUN npm ci

COPY apps/studio/ ./

ENV VITE_ORCHEO_BACKEND_URL=__VITE_ORCHEO_BACKEND_URL__ \
    VITE_ORCHEO_AUTH_DISABLED=__VITE_ORCHEO_AUTH_DISABLED__ \
    VITE_ORCHEO_APPS_BASE_DOMAIN=__VITE_ORCHEO_APPS_BASE_DOMAIN__ \
    VITE_ORCHEO_CHATKIT_DOMAIN_KEY=__VITE_ORCHEO_CHATKIT_DOMAIN_KEY__ \
    VITE_ORCHEO_APPS_PORT=__VITE_ORCHEO_APPS_PORT__

RUN npm run build

# Stage 2: install the backend and its first-party packages (non-editable) into
# a standalone virtualenv, so the runtime image carries no source tree.
FROM python:3.12-slim AS python-build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/orcheo/venv

WORKDIR /build

# Every workspace member's metadata is needed for `uv sync --frozen`.
COPY pyproject.toml uv.lock README.md ./
COPY packages/ packages/
COPY apps/backend/ apps/backend/
COPY apps/app_gateway/ apps/app_gateway/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace \
      --package orcheo-backend --package orcheo-sdk

COPY src/ src/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable \
      --package orcheo-backend --package orcheo-sdk

# Stage 3: runtime
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    VIRTUAL_ENV=/opt/orcheo/venv \
    PATH=/opt/orcheo/venv/bin:$PATH

WORKDIR /app

# Install Node.js for MCP stdio transports (e.g., Slack node uses npx)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    gosu \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs=22.* \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN printf '%s\n' '#!/bin/sh' 'exec uv tool run "$@"' > /usr/local/bin/uvx \
    && chmod +x /usr/local/bin/uvx

# Coding-agent CLIs for CodexNode, ClaudeCodeNode, and AntigravityNode. They
# install as root and run as the orcheo user, so they cannot self-update;
# rebuild the image for newer releases (agy always installs the latest).
# Logins live under /data/home on the volume, not in the image.
ARG CODEX_VERSION=latest
ARG CLAUDE_CODE_VERSION=latest
RUN npm install -g \
      "@openai/codex@${CODEX_VERSION}" \
      "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force \
    && curl -fsSL -o /tmp/agy-install.sh https://antigravity.google/cli/install.sh \
    && HOME=/tmp/agy-home bash /tmp/agy-install.sh --dir /usr/local/bin \
    && test -x /usr/local/bin/agy \
    && rm -rf /tmp/agy-install.sh /tmp/agy-home /tmp/node-compile-cache
ENV DISABLE_AUTOUPDATER=1
RUN mkdir -p /data/home \
    && groupadd --gid 1000 orcheo \
    && useradd --uid 1000 --gid 1000 --home-dir /data/home --create-home --shell /bin/sh orcheo
COPY deploy/stack/orcheo-entrypoint.sh /usr/local/bin/orcheo-entrypoint
COPY deploy/lean/lean-entrypoint.sh /usr/local/bin/orcheo-lean-entrypoint
RUN chmod +x /usr/local/bin/orcheo-entrypoint /usr/local/bin/orcheo-lean-entrypoint
# Bundled ChatKit widgets, read by the agent node; the stack mounts these instead.
COPY deploy/stack/chatkit_widgets/ /app/examples/chatkit_widgets/widgets/

COPY --from=python-build /opt/orcheo/venv /opt/orcheo/venv

RUN python -m playwright install --with-deps chromium chromium-headless-shell \
    && chown -R orcheo:orcheo "$PLAYWRIGHT_BROWSERS_PATH"
RUN HOME=/data/home UV_TOOL_DIR=/usr/local/share/uv/tools UV_TOOL_BIN_DIR=/usr/local/bin \
    uv tool install --no-cache -U skill-mgr

ENV ORCHEO_STUDIO_DIST_DIR=/opt/orcheo/studio

# The pristine bundle keeps its placeholders; the entrypoint renders a copy
# into the served directory on every start so env changes take effect. Render
# once here too, so commands that bypass the entrypoint still find a bundle.
COPY --from=studio-build /studio/dist /opt/orcheo/studio-template
RUN mkdir -p "$ORCHEO_STUDIO_DIST_DIR" \
    && orcheo-lean-entrypoint \
    && chown -R orcheo:orcheo "$ORCHEO_STUDIO_DIST_DIR"

ENV ORCHEO_STUDIO_URL=http://localhost:2025 \
    ORCHEO_INPROCESS_CRON=true \
    ORCHEO_INPROCESS_EXECUTION=true \
    ORCHEO_PLUGIN_DIR=/data/plugins \
    ORCHEO_CACHE_DIR=/data/cache/orcheo \
    UV_CACHE_DIR=/data/cache/uv

EXPOSE 2025

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=12 \
    CMD ["curl", "-fsS", "-o", "/dev/null", "http://127.0.0.1:2025/api/system/health"]

ENTRYPOINT ["orcheo-lean-entrypoint"]
CMD ["python", "-m", "uvicorn", "orcheo_backend.app:app", "--host", "0.0.0.0", "--port", "2025", "--proxy-headers"]
