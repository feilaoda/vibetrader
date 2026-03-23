#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{
    image::Image,
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    ActivationPolicy, App, AppHandle, Manager, WindowEvent,
};
use tauri_plugin_positioner::{Position, WindowExt};

const PANEL_LABEL: &str = "main";
const TRAY_ID: &str = "traderbar";

fn build_tray_icon() -> Image<'static> {
    let width = 18usize;
    let height = 18usize;
    let mut rgba = vec![0u8; width * height * 4];

    for y in 0..height {
        for x in 0..width {
            let idx = (y * width + x) * 4;
            let active = ((4..=6).contains(&x) && (5..=13).contains(&y))
                || ((8..=10).contains(&x) && (3..=13).contains(&y))
                || ((12..=14).contains(&x) && (7..=13).contains(&y))
                || ((3..=15).contains(&x) && y == 14);

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

fn show_panel(app: &AppHandle) {
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

fn toggle_panel(app: &AppHandle) {
    if let Some(window) = panel_window(app) {
        let is_visible = window.is_visible().unwrap_or(false);
        if is_visible {
            let _ = window.hide();
        } else {
            show_panel(app);
        }
    }
}

fn build_tray(app: &App) -> tauri::Result<()> {
    let quit = MenuItem::with_id(app, "quit", "Quit TraderBar", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&quit])?;

    let mut tray_builder = TrayIconBuilder::with_id(TRAY_ID)
        .icon(build_tray_icon())
        .title("TB")
        .tooltip("TraderBar")
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

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_positioner::init())
        .setup(|app| {
            #[cfg(target_os = "macos")]
            {
                let _ = app.set_activation_policy(ActivationPolicy::Accessory);
                let _ = app.set_dock_visibility(false);
            }

            build_tray(app)?;
            hide_panel_internal(&app.handle());
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() != PANEL_LABEL {
                return;
            }
            if let WindowEvent::Focused(false) = event {
                let _ = window.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![set_tray_title, hide_panel])
        .run(tauri::generate_context!())
        .expect("error while running TraderBar");
}
