//! 市场资金曲线采集器（第 4.6 / 8.7 章）。
//!
//! # 口径定义（重要，v1.0.1 修正）
//!
//! 取数优先级：
//!
//! 1. **东方财富现成的指数分钟资金流**（`fflow/kline`，secid=1.000001）。
//!    这是"大盘资金流"，一次请求直接返回当日的**累计**主力净流入，
//!    **不需要任何累加**。首页模块②读的历史库序列与这里同源，
//!    实时曲线与历史曲线因此口径一致。
//! 2. 上述通道不可用时，退回**股票层面**的 `main_net_inflow` 求和，
//!    并在 `note` 里明确标注为降级口径。
//!
//! 股票池**只有一个：全市场**（本地证券列表 + 批量行情）。
//! 池子未达全市场规模时一律不生成曲线、显示"等待"，
//! 绝不退回排行榜拼出来的有偏样本 —— 那种样本按成交额 / 换手率排序，
//! 算出来的"市场资金流"是系统性失真的。
//!
//! 无论哪条路径，都**严禁**用「行业板块 + 概念板块」的资金流相加来近似：
//! 行业与概念不是互斥集合，同一只股票（如"半导体"+"AI"+"国产替代"）会被
//! 重复计入多个板块，求和结果在数学上就不是市场主力净流入，且金额会被严重放大。
//!
//! > 早期版本的模块注释曾把"股票层面求和"写成唯一正确口径。
//! > 那个结论只对了一半 —— 它解决了"板块相加重复计算"，
//! > 却忽略了数据源本身已经提供了现成的市场级累计序列。

use std::sync::Arc;

use crate::models::{DataSource, DataStatus, FlowPoint, MarketFlowSeries, StockTick};
use crate::state::AppState;

use super::Timer;

pub const CACHE_PREFIX_INDEX: &str = "index_flow:";
pub const CACHE_PREFIX_STOCK: &str = "stock_flow:";

/// 采集指定标的的分时资金流（个股维度，走数据源接口）
pub async fn collect_flow(
    state: &Arc<AppState>,
    code: &str,
    market: u8,
    limit: usize,
) -> (Vec<FlowPoint>, DataSource, DataStatus, String) {
    let cfg = state.config();
    if cfg.enable_eastmoney && !state.gate.is_blocked(crate::state::gate_keys::EM_FFLOW) {
        match state
            .eastmoney
            .minute_flow(code, market, limit.max(60))
            .await
        {
            Ok(points) if !points.is_empty() => {
                return (
                    points,
                    DataSource::EastmoneyRealtime,
                    DataStatus::Realtime,
                    String::new(),
                )
            }
            Ok(_) => {}
            Err(err) => {
                tracing::debug!(target: "collector", code, error = %err, "分钟资金流不可用");
                return (
                    Vec::new(),
                    DataSource::None,
                    DataStatus::DataSourceLimitation,
                    err.user_message().to_string(),
                );
            }
        }
    }
    (
        Vec::new(),
        DataSource::None,
        DataStatus::DataSourceLimitation,
        "当前通道不提供分时资金流".into(),
    )
}

/// 由**股票层面**汇总出一个市场资金流点。
///
/// 这是唯一被允许用于「两市主力资金曲线」的计算方式。
pub fn aggregate_market_flow(stocks: &[StockTick], time: &str) -> FlowPoint {
    let mut point = FlowPoint {
        time: time.to_string(),
        ..Default::default()
    };
    for s in stocks {
        if s.price <= 0.0 {
            continue;
        }
        point.main_net += s.main_net_inflow;
        point.super_large += s.super_large_net;
        point.large += s.large_net;
        point.medium += s.medium_net;
        point.small += s.small_net;
    }
    point
}

/// A 股全市场股票数量下限（低于此规模一律视为**数据不完整**，不用于市场统计）
pub const FULL_MARKET_MIN_STOCKS: usize = 3000;

/// 判断该股票池是否达到全市场口径。
///
/// 必须使用**绝对阈值**：早期版本用"池子自身长度的一半"做判定，
/// 结果 1200 只的排行榜样本也会被误判为 full，把有偏样本当成全市场展示。
fn is_full_market(stocks: &[StockTick]) -> bool {
    stocks.len() >= FULL_MARKET_MIN_STOCKS
}

/// 把本轮资金流点追加到序列（保留当日全部点）
pub fn push_point(series: &mut Vec<FlowPoint>, point: FlowPoint, max_points: usize) {
    if series.last().map(|p| p.time.as_str()) == Some(point.time.as_str()) {
        if let Some(last) = series.last_mut() {
            *last = point;
        }
    } else {
        series.push(point);
    }
    if series.len() > max_points {
        let excess = series.len() - max_points;
        series.drain(0..excess);
    }
}

/// 把一个资金流点追加进内存曲线并写缓存，返回曲线点数。
///
/// 抽出来是为了让"东财现成接口"与"股票层面求和"两条路径共用同一段
/// 缓存逻辑，避免两处实现漂移。
fn cache_flow_point(
    state: &Arc<AppState>,
    point: FlowPoint,
    note: String,
    scope: &str,
    sample_size: i32,
    source: DataSource,
    status: DataStatus,
) -> usize {
    let mut series = state
        .cache
        .flow_series
        .get_stale(CACHE_PREFIX_INDEX)
        .map(|e| e.value.clone())
        .unwrap_or_default();
    push_point(&mut series, point, 600);
    let count = series.len();

    state.cache.market_flow.put_value(
        CACHE_PREFIX_INDEX,
        MarketFlowSeries {
            points: series,
            scope: scope.to_string(),
            sample_size,
            note,
        },
        source,
        status,
        std::time::Duration::from_secs(900),
    );
    count
}

pub async fn run_capital_cycle(state: &Arc<AppState>) -> (usize, DataStatus, String) {
    let timer = Timer::start();
    let time = crate::clock::now_cn().format("%H:%M").to_string();

    // ------------------------------------------------------------------
    // 首选：东方财富**现成**的指数分钟资金流（fflow/kline）
    //
    // 这条"大盘资金流"接口一次请求就返回当日的累计主力净流入，
    // **不需要**把 5571 只股票相加 —— 而且相加出来的口径与它并不等价。
    // 首页模块②读的历史库序列用的就是这个接口，实时曲线必须与它同源，
    // 否则同一天会出现两条口径不同的"资金曲线"。
    //
    // 只有该通道不可用时，才退回"股票层面合计"，并在 note 里写明口径。
    // ------------------------------------------------------------------
    let cfg = state.config();
    if cfg.enable_eastmoney && !state.gate.is_blocked(crate::state::gate_keys::EM_FFLOW) {
        match state
            .eastmoney
            .minute_flow_by_secid(crate::history::preload::SH_INDEX_SECID, 0)
            .await
        {
            Ok(points) if !points.is_empty() => {
                state.gate.clear(crate::state::gate_keys::EM_FFLOW);
                let last = points.last().expect("已判非空");
                let point = FlowPoint {
                    time: last.time.clone(),
                    main_net: last.main_net,
                    super_large: last.super_large,
                    large: last.large,
                    medium: last.medium,
                    small: last.small,
                };
                let note = format!(
                    "东方财富大盘资金流（fflow/kline 现成接口，接口本身即累计口径，未做任何累加）；\
                     数据时间 {}",
                    point.time
                );
                let count = cache_flow_point(state, point, note.clone(), "source", 0, DataSource::EastmoneyRealtime, DataStatus::Realtime);
                state
                    .collectors
                    .note_success("capital", count, timer.ms(), DataSource::EastmoneyRealtime, DataStatus::Realtime);
                return (count, DataStatus::Realtime, note);
            }
            Ok(_) => {
                state
                    .gate
                    .block(crate::state::gate_keys::EM_FFLOW, "大盘资金流返回为空");
            }
            Err(err) => {
                state
                    .gate
                    .block(crate::state::gate_keys::EM_FFLOW, &err.to_string());
                tracing::warn!(target: "collector", err = %err.user_message(), "大盘资金流不可用，退回股票层面合计");
            }
        }
    }

    // 只有一个股票池：全市场（本地证券列表 + 批量行情）。
    //
    // 池子规模没达到全市场标准时**不生成曲线** —— 宁可显示"等待"，
    // 也不拿不完整数据充当市场口径（早期版本会退回一个有偏的 1200 只样本）。
    let pool = state
        .cache
        .stocks
        .get(super::market::CACHE_KEY_FULL_STOCKS)
        .or_else(|| state.cache.stocks.get_stale(super::market::CACHE_KEY_FULL_STOCKS))
        .map(|e| (e.value.clone(), e.source, e.status));

    let (stocks, source, status) = match pool {
        Some((v, src, st)) if !v.is_empty() => (v, src, st),
        _ => {
            state
                .collectors
                .note_status("capital", DataStatus::Waiting, "等待全市场股票池");
            return (0, DataStatus::Waiting, "等待全市场股票池".into());
        }
    };

    if !is_full_market(&stocks) {
        let n = stocks.len();
        let msg = format!(
            "全市场股票池尚未就绪（当前 {n} 只，未达全市场规模），暂不生成资金曲线"
        );
        state
            .collectors
            .note_status("capital", DataStatus::Waiting, &msg);
        return (0, DataStatus::Waiting, msg);
    }

    let sample_size = stocks.len() as i32;
    let point = aggregate_market_flow(&stocks, &time);

    // 降级口径：东财大盘资金流不可用，才用股票层面求和，
    // 并且必须在 note 里写明"这不是数据源的现成口径"。
    let scope = "full".to_string();
    let note = format!(
        "降级口径：东财大盘资金流不可用，暂以全市场 {sample_size} 只个股主力净流入合计代替"
    );
    let count = cache_flow_point(
        state,
        point,
        note.clone(),
        &scope,
        sample_size,
        source,
        status,
    );

    let latency = timer.ms();
    state
        .collectors
        .note_success("capital", count, latency, source, status);
    (count, status, note)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stock(code: &str, flow: f64) -> StockTick {
        StockTick {
            code: code.into(),
            name: code.into(),
            price: 10.0,
            main_net_inflow: flow,
            super_large_net: flow * 0.6,
            large_net: flow * 0.4,
            medium_net: -flow * 0.3,
            small_net: -flow * 0.7,
            ..Default::default()
        }
    }

    #[test]
    fn aggregates_stock_level_flow() {
        let stocks = vec![stock("000001", 1.0e8), stock("600519", -3.0e7)];
        let p = aggregate_market_flow(&stocks, "09:35");
        assert!((p.main_net - 7.0e7).abs() < 1.0);
        assert!((p.super_large - 4.2e7).abs() < 1.0);
        assert!((p.large - 2.8e7).abs() < 1.0);
        assert_eq!(p.time, "09:35");
    }

    #[test]
    fn skips_invalid_stocks() {
        let mut bad = stock("000002", 9.9e9);
        bad.price = 0.0;
        let p = aggregate_market_flow(&[bad], "09:35");
        assert_eq!(p.main_net, 0.0, "无效标的不应计入");
    }

    #[test]
    fn full_market_detection() {
        // 回归：1200 只活跃股样本绝不能被判为全市场口径
        let small: Vec<StockTick> = (0..1200)
            .map(|i| stock(&format!("{i:06}"), 0.0))
            .collect();
        assert!(!is_full_market(&small), "1200 只属于活跃股样本");
        assert!(!is_full_market(&small[..2500.min(small.len())]), "2500 只仍属样本");

        let big: Vec<StockTick> = (0..5000)
            .map(|i| stock(&format!("{i:06}"), 0.0))
            .collect();
        assert!(is_full_market(&big), "5000 只属于全市场");
        assert!(is_full_market(&big[..3000]), "恰好 3000 只达到全市场下限");
    }

    #[test]
    fn push_point_replaces_same_minute() {
        let mut series = Vec::new();
        push_point(
            &mut series,
            FlowPoint {
                time: "09:35".into(),
                main_net: 1.0,
                ..Default::default()
            },
            100,
        );
        push_point(
            &mut series,
            FlowPoint {
                time: "09:35".into(),
                main_net: 2.0,
                ..Default::default()
            },
            100,
        );
        assert_eq!(series.len(), 1);
        assert_eq!(series[0].main_net, 2.0);
        push_point(
            &mut series,
            FlowPoint {
                time: "09:36".into(),
                main_net: 3.0,
                ..Default::default()
            },
            100,
        );
        assert_eq!(series.len(), 2);
    }

    #[test]
    fn push_point_trims_to_max() {
        let mut series = Vec::new();
        for i in 0..50 {
            push_point(
                &mut series,
                FlowPoint {
                    time: format!("09:{i:02}"),
                    main_net: i as f64,
                    ..Default::default()
                },
                10,
            );
        }
        assert_eq!(series.len(), 10);
        assert_eq!(series.last().unwrap().main_net, 49.0);
    }
}
