//! 领域模型。所有对外数据结构均携带 `status` / `source` / `updated_at`
//! 三个真实性字段（第 4.12 / 8.15 章）。

use chrono::{DateTime, FixedOffset, NaiveDate};
use serde::{Deserialize, Serialize};

use crate::clock::cn_offset;

/// 数据来源（禁止伪造，第 4.12 章）
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DataSource {
    /// 东方财富实时接口
    EastmoneyRealtime,
    /// 东方财富历史分钟数据重建
    EastmoneyHistory,
    /// 新浪财经（降级源）
    SinaRealtime,
    /// 腾讯财经（降级源）
    TencentRealtime,
    /// 内存缓存
    MemoryCache,
    /// SQLite 回放
    DatabaseReplay,
    /// 无可用数据
    None,
}

impl DataSource {
    pub fn label(&self) -> &'static str {
        match self {
            DataSource::EastmoneyRealtime => "东方财富实时",
            DataSource::EastmoneyHistory => "东方财富历史",
            DataSource::SinaRealtime => "新浪财经",
            DataSource::TencentRealtime => "腾讯财经",
            DataSource::MemoryCache => "本地缓存",
            DataSource::DatabaseReplay => "本地数据库",
            DataSource::None => "无数据源",
        }
    }
}

/// 数据状态（第 4.11 / 8.15 章，禁止统一返回 null）
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum DataStatus {
    /// 实时数据
    Realtime,
    /// 缓存数据
    Cache,
    /// 由历史分钟数据重建
    Rebuild,
    /// 延迟数据
    Delay,
    /// 数据源异常
    Error,
    /// 完全不可用
    Unavailable,
    /// 节点尚未到达
    Waiting,
    /// 部分数据缺失
    Partial,
    /// 数据源本身不提供该口径
    DataSourceLimitation,
}

impl DataStatus {
    pub fn label(&self) -> &'static str {
        match self {
            DataStatus::Realtime => "实时",
            DataStatus::Cache => "缓存",
            DataStatus::Rebuild => "历史重建",
            DataStatus::Delay => "延迟",
            DataStatus::Error => "数据源异常",
            DataStatus::Unavailable => "暂无数据",
            DataStatus::Waiting => "等待生成",
            DataStatus::Partial => "部分缺失",
            DataStatus::DataSourceLimitation => "数据源限制",
        }
    }

    pub fn color(&self) -> &'static str {
        match self {
            DataStatus::Realtime => "green",
            DataStatus::Cache | DataStatus::Rebuild | DataStatus::Delay => "amber",
            _ => "red",
        }
    }

    pub fn is_usable(&self) -> bool {
        matches!(
            self,
            DataStatus::Realtime | DataStatus::Cache | DataStatus::Rebuild | DataStatus::Delay
        )
    }
}

/// 统一响应信封
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Envelope<T> {
    pub data: T,
    pub status: DataStatus,
    pub source: DataSource,
    pub source_label: String,
    pub status_label: String,
    pub updated_at: String,
    /// 数据年龄（秒）
    pub age_secs: i64,
    pub message: String,
}

impl<T> Envelope<T> {
    pub fn realtime(data: T, source: DataSource, updated_at: DateTime<FixedOffset>) -> Self {
        Self::build(data, DataStatus::Realtime, source, updated_at, String::new())
    }

    pub fn build(
        data: T,
        status: DataStatus,
        source: DataSource,
        updated_at: DateTime<FixedOffset>,
        message: String,
    ) -> Self {
        let age = (crate::clock::now_cn() - updated_at).num_seconds().max(0);
        Self {
            data,
            status,
            source,
            source_label: source.label().to_string(),
            status_label: status.label().to_string(),
            updated_at: updated_at.format("%H:%M:%S").to_string(),
            age_secs: age,
            message,
        }
    }

    pub fn unavailable(data: T, message: impl Into<String>) -> Self {
        let now = crate::clock::now_cn();
        let mut env = Self::build(
            data,
            DataStatus::Unavailable,
            DataSource::None,
            now,
            message.into(),
        );
        env.updated_at = "--:--:--".to_string();
        env.age_secs = -1;
        env
    }

    pub fn map<U>(self, f: impl FnOnce(T) -> U) -> Envelope<U> {
        Envelope {
            data: f(self.data),
            status: self.status,
            source: self.source,
            source_label: self.source_label,
            status_label: self.status_label,
            updated_at: self.updated_at,
            age_secs: self.age_secs,
            message: self.message,
        }
    }
}

/// 个股实时快照
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct StockTick {
    pub code: String,
    pub name: String,
    /// 0 = 深市 / 1 = 沪市 / 2 = 北交所
    pub market: u8,
    pub price: f64,
    pub change_pct: f64,
    pub change_amount: f64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub prev_close: f64,
    /// 成交量（手）
    pub volume: f64,
    /// 成交额（元）
    pub amount: f64,
    pub amplitude: f64,
    pub turnover_rate: f64,
    pub volume_ratio: f64,
    /// 主力净流入（元）
    pub main_net_inflow: f64,
    /// 主力净占比（%）
    pub main_net_ratio: f64,
    pub super_large_net: f64,
    pub large_net: f64,
    pub medium_net: f64,
    pub small_net: f64,
    /// 换手率是否有效
    pub is_st: bool,
}

impl StockTick {
    /// 复合强度：用于综合榜单
    pub fn composite(&self) -> f64 {
        let flow_score = if self.main_net_inflow.abs() > 0.0 {
            (self.main_net_inflow / 1.0e7).clamp(-50.0, 50.0)
        } else {
            0.0
        };
        self.change_pct * 1.5 + flow_score + (self.volume_ratio.clamp(0.0, 10.0)) * 0.8
    }

    pub fn secid(&self) -> String {
        format!("{}.{}", self.market, self.code)
    }
}

/// 板块快照
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SectorTick {
    pub code: String,
    pub name: String,
    /// 行业 / 概念 / 地域
    pub kind: String,
    pub change_pct: f64,
    pub main_net_inflow: f64,
    pub main_net_ratio: f64,
    pub amount: f64,
    pub volume: f64,
    pub turnover_rate: f64,
    pub up_count: i32,
    pub down_count: i32,
    pub leader_code: String,
    pub leader_name: String,
    pub leader_change_pct: f64,
}

/// 指数快照
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct IndexTick {
    pub code: String,
    pub name: String,
    pub price: f64,
    pub change_pct: f64,
    pub change_amount: f64,
    pub volume: f64,
    pub amount: f64,
    pub high: f64,
    pub low: f64,
    pub open: f64,
    pub prev_close: f64,
    /// 指数主力净流入（部分源不提供）
    pub main_net_inflow: Option<f64>,
}

/// 市场概况
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct MarketBreadth {
    pub up_count: i32,
    pub down_count: i32,
    pub flat_count: i32,
    pub limit_up_count: i32,
    pub limit_down_count: i32,
    /// 两市成交额（元）
    pub total_amount: f64,
    /// 两市主力净流入（元）
    pub total_main_net: f64,
    /// 上涨家数占比
    pub up_ratio: f64,
    /// 本次统计覆盖的股票数量（真实样本数，禁止伪造全市场口径）
    pub sample_size: i32,
    /// 统计范围：
    ///   * `full`     —— 全市场（本地证券列表 + 批量行情）
    ///   * `partial`  —— 全市场扫描未取全（缺失股票数在 `missing_symbols`）
    ///   * `source`   —— 直接用数据源给出的现成汇总（如东财大盘资金流）
    ///
    /// 历史上还有过 `sample`（由排行榜接口拼出的有偏样本），**已废弃**：
    /// 排行榜按成交额 / 换手率排序，用它算涨跌家数会系统性失真。
    pub scope: String,
    /// 统计来源
    pub source: String,
    /// 市场情绪分（-100 ~ 100）
    pub sentiment: f64,
    /// 股票宇宙总规模（证券列表条数）
    pub total_symbols: i32,
    /// 未能取到行情的股票数
    pub missing_symbols: i32,
    /// 是否达到"全市场完整"标准
    pub complete: bool,
}

/// 榜单条目
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RankEntry {
    pub rank: usize,
    pub code: String,
    pub name: String,
    pub value: f64,
    pub value_label: String,
    pub change_pct: f64,
    pub price: f64,
    pub volume_ratio: f64,
    pub turnover_rate: f64,
    pub main_net_inflow: f64,
    pub amount: f64,
    pub volume: f64,
    /// 相对上一轮排名变化（正数表示上升）
    pub rank_delta: i32,
    /// 首次出现时间 HH:MM
    pub first_seen: String,
    /// 累计上榜次数（事件驱动，第 8.13 章）
    pub appear_count: u32,
    /// 该股命中的榜单 id 列表
    pub boards: Vec<String>,
    /// 历史命中时间点
    pub history: Vec<String>,
    /// 板块类型（行业 / 概念 / 地域）；仅板块类条目有值
    #[serde(default)]
    pub kind: String,
}

/// 一个榜单的完整结果
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct BoardResult {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub scope: String,
    pub size: usize,
    pub entries: Vec<RankEntry>,
    /// 数据时间点 HH:MM
    pub as_of: String,
    /// 该榜单当前是否有有效数据（无有效指标时明确标注，而不是展示全 0）
    pub available: bool,
    /// 不可用原因（面向用户的中文说明）
    pub note: String,
}

/// 早盘节点快照（第 4.10 / 8.10 章）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IntradaySnapshot {
    pub trade_date: String,
    pub node: String,
    /// realtime / replay
    pub origin: String,
    pub status: DataStatus,
    pub source: DataSource,
    pub created_at: String,
    /// 开盘检测：成交量 Top5
    pub volume_top: Vec<RankEntry>,
    /// 开盘检测：主力净流入 Top5
    pub netflow_top: Vec<RankEntry>,
    /// 异动：量比 Top10
    pub ratio_top: Vec<RankEntry>,
    /// 异动：成交量 Top10
    pub anomaly_volume_top: Vec<RankEntry>,
    /// 异动：主力净流入 Top10
    pub anomaly_netflow_top: Vec<RankEntry>,
    /// 板块资金流 Top
    pub sector_netflow_top: Vec<RankEntry>,
    /// 市场概况
    pub breadth: MarketBreadth,
    /// 说明信息
    pub note: String,
}

/// 九榜统计聚合（个股精选，第 4.15 / 8.13 章）
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct StockAggregate {
    pub code: String,
    pub name: String,
    /// 累计上榜事件次数（事件驱动，非刷新计数）
    pub appear_count: u32,
    /// 命中过的榜单数
    pub board_count: u32,
    pub boards: Vec<String>,
    pub board_labels: Vec<String>,
    pub history: Vec<String>,
    pub best_rank: usize,
    pub last_rank: usize,
    pub last_seen: String,
    pub main_net_inflow: f64,
    pub change_pct: f64,
    pub price: f64,
    /// 综合评分
    pub score: f64,
    /// 是否仍在榜
    pub active: bool,
}

/// 榜单事件（持久化，第 8.14 章）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RankingEvent {
    pub trade_date: NaiveDate,
    pub stock_code: String,
    pub stock_name: String,
    pub rank_type: String,
    pub rank_time: String,
    pub rank_position: usize,
    pub value: f64,
    pub source: String,
}

/// 分钟数据点（用于历史重建，第 4.11 章）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MinutePoint {
    pub code: String,
    pub trade_date: NaiveDate,
    pub minute: String,
    pub price: f64,
    pub volume: f64,
    pub amount: f64,
    pub main_net_inflow: f64,
}

/// 当日资金曲线点
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct FlowPoint {
    pub time: String,
    pub main_net: f64,
    pub super_large: f64,
    pub large: f64,
    pub medium: f64,
    pub small: f64,
}

/// 市场资金曲线（必须携带口径信息）
///
/// 口径优先级：数据源现成的市场级汇总（`scope = source`，如东财大盘资金流）
/// 优先；不可用时退回全市场股票层面 `main_net_inflow` 求和（`scope = full`）。
///
/// **不存在"活跃股样本"口径**：股票池只有一个（全市场），
/// 池子未就绪时接口不返回曲线，而不是返回小样本算出的曲线。
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct MarketFlowSeries {
    pub points: Vec<FlowPoint>,
    /// `full` = 全市场；`source` = 数据源现成汇总
    pub scope: String,
    /// 参与统计的股票数量
    pub sample_size: i32,
    /// 面向用户的口径说明
    pub note: String,
}

/// 板块资金曲线
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SectorFlowSeries {
    pub code: String,
    pub name: String,
    pub points: Vec<FlowPoint>,
}

/// 系统运行状态
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SystemStatus {
    pub version: String,
    pub status: String,
    pub market_status: String,
    pub market_status_label: String,
    pub trade_date: String,
    pub server_time: String,
    pub uptime_secs: u64,
    pub collectors: Vec<CollectorStatus>,
    pub net: NetStats,
    pub db: DbStats,
    pub cache: CacheStats,
    pub notes: Vec<String>,
    /// 处于冷却中的数据源：(名称, 剩余秒数, 原因)
    pub gated_sources: Vec<GatedSource>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GatedSource {
    pub name: String,
    pub label: String,
    pub remaining_secs: u64,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CollectorStatus {
    pub name: String,
    pub label: String,
    pub last_run: String,
    pub last_success: String,
    pub last_error: String,
    pub ok: bool,
    pub latency_ms: u64,
    pub runs: u64,
    pub failures: u64,
    pub data_points: usize,
    pub status: DataStatus,
    pub source: DataSource,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct NetStats {
    pub total_requests: u64,
    pub success: u64,
    pub failed: u64,
    pub deduped: u64,
    pub rate_limited: u64,
    pub circuit_open: bool,
    pub circuit_opens: u64,
    pub avg_latency_ms: u64,
    pub hosts: Vec<HostHealth>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HostHealth {
    pub host: String,
    pub state: String,
    pub success: u64,
    pub failure: u64,
    pub avg_latency_ms: u64,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct DbStats {
    pub path: String,
    pub size_bytes: u64,
    pub snapshots: u64,
    pub ranking_events: u64,
    pub minute_rows: u64,
    pub trading_days: u64,
    pub last_write: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CacheStats {
    pub entries: usize,
    pub hits: u64,
    pub misses: u64,
    pub hit_rate: f64,
    pub memory_kb: u64,
}

/// 复盘日历项
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReviewDay {
    pub trade_date: String,
    pub snapshots: usize,
    pub events: usize,
    pub minute_rows: usize,
    pub first_node: String,
    pub last_node: String,
    pub complete: bool,
}

pub fn now_str() -> String {
    crate::clock::now_cn().format("%Y-%m-%d %H:%M:%S").to_string()
}

pub fn fmt_money(v: f64) -> String {
    let abs = v.abs();
    if abs >= 1.0e8 {
        format!("{:.2}亿", v / 1.0e8)
    } else if abs >= 1.0e4 {
        format!("{:.2}万", v / 1.0e4)
    } else {
        format!("{v:.0}")
    }
}

pub fn fmt_volume(v: f64) -> String {
    let abs = v.abs();
    if abs >= 1.0e8 {
        format!("{:.2}亿手", v / 1.0e8)
    } else if abs >= 1.0e4 {
        format!("{:.2}万手", v / 1.0e4)
    } else {
        format!("{v:.0}手")
    }
}

pub fn local_dt(s: &str) -> DateTime<FixedOffset> {
    if let Ok(dt) = DateTime::parse_from_rfc3339(s) {
        return dt.with_timezone(&cn_offset());
    }
    crate::clock::now_cn()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn envelope_reports_age_and_labels() {
        let now = crate::clock::now_cn();
        let env = Envelope::realtime(vec![1, 2, 3], DataSource::EastmoneyRealtime, now);
        assert_eq!(env.status, DataStatus::Realtime);
        assert_eq!(env.source_label, "东方财富实时");
        assert_eq!(env.status_label, "实时");
        assert!(env.age_secs <= 1);
        assert_eq!(env.data.len(), 3);
    }

    #[test]
    fn unavailable_has_no_time() {
        let env: Envelope<Vec<u8>> = Envelope::unavailable(Vec::new(), "数据源暂时异常");
        assert_eq!(env.updated_at, "--:--:--");
        assert_eq!(env.age_secs, -1);
        assert!(!env.status.is_usable());
    }

    #[test]
    fn money_formatting() {
        assert_eq!(fmt_money(1.285e9), "12.85亿");
        assert_eq!(fmt_money(-3.4e4), "-3.40万");
        assert_eq!(fmt_money(520.0), "520");
    }

    #[test]
    fn composite_score_orders_reasonably() {
        let mut a = StockTick::default();
        a.change_pct = 10.0;
        a.main_net_inflow = 3.0e8;
        a.volume_ratio = 5.0;
        let mut b = StockTick::default();
        b.change_pct = -5.0;
        b.main_net_inflow = -2.0e8;
        b.volume_ratio = 1.0;
        assert!(a.composite() > b.composite());
    }
}
