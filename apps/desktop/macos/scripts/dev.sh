#!/usr/bin/env bash
set -euo pipefail

# Runs the Swift shell directly from the source checkout without packaging a
# .app: Studio is built from apps/studio and the shell resolves the repo root
# from the working directory, so no resources are staged or bundled.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROOT_DIR="$(cd "${APP_DIR}/../../.." && pwd)"

bash "${SCRIPT_DIR}/check-prereqs.sh"

if [[ "${ORCHEO_MACOS_SKIP_STUDIO_BUILD:-false}" != "true" ]]; then
  echo "Building Studio from source..."
  bash "${SCRIPT_DIR}/build-studio.sh"
fi

cd "${ROOT_DIR}"
exec swift run --package-path "${APP_DIR}" OrcheoDesktop
