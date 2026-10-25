//! 单实例控制（第 3.4 / 6.15 / 12.11 章）。
//!
//! 方案：以“绑定本地端口”作为互斥体。
//!   * 端口绑定成功 → 本进程成为唯一实例
//!   * 端口已被占用且健康检查通过 → 已有实例在运行，本进程立即退出
//!   * 端口被其他程序占用 → 自动向后寻找可用端口，并把实际端口写入端口文件
//!
//! 同时写入 PID 锁文件，便于诊断与异常恢复。

use std::io::Write;
use std::net::{Ipv4Addr, SocketAddrV4, TcpListener};
use std::path::{Path, PathBuf};
use std::time::Duration;

#[derive(Debug)]
pub enum AcquireOutcome {
    /// 成功绑定，成为唯一实例
    Bound { listener: TcpListener, port: u16 },
    /// 已有实例正在运行
    AlreadyRunning { port: u16 },
}

pub fn port_file(paths: &crate::paths::AppPaths) -> PathBuf {
    paths.cache_dir.join("port")
}

pub fn lock_file(paths: &crate::paths::AppPaths) -> PathBuf {
    paths.root.join("backend.lock")
}

/// 尝试获取单实例所有权
pub fn acquire(paths: &crate::paths::AppPaths, preferred: u16) -> AcquireOutcome {
    let candidates: Vec<u16> = (preferred..preferred.saturating_add(20)).collect();
    let mut first_in_use: Option<u16> = None;

    for port in candidates {
        let addr = SocketAddrV4::new(Ipv4Addr::LOCALHOST, port);
        match TcpListener::bind(addr) {
            Ok(listener) => {
                let _ = listener.set_nonblocking(false);
                write_pid(paths, port);
                let _ = write_port_file(paths, port);
                tracing::info!(target: "app", port, "单实例检查通过，已绑定本地端口");
                return AcquireOutcome::Bound { listener, port };
            }
            Err(err) => {
                if first_in_use.is_none() {
                    first_in_use = Some(port);
                }
                tracing::debug!(target: "app", port, error = %err, "端口被占用");
                // 该端口是否已有本软件的实例？
                if health_ok(port) {
                    tracing::info!(target: "app", port, "检测到已有实例正在运行，复用该实例");
                    return AcquireOutcome::AlreadyRunning { port };
                }
            }
        }
    }

    // 全部候选端口被占用但不是本软件：直接返回占用端口，由上层处理
    AcquireOutcome::AlreadyRunning {
        port: first_in_use.unwrap_or(preferred),
    }
}

/// 同步健康检查（启动阶段使用，避免依赖 tokio）
pub fn health_ok(port: u16) -> bool {
    use std::io::{Read, Write as _};
    let addr = SocketAddrV4::new(Ipv4Addr::LOCALHOST, port);
    let Ok(mut stream) = std::net::TcpStream::connect_timeout(
        &std::net::SocketAddr::V4(addr),
        Duration::from_millis(600),
    ) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(800)));
    let req = format!(
        "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = String::new();
    if stream.read_to_string(&mut buf).is_err() {
        return false;
    }
    buf.contains("\"status\":\"ok\"") || buf.contains("200 OK")
}

fn write_pid(paths: &crate::paths::AppPaths, port: u16) {
    let pid = std::process::id();
    let content = format!(
        "pid={pid}\nport={port}\nstarted_at={}\n",
        crate::models::now_str()
    );
    let _ = write_file(&lock_file(paths), &content);
}

fn write_port_file(paths: &crate::paths::AppPaths, port: u16) -> std::io::Result<()> {
    write_file(&port_file(paths), &port.to_string())
}

fn write_file(path: &Path, content: &str) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut f = std::fs::File::create(path)?;
    f.write_all(content.as_bytes())?;
    f.flush()
}

/// 读取端口文件（启动器使用）
pub fn read_port_file(paths: &crate::paths::AppPaths) -> Option<u16> {
    std::fs::read_to_string(port_file(paths))
        .ok()
        .and_then(|s| s.trim().parse().ok())
}

pub fn cleanup(paths: &crate::paths::AppPaths) {
    let _ = std::fs::remove_file(lock_file(paths));
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::paths::AppPaths;

    fn paths(dir: &Path) -> AppPaths {
        let p = AppPaths {
            root: dir.to_path_buf(),
            data_dir: dir.join("data"),
            db_path: dir.join("data/db.sqlite"),
            logs_dir: dir.join("logs"),
            config_dir: dir.join("config"),
            cache_dir: dir.join("cache"),
            install_dir: dir.to_path_buf(),
            resources_dir: dir.join("res"),
        };
        std::fs::create_dir_all(&p.cache_dir).unwrap();
        p
    }

    #[test]
    fn binds_and_writes_port_file() {
        let tmp = tempfile::tempdir().unwrap();
        let p = paths(tmp.path());
        // 使用高位端口避免与环境冲突
        let outcome = acquire(&p, 39_911);
        match outcome {
            AcquireOutcome::Bound { listener, port } => {
                assert_eq!(port, 39_911);
                assert_eq!(read_port_file(&p), Some(39_911));
                assert!(lock_file(&p).exists());
                let content = std::fs::read_to_string(lock_file(&p)).unwrap();
                assert!(content.contains("pid="));
                drop(listener);
            }
            AcquireOutcome::AlreadyRunning { .. } => panic!("端口 39911 意外被占用"),
        }
    }

    #[test]
    fn second_acquire_detects_running_instance_when_health_responds() {
        let tmp = tempfile::tempdir().unwrap();
        let p = paths(tmp.path());
        // 模拟一个监听 39912 并返回健康检查的服务
        let listener = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 39_912)).unwrap();
        listener.set_nonblocking(true).unwrap();
        let handle = std::thread::spawn(move || {
            use std::io::{ErrorKind, Read, Write};
            let deadline = std::time::Instant::now() + Duration::from_secs(4);
            while std::time::Instant::now() < deadline {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        let _ = stream.set_read_timeout(Some(Duration::from_millis(400)));
                        let mut buf = [0u8; 512];
                        let _ = stream.read(&mut buf);
                        let body = r#"{"status":"ok"}"#;
                        let resp = format!(
                            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                            body.len(),
                            body
                        );
                        let _ = stream.write_all(resp.as_bytes());
                    }
                    Err(ref e) if e.kind() == ErrorKind::WouldBlock => {
                        std::thread::sleep(Duration::from_millis(15));
                    }
                    Err(_) => break,
                }
            }
        });

        let outcome = acquire(&p, 39_912);
        match outcome {
            AcquireOutcome::AlreadyRunning { port } => assert_eq!(port, 39_912),
            AcquireOutcome::Bound { .. } => panic!("应检测到已有实例"),
        }
        let _ = handle.join();
    }

    #[test]
    fn falls_back_to_next_port_when_occupied_by_other_app() {
        let tmp = tempfile::tempdir().unwrap();
        let p = paths(tmp.path());
        // 占用 39913，且不响应健康检查（模拟其他程序）
        let blocker = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 39_913)).unwrap();
        let outcome = acquire(&p, 39_913);
        match outcome {
            AcquireOutcome::Bound { port, .. } => {
                assert!(port > 39_913, "应自动向后寻找可用端口，实际 {port}");
                assert_eq!(read_port_file(&p), Some(port));
            }
            AcquireOutcome::AlreadyRunning { .. } => panic!("应换端口而不是判定为已有实例"),
        }
        drop(blocker);
    }

    #[test]
    fn health_check_fails_for_closed_port() {
        assert!(!health_ok(39_999));
    }
}
