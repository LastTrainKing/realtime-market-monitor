//! 统一时间上下文（第 4.8 / 4.9 章）。
//!
//! # 为什么必须严格分离时间概念
//!
//! 早期实现把"程序当前时间"直接当作行情的 `updated_at`，
//! 结果在休市日（2026-10-01）打开软件时，页面显示：
//!
//! ```text
//!   实时行情 · 最后更新 17:11
//! ```
//!
//! 而实际展示的是 **2026-09-30 的收盘数据**。这是"实时标签欺骗静态数据"，
//! 属于数据诚信问题，必须从数据模型层面根除。
//!
//! 本模块把五个时间概念彻底拆开，任何模块都不得再自行比较时间：
//!
//! | 字段 | 含义 | 示例 |
//! | --- | --- | --- |
//! | `system_time` | 程序当前时间 | 2026-10-01 17:12:34 |
//! | `server_time` | 数据源服务器时间（不可得时等于 system_time 并标注） | 2026-10-01 17:12:34 |
//! | `trade_date` | 页面正在分析的**数据交易日** | 2026-09-30 |
//! | `data_timestamp` | 该交易日的**行情最后有效时间** | 2026-09-30 15:00 |
//! | `data_fetch_time` | 本次数据**抓取**时间 | 2026-10-01 17:11:58 |

use serde::{Deserialize, Serialize};

use crate::clock::MarketStatus;
use crate::state::AppState;

/// 数据模式：决定 UI 上允许出现什么标签
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum DataMode {
    /// 盘中实时行情（只有交易时段且数据源正常时才允许）
    #[default]
    Realtime,
    /// 延迟行情（备用数据源）
    Delayed,
    /// 历史收盘数据（休市日 / 收盘后展示的最近交易日定稿数据）
    HistoricalClose,
    /// 静态历史数据（从本地历史库读取的过往交易日）
    Static,
    /// 数据不可用
    Unavailable,
}

impl DataMode {
    pub fn label(&self) -> &'static str {
        match self {
            DataMode::Realtime => "实时行情",
            DataMode::Delayed => "延迟行情",
            DataMode::HistoricalClose => "历史收盘",
            DataMode::Static => "静态数据",
            DataMode::Unavailable => "数据不可用",
        }
    }

    /// 是否允许在 UI 上使用"实时"字样
    pub fn allows_realtime_label(&self) -> bool {
        matches!(self, DataMode::Realtime)
    }
}

/// 数据源连接状态（与"是否实时"完全独立）
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum SourceLink {
    #[default]
    Connected,
    Degraded,
    Disconnected,
}

impl SourceLink {
    pub fn label(&self) -> &'static str {
        match self {
            SourceLink::Connected => "数据源已连接",
            SourceLink::Degraded => "数据源降级中",
            SourceLink::Disconnected => "数据源断开",
        }
    }
}

/// 统一时间上下文
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct MarketTimeContext {
    /// 程序当前时间（仅用于展示"现在几点"）
    pub system_time: String,
    /// 数据源服务器时间（不可得时等于 system_time，并置 server_time_estimated）
    pub server_time: String,
    pub server_time_estimated: bool,
    /// 页面正在分析的数据交易日
    pub trade_date: String,
    /// 行情最后有效时间（该交易日的最后一个有效分钟）
    pub data_timestamp: String,
    /// 本次数据抓取时间（≠ 行情时间）
    pub data_fetch_time: String,
    /// 市场状态
    pub market_status: String,
    pub market_status_label: String,
    /// 数据模式
    pub data_mode: DataMode,
    pub data_mode_label: String,
    /// 是否为定稿数据（收盘后不再变化）
    pub is_final: bool,
    /// 数据源连接状态
    pub source_link: SourceLink,
    pub source_link_label: String,
    /// 面向用户的一句话说明
    pub note: String,
}

impl MarketTimeContext {
    /// 交易时段内的"当前行情时间"：取已过分钟数的最后一个整分钟
    pub fn current_minute(&self) -> String {
        self.data_timestamp
            .rsplit(' ')
            .next()
            .unwrap_or("")
            .to_string()
    }

    pub fn trade_date_only(&self) -> &str {
        self.data_timestamp.split(' ').next().unwrap_or("")
    }
}

/// 解析当前统一时间上下文。
///
/// 这是全系统**唯一**允许决定"用哪个交易日的数据"的地方。
pub fn resolve(state: &AppState) -> MarketTimeContext {
    let now = crate::clock::now_cn();
    let clock = state.clock.read().clone();
    let status = clock.status();
    let system_time = now.format("%Y-%m-%d %H:%M:%S").to_string();

    let link = if state.is_degraded() {
        SourceLink::Degraded
    } else {
        SourceLink::Connected
    };

    // 交易时段内：数据日期 = 今天，行情时间 = 当前整分钟
    if status.is_live() && clock.is_trading_day(now.date_naive()) {
        let data_timestamp = format!("{} {}", now.format("%Y-%m-%d"), now.format("%H:%M"));
        return MarketTimeContext {
            system_time: system_time.clone(),
            server_time: system_time.clone(),
            server_time_estimated: true,
            trade_date: now.format("%Y-%m-%d").to_string(),
            data_timestamp,
            data_fetch_time: system_time,
            market_status: format!("{status:?}"),
            market_status_label: status.label().to_string(),
            data_mode: if state.is_degraded() {
                DataMode::Delayed
            } else {
                DataMode::Realtime
            },
            data_mode_label: if state.is_degraded() {
                DataMode::Delayed.label()
            } else {
                DataMode::Realtime.label()
            }
            .to_string(),
            is_final: false,
            source_link: link,
            source_link_label: link.label().to_string(),
            note: "盘中实时数据".to_string(),
        };
    }

    // 非交易时段 / 休市：使用最近一个已完成交易的交易日收盘数据
    let last = clock.last_closed_trading_day();
    let trade_date = last.format("%Y-%m-%d").to_string();
    let data_timestamp = format!("{trade_date} 15:00");

    let (mode, note) = if status == MarketStatus::NonTradingDay {
        (
            DataMode::HistoricalClose,
            format!(
                "今日休市，展示最近交易日 {} 的收盘数据（非实时行情）",
                trade_date
            ),
        )
    } else if status == MarketStatus::Closed {
        (
            DataMode::HistoricalClose,
            format!("已收盘，展示 {} 的收盘数据", trade_date),
        )
    } else {
        (
            DataMode::HistoricalClose,
            format!("非交易时段，展示最近交易日 {} 的收盘数据", trade_date),
        )
    };

    MarketTimeContext {
        system_time: system_time.clone(),
        server_time: system_time.clone(),
        server_time_estimated: true,
        trade_date,
        data_timestamp,
        data_fetch_time: system_time,
        market_status: format!("{status:?}"),
        market_status_label: status.label().to_string(),
        data_mode: mode,
        data_mode_label: mode.label().to_string(),
        is_final: true,
        source_link: link,
        source_link_label: link.label().to_string(),
        note,
    }
}

/// 校验：静态/历史数据绝不允许被标成"实时"
pub fn assert_label_consistent(ctx: &MarketTimeContext) -> Result<(), String> {
    if !ctx.data_mode.allows_realtime_label() && ctx.data_mode_label.contains("实时") {
        return Err(format!(
            "数据模式 {} 不允许使用「实时」标签",
            ctx.data_mode_label
        ));
    }
    Ok(())
}

/// 交易日解析：给定系统时间，返回应当用于分析的交易日
pub fn resolve_trade_date(state: &AppState) -> String {
    resolve(state).trade_date
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn realtime_label_only_for_realtime_mode() {
        assert!(DataMode::Realtime.allows_realtime_label());
        assert!(!DataMode::HistoricalClose.allows_realtime_label());
        assert!(!DataMode::Static.allows_realtime_label());
        assert!(!DataMode::Delayed.allows_realtime_label());
        assert!(!DataMode::Unavailable.allows_realtime_label());
    }

    #[test]
    fn historical_close_label_is_explicit() {
        assert_eq!(DataMode::HistoricalClose.label(), "历史收盘");
        assert_eq!(DataMode::Static.label(), "静态数据");
        assert_eq!(SourceLink::Connected.label(), "数据源已连接");
    }

    #[test]
    fn label_consistency_guard_rejects_fake_realtime() {
        let ctx = MarketTimeContext {
            data_mode: DataMode::HistoricalClose,
            data_mode_label: "实时行情".into(),
            ..Default::default()
        };
        assert!(
            assert_label_consistent(&ctx).is_err(),
            "历史收盘数据不得使用实时标签"
        );

        let ok = MarketTimeContext {
            data_mode: DataMode::HistoricalClose,
            data_mode_label: "历史收盘".into(),
            ..Default::default()
        };
        assert!(assert_label_consistent(&ok).is_ok());
    }

    #[test]
    fn five_time_concepts_are_distinct_fields() {
        let ctx = MarketTimeContext {
            system_time: "2026-10-01 17:12:34".into(),
            server_time: "2026-10-01 17:12:34".into(),
            trade_date: "2026-09-30".into(),
            data_timestamp: "2026-09-30 15:00".into(),
            data_fetch_time: "2026-10-01 17:11:58".into(),
            ..Default::default()
        };
        // 数据日期与系统日期必须可以不同（休市日场景）
        assert_ne!(ctx.trade_date, ctx.system_time[..10]);
        assert_eq!(ctx.trade_date_only(), "2026-09-30");
        assert_eq!(ctx.current_minute(), "15:00");
    }
}
