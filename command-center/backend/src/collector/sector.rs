//! 板块资金流采集器（第 4.3 / 8.3 章）。

use std::sync::Arc;

use crate::models::{DataSource, DataStatus, SectorTick};
use crate::net::NetError;
use crate::state::AppState;

use super::Timer;

pub const CACHE_KEY_INDUSTRY: &str = "industry";
pub const CACHE_KEY_CONCEPT: &str = "concept";
pub const CACHE_KEY_ALL: &str = "all";

/// 当日板块快照的落盘节点名。
///
/// 首页"主力资金流入流出板块对比"模块在**非交易时段**必须能从本地
/// 持久化快照读取，而不是依赖实时轮询缓存 —— 因此每个交易日都会把
/// 最近一次板块资金流快照以该节点名写入 `sector_nodes` 表。
pub const NODE_LATEST: &str = "latest";

#[derive(Debug, Clone)]
pub struct SectorResult {
    pub sectors: Vec<SectorTick>,
    pub source: DataSource,
    pub status: DataStatus,
    pub message: String,
}

/// 采集行业 + 概念板块
pub async fn collect_sectors(state: &Arc<AppState>) -> SectorResult {
    let cfg = state.config();
    let mut messages: Vec<String> = Vec::new();

    if cfg.enable_eastmoney && !state.gate.is_blocked(crate::state::gate_keys::EM_CLIST) {
        match eastmoney_sectors(state).await {
            Ok((sectors, ind_total, cpt_total)) => {
                state.gate.clear(crate::state::gate_keys::EM_CLIST);
                return SectorResult {
                    sectors,
                    source: DataSource::EastmoneyRealtime,
                    status: DataStatus::Realtime,
                    message: format!(
                        "东方财富全量板块（接口声明 行业 {ind_total} / 概念 {cpt_total}）"
                    ),
                }
            }
            Err(err) => {
                state
                    .gate
                    .block(crate::state::gate_keys::EM_CLIST, &err.to_string());
                messages.push(err.user_message().to_string());
            }
        }
    }

    if cfg.enable_tencent {
        let tx = state.tencent.clone();
        // 腾讯板块榜单单次请求上限实测为 200（250 及以上服务端直接拒绝），
        // 因此这里按上限请求；实际覆盖数在 message 里如实上报，
        // 不再让"降级 = 容量变小"被隐藏。
        let cap = crate::source::tencent::RANK_MAX_COUNT;
        let (a, b) = tokio::join!(
            tx.rank_sectors("industry", cap),
            tx.rank_sectors("concept", cap)
        );
        let mut sectors = Vec::new();
        let mut ok = false;
        if let Ok(mut v) = a {
            sectors.append(&mut v);
            ok = true;
        }
        if let Ok(mut v) = b {
            sectors.append(&mut v);
            ok = true;
        }
        if ok && !sectors.is_empty() {
            let ind_n = sectors.iter().filter(|s| s.kind != "concept").count();
            let cpt_n = sectors.iter().filter(|s| s.kind == "concept").count();
            let detail = format!(
                "腾讯财经通道单次上限 {cap} 条：行业 {ind_n} / 概念 {cpt_n}，\
                 覆盖数明显少于东方财富全量（东财概念板块 400+）"
            );
            return SectorResult {
                sectors,
                source: DataSource::TencentRealtime,
                status: DataStatus::Delay,
                message: if messages.is_empty() {
                    format!("已使用腾讯财经通道（{detail}）")
                } else {
                    format!("{}；已切换腾讯财经（{detail}）", messages.join("；"))
                },
            };
        }
        messages.push("腾讯板块通道不可用".into());
    }

    SectorResult {
        sectors: Vec::new(),
        source: DataSource::None,
        status: DataStatus::Error,
        message: if messages.is_empty() {
            "板块数据通道不可用".into()
        } else {
            messages.join("；")
        },
    }
}

/// 东方财富通道：行业 + 概念板块（**翻页取全**）
///
/// 返回 `(板块列表, 行业总数, 概念总数)`。总数来自接口的 `data.total`，
/// 用于上层上报"是否取全"，避免再次出现"只拿到 200 个概念板块却当作全量"。
async fn eastmoney_sectors(
    state: &Arc<AppState>,
) -> Result<(Vec<SectorTick>, usize, usize), NetError> {
    let em = state.eastmoney.clone();
    let (industry, concept) = tokio::join!(
        em.clist_sectors_all("industry", "f62", true),
        em.clist_sectors_all("concept", "f62", true),
    );
    let mut out = Vec::new();
    let mut errors = Vec::new();
    let mut ind_total = 0usize;
    let mut cpt_total = 0usize;
    match industry {
        Ok((mut v, t)) => {
            ind_total = t;
            out.append(&mut v);
        }
        Err(e) => errors.push(e),
    }
    match concept {
        Ok((mut v, t)) => {
            cpt_total = t;
            out.append(&mut v);
        }
        Err(e) => errors.push(e),
    }
    if out.is_empty() {
        return Err(errors
            .pop()
            .unwrap_or_else(|| NetError::EmptyBody("板块数据为空".into())));
    }
    Ok((out, ind_total, cpt_total))
}

/// 一次板块采集：取数 → 写缓存
pub async fn run_sector_cycle(state: &Arc<AppState>) -> SectorResult {
    let timer = Timer::start();
    let result = collect_sectors(state).await;
    let latency = timer.ms();

    if result.sectors.is_empty() {
        state
            .collectors
            .note_failure("sector", &result.message, latency);
        if let Some(stale) = state.cache.sectors.get_stale(CACHE_KEY_ALL) {
            return SectorResult {
                sectors: stale.value.clone(),
                source: stale.source,
                status: DataStatus::Cache,
                message: result.message,
            };
        }
        return result;
    }

    state.collectors.note_success(
        "sector",
        result.sectors.len(),
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
    fn cache_keys_are_stable() {
        assert_eq!(CACHE_KEY_INDUSTRY, "industry");
        assert_eq!(CACHE_KEY_CONCEPT, "concept");
        assert_eq!(CACHE_KEY_ALL, "all");
    }
}
