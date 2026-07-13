#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for target in "${APP_DIR}/bundle" "${APP_DIR}/build" "${APP_DIR}/.build"; do
  rm -rf "${target}"
  echo "Removed ${target}"
done
