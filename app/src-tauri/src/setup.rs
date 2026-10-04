//! Where the engine lives, and the Python environment that runs it.
//!
//! Development (debug build run from the repo): the engine is the repo root
//! and its `.venv` (made by `run.sh`) is used as is.
//! Release: the engine files are bundled as resources (`engine/`), and a
//! managed venv is created in the app data directory on first launch.

use serde::Serialize;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use tauri::{AppHandle, Emitter, Manager};

#[derive(Clone, Debug, Serialize)]
pub struct Paths {
    pub engine_dir: PathBuf,
    pub venv_dir: PathBuf,
    pub prompts_dir: PathBuf,
    pub log_path: PathBuf,
    pub dev: bool,
}

impl Paths {
    pub fn resolve(app: &AppHandle) -> Result<Paths, String> {
        let data = app.path().app_data_dir().map_err(|e| e.to_string())?;
        let logs = app.path().app_log_dir().map_err(|e| e.to_string())?;
        let log_path = logs.join("engine.log");

        if let Ok(dir) = std::env::var("BAVARD_ENGINE_DIR") {
            let dir = PathBuf::from(dir);
            return Ok(Paths {
                venv_dir: dir.join(".venv"),
                prompts_dir: dir.join("prompts"),
                engine_dir: dir,
                log_path,
                dev: true,
            });
        }

        let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        if cfg!(debug_assertions) && repo.join("serve.py").exists() {
            let repo = repo.canonicalize().map_err(|e| e.to_string())?;
            return Ok(Paths {
                venv_dir: repo.join(".venv"),
                prompts_dir: repo.join("prompts"),
                engine_dir: repo,
                log_path,
                dev: true,
            });
        }

        let engine_dir = app
            .path()
            .resource_dir()
            .map_err(|e| e.to_string())?
            .join("engine");
        Ok(Paths {
            engine_dir,
            venv_dir: data.join("venv"),
            prompts_dir: data.join("prompts"),
            log_path,
            dev: false,
        })
    }

    pub fn venv_python(&self) -> PathBuf {
        self.venv_dir.join("bin/python")
    }

    /// Release builds: copy the bundled prompts to the data dir once, so the
    /// learner can edit them.
    pub fn ensure_prompts(&self) -> Result<(), String> {
        if self.dev {
            return Ok(());
        }
        std::fs::create_dir_all(&self.prompts_dir).map_err(|e| e.to_string())?;
        for name in ["tutor.txt", "hear.txt"] {
            let dst = self.prompts_dir.join(name);
            if !dst.exists() {
                std::fs::copy(self.engine_dir.join("prompts").join(name), &dst)
                    .map_err(|e| format!("copy {name}: {e}"))?;
            }
        }
        Ok(())
    }
}

#[derive(Clone, Serialize)]
struct SetupEvent<'a> {
    stage: &'a str,
    line: Option<String>,
}

fn emit(app: &AppHandle, stage: &str, line: Option<String>) {
    let _ = app.emit("setup", SetupEvent { stage, line });
}

fn python_version(python: &Path) -> Option<(u32, u32)> {
    let out = Command::new(python)
        .args(["-c", "import sys; print('%d.%d' % sys.version_info[:2])"])
        .stdin(Stdio::null())
        .output()
        .ok()?;
    let text = String::from_utf8_lossy(&out.stdout);
    let (maj, min) = text.trim().split_once('.')?;
    Some((maj.parse().ok()?, min.parse().ok()?))
}

/// Find a Python 3.12+ interpreter. GUI apps get a minimal PATH, so look in
/// the usual install locations and ask a login shell as a last resort.
fn find_base_python() -> Option<PathBuf> {
    let home = std::env::var("HOME").unwrap_or_default();
    let mut candidates: Vec<PathBuf> = Vec::new();
    for v in ["3.13", "3.12", "3.14"] {
        candidates.push(format!("/opt/homebrew/bin/python{v}").into());
        candidates.push(format!("/usr/local/bin/python{v}").into());
        candidates.push(format!("/Library/Frameworks/Python.framework/Versions/{v}/bin/python3").into());
    }
    for root in [
        format!("{home}/.local/share/mise/installs/python"),
        format!("{home}/.pyenv/versions"),
    ] {
        if let Ok(entries) = std::fs::read_dir(&root) {
            let mut dirs: Vec<PathBuf> = entries.flatten().map(|e| e.path()).collect();
            dirs.sort();
            dirs.reverse();
            candidates.extend(dirs.into_iter().map(|d| d.join("bin/python3")));
        }
    }
    candidates.push("/opt/homebrew/bin/python3".into());
    candidates.push("/usr/local/bin/python3".into());
    if let Ok(out) = Command::new("/bin/zsh")
        .args(["-lc", "command -v python3.13 python3.12 python3"])
        .stdin(Stdio::null())
        .output()
    {
        for line in String::from_utf8_lossy(&out.stdout).lines() {
            candidates.push(line.trim().into());
        }
    }
    candidates
        .into_iter()
        .filter(|p| p.exists())
        .find(|p| matches!(python_version(p), Some((3, m)) if m >= 12))
}

fn run_logged(app: &AppHandle, stage: &str, cmd: &mut Command) -> Result<(), String> {
    let mut child = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("{stage}: {e}"))?;
    let stderr = child.stderr.take().unwrap();
    let app2 = app.clone();
    let stage2 = stage.to_string();
    let err_thread = std::thread::spawn(move || {
        let mut tail = Vec::new();
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            emit(&app2, &stage2, Some(line.clone()));
            tail.push(line);
            if tail.len() > 20 {
                tail.remove(0);
            }
        }
        tail
    });
    for line in BufReader::new(child.stdout.take().unwrap()).lines().map_while(Result::ok) {
        emit(app, stage, Some(line));
    }
    let status = child.wait().map_err(|e| e.to_string())?;
    let tail = err_thread.join().unwrap_or_default();
    if status.success() {
        Ok(())
    } else {
        Err(format!("{stage} failed ({status}):\n{}", tail.join("\n")))
    }
}

/// Return the interpreter that runs the engine, creating and filling the
/// managed venv when needed. Emits `setup` events while it works.
pub fn ensure_python(app: &AppHandle, paths: &Paths) -> Result<PathBuf, String> {
    if let Ok(p) = std::env::var("BAVARD_PYTHON") {
        return Ok(PathBuf::from(p));
    }
    let python = paths.venv_python();
    let requirements = paths.engine_dir.join("requirements.txt");
    let wanted = std::fs::read_to_string(&requirements)
        .map_err(|e| format!("read {}: {e}", requirements.display()))?;

    if paths.dev {
        // The repo venv is managed by run.sh.
        if python.exists() {
            return Ok(python);
        }
        return Err(format!(
            "No venv at {}. Run ./run.sh once in the repo to create it.",
            paths.venv_dir.display()
        ));
    }

    let marker = paths.venv_dir.join(".bavard-requirements");
    if python.exists() && std::fs::read_to_string(&marker).ok().as_deref() == Some(wanted.as_str()) {
        return Ok(python);
    }

    if !python.exists() {
        emit(app, "find_python", None);
        let base = find_base_python().ok_or(
            "Python 3.12 or newer was not found. Install it (for example `brew install python@3.12`) and restart Bavard.",
        )?;
        emit(app, "venv", Some(base.display().to_string()));
        run_logged(
            app,
            "venv",
            Command::new(&base).args(["-m", "venv", "--prompt", "bavard"]).arg(&paths.venv_dir),
        )?;
        run_logged(
            app,
            "pip",
            Command::new(&python).args(["-m", "pip", "install", "--upgrade", "pip"]),
        )?;
    }
    emit(app, "pip", Some("pip install -r requirements.txt".into()));
    run_logged(
        app,
        "pip",
        Command::new(&python)
            .args(["-m", "pip", "install", "--progress-bar", "off", "-r"])
            .arg(&requirements),
    )?;
    std::fs::write(&marker, wanted).map_err(|e| e.to_string())?;
    emit(app, "done", None);
    Ok(python)
}
