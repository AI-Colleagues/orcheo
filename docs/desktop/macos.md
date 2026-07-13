# macOS Desktop Packaging

The macOS desktop package is a thin native shell around the existing web-based
Orcheo Studio. Native code owns lifecycle, logs, update affordances, and local
service supervision; Studio remains the product UI.

Current shape:

- `apps/desktop/macos` contains the AppKit/WKWebView shell.
- `scripts/build-macos-app.sh` builds Studio, builds the Swift shell, and creates
  `build/macos/Orcheo.app`.
- `ORCHEO_STUDIO_DIST_DIR` lets the backend serve the built Studio SPA from the
  same local origin.
- The build script bundles a filtered checkout under
  `Contents/Resources/orcheo` so double-click launch does not require
  `ORCHEO_DESKTOP_REPO_ROOT`.
- `.env` is excluded by default so the desktop app does not accidentally attach
  to a normal Orcheo server deployment database or ports.
- The shell chooses an available loopback backend port in `22025-22999` unless
  `ORCHEO_DESKTOP_BACKEND_PORT` is set.
- Desktop launches default to `ORCHEO_WORKFLOW_DEFINITION_MODE=unrestricted`.
  In unrestricted mode, the shell sets
  `ORCHEO_WORKFLOW_TRUST_MODE=allow_client_uploads` so Studio upload/update
  controls and the CLI workflow upload endpoint are enabled. Explicit values in
  the inherited environment or `desktop.env` still win.
- If no explicit desktop Postgres DSN is configured, the shell starts a managed
  desktop database on `127.0.0.1:25432-25531`. It prefers the Postgres runtime
  bundled under `Contents/Resources/postgres`, then local PostgreSQL binaries.
- The app icon is generated from
  `apps/studio/public/orcheo.png`.

Build:

```bash
./scripts/build-macos-app.sh
```

Run the development bundle:

```bash
open build/macos/Orcheo.app
```

Useful environment:

- `ORCHEO_DESKTOP_REPO_ROOT=...` overrides the bundled checkout during
  development.
- `ORCHEO_DESKTOP_BACKEND_PORT=...` pins the local backend port; otherwise a
  free desktop-only port is selected automatically.
- `ORCHEO_DESKTOP_POSTGRES_DSN=...` points the desktop app at an explicit
  non-deployment Postgres database. This takes precedence over the managed
  desktop database and `ORCHEO_POSTGRES_DSN`.
- `ORCHEO_DESKTOP_POSTGRES_BIN_DIR=...` points the managed database helper at
  local PostgreSQL binaries containing `initdb`, `pg_ctl`, and `createdb`.
- `ORCHEO_DESKTOP_POSTGRES_LOCALE=en_US.UTF-8` overrides the locale used while
  initializing or stopping the managed desktop database.
- `ORCHEO_WORKFLOW_DEFINITION_MODE=restricted` opts the desktop backend into the
  restricted workflow compiler.
- `ORCHEO_WORKFLOW_TRUST_MODE=managed` keeps workflow uploads disabled even when
  the desktop backend is otherwise unrestricted.
- `ORCHEO_DESKTOP_START_WORKER=true` starts the Celery worker.
- `ORCHEO_DESKTOP_START_BEAT=true` starts Celery Beat.
- `ORCHEO_DESKTOP_BACKEND_COMMAND=...` overrides the backend command.
- `ORCHEO_DESKTOP_WORKER_COMMAND=...` overrides the worker command.
- `ORCHEO_DESKTOP_BEAT_COMMAND=...` overrides the beat command.
- `ORCHEO_MACOS_ICON_SOURCE=...` overrides the PNG used to generate `AppIcon.icns`.
- `ORCHEO_MACOS_INCLUDE_ENV=true` includes the local `.env` in a development
  bundle. Do not use this for distributable builds.
- `ORCHEO_MACOS_BUNDLE_POSTGRES=true` bundles native PostgreSQL. Enabled by
  default.
- `ORCHEO_MACOS_POSTGRES_SOURCE_DIR=...` uses an existing PostgreSQL prefix as
  the bundle source.
- `ORCHEO_MACOS_INSTALL_POSTGRES=true` lets the build install `postgresql@17`
  with Homebrew if it is not already installed. Enabled by default for desktop
  builds.
- `ORCHEO_MACOS_BUNDLE_PLAYWRIGHT=true` bundles Playwright's Chromium and
  headless shell browser payloads under `Contents/Resources/ms-playwright`.
  Enabled by default for desktop builds.
- `ORCHEO_SPARKLE_FEED_URL=...` configures the update feed placeholder.

For double-click launches, put desktop-only settings in:

```text
~/Library/Application Support/com.orcheo.desktop/desktop.env
```

Optional explicit database example:

```dotenv
ORCHEO_DESKTOP_POSTGRES_DSN=postgresql://orcheo:orcheo@127.0.0.1:25432/orcheo_desktop
```

The current app bundle is a development shell. It still expects `uv` to be
available outside the bundle. The native shell directs `uv` caches, the Python
environment, and managed Postgres state to
`~/Library/Application Support/com.orcheo.desktop` so the app bundle itself stays
read-only after launch. Playwright uses the bundled browser payload when present
and falls back to `~/Library/Application Support/com.orcheo.desktop/ms-playwright`
otherwise.

The next packaging milestone is to replace source-checkout execution with
bundled runtime resources:

- embedded Python 3.12 runtime and locked wheel environment,
- native broker strategy or explicit external-service configuration,
- Sparkle framework integration for signed appcast updates.

For signed releases, set:

```bash
ORCHEO_MACOS_CODESIGN_IDENTITY="Developer ID Application: Example"
ORCHEO_SPARKLE_FEED_URL="https://updates.orcheo.dev/appcast.xml"
./scripts/build-macos-app.sh
```

Then notarize the resulting `.app` or a `.dmg`/`.pkg` wrapper with
`xcrun notarytool`.
