//! 业务计算引擎。
//!
//! 同一套计算逻辑同时服务实时盯盘与历史回放（第 4.1 章核心原则）。

pub mod indicator;
pub mod ranking;
pub mod snapshot;

pub use indicator::{
    breadth, flow_intensity, is_limit_down, is_limit_up, limit_pct, sentiment_score,
};
pub use ranking::{
    EpisodeState, RankingEngine, RankingTracker, TopMetric, top_by,
};
pub use snapshot::{
    NodeAggregate, SnapshotInput, build_snapshot, build_snapshot_from_aggregates, node_status,
};
