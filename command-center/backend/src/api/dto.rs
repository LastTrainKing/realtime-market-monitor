//! API 数据传输对象。

use serde::{Deserialize, Serialize};

use crate::models::{
    BoardResult, DataSource, DataStatus, Envelope, FlowPoint, IndexTick, IntradaySnapshot,
    MarketBreadth, MarketFlowSeries, SectorTick, StockAggregate, StockTick, SystemStatus,
};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HealthResponse {
    pub status: String,
    pub version: String,
    pub service: String,
    pub uptime_secs: u64,
    pub trade_date: String,
    pub market_status: String,
}

/// 首页总览（一次请求返回全部核心卡片，避免前端多次请求）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DashboardResponse {
    pub indices: Envelope<Vec<IndexTick>>,
    pub breadth: Envelope<MarketBreadth>,
    pub sectors: Envelope<Vec<SectorTick>>,
    pub boards: Envelope<Vec<BoardResult>>,
    pub top_stocks: Envelope<Vec<StockTick>>,
    pub capital_curve: Envelope<MarketFlowSeries>,
    pub node_summary: Vec<NodeState>,
    pub market_status: String,
    pub market_status_label: String,
    pub trade_date: String,
    pub server_time: String,
    pub degraded: bool,
    pub cycle: u64,
}

/// 统一数据快照：首页所有模块必须从同一个快照读取数据，
/// 避免各组件各自 getCurrentTime() / getLatestData() 造成数据错位。
/// 分钟序列的当日汇总。
///
/// **在后端计算**，前端只负责展示 —— 遵守"业务逻辑不写在前端"的架构约束。
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SeriesSummary {
    pub points: usize,
    /// 序列首 / 末分钟（HH:MM）
    pub first_minute: String,
    pub last_minute: String,
    /// 开盘（首根 bar 的开盘）
    pub open: f64,
    /// 最新（末根 bar 的收盘 / 最新累计值）
    pub last: f64,
    pub high: f64,
    pub low: f64,
    /// 相对基准（昨收）的涨跌额；无基准时为 None
    pub change: Option<f64>,
    pub change_pct: Option<f64>,
    /// 日内振幅（(high-low)/基准×100）；无基准时为 None
    pub amplitude: Option<f64>,
    /// 当日累计成交额 / 累计成交量
    pub amount: f64,
    pub volume: f64,
    /// 面向用户的说明（口径、缺失原因）
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SnapshotResponse {
    /// 统一时间上下文（系统时间 / 数据交易日 / 行情时间 / 数据模式）
    pub time: crate::market_time::MarketTimeContext,
    /// 该快照统一使用的数据交易日（来自市场时钟）
    pub trade_date: String,
    /// 分钟序列**实际所属**的交易日。
    ///
    /// 当天分钟数据尚未落盘时，会回退到历史库中最近一个已落盘的交易日。
    /// 此时 `series_fallback = true`，前端必须如实标注，不得把旧交易日
    /// 的数据当作当天数据展示。
    pub series_date: String,
    /// 是否发生了交易日回退
    pub series_fallback: bool,
    /// 指数分时（完整交易日 09:31..15:00）
    pub index_series: Vec<crate::history::MinuteBar>,
    /// 两市资金曲线（完整交易日，真实分钟资金流，累计口径）
    pub flow_series: Vec<crate::history::MinuteBar>,
    /// 指数分时是否为完整交易日
    pub index_complete: bool,
    pub flow_complete: bool,
    /// 分时图基准：上证指数昨收。
    ///
    /// 仅当 `series_date == trade_date`（即画的是当前交易日）时才给出，
    /// 避免拿别的交易日的昨收去画另一天的分时图。
    pub index_prev_close: Option<f64>,
    /// 分时图对应的指数代码 / 名称
    pub index_code: String,
    pub index_name: String,
    /// 指数分时当日汇总（后端计算）
    pub index_summary: SeriesSummary,
    /// 两市资金曲线当日汇总（后端计算，累计口径）
    pub flow_summary: SeriesSummary,
    /// 历史库覆盖情况
    pub coverage: crate::history::preload::HistoryCoverage,
    /// 数据源能力说明（如实告知哪些历史数据不可回溯）
    pub capability_note: String,
    /// 数据缺失原因（有缺失时非空）
    pub missing_note: String,
}

/// 时点数据面板：与资金曲线同一份数据集、同一交易日
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TimelinePointResponse {
    pub trade_date: String,
    pub minute: String,
    /// 该时点的指数点位
    pub index_close: Option<f64>,
    /// 该时点的两市主力净流入
    pub main_net: Option<f64>,
    pub super_large: Option<f64>,
    pub large: Option<f64>,
    pub medium: Option<f64>,
    pub small: Option<f64>,
    pub available: bool,
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NodeState {
    pub node: String,
    pub status: DataStatus,
    pub status_label: String,
    pub source: DataSource,
    pub source_label: String,
    pub note: String,
    pub available: bool,
}

/// 开盘检测响应（第 4.13 章）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OpeningResponse {
    pub trade_date: String,
    pub nodes: Vec<OpeningNode>,
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OpeningNode {
    pub node: String,
    pub status: DataStatus,
    pub status_label: String,
    pub source: DataSource,
    pub source_label: String,
    pub created_at: String,
    pub origin: String,
    pub note: String,
    /// 成交量 Top5
    pub volume_top: Vec<crate::models::RankEntry>,
    /// 主力净流入 Top5
    pub netflow_top: Vec<crate::models::RankEntry>,
}

/// 个股异动响应（第 4.14 章）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AnomalyResponse {
    pub trade_date: String,
    pub nodes: Vec<AnomalyNode>,
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AnomalyNode {
    pub node: String,
    pub status: DataStatus,
    pub status_label: String,
    pub origin: String,
    /// 量比 Top10
    pub ratio_top: Vec<crate::models::RankEntry>,
    /// 成交量 Top10
    pub volume_top: Vec<crate::models::RankEntry>,
    /// 主力净流入 Top10
    pub netflow_top: Vec<crate::models::RankEntry>,
}

/// 历史数据状态诊断
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HistoryStatusResponse {
    /// DuckDB 版本（证明查询层确实在用 DuckDB）
    pub duckdb_version: String,
    /// 历史库根目录
    pub root: String,
    /// 磁盘占用
    pub size_bytes: u64,
    /// 已落盘的交易日（全市场物化视图）
    pub dates: Vec<String>,
    /// 覆盖情况
    pub coverage: crate::history::preload::HistoryCoverage,
    /// 状态清单
    pub manifest: Vec<crate::history::ManifestRow>,
    /// 各粒度的数据源可回溯能力（实测值）
    pub granularity_capability: Vec<GranularityCapability>,
    /// 当前配置的历史粒度
    pub granularity: u32,
    pub granularity_label: String,
    /// 数据源能力说明
    pub capability_note: String,
    /// 缺失说明
    pub missing_note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GranularityCapability {
    pub minutes: u32,
    pub label: String,
    /// 数据源单次请求可覆盖的交易日数（实测）
    pub source_max_days: usize,
    /// 是否满足配置的历史天数要求
    pub meets_requirement: bool,
}

/// 板块资金流入/流出对比（首页核心模块 4）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SectorCompareResponse {
    pub trade_date: String,
    pub inflow_top: Vec<crate::models::RankEntry>,
    pub outflow_top: Vec<crate::models::RankEntry>,
    /// 板块数据的时间口径说明
    pub note: String,
    pub status: DataStatus,
    /// 数据来源通道（东方财富 / 腾讯财经 …）。
    ///
    /// 单独成字段而不是塞在 note 里，是为了让界面能明确标出
    /// "当前用的是哪条通道"，避免降级后覆盖数变小却不被发现。
    pub source: String,
    /// 板块总数
    pub board_count: usize,
    pub industry_count: usize,
    pub concept_count: usize,
    /// 概念板块数量明显偏少（通常是降级通道所致，东财全量 400+）
    pub concept_truncated: bool,
}

/// 个股精选响应（第 4.15 章）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SelectionResponse {
    pub trade_date: String,
    pub as_of: String,
    pub stocks: Vec<StockAggregate>,
    pub total_events: u64,
    pub note: String,
}

/// 个股详情
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StockDetailResponse {
    pub tick: Envelope<StockTick>,
    pub flow: Envelope<Vec<FlowPoint>>,
    pub aggregates: Vec<StockAggregate>,
    pub minute: Envelope<Vec<crate::models::MinutePoint>>,
}

/// 复盘日历
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReviewDaysResponse {
    pub days: Vec<crate::models::ReviewDay>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReviewDayResponse {
    pub trade_date: String,
    pub snapshots: Vec<IntradaySnapshot>,
    pub boards: Vec<BoardResult>,
    pub aggregates: Vec<StockAggregate>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SystemResponse {
    pub status: SystemStatus,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MessageResponse {
    pub ok: bool,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WatchRequest {
    pub code: String,
    pub name: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct ListQuery {
    pub limit: Option<usize>,
    pub kind: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClientLogRequest {
    pub detail: String,
}

#[derive(Debug, Deserialize)]
pub struct RebuildRequest {
    pub trade_date: Option<String>,
}
