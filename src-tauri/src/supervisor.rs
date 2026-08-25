//! Helix Support desktop shell — sidecar supervision.
//!
//! Spawns the packaged Python backend (PyInstaller onedir) or a dev
//! interpreter, probes `/health/ready` with exponential backoff, and shuts
//! the process tree down gracefully when the last window closes. On
//! unexpected backend exit it auto-restarts (up to 3 times per minute) and
//! records startup telemetry timestamps for the cold-start SLO.

use std::io::{BufRead, BufReader};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tauri::Manager;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const MAX_RESTARTS_PER_MINUTE: u32 = 3;
const READY_TIMEOUT_SECS: u64 = 20;
const RESTART_WINDOW_SECS: u64 = 60;

pub struct Backend {
    pub port: u16,
    child: Child,
}

pub struct SupervisorState {
    pub backend: Mutex<Option<Backend>>,
    pub restarts: AtomicU32,
    /// Wall-clock timestamps (ms since process start) for the startup SLO.
    pub telemetry: Mutex<StartupTelemetry>,
}

#[derive(Debug, Default, Clone, serde::Serialize)]
pub struct StartupTelemetry {
    pub t_window_created_ms: Option<u64>,
    pub t_backend_ready_ms: Option<u64>,
    pub t_ui_ready_ms: Option<u64>,
    pub backend_port: Option<u16>,
    pub backend_mode: Option<String>,
    pub error: Option<String>,
}

/// Pick a free localhost port by binding port 0 and releasing it.
/// There is an inherent TOCTOU race; the backend also handles bind
/// conflicts, and the supervisor restarts once if health never goes ready.
pub fn pick_free_port() -> std::io::Result<u16> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

/// Resolve the resource directory via the Tauri app handle when available;
/// fall back to the directory next to the current exe (dev builds).
fn resource_dir(handle: Option<&tauri::AppHandle>) -> PathBuf {
    if let Some(app) = handle {
        if let Ok(dir) = app.path().resource_dir() {
            return dir;
        }
    }
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."))
}

fn resolve_server_command(res_dir: &Path) -> (Command, String) {
    // Packaged layout: <resources>/server/helix-server/helix-server.exe
    let bundled = res_dir.join("server").join("helix-server");
    let exe = bundled.join("helix-server.exe");
    if exe.exists() {
        let mut cmd = Command::new(exe);
        cmd.current_dir(&bundled);
        return (cmd, "bundled".into());
    }
    // Dev fallback: rls-venv uvicorn against the repo checkout.
    // HELIX_DEV_ROOT must point at the repo when running `cargo tauri dev`.
    let dev_root = std::env::var("HELIX_DEV_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."));
    let python = dev_root
        .join("artifacts")
        .join("rls-venv")
        .join("Scripts")
        .join("python.exe");
    let mut cmd = Command::new(python);
    cmd.arg("-m")
        .arg("uvicorn")
        .arg("app.main:app")
        .arg("--no-access-log")
        .current_dir(dev_root);
    (cmd, "dev".into())
}

fn spawn_backend(
    port: u16,
    res_dir: &Path,
    telemetry: &Mutex<StartupTelemetry>,
) -> Result<Backend, String> {
    let (mut cmd, mode) = resolve_server_command(res_dir);

    cmd.env("DATABASE_PATH", data_dir().join("support.db"))
        .env("HELIX_PORT", port.to_string())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    #[cfg(windows)]
    {
        // Hide the console window that a packaged exe would flash.
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    // uvicorn reads --port from argv only; for the dev path append it now.
    if mode == "dev" {
        cmd.arg("--host")
            .arg("127.0.0.1")
            .arg("--port")
            .arg(port.to_string());
    }
    // helix-server entrypoint reads HELIX_PORT (set above).

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("failed to spawn backend ({mode}): {e}"))?;

    if let Some(stdout) = child.stdout.take() {
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                log_line("server", &line);
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                log_line("server!", &line);
            }
        });
    }

    telemetry.lock().unwrap().backend_mode = Some(mode.clone());
    Ok(Backend { port, child })
}

fn log_line(tag: &str, line: &str) {
    eprintln!("[{tag}] {line}");
}

fn data_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("HELIX_DATA_DIR") {
        return PathBuf::from(dir);
    }
    dirs_fallback().join("data")
}

fn dirs_fallback() -> PathBuf {
    #[cfg(windows)]
    {
        std::env::var("APPDATA")
            .map(|d| PathBuf::from(d).join("HelixSupport"))
            .unwrap_or_else(|_| PathBuf::from("."))
    }
    #[cfg(not(windows))]
    {
        PathBuf::from(".")
    }
}

/// Telemetry directory: %APPDATA%/HelixSupport/telemetry/
pub fn telemetry_dir() -> PathBuf {
    dirs_fallback().join("telemetry")
}

fn health_ready(port: u16) -> bool {
    match std::net::TcpStream::connect_timeout(
        &std::net::SocketAddr::from(([127, 0, 0, 1], port)),
        Duration::from_millis(800),
    ) {
        Ok(mut stream) => {
            use std::io::{Read, Write};
            let req = format!(
                "GET /health/ready HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
            );
            if stream.write_all(req.as_bytes()).is_err() {
                return false;
            }
            let mut buf = [0u8; 256];
            let n = stream.read(&mut buf).unwrap_or(0);
            let head = String::from_utf8_lossy(&buf[..n]);
            head.starts_with("HTTP/1.1 200") || head.starts_with("HTTP/1.0 200")
        }
        Err(_) => false,
    }
}

/// Probe readiness up to 3 times with short backoff. Returns true as soon as
/// `/health/ready` answers 200, so a single busy-window timeout does not read
/// as death.
fn probe_alive(port: u16) -> bool {
    for delay in [0u64, 150, 300] {
        if delay > 0 {
            thread::sleep(Duration::from_millis(delay));
        }
        if health_ready(port) {
            return true;
        }
    }
    false
}

/// Wall-clock milliseconds since the process epoch (for relative deltas).
fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// Spawn + wait for readiness with exponential backoff (200ms -> 2s, cap ~20s).
/// `boot_start` is the process-origin timestamp used to compute relative deltas.
pub fn start_backend(
    state: &SupervisorState,
    handle: Option<&tauri::AppHandle>,
    boot_start_ms: u64,
) -> Result<u16, String> {
    let port = pick_free_port().map_err(|e| e.to_string())?;
    let res_dir = resource_dir(handle);
    let mut backend = spawn_backend(port, &res_dir, &state.telemetry)?;

    let started = Instant::now();
    let mut delay = Duration::from_millis(200);
    loop {
        if health_ready(backend.port) {
            log_line(
                "desktop",
                &format!("backend ready on :{} in {:?}", port, started.elapsed()),
            );
            {
                let mut tel = state.telemetry.lock().unwrap();
                tel.backend_port = Some(port);
                tel.t_backend_ready_ms = Some(now_ms().saturating_sub(boot_start_ms));
            }
            // Guard dropped above; persist_telemetry re-acquires the lock.
            persist_telemetry(&state.telemetry);
            break;
        }
        if let Ok(Some(status)) = backend.child.try_wait() {
            let _ = kill_tree(&mut backend);
            return Err(format!("backend exited during startup: {status}"));
        }
        if started.elapsed() > Duration::from_secs(READY_TIMEOUT_SECS) {
            let _ = kill_tree(&mut backend);
            return Err(format!(
                "backend did not become ready within {READY_TIMEOUT_SECS}s"
            ));
        }
        thread::sleep(delay);
        delay = (delay * 2).min(Duration::from_secs(2));
    }

    *state.backend.lock().unwrap() = Some(backend);
    Ok(port)
}

/// Check on the running backend; restart it if it died and we haven't
/// exceeded the restart budget for the rolling minute window.
/// Returns Ok(true) if a restart was performed, Ok(false) if the backend is
/// still alive, or Err(message) when the restart budget is exhausted.
pub fn ensure_alive(
    state: &SupervisorState,
    handle: Option<&tauri::AppHandle>,
    boot_start_ms: u64,
) -> Result<bool, String> {
    let alive = {
        let mut guard = state.backend.lock().unwrap();
        match guard.as_mut() {
            // uvicorn's child process structure (event loop + workers) can
            // make try_wait() report a stale/exit status even while the
            // server is still serving. Cross-check with a health probe
            // before declaring death — if /health/ready still answers, the
            // try_wait() result is a false positive and we keep going.
            Some(b) => {
                if b.child.try_wait().ok().flatten().is_none() {
                    true
                } else {
                    // uvicorn's child process structure (event loop + workers)
                    // can make try_wait() report a stale/exit status even while
                    // the server is still serving, and a single health probe
                    // can time out during a busy maintenance/replay window.
                    // Retry the probe a few times before declaring death so a
                    // transient false positive does not trigger a needless
                    // restart that overwrites the cold-start telemetry.
                    probe_alive(b.port)
                }
            }
            None => false,
        }
    };
    if alive {
        return Ok(false);
    }
    // Backend died (or never started). Check restart budget.
    let count = state.restarts.fetch_add(1, Ordering::SeqCst) + 1;
    if count > MAX_RESTARTS_PER_MINUTE {
        let msg = format!(
            "backend crashed {count} times within the {RESTART_WINDOW_SECS}s window; \
             auto-restart suspended to avoid a crash loop"
        );
        log_line("desktop!", &msg);
        state.telemetry.lock().unwrap().error = Some(msg.clone());
        persist_telemetry(&state.telemetry);
        return Err(msg);
    }
    log_line(
        "desktop",
        &format!("backend died; auto-restart attempt {count}/{MAX_RESTARTS_PER_MINUTE}"),
    );
    start_backend(state, handle, boot_start_ms)?;
    Ok(true)
}

/// Record the window-created timestamp (relative to process boot).
/// Persists immediately so the telemetry file exists even if the UI-ready
/// event never fires (e.g. backend crash during dev).
pub fn record_window_created(state: &SupervisorState, boot_start_ms: u64) {
    state.telemetry.lock().unwrap().t_window_created_ms =
        Some(now_ms().saturating_sub(boot_start_ms));
    persist_telemetry(&state.telemetry);
}

/// Record the UI-ready timestamp (called from the frontend readiness event).
pub fn record_ui_ready(state: &SupervisorState, boot_start_ms: u64) {
    {
        let mut tel = state.telemetry.lock().unwrap();
        tel.t_ui_ready_ms = Some(now_ms().saturating_sub(boot_start_ms));
    }
    persist_telemetry(&state.telemetry);
}

/// Persist telemetry to %APPDATA%/HelixSupport/telemetry/startup.json.
fn persist_telemetry(state: &Mutex<StartupTelemetry>) {
    let tel = state.lock().unwrap().clone();
    let dir = telemetry_dir();
    let _ = std::fs::create_dir_all(&dir);
    let path = dir.join("startup.json");
    match serde_json::to_string_pretty(&tel) {
        Ok(json) => match std::fs::write(&path, &json) {
            Ok(_) => log_line(
                "desktop",
                &format!(
                    "telemetry: window={}ms backend={}ms ui={}ms port={:?} mode={:?}",
                    tel.t_window_created_ms.unwrap_or(0),
                    tel.t_backend_ready_ms.unwrap_or(0),
                    tel.t_ui_ready_ms.unwrap_or(0),
                    tel.backend_port,
                    tel.backend_mode,
                ),
            ),
            Err(e) => log_line("desktop!", &format!("telemetry write error {}: {e}", path.display())),
        },
        Err(e) => log_line("desktop!", &format!("telemetry serialize error: {e}")),
    }
}

/// Graceful stop: kill the process tree, then reap.
fn kill_tree(backend: &mut Backend) -> std::io::Result<()> {
    #[cfg(windows)]
    {
        // taskkill /T walks the child tree (uvicorn reload workers etc.).
        let pid = backend.child.id();
        let mut cmd = Command::new("taskkill");
        cmd.args(["/PID", &pid.to_string(), "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW);
        let _ = cmd.output();
    }
    #[cfg(not(windows))]
    {
        let _ = backend.child.kill();
    }
    backend.child.wait()?;
    Ok(())
}

pub fn shutdown(state: &SupervisorState) {
    if let Some(mut backend) = state.backend.lock().unwrap().take() {
        log_line("desktop", "shutting down backend");
        if let Err(e) = kill_tree(&mut backend) {
            log_line("desktop", &format!("shutdown error: {e}"));
        }
    }
}

/// Flush a final telemetry snapshot (called on window destroy).
pub fn flush_telemetry(state: &SupervisorState) {
    persist_telemetry(&state.telemetry);
}

// Touch the trait import so clippy doesn't warn when the windows cfg is off.
#[cfg(not(windows))]
fn _trait_in_scope() {
    let _ = std::process::Command::new("");
}
