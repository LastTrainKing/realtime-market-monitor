//! 个股行情 / 资金流采集器（第 8.3 / 8.9 章）。
//!
//! # 股票池只有一个：全市场
//!
//! 早期实现存在**两个**股票池：
//!   * 「活跃股样本」—— 由排行榜接口拼出来（腾讯 `rank_stocks_raw(1200)` 等），
//!     规模上限 **1200 只**；
//!   * 「全市场」—— 本地证券列表 + 批量行情，约 5571 只。
//!
//! 两个池子并存是**错误设计**，理由不是"样本不够准"，而是
//! **排行榜拼出来的池子是有偏的**：它是按成交额 / 换手率 / 量比排序的
//! 前 N 名，天然向涨停、高换手股票倾斜。用它算涨跌家数、涨停跌停、
//! 成交额，得到的不是"略不精确"，而是**系统性失真**
//! （例如涨停家数会被大幅高估）。
//!
//! 而且这个降级样本根本没必要存在：全市场路径走的是
//! **本地证券列表 + 批量行情**，在东方财富、腾讯、新浪三条通道上都能跑通
//! （实测：东方财富被完全阻断时，仍能从新浪拿到 5571/5571、缺失 0）。
//!
//! 因此现在**只保留全市场股票池**：拿不到时如实返回"扫描进行中"，
//! 绝不退回有偏样本、更不拿样本数字冒充市场统计。

use std::sync::Arc;

use crate::models::{DataSource, DataStatus, StockTick};
use crate::net::NetError;
use crate::state::AppState;

pub const CACHE_KEY_FULL: &str = "full";
/// 全市场扫描得到的完整股票列表（涨跌家数、榜单、资金曲线等一切
/// 需要市场口径的模块**都**用它，不存在第二个股票池）
pub const CACHE_KEY_FULL_STOCKS: &str = "full_stocks";

#[derive(Debug, Clone)]
pub struct UniverseResult {
    pub stocks: Vec<StockTick>,
    pub source: DataSource,
    pub status: DataStatus,
    pub message: String,
    pub requests: usize,
}

/// 执行一次个股采集（**全市场口径**）。
///
/// 榜单、涨跌家数、资金排名、资金曲线都必须建立在完整股票宇宙之上，
/// 绝不能出现"全市场 TOP20 实际是 1200 只里的 TOP20"这种逻辑错误。
///
/// 数据来源优先级：
///   1. 全市场扫描已缓存的完整股票列表（`CACHE_KEY_FULL_STOCKS`）
///   2. 缓存为空时主动做一次全市场采集（本地证券列表 + 批量行情）
///   3. 都拿不到时返回**空池 + 原因**，由调用方按"扫描进行中"处理
///
/// **第 3 步刻意不再降级到活跃股样本**：那种池子由排行榜接口拼出，
/// 按成交额 / 换手率排序，天然偏向涨停与高换手股票，
/// 用它算涨跌家数、涨停跌停会系统性失真。
pub async fn run_full_market_cycle(state: &Arc<AppState>) -> UniverseResult {
    let cached = state
        .cache
        .stocks
        .get(CACHE_KEY_FULL_STOCKS)
        .or_else(|| state.cache.stocks.get_stale(CACHE_KEY_FULL_STOCKS));

    if let Some(entry) = cached {
        if !entry.value.is_empty() {
            return UniverseResult {
                stocks: entry.value.clone(),
                source: entry.source,
                status: entry.status,
                message: String::new(),
                requests: 0,
            };
        }
    }

    // 缓存为空：直接做一次全市场采集
    let snap = crate::universe::collect_full_universe(state).await;
    if !snap.stocks.is_empty() {
        let source = match snap.stats.source.as_str() {
            "东方财富实时" => DataSource::EastmoneyRealtime,
            "新浪财经" => DataSource::SinaRealtime,
            "腾讯财经" => DataSource::TencentRealtime,
            _ => DataSource::None,
        };
        let status = if snap.stats.complete {
            DataStatus::Realtime
        } else {
            DataStatus::Partial
        };
        return UniverseResult {
            stocks: snap.stocks,
            source,
            status,
            message: if snap.stats.complete {
                String::new()
            } else {
                snap.stats.note
            },
            requests: snap.stats.batches,
        };
    }

    // 全市场拿不到时如实返回空池 —— 不退回有偏样本，不展示任何数字。
    UniverseResult {
        stocks: Vec::new(),
        source: DataSource::None,
        status: DataStatus::Waiting,
        message: "全市场扫描尚未拿到数据（正在重试）；本程序不会用有偏样本冒充全市场".into(),
        requests: 0,
    }
}

/// 批量快照（自选股 / 单股详情）
pub async fn fetch_quotes(
    state: &Arc<AppState>,
    codes: &[(String, u8)],
) -> Result<(Vec<StockTick>, DataSource), NetError> {
    if codes.is_empty() {
        return Ok((Vec::new(), DataSource::None));
    }
    let cfg = state.config();

    if cfg.enable_eastmoney {
        let secids: Vec<String> = codes
            .iter()
            .map(|(c, m)| crate::source::secid(c, *m))
            .collect();
        match state.eastmoney.ulist(&secids).await {
            Ok(v) if !v.is_empty() => return Ok((v, DataSource::EastmoneyRealtime)),
            Ok(_) => {}
            Err(err) => tracing::debug!(target: "collector", error = %err, "ulist 失败，切换快照源"),
        }
    }

    if cfg.enable_tencent {
        let prefixed: Vec<String> = codes
            .iter()
            .map(|(c, m)| crate::source::prefixed_code(c, *m))
            .collect();
        match state.tencent.quotes(&prefixed).await {
            Ok(v) if !v.is_empty() => return Ok((v, DataSource::TencentRealtime)),
            Ok(_) => {}
            Err(err) => tracing::debug!(target: "collector", error = %err, "腾讯快照失败"),
        }
    }

    if cfg.enable_sina {
        let prefixed: Vec<String> = codes
            .iter()
            .map(|(c, m)| crate::source::prefixed_code(c, *m))
            .collect();
        match state.sina.quotes(&prefixed).await {
            Ok(v) if !v.is_empty() => return Ok((v, DataSource::SinaRealtime)),
            Ok(_) => {}
            Err(err) => tracing::debug!(target: "collector", error = %err, "新浪快照失败"),
        }
    }

    Err(NetError::EmptyBody("全部快照通道不可用".into()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cache_keys_are_stable() {
        assert_eq!(CACHE_KEY_FULL_STOCKS, "full_stocks");
        assert_eq!(CACHE_KEY_FULL, "full");
    }

    /// 回归：股票池只能有一个，且必须是全市场。
    ///
    /// 早期实现存在第二个「活跃股样本」池（由排行榜接口拼出，上限 1200 只），
    /// 会被当作市场统计的降级来源 —— 而排行榜拼出来的池子是有偏的，
    /// 用它算涨跌家数、涨停跌停会系统性失真。
    ///
    /// 注意：关键字用**运行时拼接**构造，否则断言本身就会把待查字符串
    /// 写进被检查的源文件里，`contains` 永远为真（第一次写这个测试就踩了）。
    #[test]
    fn there_is_only_one_stock_pool_and_it_is_the_full_market() {
        let src = include_str!("market.rs");
        let banned = [
            ["CACHE_KEY", "UNIVERSE"].join("_"),
            ["fn collect", "_universe"].concat(),
            ["fn run", "_market_cycle"].concat(),
            ["fn tencent", "_universe"].concat(),
            ["fn sina", "_universe"].concat(),
            ["fn eastmoney", "_universe"].concat(),
        ];
        for needle in &banned {
            assert!(
                !src.contains(needle.as_str()),
                "不允许再出现「活跃股样本」相关实现: {needle}"
            );
        }
        // 唯一股票池必须存在
        assert!(src.contains(&["CACHE_KEY_FULL", "_STOCKS"].concat()));
    }

    #[test]
    fn universe_result_defaults_are_sane() {
        let r = UniverseResult {
            stocks: vec![],
            source: DataSource::None,
            status: DataStatus::Unavailable,
            message: "无数据".into(),
            requests: 0,
        };
        assert!(r.stocks.is_empty());
        assert_eq!(r.requests, 0);
    }
}
