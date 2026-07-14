#!/usr/bin/env bash
set -euo pipefail

# Builds the native macOS desktop app entirely from the local source
# checkout: Studio from apps/studio, the backend workspace from the repo, and
# the Swift shell from Sources/. This is the from-source counterpart of the
# Tauri release build -- it never consumes published Orcheo packages.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROOT_DIR="$(cd "${APP_DIR}/../../.." && pwd)"

APP_NAME="${ORCHEO_MACOS_APP_NAME:-Orcheo}"
APP_VERSION="${ORCHEO_MACOS_VERSION:-0.1.1}"
BUILD_DIR="${APP_DIR}/build"
APP_BUNDLE="${BUILD_DIR}/${APP_NAME}.app"
CONTENTS_DIR="${APP_BUNDLE}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
STAGED_BUNDLE_DIR="${APP_DIR}/bundle"
ICON_SOURCE="${ORCHEO_MACOS_ICON_SOURCE:-${ROOT_DIR}/apps/studio/public/orcheo-mark.png}"
ICONSET_DIR="${BUILD_DIR}/${APP_NAME}.iconset"

bash "${SCRIPT_DIR}/check-prereqs.sh"

if [[ "${ORCHEO_MACOS_SKIP_STUDIO_BUILD:-false}" != "true" ]]; then
  echo "Building Studio from source..."
  bash "${SCRIPT_DIR}/build-studio.sh"
fi

if [[ "${ORCHEO_MACOS_SKIP_RESOURCES:-false}" != "true" ]]; then
  bash "${SCRIPT_DIR}/prepare-resources.sh"
elif [[ ! -f "${STAGED_BUNDLE_DIR}/orcheo/pyproject.toml" ]]; then
  echo "ORCHEO_MACOS_SKIP_RESOURCES=true but no staged bundle exists at ${STAGED_BUNDLE_DIR}. Run scripts/prepare-resources.sh first." >&2
  exit 1
fi

echo "Building macOS shell..."
swift build --package-path "${APP_DIR}" --configuration release

rm -rf "${APP_BUNDLE}"
mkdir -p "${MACOS_DIR}" "${RESOURCES_DIR}"

SHELL_BINARY="$(swift build --package-path "${APP_DIR}" --configuration release --show-bin-path)/OrcheoDesktop"
cp "${SHELL_BINARY}" "${MACOS_DIR}/${APP_NAME}"

echo "Copying bundled resources..."
cp -R "${ROOT_DIR}/apps/studio/dist" "${RESOURCES_DIR}/studio"
cp -R "${STAGED_BUNDLE_DIR}/orcheo" "${RESOURCES_DIR}/orcheo"

if [[ -x "${STAGED_BUNDLE_DIR}/postgres/bin/postgres" ]]; then
  cp -R "${STAGED_BUNDLE_DIR}/postgres" "${RESOURCES_DIR}/postgres"
fi

if [[ -d "${STAGED_BUNDLE_DIR}/ms-playwright" ]]; then
  # cp -R preserves symlinks, which Chromium's versioned framework layout
  # (Framework.framework/Resources -> Versions/Current/..., etc.) requires;
  # dereferencing them would duplicate the ~220MB framework payload and has
  # historically broken the packaged browser.
  cp -R "${STAGED_BUNDLE_DIR}/ms-playwright" "${RESOURCES_DIR}/ms-playwright"
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
iconutil -c icns "${ICONSET_DIR}" -o "${RESOURCES_DIR}/AppIcon.icns"
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
  <string>${ORCHEO_MACOS_BUNDLE_ID:-com.orcheo.desktop}</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundleName</key>
  <string>${APP_NAME}</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>${APP_VERSION}</string>
  <key>CFBundleVersion</key>
  <string>${ORCHEO_MACOS_BUILD:-1}</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsLocalNetworking</key>
    <true/>
  </dict>
</dict>
</plist>
PLIST

if [[ "${ORCHEO_MACOS_CODESIGN_IDENTITY:-}" != "" ]]; then
  echo "Signing ${APP_BUNDLE}..."
  codesign --force --deep --options runtime \
    --sign "${ORCHEO_MACOS_CODESIGN_IDENTITY}" \
    "${APP_BUNDLE}"
else
  echo "Ad-hoc signing ${APP_BUNDLE}..."
  codesign --force --deep --sign - "${APP_BUNDLE}"
fi

if [[ "${ORCHEO_MACOS_MAKE_DMG:-false}" == "true" ]]; then
  DMG_PATH="${BUILD_DIR}/${APP_NAME}_${APP_VERSION}_$(uname -m).dmg"
  echo "Creating ${DMG_PATH}..."
  DMG_STAGING="$(mktemp -d "${TMPDIR:-/tmp}/orcheo-dmg-XXXXXX")"
  trap 'rm -rf "${DMG_STAGING}"' EXIT
  ditto "${APP_BUNDLE}" "${DMG_STAGING}/${APP_NAME}.app"
  ln -s /Applications "${DMG_STAGING}/Applications"
  rm -f "${DMG_PATH}"
  hdiutil create \
    -volname "${APP_NAME}" \
    -srcfolder "${DMG_STAGING}" \
    -ov \
    -format UDZO \
    -imagekey zlib-level=9 \
    "${DMG_PATH}"
fi

echo "Created ${APP_BUNDLE}"
echo
echo "Open with:"
echo "  open '${APP_BUNDLE}'"
