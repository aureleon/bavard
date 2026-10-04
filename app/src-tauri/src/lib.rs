mod engine;
mod settings;
mod setup;

use engine::{Engine, Launch};
use serde_json::{json, Value};
use settings::Settings;
use setup::Paths;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State, WebviewWindow, WindowEvent};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

struct AppState {
    engine: Arc<Engine>,
    settings: Mutex<Settings>,
    settings_path: PathBuf,
    paths: Paths,
    /// Serializes start/restart so two setups never run at once.
    starting: Mutex<()>,
}

fn start_engine(app: &AppHandle) {
    let app = app.clone();
    std::thread::spawn(move || {
        let state = app.state::<AppState>();
        let _guard = state.starting.lock().unwrap();
        let result = (|| -> Result<(), String> {
            state.paths.ensure_prompts()?;
            let python = setup::ensure_python(&app, &state.paths)?;
            let settings = state.settings.lock().unwrap().clone();
            let mut args = settings.engine_args();
            args.push("--tutor-prompt".into());
            args.push(state.paths.prompts_dir.join("tutor.txt").display().to_string());
            args.push("--hear-prompt".into());
            args.push(state.paths.prompts_dir.join("hear.txt").display().to_string());
            state.engine.spawn(
                &app,
                Launch {
                    python,
                    engine_dir: state.paths.engine_dir.clone(),
                    args,
                    log_path: state.paths.log_path.clone(),
                },
            )
        })();
        if let Err(message) = result {
            let _ = app.emit("engine-exit", json!({ "code": null, "tail": message, "setup": true }));
        }
    });
}

// -- window -------------------------------------------------------------------

fn main_window(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window("main")
}

fn hide(app: &AppHandle) {
    if let Some(w) = main_window(app) {
        let _ = w.hide();
    }
    // Hands-free modes: close the mic while the window is away.
    let _ = app.state::<AppState>().engine.send(&json!({ "cmd": "pause" }));
}

fn show(app: &AppHandle) {
    if let Some(w) = main_window(app) {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
    let _ = app.state::<AppState>().engine.send(&json!({ "cmd": "resume" }));
}

fn toggle(app: &AppHandle) {
    let visible = main_window(app)
        .map(|w| w.is_visible().unwrap_or(false) && w.is_focused().unwrap_or(false))
        .unwrap_or(false);
    if visible {
        hide(app)
    } else {
        show(app)
    }
}

// -- commands -----------------------------------------------------------------

#[tauri::command]
fn get_settings(state: State<AppState>) -> Settings {
    state.settings.lock().unwrap().clone()
}

/// Save settings. Returns true when the engine must restart to apply them.
#[tauri::command]
fn save_settings(settings: Settings, state: State<AppState>) -> Result<bool, String> {
    let settings = settings.sanitized();
    let mut current = state.settings.lock().unwrap();
    let restart = current.needs_restart(&settings);
    settings.save(&state.settings_path)?;
    *current = settings;
    Ok(restart)
}

#[tauri::command]
fn engine_start(app: AppHandle, state: State<AppState>) {
    if !state.engine.is_running() {
        start_engine(&app);
    }
}

#[tauri::command]
fn engine_restart(app: AppHandle, state: State<AppState>) {
    state.engine.stop();
    start_engine(&app);
}

#[tauri::command]
fn engine_send(msg: Value, state: State<AppState>) -> Result<(), String> {
    state.engine.send(&msg)
}

#[tauri::command]
fn engine_info(state: State<AppState>) -> Paths {
    state.paths.clone()
}

#[tauri::command]
fn open_prompts(state: State<AppState>) -> Result<(), String> {
    state.paths.ensure_prompts()?;
    std::process::Command::new("open")
        .arg(&state.paths.prompts_dir)
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn open_log(state: State<AppState>) -> Result<(), String> {
    std::process::Command::new("open")
        .args(["-R"])
        .arg(&state.paths.log_path)
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn hide_window(app: AppHandle) {
    hide(&app);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let summon = Shortcut::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::Space);

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, shortcut, event| {
                    if shortcut == &summon && event.state() == ShortcutState::Pressed {
                        toggle(app);
                    }
                })
                .build(),
        )
        .setup(move |app| {
            let config_dir = app.path().app_config_dir()?;
            let settings_path = config_dir.join("settings.json");
            let paths = Paths::resolve(app.handle()).map_err(std::io::Error::other)?;
            app.manage(AppState {
                engine: Arc::new(Engine::default()),
                settings: Mutex::new(Settings::load(&settings_path)),
                settings_path,
                paths,
                starting: Mutex::new(()),
            });
            if let Err(e) = app.global_shortcut().register(summon) {
                eprintln!("cannot register Cmd+Shift+Space: {e}");
            }
            Ok(())
        })
        // The red traffic light hides the HUD (summon it again with
        // Cmd+Shift+Space); Cmd+Q quits and frees the model memory.
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                hide(window.app_handle());
            }
        })
        .invoke_handler(tauri::generate_handler![
            get_settings,
            save_settings,
            engine_start,
            engine_restart,
            engine_send,
            engine_info,
            open_prompts,
            open_log,
            hide_window
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app, event| match event {
        // Cmd+Q, or the last window closed: free the models' memory now.
        RunEvent::ExitRequested { .. } | RunEvent::Exit => {
            if let Some(state) = app.try_state::<AppState>() {
                state.engine.stop();
            }
        }
        // Dock icon clicked while hidden.
        RunEvent::Reopen { .. } => show(app),
        _ => {}
    });
}
