# Orcheo Tauri Desktop

This is a cross-platform Tauri prototype for evaluating whether Orcheo should
invest in Linux and Windows desktop support early, while keeping the native
macOS app in `apps/desktop/macos`.

## What It Does

- Opens a small Tauri startup screen.
- Starts the local FastAPI backend on `127.0.0.1`.
- Bundles a trimmed Orcheo checkout into the app resources.
- Bundles native Postgres and Redis on macOS and Playwright Chromium into the
  app resources for offline runtime startup.
- Points the backend at the built Studio bundle through `ORCHEO_STUDIO_DIST_DIR`.
- Starts the bundled Redis, then the Celery worker and beat against it, so
  scheduled workflows run in their own process. If Redis is missing or fails to
  start, the shell falls back to in-process cron dispatch and execution and the
  app still works.
- Opens the backend-served Studio app once `/api/system/health` returns 200.
- Provides an app-menu ChatKit Settings screen for saving a session-token
  signing key and restarting the local backend.

## Prerequisites

- Rust and Cargo.
- Node.js and npm.
- `uv` and the Orcheo Python dependencies.
- Platform webview dependencies required by Tauri.
- Postgres:
  - macOS builds bundle Postgres from Homebrew by default.
  - Linux can reuse `scripts/desktop-postgres.sh` when no DSN is configured.
  - Windows currently requires `ORCHEO_DESKTOP_POSTGRES_DSN` to point at a
    Windows-accessible Postgres database.

## Commands

From the repository root:

```bash
make desktop-tauri-check
make desktop-tauri-dev
make desktop-tauri-build
make desktop-tauri-clean
```

Or directly:

```bash
npm --prefix apps/desktop/tauri install
npm --prefix apps/desktop/tauri run check:prereqs
npm --prefix apps/desktop/tauri run prepare:resources
npm --prefix apps/desktop/tauri run dev
npm --prefix apps/desktop/tauri run build
npm --prefix apps/desktop/tauri run build:app
npm --prefix apps/desktop/tauri run build:dmg
npm --prefix apps/desktop/tauri run clean
```

`make desktop-tauri-build` builds only the macOS `.app`. `npm run build:dmg`
builds both the app and its DMG release package. Both commands first build
Studio with `VITE_ORCHEO_AUTH_DISABLED=true` unless that variable is already
set, then stage a trimmed repo, bundled Postgres, and Playwright Chromium under
`apps/desktop/tauri/bundle/` for inclusion in the Tauri app.

The bundled Chromium version is pinned by the `playwright` package version
(Playwright maps 1:1 to a Chromium revision). Local-source builds stage it with
`uv run --frozen` against `uv.lock`, and the published-release build
(`ORCHEO_TAURI_USE_PUBLISHED_RELEASES`) resolves it from the released Orcheo
packages; both print the resolved revision during the build
(`Bundled Playwright browser: chromium-…`). The Tauri UI itself renders in the
OS WebView (WKWebView / WebView2 / WebKitGTK), which is provided by the platform
and is not pinnable.

If the build fails with `failed to run 'cargo metadata'`, Cargo is not available
on `PATH`; install Rust with rustup and restart the shell before rebuilding.

## Useful Environment Variables

- `ORCHEO_DESKTOP_REPO_ROOT`: path to an Orcheo checkout.
- `ORCHEO_DESKTOP_BACKEND_PORT`: fixed local backend port.
- `ORCHEO_DESKTOP_BACKEND_COMMAND`: override backend launch command.
- `ORCHEO_DESKTOP_WORKER_COMMAND`: override worker launch command.
- `ORCHEO_DESKTOP_BEAT_COMMAND`: override beat launch command.
- `ORCHEO_DESKTOP_START_WORKER`, `ORCHEO_DESKTOP_START_BEAT`: start the Celery
  worker and beat. Default to true when Redis is bundled; set to `false` to run
  everything in the backend process instead.
- `REDIS_URL`: use an existing broker instead of starting the bundled Redis.
- `ORCHEO_DESKTOP_REDIS_BIN_DIR`: directory holding `redis-server` /
  `redis-cli`, overriding the bundled copy.
- `ORCHEO_INPROCESS_CRON`, `ORCHEO_INPROCESS_EXECUTION`: in-process cron
  dispatch and run execution. The shell turns each off when the matching Celery
  process is actually running against a live broker, and leaves them on
  otherwise; set either variable explicitly to override.
- `ORCHEO_CRON_DISPATCH_INTERVAL`: seconds between in-process cron polls
  (default `60`).
- `ORCHEO_DESKTOP_POSTGRES_DSN`: desktop-safe Postgres DSN.
- `ORCHEO_DESKTOP_STUDIO_DIST_DIR`: built Studio bundle directory.
- `ORCHEO_TAURI_BUNDLE_POSTGRES=false`: skip macOS Postgres bundling.
- `ORCHEO_TAURI_BUNDLE_REDIS=false`: skip macOS Redis bundling.
- `ORCHEO_TAURI_BUNDLE_PLAYWRIGHT=false`: skip Playwright browser bundling.
- `PLAYWRIGHT_BROWSERS_PATH`: existing Playwright browser cache.

## ChatKit Signing Key

Use **Orcheo → ChatKit Settings…** to save
`ORCHEO_CHATKIT_TOKEN_SIGNING_KEY` after the app has started. Saving the key
restarts the local backend. The key is persisted in the app data directory with
owner-only permissions on Unix, rather than inside the application bundle, so
it remains available after the app is moved to Applications.

The native macOS app exposes the same control through **Orcheo → ChatKit
Settings…**.

## Current Evaluation Boundaries

The Tauri shell is cross-platform, but release packaging is still intentionally
thin. The macOS build bundles the Orcheo source checkout, Postgres, and
Playwright Chromium, but does not yet package Python, `uv`, or Redis into native
installers. Windows still requires an external Postgres DSN.
