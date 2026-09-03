// Desktop application menu bar
//
// Provides native menu items for common operations:
// - File: Quit
// - Help: Check for Updates, About
//
// Menu actions are dispatched via Tauri commands.

use tauri::{AppHandle, menu::{MenuBuilder, MenuItemBuilder}};
use tauri_plugin_dialog::DialogExt;

/// Build the application menu bar
pub fn build_menu(app: &AppHandle) -> Result<tauri::menu::Menu<tauri::Wry>, tauri::Error> {
    // File menu
    let quit = MenuItemBuilder::with_id("quit", "退出")
        .accelerator("CommandOrControl+Q")
        .build(app)?;

    let file_menu = tauri::menu::SubmenuBuilder::new(app, "文件")
        .item(&quit)
        .build()?;

    // Help menu
    let check_update = MenuItemBuilder::with_id("check_update", "检查更新...")
        .build(app)?;

    let about = MenuItemBuilder::with_id("about", "关于 Helix Support")
        .build(app)?;

    let help_menu = tauri::menu::SubmenuBuilder::new(app, "帮助")
        .item(&check_update)
        .separator()
        .item(&about)
        .build()?;

    // Build the complete menu
    let menu = MenuBuilder::new(app)
        .item(&file_menu)
        .item(&help_menu)
        .build()?;

    Ok(menu)
}

/// Handle menu events
pub async fn handle_menu_event(app: &AppHandle, event_id: &str) {
    match event_id {
        "quit" => {
            app.exit(0);
        }
        "check_update" => {
            // Trigger manual update check
            match crate::updater::check_and_prompt_update(app).await {
                Ok(updated) => {
                    if !updated {
                        // No update available - show info dialog
                        let dialog = app.dialog();
                        dialog
                            .message("当前已是最新版本。")
                            .title("检查更新")
                            .blocking_show();
                    }
                    // If updated=true, the app will restart automatically
                }
                Err(e) => {
                    eprintln!("[menu] Manual update check failed: {}", e);

                    // Show error dialog with more helpful message
                    let dialog = app.dialog();
                    dialog
                        .message(format!(
                            "检查更新失败。\n\n请确认网络连接正常，或稍后重试。\n\n错误详情：{}",
                            e
                        ))
                        .title("更新检查失败")
                        .blocking_show();
                }
            }
        }
        "about" => {
            // Show about dialog
            let version = app.package_info().version.to_string();
            let dialog = app.dialog();
            dialog
                .message(format!(
                    "Helix Support v{}\n\n可本地运行、租户隔离、关键操作可审计的多 Agent 智能客服平台",
                    version
                ))
                .title("关于 Helix Support")
                .blocking_show();
        }
        _ => {
            eprintln!("[menu] Unknown menu event: {}", event_id);
        }
    }
}
