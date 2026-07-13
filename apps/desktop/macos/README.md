# Orcheo macOS Desktop

This is the macOS-native (AppKit/WKWebView) version of the cross-platform
Tauri shell in `apps/desktop/tauri`. It supervises the same local services and
bundles the same resources, with one deliberate difference: **it always builds
from the local source checkout**. The Tauri release pipeline can build from
published Orcheo packages (`ORCHEO_TAURI_USE_PUBLISHED_RELEASES`); this app
has no such mode.

## What It Does

- Opens a native window with a startup screen.
- Starts the local FastAPI backend on `127.0.0.1`.
- Bundles a trimmed Orcheo checkout into the app resources.
- Bundles native Postgres (from Homebrew) and Playwright Chromium into the app
  resources for offline runtime startup.
- Points the backend at the built Studio bundle through `ORCHEO_STUDIO_DIST_DIR`.
- Optionally starts the Celery worker and beat with the same environment flags
  as the Tauri shell.
- Opens the backend-served Studio app once `/api/system/health` returns 200.
- Provides an app-menu ChatKit Settings dialog for saving a session-token
  signing key and restarting the local backend.
- Closing the window hides it while local services keep running; the Dock icon
  brings it back. Quit stops all supervised services.

## Prerequisites

- Xcode Command Line Tools (`swift`).
- Node.js and npm.
- `uv`.
- Homebrew, to source the bundled Postgres (or set
  `ORCHEO_MACOS_POSTGRES_SOURCE_DIR` to an existing Postgres prefix).

## Commands

From the repository root:

```bash
make desktop-macos-check
make desktop-macos-dev
make desktop-macos
make desktop-macos-clean
```

Or directly:

```bash
bash apps/desktop/macos/scripts/check-prereqs.sh
bash apps/desktop/macos/scripts/dev.sh
bash apps/desktop/macos/scripts/build-app.sh
bash apps/desktop/macos/scripts/clean.sh
```

`build-app.sh` builds Studio from source with `VITE_ORCHEO_AUTH_DISABLED=true`
(unless that variable is already set), stages a trimmed repo checkout, bundled
Postgres, and Playwright Chromium under `apps/desktop/macos/bundle/`, compiles
the Swift shell, and assembles `apps/desktop/macos/build/Orcheo.app`.

`dev.sh` skips packaging entirely: it builds Studio and runs the shell from the
checkout with `swift run`, resolving the repo root from the working directory.

## Useful Environment Variables

Runtime (shared with the Tauri shell):

- `ORCHEO_DESKTOP_REPO_ROOT`: path to an Orcheo checkout (overrides the
  bundled one).
- `ORCHEO_DESKTOP_BACKEND_PORT`: fixed local backend port; otherwise a free
  port in `22025-22999` is selected.
- `ORCHEO_DESKTOP_BACKEND_COMMAND`: override backend launch command.
- `ORCHEO_DESKTOP_WORKER_COMMAND`: override worker launch command.
- `ORCHEO_DESKTOP_BEAT_COMMAND`: override beat launch command.
- `ORCHEO_DESKTOP_START_WORKER=true`: start the Celery worker.
- `ORCHEO_DESKTOP_START_BEAT=true`: start Celery beat.
- `ORCHEO_DESKTOP_POSTGRES_DSN`: desktop-safe Postgres DSN. Without it, a
  managed desktop database is started via `scripts/desktop-postgres.sh`.
- `ORCHEO_STUDIO_DIST_DIR` / `ORCHEO_DESKTOP_STUDIO_DIST_DIR`: built Studio
  bundle directory.
- `PLAYWRIGHT_BROWSERS_PATH`: existing Playwright browser cache.

Build:

- `ORCHEO_MACOS_BUNDLE_POSTGRES=false`: skip Postgres bundling.
- `ORCHEO_MACOS_BUNDLE_PLAYWRIGHT=false`: skip Playwright browser bundling.
- `ORCHEO_MACOS_POSTGRES_SOURCE_DIR`: use an existing Postgres prefix instead
  of Homebrew.
- `ORCHEO_MACOS_SKIP_STUDIO_BUILD=true`: reuse the existing Studio dist.
- `ORCHEO_MACOS_SKIP_RESOURCES=true`: reuse the previously staged `bundle/`.
- `ORCHEO_MACOS_APP_NAME`, `ORCHEO_MACOS_VERSION`, `ORCHEO_MACOS_BUILD`,
  `ORCHEO_MACOS_BUNDLE_ID`, `ORCHEO_MACOS_ICON_SOURCE`: bundle metadata.
- `ORCHEO_MACOS_CODESIGN_IDENTITY`: sign with a Developer ID identity instead
  of ad-hoc.
- `ORCHEO_MACOS_MAKE_DMG=true`: also produce a drag-and-drop `.dmg` next to
  the `.app`.

For double-click launches, desktop-only settings can be placed in
`~/Library/Application Support/com.orcheo.desktop/desktop.env`.

## ChatKit Signing Key

Use **Orcheo → ChatKit Settings...** to save
`ORCHEO_CHATKIT_TOKEN_SIGNING_KEY` after the app has started. Saving the key
restarts the local backend. The key is persisted in the app data directory
with owner-only permissions, rather than inside the application bundle, so it
remains available after the app is moved to Applications.

The Tauri shell exposes the same control through **Orcheo → ChatKit
Settings...**.

## Current Boundaries

Like the Tauri shell, the packaged app bundles the Orcheo source checkout,
Postgres, and Playwright Chromium, but does not yet package Python, `uv`, or
Redis; `uv` must be installed on the machine running the app.
