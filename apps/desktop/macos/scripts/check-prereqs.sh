#!/usr/bin/env bash
set -euo pipefail

missing=0

check() {
  local command_name="$1"
  local install_hint="$2"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "- ${command_name}: ${install_hint}" >&2
    missing=1
  fi
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The native macOS desktop app can only be built on macOS." >&2
  exit 1
fi

check swift "Install the Xcode Command Line Tools with 'xcode-select --install'."
check uv "Install uv from https://docs.astral.sh/uv/getting-started/installation/ . The build uses it to install Python dependencies and Playwright Chromium."
check npm "Install Node.js and npm from https://nodejs.org/ . The build uses npm to build Studio from source."

if [[ "${missing}" != "0" ]]; then
  echo "" >&2
  echo "Missing macOS desktop prerequisite(s) listed above." >&2
  exit 1
fi

echo "macOS desktop prerequisites found."
