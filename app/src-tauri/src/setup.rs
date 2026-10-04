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

/// Kokoro (and its misaki / spaCy stack) only supports Python 3.10 - 3.12.
/// 3.12 is preferred: it is what the engine is tested with.
const MIN_MINOR: u32 = 10;
const MAX_MINOR: u32 = 12;

fn supported(v: Option<(u32, u32)>) -> Option<u32> {
    match v {
        Some((3, m)) if (MIN_MINOR..=MAX_MINOR).contains(&m) => Some(m),
        _ => None,
    }
}

fn shell_lookup(cmd: &str) -> Vec<PathBuf> {
    Command::new("/bin/zsh")
        .args(["-lc", cmd])
        .stdin(Stdio::null())
        .output()
        .map(|o| {
            String::from_utf8_lossy(&o.stdout)
                .lines()
                .map(|l| PathBuf::from(l.trim()))
                .filter(|p| p.is_absolute())
                .collect()
        })
        .unwrap_or_default()
}

/// Find a Python 3.10 - 3.12 interpreter, newest first. GUI apps get a
/// minimal PATH, so look in the usual install locations and ask a login
/// shell as well.
fn find_base_python() -> Option<PathBuf> {
    let home = std::env::var("HOME").unwrap_or_default();
    let mut candidates: Vec<PathBuf> = Vec::new();
    for m in (MIN_MINOR..=MAX_MINOR).rev() {
        let v = format!("3.{m}");
        candidates.push(format!("/opt/homebrew/bin/python{v}").into());
        candidates.push(format!("/opt/homebrew/opt/python@{v}/bin/python{v}").into());
        candidates.push(format!("/usr/local/bin/python{v}").into());
        candidates.push(format!("/Library/Frameworks/Python.framework/Versions/{v}/bin/python3").into());
    }
    for root in [
        format!("{home}/.local/share/mise/installs/python"),
        format!("{home}/.pyenv/versions"),
        format!("{home}/.local/share/uv/python"),
    ] {
        if let Ok(entries) = std::fs::read_dir(&root) {
            for e in entries.flatten() {
                candidates.push(e.path().join("bin/python3"));
            }
        }
    }
    candidates.extend(shell_lookup("command -v python3.12 python3.11 python3.10 python3"));
    candidates.push("/opt/homebrew/bin/python3".into());
    candidates.push("/usr/local/bin/python3".into());

    let mut best: Option<(u32, PathBuf)> = None;
    for p in candidates.into_iter().filter(|p| p.exists()) {
        if let Some(m) = supported(python_version(&p)) {
            if best.as_ref().is_none_or(|(bm, _)| m > *bm) {
                best = Some((m, p));
            }
        }
    }
    best.map(|(_, p)| p)
}

/// Last resort: let uv download a standalone Python 3.12.
fn python_from_uv(app: &AppHandle, paths: &Paths) -> Option<PathBuf> {
    let home = std::env::var("HOME").unwrap_or_default();
    let mut uvs: Vec<PathBuf> = vec![
        format!("{home}/.local/bin/uv").into(),
        format!("{home}/.cargo/bin/uv").into(),
        "/opt/homebrew/bin/uv".into(),
        "/usr/local/bin/uv".into(),
    ];
    uvs.extend(shell_lookup("command -v uv"));
    let uv = uvs.into_iter().find(|p| p.exists())?;
    emit(app, "uv_python", None);
    log_line(paths, &format!("setup: installing Python 3.12 with {}", uv.display()));
    let ok = Command::new(&uv)
        .args(["python", "install", "3.12"])
        .stdin(Stdio::null())
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);
    if !ok {
        return None;
    }
    let out = Command::new(&uv).args(["python", "find", "3.12"]).stdin(Stdio::null()).output().ok()?;
    let p = PathBuf::from(String::from_utf8_lossy(&out.stdout).trim());
    supported(python_version(&p)).map(|_| p)
}

/// Setup steps also go to the engine log, so failures can be read later.
fn log_line(paths: &Paths, line: &str) {
    use std::io::Write;
    if let Some(dir) = paths.log_path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&paths.log_path) {
        let _ = writeln!(f, "{line}");
    }
}

fn run_logged(app: &AppHandle, stage: &str, cmd: &mut Command) -> Result<(), String> {
    let mut child = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("{stage}: {e}"))?;
    let stderr = child.stderr.take().unwrap();
    // The UI only shows the stage; the output is kept for the error report.
    let err_thread = std::thread::spawn(move || {
        let mut tail = Vec::new();
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            tail.push(line);
            if tail.len() > 20 {
                tail.remove(0);
            }
        }
        tail
    });
    emit(app, stage, None);
    let mut out_tail: Vec<String> = Vec::new();
    for line in BufReader::new(child.stdout.take().unwrap()).lines().map_while(Result::ok) {
        out_tail.push(line);
        if out_tail.len() > 20 {
            out_tail.remove(0);
        }
    }
    let status = child.wait().map_err(|e| e.to_string())?;
    let tail = err_thread.join().unwrap_or_default();
    if status.success() {
        Ok(())
    } else {
        Err(format!("{stage} failed ({status}):\n{}\n{}", out_tail.join("\n"), tail.join("\n")))
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
    // A venv made with an unsupported Python (e.g. 3.14) can never install
    // Kokoro: start over.
    if python.exists() && supported(python_version(&python)).is_none() {
        log_line(paths, "setup: removing a venv made with an unsupported Python");
        std::fs::remove_dir_all(&paths.venv_dir).map_err(|e| e.to_string())?;
    }
    if python.exists() && std::fs::read_to_string(&marker).ok().as_deref() == Some(wanted.as_str()) {
        return Ok(python);
    }
    let result = install(app, paths, &python, &requirements);
    match &result {
        Ok(()) => {
            std::fs::write(&marker, wanted).map_err(|e| e.to_string())?;
            log_line(paths, "setup: done");
            emit(app, "done", None);
        }
        Err(e) => log_line(paths, &format!("setup failed: {e}")),
    }
    result.map(|_| python)
}

fn install(app: &AppHandle, paths: &Paths, python: &Path, requirements: &Path) -> Result<(), String> {
    log_line(paths, &format!("\n=== setup {}", paths.venv_dir.display()));
    if !python.exists() {
        emit(app, "find_python", None);
        let base = find_base_python().or_else(|| python_from_uv(app, paths)).ok_or(
            "Python 3.12 was not found. Bavard needs Python 3.10 - 3.12 (the Kokoro voice does not \
             support 3.13 or newer). Install it with `brew install python@3.12` (or \
             `uv python install 3.12`) and restart Bavard.",
        )?;
        log_line(paths, &format!("setup: base Python {}", base.display()));
        run_logged(
            app,
            "venv",
            Command::new(&base).args(["-m", "venv", "--prompt", "bavard"]).arg(&paths.venv_dir),
        )?;
        run_logged(
            app,
            "pip",
            Command::new(python).args(["-m", "pip", "install", "--upgrade", "pip"]),
        )?;
    }
    run_logged(
        app,
        "pip",
        Command::new(python)
            .args(["-m", "pip", "install", "--progress-bar", "off", "-r"])
            .arg(requirements),
    )
}
