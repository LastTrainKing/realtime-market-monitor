//! 数据采集层（第 4.3 / 4.4 / 8.3 章）。
//!
//! 每个采集器独立运行、独立计时，禁止把全部请求塞进一个循环。
//! 采集器只负责“取数 → 归一化 → 写缓存”，计算由 engine 完成。

pub mod capital;
pub mod history;
pub mod index;
pub mod market;
pub mod sector;

use std::collections::HashMap;
use std::sync::Arc;

use crate::models::{BoardResult, DataSource, DataStatus, StockTick};
use crate::state::AppState;

/// 合并多批个股数据（按代码去重，保留信息更完整的一条）
pub fn merge_stocks(batches: Vec<Vec<StockTick>>) -> Vec<StockTick> {
    let mut map: HashMap<String, StockTick> = HashMap::new();
    for batch in batches {
        for tick in batch {
            if tick.code.is_empty() || tick.price <= 0.0 {
                continue;
            }
            match map.get(&tick.code) {
                Some(existing) if completeness(existing) >= completeness(&tick) => {}
                _ => {
                    map.insert(tick.code.clone(), tick);
                }
            }
        }
    }
    map.into_values().collect()
}

fn completeness(t: &StockTick) -> u32 {
    let mut score = 0;
    if t.name.is_empty() {
        return 0;
    }
    score += 1;
    if t.price > 0.0 {
        score += 1;
    }
    if t.volume > 0.0 {
        score += 1;
    }
    if t.amount > 0.0 {
        score += 1;
    }
    if t.main_net_inflow != 0.0 {
        score += 2;
    }
    if t.volume_ratio > 0.0 {
        score += 1;
    }
    if t.turnover_rate > 0.0 {
        score += 1;
    }
    score
}

/// 把本轮榜单结果持久化（事件驱动，第 8.14 章）。
///
/// `source` 必须是本轮数据的真实来源，禁止硬编码为 eastmoney
/// （否则界面显示"已切换腾讯"，数据库却记录成"东方财富"，血缘自相矛盾）。
pub async fn persist_boards(
    state: &Arc<AppState>,
    boards: &[BoardResult],
    trade_date: &str,
    source: crate::models::DataSource,
) -> usize {
    let db = state.db.clone();
    let date = trade_date.to_string();
    let boards = boards.to_vec();
    let source = source.label().to_string();
    let result = tokio::task::spawn_blocking(move || {
        let mut new_events = 0usize;
        let trade_date_parsed = chrono::NaiveDate::parse_from_str(&date, "%Y-%m-%d")
            .unwrap_or_else(|_| crate::clock::now_cn().date_naive());

        for board in &boards {
            let mut present: Vec<String> = Vec::with_capacity(board.entries.len());
            for entry in &board.entries {
                present.push(entry.code.clone());

                // 事件驱动：只有"首次进入"或"掉出后重新进入"才返回 true
                let is_new_event = db
                    .record_episode(
                        &date,
                        &board.id,
                        &entry.code,
                        &entry.name,
                        &board.as_of,
                        entry.rank,
                        entry.value,
                    )
                    .unwrap_or(false);

                if is_new_event {
                    new_events += 1;
                    // 事件表只记录真正的"进入事件"，不是每个节点的存在记录，
                    // 否则重启回灌时会把"持续停留"重复计成多次事件
                    let ev = crate::models::RankingEvent {
                        trade_date: trade_date_parsed,
                        stock_code: entry.code.clone(),
                        stock_name: entry.name.clone(),
                        rank_type: board.id.clone(),
                        rank_time: board.as_of.clone(),
                        rank_position: entry.rank,
                        value: entry.value,
                        source: source.clone(),
                    };
                    let _ = db.record_ranking_event(&ev);
                }
            }
            // 本轮已掉出榜单的标的标记为非活跃，便于下次重新进入时累计事件
            let _ = db.deactivate_missing_episodes(&date, &board.id, &present);
        }
        new_events
    })
    .await;
    result.unwrap_or(0)
}

/// 记录采集耗时并写入状态
pub struct Timer {
    started: std::time::Instant,
}

impl Timer {
    pub fn start() -> Self {
        Self {
            started: std::time::Instant::now(),
        }
    }
    pub fn ms(&self) -> u64 {
        self.started.elapsed().as_millis() as u64
    }
}

/// 统一的降级提示文案（第 8.16 章）
pub fn degraded_note(source: DataSource, detail: &str) -> String {
    format!("{}不可用（{}），已切换至{}", "主数据源", detail, source.label())
}

pub fn status_for_source(source: DataSource) -> DataStatus {
    match source {
        DataSource::EastmoneyRealtime => DataStatus::Realtime,
        DataSource::SinaRealtime | DataSource::TencentRealtime => DataStatus::Delay,
        DataSource::MemoryCache => DataStatus::Cache,
        DataSource::DatabaseReplay | DataSource::EastmoneyHistory => DataStatus::Rebuild,
        DataSource::None => DataStatus::Unavailable,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tick(code: &str, name: &str, price: f64, flow: f64, ratio: f64) -> StockTick {
        StockTick {
            code: code.into(),
            name: name.into(),
            price,
            main_net_inflow: flow,
            volume_ratio: ratio,
            volume: 100.0,
            amount: 1000.0,
            ..Default::default()
        }
    }

    #[test]
    fn merge_dedupes_and_prefers_complete() {
        let a = vec![tick("000001", "平安银行", 11.0, 0.0, 0.0)];
        let b = vec![tick("000001", "平安银行", 11.0, 5.0e7, 1.5)];
        let merged = merge_stocks(vec![a, b]);
        assert_eq!(merged.len(), 1);
        assert_eq!(merged[0].main_net_inflow, 5.0e7);
        assert_eq!(merged[0].volume_ratio, 1.5);
    }

    #[test]
    fn merge_drops_invalid_entries() {
        let bad = vec![
            StockTick {
                code: String::new(),
                price: 1.0,
                ..Default::default()
            },
            StockTick {
                code: "000001".into(),
                price: 0.0,
                ..Default::default()
            },
        ];
        assert!(merge_stocks(vec![bad]).is_empty());
    }

    #[test]
    fn merge_keeps_distinct_codes() {
        let merged = merge_stocks(vec![
            vec![tick("000001", "A", 1.0, 0.0, 0.0)],
            vec![tick("600519", "B", 2.0, 0.0, 0.0)],
        ]);
        assert_eq!(merged.len(), 2);
    }

    #[test]
    fn status_mapping_matches_source() {
        assert_eq!(
            status_for_source(DataSource::EastmoneyRealtime),
            DataStatus::Realtime
        );
        assert_eq!(
            status_for_source(DataSource::TencentRealtime),
            DataStatus::Delay
        );
        assert_eq!(status_for_source(DataSource::None), DataStatus::Unavailable);
    }

    #[test]
    fn degraded_note_is_user_friendly() {
        let note = degraded_note(DataSource::TencentRealtime, "HTTP 403");
        assert!(note.contains("腾讯财经"));
        assert!(note.contains("已切换"));
    }
}
