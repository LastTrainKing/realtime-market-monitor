//! 指数行情采集器（第 8.8 章：秒级刷新重要指标）。

use std::sync::Arc;

use crate::models::{DataSource, DataStatus, IndexTick};
use crate::state::AppState;

use super::Timer;

pub const CACHE_KEY_INDICES: &str = "indices";

#[derive(Debug, Clone)]
pub struct IndexResult {
    pub indices: Vec<IndexTick>,
    pub source: DataSource,
    pub status: DataStatus,
    pub message: String,
}

pub async fn collect_indices(state: &Arc<AppState>) -> IndexResult {
    let cfg = state.config();
    let mut messages = Vec::new();

    if cfg.enable_eastmoney && !state.gate.is_blocked(crate::state::gate_keys::EM_ULIST) {
        match state.eastmoney.indices().await {
            Ok(v) if !v.is_empty() => {
                state.gate.clear(crate::state::gate_keys::EM_ULIST);
                return IndexResult {
                    indices: v,
                    source: DataSource::EastmoneyRealtime,
                    status: DataStatus::Realtime,
                    message: String::new(),
                }
            }
            Ok(_) => messages.push("指数通道返回为空".to_string()),
            Err(err) => {
                state
                    .gate
                    .block(crate::state::gate_keys::EM_ULIST, &err.to_string());
                messages.push(err.user_message().to_string());
            }
        }
    }

    if cfg.enable_tencent {
        match state.tencent.indices().await {
            Ok(v) if !v.is_empty() => {
                return IndexResult {
                    indices: v,
                    source: DataSource::TencentRealtime,
                    status: DataStatus::Delay,
                    message: if messages.is_empty() {
                        "已使用腾讯财经通道".into()
                    } else {
                        format!("{}；已切换腾讯财经", messages.join("；"))
                    },
                }
            }
            Ok(_) => messages.push("腾讯指数通道返回为空".to_string()),
            Err(err) => messages.push(err.user_message().to_string()),
        }
    }

    IndexResult {
        indices: Vec::new(),
        source: DataSource::None,
        status: DataStatus::Error,
        message: if messages.is_empty() {
            "指数数据通道不可用".into()
        } else {
            messages.join("；")
        },
    }
}

pub async fn run_index_cycle(state: &Arc<AppState>) -> IndexResult {
    let timer = Timer::start();
    let result = collect_indices(state).await;
    let latency = timer.ms();

    if result.indices.is_empty() {
        state
            .collectors
            .note_failure("index", &result.message, latency);
        if let Some(stale) = state.cache.indices.get_stale(CACHE_KEY_INDICES) {
            return IndexResult {
                indices: stale.value.clone(),
                source: stale.source,
                status: DataStatus::Cache,
                message: result.message,
            };
        }
        return result;
    }

    state.collectors.note_success(
        "index",
        result.indices.len(),
        latency,
        result.source,
        result.status,
    );
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cache_key_stable() {
        assert_eq!(CACHE_KEY_INDICES, "indices");
    }

    #[test]
    fn empty_result_reports_error_status() {
        let r = IndexResult {
            indices: vec![],
            source: DataSource::None,
            status: DataStatus::Error,
            message: "x".into(),
        };
        assert!(r.indices.is_empty());
        assert_eq!(r.status, DataStatus::Error);
    }
}
