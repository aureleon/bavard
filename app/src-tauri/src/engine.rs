//! Supervises the Python sidecar (`serve.py`): spawn, relay events, send
//! commands, and kill the whole process group on exit so its ~6 GB of model
//! memory goes back to macOS at once.

use serde_json::{json, Value};
use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};

const STDERR_TAIL: usize = 60;

#[derive(Default)]
struct Inner {
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    /// Bumped on every spawn, so threads of an old process stay quiet.
    generation: u64,
}

#[derive(Default)]
pub struct Engine {
    inner: Mutex<Inner>,
    tail: Arc<Mutex<VecDeque<String>>>,
}

pub struct Launch {
    pub python: PathBuf,
    pub engine_dir: PathBuf,
    pub args: Vec<String>,
    pub log_path: PathBuf,
}

impl Engine {
    pub fn is_running(&self) -> bool {
        self.inner.lock().unwrap().child.is_some()
    }

    pub fn spawn(self: &Arc<Self>, app: &AppHandle, launch: Launch) -> Result<(), String> {
        self.stop();
        if let Some(dir) = launch.log_path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        let mut log = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&launch.log_path)
            .ok();
        if let Some(f) = log.as_mut() {
            let _ = writeln!(f, "\n=== start {:?} {:?}", launch.python, launch.args);
        }

        let mut cmd = Command::new(&launch.python);
        cmd.arg("-u")
            .arg(launch.engine_dir.join("serve.py"))
            .args(&launch.args)
            .current_dir(&launch.engine_dir)
            .env("PYTHONUNBUFFERED", "1")
            .env("TOKENIZERS_PARALLELISM", "false")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            // Own process group: one killpg() reaches any helper process too.
            .process_group(0);
        let mut child = cmd
            .spawn()
            .map_err(|e| format!("cannot start {}: {e}", launch.python.display()))?;

        let stdout = child.stdout.take().unwrap();
        let stderr = child.stderr.take().unwrap();
        let stdin = child.stdin.take();
        let generation = {
            let mut inner = self.inner.lock().unwrap();
            inner.generation += 1;
            inner.child = Some(child);
            inner.stdin = stdin;
            inner.generation
        };
        self.tail.lock().unwrap().clear();

        // stdout: protocol events -> frontend
        let app_out = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                match serde_json::from_str::<Value>(&line) {
                    Ok(v) => {
                        let _ = app_out.emit("engine", v);
                    }
                    Err(_) => eprintln!("[engine] non-protocol line: {line}"),
                }
            }
        });

        // stderr: log file + tail for crash reports
        let tail = self.tail.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                if let Some(f) = log.as_mut() {
                    let _ = writeln!(f, "{line}");
                }
                let mut t = tail.lock().unwrap();
                t.push_back(line);
                if t.len() > STDERR_TAIL {
                    t.pop_front();
                }
            }
        });

        // waiter: report unexpected exits
        let me = self.clone();
        let app_exit = app.clone();
        std::thread::spawn(move || loop {
            std::thread::sleep(Duration::from_millis(250));
            let mut inner = me.inner.lock().unwrap();
            if inner.generation != generation {
                return; // replaced or stopped on purpose
            }
            let Some(child) = inner.child.as_mut() else { return };
            if let Ok(Some(status)) = child.try_wait() {
                inner.child = None;
                inner.stdin = None;
                drop(inner);
                std::thread::sleep(Duration::from_millis(100)); // let stderr drain
                let tail: Vec<String> = me.tail.lock().unwrap().iter().cloned().collect();
                let _ = app_exit.emit(
                    "engine-exit",
                    json!({ "code": status.code(), "tail": tail.join("\n") }),
                );
                return;
            }
        });
        Ok(())
    }

    pub fn send(&self, msg: &Value) -> Result<(), String> {
        let mut inner = self.inner.lock().unwrap();
        let stdin = inner.stdin.as_mut().ok_or("engine not running")?;
        let line = serde_json::to_string(msg).map_err(|e| e.to_string())?;
        stdin
            .write_all(format!("{line}\n").as_bytes())
            .and_then(|_| stdin.flush())
            .map_err(|e| e.to_string())
    }

    /// Ask the sidecar to exit, then kill its process group after a short grace.
    pub fn stop(&self) {
        let (child, stdin) = {
            let mut inner = self.inner.lock().unwrap();
            inner.generation += 1;
            (inner.child.take(), inner.stdin.take())
        };
        let Some(mut child) = child else { return };
        if let Some(mut stdin) = stdin {
            let _ = stdin.write_all(b"{\"cmd\":\"shutdown\"}\n");
            let _ = stdin.flush();
        }
        let pid = child.id() as i32;
        let deadline = Instant::now() + Duration::from_millis(1500);
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                break;
            }
            std::thread::sleep(Duration::from_millis(30));
        }
        unsafe {
            libc::killpg(pid, libc::SIGKILL);
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}
