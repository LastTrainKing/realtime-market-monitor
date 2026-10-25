//! 指标计算（第 4.13 / 8.11 章）。
//!
//! 所有指标计算集中于此，实时与历史回放使用同一套函数（第 4.1 章原则）。

use crate::models::{MarketBreadth, StockTick};

/// A 股涨跌停幅度（按板块）
pub fn limit_pct(code: &str, name: &str) -> f64 {
    let upper = name.to_uppercase();
    if upper.contains("ST") {
        return 5.0;
    }
    match code.chars().next() {
        Some('3') => 20.0,          // 创业板
        Some('6') if code.starts_with("688") => 20.0, // 科创板
        Some('4') | Some('8') => 30.0, // 北交所
        _ => 10.0,
    }
}

pub fn is_limit_up(t: &StockTick) -> bool {
    let lim = limit_pct(&t.code, &t.name);
    t.change_pct >= lim - 0.15
}

pub fn is_limit_down(t: &StockTick) -> bool {
    let lim = limit_pct(&t.code, &t.name);
    t.change_pct <= -lim + 0.15
}

/// 市场概况（涨跌家数 / 涨跌停 / 总成交额 / 总主力净流入）
pub fn breadth(stocks: &[StockTick]) -> MarketBreadth {
    let mut b = MarketBreadth::default();
    for s in stocks {
        if s.price <= 0.0 || s.name.is_empty() {
            continue;
        }
        if s.change_pct > 0.01 {
            b.up_count += 1;
        } else if s.change_pct < -0.01 {
            b.down_count += 1;
        } else {
            b.flat_count += 1;
        }
        if is_limit_up(s) {
            b.limit_up_count += 1;
        }
        if is_limit_down(s) {
            b.limit_down_count += 1;
        }
        b.total_amount += s.amount;
        b.total_main_net += s.main_net_inflow;
    }
    let total = b.up_count + b.down_count + b.flat_count;
    b.up_ratio = if total > 0 {
        b.up_count as f64 / total as f64 * 100.0
    } else {
        0.0
    };
    b
}

/// 量比
///
/// 若数据源直接提供则直接使用；否则按
/// `当前每分钟成交量 / 过去 N 日每分钟平均成交量` 估算。
pub fn volume_ratio(
    provided: f64,
    current_volume: f64,
    minutes_elapsed: i64,
    avg_daily_volume: f64,
    trading_minutes_per_day: f64,
) -> f64 {
    if provided > 0.0 {
        return provided;
    }
    if minutes_elapsed <= 0 || avg_daily_volume <= 0.0 || trading_minutes_per_day <= 0.0 {
        return 0.0;
    }
    let per_minute = current_volume / minutes_elapsed as f64;
    let baseline = avg_daily_volume / trading_minutes_per_day;
    if baseline <= 0.0 {
        return 0.0;
    }
    (per_minute / baseline * 100.0).round() / 100.0
}

/// 开盘以来成交量（实时 volume 字段本身即为自开盘累计，直接使用）
pub fn cumulative_volume(t: &StockTick) -> f64 {
    t.volume
}

/// 资金强度：主力净流入 / 成交额（‰），用于衡量资金介入力度
pub fn flow_intensity(t: &StockTick) -> f64 {
    if t.amount <= 0.0 {
        return 0.0;
    }
    t.main_net_inflow / t.amount * 1000.0
}

/// 复合情绪分（-100 ~ 100）
pub fn sentiment_score(stocks: &[StockTick]) -> f64 {
    let b = breadth(stocks);
    let total = (b.up_count + b.down_count + b.flat_count).max(1) as f64;
    let breadth_score = (b.up_count - b.down_count) as f64 / total * 60.0;
    let limit_score = ((b.limit_up_count - b.limit_down_count) as f64).clamp(-20.0, 20.0);
    let flow_score = (b.total_main_net / 1.0e10).clamp(-20.0, 20.0) * 1.0;
    (breadth_score + limit_score + flow_score).clamp(-100.0, 100.0)
}

/// 资金曲线增量：从分钟资金流中提取累计序列
pub fn normalize_flow_series(points: &[crate::models::FlowPoint]) -> Vec<crate::models::FlowPoint> {
    points.to_vec()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mk(code: &str, name: &str, chg: f64, amount: f64, flow: f64) -> StockTick {
        StockTick {
            code: code.into(),
            name: name.into(),
            price: 10.0,
            change_pct: chg,
            amount,
            main_net_inflow: flow,
            ..Default::default()
        }
    }

    #[test]
    fn limit_thresholds_by_board() {
        assert_eq!(limit_pct("600519", "贵州茅台"), 10.0);
        assert_eq!(limit_pct("300750", "宁德时代"), 20.0);
        assert_eq!(limit_pct("688185", "康希诺"), 20.0);
        assert_eq!(limit_pct("830799", "艾融软件"), 30.0);
        assert_eq!(limit_pct("600519", "ST测试"), 5.0);
    }

    #[test]
    fn detects_limit_up_and_down() {
        assert!(is_limit_up(&mk("300750", "宁德时代", 20.0, 0.0, 0.0)));
        assert!(!is_limit_up(&mk("600519", "贵州茅台", 9.0, 0.0, 0.0)));
        assert!(is_limit_up(&mk("600519", "贵州茅台", 10.0, 0.0, 0.0)));
        assert!(is_limit_down(&mk("600519", "贵州茅台", -10.0, 0.0, 0.0)));
    }

    #[test]
    fn breadth_counts_correctly() {
        let stocks = vec![
            mk("600519", "A", 5.0, 1.0e9, 1.0e8),
            mk("000001", "B", -3.0, 2.0e9, -2.0e8),
            mk("000002", "C", 0.0, 1.0e9, 0.0),
            mk("300750", "D", 20.0, 3.0e9, 5.0e8),
        ];
        let b = breadth(&stocks);
        assert_eq!(b.up_count, 2);
        assert_eq!(b.down_count, 1);
        assert_eq!(b.flat_count, 1);
        assert_eq!(b.limit_up_count, 1);
        assert!((b.total_amount - 7.0e9).abs() < 1.0);
        assert!((b.total_main_net - 4.0e8).abs() < 1.0);
        assert!((b.up_ratio - 50.0).abs() < 1e-6);
    }

    #[test]
    fn volume_ratio_prefers_provided_value() {
        assert!((volume_ratio(3.5, 100.0, 5, 1000.0, 240.0) - 3.5).abs() < 1e-9);
    }

    #[test]
    fn volume_ratio_estimates_when_missing() {
        // 当前 5 分钟成交 500 手 -> 每分钟 100；基准 2400/240 = 10 -> 量比 10
        let r = volume_ratio(0.0, 500.0, 5, 2400.0, 240.0);
        assert!((r - 10.0).abs() < 1e-6);
        assert_eq!(volume_ratio(0.0, 500.0, 0, 2400.0, 240.0), 0.0);
        assert_eq!(volume_ratio(0.0, 500.0, 5, 0.0, 240.0), 0.0);
    }

    #[test]
    fn flow_intensity_scales() {
        let t = mk("600519", "A", 1.0, 1.0e9, 1.0e8);
        assert!((flow_intensity(&t) - 100.0).abs() < 1e-6);
        let zero = mk("600519", "A", 1.0, 0.0, 1.0e8);
        assert_eq!(flow_intensity(&zero), 0.0);
    }

    #[test]
    fn sentiment_is_bounded() {
        let bull: Vec<StockTick> = (0..50)
            .map(|_| mk("600519", "X", 9.0, 1.0e9, 1.0e9))
            .collect();
        let bear: Vec<StockTick> = (0..50)
            .map(|_| mk("600519", "X", -9.0, 1.0e9, -1.0e9))
            .collect();
        assert!(sentiment_score(&bull) > 0.0);
        assert!(sentiment_score(&bear) < 0.0);
        assert!(sentiment_score(&bull) <= 100.0);
        assert!(sentiment_score(&bear) >= -100.0);
    }
}
