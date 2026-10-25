//! 九榜 Ranking Engine（第 4.16 / 8.12 章）。
//!
//! 榜单完全由配置驱动；每个榜单独立计算，禁止一个排序冒充三个榜。
//! 上榜次数采用事件驱动统计（第 8.13 章）：
//!   首次进入榜单 → 记 1 次；持续停留 → 不重复；掉出后再次进入 → 新增 1 次。

use std::collections::{HashMap, HashSet};

use crate::config::{BoardConfig, BoardKind, BoardScope};
use crate::models::{BoardResult, RankEntry, SectorTick, StockAggregate, StockTick};

/// 榜单停留区间状态
#[derive(Debug, Clone)]
pub struct EpisodeState {
    pub board_id: String,
    pub code: String,
    pub name: String,
    pub first_seen: String,
    pub last_seen: String,
    pub best_rank: usize,
    pub last_rank: usize,
    pub max_value: f64,
    /// 上榜事件次数（掉出后重新进入会 +1）
    pub appearances: u32,
    /// 每次进入榜单的时间点（事件历史）
    pub history: Vec<String>,
    /// 当前是否仍在榜
    pub active: bool,
    /// 榜单展示名
    pub board_label: String,
}

#[derive(Default)]
struct TrackerInner {
    /// (board_id, code) -> 区间
    episodes: HashMap<(String, String), EpisodeState>,
    /// 上一轮排名 (board_id, code) -> rank
    prev_rank: HashMap<(String, String), usize>,
    /// 最近一次快照的个股基础信息
    stock_info: HashMap<String, (String, f64, f64, f64)>, // code -> (name, price, change_pct, main_net)
    /// 事件总数（用于监控）
    total_events: u64,
}

/// 榜单追踪器：排名变化 + 事件驱动上榜统计
#[derive(Default)]
pub struct RankingTracker {
    inner: parking_lot::Mutex<TrackerInner>,
}

impl RankingTracker {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn total_events(&self) -> u64 {
        self.inner.lock().total_events
    }

    /// 记录本轮榜单结果，返回本轮“新上榜事件”
    pub fn update_board(
        &self,
        board: &BoardConfig,
        entries: &[RankEntry],
        time: &str,
    ) -> Vec<(String, String, usize, f64)> {
        let mut inner = self.inner.lock();
        let mut new_events = Vec::new();
        let mut present: HashSet<String> = HashSet::new();

        for e in entries {
            present.insert(e.code.clone());
            let key = (board.id.clone(), e.code.clone());
            inner
                .prev_rank
                .insert(key.clone(), e.rank);
            let mut new_event = false;
            match inner.episodes.get_mut(&key) {
                Some(ep) if ep.active => {
                    // 持续停留：更新状态但绝不重复计数（第 8.13 章）
                    ep.last_seen = time.to_string();
                    ep.last_rank = e.rank;
                    ep.best_rank = ep.best_rank.min(e.rank);
                    ep.max_value = ep.max_value.max(e.value);
                    ep.name = e.name.clone();
                }
                Some(ep) => {
                    // 曾掉出榜单，现在重新进入 → 记一次新的上榜事件
                    ep.first_seen = time.to_string();
                    ep.last_seen = time.to_string();
                    ep.best_rank = e.rank;
                    ep.last_rank = e.rank;
                    ep.max_value = e.value;
                    ep.appearances += 1;
                    ep.history.push(time.to_string());
                    ep.name = e.name.clone();
                    ep.active = true;
                    new_event = true;
                }
                None => {
                    // 首次进入
                    let ep = EpisodeState {
                        board_id: board.id.clone(),
                        code: e.code.clone(),
                        name: e.name.clone(),
                        first_seen: time.to_string(),
                        last_seen: time.to_string(),
                        best_rank: e.rank,
                        last_rank: e.rank,
                        max_value: e.value,
                        appearances: 1,
                        history: vec![time.to_string()],
                        active: true,
                        board_label: board.name.clone(),
                    };
                    inner.episodes.insert(key, ep);
                    new_event = true;
                }
            }
            if new_event {
                inner.total_events += 1;
                new_events.push((board.id.clone(), e.code.clone(), e.rank, e.value));
            }
        }

        // 标记掉出榜单的区间（不清除，以便再次进入时累计事件）
        let prefix = board.id.clone();
        for ((bid, _code), ep) in inner.episodes.iter_mut() {
            if *bid == prefix && !present.contains(&ep.code) {
                ep.active = false;
            }
        }
        new_events
    }

    /// 记录个股基础信息（供聚合使用）
    pub fn record_stocks(&self, stocks: &[StockTick]) {
        let mut inner = self.inner.lock();
        for s in stocks {
            inner.stock_info.insert(
                s.code.clone(),
                (s.name.clone(), s.price, s.change_pct, s.main_net_inflow),
            );
        }
    }

    /// 排名变化（正数 = 排名上升）
    pub fn rank_delta(&self, board_id: &str, code: &str, current: usize) -> i32 {
        let inner = self.inner.lock();
        match inner.prev_rank.get(&(board_id.to_string(), code.to_string())) {
            Some(prev) => *prev as i32 - current as i32,
            None => 0,
        }
    }

    /// 某榜单的上榜区间
    pub fn board_episodes(&self, board_id: &str) -> Vec<EpisodeState> {
        let inner = self.inner.lock();
        inner
            .episodes
            .values()
            .filter(|e| e.board_id == board_id)
            .cloned()
            .collect()
    }

    /// 九榜统计聚合（个股精选，第 4.15 章）
    ///
    /// 只统计“个股”口径的榜单，板块榜单不进入个股精选。
    pub fn aggregates(&self, boards: &[BoardConfig], top: usize) -> Vec<StockAggregate> {
        let inner = self.inner.lock();
        let label_of: HashMap<&str, &str> = boards
            .iter()
            .map(|b| (b.id.as_str(), b.name.as_str()))
            .collect();
        let stock_boards: std::collections::HashSet<&str> = boards
            .iter()
            .filter(|b| b.scope == BoardScope::Stock)
            .map(|b| b.id.as_str())
            .collect();

        let mut grouped: HashMap<String, Vec<&EpisodeState>> = HashMap::new();
        for ep in inner.episodes.values() {
            if !stock_boards.contains(ep.board_id.as_str()) {
                continue;
            }
            grouped.entry(ep.code.clone()).or_default().push(ep);
        }

        let mut out: Vec<StockAggregate> = grouped
            .into_iter()
            .map(|(code, eps)| {
                let mut boards_hit: Vec<String> = Vec::new();
                let mut board_labels: Vec<String> = Vec::new();
                let mut history: Vec<String> = Vec::new();
                let mut appear_count = 0u32;
                let mut best_rank = usize::MAX;
                let mut last_rank = usize::MAX;
                let mut last_seen = String::new();
                let mut active = false;

                for ep in eps {
                    appear_count += ep.appearances;
                    if !boards_hit.contains(&ep.board_id) {
                        boards_hit.push(ep.board_id.clone());
                        board_labels.push(
                            label_of
                                .get(ep.board_id.as_str())
                                .map(|s| s.to_string())
                                .unwrap_or_else(|| ep.board_label.clone()),
                        );
                    }
                    for h in &ep.history {
                        if !history.contains(h) {
                            history.push(h.clone());
                        }
                    }
                    best_rank = best_rank.min(ep.best_rank);
                    if ep.last_seen >= last_seen {
                        last_seen = ep.last_seen.clone();
                        last_rank = ep.last_rank;
                    }
                    active = active || ep.active;
                }
                history.sort();

                let (name, price, change_pct, main_net) = inner
                    .stock_info
                    .get(&code)
                    .cloned()
                    .unwrap_or_else(|| (String::new(), 0.0, 0.0, 0.0));

                let board_count = boards_hit.len() as u32;
                let rank_score = if best_rank == usize::MAX {
                    0.0
                } else {
                    (11.0 - best_rank.min(10) as f64).max(0.0)
                };
                let score = board_count as f64 * 25.0
                    + appear_count as f64 * 6.0
                    + rank_score * 3.0
                    + (main_net / 1.0e8).clamp(-5.0, 5.0) * 2.0
                    + if active { 8.0 } else { 0.0 };

                StockAggregate {
                    code,
                    name,
                    appear_count,
                    board_count,
                    boards: boards_hit,
                    board_labels,
                    history,
                    best_rank: if best_rank == usize::MAX { 0 } else { best_rank },
                    last_rank: if last_rank == usize::MAX { 0 } else { last_rank },
                    last_seen,
                    main_net_inflow: main_net,
                    change_pct,
                    price,
                    score,
                    active,
                }
            })
            .collect();

        out.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        out.truncate(top.max(10));
        out
    }

    /// 日切时清空统计状态（跨交易日调用；同一交易日内重启应改用 warm_from_events）
    pub fn reset_day(&self) {
        let mut inner = self.inner.lock();
        inner.episodes.clear();
        inner.prev_rank.clear();
        inner.stock_info.clear();
        inner.total_events = 0;
    }

    /// 从持久化的**区间状态**回灌统计（进程重启后必须调用）。
    ///
    /// 不做回灌会出现：上午 09:35 某股已上榜 1 次，中午关掉软件，
    /// 下午重新打开后 Tracker 从 0 开始，同一只股票被当成"首次出现"，
    /// 导致「九榜事件统计」在重启前后不连续。
    ///
    /// 回灌源必须是 `board_episodes`（它保存了 appearances / active / history），
    /// 而不是 `ranking_events` 的原始行数 —— 后者只是事件流水，
    /// 按行数累加会把"持续停留"重复计数。
    pub fn warm_from_episodes(&self, episodes: &[crate::db::EpisodeRow]) {
        if episodes.is_empty() {
            return;
        }
        let mut inner = self.inner.lock();
        for row in episodes {
            let key = (row.board_id.clone(), row.code.clone());
            let history = if row.history.is_empty() {
                vec![row.first_seen.clone()]
            } else {
                row.history.clone()
            };
            let ep = EpisodeState {
                board_id: row.board_id.clone(),
                code: row.code.clone(),
                name: row.name.clone(),
                first_seen: row.first_seen.clone(),
                last_seen: row.last_seen.clone(),
                best_rank: row.best_rank,
                last_rank: row.last_rank,
                max_value: row.max_value,
                appearances: row.appearances,
                history,
                // 回灌时一律视为"仍在榜"：下一轮计算会根据实际排名修正，
                // 若已掉出会被标记为非活跃且不增加事件数，避免重复计数
                active: true,
                board_label: row.board_id.clone(),
            };
            inner.episodes.insert(key, ep);
        }
        inner.total_events = inner.episodes.values().map(|e| e.appearances as u64).sum();
        tracing::info!(
            target: "app",
            episodes = inner.episodes.len(),
            total_events = inner.total_events,
            "已从持久化区间状态回灌九榜统计"
        );
    }

    pub fn episode_count(&self) -> usize {
        self.inner.lock().episodes.len()
    }
}

/// 榜单计算引擎
pub struct RankingEngine {
    boards: Vec<BoardConfig>,
}

impl RankingEngine {
    pub fn new(boards: Vec<BoardConfig>) -> Self {
        Self { boards }
    }

    pub fn boards(&self) -> &[BoardConfig] {
        &self.boards
    }

    pub fn board_by_id(&self, id: &str) -> Option<&BoardConfig> {
        self.boards.iter().find(|b| b.id == id)
    }

    /// 计算全部榜单
    pub fn compute(
        &self,
        stocks: &[StockTick],
        sectors: &[SectorTick],
        as_of: &str,
        tracker: &RankingTracker,
    ) -> Vec<BoardResult> {
        self.boards
            .iter()
            .filter(|b| b.enabled)
            .map(|board| self.compute_one(board, stocks, sectors, as_of, tracker))
            .collect()
    }

    pub fn compute_one(
        &self,
        board: &BoardConfig,
        stocks: &[StockTick],
        sectors: &[SectorTick],
        as_of: &str,
        tracker: &RankingTracker,
    ) -> BoardResult {
        let mut entries: Vec<RankEntry> = match board.scope {
            BoardScope::Stock => stocks
                .iter()
                .filter(|s| s.price > 0.0 && !s.name.is_empty())
                .map(|s| stock_entry(board.kind, s))
                .collect(),
            BoardScope::Sector => sectors
                .iter()
                .filter(|s| !s.name.is_empty())
                .map(|s| sector_entry(board.kind, s))
                .collect(),
        };

        entries.retain(|e| e.value.is_finite());
        entries.sort_by(|a, b| {
            let ord = b
                .value
                .partial_cmp(&a.value)
                .unwrap_or(std::cmp::Ordering::Equal);
            if board.descending {
                ord
            } else {
                ord.reverse()
            }
        });
        entries.truncate(board.size);

        for (i, e) in entries.iter_mut().enumerate() {
            e.rank = i + 1;
            e.rank_delta = tracker.rank_delta(&board.id, &e.code, e.rank);
        }

        let new_events = tracker.update_board(board, &entries, as_of);
        let new_set: HashSet<String> = new_events.iter().map(|(_, c, _, _)| c.clone()).collect();

        // 用追踪器补充事件统计信息
        let episodes = tracker.board_episodes(&board.id);
        let ep_map: HashMap<String, EpisodeState> = episodes
            .into_iter()
            .map(|e| (e.code.clone(), e))
            .collect();
        for e in entries.iter_mut() {
            if let Some(ep) = ep_map.get(&e.code) {
                e.appear_count = ep.appearances;
                e.first_seen = ep.first_seen.clone();
                e.history = vec![ep.first_seen.clone(), ep.last_seen.clone()];
            }
            if new_set.contains(&e.code) {
                e.first_seen = as_of.to_string();
            }
        }

        // 指标全为 0 说明当前数据源不提供该口径，必须明确标注而不是展示一排 0
        let available = entries.iter().any(|e| e.value.abs() > 1e-9);
        let note = if entries.is_empty() {
            "当前扫描范围内暂无符合条件的标的".to_string()
        } else if !available {
            format!("当前数据源未提供「{}」口径，该榜单暂不可用", board.name)
        } else {
            String::new()
        };
        if !available && !entries.is_empty() {
            entries.clear();
        }

        BoardResult {
            id: board.id.clone(),
            name: board.name.clone(),
            kind: format!("{:?}", board.kind).to_lowercase(),
            scope: match board.scope {
                BoardScope::Stock => "stock".into(),
                BoardScope::Sector => "sector".into(),
            },
            size: board.size,
            entries,
            as_of: as_of.to_string(),
            available,
            note,
        }
    }
}

fn stock_entry(kind: BoardKind, s: &StockTick) -> RankEntry {
    // 主力净占比 = 主力净流入 / 成交额 × 100%。
    // 数据源未提供该字段时按定义推导（真实计算，而非填 0）。
    let derived_ratio = if s.main_net_ratio != 0.0 {
        s.main_net_ratio
    } else if s.amount > 0.0 {
        s.main_net_inflow / s.amount * 100.0
    } else {
        0.0
    };
    let (value, label) = match kind {
        BoardKind::NetFlow => (s.main_net_inflow, crate::models::fmt_money(s.main_net_inflow)),
        BoardKind::NetRatio => (derived_ratio, format!("{derived_ratio:.2}%")),
        BoardKind::Volume => (s.volume, crate::models::fmt_volume(s.volume)),
        BoardKind::Amount => (s.amount, crate::models::fmt_money(s.amount)),
        BoardKind::VolumeRatio => (s.volume_ratio, format!("{:.2}", s.volume_ratio)),
        BoardKind::Turnover => (s.turnover_rate, format!("{:.2}%", s.turnover_rate)),
        BoardKind::ChangePct => (s.change_pct, format!("{:+.2}%", s.change_pct)),
        BoardKind::Composite => (s.composite(), format!("{:.1}", s.composite())),
    };
    RankEntry {
        rank: 0,
        code: s.code.clone(),
        name: s.name.clone(),
        value,
        value_label: label,
        change_pct: s.change_pct,
        price: s.price,
        volume_ratio: s.volume_ratio,
        turnover_rate: s.turnover_rate,
        main_net_inflow: s.main_net_inflow,
        amount: s.amount,
        volume: s.volume,
        rank_delta: 0,
        first_seen: String::new(),
        appear_count: 0,
        boards: Vec::new(),
        history: Vec::new(),
        kind: String::new(),
    }
}

fn sector_entry(kind: BoardKind, s: &SectorTick) -> RankEntry {
    let (value, label) = match kind {
        BoardKind::NetFlow => (s.main_net_inflow, crate::models::fmt_money(s.main_net_inflow)),
        BoardKind::NetRatio => (s.main_net_ratio, format!("{:.2}%", s.main_net_ratio)),
        BoardKind::Volume => (s.volume, crate::models::fmt_volume(s.volume)),
        BoardKind::Amount => (s.amount, crate::models::fmt_money(s.amount)),
        BoardKind::Turnover => (s.turnover_rate, format!("{:.2}%", s.turnover_rate)),
        BoardKind::ChangePct => (s.change_pct, format!("{:+.2}%", s.change_pct)),
        _ => (s.change_pct, format!("{:+.2}%", s.change_pct)),
    };
    RankEntry {
        rank: 0,
        code: s.code.clone(),
        name: s.name.clone(),
        value,
        value_label: label,
        change_pct: s.change_pct,
        price: 0.0,
        volume_ratio: 0.0,
        turnover_rate: s.turnover_rate,
        main_net_inflow: s.main_net_inflow,
        amount: s.amount,
        volume: s.volume,
        rank_delta: 0,
        first_seen: String::new(),
        appear_count: 0,
        boards: Vec::new(),
        history: Vec::new(),
        kind: s.kind.clone(),
    }
}

/// 按指定指标对个股排序并取前 N（供开盘检测 / 异动模块复用）
pub fn top_by(stocks: &[StockTick], metric: TopMetric, n: usize) -> Vec<RankEntry> {
    let mut entries: Vec<RankEntry> = stocks
        .iter()
        .filter(|s| s.price > 0.0 && !s.name.is_empty())
        .map(|s| stock_entry(metric.kind(), s))
        .collect();
    entries.sort_by(|a, b| {
        b.value
            .partial_cmp(&a.value)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    entries.truncate(n);
    for (i, e) in entries.iter_mut().enumerate() {
        e.rank = i + 1;
    }
    entries
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TopMetric {
    Volume,
    NetFlow,
    VolumeRatio,
    Amount,
    ChangePct,
    Turnover,
}

impl TopMetric {
    pub fn kind(&self) -> BoardKind {
        match self {
            TopMetric::Volume => BoardKind::Volume,
            TopMetric::NetFlow => BoardKind::NetFlow,
            TopMetric::VolumeRatio => BoardKind::VolumeRatio,
            TopMetric::Amount => BoardKind::Amount,
            TopMetric::ChangePct => BoardKind::ChangePct,
            TopMetric::Turnover => BoardKind::Turnover,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::default_boards;

    fn stock(code: &str, name: &str, vol: f64, flow: f64, ratio: f64) -> StockTick {
        StockTick {
            code: code.into(),
            name: name.into(),
            price: 10.0,
            volume: vol,
            main_net_inflow: flow,
            volume_ratio: ratio,
            change_pct: 3.0,
            ..Default::default()
        }
    }

    #[test]
    fn computes_all_nine_boards_independently() {
        let stocks = vec![
            stock("000001", "A", 1000.0, 5.0e8, 1.0),
            stock("000002", "B", 5000.0, 1.0e8, 8.0),
            stock("000003", "C", 300.0, 9.0e8, 3.0),
        ];
        let engine = RankingEngine::new(default_boards());
        let tracker = RankingTracker::new();
        let results = engine.compute(&stocks, &[], "09:35", &tracker);
        assert_eq!(results.len(), 9);

        let by_id = |id: &str| results.iter().find(|r| r.id == id).unwrap();
        // 成交量榜第一名必须是 B
        assert_eq!(by_id("volume").entries[0].code, "000002");
        // 主力净流入榜第一名必须是 C
        assert_eq!(by_id("main_net_flow").entries[0].code, "000003");
        // 量比榜第一名必须是 B
        assert_eq!(by_id("volume_ratio").entries[0].code, "000002");
        // 三个榜的第一名不同 -> 说明确实独立计算
        assert_ne!(
            by_id("volume").entries[0].code,
            by_id("main_net_flow").entries[0].code
        );
    }

    #[test]
    fn sector_boards_use_sector_scope() {
        let sectors = vec![SectorTick {
            code: "BK0447".into(),
            name: "通信设备".into(),
            change_pct: 3.2,
            main_net_inflow: 1.5e9,
            ..Default::default()
        }];
        let engine = RankingEngine::new(default_boards());
        let tracker = RankingTracker::new();
        let results = engine.compute(&[], &sectors, "09:35", &tracker);
        let sector_board = results.iter().find(|r| r.id == "sector_net_flow").unwrap();
        assert_eq!(sector_board.entries.len(), 1);
        assert_eq!(sector_board.entries[0].name, "通信设备");
        assert_eq!(sector_board.scope, "sector");
    }

    #[test]
    fn event_driven_counting_no_inflation() {
        let stocks = vec![stock("000001", "A", 1000.0, 5.0e8, 1.0)];
        let engine = RankingEngine::new(default_boards());
        let tracker = RankingTracker::new();

        // 模拟刷新 100 次，股票一直停留榜首
        for i in 0..100 {
            let time = format!("09:{:02}", 35 + (i % 20));
            engine.compute(&stocks, &[], &time, &tracker);
        }
        let agg = tracker.aggregates(&default_boards(), 20);
        let a = agg.iter().find(|x| x.code == "000001").unwrap();
        // 七个个股榜，每榜恰好 1 次事件
        assert_eq!(a.appear_count, 7, "连续停留不应重复累计，实际 {}", a.appear_count);
        assert_eq!(a.board_count, 7);
    }

    #[test]
    fn re_entry_after_drop_counts_again() {
        let engine = RankingEngine::new(default_boards());
        let tracker = RankingTracker::new();
        let present = vec![stock("000001", "A", 1000.0, 5.0e8, 1.0)];
        let absent = vec![stock("000002", "B", 900.0, 4.0e8, 1.0)];

        engine.compute(&present, &[], "09:35", &tracker);
        engine.compute(&absent, &[], "09:40", &tracker); // A 掉出
        engine.compute(&present, &[], "09:45", &tracker); // A 重新进入

        let agg = tracker.aggregates(&default_boards(), 20);
        let a = agg.iter().find(|x| x.code == "000001").unwrap();
        assert_eq!(a.appear_count, 14, "掉出后再进入应新增事件，实际 {}", a.appear_count);
    }

    #[test]
    fn rank_delta_tracks_movement() {
        let engine = RankingEngine::new(default_boards());
        let tracker = RankingTracker::new();
        let first = vec![
            stock("000001", "A", 1000.0, 9.0e8, 1.0),
            stock("000002", "B", 2000.0, 1.0e8, 1.0),
        ];
        engine.compute(&first, &[], "09:35", &tracker);
        let second = vec![
            stock("000001", "A", 1000.0, 1.0e8, 1.0),
            stock("000002", "B", 2000.0, 9.0e8, 1.0),
        ];
        let res = engine.compute(&second, &[], "09:40", &tracker);
        let flow = res.iter().find(|r| r.id == "main_net_flow").unwrap();
        assert_eq!(flow.entries[0].code, "000002");
        assert_eq!(flow.entries[0].rank_delta, 1, "B 从第 2 升到第 1");
    }

    #[test]
    fn unavailable_board_is_marked_not_zero_filled() {
        // 数据源未提供量比（全 0）时，榜单必须标记不可用且不展示 0
        let s = stock("000001", "A", 1000.0, 5.0e7, 0.0);
        let engine = RankingEngine::new(default_boards());
        let tracker = RankingTracker::new();
        let res = engine.compute(&[s], &[], "09:35", &tracker);
        let ratio = res.iter().find(|b| b.id == "volume_ratio").unwrap();
        assert!(!ratio.available);
        assert!(ratio.entries.is_empty(), "不可用榜单不应展示 0 值条目");
        assert!(ratio.note.contains("未提供"));

        let flow = res.iter().find(|b| b.id == "main_net_flow").unwrap();
        assert!(flow.available);
        assert_eq!(flow.entries.len(), 1);
        assert!(flow.note.is_empty());
    }

    #[test]
    fn derives_net_ratio_when_source_missing() {
        let mut s = stock("000001", "A", 1000.0, 5.0e7, 1.0);
        s.amount = 5.0e8; // 成交额 5 亿，主力净流入 0.5 亿 -> 净占比 10%
        s.main_net_ratio = 0.0;
        let engine = RankingEngine::new(default_boards());
        let tracker = RankingTracker::new();
        let res = engine.compute(&[s], &[], "09:35", &tracker);
        let ratio = res.iter().find(|b| b.id == "main_net_ratio").unwrap();
        assert!((ratio.entries[0].value - 10.0).abs() < 1e-6, "应推导出 10%，实际 {}", ratio.entries[0].value);
        assert_eq!(ratio.entries[0].value_label, "10.00%");
    }

    #[test]
    fn aggregates_exclude_sector_boards() {
        let engine = RankingEngine::new(default_boards());
        let tracker = RankingTracker::new();
        let stocks = vec![stock("000001", "A", 1000.0, 5.0e8, 1.0)];
        let sectors = vec![SectorTick {
            code: "BK0447".into(),
            name: "通信设备".into(),
            change_pct: 3.0,
            main_net_inflow: 1.5e9,
            ..Default::default()
        }];
        engine.compute(&stocks, &sectors, "09:35", &tracker);
        let agg = tracker.aggregates(&default_boards(), 20);
        assert!(
            agg.iter().all(|a| !a.code.starts_with("BK") && !a.code.starts_with("pt")),
            "个股精选不应包含板块条目：{:?}",
            agg.iter().map(|a| &a.code).collect::<Vec<_>>()
        );
        assert!(agg.iter().any(|a| a.code == "000001"));
    }

    #[test]
    fn aggregates_rank_multi_board_stocks_higher() {
        let engine = RankingEngine::new(default_boards());
        let tracker = RankingTracker::new();
        // A 同时霸榜成交量 + 净流入 + 量比；B 只强在成交量
        let stocks = vec![
            stock("000001", "A", 9999.0, 9.9e8, 9.9),
            stock("000002", "B", 9000.0, -1.0e8, 1.0),
        ];
        engine.compute(&stocks, &[], "09:35", &tracker);
        let agg = tracker.aggregates(&default_boards(), 20);
        assert_eq!(agg[0].code, "000001");
        assert!(agg[0].board_count >= agg[1].board_count);
        assert!(agg[0].score > agg[1].score);
    }

    #[test]
    fn top_by_metric_works() {
        let stocks = vec![
            stock("000001", "A", 1000.0, 5.0e8, 1.0),
            stock("000002", "B", 5000.0, 1.0e8, 8.0),
        ];
        let t = top_by(&stocks, TopMetric::Volume, 1);
        assert_eq!(t.len(), 1);
        assert_eq!(t[0].code, "000002");
        let t2 = top_by(&stocks, TopMetric::NetFlow, 2);
        assert_eq!(t2[0].code, "000001");
    }

    #[test]
    fn warm_from_episodes_restores_counters_after_restart() {
        let engine = RankingEngine::new(default_boards());
        let tracker = RankingTracker::new();
        let stocks = vec![stock("000001", "A", 1000.0, 5.0e8, 1.0)];

        // 上午跑了 3 轮，A 一直在榜
        for t in ["09:35", "09:40", "09:45"] {
            engine.compute(&stocks, &[], t, &tracker);
        }
        let before = tracker.aggregates(&default_boards(), 20);
        let a_before = before.iter().find(|x| x.code == "000001").unwrap().clone();
        assert_eq!(a_before.appear_count, 7, "7 个个股榜各 1 次事件");

        // 模拟进程重启：Tracker 重新构造，然后从持久化事件回灌
        let tracker2 = RankingTracker::new();
        assert_eq!(tracker2.total_events(), 0);
        // 数据库中保存的是区间状态：7 个个股榜各 1 次事件
        let rows: Vec<crate::db::EpisodeRow> = default_boards()
            .into_iter()
            .filter(|b| b.scope == BoardScope::Stock)
            .map(|b| crate::db::EpisodeRow {
                board_id: b.id,
                code: "000001".into(),
                name: "A".into(),
                first_seen: "09:35".into(),
                last_seen: "09:45".into(),
                best_rank: 1,
                last_rank: 1,
                max_value: 1.0,
                appearances: 1,
                active: true,
                history: vec!["09:35".into()],
            })
            .collect();
        tracker2.warm_from_episodes(&rows);

        let after = tracker2.aggregates(&default_boards(), 20);
        let a_after = after.iter().find(|x| x.code == "000001").unwrap();
        assert_eq!(
            a_after.appear_count, a_before.appear_count,
            "重启回灌后事件数必须与重启前一致"
        );
        assert_eq!(a_after.board_count, a_before.board_count);

        // 回灌后继续计算：仍在榜不应重复计数
        engine.compute(&stocks, &[], "09:50", &tracker2);
        let after2 = tracker2.aggregates(&default_boards(), 20);
        let a2 = after2.iter().find(|x| x.code == "000001").unwrap();
        assert_eq!(a2.appear_count, a_before.appear_count, "持续停留不得重复计数");
    }

    #[test]
    fn warm_from_episodes_keeps_reentry_counting() {
        // 只保留一个榜单，便于精确断言事件计数
        let boards: Vec<crate::config::BoardConfig> = default_boards()
            .into_iter()
            .filter(|b| b.id == "volume")
            .collect();
        let engine = RankingEngine::new(boards.clone());
        let tracker = RankingTracker::new();
        let rows = vec![crate::db::EpisodeRow {
            board_id: "volume".into(),
            code: "000001".into(),
            name: "A".into(),
            first_seen: "09:35".into(),
            last_seen: "09:35".into(),
            best_rank: 1,
            last_rank: 1,
            max_value: 1.0,
            appearances: 1,
            active: true,
            history: vec!["09:35".into()],
        }];
        tracker.warm_from_episodes(&rows);
        assert_eq!(tracker.total_events(), 1, "回灌后应还原为 1 次事件");

        // 该股不在当前榜单 -> 先掉出（掉出本身不计事件）
        engine.compute(&[stock("000002", "B", 1.0, 1.0, 1.0)], &[], "09:40", &tracker);
        assert_eq!(tracker.total_events(), 2, "B 首次进入 +1，A 掉出不计");

        // 重新进入 -> 应新增一次事件
        engine.compute(&[stock("000001", "A", 9999.0, 9.0e8, 9.0)], &[], "09:45", &tracker);
        let agg = tracker.aggregates(&boards, 20);
        let a = agg.iter().find(|x| x.code == "000001").unwrap();
        assert_eq!(a.appear_count, 2, "回灌后掉出再进入应计为第 2 次事件");
        assert_eq!(a.history.len(), 2, "两次进入的时间点都应保留");
        assert_eq!(a.history, vec!["09:35".to_string(), "09:45".to_string()]);
    }

    #[test]
    fn reset_day_clears_state() {
        let engine = RankingEngine::new(default_boards());
        let tracker = RankingTracker::new();
        engine.compute(&[stock("000001", "A", 1.0, 1.0, 1.0)], &[], "09:35", &tracker);
        assert!(tracker.episode_count() > 0);
        tracker.reset_day();
        assert_eq!(tracker.episode_count(), 0);
        assert_eq!(tracker.total_events(), 0);
    }
}
