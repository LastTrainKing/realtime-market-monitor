//! 数据源层（第 4.3 / 8.2 / 8.3 章）。
//!
//! 所有外部数据只能经由此层进入系统，前端永不接触数据源。
//! 路由策略：东方财富（主） → 腾讯财经（备） → 新浪财经（备） → 缓存降级。

pub mod eastmoney;
pub mod sina;
pub mod tencent;

use serde_json::Value;

use crate::models::{DataStatus, FlowPoint, IndexTick, SectorTick, StockTick};

pub use eastmoney::EastmoneySource;
pub use sina::SinaSource;
pub use tencent::TencentSource;

/// 稳健的数值解析：兼容数字、字符串、"-"、null
pub fn to_f64(v: &Value) -> f64 {
    match v {
        Value::Number(n) => n.as_f64().unwrap_or(0.0),
        Value::String(s) => {
            let t = s.trim();
            if t.is_empty() || t == "-" || t == "--" {
                0.0
            } else {
                t.parse::<f64>().unwrap_or(0.0)
            }
        }
        Value::Bool(b) => {
            if *b {
                1.0
            } else {
                0.0
            }
        }
        _ => 0.0,
    }
}

pub fn to_opt_f64(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => {
            let t = s.trim();
            if t.is_empty() || t == "-" || t == "--" {
                None
            } else {
                t.parse::<f64>().ok()
            }
        }
        _ => None,
    }
}

pub fn to_i64(v: &Value) -> i64 {
    to_f64(v).round() as i64
}

pub fn to_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.trim().to_string(),
        Value::Number(n) => n.to_string(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// 按 Eastmoney 的 `f62` 风格字段名取数
pub fn field_f64(obj: &Value, key: &str) -> f64 {
    obj.get(key).map(to_f64).unwrap_or(0.0)
}

pub fn field_str(obj: &Value, key: &str) -> String {
    obj.get(key).map(to_str).unwrap_or_default()
}

/// 根据 6 位代码推断市场：1=沪市, 0=深市, 2=北交所
pub fn market_of(code: &str) -> u8 {
    let c = code.trim();
    // 北交所 920xxx 与沪 B 900xxx 都以 9 开头，必须分开判断，
    // 否则 920xxx 会被误判为沪市，生成错误的 secid（1.920000 而非 0.920000）。
    if c.starts_with("920") {
        return 2;
    }
    let first = c.chars().next().unwrap_or('0');
    match first {
        '6' | '5' | '9' => 1,
        '4' | '8' => 2,
        _ => 0,
    }
}

pub fn secid(code: &str, market: u8) -> String {
    let m = match market {
        2 => 0, // 北交所在东方财富口径下与深市同为 0
        other => other,
    };
    format!("{m}.{code}")
}

/// 腾讯/新浪前缀代码
pub fn prefixed_code(code: &str, market: u8) -> String {
    match market {
        1 => format!("sh{code}"),
        2 => format!("bj{code}"),
        _ => format!("sz{code}"),
    }
}

/// 从 `sh600519` / `sz000001` / `600519` 解析 (代码, 市场)
pub fn parse_prefixed(raw: &str) -> (String, u8) {
    let s = raw.trim().to_ascii_lowercase();
    if let Some(rest) = s.strip_prefix("sh") {
        (rest.to_string(), 1)
    } else if let Some(rest) = s.strip_prefix("sz") {
        (rest.to_string(), 0)
    } else if let Some(rest) = s.strip_prefix("bj") {
        (rest.to_string(), 2)
    } else {
        let code = s.trim_start_matches(|c: char| !c.is_ascii_digit()).to_string();
        let m = market_of(&code);
        (code, m)
    }
}

/// 数据源统一返回
#[derive(Debug, Clone)]
pub struct FetchResult<T> {
    pub data: T,
    pub status: DataStatus,
    pub source: crate::models::DataSource,
    pub message: String,
}

impl<T> FetchResult<T> {
    pub fn ok(data: T, source: crate::models::DataSource) -> Self {
        Self {
            data,
            status: DataStatus::Realtime,
            source,
            message: String::new(),
        }
    }

    pub fn partial(data: T, source: crate::models::DataSource, msg: impl Into<String>) -> Self {
        Self {
            data,
            status: DataStatus::Partial,
            source,
            message: msg.into(),
        }
    }
}

/// 数据源健康记录
#[derive(Debug, Clone, Default)]
pub struct SourceHealth {
    pub eastmoney_ok: u64,
    pub eastmoney_fail: u64,
    pub tencent_ok: u64,
    pub tencent_fail: u64,
    pub sina_ok: u64,
    pub sina_fail: u64,
}

impl SourceHealth {
    pub fn note(&mut self, source: &str, ok: bool) {
        match (source, ok) {
            ("eastmoney", true) => self.eastmoney_ok += 1,
            ("eastmoney", false) => self.eastmoney_fail += 1,
            ("tencent", true) => self.tencent_ok += 1,
            ("tencent", false) => self.tencent_fail += 1,
            ("sina", true) => self.sina_ok += 1,
            ("sina", false) => self.sina_fail += 1,
            _ => {}
        }
    }
}

/// 空值判定：避免把“全 0”当作有效数据
pub fn is_meaningful(ticks: &[StockTick]) -> bool {
    !ticks.is_empty() && ticks.iter().any(|t| t.price > 0.0)
}

pub fn is_meaningful_sectors(sectors: &[SectorTick]) -> bool {
    !sectors.is_empty() && sectors.iter().any(|s| s.change_pct != 0.0 || s.amount > 0.0)
}

pub fn is_meaningful_indices(indices: &[IndexTick]) -> bool {
    !indices.is_empty() && indices.iter().any(|i| i.price > 0.0)
}

pub fn is_meaningful_flow(points: &[FlowPoint]) -> bool {
    !points.is_empty()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_numbers_strings_and_placeholders() {
        assert_eq!(to_f64(&json!(12.5)), 12.5);
        assert_eq!(to_f64(&json!("12.5")), 12.5);
        assert_eq!(to_f64(&json!("-")), 0.0);
        assert_eq!(to_f64(&json!(null)), 0.0);
        assert_eq!(to_f64(&json!("")), 0.0);
        assert_eq!(to_opt_f64(&json!("-")), None);
        assert_eq!(to_opt_f64(&json!("3.3")), Some(3.3));
    }

    #[test]
    fn market_inference() {
        assert_eq!(market_of("600519"), 1);
        assert_eq!(market_of("000001"), 0);
        assert_eq!(market_of("300750"), 0);
        assert_eq!(market_of("688185"), 1);
        assert_eq!(market_of("430047"), 2);
        assert_eq!(market_of("830799"), 2);
        // 回归：北交所 920xxx 与沪 B 900xxx 都以 9 开头，必须区分
        assert_eq!(market_of("920000"), 2, "920xxx 是北交所，不是沪市");
        assert_eq!(market_of("900901"), 1, "900xxx 是沪 B");
        assert_eq!(secid("920000", market_of("920000")), "0.920000");
        assert_eq!(prefixed_code("920000", market_of("920000")), "bj920000");
    }

    #[test]
    fn secid_mapping() {
        assert_eq!(secid("600519", 1), "1.600519");
        assert_eq!(secid("000001", 0), "0.000001");
        assert_eq!(secid("430047", 2), "0.430047");
    }

    #[test]
    fn prefixed_roundtrip() {
        assert_eq!(prefixed_code("600519", 1), "sh600519");
        assert_eq!(prefixed_code("000001", 0), "sz000001");
        assert_eq!(parse_prefixed("sh600519"), ("600519".into(), 1));
        assert_eq!(parse_prefixed("sz000001"), ("000001".into(), 0));
        assert_eq!(parse_prefixed("600519"), ("600519".into(), 1));
    }

    #[test]
    fn meaningfulness_checks() {
        let mut t = StockTick::default();
        assert!(!is_meaningful(&[t.clone()]), "全 0 不应视为有效数据");
        t.price = 10.0;
        assert!(is_meaningful(&[t]));
        assert!(!is_meaningful(&[]));
    }
}
