#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

rm -rf "${ROOT_DIR}/build/macos"
rm -rf "${ROOT_DIR}/apps/desktop/macos/.build"

echo "Removed ${ROOT_DIR}/build/macos"
echo "Removed ${ROOT_DIR}/apps/desktop/macos/.build"
