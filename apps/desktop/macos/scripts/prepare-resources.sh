#!/usr/bin/env bash
set -euo pipefail

# Stages everything the packaged app needs under apps/desktop/macos/bundle/,
# mirroring the Tauri shell's prepare-resources step -- except this app is
# always built from the local source checkout. There is deliberately no
# published-packages mode here; the Tauri release pipeline
# (ORCHEO_TAURI_USE_PUBLISHED_RELEASES) is the only build that consumes
# published Orcheo packages.

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(cd "${APP_DIR}/../../.." && pwd)"
BUNDLE_DIR="${APP_DIR}/bundle"
STAGED_REPO="${BUNDLE_DIR}/orcheo"
STAGED_POSTGRES="${BUNDLE_DIR}/postgres"
STAGED_REDIS="${BUNDLE_DIR}/redis"
STAGED_PLAYWRIGHT="${BUNDLE_DIR}/ms-playwright"

if [[ ! -f "${ROOT_DIR}/apps/studio/dist/index.html" ]]; then
  echo "Studio is not built at ${ROOT_DIR}/apps/studio/dist. Run 'bash ${APP_DIR}/scripts/build-studio.sh' first." >&2
  exit 1
fi

# Only the trimmed repo is staged unconditionally. The Postgres, Redis and
# Playwright staging dirs are created by their bundling steps below, so a
# skipped component never leaves an empty directory behind -- the runtime
# prefers a bundled directory whenever it merely exists.
rm -rf "${STAGED_REPO}" "${STAGED_POSTGRES}" "${STAGED_REDIS}" "${STAGED_PLAYWRIGHT}"
mkdir -p "${STAGED_REPO}"

# The backend only needs the workspace members it actually depends on
# (`uv run` from the repo root installs this whole workspace) plus the one
# script the desktop shell shells out to. Everything else in the monorepo
# (other apps, docs, marketing sites, test fixtures, agent skills, etc.) is
# dead weight in a packaged app.
REQUIRED_ROOT_FILES=(pyproject.toml uv.lock README.md .python-version)
REQUIRED_ROOT_DIRS=(src apps/backend packages/agentensor packages/sdk)
REQUIRED_SCRIPT_FILES=(scripts/desktop-postgres.sh scripts/desktop-redis.sh)

RSYNC_EXCLUDES=(
  --exclude ".cache/"
  --exclude ".coverage-shards/"
  --exclude ".git/"
  --exclude ".mypy_cache/"
  --exclude ".orcheo/"
  --exclude ".pytest_cache/"
  --exclude ".ruff_cache/"
  --exclude ".tox/"
  --exclude ".venv/"
  --exclude "__pycache__/"
  --exclude "build/"
  --exclude "dist-ssr/"
  --exclude "htmlcov/"
  --exclude "node_modules/"
  --exclude "target/"
  --exclude ".DS_Store"
  --exclude "*.pyc"
  --exclude "*.pyo"
)

echo "Staging trimmed Orcheo checkout..."
for relative_path in "${REQUIRED_ROOT_FILES[@]}"; do
  if [[ -f "${ROOT_DIR}/${relative_path}" ]]; then
    cp -p "${ROOT_DIR}/${relative_path}" "${STAGED_REPO}/${relative_path}"
  fi
done

for relative_path in "${REQUIRED_ROOT_DIRS[@]}"; do
  mkdir -p "${STAGED_REPO}/${relative_path}"
  rsync -a --delete "${RSYNC_EXCLUDES[@]}" \
    "${ROOT_DIR}/${relative_path}/" "${STAGED_REPO}/${relative_path}/"
done

for relative_path in "${REQUIRED_SCRIPT_FILES[@]}"; do
  mkdir -p "$(dirname "${STAGED_REPO}/${relative_path}")"
  cp -p "${ROOT_DIR}/${relative_path}" "${STAGED_REPO}/${relative_path}"
done

if [[ "${ORCHEO_MACOS_BUNDLE_POSTGRES:-true}" != "false" ]]; then
  echo "Bundling native Postgres..."
  bash "${ROOT_DIR}/scripts/bundle-postgres-macos.sh" "${STAGED_POSTGRES}"

  # Development files are dead weight in a packaged runtime.
  rm -rf "${STAGED_POSTGRES}/include" "${STAGED_POSTGRES}/lib/postgresql/pkgconfig"
  find "${STAGED_POSTGRES}/lib/postgresql" -maxdepth 1 -name "*.a" -delete 2>/dev/null || true

  # Homebrew trees contain symlinks that point outside the staged bundle
  # (into /opt/homebrew/Cellar/...), which must be resolved to real files to
  # be portable inside the .app.
  POSTGRES_DEREF="${STAGED_POSTGRES}.deref"
  rm -rf "${POSTGRES_DEREF}"
  rsync -a --copy-links --copy-dirlinks "${STAGED_POSTGRES}/" "${POSTGRES_DEREF}/"
  rm -rf "${STAGED_POSTGRES}"
  mv "${POSTGRES_DEREF}" "${STAGED_POSTGRES}"

  find "${STAGED_POSTGRES}" -type d -exec chmod 755 {} +
  find "${STAGED_POSTGRES}" -type f -perm +111 -exec chmod 755 {} +
  find "${STAGED_POSTGRES}" -type f ! -perm +111 -exec chmod 644 {} +
fi

if [[ "${ORCHEO_MACOS_BUNDLE_REDIS:-true}" != "false" ]]; then
  echo "Bundling native Redis..."
  bash "${ROOT_DIR}/scripts/bundle-redis-macos.sh" "${STAGED_REDIS}"

  find "${STAGED_REDIS}" -type d -exec chmod 755 {} +
  find "${STAGED_REDIS}" -type f -perm +111 -exec chmod 755 {} +
  find "${STAGED_REDIS}" -type f ! -perm +111 -exec chmod 644 {} +
fi

if [[ "${ORCHEO_MACOS_BUNDLE_PLAYWRIGHT:-true}" != "false" ]]; then
  echo "Bundling Playwright Chromium..."
  mkdir -p "${STAGED_PLAYWRIGHT}"
  # Playwright's installer ships correct permissions, and its browser bundles
  # use macOS's standard versioned framework layout whose internal symlinks
  # must be preserved as-is (dereferencing would duplicate the framework
  # payload 2-3x over).
  # --frozen: install against the committed uv.lock without re-locking, so the
  # bundled Chromium cannot silently drift from the locked dependencies (see
  # README). Fails loudly if pyproject.toml/uv.lock are out of sync.
  (
    cd "${ROOT_DIR}"
    PLAYWRIGHT_BROWSERS_PATH="${STAGED_PLAYWRIGHT}" \
      uv run --frozen python -m playwright install chromium chromium-headless-shell
  )
fi

REQUIRED_STAGED_FILES=(
  "${STAGED_REPO}/pyproject.toml"
  "${STAGED_REPO}/uv.lock"
  "${STAGED_REPO}/apps/backend/src/orcheo_backend/app/__init__.py"
  "${STAGED_REPO}/scripts/desktop-postgres.sh"
  "${STAGED_REPO}/scripts/desktop-redis.sh"
)

if [[ "${ORCHEO_MACOS_BUNDLE_POSTGRES:-true}" != "false" ]]; then
  REQUIRED_STAGED_FILES+=(
    "${STAGED_POSTGRES}/bin/initdb"
    "${STAGED_POSTGRES}/bin/pg_ctl"
    "${STAGED_POSTGRES}/bin/postgres"
  )
fi

if [[ "${ORCHEO_MACOS_BUNDLE_REDIS:-true}" != "false" ]]; then
  REQUIRED_STAGED_FILES+=(
    "${STAGED_REDIS}/bin/redis-server"
    "${STAGED_REDIS}/bin/redis-cli"
  )
fi

if [[ "${ORCHEO_MACOS_BUNDLE_PLAYWRIGHT:-true}" != "false" ]]; then
  REQUIRED_STAGED_FILES+=("${STAGED_PLAYWRIGHT}")
fi

missing=0
for staged_path in "${REQUIRED_STAGED_FILES[@]}"; do
  if [[ ! -e "${staged_path}" ]]; then
    if [[ "${missing}" == "0" ]]; then
      echo "Staged macOS resource bundle is missing required files:" >&2
    fi
    echo "- ${staged_path}" >&2
    missing=1
  fi
done
if [[ "${missing}" != "0" ]]; then
  exit 1
fi

echo "Prepared macOS resource bundle at ${BUNDLE_DIR}"
