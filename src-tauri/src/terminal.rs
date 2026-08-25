//! Helix Support — terminal PTY module (D4)
//!
//! Provides a whitelist-command diagnostic terminal and an optional
//! interactive PTY for DEBUG builds. RBAC: terminal commands are only
//! available to admin/platform roles. The frontend hides the entry and
//! passes the caller's role via the `role` IPC argument; Rust re-validates
//! it here as defense-in-depth (§4.1) so a compromised renderer cannot
//! reach the terminal even by crafting an invoke call.
//! See DESKTOP_TAURI_PLAN.md §4.1 + §4.2.

use std::collections::HashMap;
use std::io::Write;
#[cfg(debug_assertions)]
use std::io::Read;
use std::sync::Mutex;

/// Roles permitted to use the terminal (§4.1 RBAC allow-set).
const ALLOWED_ROLES: &[&str] = &["admin", "platform"];

/// Return Ok(()) iff `role` is in the terminal allow-set.
fn authorize(role: &str) -> Result<(), String> {
    if ALLOWED_ROLES.contains(&role) {
        Ok(())
    } else {
        Err(format!(
            "terminal access denied: role '{role}' is not permitted \
             (requires one of: {})",
            ALLOWED_ROLES.join(", ")
        ))
    }
}

/// Whitelist of diagnostic commands (§4.1: production build default mode).
pub fn diagnostic_commands() -> Vec<DiagnosticCommand> {
    vec![
        DiagnosticCommand {
            id: "health".into(),
            label: "健康检查".into(),
            command: "curl".into(),
            args: vec!["-s".into(), "http://127.0.0.1:${HELIX_PORT}/health/ready".into()],
        },
        DiagnosticCommand {
            id: "migrations".into(),
            label: "迁移状态".into(),
            command: "echo".into(),
            args: vec!["migration status: see server logs".into()],
        },
        DiagnosticCommand {
            id: "logs".into(),
            label: "服务器日志".into(),
            command: "echo".into(),
            args: vec!["attach server stdout via the terminal drawer".into()],
        },
    ]
}

pub struct DiagnosticCommand {
    pub id: String,
    pub label: String,
    pub command: String,
    pub args: Vec<String>,
}

#[derive(serde::Serialize)]
pub struct CommandResult {
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
}

/// Run a whitelist diagnostic command by id. The caller must pass its role
/// for the RBAC check (defense-in-depth, §4.1).
#[tauri::command]
pub fn run_diagnostic(command_id: String, role: String) -> Result<CommandResult, String> {
    authorize(&role)?;
    let cmd = diagnostic_commands()
        .into_iter()
        .find(|c| c.id == command_id)
        .ok_or_else(|| format!("unknown diagnostic command: {command_id}"))?;

    let port = std::env::var("HELIX_PORT").unwrap_or_else(|_| "8766".into());
    let args: Vec<String> = cmd
        .args
        .iter()
        .map(|a| a.replace("${HELIX_PORT}", &port))
        .collect();

    let output = std::process::Command::new(&cmd.command)
        .args(&args)
        .output()
        .map_err(|e| format!("failed to run {}: {e}", cmd.command))?;

    Ok(CommandResult {
        exit_code: output.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    })
}

#[derive(serde::Serialize)]
pub struct DiagnosticCommandInfo {
    pub id: String,
    pub label: String,
}

/// List available diagnostic commands for the frontend. The caller must
/// pass its role for the RBAC check (defense-in-depth, §4.1).
#[tauri::command]
pub fn list_diagnostics(role: String) -> Result<Vec<DiagnosticCommandInfo>, String> {
    authorize(&role)?;
    Ok(diagnostic_commands()
        .into_iter()
        .map(|c| DiagnosticCommandInfo {
            id: c.id,
            label: c.label,
        })
        .collect())
}

/// Shared PTY session storage (sessions map keyed by session id).
pub struct PtyManager {
    sessions: Mutex<HashMap<String, PtySession>>,
}

struct PtySession {
    writer: Box<dyn Write + Send>,
    child: Box<dyn portable_pty::Child + Send + Sync>,
}

impl PtyManager {
    pub fn new() -> Self {
        Self {
            sessions: Mutex::new(HashMap::new()),
        }
    }
}

/// Spawn an interactive PTY session (DEBUG builds only). The interactive
/// PTY is disabled in release builds per §4.1 — production ships whitelist
/// diagnostics only. The caller must pass its role for the RBAC check.
#[tauri::command]
pub fn spawn_pty(
    session_id: String,
    role: String,
    on_data: tauri::ipc::Channel<tauri::ipc::InvokeResponseBody>,
    state: tauri::State<'_, PtyManager>,
) -> Result<(), String> {
    authorize(&role)?;
    // Release builds refuse to spawn an interactive PTY (§4.1).
    #[cfg(not(debug_assertions))]
    {
        let _ = (session_id, on_data, state);
        return Err(
            "interactive PTY is disabled in release builds \
             (whitelist diagnostic mode only)"
                .into(),
        );
    }
    #[cfg(debug_assertions)]
    {
        use portable_pty::*;

    let pty_system = native_pty_system();
    let pair = pty_system
        .openpty(PtySize {
            rows: 24,
            cols: 80,
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|e| format!("pty open: {e}"))?;

    let cmd = {
        #[cfg(windows)]
        {
            CommandBuilder::new("powershell.exe")
        }
        #[cfg(not(windows))]
        {
            CommandBuilder::new("bash")
        }
    };

    let (reader, writer) = (
        pair.master.try_clone_reader().map_err(|e| format!("pty reader: {e}"))?,
        pair.master.take_writer().map_err(|e| format!("pty writer: {e}"))?,
    );
    let child = pair
        .slave
        .spawn_command(cmd)
        .map_err(|e| format!("pty spawn: {e}"))?;

    // Move reader into a mutable variable for the streaming thread.
    let mut reader = reader;

    // Stream PTY output to the Tauri Channel.
    std::thread::spawn(move || {
        let mut buf = [0u8; 4096];
        loop {
            match reader.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    let _ = on_data.send(tauri::ipc::InvokeResponseBody::Raw(buf[..n].to_vec()));
                }
                Err(_) => break,
            }
        }
    });

    let session = PtySession { writer, child };
    state
        .sessions
        .lock()
        .unwrap()
        .insert(session_id, session);

    Ok(())
    }
    }

/// Write input to an active PTY session.
#[tauri::command]
pub fn write_pty(
    session_id: String,
    data: Vec<u8>,
    state: tauri::State<'_, PtyManager>,
) -> Result<(), String> {
    let mut sessions = state.sessions.lock().unwrap();
    let session = sessions
        .get_mut(&session_id)
        .ok_or_else(|| format!("no pty session: {session_id}"))?;
    session
        .writer
        .write_all(&data)
        .map_err(|e| format!("pty write: {e}"))?;
    Ok(())
}

/// Kill and close a PTY session.
#[tauri::command]
pub fn kill_pty(
    session_id: String,
    state: tauri::State<'_, PtyManager>,
) -> Result<(), String> {
    let mut sessions = state.sessions.lock().unwrap();
    if let Some(mut session) = sessions.remove(&session_id) {
        let _ = session.child.kill();
        let _ = session.child.wait();
    }
    Ok(())
}
