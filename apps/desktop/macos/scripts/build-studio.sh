#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"

# The desktop app serves Studio from the local backend origin with desktop
# auth disabled, so the bundle is always built from the source checkout with
# the same flag the Tauri shell uses.
VITE_ORCHEO_AUTH_DISABLED="${VITE_ORCHEO_AUTH_DISABLED:-true}" \
  npm --prefix "${ROOT_DIR}/apps/studio" run build
