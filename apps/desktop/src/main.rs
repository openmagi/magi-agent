// Open Magi desktop shell. Launches the local serve runtime (`magi-agent`,
// i.e. `magi_agent.main:main`) and loads its dashboard in a hardened Tauri v2
// webview.
//
// Do not pop a console window on Windows for the release build.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use magi_desktop_core::lifecycle::{self, Phase};
use magi_desktop_core::server;
use magi_desktop_core::url_policy::{classify, UrlClass};

use tauri::webview::NewWindowResponse;
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

/// Total time we wait for the runtime to report ready before failing.
///
/// The bundled PyInstaller onedir sidecar (~370 MB: litellm, google-genai,
/// rdflib, pyshacl) can take well over 30s to import and boot on first launch,
/// so a short deadline would wrongly fail a healthy-but-slow cold start. Be
/// generous: a couple of minutes covers the worst first-run import.
const READY_DEADLINE: Duration = Duration::from_secs(120);
/// Delay between bootstrap polls.
const POLL_INTERVAL: Duration = Duration::from_millis(400);

/// Owns the spawned serve child so we can kill it on exit. Stored in Tauri
/// managed state behind a mutex.
struct ServeProcess(Mutex<Option<Child>>);

impl ServeProcess {
    fn shutdown(&self) {
        // Recover the guard even if the mutex is poisoned (a panic in another
        // thread while holding the lock). A poisoned mutex must NOT leave the
        // `magi serve` child orphaned, so we take the inner guard regardless.
        let mut guard = self.0.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(mut child) = guard.take() {
            // Ask politely, then ensure it is gone. `kill` on a process
            // that already exited returns an error we can ignore. A poisoned
            // mutex must NOT leave the serve child orphaned.
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// Resolve the user home directory without unwrapping. Falls back to the
/// current directory so logging never panics.
fn home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Open (creating parents) the desktop serve log for appending.
///
/// The log can contain uvicorn access lines that echo the bearer token, so on
/// unix we lock down both the `logs` directory (`0700`) and the file (`0600`)
/// to the owner. On non-unix platforms we fall back to default permissions.
fn open_log(home: &Path) -> std::io::Result<File> {
    let path = server::log_file_path(home);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
        restrict_dir_to_owner(parent);
    }
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        // Owner read/write only (0600). Honoured only when the file is created;
        // an existing file keeps its mode, which we also tighten below.
        options.mode(0o600);
    }
    let file = options.open(&path)?;
    restrict_file_to_owner(&path);
    Ok(file)
}

/// Best-effort: set a directory to `0700` (owner-only) on unix. No-op elsewhere
/// and silent on failure so logging never blocks startup.
#[cfg(unix)]
fn restrict_dir_to_owner(dir: &Path) {
    use std::os::unix::fs::PermissionsExt;
    let _ = std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700));
}

#[cfg(not(unix))]
fn restrict_dir_to_owner(_dir: &Path) {}

/// Best-effort: set a file to `0600` (owner-only) on unix. No-op elsewhere and
/// silent on failure. Covers a pre-existing log created at a looser umask.
#[cfg(unix)]
fn restrict_file_to_owner(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
}

#[cfg(not(unix))]
fn restrict_file_to_owner(_path: &Path) {}

/// Find the serve binary using the pure resolution order. The bundled runtime
/// is the PyInstaller `--onedir` tree shipped as a Tauri resource, so the
/// executable lives at `<resource_dir>/magi/magi` (see
/// `server::bundled_resource_binary`). `resource_dir` is the app's resolved
/// resource directory (`None` when it cannot be determined, e.g. in dev).
fn find_magi_binary(resource_dir: Option<&Path>) -> Option<PathBuf> {
    let bundled = resource_dir.map(server::bundled_resource_binary);
    let env_bin = std::env::var_os("MAGI_BIN").map(PathBuf::from);
    let home = home_dir();
    server::resolve_magi_binary(bundled, env_bin, Some(&home), |p| p.exists(), which_on_path)
}

/// Look up a bare command name on `PATH`. Returns the first executable match.
fn which_on_path(name: &str) -> Option<PathBuf> {
    let paths = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&paths) {
        let candidate = dir.join(name);
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

/// Spawn `<bin> --host 127.0.0.1 --port <port>` with stdout/stderr teed to the
/// log file.
///
/// The serve entrypoint is the `magi-agent` console script
/// (`magi_agent.main:main`), a plain argparse that takes `--host`/`--port`
/// directly: there is NO `serve` subcommand. The bundled PyInstaller binary is
/// named `magi` but is the same `main:main` entry, so it accepts these flags
/// too.
///
/// The desktop app MUST bind loopback only: the runtime otherwise binds
/// `0.0.0.0` with a well-known dev token, which would expose the agent to
/// anyone on the same LAN (remote takeover on shared wifi). We pin `--host
/// 127.0.0.1` so the runtime is reachable only from this machine.
fn spawn_serve(bin: &Path, port: u16, log: File) -> std::io::Result<Child> {
    let err_log = log.try_clone()?;
    // A GUI-launched .app inherits CWD "/" (read-only), but the runtime writes
    // some state to relative paths (e.g. the observability store ".openmagi").
    // Pin the child's working directory to the home dir so those writes land in
    // a writable location instead of crashing on a read-only filesystem.
    Command::new(bin)
        .current_dir(home_dir())
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port.to_string())
        .env("CORE_AGENT_PORT", port.to_string())
        .env("MAGI_SERVE_HOST", "127.0.0.1")
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(err_log))
        .stdin(Stdio::null())
        .spawn()
}

/// Minimal blocking HTTP GET over loopback. Returns (status, body). We avoid an
/// HTTP crate because the only endpoint we hit is our own bootstrap JSON on
/// localhost. A connection error maps to status 0 so the caller treats it as
/// "not ready yet".
fn http_get_loopback(port: u16, path: &str) -> (u16, String) {
    let addr = format!("127.0.0.1:{port}");
    let stream = match TcpStream::connect(&addr) {
        Ok(s) => s,
        Err(_) => return (0, String::new()),
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let mut stream = stream;
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\nAccept: application/json\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return (0, String::new());
    }
    let mut raw = String::new();
    if stream.read_to_string(&mut raw).is_err() {
        return (0, String::new());
    }
    parse_http_response(&raw)
}

/// Parse a raw HTTP/1.1 response into (status_code, body). Lenient: anything
/// malformed yields status 0.
fn parse_http_response(raw: &str) -> (u16, String) {
    let mut parts = raw.splitn(2, "\r\n\r\n");
    let head = parts.next().unwrap_or("");
    let body = parts.next().unwrap_or("").to_string();
    let status = head
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse::<u16>().ok())
        .unwrap_or(0);
    (status, body)
}

/// Poll the bootstrap URL, driving the lifecycle state machine, until Ready or
/// Failed. Returns the terminal phase.
fn wait_until_ready(port: u16) -> Phase {
    let start = Instant::now();
    let mut phase = Phase::Spawning;
    loop {
        let (status, body) = http_get_loopback(port, "/app/bootstrap.json");
        let healthy = server::is_ready(&body, status);
        phase = lifecycle::next(&phase, healthy, start.elapsed(), READY_DEADLINE);
        match phase {
            Phase::Ready | Phase::Failed(_) => return phase,
            _ => std::thread::sleep(POLL_INTERVAL),
        }
    }
}

/// HTML shown when startup fails. Static, no runtime data, no remote nav.
fn error_page(reason: &str) -> String {
    let safe = reason
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;");
    format!(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">\
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\
<title>Open Magi could not start</title>\
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;\
background:#0b0d12;color:#e6e9f0;font-family:ui-sans-serif,system-ui,sans-serif}}\
main{{max-width:34rem;padding:2rem}}code{{background:#1b1f29;padding:.15em .4em;\
border-radius:4px}}</style></head><body><main><h1>Open Magi could not start</h1>\
<p>{safe}</p><p>Check the log at <code>~/.magi/logs/desktop-serve.log</code> \
for details, then relaunch.</p></main></body></html>"
    )
}

/// Loading HTML shown while the runtime boots.
fn loading_page() -> &'static str {
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">\
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\
<title>Starting Open Magi</title>\
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;\
background:#0b0d12;color:#e6e9f0;font-family:ui-sans-serif,system-ui,sans-serif}\
main{text-align:center}.dot{display:inline-block;width:.5rem;height:.5rem;\
margin:0 .15rem;border-radius:50%;background:#22c55e;animation:b 1s infinite}\
.dot:nth-child(2){animation-delay:.15s}.dot:nth-child(3){animation-delay:.3s}\
@keyframes b{0%,100%{opacity:.3}50%{opacity:1}}</style></head><body><main>\
<h1>Starting Open Magi</h1><p>Launching the local agent runtime.</p>\
<p style=\"opacity:.7;font-size:.9rem;max-width:30rem\">First launch can take \
up to a couple of minutes while the local model runtime starts. Later launches \
are much faster.</p>\
<div><span class=\"dot\"></span><span class=\"dot\"></span>\
<span class=\"dot\"></span></div></main></body></html>"
}

fn main() {
    let single_instance = tauri_plugin_single_instance::init(|app, _argv, _cwd| {
        // A second launch just focuses the existing window (dashboard or loading).
        if let Some(win) = app
            .get_webview_window("main")
            .or_else(|| app.get_webview_window("loading"))
        {
            let _ = win.set_focus();
        }
    });

    tauri::Builder::default()
        .plugin(single_instance)
        .plugin(tauri_plugin_opener::init())
        .manage(ServeProcess(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();

            // 1. Choose a port and resolve the binary.
            let port = match server::pick_free_port() {
                Ok(p) => p,
                Err(e) => {
                    show_error(&handle, &format!("could not allocate a local port: {e}"));
                    return Ok(());
                }
            };
            // The bundled runtime ships as a Tauri resource tree, so resolve the
            // app's resource directory and look for `<resource_dir>/magi/magi`.
            let resource_dir = app.path().resource_dir().ok();
            let bin = match find_magi_binary(resource_dir.as_deref()) {
                Some(b) => b,
                None => {
                    show_error(
                        &handle,
                        "could not find the serve binary (`magi-agent`). Install Open Magi \
                         (brew install openmagi/tap/magi-agent) or set MAGI_BIN.",
                    );
                    return Ok(());
                }
            };

            // 2. Spawn the serve process, teeing output to the log file.
            let home = home_dir();
            let log = match open_log(&home) {
                Ok(f) => f,
                Err(e) => {
                    show_error(&handle, &format!("could not open the desktop log: {e}"));
                    return Ok(());
                }
            };
            let child = match spawn_serve(&bin, port, log) {
                Ok(c) => c,
                Err(e) => {
                    show_error(&handle, &format!("could not start the serve runtime: {e}"));
                    return Ok(());
                }
            };
            if let Some(state) = app.try_state::<ServeProcess>() {
                if let Ok(mut guard) = state.0.lock() {
                    *guard = Some(child);
                }
            }

            // 3. Show a lightweight loading window immediately (label "loading"),
            //    then poll readiness off the main thread. On readiness we create
            //    the real dashboard window (label "main") pointed straight at the
            //    dashboard URL and close the loading window. We deliberately do
            //    NOT navigate the loading window to the dashboard: navigating a
            //    document.write'd about:blank webview does not reliably repaint
            //    (the resources load but the view stays on the loading page), so
            //    a window born at the dashboard URL is used instead. The child is
            //    killed only when the "main" window is destroyed (see below), so
            //    closing the loading window does not stop the runtime.
            let loading = WebviewWindowBuilder::new(
                &handle,
                "loading",
                WebviewUrl::App("about:blank".into()),
            )
            .title("Open Magi")
            .inner_size(1280.0, 860.0)
            .min_inner_size(960.0, 600.0)
            .build()?;
            let _ = loading.eval(format!(
                "document.open();document.write({});document.close();",
                serde_json::to_string(loading_page()).unwrap_or_else(|_| "''".into())
            ));

            std::thread::spawn(move || {
                let phase = wait_until_ready(port);
                match phase {
                    Phase::Ready => open_dashboard_window(&handle, port),
                    Phase::Failed(reason) => show_error(&handle, &reason),
                    // wait_until_ready only returns terminal phases.
                    _ => show_error(&handle, "unexpected startup state"),
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            // The dashboard runs in the "main" window; kill the child only when
            // THAT window is destroyed (the user closed the app). The transient
            // "loading" window closing during the handoff must not stop serve.
            if let tauri::WindowEvent::Destroyed = event {
                if window.label() == "main" {
                    if let Some(state) = window.try_state::<ServeProcess>() {
                        state.shutdown();
                    }
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build the Open Magi desktop app")
        .run(|app_handle, event| {
            if let tauri::RunEvent::ExitRequested { .. } = event {
                if let Some(state) = app_handle.try_state::<ServeProcess>() {
                    state.shutdown();
                }
            }
        });
}

/// Create the dashboard window (label "main") pointed straight at the dashboard
/// URL, install the navigation guards, then close the transient loading window.
/// A window born at the target URL repaints reliably, unlike navigating a
/// document.write'd about:blank webview. The child is killed when this "main"
/// window is destroyed, so closing "loading" right after is safe.
fn open_dashboard_window(handle: &tauri::AppHandle, port: u16) {
    let dashboard = format!("http://127.0.0.1:{port}/dashboard");
    let url = match dashboard.parse() {
        Ok(u) => u,
        Err(_) => {
            show_error(handle, "internal: dashboard URL was not parseable");
            return;
        }
    };
    let guard_handle = handle.clone();
    let new_window_handle = handle.clone();
    let built = WebviewWindowBuilder::new(handle, "main", WebviewUrl::External(url))
        .title("Open Magi")
        .inner_size(1280.0, 860.0)
        .min_inner_size(960.0, 600.0)
        .on_navigation(move |target| match classify(target.as_str(), port) {
            UrlClass::InApp => true,
            UrlClass::External => {
                use tauri_plugin_opener::OpenerExt;
                let _ = guard_handle
                    .opener()
                    .open_url(target.as_str(), None::<&str>);
                false
            }
            UrlClass::Invalid => false,
        })
        // Guard window.open / target=_blank / programmatic new webviews: deny
        // every new window (single-window shell) and route the URL through the
        // same policy. Fail-closed.
        .on_new_window(move |requested, _features| {
            match classify(requested.as_str(), port) {
                UrlClass::External => {
                    use tauri_plugin_opener::OpenerExt;
                    let _ = new_window_handle
                        .opener()
                        .open_url(requested.as_str(), None::<&str>);
                }
                UrlClass::InApp => {
                    if let Some(main) = new_window_handle.get_webview_window("main") {
                        let _ = main.navigate(requested.clone());
                    }
                }
                UrlClass::Invalid => {}
            }
            NewWindowResponse::Deny
        })
        .build();
    match built {
        Ok(_) => {
            if let Some(loading) = handle.get_webview_window("loading") {
                let _ = loading.close();
            }
        }
        Err(e) => show_error(handle, &format!("could not open the dashboard window: {e}")),
    }
}

/// Render the static error page in the main window (creating it if needed).
fn show_error(handle: &tauri::AppHandle, reason: &str) {
    let html = error_page(reason);
    let script = format!(
        "document.open();document.write({});document.close();",
        serde_json::to_string(&html).unwrap_or_else(|_| "''".into())
    );
    // Render into whichever window is currently up: the "loading" window during
    // startup, or the "main" window once the dashboard is open.
    for label in ["loading", "main"] {
        if let Some(win) = handle.get_webview_window(label) {
            let _ = win.eval(&script);
            return;
        }
    }
    if let Ok(win) =
        WebviewWindowBuilder::new(handle, "loading", WebviewUrl::App("about:blank".into()))
            .title("Open Magi")
            .inner_size(1280.0, 860.0)
            .build()
    {
        let _ = win.eval(&script);
    }
}
