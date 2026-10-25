//! 日志系统（第 6.11 章）。
//!
//! 分类落盘：app.log / backend.log / network.log / error.log
//! 生产环境不向控制台输出（避免出现 CMD 窗口）。

use std::path::Path;
use tracing_appender::non_blocking::WorkerGuard;
use tracing_subscriber::filter::filter_fn;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;
use tracing_subscriber::{fmt, EnvFilter, Layer};

pub struct LogGuards {
    _guards: Vec<WorkerGuard>,
}

pub fn init(logs_dir: &Path, console: bool, level: &str) -> LogGuards {
    let _ = std::fs::create_dir_all(logs_dir);

    let env_filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new(format!("{level},hyper=warn,reqwest=warn")));

    let mut guards = Vec::new();

    let (app_writer, g1) = tracing_appender::non_blocking(tracing_appender::rolling::daily(logs_dir, "app.log"));
    guards.push(g1);
    let (backend_writer, g2) =
        tracing_appender::non_blocking(tracing_appender::rolling::daily(logs_dir, "backend.log"));
    guards.push(g2);
    let (net_writer, g3) =
        tracing_appender::non_blocking(tracing_appender::rolling::daily(logs_dir, "network.log"));
    guards.push(g3);
    let (err_writer, g4) =
        tracing_appender::non_blocking(tracing_appender::rolling::daily(logs_dir, "error.log"));
    guards.push(g4);

    let app_layer = fmt::layer()
        .with_writer(app_writer)
        .with_ansi(false)
        .with_target(true)
        .with_thread_ids(false)
        .with_file(false)
        .with_line_number(false)
        .compact()
        .with_filter(env_filter.clone());

    let backend_layer = fmt::layer()
        .with_writer(backend_writer)
        .with_ansi(false)
        .with_target(true)
        .compact()
        .with_filter(filter_fn(|meta| {
            let t = meta.target();
            t.starts_with("capital_backend") || t.starts_with("app") || t.starts_with("collector")
        }))
        .with_filter(env_filter.clone());

    let network_layer = fmt::layer()
        .with_writer(net_writer)
        .with_ansi(false)
        .with_target(true)
        .compact()
        .with_filter(filter_fn(|meta| meta.target().starts_with("network")))
        .with_filter(env_filter.clone());

    let error_layer = fmt::layer()
        .with_writer(err_writer)
        .with_ansi(false)
        .with_target(true)
        .with_file(true)
        .with_line_number(true)
        .compact()
        .with_filter(filter_fn(|meta| {
            *meta.level() == tracing::Level::ERROR || *meta.level() == tracing::Level::WARN
        }));

    let registry = tracing_subscriber::registry()
        .with(app_layer)
        .with(backend_layer)
        .with(network_layer)
        .with(error_layer);

    if console {
        let console_layer = fmt::layer()
            .with_writer(std::io::stdout)
            .with_ansi(true)
            .compact()
            .with_filter(env_filter);
        registry.with(console_layer).init();
    } else {
        registry.init();
    }

    tracing::info!(target: "app", version = crate::config::APP_VERSION, "日志系统已启动");
    LogGuards { _guards: guards }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn init_creates_log_files_directory() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("logs");
        let guards = init(&dir, false, "info");
        tracing::info!(target: "app", "测试日志写入");
        tracing::warn!(target: "network", "测试网络日志");
        tracing::error!(target: "app", "测试错误日志");
        drop(guards);
        assert!(dir.exists(), "日志目录应被创建");
    }
}
