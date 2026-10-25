//! 统一任务调度（第 4.4 / 8.8 章）。
//!
//! 全部采集由 Scheduler 统一驱动，禁止任何模块自行启动定时器。
//! 分级刷新：
//!   * 3 秒   指数 / 大盘
//!   * 10 秒  板块
//!   * 15 秒  资金曲线
//!   * 20 秒  个股池（覆盖全部榜单）
//!   * 60 秒  分钟数据落库
//!   * 90 秒  全市场扫描
//! 非交易时段自动降频，避免无意义请求。

use std::sync::Arc;
use std::time::Duration;

use tokio::task::JoinHandle;

use crate::collector::{capital, history, index, market, sector};
use crate::clock::MarketStatus;
use crate::models::DataStatus;
use crate::state::AppState;


pub struct Scheduler {
    handles: Vec<JoinHandle<()>>,
}

impl Scheduler {
    pub fn spawn(state: Arc<AppState>) -> Self {
        let mut handles = Vec::new();
        handles.push(spawn_index(state.clone()));
        handles.push(spawn_sector(state.clone()));
        handles.push(spawn_market(state.clone()));
        handles.push(spawn_full_scan(state.clone()));
        handles.push(spawn_capital(state.clone()));
        handles.push(spawn_minute_persist(state.clone()));
        handles.push(spawn_node_watcher(state.clone()));
        handles.push(spawn_housekeeping(state.clone()));
        tracing::info!(target: "app", tasks = handles.len(), "调度器已启动");
        Self { handles }
    }

    pub async fn shutdown(self) {
        for h in self.handles {
            h.abort();
        }
    }
}

/// 是否处于交易时段
fn is_live(state: &Arc<AppState>) -> bool {
    state.clock.read().is_live()
}

/// 非交易时段仍保持低频采集，保证打开软件就能看到最近一个交易日的真实数据，
/// 而不是空白页面（第 4.6 / 8.16 章：降级也要有数据）。
fn should_collect(_state: &Arc<AppState>) -> bool {
    true
}

/// 实际采集间隔：交易时段用配置值，非交易时段自动降频。
///
/// 降频上限控制在 5 分钟以内，保证用户任何时候打开软件都能看到
/// 最近一个交易日的真实数据，而不是“暂无数据”（第 4.6 / 8.16 章）。
fn interval_for(state: &Arc<AppState>, base: u64) -> u64 {
    if is_live(state) {
        base.max(1)
    } else {
        (base.saturating_mul(6)).clamp(60, 300)
    }
}

/// 缓存有效期：必须明显长于采集间隔，否则界面会在两次采集之间“断档”
fn cache_ttl(interval_secs: u64) -> Duration {
    Duration::from_secs(interval_secs.saturating_mul(3).max(30))
}

fn spawn_index(state: Arc<AppState>) -> JoinHandle<()> {
    tokio::spawn(async move {
        loop {
            let secs = interval_for(&state, state.config().refresh.index_interval_secs.max(1));
            if should_collect(&state) {
                let result = index::run_index_cycle(&state).await;
                if !result.indices.is_empty() {
                    state.cache.indices.put_value(
                        index::CACHE_KEY_INDICES,
                        result.indices,
                        result.source,
                        result.status,
                        cache_ttl(secs),
                    );
                }
            }
            if sleep_or_shutdown(&state, secs).await {
                break;
            }
        }
    })
}

fn spawn_sector(state: Arc<AppState>) -> JoinHandle<()> {
    tokio::spawn(async move {
        // 板块快照落盘节流：每分钟最多写一次（板块快照是一个几百条记录的
        // JSON，按采集周期每 5 秒写一次会给 SQLite 造成无谓压力）
        let mut last_persist_minute: u64 = 0;
        loop {
            let secs = interval_for(&state, state.config().refresh.sector_interval_secs.max(2));
            if should_collect(&state) {
                let result = sector::run_sector_cycle(&state).await;
                if !result.sectors.is_empty() {
                    state.cache.sectors.put_value(
                        sector::CACHE_KEY_ALL,
                        result.sectors.clone(),
                        result.source,
                        result.status,
                        cache_ttl(secs),
                    );
                    // 落盘当日板块快照（节点 "latest"）。
                    // 这样在收盘后 / 休市日 / 断网时，首页模块 3 仍能从本地
                    // 快照读到**真实**的当日板块资金流，而不是空数据。
                    let now_minute = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .map(|d| d.as_secs() / 60)
                        .unwrap_or(0);
                    if now_minute != last_persist_minute {
                        last_persist_minute = now_minute;
                        let db = state.db.clone();
                        let date = crate::market_time::resolve(&state).trade_date;
                        let payload = result.sectors.clone();
                        let src = result.source.label().to_string();
                        let _ = tokio::task::spawn_blocking(move || {
                            db.save_sector_node(&date, sector::NODE_LATEST, &src, &payload)
                        })
                        .await;
                    }
                    let mut industry = Vec::new();
                    let mut concept = Vec::new();
                    for s in result.sectors {
                        if s.kind == "concept" {
                            concept.push(s);
                        } else {
                            industry.push(s);
                        }
                    }
                    state.cache.sectors.put_value(
                        sector::CACHE_KEY_INDUSTRY,
                        industry,
                        result.source,
                        result.status,
                        cache_ttl(secs),
                    );
                    state.cache.sectors.put_value(
                        sector::CACHE_KEY_CONCEPT,
                        concept,
                        result.source,
                        result.status,
                        cache_ttl(secs),
                    );
                }
            }
            if sleep_or_shutdown(&state, secs).await {
                break;
            }
        }
    })
}

fn spawn_market(state: Arc<AppState>) -> JoinHandle<()> {
    tokio::spawn(async move {
        loop {
            let secs = interval_for(&state, state.config().refresh.market_interval_secs.max(5));
            if should_collect(&state) {
                // 榜单/涨跌家数/资金排名一律基于完整股票宇宙
                let result = market::run_full_market_cycle(&state).await;
                if !result.stocks.is_empty() {
                    let trade_date = state.current_trade_date();
                    let as_of = crate::clock::now_cn().format("%H:%M").to_string();

                    state.tracker.record_stocks(&result.stocks);
                    state.cache.stocks.put_value(
                        market::CACHE_KEY_FULL_STOCKS,
                        result.stocks.clone(),
                        result.source,
                        result.status,
                        cache_ttl(secs),
                    );

                    let sectors = state
                        .cache
                        .sectors
                        .get(sector::CACHE_KEY_ALL)
                        .map(|e| e.value.clone())
                        .unwrap_or_default();

                    // 计算九榜
                    let boards = state
                        .engine()
                        .compute(&result.stocks, &sectors, &as_of, &state.tracker);
                    state.cache.boards.put_value(
                        "boards",
                        boards.clone(),
                        result.source,
                        result.status,
                        cache_ttl(secs),
                    );
                    state.cache.aggregates.put_value(
                        "aggregates",
                        state
                            .tracker
                            .aggregates(state.engine().boards(), state.config().top_n * 3),
                        result.source,
                        result.status,
                        cache_ttl(secs),
                    );

                    // 事件驱动落库
                    crate::collector::persist_boards(&state, &boards, &trade_date, result.source)
                        .await;

                    state.mark_degraded(!matches!(result.source, crate::models::DataSource::EastmoneyRealtime));
                    if !result.message.is_empty() {
                        state.push_note(result.message.clone());
                    }
                }
                state.cycle.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            }
            if sleep_or_shutdown(&state, secs).await {
                break;
            }
        }
    })
}

/// 全市场行情采集：以**本地证券列表**为宇宙，用批量接口取全市场行情。
///
/// 关键修复：早期实现用"排行榜接口"当宇宙（腾讯 6×200 上限），
/// 把 5900 只 A 股截断成 1200 只，导致涨跌家数、涨停跌停、成交额、
/// 榜单 TOP N 全部错误。现在宇宙来自本地证券列表，批量行情按代码取数。
fn spawn_full_scan(state: Arc<AppState>) -> JoinHandle<()> {
    tokio::spawn(async move {
        loop {
            let secs = interval_for(&state, state.config().refresh.full_scan_interval_secs.max(30));
            if should_collect(&state) {
                let snap = crate::universe::collect_full_universe(&state).await;
                let stats = snap.stats.clone();

                if !snap.stocks.is_empty() {
                    let stocks = snap.stocks;
                    let mut breadth = crate::engine::breadth(&stocks);
                    breadth.sample_size = stats.loaded_symbols as i32;
                    breadth.scope = if stats.complete { "full" } else { "partial" }.into();
                    breadth.source = stats.source.clone();
                    breadth.sentiment = crate::engine::sentiment_score(&stocks);
                    // 完整性指标随广度一起返回，前端可据此判断口径
                    breadth.total_symbols = stats.total_symbols as i32;
                    breadth.missing_symbols = stats.missing_symbols as i32;
                    breadth.complete = stats.complete;

                    let source = if stats.source == "东方财富实时" {
                        crate::models::DataSource::EastmoneyRealtime
                    } else if stats.source == "新浪财经" {
                        crate::models::DataSource::SinaRealtime
                    } else if stats.source == "腾讯财经" {
                        crate::models::DataSource::TencentRealtime
                    } else {
                        crate::models::DataSource::None
                    };
                    let status = if stats.complete {
                        DataStatus::Realtime
                    } else {
                        DataStatus::Partial
                    };

                    state.cache.breadth.put_value(
                        "breadth_full",
                        breadth,
                        source,
                        status,
                        cache_ttl(secs),
                    );
                    state.cache.stocks.put_value(
                        market::CACHE_KEY_FULL_STOCKS,
                        stocks,
                        source,
                        status,
                        cache_ttl(secs),
                    );
                    state
                        .full_scan_done
                        .store(true, std::sync::atomic::Ordering::Relaxed);

                    if !stats.complete {
                        state.push_note(format!(
                            "全市场行情不完整：{}（涨跌家数为不完整口径）",
                            stats.note
                        ));
                    }
                } else {
                    state
                        .collectors
                        .note_failure("full_scan", &stats.note, 0);
                }
            }
            if sleep_or_shutdown(&state, secs).await {
                break;
            }
        }
    })
}

fn spawn_capital(state: Arc<AppState>) -> JoinHandle<()> {
    tokio::spawn(async move {
        loop {
            let secs = interval_for(&state, 15);
            if should_collect(&state) {
                capital::run_capital_cycle(&state).await;
            }
            if sleep_or_shutdown(&state, secs).await {
                break;
            }
        }
    })
}

fn spawn_minute_persist(state: Arc<AppState>) -> JoinHandle<()> {
    tokio::spawn(async move {
        loop {
            let secs = interval_for(&state, state.config().refresh.minute_persist_secs.max(20));
            if should_collect(&state) {
                if let Some(entry) = state.cache.stocks.get(market::CACHE_KEY_FULL_STOCKS) {
                    let stocks = entry.value.clone();
                    let trade_date = state.current_trade_date();
                    let n = history::persist_minutes(&state, &stocks, &trade_date).await;
                    if n > 0 {
                        state.collectors.note_success(
                            "history",
                            n,
                            0,
                            entry.source,
                            DataStatus::Realtime,
                        );
                    }
                }
            }
            if sleep_or_shutdown(&state, secs).await {
                break;
            }
        }
    })
}

/// 节点监听：到达 09:35 / 09:40 / 09:45 时生成快照
fn spawn_node_watcher(state: Arc<AppState>) -> JoinHandle<()> {
    tokio::spawn(async move {
        let mut captured: std::collections::HashSet<String> = std::collections::HashSet::new();
        let mut last_date = state.current_trade_date();
        loop {
            let now = crate::clock::now_cn();
            let clock = state.clock.read().clone();
            let trade_date = clock.trading_date_at(now).to_string();

            if trade_date != last_date {
                captured.clear();
                state.tracker.reset_day();
                last_date = trade_date.clone();
                state.set_trade_date(&trade_date);
            }
            state.set_trade_date(&trade_date);

            let status = clock.status();
            if matches!(
                status,
                MarketStatus::MorningSession | MarketStatus::AfternoonSession | MarketStatus::Open
            ) {
                let hhmm = now.format("%H:%M").to_string();
                let nodes = state.config().nodes;
                if nodes.iter().any(|n| n == &hhmm) {
                    let key = format!("{trade_date} {hhmm}");
                    if captured.insert(key) {
                        history::capture_node_snapshot(&state, &hhmm, &trade_date).await;
                    }
                }
            }
            if sleep_or_shutdown(&state, 1).await {
                break;
            }
        }
    })
}

/// 维护任务：日切归档、过期清理、交易日校验
fn spawn_housekeeping(state: Arc<AppState>) -> JoinHandle<()> {
    tokio::spawn(async move {
        let mut last_archive_date = String::new();
        let mut last_purge = std::time::Instant::now();
        loop {
            let now = crate::clock::now_cn();
            let clock = state.clock.read().clone();
            let trade_date = clock.trading_date_at(now).to_string();
            let status = clock.status();

            // 收盘后归档当日数据（第 8.21 章）
            if matches!(status, MarketStatus::Closed) && last_archive_date != trade_date {
                if let Some(entry) = state.cache.stocks.get_stale(market::CACHE_KEY_FULL_STOCKS) {
                    history::archive_day(&state, &trade_date, &entry.value, entry.source).await;
                }
                // 收盘后补齐所有节点快照
                history::ensure_node_snapshots(&state, &trade_date).await;
                last_archive_date = trade_date.clone();
                let _ = state.db.set_state("last_archive", &trade_date);
            }

            // 每小时清理一次过期数据
            if last_purge.elapsed() > Duration::from_secs(3600) {
                history::purge_old_data(&state).await;
                last_purge = std::time::Instant::now();
            }

            // 启动时补齐节点快照（每 5 分钟检查一次，直到全部就绪）
            history::ensure_node_snapshots(&state, &trade_date).await;

            if sleep_or_shutdown(&state, if is_live(&state) { 300 } else { 900 }).await {
                break;
            }
        }
    })
}

/// 睡眠，返回 true 表示收到关闭信号
async fn sleep_or_shutdown(state: &Arc<AppState>, secs: u64) -> bool {
    tokio::select! {
        _ = tokio::time::sleep(Duration::from_secs(secs)) => false,
        _ = state.shutdown.notified() => {
            tracing::info!(target: "app", "调度任务收到关闭信号");
            true
        }
    }
}

/// 启动时初始化（第 6.9 章）。
///
/// 顺序很重要：必须先完成一轮真实采集，建立个股候选池，
/// 之后的历史节点重建才有可用的股票池（否则会出现“没有数据可重建”）。
pub async fn bootstrap(state: Arc<AppState>) {
    let trade_date = state.current_trade_date();
    tracing::info!(target: "app", trade_date = %trade_date, "开始初始化当日数据");

    // 启动阶段写入的缓存给足有效期，避免调度器首轮刷新前出现“暂无数据”
    const BOOTSTRAP_TTL: Duration = Duration::from_secs(600);

    // 1) 指数
    let idx = index::run_index_cycle(&state).await;
    if !idx.indices.is_empty() {
        state.cache.indices.put_value(
            index::CACHE_KEY_INDICES,
            idx.indices,
            idx.source,
            idx.status,
            BOOTSTRAP_TTL,
        );
    }

    // 2) 板块
    let sec = sector::run_sector_cycle(&state).await;
    if !sec.sectors.is_empty() {
        state.cache.sectors.put_value(
            sector::CACHE_KEY_ALL,
            sec.sectors,
            sec.source,
            sec.status,
            BOOTSTRAP_TTL,
        );
    }

    // 3) 个股池（建立重建候选池）
    // 启动初始化同样使用全市场口径
    let uni = market::run_full_market_cycle(&state).await;
    if !uni.stocks.is_empty() {
        state.tracker.record_stocks(&uni.stocks);
        state.cache.stocks.put_value(
            market::CACHE_KEY_FULL_STOCKS,
            uni.stocks.clone(),
            uni.source,
            uni.status,
            BOOTSTRAP_TTL,
        );
        let sectors = state
            .cache
            .sectors
            .get(sector::CACHE_KEY_ALL)
            .map(|e| e.value.clone())
            .unwrap_or_default();
        let as_of = crate::clock::now_cn().format("%H:%M").to_string();
        let boards = state
            .engine()
            .compute(&uni.stocks, &sectors, &as_of, &state.tracker);
        state.cache.boards.put_value(
            "boards",
            boards.clone(),
            uni.source,
            uni.status,
            BOOTSTRAP_TTL,
        );
        state.cache.aggregates.put_value(
            "aggregates",
            state
                .tracker
                .aggregates(state.engine().boards(), state.config().top_n * 3),
            uni.source,
            uni.status,
            BOOTSTRAP_TTL,
        );
        crate::collector::persist_boards(&state, &boards, &trade_date, uni.source).await;
        if !uni.message.is_empty() {
            state.push_note(uni.message);
        }
    }

    // 4) 从数据库回灌当日九榜统计状态。
    //    没有这一步，进程重启后事件统计会从 0 重新开始，
    //    「九榜事件统计 / 个股精选」在重启前后不连续。
    {
        let db = state.db.clone();
        let date = trade_date.clone();
        let episodes = tokio::task::spawn_blocking(move || db.load_episodes(&date))
            .await
            .ok()
            .and_then(|r| r.ok())
            .unwrap_or_default();
        if !episodes.is_empty() {
            state.tracker.warm_from_episodes(&episodes);
        }
    }

    // 5) 历史库预加载（首页四个核心模块全部依赖它，必须尽早执行）。
    //    放在节点重建之前：节点重建要逐只拉分钟数据、耗时可达数分钟，
    //    如果排在前面会导致首页长时间拿不到指数分时与资金曲线。
    let store = state.history.clone();
    let cov = crate::history::preload::preload(&state, &store).await;
    tracing::info!(
        target: "app",
        latest = %cov.latest_trading_day,
        points = cov.latest_minute_points,
        ready = cov.dates_minute_ready.len(),
        missing = cov.dates_minute_missing.len(),
        "历史库预加载完成"
    );
    *state.coverage.write() = cov;

    // 6) 资金曲线首个点
    capital::run_capital_cycle(&state).await;

    // 7) 落一次分钟数据（当天数据库即有内容，便于复盘与重建）
    if let Some(entry) = state.cache.stocks.get(market::CACHE_KEY_FULL_STOCKS) {
        let stocks = entry.value.clone();
        let n = history::persist_minutes(&state, &stocks, &trade_date).await;
        if n > 0 {
            state.collectors.note_success(
                "history",
                n,
                0,
                entry.source,
                crate::models::DataStatus::Realtime,
            );
        }
    }


    // 8) 先只做“已有快照的读取”，保证界面能立刻拿到数据
    let quick = history::ensure_node_snapshots(&state, &trade_date).await;
    for (node, status, note) in &quick {
        tracing::info!(target: "app", node = %node, status = ?status, note = %note, "节点状态");
    }

    // 9) 历史节点重建耗时较长（需要逐只拉取分钟数据），放到后台执行，
    //    保证窗口与接口立刻可用（第 6.14 章：耗时操作后台执行）
    let bg = state.clone();
    let bg_date = trade_date.clone();
    tokio::spawn(async move {
        // 先做一轮，让晚启动用户尽快看到早盘节点
        let summary = history::ensure_node_snapshots(&bg, &bg_date).await;
        for (node, status, note) in summary {
            tracing::info!(target: "app", node = %node, status = ?status, note = %note, "后台重建完成");
        }
    });
}

/// 供监控使用：调度概览
pub fn overview(state: &Arc<AppState>) -> Vec<(String, u64)> {
    let cfg = state.config();
    vec![
        ("指数行情".into(), cfg.refresh.index_interval_secs),
        ("板块资金流".into(), cfg.refresh.sector_interval_secs),
        ("个股资金流".into(), cfg.refresh.market_interval_secs),
        ("全市场扫描".into(), cfg.refresh.full_scan_interval_secs),
        ("分钟数据".into(), cfg.refresh.minute_persist_secs),
        ("资金曲线".into(), 15),
        ("节点监听".into(), 1),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::db::Db;
    use crate::net::HttpClient;
    use crate::paths::AppPaths;

    fn test_state(dir: &std::path::Path) -> Arc<AppState> {
        let paths = AppPaths {
            root: dir.to_path_buf(),
            data_dir: dir.join("data"),
            db_path: dir.join("data/db.sqlite"),
            logs_dir: dir.join("logs"),
            config_dir: dir.join("config"),
            cache_dir: dir.join("cache"),
            install_dir: dir.to_path_buf(),
            resources_dir: dir.join("res"),
        };
        std::fs::create_dir_all(&paths.data_dir).unwrap();
        let db = Db::open(&paths.db_path).unwrap();
        let cfg = Config::default();
        let http = Arc::new(HttpClient::new(&cfg.net).unwrap());
        AppState::new(paths, cfg, vec![], http, db)
    }

    #[test]
    fn overview_lists_all_tasks() {
        let tmp = tempfile::tempdir().unwrap();
        let state = test_state(tmp.path());
        let ov = overview(&state);
        assert!(ov.len() >= 7);
        assert!(ov.iter().any(|(n, _)| n.contains("节点监听")));
    }

    #[tokio::test]
    async fn sleep_or_shutdown_reacts_to_signal() {
        let tmp = tempfile::tempdir().unwrap();
        let state = test_state(tmp.path());
        let s2 = state.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(30)).await;
            s2.shutdown.notify_waiters();
        });
        let stopped = sleep_or_shutdown(&state, 30).await;
        assert!(stopped, "收到关闭信号后应立即返回");
    }

    #[tokio::test]
    async fn scheduler_starts_and_stops_cleanly() {
        let tmp = tempfile::tempdir().unwrap();
        let state = test_state(tmp.path());
        let scheduler = Scheduler::spawn(state.clone());
        tokio::time::sleep(Duration::from_millis(50)).await;
        scheduler.shutdown().await;
        assert!(state.uptime_secs() < 10);
    }
}
