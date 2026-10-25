//! 本地 API 安全边界（第 6.15 / 12.11 章）。
//!
//! 本程序只监听 `127.0.0.1`，暴露面已经很小，但仍然存在一个现实风险：
//! 用户浏览器里的任意网页都可能尝试访问 `http://127.0.0.1:<port>/api/*`。
//! 一旦存在 `POST /api/shutdown`、`PUT /api/config` 这类写接口且没有鉴权，
//! 恶意页面就能通过简单请求对本地软件造成影响。
//!
//! 因此：
//!   1. 启动时生成一次性随机 token，写入用户目录下的 `cache/token`；
//!   2. 除 `/api/health` 外的所有接口都必须携带 `X-CF-Token`；
//!   3. CORS 只允许本程序自己的前端来源，不再使用 `Any`。

use std::path::Path;

use crate::paths::AppPaths;

/// 生成 128 位随机 token（使用标准库中由操作系统熵源播种的 RandomState）
pub fn generate_token() -> String {
    use std::hash::{BuildHasher, Hasher};

    let mut out = String::with_capacity(32);
    for round in 0..2u64 {
        let mut hasher = std::collections::hash_map::RandomState::new().build_hasher();
        hasher.write_u64(round);
        hasher.write_u64(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos() as u64)
                .unwrap_or(0),
        );
        hasher.write_u32(std::process::id());
        hasher.write_usize(&out as *const _ as usize);
        out.push_str(&format!("{:016x}", hasher.finish()));
    }
    out
}

/// 写入 token 文件（供 Tauri 外壳与开发代理读取）
pub fn write_token(paths: &AppPaths, token: &str) -> std::io::Result<()> {
    let file = paths.token_file();
    if let Some(parent) = file.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(file, token)
}

/// 读取 token 文件
pub fn read_token(paths: &AppPaths) -> Option<String> {
    std::fs::read_to_string(paths.token_file())
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// 从任意路径读取 token（供外壳在不知道 AppPaths 时使用）
pub fn read_token_from(path: &Path) -> Option<String> {
    std::fs::read_to_string(path)
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// 允许的前端来源（CORS 白名单）
///
/// 覆盖 Tauri 生产环境（`tauri://localhost` / `http://tauri.localhost`）
/// 与 Vite 开发服务器，不再使用 `Any`。
pub fn allowed_origins(dev_port: u16) -> Vec<String> {
    vec![
        "tauri://localhost".to_string(),
        "http://tauri.localhost".to_string(),
        "https://tauri.localhost".to_string(),
        format!("http://localhost:{dev_port}"),
        format!("http://127.0.0.1:{dev_port}"),
    ]
}

/// 免鉴权路径（健康检查用于启动探测）
pub fn is_public_path(path: &str) -> bool {
    matches!(path, "/api/health")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn token_is_random_and_long_enough() {
        let a = generate_token();
        let b = generate_token();
        assert_eq!(a.len(), 32, "token 应为 32 位十六进制");
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b, "两次生成的 token 不应相同");
    }

    #[test]
    fn token_roundtrip() {
        let tmp = tempfile::tempdir().unwrap();
        let paths = AppPaths {
            root: tmp.path().to_path_buf(),
            data_dir: tmp.path().join("data"),
            db_path: tmp.path().join("data/db.sqlite"),
            logs_dir: tmp.path().join("logs"),
            config_dir: tmp.path().join("config"),
            cache_dir: tmp.path().join("cache"),
            install_dir: tmp.path().to_path_buf(),
            resources_dir: tmp.path().join("res"),
        };
        std::fs::create_dir_all(&paths.cache_dir).unwrap();
        let token = generate_token();
        write_token(&paths, &token).unwrap();
        assert_eq!(read_token(&paths).as_deref(), Some(token.as_str()));
    }

    #[test]
    fn health_is_public_others_are_not() {
        assert!(is_public_path("/api/health"));
        assert!(!is_public_path("/api/shutdown"));
        assert!(!is_public_path("/api/config"));
        assert!(!is_public_path("/api/watchlist"));
        assert!(!is_public_path("/api/dashboard"));
    }

    #[test]
    fn origins_are_explicit_not_wildcard() {
        let origins = allowed_origins(5183);
        assert!(origins.iter().all(|o| o != "*"));
        assert!(origins.iter().any(|o| o.contains("tauri.localhost")));
        assert!(origins.iter().any(|o| o.contains("5183")));
    }
}
