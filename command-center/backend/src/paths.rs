//! 应用路径解析。
//!
//! 生产级目录约定（第 6.7 / 6.8 章）：
//!   * 程序文件位于安装目录（只读），例如 `C:\Program Files\CapitalFlowCenter\`
//!   * 用户数据位于 `%APPDATA%\CapitalFlowCenter\`（可写），避免 Windows 权限问题

use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
pub struct AppPaths {
    /// 可写数据根目录
    pub root: PathBuf,
    pub data_dir: PathBuf,
    pub db_path: PathBuf,
    pub logs_dir: PathBuf,
    pub config_dir: PathBuf,
    pub cache_dir: PathBuf,
    /// 程序安装目录（只读资源）
    pub install_dir: PathBuf,
    pub resources_dir: PathBuf,
}

impl AppPaths {
    pub fn resolve() -> Self {
        let exe = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("."));
        let install_dir = exe
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| PathBuf::from("."));

        // 优先级：环境变量 > %APPDATA% > 安装目录旁 data/
        let root = if let Ok(custom) = std::env::var("CAPITAL_FLOW_HOME") {
            PathBuf::from(custom)
        } else if let Ok(appdata) = std::env::var("APPDATA") {
            PathBuf::from(appdata).join("CapitalFlowCenter")
        } else if let Ok(home) = std::env::var("USERPROFILE") {
            PathBuf::from(home).join(".capital-flow-center")
        } else {
            install_dir.join("data")
        };

        let resources_dir = {
            let packaged = install_dir.join("resources");
            if packaged.exists() {
                packaged
            } else {
                install_dir.join("..").join("resources")
            }
        };

        let paths = Self {
            data_dir: root.join("data"),
            db_path: root.join("data").join("database.sqlite"),
            logs_dir: root.join("logs"),
            config_dir: root.join("config"),
            cache_dir: root.join("cache"),
            resources_dir,
            install_dir,
            root,
        };
        paths.ensure_dirs();
        paths
    }

    fn ensure_dirs(&self) {
        for dir in [
            &self.root,
            &self.data_dir,
            &self.logs_dir,
            &self.config_dir,
            &self.cache_dir,
        ] {
            if let Err(err) = std::fs::create_dir_all(dir) {
                eprintln!("[paths] 创建目录失败 {}: {err}", dir.display());
            }
        }
    }

    pub fn config_file(&self) -> PathBuf {
        self.config_dir.join("config.json")
    }

    pub fn state_file(&self) -> PathBuf {
        self.cache_dir.join("last_state.json")
    }

    pub fn version_file(&self) -> PathBuf {
        self.config_dir.join("version.json")
    }

    /// 历史分钟数据根目录（Parquet 列式存储）
    pub fn history_dir(&self) -> PathBuf {
        self.data_dir.join("history")
    }

    /// 本地 API 访问令牌文件（仅本机用户可读）
    pub fn token_file(&self) -> PathBuf {
        self.cache_dir.join("token")
    }

    pub fn log_file(&self, name: &str) -> PathBuf {
        self.logs_dir.join(name)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolves_and_creates_directories() {
        let tmp = tempfile::tempdir().unwrap();
        unsafe {
            std::env::set_var("CAPITAL_FLOW_HOME", tmp.path());
        }
        let paths = AppPaths::resolve();
        assert!(paths.data_dir.exists());
        assert!(paths.logs_dir.exists());
        assert!(paths.config_dir.exists());
        assert!(paths.db_path.ends_with("database.sqlite"));
        unsafe {
            std::env::remove_var("CAPITAL_FLOW_HOME");
        }
    }
}
