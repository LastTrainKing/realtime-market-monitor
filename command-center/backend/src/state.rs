//! 应用共享状态。
//!
//! 所有业务组件在此装配，Axum 处理器只读取状态，不包含业务逻辑
//! （第 2.2 / 9.6 章：业务逻辑禁止写入 React / Tauri Command，也禁止写入路由层）。

use dashmap::DashMap;
use parking_lot::RwLock;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Instant;

use crate::cache::CacheHub;
use crate::clock::TradingClock;
use crate::config::Config;
use crate::db::Db;
use crate::engine::{RankingEngine, RankingTracker};
use crate::models::{CollectorStatus, DataSource, DataStatus};
use crate::net::HttpClient;
use crate::paths::AppPaths;
use crate::source::{EastmoneySource, SinaSource, TencentSource};

/// 采集器运行状态登记（第 3.10 / 8.20 章）
#[derive(Default)]
pub struct CollectorRegistry {
    statuses: DashMap<String, CollectorStatus>,
}

impl CollectorRegistry {
    pub fn new() -> Self {
        Self {
            statuses: DashMap::new(),
        }
    }

    pub fn register(&self, name: &str, label: &str) {
        self.statuses.insert(
            name.to_string(),
            CollectorStatus {
                name: name.to_string(),
                label: label.to_string(),
                last_run: "--:--:--".into(),
                last_success: "--:--:--".into(),
                last_error: String::new(),
                // ok = “尚无失败”，未运行过时展示为“等待生成”，不误报为异常
                ok: true,
                latency_ms: 0,
                runs: 0,
                failures: 0,
                data_points: 0,
                status: DataStatus::Waiting,
                source: DataSource::None,
            },
        );
    }

    pub fn note_success(
        &self,
        name: &str,
        points: usize,
        latency_ms: u64,
        source: DataSource,
        status: DataStatus,
    ) {
        if let Some(mut s) = self.statuses.get_mut(name) {
            let now = crate::clock::now_cn().format("%H:%M:%S").to_string();
            s.last_run = now.clone();
            s.last_success = now;
            s.last_error.clear();
            s.ok = true;
            s.latency_ms = latency_ms;
            s.runs += 1;
            s.data_points = points;
            s.source = source;
            s.status = status;
        }
    }

    pub fn note_failure(&self, name: &str, error: &str, latency_ms: u64) {
        if let Some(mut s) = self.statuses.get_mut(name) {
            s.last_run = crate::clock::now_cn().format("%H:%M:%S").to_string();
            s.last_error = error.to_string();
            s.ok = false;
            s.latency_ms = latency_ms;
            s.runs += 1;
            s.failures += 1;
            s.status = DataStatus::Error;
        }
    }

    pub fn note_status(&self, name: &str, status: DataStatus, message: &str) {
        if let Some(mut s) = self.statuses.get_mut(name) {
            s.status = status;
            if !message.is_empty() {
                s.last_error = message.to_string();
            }
        }
    }

    pub fn all(&self) -> Vec<CollectorStatus> {
        let mut out: Vec<CollectorStatus> = self.statuses.iter().map(|e| e.value().clone()).collect();
        out.sort_by(|a, b| a.name.cmp(&b.name));
        out
    }

    pub fn get(&self, name: &str) -> Option<CollectorStatus> {
        self.statuses.get(name).map(|e| e.value().clone())
    }

    pub fn healthy_count(&self) -> (usize, usize) {
        let all = self.statuses.len();
        let ok = self.statuses.iter().filter(|e| e.value().ok).count();
        (ok, all)
    }
}

/// 数据源冷却闸门。
///
/// 某个数据源整体失败后，在冷却期内直接跳过它，避免每个采集周期都
/// 重新经历一轮超时与退避（真实用户最直观的体验优化，第 8.16 章）。
#[derive(Default)]
pub struct SourceGate {
    cooldowns: DashMap<String, std::time::Instant>,
    last_reason: DashMap<String, String>,
}

/// 东方财富各接口族使用**独立的冷却键**。
///
/// 早期实现用单一 "eastmoney" 键，导致 clist 榜单接口被限流时，
/// ulist 批量行情、push2his 历史 K 线、push2 资金流全部被一起禁用 ——
/// 而它们其实是不同的主机与接口，可用性互不影响。
pub mod gate_keys {
    pub const EM_CLIST: &str = "eastmoney:clist";
    pub const EM_ULIST: &str = "eastmoney:ulist";
    pub const EM_HIS: &str = "eastmoney:his";
    pub const EM_FFLOW: &str = "eastmoney:fflow";
    pub const TENCENT: &str = "tencent";
    pub const SINA: &str = "sina";
}

impl SourceGate {
    pub fn new() -> Self {
        Self::default()
    }

    /// 是否处于冷却中（应跳过该数据源）
    pub fn is_blocked(&self, source: &str) -> bool {
        match self.cooldowns.get(source) {
            Some(t) => {
                if t.elapsed() < Self::cooldown_duration() {
                    true
                } else {
                    drop(t);
                    self.cooldowns.remove(source);
                    false
                }
            }
            None => false,
        }
    }

    fn cooldown_duration() -> std::time::Duration {
        std::time::Duration::from_secs(180)
    }

    pub fn block(&self, source: &str, reason: &str) {
        self.cooldowns.insert(source.to_string(), std::time::Instant::now());
        self.last_reason.insert(source.to_string(), reason.to_string());
        tracing::warn!(target: "app", source, reason, "数据源进入冷却，暂时跳过");
    }

    pub fn clear(&self, source: &str) {
        if self.cooldowns.remove(source).is_some() {
            tracing::info!(target: "app", source, "数据源冷却结束，恢复使用");
        }
        self.last_reason.remove(source);
    }

    pub fn reason(&self, source: &str) -> Option<String> {
        self.last_reason.get(source).map(|e| e.value().clone())
    }

    pub fn remaining_secs(&self, source: &str) -> u64 {
        self.cooldowns
            .get(source)
            .map(|t| Self::cooldown_duration().saturating_sub(t.elapsed()).as_secs())
            .unwrap_or(0)
    }

    pub fn snapshot(&self) -> Vec<(String, u64, String)> {
        self.cooldowns
            .iter()
            .map(|e| {
                (
                    e.key().clone(),
                    self.remaining_secs(e.key()),
                    self.reason(e.key()).unwrap_or_default(),
                )
            })
            .collect()
    }
}

pub struct AppState {
    pub paths: AppPaths,
    pub config: RwLock<Config>,
    pub clock: RwLock<TradingClock>,
    pub http: Arc<HttpClient>,
    pub eastmoney: Arc<EastmoneySource>,
    pub tencent: Arc<TencentSource>,
    pub sina: Arc<SinaSource>,
    pub cache: Arc<CacheHub>,
    pub db: Db,
    pub engine: RwLock<Arc<RankingEngine>>,
    pub tracker: Arc<RankingTracker>,
    pub collectors: Arc<CollectorRegistry>,
    /// 数据源冷却闸门
    pub gate: Arc<SourceGate>,
    pub started_at: Instant,
    pub startup_notes: RwLock<Vec<String>>,
    pub last_error: RwLock<String>,
    /// 当前交易日
    pub trade_date: RwLock<String>,
    /// 已生成的节点快照（node -> 状态）
    pub node_state: DashMap<String, (DataStatus, DataSource, String)>,
    /// 采集总轮次
    pub cycle: AtomicU64,
    /// 全市场扫描是否已完成过至少一次
    pub full_scan_done: AtomicBool,
    /// 是否处于降级模式
    pub degraded: AtomicBool,
    /// 本地 API 访问令牌（除 /api/health 外所有接口必须携带）
    pub api_token: String,
    /// 历史分钟数据列式存储（Parquet）
    pub history: Arc<crate::history::MinuteStore>,
    /// 历史数据查询层（DuckDB over Parquet）
    pub repo: Arc<crate::history::DuckRepository>,
    /// 历史库覆盖情况（启动预加载后写入）
    pub coverage: RwLock<crate::history::preload::HistoryCoverage>,
    /// 关闭信号
    pub shutdown: Arc<tokio::sync::Notify>,
    pub shutting_down: AtomicBool,
}

impl AppState {
    pub fn new(
        paths: AppPaths,
        config: Config,
        notes: Vec<String>,
        http: Arc<HttpClient>,
        db: Db,
    ) -> Arc<Self> {
        let clock = TradingClock::new(config.holidays.clone());
        let trade_date = clock
            .trading_date_at(crate::clock::now_cn())
            .to_string();
        let engine = Arc::new(RankingEngine::new(config.boards.clone()));
        let cache = Arc::new(CacheHub::new(config.cache_ttl_secs));
        let collectors = Arc::new(CollectorRegistry::new());

        for (name, label) in [
            ("index", "指数行情"),
            ("market", "个股资金流"),
            ("universe", "证券列表"),
            ("full_scan", "全市场扫描"),
            ("sector", "板块资金流"),
            ("capital", "资金曲线"),
            ("history", "分钟数据"),
            ("snapshot", "节点快照"),
            ("review", "复盘重建"),
        ] {
            collectors.register(name, label);
        }

        let api_token = crate::security::generate_token();
        if let Err(err) = crate::security::write_token(&paths, &api_token) {
            tracing::warn!(target: "app", "令牌文件写入失败: {err}");
        }
        // 历史库根目录需要在 paths 被移动前取出来
        let history = Arc::new(crate::history::MinuteStore::new(paths.history_dir()));
        let repo = match crate::history::DuckRepository::open(paths.history_dir()) {
            Ok(r) => Arc::new(r),
            Err(err) => {
                tracing::error!(target: "app", error = %err, "DuckDB 查询层初始化失败");
                Arc::new(
                    crate::history::DuckRepository::open(paths.history_dir())
                        .expect("DuckDB 无法初始化"),
                )
            }
        };

        Arc::new(Self {
            paths,
            config: RwLock::new(config),
            clock: RwLock::new(clock),
            eastmoney: Arc::new(EastmoneySource::new(http.clone())),
            tencent: Arc::new(TencentSource::new(http.clone())),
            sina: Arc::new(SinaSource::new(http.clone())),
            http,
            cache,
            db,
            engine: RwLock::new(engine),
            tracker: Arc::new(RankingTracker::new()),
            collectors,
            gate: Arc::new(SourceGate::new()),
            started_at: Instant::now(),
            startup_notes: RwLock::new(notes),
            last_error: RwLock::new(String::new()),
            trade_date: RwLock::new(trade_date),
            node_state: DashMap::new(),
            cycle: AtomicU64::new(0),
            full_scan_done: AtomicBool::new(false),
            degraded: AtomicBool::new(false),
            api_token,
            history,
            repo,
            coverage: RwLock::new(Default::default()),
            shutdown: Arc::new(tokio::sync::Notify::new()),
            shutting_down: AtomicBool::new(false),
        })
    }

    pub fn config(&self) -> Config {
        self.config.read().clone()
    }

    pub fn set_config(&self, cfg: Config) {
        {
            let mut guard = self.config.write();
            *guard = cfg.clone();
        }
        let mut clock = self.clock.write();
        *clock = TradingClock::new(cfg.holidays.clone());
        let mut engine = self.engine.write();
        *engine = Arc::new(RankingEngine::new(cfg.boards.clone()));
        self.http.update_config(&cfg.net);
    }

    pub fn engine(&self) -> Arc<RankingEngine> {
        self.engine.read().clone()
    }

    pub fn uptime_secs(&self) -> u64 {
        self.started_at.elapsed().as_secs()
    }

    pub fn current_trade_date(&self) -> String {
        self.trade_date.read().clone()
    }

    pub fn set_trade_date(&self, date: &str) {
        let mut d = self.trade_date.write();
        if *d != date {
            tracing::info!(target: "app", from = %*d, to = %date, "交易日切换");
            *d = date.to_string();
        }
    }

    pub fn note_error(&self, msg: &str) {
        *self.last_error.write() = msg.to_string();
        tracing::warn!(target: "app", "{msg}");
    }

    pub fn push_note(&self, msg: impl Into<String>) {
        let msg = msg.into();
        let mut notes = self.startup_notes.write();
        if !notes.contains(&msg) {
            notes.push(msg);
        }
    }

    pub fn mark_degraded(&self, degraded: bool) {
        let prev = self.degraded.swap(degraded, Ordering::Relaxed);
        if prev != degraded {
            tracing::warn!(target: "app", degraded, "数据源降级状态变化");
        }
    }

    pub fn is_degraded(&self) -> bool {
        self.degraded.load(Ordering::Relaxed)
    }

    pub fn begin_shutdown(&self) -> bool {
        !self.shutting_down.swap(true, Ordering::SeqCst)
    }

    /// 保存运行状态（关闭流程，第 3.5 章）
    pub fn persist_state(&self) {
        let cfg = self.config();
        let payload = serde_json::json!({
            "last_trade_date": self.current_trade_date(),
            "last_node": self.node_state.iter().map(|e| e.key().clone()).collect::<Vec<_>>(),
            "fullscreen": cfg.fullscreen,
            "version": crate::config::APP_VERSION,
            "saved_at": crate::models::now_str(),
        });
        let raw = serde_json::to_string_pretty(&payload).unwrap_or_default();
        if let Some(parent) = self.paths.state_file().parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if let Err(err) = std::fs::write(self.paths.state_file(), raw) {
            tracing::warn!(target: "app", "状态文件写入失败: {err}");
        }
        let _ = self.db.set_state("last_trade_date", &self.current_trade_date());
        let _ = self.db.set_state("last_shutdown", &crate::models::now_str());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn build_state(dir: &std::path::Path) -> Arc<AppState> {
        let mut cfg = Config::default();
        cfg.port = 47899;
        let paths = AppPaths {
            root: dir.to_path_buf(),
            data_dir: dir.join("data"),
            db_path: dir.join("data/database.sqlite"),
            logs_dir: dir.join("logs"),
            config_dir: dir.join("config"),
            cache_dir: dir.join("cache"),
            install_dir: dir.to_path_buf(),
            resources_dir: dir.join("resources"),
        };
        std::fs::create_dir_all(&paths.data_dir).unwrap();
        let db = Db::open(&paths.db_path).unwrap();
        let http = Arc::new(HttpClient::new(&cfg.net).unwrap());
        AppState::new(paths, cfg, vec![], http, db)
    }

    #[test]
    fn state_registers_all_collectors() {
        let tmp = tempfile::tempdir().unwrap();
        let state = build_state(tmp.path());
        let all = state.collectors.all();
        assert!(all.len() >= 8, "应注册全部采集器，实际 {}", all.len());
        assert!(all.iter().any(|c| c.name == "market"));
        assert!(all.iter().any(|c| c.name == "sector"));
        assert!(all.iter().any(|c| c.name == "history"));
    }

    #[test]
    fn collector_status_transitions() {
        let tmp = tempfile::tempdir().unwrap();
        let state = build_state(tmp.path());
        state.collectors.note_failure("market", "timeout", 120);
        let s = state.collectors.get("market").unwrap();
        assert!(!s.ok);
        assert_eq!(s.failures, 1);
        assert_eq!(s.status, DataStatus::Error);

        state.collectors.note_success(
            "market",
            800,
            90,
            DataSource::EastmoneyRealtime,
            DataStatus::Realtime,
        );
        let s = state.collectors.get("market").unwrap();
        assert!(s.ok);
        assert_eq!(s.data_points, 800);
        assert!(s.last_error.is_empty());
    }

    #[test]
    fn source_gate_blocks_and_recovers() {
        let gate = SourceGate::new();
        assert!(!gate.is_blocked("eastmoney"));
        gate.block("eastmoney", "连接失败");
        assert!(gate.is_blocked("eastmoney"));
        assert!(gate.remaining_secs("eastmoney") > 0);
        assert_eq!(gate.reason("eastmoney").as_deref(), Some("连接失败"));
        assert_eq!(gate.snapshot().len(), 1);
        gate.clear("eastmoney");
        assert!(!gate.is_blocked("eastmoney"));
        assert_eq!(gate.snapshot().len(), 0);
    }

    #[test]
    fn shutdown_is_idempotent() {
        let tmp = tempfile::tempdir().unwrap();
        let state = build_state(tmp.path());
        assert!(state.begin_shutdown());
        assert!(!state.begin_shutdown(), "重复关闭应返回 false");
    }

    #[test]
    fn state_persists_and_updates_config() {
        let tmp = tempfile::tempdir().unwrap();
        let state = build_state(tmp.path());
        let mut cfg = state.config();
        cfg.top_n = 25;
        state.set_config(cfg);
        assert_eq!(state.config().top_n, 25);
        state.set_trade_date("2026-09-30");
        state.persist_state();
        assert!(state.paths.state_file().exists());
    }
}
