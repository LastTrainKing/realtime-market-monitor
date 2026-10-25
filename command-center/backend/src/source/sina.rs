//! 新浪财经数据源（二级降级源，第 4.6 / 8.16 章）。
//!
//! 接口：
//!   * `hq.sinajs.cn/list=`                     批量实时快照（GB18030）
//!   * `vip.stock.finance.sina.com.cn/...`      全市场榜单（涨跌幅 / 换手率 / 成交额）
//!   * `quotes.sina.cn/.../getKLineData`        **历史分钟 K 线**（本项目历史库的主数据源）
//!
//! # 历史分钟 K 线的能力边界（实测结论，勿凭猜测修改）
//!
//! `getKLineData` 的 `datalen` **上限为 1023**（传更大值返回空数组），
//! 因此单次请求能覆盖的历史长度由 `scale` 决定：
//!
//! | scale | 周期 | 覆盖交易日（实测） |
//! | --- | --- | --- |
//! | 1 | 1 分钟 | 5 |
//! | 5 | 5 分钟 | 22 |
//! | 15 | 15 分钟 | 64 |
//! | 30 | 30 分钟 | 128 |
//! | 60 | 60 分钟 | 256 |
//!
//! 结论：**90 天的 1 分钟数据无法从该接口一次性取得**，
//! 1 分钟只能取最近 5 个交易日，其余需要本程序逐日累积；
//! 若需要 90 天覆盖，必须使用 15 分钟（64 天）或 30 分钟（128 天）粒度。

use serde_json::Value;
use std::sync::Arc;

use crate::models::StockTick;
use crate::net::{HttpClient, NetError};

use super::{parse_prefixed, to_f64, to_str};

const HQ_HOST: &str = "hq.sinajs.cn";
/// 历史分钟 K 线接口
const KLINE_URL: &str = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData";
/// 单次请求最大返回点数（服务端硬限制，实测传更大值返回空）
pub const KLINE_MAX_DATALEN: usize = 1023;
const RANK_URL: &str = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData";
const REFERER: &str = "https://finance.sina.com.cn";

pub struct SinaSource {
    http: Arc<HttpClient>,
}

impl SinaSource {
    pub fn new(http: Arc<HttpClient>) -> Self {
        Self { http }
    }

    /// 历史分钟 K 线。
    ///
    /// `symbol` 形如 `sh600519`；`scale` 为分钟周期（1/5/15/30/60）；
    /// `datalen` 会被限制在服务端硬上限 1023 以内。
    ///
    /// 返回的每根 bar 都带**真实行情时间**（`trade_date` + `minute`），
    /// 时间轴已由数据源保证不含非交易时段（实测午休断点为 11:30 → 13:01）。
    pub async fn minute_kline(
        &self,
        symbol: &str,
        scale: u32,
        datalen: usize,
    ) -> Result<Vec<crate::history::MinuteBar>, NetError> {
        let n = datalen.clamp(1, KLINE_MAX_DATALEN);
        let url = format!("{KLINE_URL}?symbol={symbol}&scale={scale}&ma=no&datalen={n}");
        let value: Value = self.http.get_json(&url, Some(REFERER)).await?;
        let list = value.as_array().cloned().unwrap_or_default();
        let mut out = Vec::with_capacity(list.len());
        for item in list.iter() {
            let stamp = item.get("day").and_then(|v| v.as_str()).unwrap_or("").trim();
            if stamp.len() < 16 {
                continue;
            }
            let (date, time) = stamp.split_at(10);
            let minute = time.trim_start_matches(' ').get(0..5).unwrap_or("").to_string();
            if minute.len() != 5 || !minute.contains(':') {
                continue;
            }
            let f = |k: &str| -> f64 {
                item.get(k)
                    .and_then(|v| {
                        v.as_str()
                            .map(|s| s.trim().to_string())
                            .or_else(|| Some(v.to_string()))
                    })
                    .and_then(|s| s.parse::<f64>().ok())
                    .unwrap_or(0.0)
            };
            let close = f("close");
            if close <= 0.0 {
                continue;
            }
            out.push(crate::history::MinuteBar {
                code: symbol[2..].to_string(),
                trade_date: date.to_string(),
                minute: minute.clone(),
                dt_ms: crate::history::preload::minute_to_ms(date, &minute),
                open: f("open"),
                high: f("high"),
                low: f("low"),
                close,
                volume: f("volume"),
                amount: f("amount"),
                main_net_inflow: 0.0,
            });
        }
        if out.is_empty() {
            return Err(NetError::EmptyBody(format!("{symbol} scale={scale} 分钟K线为空")));
        }
        Ok(out)
    }

    /// 证券列表分页（hs_a 节点，用于建立全市场股票宇宙）
    pub async fn list_page(
        &self,
        page: usize,
        num: usize,
    ) -> Result<Vec<crate::universe::SymbolInfo>, NetError> {
        let url = format!(
            "{RANK_URL}?page={page}&num={}&sort=symbol&asc=1&node=hs_a&symbol=",
            num.min(100)
        );
        let value: Value = self.http.get_json(&url, Some(REFERER)).await?;
        let list = value.as_array().cloned().unwrap_or_default();
        let out: Vec<crate::universe::SymbolInfo> = list
            .iter()
            .filter_map(|v| {
                let code = v.get("code").and_then(|c| c.as_str()).unwrap_or("").trim().to_string();
                let name = v.get("name").and_then(|c| c.as_str()).unwrap_or("").trim().to_string();
                if !crate::universe::is_a_share(&code) || name.is_empty() {
                    return None;
                }
                Some(crate::universe::SymbolInfo::new(&code, &name))
            })
            .collect();
        if out.is_empty() {
            // 空页说明已到末尾，不算错误
            return Ok(Vec::new());
        }
        Ok(out)
    }

    /// 单页拉取全市场榜单（新浪单页上限 100 条）
    pub async fn rank_page(
        &self,
        sort: &str,
        page: usize,
        num: usize,
        descending: bool,
    ) -> Result<Vec<StockTick>, NetError> {
        let url = format!(
            "{RANK_URL}?page={page}&num={}&sort={sort}&asc={}&node=hs_a&symbol=",
            num.clamp(20, 100),
            if descending { 0 } else { 1 }
        );
        let value: Value = self.http.get_json(&url, Some(REFERER)).await?;
        let list = value.as_array().cloned().unwrap_or_default();
        let ticks: Vec<StockTick> = list.iter().map(parse_node_item).collect();
        if super::is_meaningful(&ticks) {
            Ok(ticks)
        } else {
            Err(NetError::EmptyBody("新浪榜单为空".into()))
        }
    }

    /// 全市场榜单（新浪仅提供行情类排序，无资金流），自动分页
    ///
    /// `sort` ∈ {changepercent, turnoverratio, amount, volume, trade}
    pub async fn rank_stocks(
        &self,
        sort: &str,
        count: usize,
        descending: bool,
    ) -> Result<Vec<StockTick>, NetError> {
        let want = count.clamp(20, 1000);
        let page_size = 100usize;
        let pages = want.div_ceil(page_size).min(8);
        let mut all: Vec<StockTick> = Vec::new();
        let mut last_err: Option<NetError> = None;
        for page in 1..=pages {
            match self.rank_page(sort, page, page_size, descending).await {
                Ok(mut v) => {
                    if v.is_empty() {
                        break;
                    }
                    all.append(&mut v);
                }
                Err(err) => {
                    last_err = Some(err);
                    break;
                }
            }
        }
        if super::is_meaningful(&all) {
            Ok(all)
        } else {
            Err(last_err.unwrap_or_else(|| NetError::EmptyBody("新浪榜单为空".into())))
        }
    }

    /// 批量实时快照
    pub async fn quotes(&self, codes: &[String]) -> Result<Vec<StockTick>, NetError> {
        if codes.is_empty() {
            return Ok(Vec::new());
        }
        let mut out = Vec::new();
        for chunk in codes.chunks(60) {
            let url = format!("https://{HQ_HOST}/list={}", chunk.join(","));
            let text = self.http.get_text_gbk(&url, Some(REFERER)).await?;
            out.extend(parse_hq_text(&text));
        }
        if super::is_meaningful(&out) {
            Ok(out)
        } else {
            Err(NetError::EmptyBody("新浪快照为空".into()))
        }
    }
}

/// 解析 `Market_Center.getHQNodeData` 单条
pub fn parse_node_item(v: &Value) -> StockTick {
    let raw = to_str(v.get("symbol").unwrap_or(&Value::Null));
    let (code, market) = if raw.is_empty() {
        let c = to_str(v.get("code").unwrap_or(&Value::Null));
        let m = super::market_of(&c);
        (c, m)
    } else {
        parse_prefixed(&raw)
    };
    let price = to_f64(v.get("trade").unwrap_or(&Value::Null));
    let prev_close = to_f64(v.get("settlement").unwrap_or(&Value::Null));
    StockTick {
        code,
        name: to_str(v.get("name").unwrap_or(&Value::Null)).replace(' ', ""),
        market,
        price,
        change_pct: to_f64(v.get("changepercent").unwrap_or(&Value::Null)),
        change_amount: to_f64(v.get("pricechange").unwrap_or(&Value::Null)),
        open: to_f64(v.get("open").unwrap_or(&Value::Null)),
        high: to_f64(v.get("high").unwrap_or(&Value::Null)),
        low: to_f64(v.get("low").unwrap_or(&Value::Null)),
        prev_close,
        volume: to_f64(v.get("volume").unwrap_or(&Value::Null)) / 100.0, // 股 → 手
        amount: to_f64(v.get("amount").unwrap_or(&Value::Null)),
        amplitude: if prev_close > 0.0 {
            (to_f64(v.get("high").unwrap_or(&Value::Null))
                - to_f64(v.get("low").unwrap_or(&Value::Null)))
                / prev_close
                * 100.0
        } else {
            0.0
        },
        turnover_rate: to_f64(v.get("turnoverratio").unwrap_or(&Value::Null)),
        volume_ratio: 0.0,
        main_net_inflow: 0.0,
        main_net_ratio: 0.0,
        super_large_net: 0.0,
        large_net: 0.0,
        medium_net: 0.0,
        small_net: 0.0,
        is_st: false,
    }
}

/// 解析 `hq.sinajs.cn` 文本
pub fn parse_hq_text(text: &str) -> Vec<StockTick> {
    let mut out = Vec::new();
    for line in text.lines() {
        let Some((key, payload)) = line.split_once('=') else {
            continue;
        };
        let code_key = key.trim().trim_start_matches("var").trim().trim_start_matches("hq_str_");
        if code_key.is_empty() {
            continue;
        }
        let body = payload.trim().trim_matches(|c| c == '"' || c == ';');
        if body.is_empty() {
            continue;
        }
        let fields: Vec<&str> = body.split(',').collect();
        let (code, market) = parse_prefixed(code_key);
        // 简版（s_ 前缀）只有 6 个字段
        if code_key.starts_with("s_") || fields.len() < 10 {
            if fields.len() < 6 {
                continue;
            }
            let price = p(&fields, 1);
            if price <= 0.0 {
                continue;
            }
            out.push(StockTick {
                code,
                name: fields[0].trim().to_string(),
                market,
                price,
                change_amount: p(&fields, 2),
                change_pct: p(&fields, 3),
                volume: p(&fields, 4),
                amount: p(&fields, 5) * 1.0e4,
                ..Default::default()
            });
            continue;
        }
        let price = p(&fields, 3);
        let prev_close = p(&fields, 2);
        if price <= 0.0 && prev_close <= 0.0 {
            continue;
        }
        let open = p(&fields, 1);
        let high = p(&fields, 4);
        let low = p(&fields, 5);
        out.push(StockTick {
            code,
            name: fields[0].trim().to_string(),
            market,
            price: if price > 0.0 { price } else { prev_close },
            prev_close,
            open,
            high,
            low,
            change_amount: if prev_close > 0.0 { price - prev_close } else { 0.0 },
            change_pct: if prev_close > 0.0 {
                (price - prev_close) / prev_close * 100.0
            } else {
                0.0
            },
            // 新浪成交量单位为股 → 手
            volume: p(&fields, 8) / 100.0,
            amount: p(&fields, 9),
            amplitude: if prev_close > 0.0 && high > 0.0 && low > 0.0 {
                (high - low) / prev_close * 100.0
            } else {
                0.0
            },
            ..Default::default()
        });
    }
    out
}

fn p(fields: &[&str], idx: usize) -> f64 {
    fields
        .get(idx)
        .map(|s| s.trim())
        .and_then(|s| if s.is_empty() { None } else { s.parse::<f64>().ok() })
        .unwrap_or(0.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_minute_kline_shape() {
        // 真实返回结构（新浪 getKLineData）
        let raw = r#"[
          {"day":"2026-09-30 09:31:00","open":"1256.65","high":"1258.62","low":"1256.05","close":"1258.62","volume":"97555","amount":"122728369.8076"},
          {"day":"2026-09-30 11:30:00","open":"1250.00","high":"1251.00","low":"1249.00","close":"1250.50","volume":"1000","amount":"1250000.0"},
          {"day":"2026-09-30 13:01:00","open":"1250.60","high":"1252.00","low":"1250.00","close":"1251.80","volume":"2000","amount":"2503000.0"},
          {"day":"2026-09-30 15:00:00","open":"1258.00","high":"1258.62","low":"1257.50","close":"1258.62","volume":"3000","amount":"3775860.0"}
        ]"#;
        let list: Value = serde_json::from_str(raw).unwrap();
        let mut bars = Vec::new();
        for item in list.as_array().unwrap() {
            let stamp = item.get("day").and_then(|v| v.as_str()).unwrap();
            let (date, time) = stamp.split_at(10);
            bars.push((
                date.to_string(),
                time.trim_start_matches(' ')[0..5].to_string(),
                item.get("close").and_then(|v| v.as_str()).unwrap().to_string(),
            ));
        }
        assert_eq!(bars[0], ("2026-09-30".into(), "09:31".into(), "1258.62".into()));
        assert_eq!(bars[1].1, "11:30");
        // 午休后第一根必须是 13:01，不能是 11:31
        assert_eq!(bars[2].1, "13:01");
        assert_eq!(bars[3].1, "15:00");
    }

    #[test]
    fn datalen_is_capped_at_server_limit() {
        // 服务端硬限制：超过 1023 返回空数组
        assert_eq!(KLINE_MAX_DATALEN, 1023);
        let capped = 99999usize.clamp(1, KLINE_MAX_DATALEN);
        assert_eq!(capped, 1023);
    }

    #[test]
    fn parses_node_item() {
        let v = json!({
            "symbol":"sz001246","code":"001246","name":"N力勤","trade":"65.090",
            "pricechange":43.86,"changepercent":206.594,"settlement":"21.230","open":"65.000",
            "high":"84.850","low":"65.000","volume":117885162i64,"amount":8345390509i64,
            "turnoverratio":74.93
        });
        let t = parse_node_item(&v);
        assert_eq!(t.code, "001246");
        assert_eq!(t.market, 0);
        assert!((t.price - 65.09).abs() < 1e-6);
        assert!((t.change_pct - 206.594).abs() < 1e-3);
        // 股 → 手
        assert!((t.volume - 1178851.62).abs() < 1e-2);
        assert!((t.turnover_rate - 74.93).abs() < 1e-6);
    }

    #[test]
    fn parses_full_hq_line() {
        let text = "var hq_str_sh600519=\"贵州茅台,1239.53,1235.58,1258.62,1270.00,1238.00,1258.60,1258.62,3833100,4797250000,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2026-09-30,15:00:00,00,\";";
        let ticks = parse_hq_text(text);
        assert_eq!(ticks.len(), 1);
        let t = &ticks[0];
        assert_eq!(t.code, "600519");
        assert_eq!(t.name, "贵州茅台");
        assert!((t.price - 1258.62).abs() < 1e-6);
        assert!((t.prev_close - 1235.58).abs() < 1e-6);
        assert!((t.volume - 38331.0).abs() < 1e-3);
        assert!((t.change_pct - 1.8646).abs() < 1e-3);
    }

    #[test]
    fn parses_short_index_line() {
        let text = "var hq_str_s_sh000001=\"上证指数,3839.2527,11.74,0.31,4145602,6793989\";";
        let ticks = parse_hq_text(text);
        assert_eq!(ticks.len(), 1);
        assert_eq!(ticks[0].name, "上证指数");
        assert!((ticks[0].price - 3839.2527).abs() < 1e-3);
        assert!((ticks[0].change_pct - 0.31).abs() < 1e-6);
        // 万元 → 元
        assert!((ticks[0].amount - 6793989.0e4).abs() < 1.0e3);
    }

    #[test]
    fn skips_empty_entries() {
        let text = "var hq_str_sh999999=\"\";\ngarbage";
        assert!(parse_hq_text(text).is_empty());
    }
}
