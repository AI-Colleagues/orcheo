#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="${ORCHEO_MACOS_APP_NAME:-Orcheo}"
BUILD_DIR="${ROOT_DIR}/build/macos"
APP_DIR="${BUILD_DIR}/${APP_NAME}.app"
CONTENTS_DIR="${APP_DIR}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
SWIFT_PACKAGE_DIR="${ROOT_DIR}/apps/desktop/macos"
REPO_BUNDLE_DIR="${RESOURCES_DIR}/orcheo"
POSTGRES_BUNDLE_DIR="${RESOURCES_DIR}/postgres"
PLAYWRIGHT_BUNDLE_DIR="${RESOURCES_DIR}/ms-playwright"
ICON_SOURCE="${ORCHEO_MACOS_ICON_SOURCE:-${ROOT_DIR}/apps/studio/public/orcheo.png}"
ICONSET_DIR="${BUILD_DIR}/${APP_NAME}.iconset"
ICON_FILE="${RESOURCES_DIR}/AppIcon.icns"

rm -rf "${APP_DIR}"
mkdir -p "${MACOS_DIR}" "${RESOURCES_DIR}"

echo "Building Studio..."
(
  cd "${ROOT_DIR}"
  VITE_ORCHEO_AUTH_DISABLED="${VITE_ORCHEO_AUTH_DISABLED:-true}" \
    npm --prefix apps/studio run build
)

echo "Building macOS shell..."
swift build \
  --package-path "${SWIFT_PACKAGE_DIR}" \
  --configuration release

SHELL_BINARY="${SWIFT_PACKAGE_DIR}/.build/release/OrcheoDesktop"
cp "${SHELL_BINARY}" "${MACOS_DIR}/${APP_NAME}"
cp -R "${ROOT_DIR}/apps/studio/dist" "${RESOURCES_DIR}/studio"

if [[ "${ORCHEO_MACOS_BUNDLE_POSTGRES:-true}" == "true" ]]; then
  echo "Bundling native Postgres..."
  "${ROOT_DIR}/scripts/bundle-postgres-macos.sh" "${POSTGRES_BUNDLE_DIR}"
fi

if [[ "${ORCHEO_MACOS_BUNDLE_PLAYWRIGHT:-true}" == "true" ]]; then
  echo "Bundling Playwright Chromium..."
  PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BUNDLE_DIR}" \
    uv run python -m playwright install chromium chromium-headless-shell
fi

echo "Bundling Orcheo checkout..."
COMMON_RSYNC_EXCLUDES=(
  --exclude ".git/"
  --exclude ".git"
  --exclude ".venv/"
  --exclude ".cache/"
  --exclude ".mypy_cache/"
  --exclude ".pytest_cache/"
  --exclude ".ruff_cache/"
  --exclude "**/__pycache__/"
  --exclude "**/*.pyc"
  --exclude "node_modules/"
  --exclude "apps/studio/node_modules/"
  --exclude "apps/desktop/macos/.build/"
  --exclude "build/"
  --exclude "dist/"
  --exclude "dist-ssr/"
  --exclude ".DS_Store"
)

if [[ "${ORCHEO_MACOS_INCLUDE_ENV:-false}" == "true" ]]; then
  rsync -a --delete "${COMMON_RSYNC_EXCLUDES[@]}" \
    "${ROOT_DIR}/" "${REPO_BUNDLE_DIR}/"
else
  rsync -a --delete "${COMMON_RSYNC_EXCLUDES[@]}" \
    --exclude ".env" \
    --exclude ".env.*" \
    "${ROOT_DIR}/" "${REPO_BUNDLE_DIR}/"
fi

if [[ -f "${REPO_BUNDLE_DIR}/.env" ]]; then
  echo "Warning: bundled local .env for this development app. Set ORCHEO_MACOS_INCLUDE_ENV=false for distributable builds."
fi

echo "Generating app icon..."
rm -rf "${ICONSET_DIR}"
mkdir -p "${ICONSET_DIR}"
sips -z 16 16 "${ICON_SOURCE}" --out "${ICONSET_DIR}/icon_16x16.png" >/dev/null
sips -z 32 32 "${ICON_SOURCE}" --out "${ICONSET_DIR}/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "${ICON_SOURCE}" --out "${ICONSET_DIR}/icon_32x32.png" >/dev/null
sips -z 64 64 "${ICON_SOURCE}" --out "${ICONSET_DIR}/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "${ICON_SOURCE}" --out "${ICONSET_DIR}/icon_128x128.png" >/dev/null
sips -z 256 256 "${ICON_SOURCE}" --out "${ICONSET_DIR}/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "${ICON_SOURCE}" --out "${ICONSET_DIR}/icon_256x256.png" >/dev/null
sips -z 512 512 "${ICON_SOURCE}" --out "${ICONSET_DIR}/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "${ICON_SOURCE}" --out "${ICONSET_DIR}/icon_512x512.png" >/dev/null
sips -z 1024 1024 "${ICON_SOURCE}" --out "${ICONSET_DIR}/icon_512x512@2x.png" >/dev/null
iconutil -c icns "${ICONSET_DIR}" -o "${ICON_FILE}"
rm -rf "${ICONSET_DIR}"

cat > "${CONTENTS_DIR}/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDisplayName</key>
  <string>${APP_NAME}</string>
  <key>CFBundleExecutable</key>
  <string>${APP_NAME}</string>
  <key>CFBundleIdentifier</key>
  <string>${ORCHEO_MACOS_BUNDLE_ID:-dev.orcheo.desktop}</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundleName</key>
  <string>${APP_NAME}</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>${ORCHEO_MACOS_VERSION:-0.1.0}</string>
  <key>CFBundleVersion</key>
  <string>${ORCHEO_MACOS_BUILD:-1}</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsLocalNetworking</key>
    <true/>
  </dict>
  <key>SUFeedURL</key>
  <string>${ORCHEO_SPARKLE_FEED_URL:-}</string>
</dict>
</plist>
PLIST

if [[ "${ORCHEO_MACOS_CODESIGN_IDENTITY:-}" != "" ]]; then
  echo "Signing ${APP_DIR}..."
  codesign --force --deep --options runtime \
    --sign "${ORCHEO_MACOS_CODESIGN_IDENTITY}" \
    "${APP_DIR}"
else
  echo "Ad-hoc signing ${APP_DIR}..."
  codesign --force --deep --sign - "${APP_DIR}"
fi

echo "Created ${APP_DIR}"
echo
echo "Open with:"
echo "  open '${APP_DIR}'"
