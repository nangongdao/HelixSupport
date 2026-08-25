mod supervisor;
mod terminal;

use std::time::{SystemTime, UNIX_EPOCH};
use supervisor::{shutdown, start_backend, ensure_alive, flush_telemetry,
    record_window_created, record_ui_ready, SupervisorState, StartupTelemetry};
use tauri::{Manager, WindowEvent};
use tauri_plugin_dialog::DialogExt;

struct AppState {
    supervisor: SupervisorState,
    /// Process-origin timestamp (ms since unix epoch) for relative telemetry deltas.
    boot_start_ms: u64,
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

#[tauri::command]
fn ui_ready(state: tauri::State<'_, AppState>) {
    record_ui_ready(&state.supervisor, state.boot_start_ms);
}

/// Expose the assigned backend port + data dir to the frontend for the
/// settings page (version/DB path/port display, §D1).
#[tauri::command]
fn desktop_info(state: tauri::State<'_, AppState>) -> serde_json::Value {
    let tel = state.supervisor.telemetry.lock().unwrap().clone();
    serde_json::json!({
        "port": tel.backend_port,
        "mode": tel.backend_mode,
        "dataDir": supervisor::telemetry_dir()
            .parent()
            .map(|p| p.display().to_string())
            .unwrap_or_default(),
    })
}

pub fn run() {
    let boot_start_ms = now_ms();
    let state = AppState {
        supervisor: SupervisorState {
            backend: std::sync::Mutex::new(None),
            restarts: std::sync::atomic::AtomicU32::new(0),
            telemetry: std::sync::Mutex::new(StartupTelemetry::default()),
        },
        boot_start_ms,
    };

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        // Single-instance lock: a second launch focuses the existing window
        // instead of starting a second backend.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.show();
                let _ = win.set_focus();
            }
        }))
        .manage(state)
        .manage(terminal::PtyManager::new())
        .invoke_handler(tauri::generate_handler![
            ui_ready,
            desktop_info,
            terminal::list_diagnostics,
            terminal::run_diagnostic,
            terminal::spawn_pty,
            terminal::write_pty,
            terminal::kill_pty
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            record_window_created(
                &handle.state::<AppState>().supervisor,
                handle.state::<AppState>().boot_start_ms,
            );

            // Spawn the backend in a background thread so the window paints
            // immediately (splash-first cold start).
            let boot = handle.state::<AppState>().boot_start_ms;
            let h = handle.clone();
            std::thread::spawn(move || {
                let app_state = h.state::<AppState>();
                match start_backend(&app_state.supervisor, Some(&h), boot) {
                    Ok(port) => {
                        if let Some(win) = h.get_webview_window("main") {
                            let _ = win.eval(format!(
                                "window.__HELIX_BACKEND__ = {{ port: {port}, readyAt: performance.now() }};\
                                 window.dispatchEvent(new Event('helix-backend-ready'));"
                            ));
                        }
                    }
                    Err(e) => {
                        eprintln!("[desktop] backend failed: {e}");
                        if let Some(win) = h.get_webview_window("main") {
                            let _ = win.eval(format!(
                                "window.__HELIX_BACKEND__ = {{ error: {:?} }};\
                                 window.dispatchEvent(new Event('helix-backend-ready'));",
                                e
                            ));
                        }
                        // Surface a native dialog so the user knows the
                        // sidecar failed to start.
                        let msg = e.clone();
                        let h2 = h.clone();
                        std::thread::spawn(move || {
                            h2.dialog().message(&msg).show(|_| {});
                        });
                    }
                }
            });

            // Background watchdog: poll backend liveness every 5s and
            // auto-restart within the crash budget.
            let watch_handle = app.handle().clone();
            std::thread::spawn(move || loop {
                std::thread::sleep(std::time::Duration::from_secs(5));
                let app_state = watch_handle.state::<AppState>();
                if let Err(msg) = ensure_alive(
                    &app_state.supervisor,
                    Some(&watch_handle),
                    app_state.boot_start_ms,
                ) {
                    // Budget exhausted — surface a dialog once.
                    let h2 = watch_handle.clone();
                    std::thread::spawn(move || {
                        h2.dialog().message(&msg).show(|_| {});
                    });
                    break;
                }
            });

            eprintln!(
                "[desktop] window created in {:?}",
                std::time::Duration::from_millis(
                    now_ms().saturating_sub(handle.state::<AppState>().boot_start_ms)
                )
            );
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::Destroyed) {
                let state = window.app_handle().state::<AppState>();
                flush_telemetry(&state.supervisor);
                shutdown(&state.supervisor);
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running helix desktop");
}
