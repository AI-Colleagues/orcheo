# macOS Desktop Packaging

The macOS desktop package is the native (AppKit/WKWebView) version of the
cross-platform Tauri shell in `apps/desktop/tauri`. Native code owns
lifecycle, logs, and local service supervision; Studio remains the product UI.
Unlike the Tauri release pipeline, which can build from published Orcheo
packages, this app always builds from the local source checkout.

Current shape:

- `apps/desktop/macos` is self-contained: the Swift shell lives in
  `Sources/OrcheoDesktop` and the build pipeline in `scripts/`.
- `scripts/build-app.sh` builds Studio from source, stages a trimmed repo
  checkout, bundled Postgres, and Playwright Chromium under
  `apps/desktop/macos/bundle/`, compiles the Swift shell, and assembles
  `apps/desktop/macos/build/Orcheo.app`.
- `ORCHEO_STUDIO_DIST_DIR` lets the backend serve the built Studio SPA from
  the same local origin.
- The bundled checkout lives under `Contents/Resources/orcheo` so double-click
  launch does not require `ORCHEO_DESKTOP_REPO_ROOT`.
- `.env` is never bundled, so the desktop app does not accidentally attach to
  a normal Orcheo server deployment database or ports.
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
- Closing the window hides it while services keep running; the Dock icon
  brings it back, and Quit stops all supervised services.
- The app icon is generated from `apps/studio/public/orcheo.png`.

Build:

```bash
make desktop-macos
```

Run the built bundle:

```bash
open apps/desktop/macos/build/Orcheo.app
```

Run from the checkout without packaging:

```bash
make desktop-macos-dev
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
- `ORCHEO_MACOS_ICON_SOURCE=...` overrides the PNG used to generate
  `AppIcon.icns`.
- `ORCHEO_MACOS_BUNDLE_POSTGRES=false` skips bundling native PostgreSQL.
- `ORCHEO_MACOS_POSTGRES_SOURCE_DIR=...` uses an existing PostgreSQL prefix as
  the bundle source instead of Homebrew.
- `ORCHEO_MACOS_BUNDLE_PLAYWRIGHT=false` skips bundling Playwright's Chromium
  and headless shell browser payloads
  (`Contents/Resources/ms-playwright`).
- `ORCHEO_MACOS_SKIP_STUDIO_BUILD=true` reuses the existing Studio dist.
- `ORCHEO_MACOS_SKIP_RESOURCES=true` reuses the previously staged `bundle/`.
- `ORCHEO_MACOS_MAKE_DMG=true` also produces a drag-and-drop `.dmg`.

For double-click launches, put desktop-only settings in:

```text
~/Library/Application Support/com.orcheo.desktop/desktop.env
```

Optional explicit database example:

```dotenv
ORCHEO_DESKTOP_POSTGRES_DSN=postgresql://orcheo:orcheo@127.0.0.1:25432/orcheo_desktop
```

The app bundle still expects `uv` to be available outside the bundle. The
native shell directs `uv` caches, the Python environment, and managed Postgres
state to `~/Library/Application Support/com.orcheo.desktop` so the app bundle
itself stays read-only after launch. Playwright uses the bundled browser
payload when present and falls back to
`~/Library/Application Support/com.orcheo.desktop/ms-playwright` otherwise.

Use **Orcheo → ChatKit Settings...** to save
`ORCHEO_CHATKIT_TOKEN_SIGNING_KEY` after the app has started; the key is
stored in the app data directory with owner-only permissions and the local
backend restarts to pick it up.

For signed releases, set:

```bash
ORCHEO_MACOS_CODESIGN_IDENTITY="Developer ID Application: Example"
make desktop-macos
```

Then notarize the resulting `.app` or a `.dmg` wrapper with
`xcrun notarytool`.
