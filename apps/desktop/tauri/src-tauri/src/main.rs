use serde::Serialize;
use std::collections::HashMap;
use std::env;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::net::{TcpListener, TcpStream};
#[cfg(unix)]
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
#[cfg(target_os = "macos")]
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, Manager};
use uuid::Uuid;

#[cfg(target_os = "macos")]
const QUIT_MENU_ID: &str = "orcheo_quit";

#[derive(Default)]
struct SupervisorState {
    runtime: Option<DesktopRuntime>,
}

struct DesktopRuntime {
    configuration: DesktopConfiguration,
    processes: Vec<ManagedProcess>,
    managed_postgres: bool,
}

#[derive(Clone)]
struct DesktopConfiguration {
    repo_root: PathBuf,
    app_support_dir: PathBuf,
    logs_dir: PathBuf,
    studio_dist_dir: PathBuf,
    playwright_browsers_dir: PathBuf,
    backend_port: u16,
    backend_url: String,
    backend_command: String,
    worker_command: String,
    beat_command: String,
    start_worker: bool,
    start_beat: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopStatus {
    backend_url: String,
    repo_root: String,
    studio_dist_dir: String,
    logs_dir: String,
}

struct ManagedProcess {
    child: Child,
    log_path: PathBuf,
    output_threads: Vec<JoinHandle<()>>,
}

#[tauri::command]
fn start_orcheo(
    app: AppHandle,
    state: tauri::State<'_, Mutex<SupervisorState>>,
) -> Result<DesktopStatus, String> {
    let mut guard = state
        .lock()
        .map_err(|_| "Supervisor lock is poisoned.".to_string())?;
    if let Some(runtime) = &guard.runtime {
        return Ok(runtime.configuration.status());
    }

    let runtime = start_runtime(&app)?;
    let status = runtime.configuration.status();
    guard.runtime = Some(runtime);
    Ok(status)
}

#[tauri::command]
fn restart_orcheo(
    app: AppHandle,
    state: tauri::State<'_, Mutex<SupervisorState>>,
) -> Result<DesktopStatus, String> {
    let mut guard = state
        .lock()
        .map_err(|_| "Supervisor lock is poisoned.".to_string())?;
    if let Some(runtime) = guard.runtime.as_mut() {
        runtime.stop();
    }
    guard.runtime = None;

    let runtime = start_runtime(&app)?;
    let status = runtime.configuration.status();
    guard.runtime = Some(runtime);
    Ok(status)
}

#[tauri::command]
fn open_logs(
    app: AppHandle,
    state: tauri::State<'_, Mutex<SupervisorState>>,
) -> Result<(), String> {
    let guard = state
        .lock()
        .map_err(|_| "Supervisor lock is poisoned.".to_string())?;
    let logs_dir = if let Some(logs_dir) = guard
        .runtime
        .as_ref()
        .map(|runtime| runtime.configuration.logs_dir.clone())
    {
        logs_dir
    } else {
        app.path()
            .app_log_dir()
            .map_err(|error| format!("Could not resolve app log directory: {error}"))?
    };
    fs::create_dir_all(&logs_dir).map_err(|error| {
        format!(
            "Could not create log directory at {}: {error}",
            logs_dir.display()
        )
    })?;
    open_path(&logs_dir)
}

fn main() {
    let mut builder = tauri::Builder::default()
        .manage(Mutex::new(SupervisorState::default()))
        .invoke_handler(tauri::generate_handler![
            start_orcheo,
            restart_orcheo,
            open_logs
        ]);

    #[cfg(target_os = "macos")]
    {
        builder = builder.menu(build_macos_menu).on_menu_event(|app, event| {
            if event.id() == QUIT_MENU_ID {
                app.exit(0);
            }
        });
    }

    let app = builder
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                if let Err(error) = window.hide() {
                    eprintln!("failed to hide Orcheo window: {error}");
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build Orcheo");

    app.run(|app_handle, event| match event {
        tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit => {
            stop_runtime(app_handle);
        }
        #[cfg(target_os = "macos")]
        tauri::RunEvent::Reopen {
            has_visible_windows,
            ..
        } => {
            if !has_visible_windows {
                if let Some(window) = app_handle.get_webview_window("main") {
                    if let Err(error) = window.show() {
                        eprintln!("failed to show Orcheo window: {error}");
                    }
                    let _ = window.set_focus();
                }
            }
        }
        _ => {}
    });
}

fn stop_runtime(app: &AppHandle) {
    if let Some(state) = app.try_state::<Mutex<SupervisorState>>() {
        if let Ok(mut guard) = state.lock() {
            if let Some(runtime) = guard.runtime.as_mut() {
                runtime.stop();
            }
            guard.runtime = None;
        }
    }
}

#[cfg(target_os = "macos")]
fn build_macos_menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let about = PredefinedMenuItem::about(app, None, None)?;
    let services = PredefinedMenuItem::services(app, None)?;
    let hide = PredefinedMenuItem::hide(app, None)?;
    let hide_others = PredefinedMenuItem::hide_others(app, None)?;
    let quit = MenuItem::with_id(app, QUIT_MENU_ID, "Quit Orcheo", true, Some("CmdOrCtrl+Q"))?;
    let app_menu = Submenu::with_items(
        app,
        "Orcheo",
        true,
        &[
            &about,
            &PredefinedMenuItem::separator(app)?,
            &services,
            &PredefinedMenuItem::separator(app)?,
            &hide,
            &hide_others,
            &PredefinedMenuItem::separator(app)?,
            &quit,
        ],
    )?;

    let file_menu = Submenu::with_items(
        app,
        "File",
        true,
        &[&PredefinedMenuItem::close_window(app, None)?],
    )?;

    let edit_menu = Submenu::with_items(
        app,
        "Edit",
        true,
        &[
            &PredefinedMenuItem::undo(app, None)?,
            &PredefinedMenuItem::redo(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, None)?,
            &PredefinedMenuItem::copy(app, None)?,
            &PredefinedMenuItem::paste(app, None)?,
            &PredefinedMenuItem::select_all(app, None)?,
        ],
    )?;

    let window_menu = Submenu::with_items(
        app,
        "Window",
        true,
        &[
            &PredefinedMenuItem::minimize(app, None)?,
            &PredefinedMenuItem::maximize(app, None)?,
            &PredefinedMenuItem::fullscreen(app, None)?,
        ],
    )?;

    Menu::with_items(app, &[&app_menu, &file_menu, &edit_menu, &window_menu])
}

fn start_runtime(app: &AppHandle) -> Result<DesktopRuntime, String> {
    let configuration = DesktopConfiguration::load(app)?;
    if !configuration.studio_dist_dir.join("index.html").is_file() {
        return Err(format!(
            "Studio is not built at {}. Run `npm --prefix apps/desktop/tauri run build:studio` first.",
            configuration.studio_dist_dir.display()
        ));
    }
    fs::create_dir_all(&configuration.logs_dir).map_err(|error| {
        format!(
            "Could not create log directory at {}: {error}",
            configuration.logs_dir.display()
        )
    })?;

    let (environment, managed_postgres) = process_environment(&configuration)?;
    let mut runtime = DesktopRuntime {
        configuration,
        processes: Vec::new(),
        managed_postgres,
    };

    let backend_command = runtime.configuration.backend_command.clone();
    let worker_command = runtime.configuration.worker_command.clone();
    let beat_command = runtime.configuration.beat_command.clone();
    let start_worker = runtime.configuration.start_worker;
    let start_beat = runtime.configuration.start_beat;

    runtime.start_process("backend", &backend_command, &environment)?;
    if start_worker {
        runtime.start_process("worker", &worker_command, &environment)?;
    }
    if start_beat {
        runtime.start_process("beat", &beat_command, &environment)?;
    }

    let backend_port = runtime.configuration.backend_port;
    wait_for_backend(
        &mut runtime,
        "127.0.0.1",
        backend_port,
        "/api/system/health",
    )?;
    Ok(runtime)
}

impl DesktopRuntime {
    fn start_process(
        &mut self,
        name: &str,
        command: &str,
        environment: &HashMap<String, String>,
    ) -> Result<(), String> {
        let log_path = self.configuration.logs_dir.join(format!("{name}.log"));
        let process = ManagedProcess::start(
            command,
            &self.configuration.repo_root,
            environment,
            &log_path,
        )
        .map_err(|error| format!("Could not start {name}: {error}"))?;
        self.processes.push(process);
        Ok(())
    }

    fn stop(&mut self) {
        for process in self.processes.iter_mut().rev() {
            process.stop();
        }
        self.processes.clear();

        if self.managed_postgres {
            let _ = run_desktop_postgres_script("stop", &self.configuration);
            self.managed_postgres = false;
        }
    }
}

impl Drop for DesktopRuntime {
    fn drop(&mut self) {
        self.stop();
    }
}

impl DesktopConfiguration {
    fn load(app: &AppHandle) -> Result<Self, String> {
        let environment = env::vars().collect::<HashMap<_, _>>();
        let app_support_dir = app
            .path()
            .app_data_dir()
            .map_err(|error| format!("Could not resolve app data directory: {error}"))?;
        let logs_dir = app
            .path()
            .app_log_dir()
            .map_err(|error| format!("Could not resolve app log directory: {error}"))?;

        let repo_root = resolve_repo_root(app, &environment)?;
        let backend_port = environment
            .get("ORCHEO_DESKTOP_BACKEND_PORT")
            .and_then(|value| value.parse::<u16>().ok())
            .map(Ok)
            .unwrap_or_else(find_available_loopback_port)?;
        let backend_url = format!("http://127.0.0.1:{backend_port}");
        let studio_dist_dir = resolve_studio_dist_dir(app, &environment, &repo_root);
        let playwright_browsers_dir =
            resolve_playwright_browsers_dir(app, &environment, &app_support_dir);
        let backend_command = environment
            .get("ORCHEO_DESKTOP_BACKEND_COMMAND")
            .cloned()
            .unwrap_or_else(|| default_backend_command(&repo_root, backend_port));
        let worker_command = environment
            .get("ORCHEO_DESKTOP_WORKER_COMMAND")
            .cloned()
            .unwrap_or_else(|| {
                "uv run celery -A orcheo_backend.worker.celery_app worker --loglevel=info"
                    .to_string()
            });
        let beat_schedule = app_support_dir.join("celerybeat-schedule");
        let beat_command = environment
            .get("ORCHEO_DESKTOP_BEAT_COMMAND")
            .cloned()
            .unwrap_or_else(|| {
                shell_set_env_command(
                    "ORCHEO_CELERY_BEAT_SCHEDULE_FILE",
                    &beat_schedule,
                    "uv run celery -A orcheo_backend.worker.celery_app beat --loglevel=info",
                )
            });

        Ok(Self {
            repo_root,
            app_support_dir,
            logs_dir,
            studio_dist_dir,
            playwright_browsers_dir,
            backend_port,
            backend_url,
            backend_command,
            worker_command,
            beat_command,
            start_worker: bool_env(&environment, "ORCHEO_DESKTOP_START_WORKER"),
            start_beat: bool_env(&environment, "ORCHEO_DESKTOP_START_BEAT"),
        })
    }

    fn status(&self) -> DesktopStatus {
        DesktopStatus {
            backend_url: self.backend_url.clone(),
            repo_root: self.repo_root.display().to_string(),
            studio_dist_dir: self.studio_dist_dir.display().to_string(),
            logs_dir: self.logs_dir.display().to_string(),
        }
    }
}

impl ManagedProcess {
    fn start(
        command: &str,
        working_dir: &Path,
        environment: &HashMap<String, String>,
        log_path: &Path,
    ) -> Result<Self, String> {
        let log = OpenOptions::new()
            .create(true)
            .append(true)
            .open(log_path)
            .map_err(|error| format!("could not open {}: {error}", log_path.display()))?;
        let log = Arc::new(Mutex::new(log));
        if let Ok(mut log_file) = log.lock() {
            let _ = writeln!(log_file, "[{}] Starting process", utc_timestamp());
            let _ = writeln!(log_file, "Command: {command}");
            let _ = writeln!(log_file, "Working directory: {}", working_dir.display());
        }

        let mut process = shell_command(command);
        #[cfg(unix)]
        {
            process.process_group(0);
        }
        let mut child = process
            .current_dir(working_dir)
            .envs(environment)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|error| format!("{command}: {error}"))?;

        let mut output_threads = Vec::new();
        if let Some(stdout) = child.stdout.take() {
            output_threads.push(copy_output(stdout, Arc::clone(&log)));
        }
        if let Some(stderr) = child.stderr.take() {
            output_threads.push(copy_output(stderr, Arc::clone(&log)));
        }

        Ok(Self {
            child,
            log_path: log_path.to_path_buf(),
            output_threads,
        })
    }

    fn try_wait(&mut self) -> Result<Option<std::process::ExitStatus>, String> {
        self.child
            .try_wait()
            .map_err(|error| format!("could not inspect child process: {error}"))
    }

    fn stop(&mut self) {
        terminate_process_tree(&mut self.child);
        let _ = self.child.wait();
        for handle in self.output_threads.drain(..) {
            let _ = handle.join();
        }
    }
}

#[cfg(unix)]
fn terminate_process_tree(child: &mut Child) {
    let process_group_id = child.id() as i32;
    signal_process_group(process_group_id, libc::SIGTERM);

    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        let child_exited = !matches!(child.try_wait(), Ok(None));
        if child_exited && !process_group_exists(process_group_id) {
            return;
        }
        thread::sleep(Duration::from_millis(100));
    }

    signal_process_group(process_group_id, libc::SIGKILL);
}

#[cfg(not(unix))]
fn terminate_process_tree(child: &mut Child) {
    let _ = child.kill();
}

#[cfg(unix)]
fn signal_process_group(process_group_id: i32, signal: i32) {
    unsafe {
        libc::kill(-process_group_id, signal);
    }
}

#[cfg(unix)]
fn process_group_exists(process_group_id: i32) -> bool {
    unsafe { libc::kill(-process_group_id, 0) == 0 }
}

fn process_environment(
    configuration: &DesktopConfiguration,
) -> Result<(HashMap<String, String>, bool), String> {
    let mut environment = env::vars().collect::<HashMap<_, _>>();
    apply_desktop_env_file(&mut environment, configuration);
    environment.insert("ORCHEO_HOST".to_string(), "127.0.0.1".to_string());
    environment.insert(
        "ORCHEO_PORT".to_string(),
        configuration.backend_port.to_string(),
    );
    environment.insert(
        "ORCHEO_STUDIO_URL".to_string(),
        configuration.backend_url.clone(),
    );
    environment.insert(
        "ORCHEO_STUDIO_DIST_DIR".to_string(),
        configuration.studio_dist_dir.display().to_string(),
    );
    environment
        .entry("ORCHEO_AUTH_MODE".to_string())
        .or_insert_with(|| "disabled".to_string());
    environment.insert(
        "ORCHEO_CORS_ALLOW_ORIGINS".to_string(),
        format!("[\"{}\"]", configuration.backend_url),
    );
    configure_desktop_workflow_upload_policy(&mut environment);
    environment.insert(
        "ORCHEO_DESKTOP_APP_SUPPORT_DIR".to_string(),
        configuration.app_support_dir.display().to_string(),
    );
    environment.insert(
        "ORCHEO_DESKTOP_LOG_DIR".to_string(),
        configuration.logs_dir.display().to_string(),
    );
    let managed_postgres = configure_desktop_postgres_dsn(&mut environment, configuration)?;
    configure_desktop_vault_key(&mut environment, configuration)?;
    environment.insert(
        "UV_CACHE_DIR".to_string(),
        configuration
            .app_support_dir
            .join("uv-cache")
            .display()
            .to_string(),
    );
    environment.insert(
        "UV_PROJECT_ENVIRONMENT".to_string(),
        configuration
            .app_support_dir
            .join("python-env")
            .display()
            .to_string(),
    );
    environment.insert(
        "PLAYWRIGHT_BROWSERS_PATH".to_string(),
        configuration.playwright_browsers_dir.display().to_string(),
    );
    configure_process_path(&mut environment);
    Ok((environment, managed_postgres))
}

fn configure_process_path(environment: &mut HashMap<String, String>) {
    let mut candidates = Vec::new();
    if let Some(existing) = non_empty(environment.get("PATH")) {
        candidates.extend(env::split_paths(&existing));
    }
    if let Some(home) = env::var_os("HOME") {
        let home = PathBuf::from(home);
        candidates.push(home.join(".local/bin"));
        candidates.push(home.join(".cargo/bin"));
    }
    candidates.extend(
        [
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            "/usr/local/bin",
            "/usr/local/sbin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ]
        .into_iter()
        .map(PathBuf::from),
    );

    let mut seen = Vec::<PathBuf>::new();
    for candidate in candidates {
        if !seen.iter().any(|existing| existing == &candidate) {
            seen.push(candidate);
        }
    }
    if let Ok(joined) = env::join_paths(seen) {
        environment.insert("PATH".to_string(), joined.to_string_lossy().to_string());
    }
}

fn resolve_repo_root(
    app: &AppHandle,
    environment: &HashMap<String, String>,
) -> Result<PathBuf, String> {
    if let Some(configured) = non_empty(environment.get("ORCHEO_DESKTOP_REPO_ROOT")) {
        return Ok(PathBuf::from(configured));
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        let bundled_repo = resource_dir.join("orcheo");
        if bundled_repo.join("pyproject.toml").is_file() {
            return Ok(bundled_repo);
        }
    }

    let mut candidate =
        env::current_dir().map_err(|error| format!("Could not read current directory: {error}"))?;
    loop {
        if candidate.join("pyproject.toml").is_file() && candidate.join("apps/backend").is_dir() {
            return Ok(candidate);
        }
        if !candidate.pop() {
            break;
        }
    }

    Err("Set ORCHEO_DESKTOP_REPO_ROOT to an Orcheo checkout or bundle the repo as a Tauri resource.".to_string())
}

fn default_backend_command(repo_root: &Path, backend_port: u16) -> String {
    if repo_root.join("apps/backend/src").is_dir() {
        return format!(
            "uv run uvicorn --app-dir apps/backend/src orcheo_backend.app:app --host 127.0.0.1 --port {backend_port}"
        );
    }

    format!("uv run uvicorn orcheo_backend.app:app --host 127.0.0.1 --port {backend_port}")
}

fn resolve_studio_dist_dir(
    app: &AppHandle,
    environment: &HashMap<String, String>,
    repo_root: &Path,
) -> PathBuf {
    if let Some(configured) = non_empty(
        environment
            .get("ORCHEO_STUDIO_DIST_DIR")
            .or_else(|| environment.get("ORCHEO_DESKTOP_STUDIO_DIST_DIR")),
    ) {
        return PathBuf::from(configured);
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        let bundled_studio = resource_dir.join("studio");
        if bundled_studio.join("index.html").is_file() {
            return bundled_studio;
        }
    }

    repo_root.join("apps/studio/dist")
}

fn resolve_playwright_browsers_dir(
    app: &AppHandle,
    environment: &HashMap<String, String>,
    app_support_dir: &Path,
) -> PathBuf {
    if let Some(configured) = non_empty(environment.get("PLAYWRIGHT_BROWSERS_PATH")) {
        return PathBuf::from(configured);
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        let bundled_browsers = resource_dir.join("ms-playwright");
        if bundled_browsers.exists() {
            return bundled_browsers;
        }
    }

    app_support_dir.join("ms-playwright")
}

fn configure_desktop_workflow_upload_policy(environment: &mut HashMap<String, String>) {
    let definition_mode = non_empty(environment.get("ORCHEO_WORKFLOW_DEFINITION_MODE"))
        .unwrap_or_else(|| "unrestricted".to_string());
    environment.insert(
        "ORCHEO_WORKFLOW_DEFINITION_MODE".to_string(),
        definition_mode.clone(),
    );

    if non_empty(environment.get("ORCHEO_WORKFLOW_TRUST_MODE")).is_none()
        && definition_mode.trim().eq_ignore_ascii_case("unrestricted")
    {
        environment.insert(
            "ORCHEO_WORKFLOW_TRUST_MODE".to_string(),
            "allow_client_uploads".to_string(),
        );
    }
}

fn apply_desktop_env_file(
    environment: &mut HashMap<String, String>,
    configuration: &DesktopConfiguration,
) {
    let values = load_dotenv_values(&configuration.app_support_dir.join("desktop.env"));
    for (key, value) in values {
        environment.entry(key).or_insert(value);
    }
}

fn configure_desktop_postgres_dsn(
    environment: &mut HashMap<String, String>,
    configuration: &DesktopConfiguration,
) -> Result<bool, String> {
    let dotenv_values = load_dotenv_values(&configuration.repo_root.join(".env"));

    if let Some(desktop_dsn) = non_empty(environment.get("ORCHEO_DESKTOP_POSTGRES_DSN")) {
        environment.insert("ORCHEO_POSTGRES_DSN".to_string(), desktop_dsn);
        return Ok(false);
    }

    if let Some(inherited_dsn) = non_empty(environment.get("ORCHEO_POSTGRES_DSN")) {
        if uses_deployment_postgres_port(&inherited_dsn) {
            return Err("The desktop app refuses to use ORCHEO_POSTGRES_DSN on localhost:5432 because that is the normal server deployment port. Set ORCHEO_DESKTOP_POSTGRES_DSN to a separate desktop database.".to_string());
        }
        return Ok(false);
    }

    if let Some(dotenv_dsn) = non_empty(dotenv_values.get("ORCHEO_POSTGRES_DSN")) {
        if uses_deployment_postgres_port(&dotenv_dsn) {
            return Err("The bundled .env points ORCHEO_POSTGRES_DSN at localhost:5432. Rebuild without .env or set ORCHEO_DESKTOP_POSTGRES_DSN to a separate desktop database.".to_string());
        }
        return Ok(false);
    }

    if cfg!(windows) {
        return Err("Managed desktop Postgres is not implemented for Windows yet. Set ORCHEO_DESKTOP_POSTGRES_DSN to a Windows-accessible Postgres database for this Tauri prototype.".to_string());
    }

    let managed_dsn = run_desktop_postgres_script("start", configuration)?;
    environment.insert("ORCHEO_POSTGRES_DSN".to_string(), managed_dsn);
    Ok(true)
}

fn configure_desktop_vault_key(
    environment: &mut HashMap<String, String>,
    configuration: &DesktopConfiguration,
) -> Result<(), String> {
    let dotenv_values = load_dotenv_values(&configuration.repo_root.join(".env"));
    if non_empty(environment.get("ORCHEO_VAULT_ENCRYPTION_KEY")).is_some()
        || non_empty(dotenv_values.get("ORCHEO_VAULT_ENCRYPTION_KEY")).is_some()
    {
        return Ok(());
    }

    let key_path = configuration.app_support_dir.join("desktop-vault.key");
    if let Ok(existing) = fs::read_to_string(&key_path) {
        if let Some(key) = non_empty(Some(&existing)) {
            environment.insert("ORCHEO_VAULT_ENCRYPTION_KEY".to_string(), key);
            return Ok(());
        }
    }

    fs::create_dir_all(&configuration.app_support_dir).map_err(|error| {
        format!(
            "Could not create app support directory at {}: {error}",
            configuration.app_support_dir.display()
        )
    })?;
    let key = format!("{}-{}", Uuid::new_v4(), Uuid::new_v4());
    fs::write(&key_path, &key)
        .map_err(|error| format!("Could not write {}: {error}", key_path.display()))?;
    environment.insert("ORCHEO_VAULT_ENCRYPTION_KEY".to_string(), key);
    Ok(())
}

fn run_desktop_postgres_script(
    action: &str,
    configuration: &DesktopConfiguration,
) -> Result<String, String> {
    let script_path = configuration.repo_root.join("scripts/desktop-postgres.sh");
    if !script_path.is_file() {
        return Err(format!(
            "Desktop Postgres helper is missing at {}.",
            script_path.display()
        ));
    }

    let output = Command::new(if cfg!(windows) { "bash" } else { "/bin/bash" })
        .arg(script_path)
        .arg(action)
        .arg(&configuration.app_support_dir)
        .arg(&configuration.logs_dir)
        .current_dir(&configuration.repo_root)
        .output()
        .map_err(|error| format!("Desktop Postgres helper failed to launch: {error}"))?;

    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    if !output.status.success() {
        return Err(if stderr.is_empty() { stdout } else { stderr });
    }
    Ok(stdout)
}

// A cold first launch has to let `uv` create a virtualenv and install the
// whole workspace (and, the first time, initialize the bundled Postgres data
// directory), which can comfortably exceed a minute. The loop below already
// exits immediately if the backend process itself exits, so a generous
// deadline here only guards against a truly hung process.
const BACKEND_HEALTH_TIMEOUT: Duration = Duration::from_secs(300);

fn wait_for_backend(
    runtime: &mut DesktopRuntime,
    host: &str,
    port: u16,
    path: &str,
) -> Result<(), String> {
    let deadline = Instant::now() + BACKEND_HEALTH_TIMEOUT;
    while Instant::now() < deadline {
        if health_check(host, port, path).unwrap_or(false) {
            return Ok(());
        }
        if let Some(backend) = runtime.processes.first_mut() {
            if let Some(status) = backend.try_wait()? {
                let log_tail = read_log_tail(&backend.log_path, 4000);
                let detail = if log_tail.is_empty() {
                    String::new()
                } else {
                    format!("\n\nBackend log tail:\n{log_tail}")
                };
                return Err(format!(
                    "Backend exited before becoming healthy at http://{host}:{port}{path} with status {status}.{detail}"
                ));
            }
        }
        thread::sleep(Duration::from_millis(500));
    }
    Err(format!(
        "Backend did not become healthy at http://{host}:{port}{path}."
    ))
}

fn health_check(host: &str, port: u16, path: &str) -> Result<bool, String> {
    let mut stream = TcpStream::connect((host, port)).map_err(|error| error.to_string())?;
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .map_err(|error| error.to_string())?;
    let request =
        format!("GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n");
    stream
        .write_all(request.as_bytes())
        .map_err(|error| error.to_string())?;

    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| error.to_string())?;
    Ok(response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200"))
}

fn find_available_loopback_port() -> Result<u16, String> {
    for port in 22025..=22999 {
        if TcpListener::bind(("127.0.0.1", port)).is_ok() {
            return Ok(port);
        }
    }
    Err("Could not find an available local backend port in 22025-22999.".to_string())
}

fn shell_command(command: &str) -> Command {
    if cfg!(windows) {
        let mut process = Command::new("cmd");
        process.args(["/C", command]);
        process
    } else {
        let mut process = Command::new("/bin/sh");
        process.args(["-lc", command]);
        process
    }
}

fn shell_set_env_command(key: &str, value: &Path, command: &str) -> String {
    if cfg!(windows) {
        format!("set \"{}={}\" && {}", key, value.display(), command)
    } else {
        format!("{}='{}' {}", key, value.display(), command)
    }
}

fn copy_output<R>(mut reader: R, log: Arc<Mutex<File>>) -> JoinHandle<()>
where
    R: Read + Send + 'static,
{
    thread::spawn(move || {
        let mut buffer = [0; 8192];
        loop {
            match reader.read(&mut buffer) {
                Ok(0) | Err(_) => break,
                Ok(size) => {
                    if let Ok(mut file) = log.lock() {
                        let _ = file.write_all(&buffer[..size]);
                    }
                }
            }
        }
    })
}

fn read_log_tail(path: &Path, max_bytes: u64) -> String {
    let Ok(mut file) = File::open(path) else {
        return String::new();
    };
    let Ok(length) = file.metadata().map(|metadata| metadata.len()) else {
        return String::new();
    };
    let start = length.saturating_sub(max_bytes);
    if file.seek(SeekFrom::Start(start)).is_err() {
        return String::new();
    }
    let mut contents = String::new();
    if file.read_to_string(&mut contents).is_err() {
        return String::new();
    }
    contents
}

fn utc_timestamp() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| format!("unix:{}", duration.as_secs()))
        .unwrap_or_else(|_| "unknown-time".to_string())
}

fn load_dotenv_values(path: &Path) -> HashMap<String, String> {
    let Ok(contents) = fs::read_to_string(path) else {
        return HashMap::new();
    };
    let mut values = HashMap::new();
    for raw_line in contents.lines() {
        let mut line = raw_line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some(stripped) = line.strip_prefix("export ") {
            line = stripped.trim();
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        let value = value
            .trim()
            .trim_matches('"')
            .trim_matches('\'')
            .to_string();
        values.insert(key.trim().to_string(), value);
    }
    values
}

fn bool_env(environment: &HashMap<String, String>, key: &str) -> bool {
    environment
        .get(key)
        .map(|value| {
            matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            )
        })
        .unwrap_or(false)
}

fn non_empty(value: Option<&String>) -> Option<String> {
    value
        .map(|candidate| candidate.trim().to_string())
        .filter(|candidate| !candidate.is_empty())
}

fn uses_deployment_postgres_port(dsn: &str) -> bool {
    let lowered = dsn.to_ascii_lowercase();
    lowered.contains("localhost:5432")
        || lowered.contains("127.0.0.1:5432")
        || lowered.contains("@localhost/")
        || lowered.contains("//localhost/")
        || lowered.contains("@127.0.0.1/")
        || lowered.contains("//127.0.0.1/")
}

fn open_path(path: &Path) -> Result<(), String> {
    let mut command = if cfg!(target_os = "macos") {
        let mut command = Command::new("open");
        command.arg(path);
        command
    } else if cfg!(windows) {
        let mut command = Command::new("explorer");
        command.arg(path);
        command
    } else {
        let mut command = Command::new("xdg-open");
        command.arg(path);
        command
    };

    command
        .spawn()
        .map_err(|error| format!("Could not open {}: {error}", path.display()))?;
    Ok(())
}
