//! 腾讯财经数据源（一级降级源）。
//!
//! 接口：
//!   * `proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList` 个股榜单（含主力净流入）
//!   * `proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank`          板块榜单（含主力净流入）
//!   * `qt.gtimg.cn/q=`                                            批量实时快照（GBK）
//!
//! 榜单接口只支持 `turnover` / `volume` / `price` 排序，因此本地按目标指标重排，
//! 从而在东方财富不可用时仍能提供全部 7 个个股榜 + 2 个板块榜的真实数据。

use serde_json::Value;
use std::sync::Arc;

use crate::models::{IndexTick, SectorTick, StockTick};
use crate::net::{HttpClient, NetError};

use super::{parse_prefixed, to_f64, to_str};

const RANK_HOST: &str = "proxy.finance.qq.com";
const QUOTE_HOST: &str = "qt.gtimg.cn";
const REFERER: &str = "https://gu.qq.com/";

/// 腾讯板块榜单单次请求的条数上限（**实测值，勿随意调大**）。
///
/// 实测 `board_type=gn`：
/// `count=100` → 53350 字节；`count=200` → 106205 字节；
/// `count=250 / 300 / 400 / 500` → **83 字节的错误体**（服务端直接拒绝）。
///
/// 曾经为了"提高板块覆盖"把请求值从 200 调到 500，结果整个腾讯板块通道
/// 直接失败、连 200 个板块都拿不到 —— 降级路径被彻底打断。
/// 因此这里把上限写成常量并加单测锁住。
pub const RANK_MAX_COUNT: usize = 200;

/// 把调用方期望的条数收敛到服务端真正接受的区间
fn clamp_rank_count(count: usize) -> usize {
    count.clamp(10, RANK_MAX_COUNT)
}

/// 单只股票的逐分钟数据
#[derive(Debug, Clone, Default)]
pub struct MinuteSeries {
    /// 交易日 YYYYMMDD
    pub date: String,
    pub bars: Vec<MinuteBar>,
}

#[derive(Debug, Clone, Default)]
pub struct MinuteBar {
    /// HH:MM
    pub minute: String,
    pub price: f64,
    /// 自开盘累计成交量（手）
    pub cumulative_volume: f64,
    /// 自开盘累计成交额（元）
    pub cumulative_amount: f64,
}

impl MinuteSeries {
    /// 取截至指定节点的累计值
    pub fn at(&self, node: &str) -> Option<&MinuteBar> {
        self.bars
            .iter()
            .filter(|b| b.minute.as_str() <= node)
            .next_back()
    }
}

pub struct TencentSource {
    http: Arc<HttpClient>,
}

impl TencentSource {
    pub fn new(http: Arc<HttpClient>) -> Self {
        Self { http }
    }

    /// 单页拉取个股榜单（腾讯单页上限为 200 条）
    pub async fn rank_stocks_page(
        &self,
        offset: usize,
        count: usize,
    ) -> Result<Vec<StockTick>, NetError> {
        let count = count.clamp(20, 200);
        let url = format!(
            "https://{RANK_HOST}/cgi/cgi-bin/rank/hs/getBoardRankList\
             ?board_code=aStock&sort_type=turnover&direct=down&offset={offset}&count={count}"
        );
        let value: Value = self.http.get_json(&url, Some(REFERER)).await?;
        if to_f64(value.get("code").unwrap_or(&Value::Null)) != 0.0 {
            return Err(NetError::ServerError(format!(
                "腾讯榜单返回错误: {}",
                to_str(value.get("msg").unwrap_or(&Value::Null))
            )));
        }
        let list = value
            .get("data")
            .and_then(|d| d.get("rank_list"))
            .and_then(|l| l.as_array())
            .cloned()
            .unwrap_or_default();
        let ticks: Vec<StockTick> = list.iter().map(parse_rank_item).collect();
        if super::is_meaningful(&ticks) {
            Ok(ticks)
        } else {
            Err(NetError::EmptyBody("腾讯个股榜单为空".into()))
        }
    }

    /// 拉取个股榜单原始数据（按成交额降序，分页覆盖活跃标的）
    ///
    /// 腾讯单页上限 200 条，这里按需分页合并，返回后由调用方按目标指标排序。
    pub async fn rank_stocks_raw(&self, count: usize) -> Result<Vec<StockTick>, NetError> {
        let want = count.clamp(20, 1200);
        let page_size = 200usize;
        let pages = want.div_ceil(page_size).min(6);
        let mut all: Vec<StockTick> = Vec::new();
        let mut last_err: Option<NetError> = None;
        for page in 0..pages {
            match self.rank_stocks_page(page * page_size, page_size).await {
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
            Err(last_err.unwrap_or_else(|| NetError::EmptyBody("腾讯个股榜单为空".into())))
        }
    }

    /// 板块榜单。
    ///
    /// 会按 `count` 递减重试：服务端的条数上限是**会变**的，
    /// 只按一个硬编码值请求，一旦上限下调就会整条通道失败
    /// （本项目真踩过：把 200 调到 500 之后，降级路径一个板块都拿不到）。
    pub async fn rank_sectors(&self, kind: &str, count: usize) -> Result<Vec<SectorTick>, NetError> {
        let want = clamp_rank_count(count);
        let mut tried: Vec<usize> = Vec::new();
        for n in [want, want / 2, 50] {
            if n < 10 || tried.contains(&n) {
                continue;
            }
            tried.push(n);
            match self.rank_sectors_once(kind, n).await {
                Ok(v) => return Ok(v),
                Err(e) => {
                    if n == 10 {
                        return Err(e);
                    }
                    tracing::warn!(
                        target: "network",
                        kind,
                        count = n,
                        err = %e.user_message(),
                        "腾讯板块榜单请求失败，降条数重试"
                    );
                }
            }
        }
        Err(NetError::EmptyBody(format!("腾讯板块榜单为空 {kind}")))
    }

    async fn rank_sectors_once(
        &self,
        kind: &str,
        count: usize,
    ) -> Result<Vec<SectorTick>, NetError> {
        let board_type = match kind {
            "concept" => "gn",
            "region" => "dy",
            _ => "hy",
        };
        let url = format!(
            "https://{RANK_HOST}/cgi/cgi-bin/rank/pt/getRank\
             ?board_type={board_type}&sort_type=turnover&direct=down&offset=0&count={}",
            clamp_rank_count(count)
        );
        let value: Value = self.http.get_json(&url, Some(REFERER)).await?;
        let list = value
            .get("data")
            .and_then(|d| d.get("rank_list"))
            .and_then(|l| l.as_array())
            .cloned()
            .unwrap_or_default();
        let sectors: Vec<SectorTick> = list.iter().map(|v| parse_sector_item(v, kind)).collect();
        if super::is_meaningful_sectors(&sectors) {
            Ok(sectors)
        } else {
            Err(NetError::EmptyBody(format!("腾讯板块榜单为空 {kind}")))
        }
    }

    /// 分时数据（最新交易日逐分钟：价格 / 累计成交量 / 累计成交额）
    ///
    /// 用于历史节点重建的备用通道：东方财富分钟 K 线不可用时接管。
    pub async fn minute_series(
        &self,
        code: &str,
        market: u8,
    ) -> Result<MinuteSeries, NetError> {
        let full = super::prefixed_code(code, market);
        let url = format!(
            "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={full}"
        );
        let value: Value = self.http.get_json(&url, Some(REFERER)).await?;
        let node = value
            .get("data")
            .and_then(|d| d.get(&full))
            .ok_or_else(|| NetError::EmptyBody(format!("腾讯分时无 {full} 数据")))?;
        let inner = node.get("data").unwrap_or(&Value::Null);
        let date = to_str(inner.get("date").unwrap_or(&Value::Null));
        let rows = inner
            .get("data")
            .and_then(|d| d.as_array())
            .cloned()
            .unwrap_or_default();
        let mut bars = Vec::with_capacity(rows.len());
        for row in rows {
            let text = to_str(&row);
            let parts: Vec<&str> = text.split_whitespace().collect();
            if parts.len() < 4 {
                continue;
            }
            let hhmm = parts[0].trim();
            if hhmm.len() != 4 || !hhmm.chars().all(|c| c.is_ascii_digit()) {
                continue;
            }
            let minute = format!("{}:{}", &hhmm[0..2], &hhmm[2..4]);
            let price: f64 = parts[1].parse().unwrap_or(0.0);
            let cum_volume: f64 = parts[2].parse().unwrap_or(0.0);
            let cum_amount: f64 = parts[3].parse().unwrap_or(0.0);
            if price <= 0.0 {
                continue;
            }
            bars.push(MinuteBar {
                minute,
                price,
                cumulative_volume: cum_volume,
                cumulative_amount: cum_amount,
            });
        }
        if bars.is_empty() {
            return Err(NetError::EmptyBody(format!("腾讯分时为空 {full}")));
        }
        Ok(MinuteSeries { date, bars })
    }

    /// 批量实时快照
    pub async fn quotes(&self, codes: &[String]) -> Result<Vec<StockTick>, NetError> {
        if codes.is_empty() {
            return Ok(Vec::new());
        }
        let mut out = Vec::new();
        for chunk in codes.chunks(50) {
            let url = format!("https://{QUOTE_HOST}/q={}", chunk.join(","));
            let text = self.http.get_text_gbk(&url, Some(REFERER)).await?;
            out.extend(parse_quote_text(&text));
        }
        if super::is_meaningful(&out) {
            Ok(out)
        } else {
            Err(NetError::EmptyBody("腾讯快照为空".into()))
        }
    }

    /// 指数快照
    pub async fn indices(&self) -> Result<Vec<IndexTick>, NetError> {
        let codes = ["sh000001", "sz399001", "sz399006", "sh000300", "sh000688", "bj899050"];
        let url = format!("https://{QUOTE_HOST}/q={}", codes.join(","));
        let text = self.http.get_text_gbk(&url, Some(REFERER)).await?;
        let mut out = Vec::new();
        for line in text.lines() {
            let Some((key, payload)) = line.split_once('=') else {
                continue;
            };
            let code = key.trim().trim_start_matches("v_").to_string();
            let fields: Vec<&str> = payload.trim().trim_matches(|c| c == '"' || c == ';').split('~').collect();
            if fields.len() < 40 {
                continue;
            }
            let (short, market) = parse_prefixed(&code);
            out.push(IndexTick {
                code: short,
                name: fields[1].to_string(),
                price: f(&fields, 3),
                prev_close: f(&fields, 4),
                open: f(&fields, 5),
                change_amount: f(&fields, 31),
                change_pct: f(&fields, 32),
                high: f(&fields, 33),
                low: f(&fields, 34),
                amount: f(&fields, 37) * 1.0e4,
                volume: f(&fields, 36),
                main_net_inflow: None,
            });
            let _ = market;
        }
        if super::is_meaningful_indices(&out) {
            Ok(out)
        } else {
            Err(NetError::EmptyBody("腾讯指数快照为空".into()))
        }
    }
}

/// 紧凑布局解析（字段数 < 50 的精简返回）。
///
/// 两种紧凑形态都支持，通过第 1 个字段是否为数字自动判别：
///   A. 名称优先：名称,价格,涨跌额,涨跌幅,成交量,成交额(万)
///   B. 带前缀：1,名称,代码,价格,昨收,今开,成交量,成交额(万)
fn parse_compact(fields: &[&str], code: &str, market: u8) -> Option<StockTick> {
    let numeric = |idx: usize| -> bool {
        fields
            .get(idx)
            .map(|s| {
                let t = s.trim();
                !t.is_empty() && t != "-" && t.parse::<f64>().is_ok()
            })
            .unwrap_or(false)
    };

    // 第 0 个字段不是数字 → 形态 A（名称优先）；否则形态 B（带前缀）
    if !numeric(0) {
        // 形态 A：名称在第 0 位
        let name = fields.first()?.trim().replace(' ', "");
        if name.is_empty() || name.chars().all(|c| c.is_ascii_digit()) {
            return None;
        }
        let price = f(fields, 1);
        if price <= 0.0 {
            return None;
        }
        let change_amount = f(fields, 2);
        let change_pct = f(fields, 3);
        let prev_close = if change_amount != 0.0 {
            price - change_amount
        } else if change_pct.abs() > 1e-9 {
            price / (1.0 + change_pct / 100.0)
        } else {
            0.0
        };
        return Some(StockTick {
            code: code.to_string(),
            name,
            market,
            price,
            prev_close,
            change_amount,
            change_pct: if change_pct != 0.0 {
                change_pct
            } else if prev_close > 0.0 {
                (price - prev_close) / prev_close * 100.0
            } else {
                0.0
            },
            volume: f(fields, 4),
            // 简版成交额单位为万元
            amount: f(fields, 5) * 1.0e4,
            ..Default::default()
        });
    }

    // 形态 B：名称在第 1 位
    let name = fields.get(1)?.trim().replace(' ', "");
    if name.is_empty() {
        return None;
    }
    let price = f(fields, 3);
    if price <= 0.0 {
        return None;
    }
    let prev_close = f(fields, 4);
    Some(StockTick {
        code: code.to_string(),
        name,
        market,
        price,
        prev_close,
        open: f(fields, 5),
        change_amount: if prev_close > 0.0 {
            price - prev_close
        } else {
            0.0
        },
        change_pct: if prev_close > 0.0 {
            (price - prev_close) / prev_close * 100.0
        } else {
            0.0
        },
        volume: f(fields, 6),
        amount: f(fields, 7) * 1.0e4,
        ..Default::default()
    })
}

fn f(fields: &[&str], idx: usize) -> f64 {
    fields
        .get(idx)
        .map(|s| s.trim())
        .and_then(|s| {
            if s.is_empty() || s.trim() == "-" {
                None
            } else {
                s.parse::<f64>().ok()
            }
        })
        .unwrap_or(0.0)
}

/// 解析 `getBoardRankList` 单条记录
pub fn parse_rank_item(v: &Value) -> StockTick {
    let raw_code = to_str(v.get("code").unwrap_or(&Value::Null));
    let (code, market) = parse_prefixed(&raw_code);
    let price = to_f64(v.get("zxj").unwrap_or(&Value::Null));
    let change_pct = to_f64(v.get("zdf").unwrap_or(&Value::Null));
    let prev_close = if change_pct.abs() > 1e-9 {
        price / (1.0 + change_pct / 100.0)
    } else {
        price
    };
    let mut name = to_str(v.get("name").unwrap_or(&Value::Null));
    name = name.replace(' ', "");
    StockTick {
        code,
        name,
        market,
        price,
        change_pct,
        change_amount: to_f64(v.get("zd").unwrap_or(&Value::Null)),
        open: 0.0,
        high: 0.0,
        low: 0.0,
        prev_close,
        volume: to_f64(v.get("volume").unwrap_or(&Value::Null)),
        // 腾讯 turnover 单位为万元
        amount: to_f64(v.get("turnover").unwrap_or(&Value::Null)) * 1.0e4,
        amplitude: to_f64(v.get("zf").unwrap_or(&Value::Null)),
        turnover_rate: to_f64(v.get("hsl").unwrap_or(&Value::Null)),
        volume_ratio: to_f64(v.get("lb").unwrap_or(&Value::Null)),
        // 腾讯 zljlr 单位为万元
        main_net_inflow: to_f64(v.get("zljlr").unwrap_or(&Value::Null)) * 1.0e4,
        main_net_ratio: 0.0,
        super_large_net: 0.0,
        large_net: 0.0,
        medium_net: 0.0,
        small_net: 0.0,
        is_st: false,
    }
}

pub fn parse_sector_item(v: &Value, kind: &str) -> SectorTick {
    let leader = v.get("lzg").cloned().unwrap_or(Value::Null);
    let (leader_code, _) = parse_prefixed(&to_str(leader.get("code").unwrap_or(&Value::Null)));
    let (up, down) = v
        .get("zgb")
        .map(|z| {
            let s = to_str(z);
            let mut it = s.split('/');
            let a = it.next().and_then(|x| x.trim().parse::<i32>().ok()).unwrap_or(0);
            let b = it.next().and_then(|x| x.trim().parse::<i32>().ok()).unwrap_or(0);
            (a, (b - a).max(0))
        })
        .unwrap_or((0, 0));
    SectorTick {
        code: to_str(v.get("code").unwrap_or(&Value::Null)),
        name: to_str(v.get("name").unwrap_or(&Value::Null)),
        kind: kind.to_string(),
        change_pct: to_f64(v.get("zdf").unwrap_or(&Value::Null)),
        main_net_inflow: to_f64(v.get("zljlr").unwrap_or(&Value::Null)) * 1.0e4,
        main_net_ratio: 0.0,
        amount: to_f64(v.get("turnover").unwrap_or(&Value::Null)) * 1.0e4,
        volume: to_f64(v.get("volume").unwrap_or(&Value::Null)),
        turnover_rate: to_f64(v.get("hsl").unwrap_or(&Value::Null)),
        up_count: up,
        down_count: down,
        leader_code,
        leader_name: to_str(leader.get("name").unwrap_or(&Value::Null)),
        leader_change_pct: to_f64(leader.get("zdf").unwrap_or(&Value::Null)),
    }
}

/// 解析 `qt.gtimg.cn` 批量快照文本
pub fn parse_quote_text(text: &str) -> Vec<StockTick> {
    let mut out = Vec::new();
    for line in text.lines() {
        let Some((key, payload)) = line.split_once('=') else {
            continue;
        };
        let code_key = key.trim().trim_start_matches("v_");
        let (code, market) = parse_prefixed(code_key);
        if code.is_empty() {
            continue;
        }
        let fields: Vec<&str> = payload
            .trim()
            .trim_matches(|c| c == '"' || c == ';')
            .split('~')
            .collect();
        if fields.len() < 4 {
            continue;
        }
        if fields.get(1).map(|s| s.trim().is_empty()).unwrap_or(true) {
            continue;
        }
        // 完整行情（>=50 字段）与紧凑行情（<50 字段）字段位置不同，分别解析
        if fields.len() < 50 {
            if let Some(t) = parse_compact(&fields, &code, market) {
                out.push(t);
            }
            continue;
        }
        let price = f(&fields, 3);
        if price <= 0.0 {
            continue;
        }
        out.push(StockTick {
            code,
            name: fields[1].trim().replace(' ', ""),
            market,
            price,
            prev_close: f(&fields, 4),
            open: f(&fields, 5),
            change_amount: f(&fields, 31),
            change_pct: f(&fields, 32),
            high: f(&fields, 33),
            low: f(&fields, 34),
            // 腾讯快照成交额单位为万元
            amount: f(&fields, 37) * 1.0e4,
            volume: f(&fields, 36),
            amplitude: f(&fields, 43),
            turnover_rate: f(&fields, 38),
            volume_ratio: f(&fields, 49),
            ..Default::default()
        });
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_rank_item_with_units() {
        let v = json!({
            "code":"sh600519","name":"贵州茅台","zxj":"1258.62","zd":"23.04","zdf":"1.86",
            "zf":"2.59","hsl":"0.31","lb":"1.36","turnover":"479725","volume":"38331",
            "zljlr":"54034.72","zsz":"15733.78","ltsz":"15733.78"
        });
        let t = parse_rank_item(&v);
        assert_eq!(t.code, "600519");
        assert_eq!(t.market, 1);
        assert_eq!(t.name, "贵州茅台");
        assert!((t.price - 1258.62).abs() < 1e-6);
        // 万元 → 元
        assert!((t.amount - 479725.0e4).abs() < 1.0);
        assert!((t.main_net_inflow - 54034.72e4).abs() < 1.0);
        assert!((t.volume_ratio - 1.36).abs() < 1e-6);
        assert!((t.turnover_rate - 0.31).abs() < 1e-6);
    }

    #[test]
    fn parses_sector_item_with_leader() {
        let v = json!({
            "code":"pt01801080","name":"电子","zxj":"8223.61","zdf":"-2.38","zljlr":"-1424221.16",
            "turnover":"35421841","volume":"88051542.00","hsl":"2.41","lb":"0.76","zgb":"54/495",
            "lzg":{"code":"sz002388","name":"新亚制程","zd":"0.83","zdf":"10.04","zxj":"9.10"}
        });
        let s = parse_sector_item(&v, "industry");
        assert_eq!(s.name, "电子");
        assert_eq!(s.up_count, 54);
        assert_eq!(s.down_count, 441);
        assert_eq!(s.leader_code, "002388");
        assert_eq!(s.leader_name, "新亚制程");
        assert!((s.main_net_inflow - (-1424221.16e4)).abs() < 10.0);
    }

    /// 真实抓取的 88 字段负载（贵州茅台，2026-09-30 收盘）
    const REAL_STOCK_LINE: &str = "v_sh600519=\"1~贵州茅台~600519~1258.62~1235.58~1239.53~38331~21633~16698~1258.62~14~1258.44~1~1258.16~1~1258.05~2~1258.00~41~1258.65~2~1258.66~3~1258.68~1~1258.69~2~1258.75~80~~20260930161458~23.04~1.86~1268.00~1236.05~1258.62/38331/4797246636~38331~479725~0.31~19.32~~1268.00~1236.05~2.59~15733.78~15733.78~6.26~1359.14~1112.02~1.36~-29~1251.53~17.67~19.11~~~~0.06~479724.6636~453.1032~36~   A~GP-A~-6.71~0.38~4.13~32.41~27.30~1539.98~1151.01~-1.11~-3.15~5.87~1250081601~1250081601~-19.73~-9.97~1250081601~~-9.58~0.16~~CNY~0~___D__F__N~1257.93~15~\";";

    #[test]
    fn parses_quote_text_real_payload() {
        let ticks = parse_quote_text(REAL_STOCK_LINE);
        assert_eq!(ticks.len(), 1);
        let t = &ticks[0];
        assert_eq!(t.code, "600519");
        assert_eq!(t.market, 1);
        assert_eq!(t.name, "贵州茅台");
        assert!((t.price - 1258.62).abs() < 1e-6);
        assert!((t.prev_close - 1235.58).abs() < 1e-6);
        assert!((t.change_amount - 23.04).abs() < 1e-6);
        assert!((t.change_pct - 1.86).abs() < 1e-6);
        assert!((t.high - 1268.00).abs() < 1e-6);
        assert!((t.low - 1236.05).abs() < 1e-6);
        assert!((t.volume - 38331.0).abs() < 1e-6);
        // 万元 → 元
        assert!((t.amount - 479725.0e4).abs() < 1.0e4);
        assert!((t.turnover_rate - 0.31).abs() < 1e-6);
        assert!((t.amplitude - 2.59).abs() < 1e-6);
        assert!((t.volume_ratio - 1.36).abs() < 1e-6);
    }

    #[test]
    fn parses_compact_payload_name_first() {
        // 精简格式 A：名称,价格,涨跌额,涨跌幅,成交量,成交额(万)
        let text = "v_sh000001=\"上证指数~3839.2527~11.74~0.31~4145602~6793989\";";
        let ticks = parse_quote_text(text);
        assert_eq!(ticks.len(), 1);
        assert_eq!(ticks[0].name, "上证指数");
        assert_eq!(ticks[0].code, "000001");
        assert_eq!(ticks[0].market, 1);
        assert!((ticks[0].price - 3839.2527).abs() < 1e-6);
        assert!((ticks[0].volume - 4145602.0).abs() < 1e-6);
        assert!((ticks[0].amount - 6793989.0e4).abs() < 1.0e3);
    }

    #[test]
    fn parses_compact_payload_with_prefix() {
        // 精简格式 B：1,名称,代码,价格,昨收,今开,成交量,成交额(万)
        let text = "v_sz000001=\"1~平安银行~000001~11.39~11.35~11.36~208960~23775\";";
        let ticks = parse_quote_text(text);
        assert_eq!(ticks.len(), 1);
        assert_eq!(ticks[0].name, "平安银行");
        assert_eq!(ticks[0].market, 0);
        assert!((ticks[0].price - 11.39).abs() < 1e-6);
        assert!((ticks[0].prev_close - 11.35).abs() < 1e-6);
        assert!((ticks[0].change_pct - 0.3524).abs() < 0.01);
        assert!((ticks[0].amount - 23775.0e4).abs() < 1.0e3);
    }

    #[test]
    fn minute_series_picks_node_value() {
        let series = MinuteSeries {
            date: "20260930".into(),
            bars: vec![
                MinuteBar { minute: "09:30".into(), price: 1239.53, cumulative_volume: 161.0, cumulative_amount: 19956433.0 },
                MinuteBar { minute: "09:35".into(), price: 1238.29, cumulative_volume: 1854.0, cumulative_amount: 229776932.84 },
                MinuteBar { minute: "09:40".into(), price: 1240.0, cumulative_volume: 3000.0, cumulative_amount: 400000000.0 },
            ],
        };
        let at35 = series.at("09:35").unwrap();
        assert!((at35.cumulative_volume - 1854.0).abs() < 1e-6);
        assert!((at35.price - 1238.29).abs() < 1e-6);
        let at40 = series.at("09:40").unwrap();
        assert!((at40.cumulative_volume - 3000.0).abs() < 1e-6);
        assert!(series.at("09:00").is_none());
    }

    #[test]
    fn quote_parser_skips_empty_payload() {
        assert!(parse_quote_text("v_pv_none_match=\"1\";").is_empty());
        assert!(parse_quote_text("").is_empty());
        assert!(parse_quote_text("garbage").is_empty());
    }

    #[test]
    fn quote_parser_handles_bj_prefix() {
        // 北交所指数（88 字段完整格式）
        let text = "v_bj899050=\"1~北证50~899050~3842.19~3830.45~3839.25~414560247~0~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~~20260930161500~11.74~0.31~3851.22~3833.09~3842.19/414560247/679398992445~414560247~67939899~0.85~16.76~~3851.22~3833.09~0.47~602370.78~682245.82~0.00~-1~-1~0.92~0~3842.53~~~~~67939899.2445~0.0000~0~ ~ZS~-3.19~-2.78~~~~4258.86~3741.11~-0.57~-3.46~-3.71~4858320758781~~-9.37~-4.50~4858320758781~~~~-1.05~-0.02~~CNY~0~~0.00~0~\";";
        let ticks = parse_quote_text(text);
        assert_eq!(ticks.len(), 1);
        assert_eq!(ticks[0].code, "899050");
        assert_eq!(ticks[0].market, 2);
        assert_eq!(ticks[0].name, "北证50");
        assert!(ticks[0].price > 0.0);
    }
}

#[cfg(test)]
mod rank_count_tests {
    use super::{clamp_rank_count, RANK_MAX_COUNT};

    /// 锁住实测的服务端上限：250 及以上会被腾讯拒绝（返回 83 字节错误体）
    #[test]
    fn never_asks_for_more_than_the_measured_limit() {
        assert_eq!(RANK_MAX_COUNT, 200);
        // 曾经把请求值调到 500，结果整条降级通道直接失败
        assert_eq!(clamp_rank_count(500), 200);
        assert_eq!(clamp_rank_count(201), 200);
        assert_eq!(clamp_rank_count(250), 200);
        // 正常值原样保留
        assert_eq!(clamp_rank_count(200), 200);
        assert_eq!(clamp_rank_count(120), 120);
        // 下界保护
        assert_eq!(clamp_rank_count(0), 10);
        assert_eq!(clamp_rank_count(5), 10);
    }
}
