//! API 处理器。
//!
//! 只读取 AppState 与缓存，不做任何计算或网络请求。

use axum::Json;
use serde::Deserialize;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use std::sync::Arc;
use std::time::Instant;

use crate::cache::Entry;
use crate::collector::{capital, history, index, market, sector};
use crate::engine::snapshot as snap_engine;
use crate::models::{
    BoardResult, DataSource, DataStatus, Envelope, IndexTick, IntradaySnapshot, MarketBreadth,
    MarketFlowSeries, MinutePoint, SectorTick, StockAggregate, StockTick, SystemStatus,
};
use crate::state::AppState;

use super::dto::*;

#[derive(Debug, Deserialize)]
pub struct TimelineQuery {
    pub minute: String,
    pub trade_date: Option<String>,
}

fn now() -> chrono::DateTime<chrono::FixedOffset> {
    crate::clock::now_cn()
}

/// 把缓存项转换为带真实性元数据的信封
fn envelope_of<T: Clone>(entry: Arc<Entry<T>>, stale_message: &str) -> Envelope<T> {
    let expired = entry.is_expired(Instant::now());
    let (status, message) = if expired {
        (
            DataStatus::Cache,
            format!(
                "{}，正在使用 {} 缓存数据",
                stale_message,
                entry.updated_at.format("%H:%M:%S")
            ),
        )
    } else {
        (entry.status, entry.message.clone())
    };
    Envelope::build(
        entry.value.clone(),
        status,
        entry.source,
        entry.updated_at,
        message,
    )
}

fn empty_envelope<T: Default>(message: &str) -> Envelope<T> {
    Envelope::unavailable(T::default(), message)
}

/// 由缓存项投影出子对象，同时保留真实性元数据
fn project<T: Clone, U>(entry: &Entry<T>, f: impl FnOnce(&T) -> U) -> Entry<U> {
    Entry {
        value: f(&entry.value),
        source: entry.source,
        status: entry.status,
        updated_at: entry.updated_at,
        expires_at: entry.expires_at,
        message: entry.message.clone(),
    }
}

// ---------------- 健康检查 / 系统 ----------------

pub async fn health(State(state): State<Arc<AppState>>) -> Json<HealthResponse> {
    let clock = state.clock.read().clone();
    let status = clock.status();
    Json(HealthResponse {
        status: "ok".into(),
        version: crate::config::APP_VERSION.to_string(),
        service: "capital-flow-command-center".into(),
        uptime_secs: state.uptime_secs(),
        trade_date: state.current_trade_date(),
        market_status: format!("{status:?}"),
    })
}

pub async fn system_status(State(state): State<Arc<AppState>>) -> Json<SystemResponse> {
    let clock = state.clock.read().clone();
    let status = clock.status();
    let (ok_count, total) = state.collectors.healthy_count();
    let mut notes = state.startup_notes.read().clone();
    if ok_count < total {
        notes.push(format!("{} / {} 个采集任务运行正常", ok_count, total));
    }
    if state.is_degraded() {
        notes.push("部分数据通道正在降级运行，已自动切换备用数据源".into());
    }
    let gated_sources: Vec<crate::models::GatedSource> = state
        .gate
        .snapshot()
        .into_iter()
        .map(|(name, remaining_secs, reason)| crate::models::GatedSource {
            label: source_label(&name).to_string(),
            name,
            remaining_secs,
            reason,
        })
        .collect();
    if !gated_sources.is_empty() {
        notes.push(format!(
            "{} 个数据通道处于冷却，已自动使用备用通道",
            gated_sources.len()
        ));
    }

    Json(SystemResponse {
        status: SystemStatus {
            version: crate::config::APP_VERSION.to_string(),
            status: "running".into(),
            market_status: format!("{status:?}"),
            market_status_label: status.label().to_string(),
            trade_date: state.current_trade_date(),
            server_time: now().format("%Y-%m-%d %H:%M:%S").to_string(),
            uptime_secs: state.uptime_secs(),
            collectors: state.collectors.all(),
            net: state.http.stats(),
            db: state.db.stats(),
            cache: state.cache.stats(),
            notes,
            gated_sources,
        },
    })
}

fn source_label(name: &str) -> &'static str {
    match name {
        "eastmoney" => "东方财富",
        "tencent" => "腾讯财经",
        "sina" => "新浪财经",
        _ => "未知通道",
    }
}

pub async fn get_config(State(state): State<Arc<AppState>>) -> Json<crate::config::Config> {
    Json(state.config())
}

pub async fn put_config(
    State(state): State<Arc<AppState>>,
    Json(cfg): Json<crate::config::Config>,
) -> Result<Json<MessageResponse>, (StatusCode, Json<MessageResponse>)> {
    if cfg.port < 1024 || cfg.top_n == 0 {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(MessageResponse {
                ok: false,
                message: "配置校验失败：端口或榜单容量非法".into(),
            }),
        ));
    }
    if let Err(err) = cfg.save(&state.paths) {
        tracing::warn!(target: "app", "配置保存失败: {err}");
    }
    state.set_config(cfg);
    Ok(Json(MessageResponse {
        ok: true,
        message: "配置已保存并即时生效".into(),
    }))
}

/// 前端异常上报：写入 error 日志，便于离线排查（第 6.11 章）
pub async fn client_log(Json(req): Json<ClientLogRequest>) -> Json<MessageResponse> {
    tracing::error!(target: "app", "[frontend] {}", req.detail);
    Json(MessageResponse {
        ok: true,
        message: "已记录".into(),
    })
}

pub async fn shutdown(State(state): State<Arc<AppState>>) -> Json<MessageResponse> {
    let first = state.begin_shutdown();
    state.persist_state();
    state.shutdown.notify_waiters();
    tracing::info!(target: "app", "收到关闭请求，已保存状态");

    // 看门狗：优雅退出有上限，超时则强制结束，绝不留下后台幽灵进程（第 3.5 章）
    tokio::spawn(async {
        tokio::time::sleep(std::time::Duration::from_secs(6)).await;
        tracing::warn!(target: "app", "优雅退出超时，强制结束进程");
        std::process::exit(0);
    });

    Json(MessageResponse {
        ok: true,
        message: if first {
            "正在安全退出…".into()
        } else {
            "退出流程已在进行中".into()
        },
    })
}

// ---------------- 市场 ----------------

pub async fn indices(State(state): State<Arc<AppState>>) -> Json<Envelope<Vec<IndexTick>>> {
    match state.cache.indices.get(index::CACHE_KEY_INDICES) {
        Some(e) => Json(envelope_of(e, "指数数据源暂时异常")),
        None => match state.cache.indices.get_stale(index::CACHE_KEY_INDICES) {
            Some(e) => Json(envelope_of(e, "指数数据源暂时异常")),
            None => Json(empty_envelope("指数数据正在初始化，请稍候")),
        },
    }
}

pub async fn breadth(State(state): State<Arc<AppState>>) -> Json<Envelope<MarketBreadth>> {
    if let Some(e) = state.cache.breadth.get("breadth_full") {
        return Json(envelope_of(e, "全市场扫描暂时异常"));
    }
    // 尚未拿到全市场广度快照时，用全市场股票池现场计算。
    //
    // 注意：这个池子**就是全市场**（本地证券列表 + 批量行情），
    // 不再是早期那个由排行榜接口拼出来、上限 1200 只的有偏样本。
    if let Some(entry) = state.cache.stocks.get_stale(market::CACHE_KEY_FULL_STOCKS) {
        // 只有达到全市场规模才允许展示涨跌家数 ——
        // 绝不拿小样本冒充市场统计（有偏样本算出的涨停家数会严重高估）。
        if entry.value.len() >= crate::collector::capital::FULL_MARKET_MIN_STOCKS {
            let mut b = crate::engine::breadth(&entry.value);
            b.sample_size = entry.value.len() as i32;
            b.scope = "full".into();
            b.source = entry.source.label().to_string();
            b.sentiment = crate::engine::sentiment_score(&entry.value);
            let mut env = Envelope::build(
                b,
                entry.status,
                entry.source,
                entry.updated_at,
                format!("由全市场股票池现场统计（{} 只）", entry.value.len()),
            );
            if entry.is_expired(Instant::now()) {
                env.status = DataStatus::Cache;
            }
            return Json(env);
        }
        return Json(empty_envelope(&format!(
            "全市场扫描进行中：当前股票池仅 {} 只，未达到全市场规模，暂不展示涨跌家数",
            entry.value.len()
        )));
    }
    Json(empty_envelope("全市场扫描进行中，涨跌家数将在扫描完成后展示"))
}

pub async fn sectors(
    State(state): State<Arc<AppState>>,
    Query(q): Query<ListQuery>,
) -> Json<Envelope<Vec<SectorTick>>> {
    let key = match q.kind.as_deref() {
        Some("concept") => sector::CACHE_KEY_CONCEPT,
        Some("industry") => sector::CACHE_KEY_INDUSTRY,
        _ => sector::CACHE_KEY_ALL,
    };
    match state.cache.sectors.get(key) {
        Some(e) => {
            if let Some(limit) = q.limit {
                let entry = Arc::new(project(&e, |v| v.iter().take(limit).cloned().collect::<Vec<_>>()));
                Json(envelope_of(entry, "板块数据源暂时异常"))
            } else {
                Json(envelope_of(e, "板块数据源暂时异常"))
            }
        }
        None => match state.cache.sectors.get_stale(key) {
            Some(e) => Json(envelope_of(e, "板块数据源暂时异常")),
            None => Json(empty_envelope("板块数据正在初始化，请稍候")),
        },
    }
}

pub async fn boards(State(state): State<Arc<AppState>>) -> Json<Envelope<Vec<BoardResult>>> {
    match state.cache.boards.get("boards") {
        Some(e) => Json(envelope_of(e, "榜单数据源暂时异常")),
        None => match state.cache.boards.get_stale("boards") {
            Some(e) => Json(envelope_of(e, "榜单数据源暂时异常")),
            None => Json(empty_envelope("榜单正在计算，请稍候")),
        },
    }
}

pub async fn board_by_id(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
) -> Result<Json<Envelope<BoardResult>>, (StatusCode, Json<MessageResponse>)> {
    let entry = state
        .cache
        .boards
        .get("boards")
        .or_else(|| state.cache.boards.get_stale("boards"));
    match entry {
        Some(e) => match e.value.iter().find(|b| b.id == id) {
            Some(board) => Ok(Json(envelope_of(
                Arc::new(project(&e, |_| board.clone())),
                "榜单数据源暂时异常",
            ))),
            None => Err((
                StatusCode::NOT_FOUND,
                Json(MessageResponse {
                    ok: false,
                    message: format!("榜单 {id} 不存在"),
                }),
            )),
        },
        None => Err((
            StatusCode::SERVICE_UNAVAILABLE,
            Json(MessageResponse {
                ok: false,
                message: "榜单正在计算，请稍候".into(),
            }),
        )),
    }
}

pub async fn universe(
    State(state): State<Arc<AppState>>,
    Query(q): Query<ListQuery>,
) -> Json<Envelope<Vec<StockTick>>> {
    let limit = q.limit.unwrap_or(200).clamp(1, 6000);
    match state
        .cache
        .stocks
        .get(market::CACHE_KEY_FULL_STOCKS)
        .or_else(|| state.cache.stocks.get_stale(market::CACHE_KEY_FULL_STOCKS))
    {
        Some(e) => {
            let mut sorted = e.value.clone();
            sorted.sort_by(|a, b| {
                b.amount
                    .partial_cmp(&a.amount)
                    .unwrap_or(std::cmp::Ordering::Equal)
            });
            sorted.truncate(limit);
            Json(envelope_of(
                Arc::new(project(&e, |_| sorted)),
                "个股数据源暂时异常",
            ))
        }
        None => Json(empty_envelope("个股数据正在初始化，请稍候")),
    }
}

pub async fn stock_detail(
    State(state): State<Arc<AppState>>,
    Path(code): Path<String>,
) -> Result<Json<StockDetailResponse>, (StatusCode, Json<MessageResponse>)> {
    let entry = state
        .cache
        .stocks
        .get(market::CACHE_KEY_FULL_STOCKS)
        .or_else(|| state.cache.stocks.get_stale(market::CACHE_KEY_FULL_STOCKS));

    let tick_env = match &entry {
        Some(e) => match e.value.iter().find(|s| s.code == code) {
            Some(t) => envelope_of(
                Arc::new(project(&e, |_| t.clone())),
                "个股数据源暂时异常",
            ),
            None => {
                // 不在活跃池中，尝试实时快照
                let market = crate::source::market_of(&code);
                match market::fetch_quotes(&state, &[(code.clone(), market)]).await {
                    Ok((ticks, src)) if !ticks.is_empty() => Envelope::realtime(
                        ticks[0].clone(),
                        src,
                        now(),
                    ),
                    _ => Envelope::unavailable(
                        StockTick {
                            code: code.clone(),
                            ..Default::default()
                        },
                        "该代码暂无数据，可能不在当前扫描范围内",
                    ),
                }
            }
        },
        None => Envelope::unavailable(
            StockTick {
                code: code.clone(),
                ..Default::default()
            },
            "个股数据正在初始化，请稍候",
        ),
    };

    let market = if tick_env.data.market == 0 && !tick_env.data.code.is_empty() {
        crate::source::market_of(&tick_env.data.code)
    } else {
        tick_env.data.market
    };

    // 资金流序列：优先缓存，其次实时拉取
    let flow_env = if let Some(e) = state
        .cache
        .flow_series
        .get(&format!("{}{code}", capital::CACHE_PREFIX_STOCK))
        .or_else(|| {
            state
                .cache
                .flow_series
                .get_stale(&format!("{}{code}", capital::CACHE_PREFIX_STOCK))
        }) {
        envelope_of(e, "资金流数据源暂时异常")
    } else {
        let (points, src, status, msg) = capital::collect_flow(&state, &code, market, 300).await;
        if points.is_empty() {
            Envelope::build(Vec::new(), status, src, now(), msg)
        } else {
            state.cache.flow_series.put_value(
                format!("{}{code}", capital::CACHE_PREFIX_STOCK),
                points.clone(),
                src,
                status,
                std::time::Duration::from_secs(60),
            );
            Envelope::build(points, status, src, now(), msg)
        }
    };

    // 分钟数据：来自本机数据库
    let trade_date = state.current_trade_date();
    let db = state.db.clone();
    let code_owned = code.clone();
    let date_owned = trade_date.clone();
    let minutes = tokio::task::spawn_blocking(move || db.load_stock_minutes(&date_owned, &code_owned))
        .await
        .ok()
        .and_then(|r| r.ok())
        .unwrap_or_default();
    let minute_env = if minutes.is_empty() {
        Envelope::build(
            Vec::<MinutePoint>::new(),
            DataStatus::Waiting,
            DataSource::DatabaseReplay,
            now(),
            "本机分钟数据将在程序运行后逐步积累".into(),
        )
    } else {
        Envelope::build(
            minutes,
            DataStatus::Realtime,
            DataSource::DatabaseReplay,
            now(),
            String::new(),
        )
    };

    let aggregates = state
        .cache
        .aggregates
        .get_stale("aggregates")
        .map(|e| {
            e.value
                .iter()
                .filter(|a| a.code == code)
                .cloned()
                .collect()
        })
        .unwrap_or_default();

    Ok(Json(StockDetailResponse {
        tick: tick_env,
        flow: flow_env,
        aggregates,
        minute: minute_env,
    }))
}

pub async fn capital_curve(
    State(state): State<Arc<AppState>>,
) -> Json<Envelope<MarketFlowSeries>> {
    match state
        .cache
        .market_flow
        .get(capital::CACHE_PREFIX_INDEX)
        .or_else(|| state.cache.market_flow.get_stale(capital::CACHE_PREFIX_INDEX))
    {
        Some(e) => Json(envelope_of(e, "资金曲线数据源暂时异常")),
        None => Json(empty_envelope("资金曲线正在积累数据，请稍候")),
    }
}

// ---------------- 节点 / 开盘检测 / 异动 / 精选 ----------------

fn sorted_snapshots(mut snaps: Vec<IntradaySnapshot>) -> Vec<IntradaySnapshot> {
    snaps.sort_by(|a, b| a.node.cmp(&b.node));
    snaps
}

pub async fn node_states(State(state): State<Arc<AppState>>) -> Json<Vec<NodeState>> {
    let trade_date = state.current_trade_date();
    let snaps = history::load_day_snapshots(&state, &trade_date).await;
    let map: std::collections::HashMap<String, IntradaySnapshot> =
        snaps.into_iter().map(|s| (s.node.clone(), s)).collect();
    let cfg = state.config();
    let mut out = Vec::new();
    for node in cfg.nodes {
        match map.get(&node) {
            Some(s) => out.push(NodeState {
                node: node.clone(),
                status: s.status,
                status_label: s.status.label().to_string(),
                source: s.source,
                source_label: s.source.label().to_string(),
                note: s.note.clone(),
                available: true,
            }),
            None => {
                let (status, note) = state
                    .node_state
                    .get(&node)
                    .map(|v| (v.0, v.2.clone()))
                    .unwrap_or((DataStatus::Waiting, "等待生成".into()));
                out.push(NodeState {
                    node: node.clone(),
                    status,
                    status_label: status.label().to_string(),
                    source: DataSource::None,
                    source_label: DataSource::None.label().to_string(),
                    note,
                    available: false,
                });
            }
        }
    }
    Json(out)
}

pub async fn opening(State(state): State<Arc<AppState>>) -> Json<OpeningResponse> {
    let trade_date = state.current_trade_date();
    let snaps = sorted_snapshots(history::load_day_snapshots(&state, &trade_date).await);
    let cfg = state.config();
    let mut nodes = Vec::new();
    for node in &cfg.nodes {
        match snaps.iter().find(|s| &s.node == node) {
            Some(s) => nodes.push(OpeningNode {
                node: node.clone(),
                status: s.status,
                status_label: s.status.label().to_string(),
                source: s.source,
                source_label: s.source.label().to_string(),
                created_at: s.created_at.clone(),
                origin: s.origin.clone(),
                note: s.note.clone(),
                volume_top: s.volume_top.clone(),
                netflow_top: s.netflow_top.clone(),
            }),
            None => {
                let (status, note) = state
                    .node_state
                    .get(node)
                    .map(|v| (v.0, v.2.clone()))
                    .unwrap_or((DataStatus::Waiting, "等待生成".into()));
                nodes.push(OpeningNode {
                    node: node.clone(),
                    status,
                    status_label: status.label().to_string(),
                    source: DataSource::None,
                    source_label: DataSource::None.label().to_string(),
                    created_at: "--:--:--".into(),
                    origin: "pending".into(),
                    note,
                    volume_top: Vec::new(),
                    netflow_top: Vec::new(),
                });
            }
        }
    }
    let note = if nodes.iter().any(|n| n.available()) {
        String::new()
    } else {
        "早盘节点数据将在 09:35 / 09:40 / 09:45 自动生成；程序晚启动时会自动重建".into()
    };
    Json(OpeningResponse {
        trade_date,
        nodes,
        note,
    })
}

impl OpeningNode {
    fn available(&self) -> bool {
        self.status.is_usable()
    }
}

pub async fn anomaly(State(state): State<Arc<AppState>>) -> Json<AnomalyResponse> {
    let trade_date = state.current_trade_date();
    let snaps = sorted_snapshots(history::load_day_snapshots(&state, &trade_date).await);
    let cfg = state.config();
    let mut nodes = Vec::new();
    for node in &cfg.nodes {
        match snaps.iter().find(|s| &s.node == node) {
            Some(s) => nodes.push(AnomalyNode {
                node: node.clone(),
                status: s.status,
                status_label: s.status.label().to_string(),
                origin: s.origin.clone(),
                ratio_top: s.ratio_top.clone(),
                volume_top: s.anomaly_volume_top.clone(),
                netflow_top: s.anomaly_netflow_top.clone(),
            }),
            None => {
                let (status, _) = state
                    .node_state
                    .get(node)
                    .map(|v| (v.0, v.2.clone()))
                    .unwrap_or((DataStatus::Waiting, String::new()));
                nodes.push(AnomalyNode {
                    node: node.clone(),
                    status,
                    status_label: status.label().to_string(),
                    origin: "pending".into(),
                    ratio_top: Vec::new(),
                    volume_top: Vec::new(),
                    netflow_top: Vec::new(),
                });
            }
        }
    }
    Json(AnomalyResponse {
        trade_date,
        nodes,
        note: "量比榜需要数据源提供量比字段；历史重建时若缺少基线会明确标注".into(),
    })
}

pub async fn selection(State(state): State<Arc<AppState>>) -> Json<SelectionResponse> {
    let trade_date = state.current_trade_date();
    let as_of = now().format("%H:%M").to_string();
    let aggregates = state
        .cache
        .aggregates
        .get_stale("aggregates")
        .map(|e| e.value.clone())
        .unwrap_or_else(|| {
            state
                .tracker
                .aggregates(state.engine().boards(), state.config().top_n * 3)
        });
    Json(SelectionResponse {
        trade_date,
        as_of,
        stocks: aggregates,
        total_events: state.tracker.total_events(),
        note: "上榜次数为事件驱动统计：首次进入记 1 次，持续停留不重复计数，掉出后再次进入新增 1 次".into(),
    })
}

pub async fn rebuild_node(
    State(state): State<Arc<AppState>>,
    Path(node): Path<String>,
) -> Json<MessageResponse> {
    let trade_date = state.current_trade_date();
    let cfg = state.config();
    if !cfg.nodes.contains(&node) {
        return Json(MessageResponse {
            ok: false,
            message: format!("{node} 不是已配置的早盘节点"),
        });
    }
    match history::rebuild_node(&state, &trade_date, &node).await {
        Some(snap) => Json(MessageResponse {
            ok: true,
            message: format!("{} 节点已重建（{}）", node, snap.note),
        }),
        None => Json(MessageResponse {
            ok: false,
            message: format!("{node} 节点重建失败：历史分钟数据不可获取"),
        }),
    }
}

pub async fn node_detail(
    State(state): State<Arc<AppState>>,
    Path(node): Path<String>,
) -> Result<Json<IntradaySnapshot>, (StatusCode, Json<MessageResponse>)> {
    let trade_date = state.current_trade_date();
    let snaps = history::load_day_snapshots(&state, &trade_date).await;
    match snaps.into_iter().find(|s| s.node == node) {
        Some(s) => Ok(Json(s)),
        None => Err((
            StatusCode::NOT_FOUND,
            Json(MessageResponse {
                ok: false,
                message: format!("{node} 节点数据尚未生成"),
            }),
        )),
    }
}

// ---------------- 统一数据快照（首页四个核心模块共用） ----------------

/// 统一数据快照。
///
/// 首页加载时先取这一个快照，指数分时 / 板块资金 / 资金累计曲线 /
/// 时点数据面板全部从它读取 —— 不允许各组件自己去取"当前时间"或"最新数据"。
pub async fn snapshot(State(state): State<Arc<AppState>>) -> Json<SnapshotResponse> {
    let time = crate::market_time::resolve(&state);
    let trade_date = time.trade_date.clone();
    let store = state.history.clone();
    let repo = state.repo.clone();
    let date = trade_date.clone();

    // 先确定"序列实际来自哪一天"：
    //   优先 trade_date；该日分钟数据未落盘时回退到历史库中最近一个已落盘交易日。
    //   回退必须显式上报（series_fallback），不允许把旧数据当作当天数据。
    let store_probe = state.history.clone();
    let d_probe = trade_date.clone();
    let series_date = tokio::task::spawn_blocking(move || {
        resolve_series_date(&store_probe, &d_probe)
    })
    .await
    .unwrap_or_else(|_| date.clone());

    let series_fallback = series_date != trade_date;

    let store2 = store.clone();
    let repo2 = repo.clone();
    let sd = series_date.clone();
    let (index_series, flow_series) = tokio::task::spawn_blocking(move || {
        let idx = crate::history::preload::latest_index_series(&store2, Some(&repo2), &sd);
        let flow = crate::history::preload::latest_flow_series(&store2, Some(&repo2), &sd);
        (idx, flow)
    })
    .await
    .unwrap_or_default();

    let index_complete = crate::history::preload::is_complete_trading_day(&index_series);
    let flow_complete = crate::history::preload::is_complete_trading_day(&flow_series);

    // 分时图基准（昨收）：只在"画的确实是当前交易日"时才采用，
    // 否则跨交易日的昨收会把分时图整体画偏。
    let (index_prev_close, index_code, index_name) = if series_fallback {
        (None, "000001".to_string(), "上证指数".to_string())
    } else {
        index_baseline(&state)
    };

    let coverage = state.coverage.read().clone();
    let mut missing = Vec::new();
    if index_series.is_empty() {
        missing.push("指数分时");
    }
    if flow_series.is_empty() {
        missing.push("两市资金曲线");
    }
    let mut missing_note = if missing.is_empty() {
        String::new()
    } else {
        format!(
            "{} 在 {} 不可用；原因为数据源仅提供最近一个交易日的分钟数据，更早日期需由本程序逐日累积",
            missing.join("、"),
            trade_date
        )
    };
    if series_fallback && !index_series.is_empty() {
        let note = format!(
            "{trade_date} 的分钟数据尚未落盘，当前展示的是历史库中最近一个交易日 {series_date} 的数据"
        );
        if missing_note.is_empty() {
            missing_note = note;
        } else {
            missing_note = format!("{missing_note}；{note}");
        }
    }

    // 当日汇总一律在后端算好，前端只展示
    let index_summary = summarize_series(
        &index_series,
        index_prev_close,
        |b| b.close,
        true,
        if index_prev_close.is_some() {
            format!("{series_date} 指数分时，基准为昨收 {:.2}", index_prev_close.unwrap_or(0.0))
        } else {
            format!("{series_date} 指数分时；昨收不可得，涨跌幅暂不展示")
        },
    );
    let flow_summary = summarize_series(
        &flow_series,
        None,
        |b| b.main_net_inflow,
        false,
        format!(
            "{series_date} 数据源：东方财富大盘资金流（fflow/kline 分钟接口，secid=1.000001），\
             接口本身即为**累计**口径，本程序未做任何累加；\
             覆盖范围（沪市 / 沪深两市）以数据源口径为准"
        ),
    );

    Json(SnapshotResponse {
        time,
        trade_date,
        series_date,
        series_fallback,
        index_series,
        flow_series,
        index_complete,
        flow_complete,
        index_prev_close,
        index_code,
        index_name,
        index_summary,
        flow_summary,
        coverage,
        capability_note: crate::history::preload::source_capability_note(),
        missing_note,
    })
}

/// 确定分钟序列实际可用的交易日。
///
/// 优先返回 `preferred`；该日在历史库中没有任何序列时，回退到
/// `series/` 下最近一个已落盘的交易日（指数分时与两市资金曲线的并集）。
/// 库中完全无数据时原样返回 `preferred`（前端据此显示"尚未落盘"）。
///
/// 这里只做目录列举（`series_dates`），不逐日发起查询 ——
/// 90 个交易日逐日探测会产生上百次 DuckDB 查询，首页首屏会被拖垮。
fn resolve_series_date(
    store: &crate::history::MinuteStore,
    preferred: &str,
) -> String {
    let mut dates: Vec<String> = store
        .series_dates("index", crate::history::preload::SH_INDEX_SECID);
    dates.extend(
        store.series_dates("market_flow", crate::history::preload::SH_INDEX_SECID),
    );
    if dates.iter().any(|d| d == preferred) {
        return preferred.to_string();
    }
    // 只回退到**不晚于** preferred 的交易日，绝不用未来数据
    dates
        .into_iter()
        .filter(|d| d.as_str() <= preferred)
        .max()
        .unwrap_or_else(|| preferred.to_string())
}

/// 取上证指数昨收作为分时图基准。
///
/// 返回 `(昨收, 代码, 名称)`。行情缓存不可用时昨收为 `None`，
/// 前端此时退化为"以 09:31 开盘点位为基准"，不伪造昨收。
fn index_baseline(state: &Arc<AppState>) -> (Option<f64>, String, String) {
    let entry = state
        .cache
        .indices
        .get(crate::collector::index::CACHE_KEY_INDICES)
        .or_else(|| {
            state
                .cache
                .indices
                .get_stale(crate::collector::index::CACHE_KEY_INDICES)
        });
    let Some(entry) = entry else {
        return (None, "000001".into(), "上证指数".into());
    };
    // 上证指数：代码 000001（指数缓存里只有指数，不会与深市个股混淆）
    let tick = entry
        .value
        .iter()
        .find(|t| t.code == "000001")
        .or_else(|| entry.value.first());
    match tick {
        Some(t) => (
            if t.prev_close > 0.0 {
                Some(t.prev_close)
            } else {
                None
            },
            t.code.clone(),
            if t.name.is_empty() {
                "上证指数".into()
            } else {
                t.name.clone()
            },
        ),
        None => (None, "000001".into(), "上证指数".into()),
    }
}

/// 汇总一条分钟序列（后端计算，前端不做任何业务计算）。
///
/// `pick` 决定"用哪一列作为序列值"：指数分时用 `close`，
/// 两市资金曲线用 `main_net_inflow`（东财 fflow 为**累计**口径）。
///
/// `extremes_from_bar` 决定"最高/最低"的取法，两条序列语义不同：
///   * 指数分时 → `true`：取每根 bar 自带的 `high`/`low` 的极值，
///     这才是当日的真实最高/最低点位（分时图画的是收盘价，但极值不是收盘价的极值）；
///   * 两市资金曲线 → `false`：取 `pick` 的极值，因为累计曲线的峰值/谷值
///     就是累计值本身的极值（bar 的 high/low 列被复用存放超大单/大单，不可用）。
fn summarize_series(
    bars: &[crate::history::MinuteBar],
    baseline: Option<f64>,
    pick: fn(&crate::history::MinuteBar) -> f64,
    extremes_from_bar: bool,
    note: String,
) -> crate::api::dto::SeriesSummary {
    if bars.is_empty() {
        return crate::api::dto::SeriesSummary {
            note,
            ..Default::default()
        };
    }
    // 防御性排序：DuckDB 路径与 Parquet 路径都已有序，这里再保证一次，
    // 避免"首/末"取错导致整条曲线的涨跌幅算反。
    let mut ordered: Vec<&crate::history::MinuteBar> = bars.iter().collect();
    ordered.sort_by(|a, b| a.minute.cmp(&b.minute));

    let first = ordered[0];
    let last = *ordered.last().unwrap();
    let mut high = f64::NEG_INFINITY;
    let mut low = f64::INFINITY;
    let mut amount = 0.0;
    let mut volume = 0.0;
    for b in &ordered {
        let (hi, lo) = if extremes_from_bar {
            (b.high, b.low)
        } else {
            let v = pick(b);
            (v, v)
        };
        if hi > high {
            high = hi;
        }
        if lo < low {
            low = lo;
        }
        amount += b.amount;
        volume += b.volume;
    }
    let open = pick(first);
    let last_v = pick(last);
    let (change, change_pct, amplitude) = match baseline {
        Some(base) if base > 0.0 => (
            Some(last_v - base),
            Some((last_v - base) / base * 100.0),
            Some((high - low) / base * 100.0),
        ),
        _ => (None, None, None),
    };

    crate::api::dto::SeriesSummary {
        points: ordered.len(),
        first_minute: first.minute.clone(),
        last_minute: last.minute.clone(),
        open,
        last: last_v,
        high,
        low,
        change,
        change_pct,
        amplitude,
        amount,
        volume,
        note,
    }
}

/// 时点数据面板：读取与曲线**同一份数据集、同一交易日**的指定分钟。
pub async fn timeline_point(
    State(state): State<Arc<AppState>>,
    Query(q): Query<TimelineQuery>,
) -> Json<TimelinePointResponse> {
    let time = crate::market_time::resolve(&state);
    let trade_date = q.trade_date.clone().unwrap_or(time.trade_date.clone());
    let minute = q.minute.clone();

    let repo = state.repo.clone();
    let (d, m) = (trade_date.clone(), minute.clone());
    let bars = tokio::task::spawn_blocking(move || {
        repo.series("market_flow", crate::history::preload::SH_INDEX_SECID, &d)
            .unwrap_or_default()
            .into_iter()
            .find(|b| b.minute == m)
    })
    .await
    .ok()
    .flatten();

    let repo2 = state.repo.clone();
    let (d2, m2) = (trade_date.clone(), minute.clone());
    let idx = tokio::task::spawn_blocking(move || {
        repo2
            .series("index", crate::history::preload::SH_INDEX_SECID, &d2)
            .unwrap_or_default()
            .into_iter()
            .find(|b| b.minute == m2)
    })
    .await
    .ok()
    .flatten();

    let available = bars.is_some() || idx.is_some();
    Json(TimelinePointResponse {
        trade_date: trade_date.clone(),
        minute: minute.clone(),
        index_close: idx.as_ref().map(|b| b.close),
        main_net: bars.as_ref().map(|b| b.main_net_inflow),
        super_large: bars.as_ref().map(|b| b.high),
        large: bars.as_ref().map(|b| b.low),
        medium: bars.as_ref().map(|b| b.volume),
        small: bars.as_ref().map(|b| b.amount),
        available,
        note: if available {
            format!("{trade_date} {minute} 的真实历史分钟数据")
        } else {
            format!("{trade_date} {minute} 无历史分钟数据（未累积或数据源不可回溯）")
        },
    })
}

/// 历史数据状态诊断（第 14 章：状态清单）。
pub async fn history_status(State(state): State<Arc<AppState>>) -> Json<HistoryStatusResponse> {
    let cfg = state.config();
    let repo = state.repo.clone();
    let db = state.db.clone();

    let (version, dates, manifest) = tokio::task::spawn_blocking(move || {
        let v = repo.version();
        let d = repo.trading_dates().unwrap_or_default();
        let m = db.load_manifest().unwrap_or_default();
        (v, d, m)
    })
    .await
    .unwrap_or_else(|_| ("unknown".into(), Vec::new(), Vec::new()));

    let coverage = state.coverage.read().clone();
    let stats = state.history.stats();

    let capability: Vec<GranularityCapability> = [1u32, 5, 15, 30, 60]
        .iter()
        .filter_map(|m| crate::history::Granularity::from_minutes(*m))
        .map(|g| GranularityCapability {
            minutes: g.minutes(),
            label: g.label().to_string(),
            source_max_days: g.source_max_days(),
            meets_requirement: (g.source_max_days() as i64) >= cfg.history_days,
        })
        .collect();

    let g = crate::history::Granularity::from_minutes(cfg.history_granularity)
        .unwrap_or(crate::history::Granularity::Min1);

    Json(HistoryStatusResponse {
        duckdb_version: version,
        root: state.paths.history_dir().display().to_string(),
        size_bytes: stats.size_bytes,
        dates,
        coverage: coverage.clone(),
        manifest,
        granularity_capability: capability,
        granularity: g.minutes(),
        granularity_label: g.label().to_string(),
        capability_note: crate::history::preload::source_capability_note(),
        missing_note: if coverage.dates_minute_missing.is_empty() {
            String::new()
        } else {
            format!(
                "缺失 {} 个交易日：{}",
                coverage.dates_minute_missing.len(),
                coverage
                    .dates_minute_missing
                    .iter()
                    .take(10)
                    .cloned()
                    .collect::<Vec<_>>()
                    .join("、")
            )
        },
    })
}

/// 手动触发一次历史数据增量补齐。
pub async fn history_sync(State(state): State<Arc<AppState>>) -> Json<MessageResponse> {
    let store = state.history.clone();
    let fetcher = crate::history::HistoricalFetcher::new(state.clone(), store.clone());
    let g = crate::history::Granularity::from_minutes(state.config().history_granularity)
        .unwrap_or(crate::history::Granularity::Min1);
    let days = state.config().history_days;

    let reports = tokio::task::spawn_blocking(move || {
        // plan 需要读缓存，放到阻塞线程里执行（内部只读）
        let plan = fetcher.plan(days, g);
        plan
    })
    .await
    .ok();

    let Some(plan) = reports else {
        return Json(MessageResponse {
            ok: false,
            message: "历史补齐计划生成失败".into(),
        });
    };

    let missing = plan.dates_missing.len();
    if missing == 0 {
        return Json(MessageResponse {
            ok: true,
            message: format!(
                "历史库已完整（{}，{} 个交易日）",
                g.label(),
                plan.present_per_date.len()
            ),
        });
    }

    // 后台执行，避免阻塞接口
    let bg_state = state.clone();
    let bg_store = store.clone();
    tokio::spawn(async move {
        let f = crate::history::HistoricalFetcher::new(bg_state.clone(), bg_store);
        let reports = f.fetch_plan(&plan).await;
        let ok: usize = reports.iter().map(|r| r.succeeded).sum();
        let fail: usize = reports.iter().map(|r| r.failed.len()).sum();
        tracing::info!(target: "collector", days = reports.len(), ok, fail, "历史增量补齐完成");
    });

    Json(MessageResponse {
        ok: true,
        message: format!("已在后台开始补齐 {missing} 个缺失交易日（{}）", g.label()),
    })
}

/// 板块资金流入 / 流出对比（首页核心模块 3）。
///
/// 取数优先级 —— 这是"历史/休市场景必须从本地读"这一约束的落点：
///   1. 盘中（`DataMode::Realtime`）且缓存新鲜 → 用实时缓存，**并落盘**当日快照；
///   2. 否则 → 读 `sector_nodes(trade_date, "latest")` 这个**持久化快照**；
///   3. 本地也没有 → 返回空并如实说明原因，绝不拿别的交易日的数据冒充。
pub async fn sector_compare(State(state): State<Arc<AppState>>) -> Json<SectorCompareResponse> {
    let time = crate::market_time::resolve(&state);
    let cfg = state.config();
    let top = cfg.top_n.max(10);
    let trade_date = time.trade_date.clone();
    let live = matches!(time.data_mode, crate::market_time::DataMode::Realtime);

    let mut note;
    let mut status;
    let src_label: String;
    let sectors: Vec<crate::models::SectorTick>;

    let cached = if live {
        state
            .cache
            .sectors
            .get(sector::CACHE_KEY_ALL)
            .or_else(|| state.cache.sectors.get_stale(sector::CACHE_KEY_ALL))
    } else {
        None
    };

    match cached {
        Some(entry) => {
            // 盘中：用实时缓存，同时把当日快照落盘，供收盘后 / 休市日读取
            let payload = entry.value.clone();
            let db = state.db.clone();
            let d = trade_date.clone();
            let src = entry.source.label().to_string();
            src_label = src.clone();
            let _ = tokio::task::spawn_blocking(move || {
                db.save_sector_node(&d, sector::NODE_LATEST, &src, &payload)
            })
            .await;
            sectors = entry.value.clone();
            status = entry.status;
            note = format!(
                "板块资金流为{}（{}）的实时快照，已同步落盘到本地，供收盘后与休市日读取",
                time.data_mode_label, time.data_timestamp
            );
        }
        None => {
            // 非盘中 / 缓存不可用：一律走本地持久化快照
            let db = state.db.clone();
            let d = trade_date.clone();
            let stored = tokio::task::spawn_blocking(move || {
                db.load_sector_node_meta(&d, sector::NODE_LATEST)
            })
            .await
            .ok()
            .and_then(|r| r.ok())
            .flatten();
            match stored {
                Some((src, v)) if !v.is_empty() => {
                    sectors = v;
                    src_label = src;
                    status = DataStatus::Rebuild;
                    note = format!(
                        "板块资金流读取自本地持久化快照（交易日 {trade_date}）；\
                         数据源不提供历史交易日的板块分钟资金流，因此该模块为当日定稿快照，非分时序列"
                    );
                }
                _ => {
                    return Json(SectorCompareResponse {
                        trade_date: trade_date.clone(),
                        inflow_top: Vec::new(),
                        outflow_top: Vec::new(),
                        note: format!(
                            "{trade_date} 的板块资金流在本地没有快照，且当前不在交易时段，\
                             无法从数据源获取；本程序不会用其它交易日的数据冒充"
                        ),
                        status: DataStatus::Unavailable,
                        source: String::new(),
                        board_count: 0,
                        industry_count: 0,
                        concept_count: 0,
                        concept_truncated: false,
                    });
                }
            }
        }
    }

    let mut all: Vec<crate::models::RankEntry> = sectors
        .iter()
        .map(|s| crate::models::RankEntry {
            rank: 0,
            code: s.code.clone(),
            name: s.name.clone(),
            value: s.main_net_inflow,
            value_label: crate::models::fmt_money(s.main_net_inflow),
            change_pct: s.change_pct,
            main_net_inflow: s.main_net_inflow,
            amount: s.amount,
            kind: s.kind.clone(),
            ..Default::default()
        })
        .collect();
    all.sort_by(|a, b| b.value.partial_cmp(&a.value).unwrap_or(std::cmp::Ordering::Equal));

    let mut inflow: Vec<crate::models::RankEntry> = all.iter().take(top).cloned().collect();
    let mut outflow: Vec<crate::models::RankEntry> =
        all.iter().rev().take(top).cloned().collect();
    for (i, e) in inflow.iter_mut().enumerate() {
        e.rank = i + 1;
    }
    for (i, e) in outflow.iter_mut().enumerate() {
        e.rank = i + 1;
    }

    if status == DataStatus::Realtime && !live {
        status = DataStatus::Cache;
    }

    // 如实上报覆盖情况：通道 + 行业/概念各自数量。
    // 降级到腾讯时覆盖数明显少于东财全量（东财概念板块 400+），
    // 不写明就等于把截断藏起来。
    let ind_n = sectors.iter().filter(|s| s.kind != "concept").count();
    let cpt_n = sectors.iter().filter(|s| s.kind == "concept").count();
    let src_text = if src_label.is_empty() {
        "来源未知".to_string()
    } else {
        format!("来源 {src_label}")
    };
    let truncated =
        cpt_n > 0 && cpt_n <= crate::source::eastmoney::CONCEPT_TRUNCATION_HINT;
    note.push_str(&format!(
        "；{src_text}；共 {} 个板块（行业 {ind_n} / 概念 {cpt_n}）",
        all.len()
    ));
    if truncated {
        note.push_str(
            "；⚠️ 概念板块数量偏少，通常是降级通道所致 —— 东方财富全量概念板块有 400+ 个",
        );
    }

    Json(SectorCompareResponse {
        trade_date,
        inflow_top: inflow,
        outflow_top: outflow,
        note,
        status,
        source: src_label,
        board_count: all.len(),
        industry_count: ind_n,
        concept_count: cpt_n,
        concept_truncated: truncated,
    })
}

// ---------------- 复盘 ----------------

pub async fn review_days(State(state): State<Arc<AppState>>) -> Json<ReviewDaysResponse> {
    let db = state.db.clone();
    let days = tokio::task::spawn_blocking(move || db.review_days(120))
        .await
        .ok()
        .and_then(|r| r.ok())
        .unwrap_or_default();
    Json(ReviewDaysResponse { days })
}

pub async fn review_day(
    State(state): State<Arc<AppState>>,
    Path(date): Path<String>,
) -> Result<Json<ReviewDayResponse>, (StatusCode, Json<MessageResponse>)> {
    if chrono::NaiveDate::parse_from_str(&date, "%Y-%m-%d").is_err() {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(MessageResponse {
                ok: false,
                message: "日期格式应为 YYYY-MM-DD".into(),
            }),
        ));
    }
    let snapshots = history::load_day_snapshots(&state, &date).await;

    let db = state.db.clone();
    let date_clone = date.clone();
    let episodes = tokio::task::spawn_blocking(move || db.load_episodes(&date_clone))
        .await
        .ok()
        .and_then(|r| r.ok())
        .unwrap_or_default();

    // 由历史区间重建九榜统计
    let boards = state.engine().boards().to_vec();
    let mut grouped: std::collections::HashMap<String, Vec<crate::db::EpisodeRow>> =
        std::collections::HashMap::new();
    for ep in episodes {
        grouped.entry(ep.code.clone()).or_default().push(ep);
    }
    let mut aggregates: Vec<StockAggregate> = grouped
        .into_iter()
        .map(|(code, eps)| {
            let mut boards_hit = Vec::new();
            let mut labels = Vec::new();
            let mut history = Vec::new();
            let mut best = usize::MAX;
            let mut last = 0usize;
            let mut last_seen = String::new();
            let mut name = String::new();
            for ep in &eps {
                if !boards_hit.contains(&ep.board_id) {
                    boards_hit.push(ep.board_id.clone());
                    labels.push(
                        boards
                            .iter()
                            .find(|b| b.id == ep.board_id)
                            .map(|b| b.name.clone())
                            .unwrap_or_else(|| ep.board_id.clone()),
                    );
                }
                if !history.contains(&ep.first_seen) {
                    history.push(ep.first_seen.clone());
                }
                best = best.min(ep.best_rank);
                if ep.last_seen >= last_seen {
                    last_seen = ep.last_seen.clone();
                    last = ep.last_rank;
                }
                if !ep.name.is_empty() {
                    name = ep.name.clone();
                }
            }
            history.sort();
            let board_count = boards_hit.len() as u32;
            StockAggregate {
                code,
                name,
                appear_count: board_count,
                board_count,
                boards: boards_hit,
                board_labels: labels,
                history,
                best_rank: if best == usize::MAX { 0 } else { best },
                last_rank: last,
                last_seen,
                score: board_count as f64 * 25.0,
                ..Default::default()
            }
        })
        .collect();
    aggregates.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    if snapshots.is_empty() && aggregates.is_empty() {
        return Err((
            StatusCode::NOT_FOUND,
            Json(MessageResponse {
                ok: false,
                message: format!("{date} 暂无历史数据"),
            }),
        ));
    }

    Ok(Json(ReviewDayResponse {
        trade_date: date,
        snapshots,
        boards: state
            .cache
            .boards
            .get_stale("boards")
            .map(|e| e.value.clone())
            .unwrap_or_default(),
        aggregates,
    }))
}

// ---------------- 自选股 ----------------

pub async fn watchlist(State(state): State<Arc<AppState>>) -> Json<Vec<(String, String)>> {
    let db = state.db.clone();
    let list = tokio::task::spawn_blocking(move || db.watchlist())
        .await
        .ok()
        .and_then(|r| r.ok())
        .unwrap_or_default();
    Json(list)
}

pub async fn add_watch(
    State(state): State<Arc<AppState>>,
    Json(req): Json<WatchRequest>,
) -> Json<MessageResponse> {
    let code = req.code.trim().to_string();
    if code.len() != 6 || !code.chars().all(|c| c.is_ascii_digit()) {
        return Json(MessageResponse {
            ok: false,
            message: "股票代码应为 6 位数字".into(),
        });
    }
    let name = req.name.unwrap_or_default();
    let db = state.db.clone();
    let code2 = code.clone();
    let res = tokio::task::spawn_blocking(move || db.add_watch(&code2, &name)).await;
    match res {
        Ok(Ok(())) => Json(MessageResponse {
            ok: true,
            message: format!("{code} 已加入自选"),
        }),
        _ => Json(MessageResponse {
            ok: false,
            message: "写入失败".into(),
        }),
    }
}

pub async fn remove_watch(
    State(state): State<Arc<AppState>>,
    Path(code): Path<String>,
) -> Json<MessageResponse> {
    let db = state.db.clone();
    let res = tokio::task::spawn_blocking(move || db.remove_watch(&code)).await;
    match res {
        Ok(Ok(())) => Json(MessageResponse {
            ok: true,
            message: "已移出自选".into(),
        }),
        _ => Json(MessageResponse {
            ok: false,
            message: "删除失败".into(),
        }),
    }
}

// ---------------- 首页总览 ----------------

pub async fn dashboard(State(state): State<Arc<AppState>>) -> Json<DashboardResponse> {
    let clock = state.clock.read().clone();
    let status = clock.status();

    let indices = match state.cache.indices.get(index::CACHE_KEY_INDICES) {
        Some(e) => envelope_of(e, "指数数据源暂时异常"),
        None => empty_envelope("指数数据正在初始化，请稍候"),
    };

    let breadth_env: Envelope<MarketBreadth> =
        if let Some(e) = state.cache.breadth.get("breadth_full") {
            envelope_of(e, "全市场扫描暂时异常")
        } else if let Some(entry) = state.cache.stocks.get_stale(market::CACHE_KEY_FULL_STOCKS) {
            // 与 /api/market/breadth 同一规则：只有达到全市场规模才展示涨跌家数，
            // 否则如实说明"扫描进行中"，不拿小样本冒充市场统计。
            if entry.value.len() < crate::collector::capital::FULL_MARKET_MIN_STOCKS {
                empty_envelope(&format!(
                    "全市场扫描进行中：当前股票池仅 {} 只，未达到全市场规模，暂不展示涨跌家数",
                    entry.value.len()
                ))
            } else {
                let mut b = crate::engine::breadth(&entry.value);
                b.sample_size = entry.value.len() as i32;
                b.scope = "full".into();
                b.source = entry.source.label().to_string();
                b.sentiment = crate::engine::sentiment_score(&entry.value);
                let mut env = Envelope::build(
                    b,
                    entry.status,
                    entry.source,
                    entry.updated_at,
                    format!("由全市场股票池现场统计（{} 只）", entry.value.len()),
                );
                if entry.is_expired(Instant::now()) {
                    env.status = DataStatus::Cache;
                }
                env
            }
        } else {
            empty_envelope("全市场扫描进行中，涨跌家数将在扫描完成后展示")
        };

    let sectors = match state.cache.sectors.get(sector::CACHE_KEY_ALL) {
        Some(e) => envelope_of(e, "板块数据源暂时异常"),
        None => empty_envelope("板块数据正在初始化，请稍候"),
    };

    let boards = match state.cache.boards.get("boards") {
        Some(e) => envelope_of(e, "榜单数据源暂时异常"),
        None => empty_envelope("榜单正在计算，请稍候"),
    };

    let top_stocks: Envelope<Vec<StockTick>> = match state
        .cache
        .stocks
        .get(market::CACHE_KEY_FULL_STOCKS)
        .or_else(|| state.cache.stocks.get_stale(market::CACHE_KEY_FULL_STOCKS))
    {
        Some(e) => {
            let mut sorted = e.value.clone();
            sorted.sort_by(|a, b| {
                b.main_net_inflow
                    .partial_cmp(&a.main_net_inflow)
                    .unwrap_or(std::cmp::Ordering::Equal)
            });
            sorted.truncate(20);
            envelope_of(
                Arc::new(Entry {
                    value: sorted,
                    ..(*e).clone()
                }),
                "个股数据源暂时异常",
            )
        }
        None => empty_envelope("个股数据正在初始化，请稍候"),
    };

    let capital_curve = match state
        .cache
        .market_flow
        .get(capital::CACHE_PREFIX_INDEX)
        .or_else(|| state.cache.market_flow.get_stale(capital::CACHE_PREFIX_INDEX))
    {
        Some(e) => envelope_of(e, "资金曲线数据源暂时异常"),
        None => empty_envelope("资金曲线正在积累数据，请稍候"),
    };

    let trade_date = state.current_trade_date();
    let snaps = history::load_day_snapshots(&state, &trade_date).await;
    let cfg = state.config();
    let mut node_summary = Vec::new();
    for node in cfg.nodes {
        match snaps.iter().find(|s| s.node == node) {
            Some(s) => node_summary.push(NodeState {
                node: node.clone(),
                status: s.status,
                status_label: s.status.label().to_string(),
                source: s.source,
                source_label: s.source.label().to_string(),
                note: s.note.clone(),
                available: true,
            }),
            None => {
                let (st, note) = state
                    .node_state
                    .get(&node)
                    .map(|v| (v.0, v.2.clone()))
                    .unwrap_or((DataStatus::Waiting, "等待生成".into()));
                node_summary.push(NodeState {
                    node: node.clone(),
                    status: st,
                    status_label: st.label().to_string(),
                    source: DataSource::None,
                    source_label: DataSource::None.label().to_string(),
                    note,
                    available: false,
                });
            }
        }
    }

    // 同步今日快照到内存缓存，供其他端点复用
    if !snaps.is_empty() {
        let map: std::collections::HashMap<String, IntradaySnapshot> = snaps
            .into_iter()
            .map(|s| (s.node.clone(), s))
            .collect();
        state.cache.snapshots.put_value(
            "today",
            map,
            DataSource::DatabaseReplay,
            DataStatus::Realtime,
            std::time::Duration::from_secs(120),
        );
    }

    Json(DashboardResponse {
        indices,
        breadth: breadth_env,
        sectors,
        boards,
        top_stocks,
        capital_curve,
        node_summary,
        market_status: format!("{status:?}"),
        market_status_label: status.label().to_string(),
        trade_date,
        server_time: now().format("%Y-%m-%d %H:%M:%S").to_string(),
        degraded: state.is_degraded(),
        cycle: state.cycle.load(std::sync::atomic::Ordering::Relaxed),
    })
}

/// 统一错误响应（第 6.12 章：用户看不到技术细节）
pub fn internal_error(msg: impl Into<String>) -> impl IntoResponse {
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(MessageResponse {
            ok: false,
            message: msg.into(),
        }),
    )
}

#[allow(dead_code)]
fn _unused(_: &snap_engine::NodeAggregate) {}

#[cfg(test)]
mod snapshot_tests {
    use super::*;
    use crate::history::{MinuteBar, MinuteStore};

    fn bar(minute: &str, open: f64, high: f64, low: f64, close: f64, amount: f64) -> MinuteBar {
        MinuteBar {
            code: "1.000001".into(),
            trade_date: "2026-09-30".into(),
            minute: minute.into(),
            dt_ms: 0,
            open,
            high,
            low,
            close,
            volume: 100.0,
            amount,
            main_net_inflow: close,
        }
    }

    /// 指数分时：极值取 bar 自带的 high/low（真实日内最高/最低），
    /// 而开/收取 close；基准为昨收。
    #[test]
    fn summarize_index_uses_bar_extremes() {
        let bars = vec![
            bar("09:31", 3900.0, 3905.0, 3898.0, 3901.0, 10.0),
            bar("09:32", 3901.0, 3915.0, 3900.0, 3910.0, 20.0),
            bar("09:33", 3910.0, 3912.0, 3880.0, 3885.0, 30.0),
        ];
        let s = summarize_series(&bars, Some(3900.0), |b| b.close, true, "t".into());
        assert_eq!(s.points, 3);
        assert_eq!(s.first_minute, "09:31");
        assert_eq!(s.last_minute, "09:33");
        assert_eq!(s.open, 3901.0, "开盘取首根 close");
        assert_eq!(s.last, 3885.0, "最新取末根 close");
        assert_eq!(s.high, 3915.0, "最高取 bar.high 的极值，而非 close 的极值");
        assert_eq!(s.low, 3880.0, "最低取 bar.low 的极值");
        assert_eq!(s.amount, 60.0);
        assert_eq!(s.volume, 300.0);
        // 3885 - 3900 = -15
        assert_eq!(s.change, Some(-15.0));
        assert_eq!(s.change_pct, Some(-15.0 / 3900.0 * 100.0));
        // (3915 - 3880) / 3900 * 100
        assert_eq!(s.amplitude, Some(35.0 / 3900.0 * 100.0));
    }

    /// 资金曲线：极值取累计值本身的极值（bar 的 high/low 列被复用，不可用）
    #[test]
    fn summarize_flow_uses_value_extremes() {
        let bars = vec![
            bar("09:31", 0.0, 999.0, -999.0, 10.0, 0.0),
            bar("09:32", 0.0, 999.0, -999.0, -50.0, 0.0),
            bar("09:33", 0.0, 999.0, -999.0, 30.0, 0.0),
        ];
        let s = summarize_series(&bars, None, |b| b.main_net_inflow, false, "t".into());
        assert_eq!(s.last, 30.0);
        assert_eq!(s.high, 30.0, "峰值取累计值极值，不受 bar.high 干扰");
        assert_eq!(s.low, -50.0, "谷值取累计值极值，不受 bar.low 干扰");
    }

    /// 无基准时不得伪造涨跌幅
    #[test]
    fn summarize_without_baseline_reports_none() {
        let bars = vec![bar("09:31", 1.0, 1.0, 1.0, 1.0, 0.0), bar("09:32", 2.0, 2.0, 2.0, 2.0, 0.0)];
        let s = summarize_series(&bars, None, |b| b.close, true, "t".into());
        assert!(s.change.is_none());
        assert!(s.change_pct.is_none());
        assert!(s.amplitude.is_none());
    }

    /// 乱序输入也必须得到正确的首/末 —— 否则整条曲线涨跌幅会算反
    #[test]
    fn summarize_sorts_out_of_order_input() {
        let bars = vec![
            bar("09:33", 0.0, 0.0, 0.0, 300.0, 0.0),
            bar("09:31", 0.0, 0.0, 0.0, 100.0, 0.0),
            bar("09:32", 0.0, 0.0, 0.0, 200.0, 0.0),
        ];
        let s = summarize_series(&bars, None, |b| b.close, true, "t".into());
        assert_eq!(s.first_minute, "09:31");
        assert_eq!(s.last_minute, "09:33");
        assert_eq!(s.open, 100.0);
        assert_eq!(s.last, 300.0);
    }

    /// 空序列返回零值并保留说明，不能 panic
    #[test]
    fn summarize_empty_is_safe() {
        let s = summarize_series(&[], Some(1.0), |b| b.close, true, "无数据".into());
        assert_eq!(s.points, 0);
        assert_eq!(s.note, "无数据");
        assert!(s.first_minute.is_empty());
    }

    /// 交易日回退：优先当天，当天没有则回退到最近一个不晚于当天的已落盘日
    #[test]
    fn resolve_series_date_prefers_exact_then_falls_back() {
        let dir = crate::testutil::workspace_temp_dir("snapshot-series-date");
        let store = MinuteStore::new(dir.path().to_path_buf());
        store
            .write_series("index", crate::history::preload::SH_INDEX_SECID, "2026-09-29", &[bar("09:31", 1.0, 1.0, 1.0, 1.0, 0.0)])
            .unwrap();
        store
            .write_series("index", crate::history::preload::SH_INDEX_SECID, "2026-09-30", &[bar("09:31", 1.0, 1.0, 1.0, 1.0, 0.0)])
            .unwrap();

        // 当天有数据 → 用当天
        assert_eq!(resolve_series_date(&store, "2026-09-30"), "2026-09-30");
        // 当天没有 → 回退到 09-30（最近一个不晚于 10-08 的已落盘日）
        assert_eq!(resolve_series_date(&store, "2026-10-08"), "2026-09-30");
        // 绝不使用未来数据：请求 09-28 时库里有 09-29/09-30，必须原样返回 09-28
        assert_eq!(resolve_series_date(&store, "2026-09-28"), "2026-09-28");
        // 空库 → 原样返回请求日
        let dir2 = crate::testutil::workspace_temp_dir("snapshot-series-empty");
        let empty = MinuteStore::new(dir2.path().to_path_buf());
        assert_eq!(resolve_series_date(&empty, "2026-09-30"), "2026-09-30");
    }

    /// 资金曲线序列走 `market_flow` 分区，同样要能被回退逻辑发现
    #[test]
    fn resolve_series_date_sees_market_flow_partition() {
        let dir = crate::testutil::workspace_temp_dir("snapshot-series-flow");
        let store = MinuteStore::new(dir.path().to_path_buf());
        store
            .write_series(
                "market_flow",
                crate::history::preload::SH_INDEX_SECID,
                "2026-09-25",
                &[bar("09:31", 1.0, 1.0, 1.0, 1.0, 0.0)],
            )
            .unwrap();
        assert_eq!(resolve_series_date(&store, "2026-10-08"), "2026-09-25");
    }
}
