use serde_json::{json, Value};
use std::{
    collections::HashMap,
    io::{Read, Write},
    sync::{mpsc, Arc, Mutex, OnceLock},
    time::Duration,
};
pub fn validate(value: &Value) -> Result<(), String> {
    static VALIDATOR: OnceLock<jsonschema::Validator> = OnceLock::new();
    let schema = VALIDATOR.get_or_init(|| {
        jsonschema::validator_for(
            &serde_json::from_str(include_str!("../../spec.schema.json")).unwrap(),
        )
        .unwrap()
    });
    schema
        .validate(value)
        .map_err(|e| format!("Invalid component: {e}"))
}
pub fn session_args(
    model: &str,
    resume: bool,
    config: &str,
    plugin: &str,
) -> Result<Vec<String>, String> {
    if !["auto", "sonnet", "opus", "fable"].contains(&model) {
        return Err("Unsupported model".into());
    }
    #[cfg(windows)]
    {
        let mut args = vec![];
        if model != "auto" {
            args.extend([
                "--model".into(),
                if model == "fable" {
                    "claude-fable-5-2"
                } else {
                    model
                }
                .into(),
            ]);
        }
        if resume {
            args.push("--continue".into());
        }
        args.extend([
            "--mcp-config".into(),
            config.into(),
            "--plugin-dir".into(),
            plugin.into(),
        ]);
        return Ok(args);
    }
    #[cfg(not(windows))]
    {
        let mut args = vec![
            "--model".into(),
            model.into(),
            "--project".into(),
            platform::home()?.to_string_lossy().into_owned(),
        ];
        if resume {
            args.extend(["--resume".into(), "latest".into()]);
        }
        args.extend([
            "--".into(),
            "--mcp-config".into(),
            config.into(),
            "--plugin-dir".into(),
            plugin.into(),
        ]);
        Ok(args)
    }
}
pub mod platform {
    use std::path::PathBuf;
    pub fn home() -> Result<PathBuf, String> {
        std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
            .map(PathBuf::from)
            .ok_or("Home unavailable".into())
    }
    pub fn root() -> PathBuf {
        #[cfg(windows)]
        if let Ok(exe) = std::env::current_exe() {
            if let Some(parent) = exe.parent() {
                let runtime = parent.join("runtime");
                if runtime.join("mcp/server.py").is_file() {
                    return runtime;
                }
            }
        }
        std::env::var_os("ALNAIR_APP_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .parent()
                    .unwrap()
                    .into()
            })
    }
    pub fn launcher() -> Result<PathBuf, String> {
        #[cfg(windows)]
        {
            let local = home()?.join(".local/bin/claude.exe");
            if local.is_file() {
                return Ok(local);
            }
            for dir in std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default()) {
                let candidate = dir.join("claude.exe");
                if candidate.is_file() {
                    return Ok(candidate);
                }
            }
            Err("Claude Code not found. Install it and sign in interactively before starting Alnair.".into())
        }
        #[cfg(not(windows))]
        Ok(home()?.join(".local/bin/alnair"))
    }
    pub fn python() -> String {
        if cfg!(windows) {
            return "python".into();
        }
        if cfg!(target_os = "macos") && std::path::Path::new("/opt/homebrew/bin/python3").exists() {
            "/opt/homebrew/bin/python3".into()
        } else {
            "python3".into()
        }
    }
    pub fn material(window: &tauri::WebviewWindow) {
        #[cfg(target_os = "macos")]
        let _ = window_vibrancy::apply_vibrancy(
            window,
            window_vibrancy::NSVisualEffectMaterial::Sidebar,
            None,
            None,
        );
        #[cfg(windows)]
        if window_vibrancy::apply_mica(window, Some(true)).is_err() {
            let _ = window_vibrancy::apply_acrylic(window, Some((18, 20, 31, 220)));
        }
        #[cfg(not(any(target_os = "macos", windows)))]
        let _ = window;
    }
}
type Answer = Result<Value, String>;
struct Pending {
    sender: mpsc::Sender<Answer>,
    spec: Value,
    request_id: String,
}
pub struct Bridge {
    pub token: String,
    pub port: u16,
    panels: Mutex<HashMap<String, Value>>,
    pending: Mutex<HashMap<String, Pending>>,
    revision: std::sync::atomic::AtomicU64,
}
impl Bridge {
    pub fn start() -> Result<Arc<Self>, String> {
        let server = tiny_http::Server::http("127.0.0.1:0").map_err(|e| e.to_string())?;
        let port = server
            .server_addr()
            .to_ip()
            .ok_or("No local address")?
            .port();
        let bridge = Arc::new(Self {
            token: uuid::Uuid::new_v4().to_string(),
            port,
            panels: Mutex::new(HashMap::new()),
            pending: Mutex::new(HashMap::new()),
            revision: std::sync::atomic::AtomicU64::new(0),
        });
        let weak = Arc::downgrade(&bridge);
        std::thread::spawn(move || {
            let inflight = Arc::new(std::sync::atomic::AtomicUsize::new(0));
            loop {
                let Some(b) = weak.upgrade() else { break };
                let request = match server.recv_timeout(Duration::from_millis(250)) {
                    Ok(Some(r)) => r,
                    _ => continue,
                };
                if inflight.fetch_add(1, std::sync::atomic::Ordering::SeqCst) >= 32 {
                    inflight.fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
                    let _ = request.respond(tiny_http::Response::empty(429));
                    continue;
                }
                let count = inflight.clone();
                std::thread::spawn(move || {
                    b.http(request);
                    count.fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
                });
            }
        });
        Ok(bridge)
    }
    fn http(&self, mut req: tiny_http::Request) {
        let authorized = req.headers().iter().any(|h| {
            h.field.equiv("Authorization") && h.value.as_str() == format!("Bearer {}", self.token)
        });
        let origin = req.headers().iter().any(|h| h.field.equiv("Origin"));
        let host = req.headers().iter().any(|h| {
            h.field.equiv("Host") && h.value.as_str() == format!("127.0.0.1:{}", self.port)
        });
        if !authorized || origin || !host {
            let _ = req.respond(tiny_http::Response::empty(403));
            return;
        }
        if req.method() != &tiny_http::Method::Post || req.url() != "/tool" {
            let _ = req.respond(tiny_http::Response::empty(404));
            return;
        }
        let mut body = Vec::new();
        let result = if req
            .as_reader()
            .take(1_100_001)
            .read_to_end(&mut body)
            .is_err()
            || body.len() > 1_100_000
        {
            Err("Payload too large".into())
        } else {
            serde_json::from_slice(&body)
                .map_err(|_| "Invalid JSON".into())
                .and_then(|v| self.apply(v))
        };
        let value = result.unwrap_or_else(|error| json!({"error":error}));
        let response = tiny_http::Response::from_string(value.to_string()).with_header(
            tiny_http::Header::from_bytes("Content-Type", "application/json").unwrap(),
        );
        let _ = req.respond(response);
    }
    pub fn snapshot(&self) -> Value {
        let mut items: Vec<_> = self.panels.lock().unwrap().values().cloned().collect();
        items.sort_by(|a, b| a["id"].as_str().cmp(&b["id"].as_str()));
        json!({"revision":self.revision.load(std::sync::atomic::Ordering::SeqCst),"components":items})
    }
    pub fn clear(&self) {
        self.panels.lock().unwrap().clear();
        for (_, p) in self.pending.lock().unwrap().drain() {
            let _ = p.sender.send(Err("Canvas cleared".into()));
        }
        self.bump();
    }
    fn bump(&self) {
        self.revision
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    }
    pub fn apply(&self, input: Value) -> Result<Value, String> {
        let op = input["op"].as_str().ok_or("Missing operation")?;
        if op == "clear" {
            if let Some(id) = input["id"].as_str() {
                self.panels.lock().unwrap().remove(id);
                if let Some(p) = self.pending.lock().unwrap().remove(id) {
                    let _ = p.sender.send(Err("Component cleared".into()));
                }
                self.bump()
            } else {
                self.clear()
            }
            return Ok(json!({"ok":true}));
        }
        if !["render", "update", "ask"].contains(&op) {
            return Err("Unknown operation".into());
        }
        let spec = &input["spec"];
        validate(spec)?;
        let id = spec["id"].as_str().unwrap().to_owned();
        let (tx, rx) = mpsc::channel();
        let request_id = uuid::Uuid::new_v4().to_string();
        let timeout = if op == "ask" {
            input
                .get("timeout")
                .map(|v| {
                    v.as_u64()
                        .filter(|v| (1..=600).contains(v))
                        .ok_or("Timeout must be 1–600")
                })
                .unwrap_or(Ok(120))?
        } else {
            0
        };
        let mut pending = self.pending.lock().unwrap();
        if pending.contains_key(&id) {
            return Err("A human answer is still pending for this id".into());
        }
        if op == "ask" {
            if !["form", "choices"].contains(&spec["type"].as_str().unwrap()) {
                return Err("ui_ask requires form or choices".into());
            }
            if pending.len() >= 16 {
                return Err("Too many pending questions".into());
            }
            pending.insert(
                id.clone(),
                Pending {
                    sender: tx,
                    spec: spec.clone(),
                    request_id: request_id.clone(),
                },
            );
        }
        {
            let mut panels = self.panels.lock().unwrap();
            if panels.len() >= 100 && !panels.contains_key(&id) {
                pending.remove(&id);
                return Err("Canvas limit reached".into());
            }
            if op == "update" && !panels.contains_key(&id) {
                return Err("Component not found".into());
            }
            panels.insert(id.clone(), spec.clone());
        }
        drop(pending);
        self.bump();
        if op == "ask" {
            let result = rx
                .recv_timeout(Duration::from_secs(timeout))
                .map_err(|_| "Human answer timed out".to_string())
                .and_then(|x| x);
            {
                let mut pending = self.pending.lock().unwrap();
                if pending.get(&id).is_some_and(|p| p.request_id == request_id) {
                    pending.remove(&id);
                }
            }
            result.map(|answer| json!({"answer":answer}))
        } else {
            Ok(json!({"ok":true,"id":id}))
        }
    }
    pub fn answer(&self, id: &str, value: Value) -> Result<(), String> {
        if value.to_string().len() > 32000 {
            return Err("Answer too large".into());
        }
        let mut pending = self.pending.lock().unwrap();
        let p = pending
            .get(id)
            .ok_or("No pending question for this component")?;
        let fields = value.as_object().ok_or("Answer must be an object")?;
        if p.spec["type"] == "choices" {
            if fields.len() != 1
                || !p.spec["options"]
                    .as_array()
                    .unwrap()
                    .contains(&value["choice"])
            {
                return Err("Invalid choice".into());
            }
        } else {
            let definitions = p.spec["fields"].as_array().unwrap();
            for (name, v) in fields {
                let f = definitions
                    .iter()
                    .find(|f| f["name"] == *name)
                    .ok_or("Unknown answer field")?;
                if !v.is_string() {
                    return Err("Answer fields must be strings".into());
                }
                if f["kind"] == "select" && !f["options"].as_array().is_some_and(|a| a.contains(v))
                {
                    return Err("Invalid select option".into());
                }
            }
            for f in definitions {
                if f["required"] == true
                    && value[f["name"].as_str().unwrap()]
                        .as_str()
                        .unwrap_or("")
                        .trim()
                        .is_empty()
                {
                    return Err("Required field missing".into());
                }
            }
        }
        pending
            .remove(id)
            .unwrap()
            .sender
            .send(Ok(value))
            .map_err(|_| "Question expired".into())
    }
}
#[cfg(test)]
mod tests {
    #[test]
    fn rejects_arbitrary_components() {
        assert!(
            super::validate(&serde_json::json!({"id":"x","type":"html","html":"<script>"}))
                .is_err()
        );
    }
    #[test]
    fn accepts_card() {
        assert!(super::validate(
            &serde_json::json!({"id":"x","type":"card","title":"Hello","body":"World"})
        )
        .is_ok());
    }
    #[test]
    fn rejects_script_images() {
        assert!(super::validate(
            &serde_json::json!({"id":"x","type":"image","src":"javascript:alert(1)","alt":""})
        )
        .is_err());
    }
    #[test]
    fn rejects_unknown_keys() {
        assert!(super::validate(
            &serde_json::json!({"id":"x","type":"card","title":"X","body":"Y","onClick":"bad"})
        )
        .is_err());
    }
    #[test]
    fn no_headless_args() {
        let args = super::session_args("auto", false, "/tmp/mcp.json", "/tmp/plugin").unwrap();
        assert!(!args.iter().any(|x| x == "-p" || x == "--print"));
    }
    #[test]
    fn rejects_unknown_model() {
        assert!(super::session_args("evil", false, "m", "p").is_err());
    }
}
pub mod pty;
use tauri::{Emitter, Manager};
struct AppState {
    bridge: Arc<Bridge>,
    session: Mutex<Option<pty::Session>>,
}
fn clean_env(cmd: &mut portable_pty::CommandBuilder) {
    for key in [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    ] {
        cmd.env_remove(key);
    }
    cmd.env("TERM", "xterm-256color");
    cmd.env("COLORTERM", "truecolor");
    // Explorer-launched Windows apps must retain Python and system executable paths.
    #[cfg(not(windows))]
    cmd.env(
        "PATH",
        format!(
            "{}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            platform::home().unwrap().display()
        ),
    );
}
#[tauri::command]
fn canvas_snapshot(state: tauri::State<AppState>) -> Value {
    state.bridge.snapshot()
}
#[tauri::command]
fn submit_answer(state: tauri::State<AppState>, id: String, answer: Value) -> Result<(), String> {
    state.bridge.answer(&id, answer)
}
#[tauri::command]
fn clear_canvas(state: tauri::State<AppState>) {
    state.bridge.clear()
}
#[tauri::command]
async fn start_session(
    app: tauri::AppHandle,
    model: String,
    resume: bool,
    cols: u16,
    rows: u16,
) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<AppState>();
        start_session_inner(app.clone(), state, model, resume, cols, rows)
    })
    .await
    .map_err(|_| "Session startup failed".to_string())?
}
fn start_session_inner(
    app: tauri::AppHandle,
    state: tauri::State<AppState>,
    model: String,
    resume: bool,
    cols: u16,
    rows: u16,
) -> Result<Value, String> {
    let dimensions = pty::size(cols, rows)?;
    let mut guard = state.session.lock().map_err(|_| "Session unavailable")?;
    // Replacing a live session must be an explicit UI command.
    guard.take();
    state.bridge.clear();
    let root = platform::root();
    let home = platform::home()?;
    let run_id = uuid::Uuid::new_v4().to_string();
    let dir = home.join(".local/state/alnair-gui").join(&run_id);
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700))
            .map_err(|e| e.to_string())?;
    }
    let config = dir.join("mcp.json");
    let payload = json!({"mcpServers":{"alnair_ui":{"command":platform::python(),"args":[root.join("mcp/server.py")],"env":{"ALNAIR_UI_URL":format!("http://127.0.0.1:{}",state.bridge.port),"ALNAIR_UI_TOKEN":state.bridge.token}}}});
    // The private parent directory is created before writing the per-launch token.
    std::fs::write(&config, payload.to_string()).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&config, std::fs::Permissions::from_mode(0o600))
            .map_err(|e| e.to_string())?;
    }
    #[cfg(windows)]
    let (ledger_id, run_path) = (run_id.clone(), dir.clone());
    #[cfg(not(windows))]
    let (ledger_id, run_path) = if resume {
        let preview = std::process::Command::new(platform::launcher()?)
            .args(["--dry-run", "--resume", "latest"])
            .current_dir(&home)
            .env_remove("ANTHROPIC_API_KEY")
            .env_remove("ANTHROPIC_AUTH_TOKEN")
            .env_remove("ANTHROPIC_BASE_URL")
            .output()
            .map_err(|_| "Could not preview latest harness session")?;
        if !preview.status.success() {
            return Err("Could not resume latest harness session; check alnair doctor".into());
        }
        let data: Value =
            serde_json::from_slice(&preview.stdout).map_err(|_| "Invalid harness preview")?;
        (
            data["sessionId"]
                .as_str()
                .ok_or("Missing resume id")?
                .to_owned(),
            std::path::PathBuf::from(data["run"].as_str().ok_or("Missing resume path")?),
        )
    } else {
        (
            run_id.clone(),
            home.join(".local/state/claude-lab/runs").join(&run_id),
        )
    };
    #[allow(unused_mut)]
    let mut args = session_args(
        &model,
        resume,
        &config.to_string_lossy(),
        &root.join("plugin").to_string_lossy(),
    )?;
    #[cfg(not(windows))]
    if resume {
        let at = args.iter().position(|a| a == "--resume").unwrap() + 1;
        args[at] = run_path.to_string_lossy().into_owned();
    }
    #[cfg(not(windows))]
    if !resume {
        let at = args.iter().position(|a| a == "--").unwrap();
        args.splice(
            at..at,
            [
                "--run".into(),
                home.join(".local/state/claude-lab/runs")
                    .join(&run_id)
                    .to_string_lossy()
                    .into_owned(),
            ],
        );
    }
    let mut cmd = portable_pty::CommandBuilder::new(platform::launcher()?);
    cmd.args(&args);
    cmd.cwd(&home);
    clean_env(&mut cmd);
    let result = pty::spawn(cmd, dimensions);
    let (master, writer, child, mut reader) = match result {
        Ok(p) => p,
        Err(e) => {
            let _ = std::fs::remove_dir_all(dir);
            return Err(e);
        }
    };
    let event_id = run_id.clone();
    std::thread::spawn(move || {
        let mut bytes = [0u8; 16384];
        while let Ok(n) = reader.read(&mut bytes) {
            if n == 0 {
                break;
            }
            let _ = app.emit("pty-output", json!({"id":event_id,"bytes":&bytes[..n]}));
        }
        let _ = app.emit("pty-exit", json!({"id":event_id}));
    });
    *guard = Some(pty::Session {
        master,
        writer,
        child,
        run_id: run_id.clone(),
        ledger_id,
        run_path,
        config_dir: dir,
    });
    Ok(
        json!({"id":run_id,"model":model,"memory":if cfg!(windows) {"unavailable on this machine"} else {"loaded by harness"},"research":if cfg!(windows) {"unavailable on this machine"} else {"loaded by harness"}}),
    )
}
#[tauri::command]
fn session_status(state: tauri::State<AppState>) -> Value {
    let mut guard = state.session.lock().unwrap();
    let Some(session) = guard.as_mut() else {
        return json!({"active":false});
    };
    #[cfg(not(windows))]
    let read = |name: &str| -> Value {
        std::fs::read(session.run_path.join(name))
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or(Value::Null)
    };
    #[cfg(windows)]
    {
        let active = session
            .child
            .try_wait()
            .map(|status| status.is_none())
            .unwrap_or(false);
        if !active {
            guard.take();
        } // Releases ConPTY so the reader can reach EOF.
        return json!({"active":active,"route":null,"memory":false,"research":false});
    }
    #[cfg(not(windows))]
    {
        let runtime = read("runtime.json");
        let metrics = read("metrics.json");
        json!({"active":session.child.try_wait().map(|status|status.is_none()).unwrap_or(false),"route":metrics["route"]["modelAlias"].as_str().or_else(||metrics["route"]["model"].as_str()),"memory":runtime["claudeMem"]==true,"research":runtime["research"]==true})
    }
}
#[tauri::command]
fn pty_write(state: tauri::State<AppState>, data: String) -> Result<(), String> {
    if data.len() > 65536 {
        return Err("Input too large".into());
    }
    let mut guard = state.session.lock().unwrap();
    let s = guard.as_mut().ok_or("No active session")?;
    s.writer
        .write_all(data.as_bytes())
        .and_then(|_| s.writer.flush())
        .map_err(|e| e.to_string())
}
#[tauri::command]
fn pty_resize(state: tauri::State<AppState>, cols: u16, rows: u16) -> Result<(), String> {
    let guard = state.session.lock().unwrap();
    if let Some(s) = guard.as_ref() {
        s.master
            .resize(pty::size(cols, rows)?)
            .map_err(|e| e.to_string())?
    }
    Ok(())
}
#[tauri::command]
fn stop_session(state: tauri::State<AppState>) {
    state.session.lock().unwrap().take();
    state.bridge.clear();
}
#[tauri::command]
fn open_vault_note(state: tauri::State<AppState>) -> Result<(), String> {
    #[cfg(windows)]
    {
        let _ = state;
        return Err("Vault notes and CLP memory are unavailable on this machine.".into());
    }
    #[cfg(not(windows))]
    {
        let home = platform::home()?;
        let guard = state.session.lock().unwrap();
        let session = guard.as_ref().ok_or("Start a session first")?;
        let marker = home
            .join(".clp/sessions")
            .join(&session.ledger_id)
            .join("vault-note.json");
        let data: Value = serde_json::from_slice(
            &std::fs::read(marker).map_err(|_| "No vault note yet; finish a turn first")?,
        )
        .map_err(|e| e.to_string())?;
        let path = data["path"]
            .as_str()
            .or_else(|| data["notePath"].as_str())
            .ok_or("Vault note path unavailable")?;
        let file = std::path::Path::new(path)
            .canonicalize()
            .map_err(|e| e.to_string())?;
        let vault = std::env::var_os("ALNAIR_VAULT")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| home.join("Documents/Obsidian Vault"))
            .canonicalize()
            .map_err(|e| e.to_string())?;
        if !file.starts_with(vault) || file.extension().and_then(|x| x.to_str()) != Some("md") {
            return Err("Note is outside the vault".into());
        }
        #[cfg(target_os = "macos")]
        std::process::Command::new("open")
            .arg(file)
            .spawn()
            .map_err(|e| e.to_string())?;
        #[cfg(not(target_os = "macos"))]
        return Err("Vault opening is not implemented on this platform yet".into());
        Ok(())
    }
}
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let bridge = Bridge::start().map_err(std::io::Error::other)?;
            app.manage(AppState {
                bridge,
                session: Mutex::new(None),
            });
            platform::material(&app.get_webview_window("main").unwrap());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            session_status,
            canvas_snapshot,
            submit_answer,
            clear_canvas,
            start_session,
            pty_write,
            pty_resize,
            stop_session,
            open_vault_note
        ])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                if let Some(state) = window.try_state::<AppState>() {
                    state.session.lock().unwrap().take();
                    state.bridge.clear();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("Unable to start Alnair");
}
#[cfg(test)]
mod bridge_tests {
    use super::*;
    #[test]
    fn render_update_clear() {
        let b = Bridge::start().unwrap();
        let s = json!({"id":"card","type":"card","title":"Test","body":"Body"});
        assert!(b.apply(json!({"op":"update","spec":s})).is_err());
        b.apply(json!({"op":"render","spec":s})).unwrap();
        assert_eq!(b.snapshot()["components"].as_array().unwrap().len(), 1);
        b.clear();
        assert!(b.snapshot()["components"].as_array().unwrap().is_empty());
    }
    #[test]
    fn ask_rejects_noninteractive_specs() {
        let b = Bridge::start().unwrap();
        assert!(b
            .apply(json!({"op":"ask","spec":{"id":"x","type":"card","title":"a","body":"b"}}))
            .is_err());
    }
    #[test]
    fn actual_stdio_server_to_rust_bridge_and_form_answer() {
        let b = Bridge::start().unwrap();
        let responder = b.clone();
        let thread = std::thread::spawn(move || {
            for _ in 0..500 {
                if responder.pending.lock().unwrap().contains_key("question") {
                    assert!(responder.answer("question", json!({"name":""})).is_err());
                    responder
                        .answer("question", json!({"name":"Alnair"}))
                        .unwrap();
                    return;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            panic!("Question did not reach Rust bridge")
        });
        let status = std::process::Command::new(platform::python())
            .arg(platform::root().join("tests/real_bridge.py"))
            .env("ALNAIR_UI_URL", format!("http://127.0.0.1:{}", b.port))
            .env("ALNAIR_UI_TOKEN", &b.token)
            .status()
            .unwrap();
        thread.join().unwrap();
        assert!(status.success());
    }
}
#[cfg(test)]
mod boundary_tests {
    use super::*;
    #[test]
    fn validates_every_gallery_component() {
        let specs: Vec<Value> =
            serde_json::from_str(include_str!("../../tests/gallery.json")).unwrap();
        for spec in specs {
            validate(&spec).unwrap()
        }
    }
    #[test]
    fn ask_times_out_without_inventing_consent() {
        let b = Bridge::start().unwrap();
        let err=b.apply(json!({"op":"ask","spec":{"id":"q","type":"choices","title":"Choose","options":["yes"]},"timeout":1})).unwrap_err();
        assert!(err.contains("timed out"));
        assert!(b.answer("q", json!({"choice":"yes"})).is_err());
    }
    #[test]
    fn clear_cancels_waiting_question() {
        let b = Bridge::start().unwrap();
        let other = b.clone();
        let wait = std::thread::spawn(move || {
            other.apply(json!({"op":"ask","spec":{"id":"q","type":"choices","title":"Choose","options":["yes"]},"timeout":5}))
        });
        for _ in 0..100 {
            let ready = b.pending.lock().unwrap().contains_key("q");
            if ready {
                break;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        b.clear();
        assert!(wait.join().unwrap().unwrap_err().contains("cleared"));
    }
    #[test]
    fn enforces_canvas_limit() {
        let b = Bridge::start().unwrap();
        for i in 0..100 {
            b.apply(json!({"op":"render","spec":{"id":format!("a{i}"),"type":"card","title":"x","body":"y"}})).unwrap();
        }
        assert!(b
            .apply(
                json!({"op":"render","spec":{"id":"overflow","type":"card","title":"x","body":"y"}})
            )
            .is_err());
    }
    #[test]
    fn rejects_remote_image_and_large_spec() {
        assert!(validate(
            &json!({"id":"x","type":"image","src":"https://example.com/pixel.png","alt":"a"})
        )
        .is_err());
        assert!(
            validate(&json!({"id":"x","type":"card","title":"a","body":"x".repeat(32001)}))
                .is_err()
        );
    }
}

#[cfg(all(test, windows))]
mod windows_launch_tests {
    use super::*;
    #[test]
    fn direct_cli_uses_real_fable_id_without_harness_flags() {
        let args = session_args("fable", false, "mcp.json", "plugin").unwrap();
        assert!(args.contains(&"claude-fable-5-2".into()));
        assert!(!args
            .iter()
            .any(|a| ["--project", "--run", "--", "-p", "--print"].contains(&a.as_str())));
        assert!(args.contains(&"--mcp-config".into()));
        assert!(args.contains(&"--plugin-dir".into()));
    }
    #[test]
    fn default_leaves_model_selection_to_claude() {
        let args = session_args("auto", false, "m", "p").unwrap();
        assert!(!args.contains(&"--model".into()));
    }
    #[test]
    fn resume_uses_interactive_continue() {
        let args = session_args("opus", true, "m", "p").unwrap();
        assert!(args.contains(&"--continue".into()));
        assert!(!args.contains(&"latest".into()));
    }
}
