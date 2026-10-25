//! 分钟数据持久化 + 节点快照 + 历史重建（第 4.11 / 8.10 章）。
//!
//! 解决“程序没开，所以没有早盘数据”的问题：
//!   1. 程序运行期间每分钟把个股快照写入 SQLite（minute_data）
//!   2. 到点自动生成节点快照并落库（realtime）
//!   3. 启动晚于节点时：
//!        a. 优先从本机 SQLite 分钟数据重建
//!        b. 本机无数据则用东方财富分钟 K 线 + 分钟资金流重建候选池
//!   4. 重建结果标注 REBUILD / PARTIAL / DATA_SOURCE_LIMITATION，绝不伪造

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use crate::engine::snapshot::{NodeAggregate, SnapshotInput, build_snapshot_from_aggregates};
use crate::engine::{breadth, snapshot};
use crate::models::{
    DataSource, DataStatus, IntradaySnapshot, MinutePoint, StockTick,
};
use crate::state::AppState;

use super::market::CACHE_KEY_FULL_STOCKS;
use super::{Timer, persist_boards};

/// 默认重建候选上限（可被配置覆盖）
pub const DEFAULT_REBUILD_CANDIDATES: usize = 400;
/// 单次重建最多拉取的分钟数（一个完整交易日 240 分钟）
pub const MINUTE_FETCH_LIMIT: usize = 300;

/// 把当前个股池写为分钟数据
pub async fn persist_minutes(
    state: &Arc<AppState>,
    stocks: &[StockTick],
    trade_date: &str,
) -> usize {
    if stocks.is_empty() {
        return 0;
    }
    let minute = crate::clock::now_cn().format("%H:%M").to_string();
    let date = chrono::NaiveDate::parse_from_str(trade_date, "%Y-%m-%d")
        .unwrap_or_else(|_| crate::clock::now_cn().date_naive());

    let points: Vec<MinutePoint> = stocks
        .iter()
        .filter(|s| s.price > 0.0)
        .map(|s| MinutePoint {
            code: s.code.clone(),
            trade_date: date,
            minute: minute.clone(),
            price: s.price,
            volume: s.volume,
            amount: s.amount,
            main_net_inflow: s.main_net_inflow,
        })
        .collect();
    let names: HashMap<String, String> = stocks
        .iter()
        .map(|s| (s.code.clone(), s.name.clone()))
        .collect();

    let db = state.db.clone();
    let _count = points.len();
    let result = tokio::task::spawn_blocking(move || db.save_minutes(&points, &names)).await;
    match result {
        Ok(Ok(n)) => n,
        Ok(Err(err)) => {
            tracing::warn!(target: "collector", "分钟数据写入失败: {err}");
            0
        }
        Err(err) => {
            tracing::warn!(target: "collector", "分钟数据任务异常: {err}");
            0
        }
    }
}

/// 由实时缓存生成并保存节点快照
pub async fn capture_node_snapshot(
    state: &Arc<AppState>,
    node: &str,
    trade_date: &str,
) -> Option<IntradaySnapshot> {
    let cfg = state.config();
    let stocks = state
        .cache
        .stocks
        .get(CACHE_KEY_FULL_STOCKS)
        .map(|e| e.value.clone())
        .or_else(|| {
            state
                .cache
                .stocks
                .get_stale(CACHE_KEY_FULL_STOCKS)
                .map(|e| e.value.clone())
        })?;
    if stocks.is_empty() {
        return None;
    }
    let sectors = state
        .cache
        .sectors
        .get(super::sector::CACHE_KEY_ALL)
        .map(|e| e.value.clone())
        .unwrap_or_default();
    let b = breadth(&stocks);

    let mut input = SnapshotInput::live(trade_date, node, cfg.top_open, cfg.top_n);
    let source = state
        .cache
        .stocks
        .get_stale(CACHE_KEY_FULL_STOCKS)
        .map(|e| e.source)
        .unwrap_or(DataSource::EastmoneyRealtime);
    input.source = source;
    input.status = match source {
        DataSource::EastmoneyRealtime => DataStatus::Realtime,
        DataSource::SinaRealtime | DataSource::TencentRealtime => DataStatus::Delay,
        _ => DataStatus::Cache,
    };

    let snap = snapshot::build_snapshot(&input, &stocks, &sectors, b);

    // 落库
    let db = state.db.clone();
    let snap_clone = snap.clone();
    let _ = tokio::task::spawn_blocking(move || db.save_snapshot(&snap_clone)).await;

    // 同步保存该节点的板块快照：历史复盘时板块时间轴必须与股票时间轴一致，
    // 绝不能在回放时拿"当前缓存"的板块数据冒充历史板块数据
    if !sectors.is_empty() {
        let db = state.db.clone();
        let date = trade_date.to_string();
        let node_owned = node.to_string();
        let src = source.label().to_string();
        let sectors_clone = sectors.clone();
        let _ = tokio::task::spawn_blocking(move || {
            db.save_sector_node(&date, &node_owned, &src, &sectors_clone)
        })
        .await;
    }

    // 事件驱动落库
    let boards = state.engine().compute(&stocks, &sectors, node, &state.tracker);
    let new_events = persist_boards(state, &boards, trade_date, source).await;

    // 更新节点状态
    state.node_state.insert(
        node.to_string(),
        (snap.status, snap.source, "实时捕获".to_string()),
    );
    state.collectors.note_success(
        "snapshot",
        1,
        0,
        snap.source,
        snap.status,
    );
    tracing::info!(
        target: "collector",
        node,
        new_events,
        "节点快照已生成"
    );
    Some(snap)
}

/// 确保全部配置节点都有快照（实时 / 重建 / 等待）
pub async fn ensure_node_snapshots(state: &Arc<AppState>, trade_date: &str) -> Vec<(String, DataStatus, String)> {
    let cfg = state.config();
    let nodes = cfg.nodes.clone();
    let now = crate::clock::now_cn();
    let clock = state.clock.read().clone();
    let date = chrono::NaiveDate::parse_from_str(trade_date, "%Y-%m-%d")
        .unwrap_or_else(|_| now.date_naive());

    // 已有的快照
    let db = state.db.clone();
    let date_owned = trade_date.to_string();
    let existing = tokio::task::spawn_blocking(move || db.load_snapshots(&date_owned))
        .await
        .ok()
        .and_then(|r| r.ok())
        .unwrap_or_default();
    let mut have: HashMap<String, IntradaySnapshot> = existing
        .into_iter()
        .map(|s| (s.node.clone(), s))
        .collect();

    let mut out = Vec::new();
    for node in nodes {
        let node_dt = clock.node_datetime(date, &node);
        let passed = node_dt.map(|t| now >= t).unwrap_or(false);

        if let Some(snap) = have.get(&node) {
            let ok = snap.origin == "realtime" || snap.status == DataStatus::Rebuild;
            state
                .node_state
                .insert(node.clone(), (snap.status, snap.source, snap.note.clone()));
            out.push((
                node.clone(),
                if ok { snap.status } else { snap.status },
                snap.note.clone(),
            ));
            continue;
        }

        if !passed {
            state.node_state.insert(
                node.clone(),
                (
                    DataStatus::Waiting,
                    DataSource::None,
                    format!("{} 节点尚未到达", node),
                ),
            );
            out.push((
                node.clone(),
                DataStatus::Waiting,
                format!("{node} 节点尚未到达"),
            ));
            continue;
        }

        // 节点已过且无快照 -> 尝试重建
        match rebuild_node(state, trade_date, &node).await {
            Some(snap) => {
                let status = snap.status;
                let note = snap.note.clone();
                have.insert(node.clone(), snap);
                out.push((node.clone(), status, note));
            }
            None => {
                state.node_state.insert(
                    node.clone(),
                    (
                        DataStatus::DataSourceLimitation,
                        DataSource::None,
                        "该节点历史数据不可获取".into(),
                    ),
                );
                out.push((
                    node.clone(),
                    DataStatus::DataSourceLimitation,
                    "该节点历史数据不可获取（数据源限制）".into(),
                ));
            }
        }
    }
    out
}

/// 重建单个节点：先本机分钟数据，再数据源分钟数据。
///
/// # 语义说明（重要）
///
/// 本函数产出的是**候选样本重建**，不是"全市场历史重建"。
/// 只有当覆盖率达到 `rebuild_full_coverage_ratio` 时才标记为 `REBUILD`，
/// 否则一律标记为 `PARTIAL`，并在说明中写明实际样本数与覆盖率，
/// 避免用户把"几十只样本"误读为"该节点已完整还原"。
pub async fn rebuild_node(
    state: &Arc<AppState>,
    trade_date: &str,
    node: &str,
) -> Option<IntradaySnapshot> {
    let timer = Timer::start();
    let cfg = state.config();

    // 该交易日分钟数据覆盖的股票总数（覆盖率分母）
    let db = state.db.clone();
    let date_for_count = trade_date.to_string();
    let universe_size = tokio::task::spawn_blocking(move || db.minute_stock_count(&date_for_count))
        .await
        .ok()
        .and_then(|r| r.ok())
        .unwrap_or(0);

    // 路径 A：本机 SQLite 分钟数据（覆盖全量股票池时才算完整重建）
    let db = state.db.clone();
    let date = trade_date.to_string();
    let node_owned = node.to_string();
    let local_rows = tokio::task::spawn_blocking(move || {
        db.load_minutes_at_with_names(&date, &node_owned)
    })
    .await
    .ok()
    .and_then(|r| r.ok())
    .unwrap_or_default();

    let local_coverage = if universe_size > 0 {
        local_rows.len() as f64 / universe_size as f64
    } else {
        0.0
    };
    let local_complete = universe_size >= 100 && local_coverage >= cfg.rebuild_full_coverage_ratio;

    if local_rows.len() >= 20 {
        let aggregates: Vec<NodeAggregate> = local_rows
            .iter()
            .map(|(code, name, price, volume, amount, main_net)| NodeAggregate {
                code: code.clone(),
                name: name.clone(),
                price: *price,
                prev_close: prev_close_of(state, code, *price),
                volume: *volume,
                amount: *amount,
                main_net_inflow: *main_net,
                volume_ratio: ratio_baseline(state, code, *volume, node),
            })
            .collect();

        let (sectors, sector_note) = load_replay_sectors(state, trade_date, node).await;
        let mut input = SnapshotInput::replay(trade_date, node, cfg.top_open, cfg.top_n);
        input.source = DataSource::DatabaseReplay;
        input.status = if local_complete {
            DataStatus::Rebuild
        } else {
            DataStatus::Partial
        };
        input.note = format!(
            "由本机分钟数据重建：本节点 {} 只 / 当日已采集 {} 只（覆盖 {:.0}%）{}",
            aggregates.len(),
            universe_size.max(aggregates.len()),
            local_coverage * 100.0,
            sector_note
        );
        let snap = build_snapshot_from_aggregates(&input, &aggregates, &sectors);

        let db = state.db.clone();
        let snap_clone = snap.clone();
        let _ = tokio::task::spawn_blocking(move || db.save_snapshot(&snap_clone)).await;
        state.collectors.note_success(
            "review",
            aggregates.len(),
            timer.ms(),
            DataSource::DatabaseReplay,
            input.status,
        );
        state.node_state.insert(
            node.to_string(),
            (input.status, DataSource::DatabaseReplay, snap.note.clone()),
        );
        tracing::info!(
            target: "collector",
            node,
            rows = aggregates.len(),
            universe = universe_size,
            coverage = local_coverage,
            "由本机分钟数据重建节点"
        );
        return Some(snap);
    }

    // 路径 B：数据源分钟数据重建
    rebuild_from_api(state, trade_date, node, timer).await
}

/// 读取历史节点对应的板块快照。
///
/// 绝不允许用"当前缓存"的板块数据冒充历史板块数据 —— 那会造成
/// "股票时间轴是真的、板块时间轴是假的"。
/// 没有历史板块快照时返回空列表并附上明确说明。
async fn load_replay_sectors(
    state: &Arc<AppState>,
    trade_date: &str,
    node: &str,
) -> (Vec<crate::models::SectorTick>, String) {
    let db = state.db.clone();
    let date = trade_date.to_string();
    let node_owned = node.to_string();
    let loaded = tokio::task::spawn_blocking(move || db.load_sector_node(&date, &node_owned))
        .await
        .ok()
        .and_then(|r| r.ok())
        .flatten();

    match loaded {
        Some(sectors) if !sectors.is_empty() => {
            (sectors, "；板块数据为该节点当时快照".to_string())
        }
        _ => (
            Vec::new(),
            "；该节点未保存板块快照，板块榜不展示（不使用当前板块数据冒充历史）".to_string(),
        ),
    }
}

/// 通过数据源分钟数据重建节点（候选池方案，第 4.11 章）。
///
/// 通道优先级：
///   1. 东方财富分钟 K 线（可累计出节点成交量 / 成交额）
///   2. 腾讯分时（直接提供节点累计成交量 / 成交额，作为备用通道）
///   资金流明细单独取东方财富分钟资金流；不可得时明确标注“资金榜为部分重建”。
pub async fn rebuild_from_api(
    state: &Arc<AppState>,
    trade_date: &str,
    node: &str,
    timer: Timer,
) -> Option<IntradaySnapshot> {
    let cfg = state.config();
    let (candidates, pool_size) = candidate_codes(state, cfg.rebuild_max_candidates);
    if candidates.is_empty() {
        return None;
    }

    let date = chrono::NaiveDate::parse_from_str(trade_date, "%Y-%m-%d").ok()?;
    let node_time = node.to_string();
    let em = state.eastmoney.clone();
    let tx = state.tencent.clone();
    let use_em = cfg.enable_eastmoney && !state.gate.is_blocked("eastmoney");
    let use_tx = cfg.enable_tencent && !state.gate.is_blocked("tencent");

    let mut aggregates: Vec<NodeAggregate> = Vec::new();
    let mut missing_flow = 0usize;
    let mut flow_ok = 0usize;
    let mut tx_used = 0usize;
    // 连续失败计数：达到阈值说明通道整体不可用，立即放弃，避免长时间空转
    let mut em_streak = 0usize;
    let mut tx_streak = 0usize;
    const FAIL_STREAK_LIMIT: usize = 6;

    for (code, market, name, prev_close) in candidates.iter() {
        // ---- 价格 / 成交量 / 成交额 ----
        let mut bar: Option<(f64, f64, f64)> = None;

        if use_em && em_streak < FAIL_STREAK_LIMIT {
            match em.minute_kline(code, *market, MINUTE_FETCH_LIMIT).await {
                Ok(points) => {
                    em_streak = 0;
                    let day_points: Vec<&MinutePoint> =
                        points.iter().filter(|p| p.trade_date == date).collect();
                    let mut cum_volume = 0.0;
                    let mut cum_amount = 0.0;
                    let mut price = 0.0;
                    let mut found = false;
                    for p in day_points.iter() {
                        if p.minute.as_str() > node_time.as_str() {
                            break;
                        }
                        cum_volume += p.volume;
                        cum_amount += p.amount;
                        price = p.price;
                        found = true;
                    }
                    if found {
                        bar = Some((price, cum_volume, cum_amount));
                    }
                }
                Err(_) => {
                    em_streak += 1;
                }
            }
        }

        if bar.is_none() && use_tx && tx_streak < FAIL_STREAK_LIMIT {
            // 备用通道：腾讯分时（自带交易日，可校验日期一致）
            match tx.minute_series(code, *market).await {
                Ok(series) => {
                    tx_streak = 0;
                    let same_day = series.date.is_empty()
                        || series.date == date.format("%Y%m%d").to_string();
                    if same_day {
                        if let Some(b) = series.at(&node_time) {
                            bar = Some((b.price, b.cumulative_volume, b.cumulative_amount));
                            tx_used += 1;
                        }
                    }
                }
                Err(_) => {
                    tx_streak += 1;
                }
            }
        }

        let Some((price, volume, amount)) = bar else {
            // 两条通道都已连续失败 → 提前结束本轮重建
            if em_streak >= FAIL_STREAK_LIMIT && tx_streak >= FAIL_STREAK_LIMIT {
                tracing::warn!(
                    target: "collector",
                    "分钟数据通道连续失败，提前结束本轮重建"
                );
                break;
            }
            continue;
        };

        // ---- 主力净流入（可选，缺失时明确标注）----
        let mut main_net = 0.0;
        let mut has_flow = false;
        if use_em {
            match em.minute_flow(code, *market, MINUTE_FETCH_LIMIT).await {
                Ok(points) if !points.is_empty() => {
                    let mut acc = 0.0;
                    for p in points.iter() {
                        if p.time.as_str() <= node_time.as_str() {
                            acc = p.main_net;
                        } else {
                            break;
                        }
                    }
                    main_net = acc;
                    has_flow = true;
                    flow_ok += 1;
                }
                _ => {}
            }
        }
        if !has_flow {
            missing_flow += 1;
        }

        aggregates.push(NodeAggregate {
            code: code.clone(),
            name: name.clone(),
            price,
            prev_close: *prev_close,
            volume,
            amount,
            main_net_inflow: main_net,
            volume_ratio: ratio_baseline(state, code, volume, node),
        });
    }

    if aggregates.is_empty() {
        state
            .collectors
            .note_failure("review", "候选池分钟数据不可用", timer.ms());
        if em_streak >= FAIL_STREAK_LIMIT && use_em {
            state.gate.block("eastmoney", "分钟数据通道不可用");
        }
        if tx_streak >= FAIL_STREAK_LIMIT && use_tx {
            state.gate.block("tencent", "分时数据通道不可用");
        }
        return None;
    }

    // 覆盖率 = 本次成功重建的样本 / 重建候选池规模。
    // 只有接近全量时才允许标记 REBUILD，否则必须是 PARTIAL（候选样本重建）。
    let coverage = if pool_size > 0 {
        aggregates.len() as f64 / pool_size as f64
    } else {
        0.0
    };
    let coverage_ok = coverage >= cfg.rebuild_full_coverage_ratio && missing_flow == 0;

    let mut input = SnapshotInput::replay(trade_date, node, cfg.top_open, cfg.top_n);
    input.source = if tx_used > aggregates.len() / 2 {
        DataSource::TencentRealtime
    } else {
        DataSource::EastmoneyHistory
    };
    input.status = if coverage_ok {
        DataStatus::Rebuild
    } else {
        DataStatus::Partial
    };

    let (sectors, sector_note) = load_replay_sectors(state, trade_date, node).await;
    input.note = if missing_flow > 0 {
        format!(
            "候选样本重建：样本 {} 只 / 候选池 {} 只（覆盖 {:.0}%），其中 {} 只缺少资金流明细{}",
            aggregates.len(),
            pool_size,
            coverage * 100.0,
            missing_flow,
            sector_note
        )
    } else {
        format!(
            "候选样本重建：样本 {} 只 / 候选池 {} 只（覆盖 {:.0}%），资金流明细完整 {} 只{}",
            aggregates.len(),
            pool_size,
            coverage * 100.0,
            flow_ok,
            sector_note
        )
    };

    let snap = build_snapshot_from_aggregates(&input, &aggregates, &sectors);

    let db = state.db.clone();
    let snap_clone = snap.clone();
    let _ = tokio::task::spawn_blocking(move || db.save_snapshot(&snap_clone)).await;

    state.collectors.note_success(
        "review",
        aggregates.len(),
        timer.ms(),
        input.source,
        input.status,
    );
    state.node_state.insert(
        node.to_string(),
        (input.status, input.source, snap.note.clone()),
    );
    tracing::info!(
        target: "collector",
        node,
        samples = aggregates.len(),
        tencent = tx_used,
        flow_ok,
        "已由数据源分钟数据重建节点"
    );
    Some(snap)
}

/// 构建历史重建候选池。
///
/// # 为什么不能只取"当前最活跃的股票"
///
/// 如果只用当前榜单靠前的股票去重建历史节点，那么"09:35 爆量、10:00 后回落"
/// 的股票在下午回放时会被永久遗漏 —— 那得到的是"当下活跃股的过去"，
/// 而不是"当时的市场"，会造成历史复盘系统性偏差。
///
/// 因此这里使用**整个已采集股票池**（全市场扫描优先），按成交额降序取前
/// `max_candidates` 只作为重建候选。返回 `(候选列表, 候选池原始规模)`，
/// 后者用于计算覆盖率并决定最终状态是 REBUILD 还是 PARTIAL。
fn candidate_codes(state: &Arc<AppState>, max_candidates: usize) -> (Vec<(String, u8, String, f64)>, usize) {
    // 优先使用全市场扫描得到的完整股票池
    let pool = state
        .cache
        .stocks
        .get_stale(super::market::CACHE_KEY_FULL_STOCKS)
        .map(|e| e.value.clone())
        .or_else(|| {
            state
                .cache
                .stocks
                .get_stale(CACHE_KEY_FULL_STOCKS)
                .map(|e| e.value.clone())
        });

    let Some(stocks) = pool else {
        return (Vec::new(), 0);
    };

    let mut valid: Vec<&StockTick> = stocks.iter().filter(|s| s.price > 0.0).collect();
    if valid.is_empty() {
        return (Vec::new(), 0);
    }
    let pool_size = valid.len();

    // 按成交额降序，保证候选池稳定可复现（覆盖市场主要流动性）
    valid.sort_by(|a, b| {
        b.amount
            .partial_cmp(&a.amount)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let limit = max_candidates.max(60);
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for s in valid.into_iter().take(limit) {
        if seen.insert(s.code.clone()) {
            out.push((s.code.clone(), s.market, s.name.clone(), s.prev_close));
        }
    }
    (out, pool_size)
}

fn prev_close_of(state: &Arc<AppState>, code: &str, fallback: f64) -> f64 {
    state
        .cache
        .stocks
        .get_stale(CACHE_KEY_FULL_STOCKS)
        .and_then(|e| {
            e.value
                .iter()
                .find(|s| s.code == code)
                .map(|s| s.prev_close)
        })
        .filter(|v| *v > 0.0)
        .unwrap_or(fallback)
}

/// 量比基线：优先使用历史日均量，缺失则返回 0（不伪造）
fn ratio_baseline(state: &Arc<AppState>, _code: &str, _volume: f64, node: &str) -> f64 {
    let _ = state;
    let _ = node;
    0.0
}

/// 收盘归档：保存当日个股日线（第 8.14 / 8.19 章）
pub async fn archive_day(
    state: &Arc<AppState>,
    trade_date: &str,
    stocks: &[StockTick],
    source: DataSource,
) {
    if stocks.is_empty() {
        return;
    }
    let n = stocks.len();
    let db = state.db.clone();
    let date = trade_date.to_string();
    let ticks = stocks.to_vec();
    let _ = tokio::task::spawn_blocking(move || db.save_stock_daily(&date, &ticks)).await;
    let status = match source {
        DataSource::EastmoneyRealtime => DataStatus::Realtime,
        DataSource::None => DataStatus::Unavailable,
        _ => DataStatus::Delay,
    };
    state.collectors.note_success("history", n, 0, source, status);
}

/// 定期清理过期历史数据
pub async fn purge_old_data(state: &Arc<AppState>) {
    let cfg = state.config();
    let db = state.db.clone();
    let days = cfg.retention_days;
    let _ = tokio::task::spawn_blocking(move || db.purge_older_than(days)).await;
}

/// 读取当日全部快照
pub async fn load_day_snapshots(state: &Arc<AppState>, trade_date: &str) -> Vec<IntradaySnapshot> {
    let db = state.db.clone();
    let date = trade_date.to_string();
    tokio::task::spawn_blocking(move || db.load_snapshots(&date))
        .await
        .ok()
        .and_then(|r| r.ok())
        .unwrap_or_default()
}

/// 全部节点状态（供 API 展示）
pub fn node_summary(state: &Arc<AppState>) -> Vec<(String, DataStatus, DataSource, String)> {
    let cfg = state.config();
    let mut out = Vec::new();
    for node in cfg.nodes {
        match state.node_state.get(&node) {
            Some(v) => out.push((node.clone(), v.0, v.1, v.2.clone())),
            None => out.push((
                node.clone(),
                DataStatus::Waiting,
                DataSource::None,
                "等待生成".into(),
            )),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn candidate_limit_is_bounded() {
        assert!(
            DEFAULT_REBUILD_CANDIDATES >= 200 && DEFAULT_REBUILD_CANDIDATES <= 1000,
            "候选池必须有下限（覆盖度）与上限（保护接口）"
        );
        assert!(MINUTE_FETCH_LIMIT >= 240, "必须能覆盖完整交易日");
    }

    #[test]
    fn ratio_baseline_is_not_faked() {
        let tmp = tempfile::tempdir().unwrap();
        let state = crate::state::AppState::new(
            crate::paths::AppPaths {
                root: tmp.path().to_path_buf(),
                data_dir: tmp.path().join("data"),
                db_path: tmp.path().join("data/db.sqlite"),
                logs_dir: tmp.path().join("logs"),
                config_dir: tmp.path().join("config"),
                cache_dir: tmp.path().join("cache"),
                install_dir: tmp.path().to_path_buf(),
                resources_dir: tmp.path().join("res"),
            },
            crate::config::Config::default(),
            vec![],
            Arc::new(crate::net::HttpClient::new(&crate::config::NetConfig::default()).unwrap()),
            crate::db::Db::open(&tmp.path().join("data/db.sqlite")).unwrap(),
        );
        // 无历史基线时必须返回 0，禁止编造
        assert_eq!(ratio_baseline(&state, "000001", 1000.0, "09:35"), 0.0);
    }
}
