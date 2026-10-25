//! 全局配置（Config Driven Framework，第 4.16 / 0.3 章）。
//!
//! 所有可调参数（刷新频率、限流、榜单定义、节点、股票池规模）均来自配置，
//! 九榜系统完全由 `boards` 数组驱动，新增榜单无需修改核心代码。

use serde::{Deserialize, Serialize};
use std::path::Path;

use crate::paths::AppPaths;

pub const APP_VERSION: &str = env!("CARGO_PKG_VERSION");

fn d_interval_snapshot() -> u64 {
    15
}
fn d_interval_index() -> u64 {
    3
}
fn d_interval_sector() -> u64 {
    10
}
fn d_interval_market() -> u64 {
    20
}
fn d_interval_minute_persist() -> u64 {
    60
}
fn d_top_n() -> usize {
    10
}
fn d_top_open() -> usize {
    5
}
fn d_rps() -> u32 {
    4
}
fn d_rpm() -> u32 {
    180
}
fn d_max_concurrent() -> usize {
    4
}
fn d_retry_max() -> u32 {
    4
}
fn d_breaker_threshold() -> u32 {
    5
}
fn d_breaker_cooldown() -> u64 {
    60
}
fn d_stock_batch() -> usize {
    300
}
fn d_rebuild_candidates() -> usize {
    400
}
fn d_rebuild_ratio() -> f64 {
    0.9
}
fn d_universe_batch() -> usize {
    600
}
fn d_history_days() -> i64 {
    90
}
fn d_cache_ttl() -> u64 {
    30
}
fn d_retention_days() -> u32 {
    30
}
fn d_port() -> u16 {
    47_821
}
fn d_true() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct RefreshConfig {
    /// 秒级：指数 / 大盘资金
    pub index_interval_secs: u64,
    /// 板块榜单刷新
    pub sector_interval_secs: u64,
    /// 全市场榜单（个股资金流/成交量/量比）
    pub market_interval_secs: u64,
    /// 全市场扫描周期（秒）：分页拉取全部 A 股，用于真实涨跌家数
    pub full_scan_interval_secs: u64,
    /// 快照落库周期
    pub snapshot_interval_secs: u64,
    /// 分钟数据落库周期
    pub minute_persist_secs: u64,
}

impl Default for RefreshConfig {
    fn default() -> Self {
        Self {
            index_interval_secs: d_interval_index(),
            sector_interval_secs: d_interval_sector(),
            market_interval_secs: d_interval_market(),
            full_scan_interval_secs: d_full_scan(),
            snapshot_interval_secs: d_interval_snapshot(),
            minute_persist_secs: d_interval_minute_persist(),
        }
    }
}

fn d_full_scan() -> u64 {
    90
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct NetConfig {
    /// 每秒最大请求数（全局限流）
    pub requests_per_second: u32,
    /// 每分钟最大请求数（全局限流）
    pub requests_per_minute: u32,
    /// 单域名并发上限
    pub max_concurrent: usize,
    /// 请求超时（秒）
    pub timeout_secs: u64,
    /// 连接超时（秒）
    pub connect_timeout_secs: u64,
    /// 最大重试次数（指数退避）
    pub retry_max: u32,
    /// 熔断连续失败阈值
    pub breaker_failure_threshold: u32,
    /// 熔断恢复等待（秒）
    pub breaker_cooldown_secs: u64,
    /// 单次请求最小间隔（毫秒），防止突发
    pub min_interval_ms: u64,
    /// 用户自定义代理（可选）
    pub proxy: Option<String>,
}

impl Default for NetConfig {
    fn default() -> Self {
        Self {
            requests_per_second: d_rps(),
            requests_per_minute: d_rpm(),
            max_concurrent: d_max_concurrent(),
            timeout_secs: 8,
            connect_timeout_secs: 4,
            retry_max: d_retry_max(),
            breaker_failure_threshold: d_breaker_threshold(),
            breaker_cooldown_secs: d_breaker_cooldown(),
            min_interval_ms: 60,
            proxy: None,
        }
    }
}

/// 榜单类型（九榜系统的“计算规则”）
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BoardKind {
    /// 主力净流入（金额）
    NetFlow,
    /// 主力净占比（%）
    NetRatio,
    /// 成交量（手）
    Volume,
    /// 成交额（元）
    Amount,
    /// 量比
    VolumeRatio,
    /// 换手率
    Turnover,
    /// 涨跌幅
    ChangePct,
    /// 综合强度（复合指标）
    Composite,
}

impl BoardKind {
    pub fn label(&self) -> &'static str {
        match self {
            BoardKind::NetFlow => "主力净流入",
            BoardKind::NetRatio => "主力净占比",
            BoardKind::Volume => "成交量",
            BoardKind::Amount => "成交额",
            BoardKind::VolumeRatio => "量比",
            BoardKind::Turnover => "换手率",
            BoardKind::ChangePct => "涨跌幅",
            BoardKind::Composite => "综合强度",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BoardScope {
    Stock,
    Sector,
}

/// 单条榜单定义（配置驱动，第 4.16 章）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BoardConfig {
    pub id: String,
    pub name: String,
    pub kind: BoardKind,
    pub scope: BoardScope,
    /// 榜单容量
    pub size: usize,
    pub enabled: bool,
    /// 排序方向：true = 降序（值越大越靠前）
    pub descending: bool,
}

impl BoardConfig {
    fn new(
        id: &str,
        name: &str,
        kind: BoardKind,
        scope: BoardScope,
        size: usize,
        descending: bool,
    ) -> Self {
        Self {
            id: id.to_string(),
            name: name.to_string(),
            kind,
            scope,
            size,
            enabled: true,
            descending,
        }
    }
}

/// 默认九榜（第 4.16 / 8.12 章）
pub fn default_boards() -> Vec<BoardConfig> {
    use BoardKind::*;
    use BoardScope::*;
    vec![
        BoardConfig::new("main_net_flow", "主力净流入", NetFlow, Stock, 10, true),
        BoardConfig::new("main_net_ratio", "主力净占比", NetRatio, Stock, 10, true),
        BoardConfig::new("volume", "成交量", Volume, Stock, 10, true),
        BoardConfig::new("amount", "成交额", Amount, Stock, 10, true),
        BoardConfig::new("volume_ratio", "量比", VolumeRatio, Stock, 10, true),
        BoardConfig::new("turnover", "换手率", Turnover, Stock, 10, true),
        BoardConfig::new("change_pct", "涨跌幅", ChangePct, Stock, 10, true),
        BoardConfig::new("sector_net_flow", "板块净流入", NetFlow, Sector, 10, true),
        BoardConfig::new("sector_change", "板块涨幅", ChangePct, Sector, 10, true),
    ]
}

fn d_boards() -> Vec<BoardConfig> {
    default_boards()
}
fn d_nodes() -> Vec<String> {
    vec!["09:35".into(), "09:40".into(), "09:45".into()]
}
fn d_holidays() -> Vec<String> {
    crate::clock::builtin_holidays()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub version: String,
    /// 本地 HTTP 端口
    pub port: u16,
    /// 开机自动刷新是否启用
    pub auto_refresh: bool,
    /// 启动时是否自动进入全屏
    pub fullscreen: bool,
    /// 关闭按钮是否二次确认
    pub confirm_on_exit: bool,
    pub refresh: RefreshConfig,
    pub net: NetConfig,
    /// 九榜定义
    pub boards: Vec<BoardConfig>,
    /// 早盘节点（HH:MM）
    pub nodes: Vec<String>,
    /// 榜单容量
    pub top_n: usize,
    /// 开盘检测榜容量
    pub top_open: usize,
    /// 休市日（YYYY-MM-DD），与内置表合并
    pub holidays: Vec<String>,
    /// 股票扫描分批大小
    pub stock_batch_size: usize,
    /// 历史重建最多拉取的候选股票数（控制请求量；越大覆盖越全，重建越慢）
    pub rebuild_max_candidates: usize,
    /// 覆盖率低于该比例时，重建结果标记为 PARTIAL 而不是 REBUILD
    pub rebuild_full_coverage_ratio: f64,
    /// 全市场批量行情单次请求携带的证券数量（新浪通道使用）
    pub universe_batch_size: usize,
    /// 历史分钟数据保留天数（30~90）
    pub history_days: i64,
    /// 历史分钟数据粒度（分钟）：1 / 5 / 15 / 30 / 60
    pub history_granularity: u32,
    /// 内存缓存默认 TTL（秒）
    pub cache_ttl_secs: u64,
    /// 历史数据保留天数
    pub retention_days: u32,
    /// 交易时段结束后是否继续低频采集（用于收盘复盘）
    pub collect_after_close: bool,
    /// 数据源启用开关
    pub enable_eastmoney: bool,
    pub enable_sina: bool,
    pub enable_tencent: bool,
    /// 自动更新预留（第 6.13 章）
    pub update_url: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            version: APP_VERSION.to_string(),
            port: d_port(),
            auto_refresh: true,
            fullscreen: d_true(),
            confirm_on_exit: true,
            refresh: RefreshConfig::default(),
            net: NetConfig::default(),
            boards: d_boards(),
            nodes: d_nodes(),
            top_n: d_top_n(),
            top_open: d_top_open(),
            holidays: d_holidays(),
            stock_batch_size: d_stock_batch(),
            rebuild_max_candidates: d_rebuild_candidates(),
            rebuild_full_coverage_ratio: d_rebuild_ratio(),
            universe_batch_size: d_universe_batch(),
            history_days: d_history_days(),
            history_granularity: 1,
            cache_ttl_secs: d_cache_ttl(),
            retention_days: d_retention_days(),
            collect_after_close: false,
            enable_eastmoney: true,
            enable_sina: true,
            enable_tencent: true,
            update_url: String::new(),
        }
    }
}

impl Config {
    /// 从磁盘加载；不存在则写入默认配置（首次启动流程，第 6.9 章）
    pub fn load_or_init(paths: &AppPaths) -> (Self, Vec<String>) {
        let mut notes = Vec::new();
        let file = paths.config_file();
        if file.exists() {
            match std::fs::read_to_string(&file) {
                Ok(raw) => match serde_json::from_str::<Config>(&raw) {
                    Ok(mut cfg) => {
                        let repaired = cfg.repair();
                        notes.extend(repaired);
                        if cfg.boards.iter().filter(|b| b.enabled).count() < 9 {
                            notes.push("榜单配置少于 9 条，已按默认九榜补齐".into());
                            cfg.boards = default_boards();
                        }
                        return (cfg, notes);
                    }
                    Err(err) => {
                        notes.push(format!("配置文件损坏（{err}），已恢复默认配置"));
                    }
                },
                Err(err) => notes.push(format!("配置文件不可读（{err}），已恢复默认配置")),
            }
        }
        let cfg = Config::default();
        if let Err(err) = cfg.save(paths) {
            notes.push(format!("默认配置写入失败：{err}"));
        } else {
            notes.push("已生成默认配置文件".into());
        }
        (cfg, notes)
    }

    fn repair(&mut self) -> Vec<String> {
        let mut notes = Vec::new();
        if self.port < 1024 {
            self.port = d_port();
            notes.push("端口非法，已重置为默认 47821".into());
        }
        if self.top_n == 0 || self.top_n > 100 {
            self.top_n = d_top_n();
        }
        if self.nodes.is_empty() {
            self.nodes = d_nodes();
            notes.push("早盘节点为空，已恢复 09:35/09:40/09:45".into());
        }
        if self.net.requests_per_second == 0 {
            self.net.requests_per_second = d_rps();
        }
        if self.net.retry_max > 8 {
            self.net.retry_max = d_retry_max();
        }
        if self.rebuild_max_candidates < 60 {
            self.rebuild_max_candidates = d_rebuild_candidates();
        }
        if !(0.1..=1.0).contains(&self.rebuild_full_coverage_ratio) {
            self.rebuild_full_coverage_ratio = d_rebuild_ratio();
        }
        if self.universe_batch_size < 100 || self.universe_batch_size > 800 {
            self.universe_batch_size = d_universe_batch();
        }
        if !(30..=365).contains(&self.history_days) {
            self.history_days = d_history_days();
        }
        if crate::history::Granularity::from_minutes(self.history_granularity).is_none() {
            self.history_granularity = 1;
        }
        notes
    }

    pub fn save(&self, paths: &AppPaths) -> std::io::Result<()> {
        let file = paths.config_file();
        if let Some(parent) = file.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let raw = serde_json::to_string_pretty(self).unwrap_or_default();
        std::fs::write(file, raw)
    }

    pub fn load_from(path: &Path) -> Option<Config> {
        let raw = std::fs::read_to_string(path).ok()?;
        serde_json::from_str(&raw).ok()
    }

    pub fn enabled_boards(&self) -> Vec<BoardConfig> {
        self.boards.iter().filter(|b| b.enabled).cloned().collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_config_is_valid() {
        let cfg = Config::default();
        assert_eq!(cfg.boards.len(), 9, "默认必须提供九榜");
        assert_eq!(cfg.nodes, vec!["09:35", "09:40", "09:45"]);
        assert!(cfg.net.requests_per_second > 0);
        assert!(cfg.port > 1024);
    }

    #[test]
    fn roundtrip_serialization() {
        let cfg = Config::default();
        let raw = serde_json::to_string(&cfg).unwrap();
        let back: Config = serde_json::from_str(&raw).unwrap();
        assert_eq!(back.boards.len(), 9);
        assert_eq!(back.port, cfg.port);
    }

    #[test]
    fn repair_fixes_bad_values() {
        let mut cfg = Config::default();
        cfg.port = 80;
        cfg.top_n = 0;
        cfg.nodes.clear();
        let notes = cfg.repair();
        assert!(!notes.is_empty());
        assert_eq!(cfg.port, 47821);
        assert_eq!(cfg.top_n, 10);
        assert_eq!(cfg.nodes.len(), 3);
    }
}
