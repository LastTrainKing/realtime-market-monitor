//! 历史数据状态清单（Manifest）。
//!
//! # 为什么必须有清单
//!
//! 没有清单时，"这一天的历史数据到底全不全"只能靠**扫描整个目录**才知道，
//! 5571 只股票 × 90 天 = 50 万个文件，每次启动扫一遍是不可接受的。
//!
//! 清单把结果固化成表，启动时一次查询即可判断：
//!   * 哪些交易日已完整、哪些缺失
//!   * 某个交易日缺多少只股票（用于增量补齐）
//!   * 每只股票的 row_count / first_dt / last_dt / 完整性
//!
//! 清单只记录**实际观测到的数据**，不做任何推断或填充。

use serde::{Deserialize, Serialize};

use crate::net::NetError;

/// 历史数据粒度（分钟）
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Granularity {
    Min1,
    Min5,
    Min15,
    Min30,
    Min60,
}

impl Granularity {
    pub fn minutes(&self) -> u32 {
        match self {
            Granularity::Min1 => 1,
            Granularity::Min5 => 5,
            Granularity::Min15 => 15,
            Granularity::Min30 => 30,
            Granularity::Min60 => 60,
        }
    }

    pub fn label(&self) -> &'static str {
        match self {
            Granularity::Min1 => "1分钟",
            Granularity::Min5 => "5分钟",
            Granularity::Min15 => "15分钟",
            Granularity::Min30 => "30分钟",
            Granularity::Min60 => "60分钟",
        }
    }

    /// 数据源单次请求（datalen=1023）在该粒度下能覆盖的交易日数。
    ///
    /// 实测值，非估算：新浪 `getKLineData` 的 datalen 上限为 1023。
    pub fn source_max_days(&self) -> usize {
        // 一个交易日约 240 分钟
        let per_day = (240 / self.minutes().max(1)) as usize;
        1023 / per_day.max(1)
    }

    pub fn from_minutes(m: u32) -> Option<Self> {
        match m {
            1 => Some(Granularity::Min1),
            5 => Some(Granularity::Min5),
            15 => Some(Granularity::Min15),
            30 => Some(Granularity::Min30),
            60 => Some(Granularity::Min60),
            _ => None,
        }
    }
}

/// 某交易日 + 某粒度的清单行
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ManifestRow {
    pub trade_date: String,
    pub granularity: u32,
    /// 期望覆盖的股票数（来自全市场股票宇宙）
    pub expected_symbols: i32,
    /// 实际落盘的股票数
    pub actual_symbols: i32,
    /// 缺失股票数
    pub missing_symbols: i32,
    /// 总分钟行数
    pub total_rows: i64,
    /// 是否完整（actual == expected 且无缺失）
    pub complete: bool,
    pub created_at: String,
    pub updated_at: String,
    /// 面向用户的说明
    pub note: String,
}

/// 单只股票在某个交易日的落盘状态
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SymbolStatus {
    pub trade_date: String,
    pub granularity: u32,
    pub code: String,
    pub row_count: i32,
    pub first_dt: String,
    pub last_dt: String,
    pub complete: bool,
}

/// 历史数据校验结果
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ValidationReport {
    pub code: String,
    pub trade_date: String,
    pub row_count: usize,
    pub first_dt: String,
    pub last_dt: String,
    pub duplicate_dt: usize,
    /// 与前一分钟的时间间隔超过阈值（分钟）的断档数
    pub gaps: usize,
    pub invalid_price: usize,
    pub invalid_volume: usize,
    pub invalid_amount: usize,
    /// 时间轴是否全部落在交易时段内（09:15-11:30 / 13:00-15:00）
    pub axis_in_session: bool,
    pub ok: bool,
    pub problems: Vec<String>,
}

impl ValidationReport {
    pub fn summary(&self) -> String {
        if self.ok {
            format!(
                "{} {} 共 {} 行，{} → {}",
                self.code, self.trade_date, self.row_count, self.first_dt, self.last_dt
            )
        } else {
            format!(
                "{} {} 校验失败：{}",
                self.code,
                self.trade_date,
                self.problems.join("；")
            )
        }
    }
}

/// 校验一组分钟 bar。
///
/// 校验项严格按"数据内容"判断，**不看 HTTP 状态码、不看文件是否存在**：
/// row_count / first_dt / last_dt / 重复分钟 / 断档 / 非法价格 / 非法量额 / 时间轴越界。
///
/// 注意：**不写死"必须正好 240 点"**。实测数据源在 14:58、14:59 可能不返回
/// （深市收盘集合竞价口径差异），因此只要求"接近完整交易日"且首尾时间正确。
pub fn validate_minutes(
    code: &str,
    trade_date: &str,
    bars: &[crate::history::MinuteBar],
    granularity: Granularity,
) -> ValidationReport {
    let mut r = ValidationReport {
        code: code.to_string(),
        trade_date: trade_date.to_string(),
        row_count: bars.len(),
        ..Default::default()
    };

    if bars.is_empty() {
        r.problems.push("无数据".into());
        return r;
    }

    let mut sorted: Vec<&crate::history::MinuteBar> = bars
        .iter()
        .filter(|b| b.trade_date == trade_date)
        .collect();
    sorted.sort_by(|a, b| a.minute.cmp(&b.minute));

    if sorted.is_empty() {
        r.problems
            .push(format!("该交易日内无分钟数据（数据属于其他日期）"));
        return r;
    }

    r.row_count = sorted.len();
    r.first_dt = sorted.first().map(|b| b.minute.clone()).unwrap_or_default();
    r.last_dt = sorted.last().map(|b| b.minute.clone()).unwrap_or_default();

    // 时间轴必须落在交易时段内
    let in_session = |t: &str| {
        (t >= "09:15" && t <= "11:30") || (t >= "13:00" && t <= "15:00")
    };
    let outside = sorted.iter().filter(|b| !in_session(&b.minute)).count();
    r.axis_in_session = outside == 0;
    if outside > 0 {
        r.problems
            .push(format!("{outside} 个时间点不在交易时段内（X 轴混入了系统时间）"));
    }

    // 重复分钟
    let mut seen = std::collections::HashSet::new();
    for b in &sorted {
        if !seen.insert(b.minute.as_str()) {
            r.duplicate_dt += 1;
        }
    }
    if r.duplicate_dt > 0 {
        r.problems.push(format!("{} 个重复分钟", r.duplicate_dt));
    }

    // 断档：相邻分钟间隔超过 2 倍粒度（午休 11:30→13:01 属正常，单独放行）
    let step = granularity.minutes() as i64;
    for w in sorted.windows(2) {
        let (a, b) = (w[0].minute.as_str(), w[1].minute.as_str());
        if a == "11:30" {
            continue; // 午休断点，正常
        }
        let (Some(am), Some(bm)) = (hhmm_to_min(a), hhmm_to_min(b)) else {
            continue;
        };
        if bm - am > step * 2 {
            r.gaps += 1;
        }
    }
    if r.gaps > 0 {
        r.problems.push(format!("{} 处时间断档", r.gaps));
    }

    // 非法数值
    for b in &sorted {
        if b.close <= 0.0 || b.open <= 0.0 || b.high <= 0.0 || b.low <= 0.0 {
            r.invalid_price += 1;
        }
        if b.high < b.low {
            r.invalid_price += 1;
        }
        if b.volume < 0.0 {
            r.invalid_volume += 1;
        }
        if b.amount < 0.0 {
            r.invalid_amount += 1;
        }
    }
    if r.invalid_price > 0 {
        r.problems.push(format!("{} 行价格非法", r.invalid_price));
    }
    if r.invalid_volume > 0 {
        r.problems.push(format!("{} 行成交量为负", r.invalid_volume));
    }
    if r.invalid_amount > 0 {
        r.problems.push(format!("{} 行成交额为负", r.invalid_amount));
    }

    // 首尾时间：必须从开盘后第一根开始，收盘那根必须存在
    if r.first_dt > "09:35".to_string() {
        r.problems
            .push(format!("起始时间 {} 过晚（数据不完整）", r.first_dt));
    }
    if r.last_dt < "14:55".to_string() {
        r.problems
            .push(format!("结束时间 {} 过早（数据不完整）", r.last_dt));
    }

    // 点数下限：按粒度的 70% 作为"接近完整"的判据（不写死 240）
    let expected_min = (240 / granularity.minutes().max(1)) as usize;
    if r.row_count < expected_min * 7 / 10 {
        r.problems.push(format!(
            "点数 {} 明显少于该粒度的完整交易日（约 {}）",
            r.row_count, expected_min
        ));
    }

    r.ok = r.problems.is_empty();
    r
}

fn hhmm_to_min(t: &str) -> Option<i64> {
    let (h, m) = crate::clock::parse_hhmm(t)?;
    Some(h as i64 * 60 + m as i64)
}

/// 清单写入错误
pub fn manifest_error(msg: impl Into<String>) -> NetError {
    NetError::ServerError(format!("历史清单：{}", msg.into()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bar(minute: &str, close: f64) -> crate::history::MinuteBar {
        crate::history::MinuteBar {
            code: "600519".into(),
            trade_date: "2026-09-30".into(),
            minute: minute.into(),
            close,
            open: close,
            high: close + 1.0,
            low: close - 1.0,
            volume: 100.0,
            amount: 1000.0,
            ..Default::default()
        }
    }

    fn full_day() -> Vec<crate::history::MinuteBar> {
        let mut v = Vec::new();
        for i in 0..120 {
            let t = 9 * 60 + 31 + i;
            v.push(bar(&format!("{:02}:{:02}", t / 60, t % 60), 100.0));
        }
        for i in 0..119 {
            let t = 13 * 60 + 1 + i;
            v.push(bar(&format!("{:02}:{:02}", t / 60, t % 60), 100.0));
        }
        v.push(bar("15:00", 100.0));
        v
    }

    #[test]
    fn granularity_capability_matches_measured_limits() {
        // 实测：datalen 上限 1023，1 分钟约 5 天、5 分钟约 22 天、
        // 15 分钟约 64 天、30 分钟约 128 天
        assert_eq!(Granularity::Min1.source_max_days(), 4);
        assert_eq!(Granularity::Min5.source_max_days(), 21);
        assert_eq!(Granularity::Min15.source_max_days(), 63);
        assert_eq!(Granularity::Min30.source_max_days(), 127);
        // 关键结论：1 分钟拿不到 90 天，30 分钟可以
        assert!(Granularity::Min1.source_max_days() < 90);
        assert!(Granularity::Min30.source_max_days() > 90);
    }

    #[test]
    fn validates_complete_trading_day() {
        let bars = full_day();
        let r = validate_minutes("600519", "2026-09-30", &bars, Granularity::Min1);
        assert!(r.ok, "完整交易日应通过校验：{:?}", r.problems);
        assert_eq!(r.first_dt, "09:31");
        assert_eq!(r.last_dt, "15:00");
        assert_eq!(r.duplicate_dt, 0);
        assert!(r.axis_in_session);
    }

    #[test]
    fn rejects_system_time_on_axis() {
        // 回归：X 轴出现 17:10 这类系统时间必须判为失败
        let mut bars = full_day();
        bars.push(bar("17:10", 100.0));
        bars.push(bar("17:11", 100.0));
        let r = validate_minutes("600519", "2026-09-30", &bars, Granularity::Min1);
        assert!(!r.ok);
        assert!(!r.axis_in_session);
        assert!(r.problems.iter().any(|p| p.contains("不在交易时段")));
    }

    #[test]
    fn rejects_duplicate_minutes() {
        let mut bars = full_day();
        bars.push(bar("10:00", 100.0));
        let r = validate_minutes("600519", "2026-09-30", &bars, Granularity::Min1);
        assert!(!r.ok);
        assert_eq!(r.duplicate_dt, 1);
    }

    #[test]
    fn rejects_invalid_prices() {
        let mut bars = full_day();
        bars.push(bar("10:30", 0.0));
        let r = validate_minutes("600519", "2026-09-30", &bars, Granularity::Min1);
        assert!(!r.ok);
        assert!(r.invalid_price > 0);
    }

    #[test]
    fn rejects_short_series() {
        let bars = vec![bar("09:31", 100.0), bar("09:32", 100.0)];
        let r = validate_minutes("600519", "2026-09-30", &bars, Granularity::Min1);
        assert!(!r.ok, "2 个点绝不能判为完整交易日");
        assert!(r.problems.iter().any(|p| p.contains("点数")));
    }

    #[test]
    fn rejects_empty() {
        let r = validate_minutes("600519", "2026-09-30", &[], Granularity::Min1);
        assert!(!r.ok);
        assert_eq!(r.row_count, 0);
    }

    #[test]
    fn lunch_break_is_not_a_gap() {
        // 11:30 → 13:01 是午休，不是断档
        let bars = vec![
            bar("11:29", 100.0),
            bar("11:30", 100.0),
            bar("13:01", 100.0),
        ];
        let r = validate_minutes("600519", "2026-09-30", &bars, Granularity::Min1);
        assert_eq!(r.gaps, 0, "午休不应被计为断档");
    }

    #[test]
    fn detects_real_gap() {
        let bars = vec![bar("09:31", 100.0), bar("10:30", 100.0)];
        let r = validate_minutes("600519", "2026-09-30", &bars, Granularity::Min1);
        assert!(r.gaps > 0, "09:31 → 10:30 是真实断档");
    }

    #[test]
    fn granularity_roundtrip() {
        for m in [1u32, 5, 15, 30, 60] {
            let g = Granularity::from_minutes(m).unwrap();
            assert_eq!(g.minutes(), m);
        }
        assert!(Granularity::from_minutes(7).is_none());
    }
}
