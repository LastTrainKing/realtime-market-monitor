//! 测试辅助工具。
//!
//! # 为什么不用 `tempfile::tempdir()`
//!
//! 系统临时目录（Windows 的 `%TEMP%`）在受控执行环境中可能被限制访问，
//! 导致"文件写入成功、但 DuckDB / 其它读取方拿不到"的假失败。
//! 因此测试统一使用**工作区内部**的临时目录。

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

static COUNTER: AtomicU64 = AtomicU64::new(0);

/// 工作区内的临时目录，Drop 时自动清理
pub struct WorkspaceTempDir {
    path: PathBuf,
}

impl WorkspaceTempDir {
    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn join(&self, p: impl AsRef<Path>) -> PathBuf {
        self.path.join(p)
    }
}

impl Drop for WorkspaceTempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

/// 在 `<workspace>/backend/.test-tmp/<name>-<seq>` 下创建一个临时目录
pub fn workspace_temp_dir(name: &str) -> WorkspaceTempDir {
    let base = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".test-tmp");
    let seq = COUNTER.fetch_add(1, Ordering::Relaxed);
    let path = base.join(format!("{name}-{}-{seq}", std::process::id()));
    let _ = std::fs::create_dir_all(&path);
    WorkspaceTempDir { path }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn creates_and_cleans_up() {
        let dir = workspace_temp_dir("selftest");
        assert!(dir.path().exists());
        let p = dir.path().to_path_buf();
        std::fs::write(dir.join("a.txt"), b"x").unwrap();
        assert!(p.join("a.txt").exists());
        drop(dir);
        assert!(!p.exists(), "Drop 后应自动清理");
    }

    #[test]
    fn unique_per_call() {
        let a = workspace_temp_dir("uniq");
        let b = workspace_temp_dir("uniq");
        assert_ne!(a.path(), b.path());
    }
}
