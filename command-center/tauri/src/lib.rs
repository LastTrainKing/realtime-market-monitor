// 资金流向指挥中心 — Tauri 桌面外壳。
//
// 职责边界（第 2.3 / 3.2 章）：
//   本进程只负责：窗口管理、生命周期、系统托盘、启动/关闭后端、EXE 封装。
//   禁止在本进程内执行股票计算、数据解析、网络请求、排名计算、数据缓存。

#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddrV4, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{Manager, RunEvent, WindowEvent};

const DEFAULT_PORT: u16 = 47821;
const PORT_SCAN: u16 = 20;

struct BackendProcess {
    child: Mutex<Option<Child>>,
    port: Mutex<u16>,
}

impl BackendProcess {
    fn new() -> Self {
        Self {
            child: Mutex::new(None),
            port: Mutex::new(DEFAULT_PORT),
        }
    }

    fn port(&self) -> u16 {
        *self.port.lock().unwrap()
    }

    fn set_port(&self, p: u16) {
        *self.port.lock().unwrap() = p;
    }

    fn stop(&self) {
        let mut guard = self.child.lock().unwrap();
        if let Some(mut child) = guard.take() {
            let port = self.port();
            // 先请求优雅退出（保存状态、释放数据库）
            let _ = http_post(&format!("http://127.0.0.1:{port}/api/shutdown"));
            let deadline = Instant::now() + Duration::from_secs(4);
            loop {
                match child.try_wait() {
                    Ok(Some(_)) => return,
                    Ok(None) => {
                        if Instant::now() > deadline {
                            break;
                        }
                        std::thread::sleep(Duration::from_millis(120));
                    }
                    Err(_) => break,
                }
            }
            // 兜底：仍未退出则结束进程（避免残留后台幽灵进程）
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn http_post(url: &str) -> std::io::Result<String> {
    let rest = url.trim_start_matches("http://");
    let (host_port, path) = rest.split_once('/').unwrap_or((rest, ""));
    let (host, port) = host_port.split_once(':').unwrap_or((host_port, "80"));
    let port: u16 = port.parse().unwrap_or(80);
    let mut stream = TcpStream::connect_timeout(
        &std::net::SocketAddr::V4(SocketAddrV4::new(
            host.parse::<Ipv4Addr>().unwrap_or(Ipv4Addr::LOCALHOST),
            port,
        )),
        Duration::from_millis(1500),
    )?;
    stream.set_read_timeout(Some(Duration::from_millis(1500)))?;
    let req = format!(
        "POST /{path} HTTP/1.1\r\nHost: {host}:{port}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(req.as_bytes())?;
    let mut buf = String::new();
    let _ = stream.read_to_string(&mut buf);
    Ok(buf)
}

fn health_ok(port: u16) -> bool {
    let addr = SocketAddrV4::new(Ipv4Addr::LOCALHOST, port);
    let Ok(mut stream) = TcpStream::connect_timeout(
        &std::net::SocketAddr::V4(addr),
        Duration::from_millis(700),
    ) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(900)));
    let req = format!("GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
    if stream.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = String::new();
    let _ = stream.read_to_string(&mut buf);
    buf.contains("\"status\":\"ok\"")
}

/// 数据目录（与后端保持一致：%APPDATA%\CapitalFlowCenter）
fn data_root() -> PathBuf {
    if let Ok(custom) = std::env::var("CAPITAL_FLOW_HOME") {
        return PathBuf::from(custom);
    }
    if let Ok(appdata) = std::env::var("APPDATA") {
        return PathBuf::from(appdata).join("CapitalFlowCenter");
    }
    if let Ok(home) = std::env::var("USERPROFILE") {
        return PathBuf::from(home).join(".capital-flow-center");
    }
    PathBuf::from(".")
}

fn read_port_file() -> Option<u16> {
    std::fs::read_to_string(data_root().join("cache").join("port"))
        .ok()
        .and_then(|s| s.trim().parse().ok())
}

/// 读取后端写入的本地 API 令牌（用于前端鉴权）
fn read_token_file() -> Option<String> {
    std::fs::read_to_string(data_root().join("cache").join("token"))
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// 等待令牌文件出现（后端启动与前端首次请求之间存在竞态）
fn wait_token_file(timeout: Duration) -> Option<String> {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(t) = read_token_file() {
            return Some(t);
        }
        if Instant::now() >= deadline {
            return None;
        }
        std::thread::sleep(Duration::from_millis(150));
    }
}

/// 定位后端可执行文件
fn resolve_backend_exe(app: &tauri::AppHandle) -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();

    // 1) 安装目录下的 backend/
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("backend").join("capital_backend.exe"));
            candidates.push(dir.join("capital_backend.exe"));
            // 开发布局：target/release/ -> 项目根/backend/target/release/
            candidates.push(dir.join("..").join("..").join("backend").join("target").join("release").join("capital_backend.exe"));
            candidates.push(dir.join("..").join("..").join("backend").join("target").join("debug").join("capital_backend.exe"));
        }
    }
    // 2) 资源目录
    if let Ok(res) = app.path().resource_dir() {
        candidates.push(res.join("backend").join("capital_backend.exe"));
        candidates.push(res.join("capital_backend.exe"));
    }

    candidates.into_iter().find(|p| p.exists())
}

fn spawn_backend(exe: &Path, port: u16) -> std::io::Result<Child> {
    let mut cmd = Command::new(exe);
    cmd.arg("--port")
        .arg(port.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .current_dir(exe.parent().unwrap_or(Path::new(".")));

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // CREATE_NO_WINDOW：用户永远看不到 CMD / 日志窗口（第 6.6 章）
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        const DETACHED_PROCESS: u32 = 0x0000_0008;
        cmd.creation_flags(CREATE_NO_WINDOW | DETACHED_PROCESS);
    }

    cmd.spawn()
}

/// 确保后端在运行，返回可用端口
fn ensure_backend(app: &tauri::AppHandle, state: &BackendProcess) -> u16 {
    // 已有实例（含用户此前未关闭的实例）→ 直接复用
    if let Some(p) = read_port_file() {
        if health_ok(p) {
            eprintln!("[shell] 检测到已在运行的后端，直接复用，端口 {p}");
            state.set_port(p);
            return p;
        }
    }

    let Some(exe) = resolve_backend_exe(app) else {
        eprintln!("[shell] 未找到后端程序 capital_backend.exe");
        return state.port();
    };

    // 只启动一次：端口由后端自行选择并写入端口文件，
    // 避免外壳循环重试导致出现多个后端进程（第 3.4 章 单实例要求）
    match spawn_backend(&exe, DEFAULT_PORT) {
        Ok(child) => {
            *state.child.lock().unwrap() = Some(child);
        }
        Err(err) => {
            eprintln!("[shell] 后端启动失败：{err}");
            return state.port();
        }
    }

    // 轮询端口文件 / 端口扫描，等待后端 HTTP 服务就绪
    let deadline = Instant::now() + Duration::from_secs(90);
    while Instant::now() < deadline {
        if let Some(p) = read_port_file() {
            if health_ok(p) {
                state.set_port(p);
                eprintln!("[shell] 后端就绪，端口 {p}");
                return p;
            }
        }
        for offset in 0..PORT_SCAN {
            let p = DEFAULT_PORT + offset;
            if health_ok(p) {
                state.set_port(p);
                eprintln!("[shell] 后端就绪，端口 {p}");
                return p;
            }
        }
        std::thread::sleep(Duration::from_millis(400));
    }
    eprintln!("[shell] 等待后端就绪超时，前端将自动重试");
    state.port()
}

#[tauri::command]
fn backend_port(state: tauri::State<'_, BackendProcess>) -> u16 {
    state.port()
}

#[tauri::command]
fn backend_ready(state: tauri::State<'_, BackendProcess>) -> bool {
    health_ok(state.port())
}

/// 返回本地 API 访问令牌。
///
/// 后端启动时生成随机令牌并写入 `%APPDATA%\CapitalFlowCenter\cache\token`，
/// 前端必须携带该令牌才能调用除 /api/health 外的接口 ——
/// 这样即使浏览器中的其它网页知道了端口，也无法操作本软件。
#[tauri::command]
fn backend_token() -> String {
    // 允许前端在启动竞态下重试：最多等待 10 秒令牌文件出现
    wait_token_file(Duration::from_secs(10)).unwrap_or_default()
}

#[tauri::command]
fn restart_backend(app: tauri::AppHandle, state: tauri::State<'_, BackendProcess>) -> u16 {
    state.stop();
    std::thread::sleep(Duration::from_millis(400));
    ensure_backend(&app, &state)
}

#[tauri::command]
fn quit_app(app: tauri::AppHandle) {
    app.exit(0);
}

pub fn run() {
    let backend = BackendProcess::new();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .manage(backend)
        .invoke_handler(tauri::generate_handler![
            backend_port,
            backend_ready,
            backend_token,
            restart_backend,
            quit_app
        ])
        .setup(|app| {
            let handle = app.handle().clone();

            // 启动后端（后台线程，避免阻塞窗口出现）
            let h2 = handle.clone();
            std::thread::spawn(move || {
                let st = h2.state::<BackendProcess>();
                let port = ensure_backend(&h2, &st);
                eprintln!("[shell] 后端端口 {port}");

                // 后端就绪后把令牌注入页面：即使前端首次解析失败（启动竞态），
                // 下一次轮询也能拿到令牌，不会一直 401
                if let Some(token) = wait_token_file(Duration::from_secs(15)) {
                    if let Some(win) = h2.get_webview_window("main") {
                        let script = format!(
                            "window.__CF_TOKEN__ = '{}';",
                            token.replace('\\', "\\\\").replace('\'', "\\'")
                        );
                        let _ = win.eval(&script);
                    }
                }
            });

            // 真正全屏（第 5.10 章：不能只依赖配置文件）
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.set_decorations(false);
                let _ = win.set_fullscreen(true);
                let _ = win.show();
                let _ = win.set_focus();
            }

            // 系统托盘
            let show = MenuItem::with_id(app, "show", "打开主界面", true, None::<&str>)?;
            let restart = MenuItem::with_id(app, "restart", "重启数据服务", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &restart, &quit])?;

            let _tray = TrayIconBuilder::with_id("main-tray")
                .icon(app.default_window_icon().cloned().unwrap())
                .tooltip("资金流向指挥中心")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(move |app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(win) = app.get_webview_window("main") {
                            let _ = win.show();
                            let _ = win.unminimize();
                            let _ = win.set_fullscreen(true);
                            let _ = win.set_focus();
                        }
                    }
                    "restart" => {
                        let st = app.state::<BackendProcess>();
                        st.stop();
                        std::thread::sleep(Duration::from_millis(500));
                        let h = app.clone();
                        std::thread::spawn(move || {
                            let st = h.state::<BackendProcess>();
                            let p = ensure_backend(&h, &st);
                            eprintln!("[shell] 后端已重启，端口 {p}");
                        });
                    }
                    "quit" => {
                        let st = app.state::<BackendProcess>();
                        st.stop();
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let tauri::tray::TrayIconEvent::DoubleClick { .. } = event {
                        let app = tray.app_handle();
                        if let Some(win) = app.get_webview_window("main") {
                            let _ = win.show();
                            let _ = win.unminimize();
                            let _ = win.set_focus();
                        }
                    }
                })
                .build(app)?;

            Ok(())
        })
        .on_window_event(|window, event| {
            // 关闭按钮：交给前端确认；若直接关闭则一并停止后端（避免幽灵进程）
            if let WindowEvent::CloseRequested { .. } = event {
                let app = window.app_handle();
                let st = app.state::<BackendProcess>();
                st.stop();
            }
        })
        .build(tauri::generate_context!())
        .expect("Tauri 应用构建失败")
        .run(|app, event| {
            if let RunEvent::ExitRequested { .. } = event {
                let st = app.state::<BackendProcess>();
                st.stop();
            }
        });
}
