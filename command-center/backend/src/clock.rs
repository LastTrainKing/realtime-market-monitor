//! 统一时间服务 TradingClock（第 4.8 / 4.9 章）。
//!
//! 全系统只有这一处判断交易时间，禁止任何模块自行比较时间。

use chrono::{DateTime, Datelike, Duration, FixedOffset, NaiveDate, NaiveDateTime, NaiveTime, TimeZone, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;

/// 交易市场状态
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum MarketStatus {
    /// 非交易日（周末 / 节假日）
    NonTradingDay,
    /// 开盘前（00:00 - 09:15）
    PreMarket,
    /// 集合竞价（09:15 - 09:25）
    CallAuction,
    /// 开盘（09:30 整点附近）
    Open,
    /// 上午连续交易（09:30 - 11:30）
    MorningSession,
    /// 午间休市（11:30 - 13:00）
    LunchBreak,
    /// 下午连续交易（13:00 - 15:00）
    AfternoonSession,
    /// 已收盘（15:00 之后）
    Closed,
}

impl MarketStatus {
    pub fn is_live(&self) -> bool {
        matches!(
            self,
            MarketStatus::CallAuction
                | MarketStatus::Open
                | MarketStatus::MorningSession
                | MarketStatus::AfternoonSession
        )
    }

    pub fn label(&self) -> &'static str {
        match self {
            MarketStatus::NonTradingDay => "休市",
            MarketStatus::PreMarket => "等待开盘",
            MarketStatus::CallAuction => "集合竞价",
            MarketStatus::Open => "开盘",
            MarketStatus::MorningSession => "上午交易",
            MarketStatus::LunchBreak => "午间休市",
            MarketStatus::AfternoonSession => "下午交易",
            MarketStatus::Closed => "已收盘",
        }
    }
}

pub const CN_OFFSET_SECONDS: i32 = 8 * 3600;

pub fn cn_offset() -> FixedOffset {
    FixedOffset::east_opt(CN_OFFSET_SECONDS).expect("valid offset")
}

/// 当前北京时间
pub fn now_cn() -> DateTime<FixedOffset> {
    Utc::now().with_timezone(&cn_offset())
}

fn t(h: u32, m: u32) -> NaiveTime {
    NaiveTime::from_hms_opt(h, m, 0).expect("valid time")
}

/// 内置休市日（沪市公告口径，第 4.9 / 7.12 章）
///
/// 覆盖 2025 / 2026 两个完整年度，周末由 weekday 判断单独处理。
pub fn builtin_holidays() -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut push = |y: i32, m: u32, from: u32, to: u32| {
        for d in from..=to {
            if let Some(date) = NaiveDate::from_ymd_opt(y, m, d) {
                out.push(date.format("%Y-%m-%d").to_string());
            }
        }
    };

    // ---- 2025 ----
    push(2025, 1, 1, 1); // 元旦
    push(2025, 1, 28, 31); // 春节
    push(2025, 2, 1, 4); // 春节
    push(2025, 4, 4, 6); // 清明
    push(2025, 5, 1, 5); // 劳动节
    push(2025, 5, 31, 31); // 端午
    push(2025, 6, 1, 2); // 端午
    push(2025, 10, 1, 8); // 国庆 + 中秋

    // ---- 2026（沪深北交易所 2025-12-22 公告）----
    push(2026, 1, 1, 3); // 元旦
    push(2026, 2, 15, 23); // 春节
    push(2026, 4, 4, 6); // 清明
    push(2026, 5, 1, 5); // 劳动节
    push(2026, 6, 19, 21); // 端午
    push(2026, 9, 25, 27); // 中秋
    push(2026, 10, 1, 7); // 国庆

    out
}

#[derive(Debug, Clone)]
pub struct TradingClock {
    holidays: HashSet<NaiveDate>,
    /// 额外的工作日（调休补班，通常 A 股不补班，但保留接口）
    extra_trading_days: HashSet<NaiveDate>,
}

impl Default for TradingClock {
    fn default() -> Self {
        Self::new(Vec::new())
    }
}

impl TradingClock {
    pub fn new(extra_holidays: Vec<String>) -> Self {
        let mut holidays: HashSet<NaiveDate> = builtin_holidays()
            .into_iter()
            .filter_map(|s| parse_date(&s))
            .collect();
        for s in extra_holidays {
            if let Some(d) = parse_date(&s) {
                holidays.insert(d);
            }
        }
        Self {
            holidays,
            extra_trading_days: HashSet::new(),
        }
    }

    pub fn holiday_count(&self) -> usize {
        self.holidays.len()
    }

    /// 是否为交易日（排除周末与节假日）
    pub fn is_trading_day(&self, date: NaiveDate) -> bool {
        if self.extra_trading_days.contains(&date) {
            return true;
        }
        let wd = date.weekday().number_from_monday();
        if wd >= 6 {
            return false;
        }
        !self.holidays.contains(&date)
    }

    /// 给定时刻对应的“交易日”
    ///
    /// 规则：若当日为交易日且时间 >= 09:15，则为当日；
    /// 否则回溯到最近一个交易日（早于开盘时展示上一交易日数据，第 4.11 章）。
    pub fn trading_date_at(&self, now: DateTime<FixedOffset>) -> NaiveDate {
        let date = now.date_naive();
        if self.is_trading_day(date) && now.time() >= t(9, 15) {
            return date;
        }
        self.prev_trading_day(date)
    }

    pub fn prev_trading_day(&self, date: NaiveDate) -> NaiveDate {
        let mut d = date - Duration::days(1);
        for _ in 0..40 {
            if self.is_trading_day(d) {
                return d;
            }
            d -= Duration::days(1);
        }
        date
    }

    pub fn next_trading_day(&self, date: NaiveDate) -> NaiveDate {
        let mut d = date + Duration::days(1);
        for _ in 0..40 {
            if self.is_trading_day(d) {
                return d;
            }
            d += Duration::days(1);
        }
        date
    }

    /// 交易状态判定
    pub fn status_at(&self, now: DateTime<FixedOffset>) -> MarketStatus {
        let date = now.date_naive();
        if !self.is_trading_day(date) {
            return MarketStatus::NonTradingDay;
        }
        let time = now.time();
        if time < t(9, 15) {
            MarketStatus::PreMarket
        } else if time < t(9, 25) {
            MarketStatus::CallAuction
        } else if time < t(9, 30) {
            MarketStatus::PreMarket
        } else if time < t(9, 31) {
            MarketStatus::Open
        } else if time <= t(11, 30) {
            MarketStatus::MorningSession
        } else if time < t(13, 0) {
            MarketStatus::LunchBreak
        } else if time < t(15, 0) {
            MarketStatus::AfternoonSession
        } else {
            MarketStatus::Closed
        }
    }

    pub fn status(&self) -> MarketStatus {
        self.status_at(now_cn())
    }

    /// 是否处于盘中（含集合竞价）
    pub fn is_live(&self) -> bool {
        self.status().is_live()
    }

    /// 该交易日自开盘以来的“交易分钟数”（跳过午休），用于量比等指标
    pub fn trading_minutes_elapsed(&self, at: DateTime<FixedOffset>) -> i64 {
        let time = at.time();
        let open = t(9, 30);
        let morning_end = t(11, 30);
        let afternoon_start = t(13, 0);
        if time <= open {
            return 0;
        }
        if time <= morning_end {
            return (time - open).num_minutes();
        }
        if time < afternoon_start {
            return 120;
        }
        let pm = (time - afternoon_start).num_minutes();
        120 + pm.max(0)
    }

    /// 交易日 + HH:MM -> 带时区时间
    pub fn node_datetime(&self, date: NaiveDate, hhmm: &str) -> Option<DateTime<FixedOffset>> {
        let (h, m) = parse_hhmm(hhmm)?;
        let naive = NaiveDateTime::new(date, NaiveTime::from_hms_opt(h, m, 0)?);
        cn_offset().from_local_datetime(&naive).single()
    }

    /// 所有配置节点的触发时间
    pub fn node_datetimes(&self, date: NaiveDate, nodes: &[String]) -> Vec<DateTime<FixedOffset>> {
        nodes
            .iter()
            .filter_map(|n| self.node_datetime(date, n))
            .collect()
    }

    /// 交易时段起点（用于“开盘以来成交量”）
    pub fn session_open(&self, date: NaiveDate) -> DateTime<FixedOffset> {
        self.node_datetime(date, "09:30").unwrap_or_else(now_cn)
    }

    /// 最近的已收盘交易日（用于晚启动 / 复盘，第 4.11 章）
    pub fn last_closed_trading_day(&self) -> NaiveDate {
        let now = now_cn();
        let date = now.date_naive();
        if self.is_trading_day(date) && now.time() >= t(15, 0) {
            date
        } else {
            self.prev_trading_day(date)
        }
    }

    /// 距下一个交易日的毫秒数（用于非交易日的低频轮询）
    pub fn millis_until_next_session(&self) -> i64 {
        let now = now_cn();
        let status = self.status_at(now);
        if status.is_live() {
            return 0;
        }
        let today = now.date_naive();
        let target_date = if self.is_trading_day(today) && now.time() < t(9, 15) {
            today
        } else {
            self.next_trading_day(today)
        };
        let target = self.node_datetime(target_date, "09:15").unwrap_or(now);
        (target - now).num_milliseconds().max(0)
    }
}

pub fn parse_hhmm(s: &str) -> Option<(u32, u32)> {
    let mut parts = s.split(':');
    let h: u32 = parts.next()?.trim().parse().ok()?;
    let m: u32 = parts.next()?.trim().parse().ok()?;
    if h > 23 || m > 59 {
        return None;
    }
    Some((h, m))
}

pub fn parse_date(s: &str) -> Option<NaiveDate> {
    NaiveDate::parse_from_str(s.trim(), "%Y-%m-%d").ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(y: i32, m: u32, d: u32, hh: u32, mm: u32) -> DateTime<FixedOffset> {
        cn_offset()
            .from_local_datetime(&NaiveDateTime::new(
                NaiveDate::from_ymd_opt(y, m, d).unwrap(),
                NaiveTime::from_hms_opt(hh, mm, 0).unwrap(),
            ))
            .unwrap()
    }

    #[test]
    fn weekend_is_non_trading() {
        let clock = TradingClock::default();
        // 2026-10-03 是周六
        assert!(!clock.is_trading_day(NaiveDate::from_ymd_opt(2026, 10, 3).unwrap()));
        assert_eq!(
            clock.status_at(at(2026, 10, 3, 10, 0)),
            MarketStatus::NonTradingDay
        );
    }

    #[test]
    fn national_day_holiday_is_closed() {
        let clock = TradingClock::default();
        // 2026-10-01 ~ 10-07 国庆休市
        for d in 1..=7 {
            assert!(
                !clock.is_trading_day(NaiveDate::from_ymd_opt(2026, 10, d).unwrap()),
                "2026-10-0{d} 应为休市日"
            );
        }
        // 10-08 周四正常开市
        assert!(clock.is_trading_day(NaiveDate::from_ymd_opt(2026, 10, 8).unwrap()));
    }

    #[test]
    fn spring_festival_2026_is_closed() {
        let clock = TradingClock::default();
        for d in 15..=23 {
            assert!(!clock.is_trading_day(NaiveDate::from_ymd_opt(2026, 2, d).unwrap()));
        }
        assert!(clock.is_trading_day(NaiveDate::from_ymd_opt(2026, 2, 24).unwrap()));
    }

    #[test]
    fn session_boundaries() {
        let clock = TradingClock::default();
        let d = (2026, 9, 30); // 周三，交易日
        assert_eq!(
            clock.status_at(at(d.0, d.1, d.2, 9, 0)),
            MarketStatus::PreMarket
        );
        assert_eq!(
            clock.status_at(at(d.0, d.1, d.2, 9, 20)),
            MarketStatus::CallAuction
        );
        assert_eq!(clock.status_at(at(d.0, d.1, d.2, 9, 30)), MarketStatus::Open);
        assert_eq!(
            clock.status_at(at(d.0, d.1, d.2, 10, 30)),
            MarketStatus::MorningSession
        );
        assert_eq!(
            clock.status_at(at(d.0, d.1, d.2, 12, 0)),
            MarketStatus::LunchBreak
        );
        assert_eq!(
            clock.status_at(at(d.0, d.1, d.2, 14, 0)),
            MarketStatus::AfternoonSession
        );
        assert_eq!(
            clock.status_at(at(d.0, d.1, d.2, 15, 30)),
            MarketStatus::Closed
        );
    }

    #[test]
    fn trading_minutes_skip_lunch() {
        let clock = TradingClock::default();
        let d = (2026, 9, 30);
        assert_eq!(clock.trading_minutes_elapsed(at(d.0, d.1, d.2, 9, 35)), 5);
        assert_eq!(clock.trading_minutes_elapsed(at(d.0, d.1, d.2, 11, 30)), 120);
        assert_eq!(clock.trading_minutes_elapsed(at(d.0, d.1, d.2, 12, 30)), 120);
        assert_eq!(clock.trading_minutes_elapsed(at(d.0, d.1, d.2, 13, 30)), 150);
        assert_eq!(clock.trading_minutes_elapsed(at(d.0, d.1, d.2, 15, 0)), 240);
    }

    #[test]
    fn trading_date_falls_back_to_previous_day() {
        let clock = TradingClock::default();
        // 10-01 是休市日 -> 回溯到 09-30
        let d = clock.trading_date_at(at(2026, 10, 1, 10, 0));
        assert_eq!(d, NaiveDate::from_ymd_opt(2026, 9, 30).unwrap());
        // 交易日 09:00 尚未开盘 -> 回溯到上一交易日 09-29
        let d2 = clock.trading_date_at(at(2026, 9, 30, 9, 0));
        assert_eq!(d2, NaiveDate::from_ymd_opt(2026, 9, 29).unwrap());
        // 交易日 10:00 -> 当日
        let d3 = clock.trading_date_at(at(2026, 9, 30, 10, 0));
        assert_eq!(d3, NaiveDate::from_ymd_opt(2026, 9, 30).unwrap());
    }

    #[test]
    fn node_datetime_parses() {
        let clock = TradingClock::default();
        let d = NaiveDate::from_ymd_opt(2026, 9, 30).unwrap();
        let nd = clock.node_datetime(d, "09:35").unwrap();
        assert_eq!(nd.format("%H:%M").to_string(), "09:35");
        assert!(clock.node_datetime(d, "9:35").is_some());
        assert!(clock.node_datetime(d, "25:00").is_none());
    }
}
