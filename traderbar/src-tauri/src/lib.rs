use std::{fs, path::PathBuf, sync::Mutex};

use tauri::{
    image::Image,
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    ActivationPolicy, App, AppHandle, Emitter, Manager, WindowEvent,
};
use tauri_plugin_positioner::{Position, WindowExt};

const PANEL_LABEL: &str = "main";
const SETTINGS_LABEL: &str = "settings";
const TRAY_ID: &str = "traderbar";
const SETTINGS_UPDATED_EVENT: &str = "traderbar://settings-updated";
const PANEL_SHOWN_EVENT: &str = "traderbar://panel-shown";
const SETTINGS_FILE_NAME: &str = "settings.json";

#[derive(Default)]
struct PanelReloadState {
    pending_show: Mutex<bool>,
}

fn build_tray_icon() -> Image<'static> {
    let width = 18usize;
    let height = 18usize;
    let mut rgba = vec![0u8; width * height * 4];

    for y in 0..height {
        for x in 0..width {
            let idx = (y * width + x) * 4;
            let active = ((7..=10).contains(&x) && (4..=13).contains(&y))
                || ((5..=12).contains(&x) && (7..=10).contains(&y));

            if active {
                rgba[idx] = 0;
                rgba[idx + 1] = 0;
                rgba[idx + 2] = 0;
                rgba[idx + 3] = 255;
            }
        }
    }

    Image::new_owned(rgba, width as u32, height as u32)
}

fn panel_window(app: &AppHandle) -> Option<tauri::WebviewWindow> {
    app.get_webview_window(PANEL_LABEL)
}

fn settings_window(app: &AppHandle) -> Option<tauri::WebviewWindow> {
    app.get_webview_window(SETTINGS_LABEL)
}

fn show_panel_internal(app: &AppHandle) {
    if let Some(window) = panel_window(app) {
        let _ = window.as_ref().window().move_window(Position::TrayCenter);
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn hide_panel_internal(app: &AppHandle) {
    if let Some(window) = panel_window(app) {
        let _ = window.hide();
    }
}

fn show_settings_internal(app: &AppHandle) {
    if let Some(window) = settings_window(app) {
        let _ = window.center();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn hide_settings_internal(app: &AppHandle) {
    if let Some(window) = settings_window(app) {
        let _ = window.hide();
    }
}

fn settings_file_path(app: &AppHandle) -> Result<PathBuf, String> {
    let mut dir = app.path().app_config_dir().map_err(|err| err.to_string())?;
    fs::create_dir_all(&dir).map_err(|err| err.to_string())?;
    dir.push(SETTINGS_FILE_NAME);
    Ok(dir)
}

fn load_saved_settings(app: &AppHandle) -> Option<String> {
    let path = settings_file_path(app).ok()?;
    fs::read_to_string(path).ok()
}

fn save_saved_settings(app: &AppHandle, settings_json: &str) -> Result<(), String> {
    let path = settings_file_path(app)?;
    fs::write(path, settings_json).map_err(|err| err.to_string())
}

fn emit_saved_settings(app: &AppHandle) {
    if let Some(settings_json) = load_saved_settings(app) {
        let _ = app.emit_to(PANEL_LABEL, SETTINGS_UPDATED_EVENT, settings_json);
    }
}

fn emit_panel_shown(app: &AppHandle) {
    let _ = app.emit_to(PANEL_LABEL, PANEL_SHOWN_EVENT, "");
}

fn toggle_panel(app: &AppHandle) {
    if let Some(window) = panel_window(app) {
        let is_visible = window.is_visible().unwrap_or(false);
        if is_visible {
            let _ = window.hide();
        } else {
            show_panel_internal(app);
            emit_saved_settings(app);
            emit_panel_shown(app);
        }
    }
}

fn build_tray(app: &App) -> tauri::Result<()> {
    let quit = MenuItem::with_id(app, "quit", "Quit TraderBar", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&quit])?;

    let mut tray_builder = TrayIconBuilder::with_id(TRAY_ID)
        .icon(build_tray_icon())
        .title("Desk")
        .tooltip("Workspace")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| {
            if event.id.as_ref() == "quit" {
                app.exit(0);
            }
        })
        .on_tray_icon_event(|tray, event| {
            tauri_plugin_positioner::on_tray_event(tray.app_handle(), &event);
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                toggle_panel(&tray.app_handle());
            }
        });

    #[cfg(target_os = "macos")]
    {
        tray_builder = tray_builder.icon_as_template(true);
    }

    let _ = tray_builder.build(app)?;
    Ok(())
}

#[tauri::command]
fn set_tray_title(app: AppHandle, title: String) -> Result<(), String> {
    let tray = app
        .tray_by_id(TRAY_ID)
        .ok_or_else(|| "tray not ready".to_string())?;
    tray.set_title(Some(title.as_str()))
        .map_err(|err| err.to_string())
}

#[tauri::command]
fn hide_panel(app: AppHandle) {
    hide_panel_internal(&app);
}

#[tauri::command]
fn show_panel(app: AppHandle) {
    hide_settings_internal(&app);
    show_panel_internal(&app);
    emit_saved_settings(&app);
    emit_panel_shown(&app);
}

#[tauri::command]
fn show_settings(app: AppHandle) {
    hide_panel_internal(&app);
    show_settings_internal(&app);
}

#[tauri::command]
fn hide_settings(app: AppHandle) {
    hide_settings_internal(&app);
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

#[tauri::command]
fn show_panel_with_settings(app: AppHandle, settings_json: String) -> Result<(), String> {
    save_saved_settings(&app, &settings_json)?;
    hide_settings_internal(&app);
    show_panel_internal(&app);
    app.emit_to(PANEL_LABEL, SETTINGS_UPDATED_EVENT, settings_json)
        .map_err(|err| err.to_string())?;
    emit_panel_shown(&app);
    Ok(())
}

#[tauri::command]
fn reload_panel_with_settings(
    app: AppHandle,
    state: tauri::State<PanelReloadState>,
    settings_json: String,
) -> Result<(), String> {
    save_saved_settings(&app, &settings_json)?;
    if let Some(window) = panel_window(&app) {
        if let Ok(mut pending) = state.pending_show.lock() {
            *pending = true;
        }
        let _ = window.hide();
        let _ = window.eval("window.location.reload()");
    } else {
        hide_settings_internal(&app);
        show_panel_internal(&app);
        emit_panel_shown(&app);
    }
    Ok(())
}

#[tauri::command]
fn save_settings_json(app: AppHandle, settings_json: String) -> Result<(), String> {
    save_saved_settings(&app, &settings_json)
}

#[tauri::command]
fn load_settings_json(app: AppHandle) -> Option<String> {
    load_saved_settings(&app)
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_positioner::init())
        .manage(PanelReloadState::default())
        .setup(|app| {
            #[cfg(target_os = "macos")]
            {
                let _ = app.set_activation_policy(ActivationPolicy::Accessory);
                let _ = app.set_dock_visibility(false);
            }

            build_tray(app)?;
            hide_panel_internal(&app.handle());
            hide_settings_internal(&app.handle());
            Ok(())
        })
        .on_page_load(|window, _| {
            if window.label() != PANEL_LABEL {
                return;
            }
            let app = window.app_handle();
            let Some(state) = app.try_state::<PanelReloadState>() else {
                return;
            };
            let should_show = match state.pending_show.lock() {
                Ok(mut pending) => {
                    if !*pending {
                        false
                    } else {
                        *pending = false;
                        true
                    }
                }
                Err(_) => false,
            };
            if should_show {
                hide_settings_internal(&app);
                show_panel_internal(&app);
                emit_panel_shown(&app);
            }
        })
        .on_window_event(|window, event| {
            match (window.label(), event) {
                (PANEL_LABEL, WindowEvent::Focused(false)) => {
                    let _ = window.hide();
                }
                (SETTINGS_LABEL, WindowEvent::CloseRequested { api, .. }) => {
                    api.prevent_close();
                    let _ = window.hide();
                }
                _ => {}
            }
        })
        .invoke_handler(tauri::generate_handler![
            set_tray_title,
            hide_panel,
            show_panel,
            show_settings,
            hide_settings,
            quit_app,
            show_panel_with_settings,
            reload_panel_with_settings,
            save_settings_json,
            load_settings_json
        ])
        .run(tauri::generate_context!())
        .expect("error while running TraderBar");
}
