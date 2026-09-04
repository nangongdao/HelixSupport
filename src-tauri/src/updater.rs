// Desktop shell auto-update mechanism
//
// Implements update checking and installation using tauri-plugin-updater.
// Update flow:
//   1. Check for updates on startup (optional) or via menu/command
//   2. If available, prompt user with dialog showing version and changes
//   3. On user consent, download and install update
//   4. Restart app to apply update
//
// Security: Updates are signature-verified using the pubkey in tauri.conf.json.
// The signing certificate is an external dependency (see DEPLOYMENT_DESKTOP.md §5).

use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_updater::UpdaterExt;

/// Check for available updates and prompt user to install if found.
///
/// This function:
/// - Queries the update endpoint (GitHub Releases by default)
/// - Verifies signature using the configured pubkey
/// - Shows a dialog if an update is available
/// - Downloads and installs on user consent
///
/// Returns Ok(true) if update was performed, Ok(false) if no update or declined.
pub async fn check_and_prompt_update(app: &AppHandle) -> Result<bool, String> {
    perform_update_check(app).await
}

/// Perform the actual update check and installation flow with progress dialog.
///
/// Returns Ok(true) if update was installed, Ok(false) if no update or declined.
async fn perform_update_check(app: &AppHandle) -> Result<bool, String> {
    // Get the updater instance from the plugin
    let updater = app.updater_builder().build().map_err(|e| {
        format!("Failed to build updater: {}", e)
    })?;

    // Check for updates
    let update_response = updater.check().await.map_err(|e| {
        format!("Failed to check for updates: {}", e)
    })?;

    // If no update is available, return early
    let Some(update) = update_response else {
        // Show "no update available" dialog only for manual checks
        return Ok(false);
    };

    let version = update.version.clone();
    let current_version = update.current_version.clone();

    eprintln!(
        "[updater] Update available: {} -> {}",
        current_version, version
    );

    // Prompt user with update dialog using Tauri's dialog API
    let user_consent = app
        .dialog()
        .message(format!(
            "发现新版本 {}\n\n当前版本：{}\n\n是否立即下载并安装更新？\n\n更新将在下载完成后自动安装并重启应用。",
            version, current_version
        ))
        .title("Helix Support - 可用更新")
        .blocking_show();

    if !user_consent {
        eprintln!("[updater] User declined update");
        return Ok(false);
    }

    // Show downloading message (non-blocking information)
    eprintln!("[updater] Downloading update...");

    // Note: Tauri's dialog API doesn't support real-time progress dialogs in blocking mode,
    // so we rely on console output for progress tracking. Future enhancement could use
    // a custom window or notification system for better UX.

    // The download_and_install method handles the download, verification, and installation.
    // It will restart the app automatically after successful installation.
    update
        .download_and_install(
            |chunk_length, content_length| {
                // Progress callback - log to console
                if let Some(total) = content_length {
                    let percent = (chunk_length as f64 / total as f64) * 100.0;
                    // Only log at 10% intervals to reduce noise
                    if percent as u32 % 10 == 0 || percent >= 99.0 {
                        eprintln!("[updater] Download progress: {:.1}%", percent);
                    }
                }
            },
            || {
                // Installation complete callback
                eprintln!("[updater] Installation complete, restarting...");
            },
        )
        .await
        .map_err(|e| format!("Failed to download and install update: {}", e))?;

    Ok(true)
}

/// Check for updates on app startup (optional, controlled by user preference).
///
/// This is a non-blocking background check. If disabled in settings,
/// users can still manually check via menu or command.
pub fn check_on_startup(app: &AppHandle) {
    // TODO: Read user preference from settings
    // For now, check on every startup (can be disabled later)
    let should_check = true;

    if should_check {
        let app_handle = app.clone();
        tauri::async_runtime::spawn(async move {
            match check_and_prompt_update(&app_handle).await {
                Ok(update_performed) => {
                    if update_performed {
                        eprintln!("[updater] Startup update installed successfully");
                    } else {
                        eprintln!("[updater] No startup update available or user declined");
                    }
                }
                Err(e) => {
                    eprintln!("[updater] Startup update check failed: {}", e);
                    // Non-fatal: don't block app usage if update check fails
                }
            }
        });
    }
}
