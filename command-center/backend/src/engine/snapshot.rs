//! 开盘检测 / 个股异动 / 节点快照（第 4.10 / 4.11 / 4.13 / 4.14 / 8.10 章）。
//!
//! 关键约束：
//!   * 节点数据不得只依赖“程序运行时捕获”，必须支持从分钟数据历史重建
//!   * 实时与回放使用同一套计算函数
//!   * 必须区分 AVAILABLE / WAITING / PARTIAL / DATA_SOURCE_LIMITATION / UNAVAILABLE

use crate::models::{
    DataSource, DataStatus, IntradaySnapshot, MarketBreadth, RankEntry, SectorTick, StockTick,
};

use super::indicator;
use super::ranking::{TopMetric, top_by};

/// 历史重建所需的单只股票节点聚合值
#[derive(Debug, Clone, Default)]
pub struct NodeAggregate {
    pub code: String,
    pub name: String,
    pub price: f64,
    pub prev_close: f64,
    /// 开盘以来累计成交量（手）
    pub volume: f64,
    /// 开盘以来累计成交额（元）
    pub amount: f64,
    /// 截至该节点的主力净流入（元）
    pub main_net_inflow: f64,
    pub volume_ratio: f64,
}

impl NodeAggregate {
    pub fn change_pct(&self) -> f64 {
        if self.prev_close > 0.0 {
            (self.price - self.prev_close) / self.prev_close * 100.0
        } else {
            0.0
        }
    }

    pub fn to_tick(&self) -> StockTick {
        StockTick {
            code: self.code.clone(),
            name: self.name.clone(),
            market: crate::source::market_of(&self.code),
            price: self.price,
            change_pct: self.change_pct(),
            prev_close: self.prev_close,
            volume: self.volume,
            amount: self.amount,
            main_net_inflow: self.main_net_inflow,
            volume_ratio: self.volume_ratio,
            ..Default::default()
        }
    }
}

#[derive(Debug, Clone)]
pub struct SnapshotInput<'a> {
    pub trade_date: &'a str,
    pub node: &'a str,
    pub origin: &'a str,
    pub top_open: usize,
    pub top_n: usize,
    pub status: DataStatus,
    pub source: DataSource,
    pub note: String,
}

impl<'a> SnapshotInput<'a> {
    pub fn live(trade_date: &'a str, node: &'a str, top_open: usize, top_n: usize) -> Self {
        Self {
            trade_date,
            node,
            origin: "realtime",
            top_open,
            top_n,
            status: DataStatus::Realtime,
            source: DataSource::EastmoneyRealtime,
            note: String::new(),
        }
    }

    pub fn replay(trade_date: &'a str, node: &'a str, top_open: usize, top_n: usize) -> Self {
        Self {
            trade_date,
            node,
            origin: "replay",
            top_open,
            top_n,
            status: DataStatus::Rebuild,
            source: DataSource::DatabaseReplay,
            note: String::new(),
        }
    }
}

/// 由个股快照集合构建节点快照（实时与重建共用）
pub fn build_snapshot(
    input: &SnapshotInput<'_>,
    stocks: &[StockTick],
    sectors: &[SectorTick],
    breadth: MarketBreadth,
) -> IntradaySnapshot {
    let volume_top = top_by(stocks, TopMetric::Volume, input.top_open);
    let netflow_top = top_by(stocks, TopMetric::NetFlow, input.top_open);

    let ratio_top = top_by(stocks, TopMetric::VolumeRatio, input.top_n);
    let anomaly_volume_top = top_by(stocks, TopMetric::Volume, input.top_n);
    let anomaly_netflow_top = top_by(stocks, TopMetric::NetFlow, input.top_n);

    let mut sector_entries: Vec<RankEntry> = sectors
        .iter()
        .filter(|s| !s.name.is_empty())
        .map(|s| RankEntry {
            rank: 0,
            code: s.code.clone(),
            name: s.name.clone(),
            value: s.main_net_inflow,
            value_label: crate::models::fmt_money(s.main_net_inflow),
            change_pct: s.change_pct,
            main_net_inflow: s.main_net_inflow,
            amount: s.amount,
            volume: s.volume,
            turnover_rate: s.turnover_rate,
            ..Default::default()
        })
        .collect();
    sector_entries.sort_by(|a, b| {
        b.value
            .partial_cmp(&a.value)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    sector_entries.truncate(input.top_n);
    for (i, e) in sector_entries.iter_mut().enumerate() {
        e.rank = i + 1;
    }

    let mut snap = IntradaySnapshot {
        trade_date: input.trade_date.to_string(),
        node: input.node.to_string(),
        origin: input.origin.to_string(),
        status: input.status,
        source: input.source,
        created_at: crate::models::now_str(),
        volume_top,
        netflow_top,
        ratio_top,
        anomaly_volume_top,
        anomaly_netflow_top,
        sector_netflow_top: sector_entries,
        breadth,
        note: input.note.clone(),
    };

    // 某一指标整体缺失（全为 0）时必须隐藏该榜单并说明原因，
    // 绝不能展示一排 0 让用户误读（第 4.12 / 8.15 章）
    let missing = prune_missing_metrics(&mut snap);
    if !missing.is_empty() {
        let extra = format!("；{} 指标当前不可获取，已隐藏对应榜单", missing.join("、"));
        snap.note.push_str(&extra);
        if snap.status == DataStatus::Rebuild {
            snap.status = DataStatus::Partial;
        }
    }
    snap
}

/// 清理指标整体缺失的榜单，返回被隐藏的指标名称
fn prune_missing_metrics(snap: &mut IntradaySnapshot) -> Vec<String> {
    let all_zero = |entries: &[RankEntry]| -> bool {
        !entries.is_empty() && entries.iter().all(|e| e.value.abs() < 1e-9)
    };
    let mut missing = Vec::new();

    if all_zero(&snap.ratio_top) {
        snap.ratio_top.clear();
        missing.push("量比".to_string());
    }
    if all_zero(&snap.netflow_top) {
        snap.netflow_top.clear();
        missing.push("主力净流入".to_string());
    }
    if all_zero(&snap.anomaly_netflow_top) {
        snap.anomaly_netflow_top.clear();
    }
    if all_zero(&snap.sector_netflow_top) {
        snap.sector_netflow_top.clear();
    }
    missing
}

/// 由历史分钟聚合值构建节点快照（第 4.11 章：历史重建）
pub fn build_snapshot_from_aggregates(
    input: &SnapshotInput<'_>,
    aggregates: &[NodeAggregate],
    sectors: &[SectorTick],
) -> IntradaySnapshot {
    let ticks: Vec<StockTick> = aggregates
        .iter()
        .filter(|a| a.price > 0.0 && !a.name.is_empty())
        .map(|a| a.to_tick())
        .collect();
    let breadth = indicator::breadth(&ticks);
    build_snapshot(input, &ticks, sectors, breadth)
}

/// 节点状态判定（第 4.11 章）
pub fn node_status(
    now_minutes: i64,
    node_minutes: i64,
    has_live: bool,
    has_rebuild: bool,
    coverage_ok: bool,
) -> (DataStatus, String) {
    if has_live {
        return (DataStatus::Realtime, String::new());
    }
    if now_minutes < node_minutes {
        return (
            DataStatus::Waiting,
            format!("{} 节点尚未到达，将在到点后自动生成", fmt_minutes(node_minutes)),
        );
    }
    if has_rebuild {
        if coverage_ok {
            (
                DataStatus::Rebuild,
                "已由历史分钟数据重建该节点".to_string(),
            )
        } else {
            (
                DataStatus::Partial,
                "已由历史分钟数据部分重建，候选池覆盖有限".to_string(),
            )
        }
    } else {
        (
            DataStatus::DataSourceLimitation,
            "该节点的历史分钟数据不可获取（数据源限制），无法重建".to_string(),
        )
    }
}

fn fmt_minutes(m: i64) -> String {
    format!("{:02}:{:02}", m / 60, m % 60)
}

/// 计算“开盘以来累计成交量”：对分钟成交量求和
pub fn cumulative_from_minutes(volumes: &[f64]) -> f64 {
    volumes.iter().sum()
}

/// 量比：由分钟数据与历史日均量估算（重建路径）
pub fn ratio_from_minutes(
    cumulative_volume: f64,
    minutes_elapsed: i64,
    avg_daily_volume: f64,
) -> f64 {
    indicator::volume_ratio(
        0.0,
        cumulative_volume,
        minutes_elapsed,
        avg_daily_volume,
        240.0,
    )
}

/// 由“截至节点”的分钟序号计算已交易分钟数
pub fn minutes_elapsed_until(node: &str) -> i64 {
    let Some((h, m)) = crate::clock::parse_hhmm(node) else {
        return 0;
    };
    let t = h * 60 + m;
    let open = 9 * 60 + 30;
    let morning_end = 11 * 60 + 30;
    let afternoon_start = 13 * 60;
    let close = 15 * 60;
    if t <= open {
        0
    } else if t <= morning_end {
        (t - open) as i64
    } else if t < afternoon_start {
        120
    } else if t <= close {
        (120 + (t - afternoon_start)) as i64
    } else {
        240
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stock(code: &str, name: &str, vol: f64, flow: f64, ratio: f64) -> StockTick {
        StockTick {
            code: code.into(),
            name: name.into(),
            price: 10.0,
            change_pct: 2.0,
            volume: vol,
            amount: vol * 100.0,
            main_net_inflow: flow,
            volume_ratio: ratio,
            ..Default::default()
        }
    }

    #[test]
    fn snapshot_contains_both_open_boards_and_three_anomaly_boards() {
        let stocks = vec![
            stock("000001", "A", 1000.0, 1.0e8, 2.0),
            stock("000002", "B", 5000.0, 5.0e7, 9.0),
            stock("000003", "C", 500.0, 9.0e8, 1.0),
        ];
        let input = SnapshotInput::live("2026-09-30", "09:35", 5, 10);
        let snap = build_snapshot(&input, &stocks, &[], Default::default());
        assert_eq!(snap.volume_top[0].code, "000002");
        assert_eq!(snap.netflow_top[0].code, "000003");
        assert_eq!(snap.ratio_top[0].code, "000002");
        assert_eq!(snap.anomaly_volume_top[0].code, "000002");
        assert_eq!(snap.anomaly_netflow_top[0].code, "000003");
        assert_eq!(snap.node, "09:35");
        assert_eq!(snap.origin, "realtime");
    }

    #[test]
    fn open_boards_respect_top5_limit() {
        let stocks: Vec<StockTick> = (0..20)
            .map(|i| stock(&format!("0000{i:02}"), "X", i as f64 * 100.0, i as f64, i as f64))
            .collect();
        let input = SnapshotInput::live("2026-09-30", "09:35", 5, 10);
        let snap = build_snapshot(&input, &stocks, &[], Default::default());
        assert_eq!(snap.volume_top.len(), 5);
        assert_eq!(snap.netflow_top.len(), 5);
        assert_eq!(snap.ratio_top.len(), 10);
        assert_eq!(snap.anomaly_volume_top.len(), 10);
        assert_eq!(snap.anomaly_netflow_top.len(), 10);
    }

    #[test]
    fn rebuild_marks_origin_and_status() {
        let aggs = vec![
            NodeAggregate {
                code: "000001".into(),
                name: "平安银行".into(),
                price: 11.39,
                prev_close: 11.35,
                volume: 2089600.0,
                amount: 2.377e9,
                main_net_inflow: 5.0e7,
                volume_ratio: 1.8,
            },
            NodeAggregate {
                code: "600519".into(),
                name: "贵州茅台".into(),
                price: 1258.62,
                prev_close: 1235.58,
                volume: 3833100.0,
                amount: 4.797e9,
                main_net_inflow: 5.4e8,
                volume_ratio: 1.36,
            },
        ];
        let input = SnapshotInput::replay("2026-09-30", "09:35", 5, 10);
        let snap = build_snapshot_from_aggregates(&input, &aggs, &[]);
        assert_eq!(snap.origin, "replay");
        assert_eq!(snap.status, DataStatus::Rebuild);
        assert_eq!(snap.volume_top[0].code, "600519");
        assert_eq!(snap.netflow_top[0].code, "600519");
        assert!(snap.volume_top[1].change_pct > 0.0);
    }

    #[test]
    fn node_status_transitions() {
        // 节点未到 -> WAITING
        let (s, m) = node_status(9 * 60 + 31, 9 * 60 + 35, false, false, false);
        assert_eq!(s, DataStatus::Waiting);
        assert!(m.contains("09:35"));

        // 有实时数据 -> REALTIME
        let (s, _) = node_status(9 * 60 + 40, 9 * 60 + 35, true, false, false);
        assert_eq!(s, DataStatus::Realtime);

        // 重建成功且覆盖完整 -> REBUILD
        let (s, _) = node_status(10 * 60, 9 * 60 + 35, false, true, true);
        assert_eq!(s, DataStatus::Rebuild);

        // 重建但覆盖不足 -> PARTIAL
        let (s, m) = node_status(10 * 60, 9 * 60 + 35, false, true, false);
        assert_eq!(s, DataStatus::Partial);
        assert!(m.contains("部分"));

        // 数据源不提供 -> DATA_SOURCE_LIMITATION
        let (s, _) = node_status(10 * 60, 9 * 60 + 35, false, false, false);
        assert_eq!(s, DataStatus::DataSourceLimitation);
    }

    #[test]
    fn minutes_elapsed_computation() {
        assert_eq!(minutes_elapsed_until("09:30"), 0);
        assert_eq!(minutes_elapsed_until("09:35"), 5);
        assert_eq!(minutes_elapsed_until("09:40"), 10);
        assert_eq!(minutes_elapsed_until("09:45"), 15);
        assert_eq!(minutes_elapsed_until("11:30"), 120);
        assert_eq!(minutes_elapsed_until("13:00"), 120);
        assert_eq!(minutes_elapsed_until("13:30"), 150);
        assert_eq!(minutes_elapsed_until("15:00"), 240);
    }

    #[test]
    fn hides_metrics_that_are_entirely_missing() {
        // 重建场景下资金流与量比可能整体缺失，必须隐藏榜单而不是展示 0
        let aggs = vec![
            NodeAggregate {
                code: "000001".into(),
                name: "平安银行".into(),
                price: 11.39,
                prev_close: 11.35,
                volume: 2089600.0,
                amount: 2.377e9,
                main_net_inflow: 0.0,
                volume_ratio: 0.0,
            },
            NodeAggregate {
                code: "600519".into(),
                name: "贵州茅台".into(),
                price: 1258.62,
                prev_close: 1235.58,
                volume: 3833100.0,
                amount: 4.797e9,
                main_net_inflow: 0.0,
                volume_ratio: 0.0,
            },
        ];
        let input = SnapshotInput::replay("2026-09-30", "09:35", 5, 10);
        let snap = build_snapshot_from_aggregates(&input, &aggs, &[]);
        assert!(snap.netflow_top.is_empty(), "资金榜应被隐藏");
        assert!(snap.anomaly_netflow_top.is_empty());
        assert!(snap.ratio_top.is_empty(), "量比榜应被隐藏");
        assert!(!snap.volume_top.is_empty(), "成交量榜仍应可用");
        assert!(snap.note.contains("不可获取"), "note={}", snap.note);
        assert_eq!(snap.status, DataStatus::Partial);
    }

    #[test]
    fn keeps_metrics_when_data_present() {
        let aggs = vec![NodeAggregate {
            code: "000001".into(),
            name: "平安银行".into(),
            price: 11.39,
            prev_close: 11.35,
            volume: 2089600.0,
            amount: 2.377e9,
            main_net_inflow: 5.0e7,
            volume_ratio: 1.8,
        }];
        let input = SnapshotInput::replay("2026-09-30", "09:35", 5, 10);
        let snap = build_snapshot_from_aggregates(&input, &aggs, &[]);
        assert!(!snap.netflow_top.is_empty());
        assert!(!snap.ratio_top.is_empty());
        assert!(snap.note.is_empty());
    }

    #[test]
    fn cumulative_and_ratio_helpers() {
        assert_eq!(cumulative_from_minutes(&[10.0, 20.0, 30.0]), 60.0);
        // 15 分钟累计 1500 手 -> 每分钟 100；基准 2400/240=10 -> 量比 10
        let r = ratio_from_minutes(1500.0, 15, 2400.0);
        assert!((r - 10.0).abs() < 1e-6);
    }
}
