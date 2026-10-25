//! 东方财富数据源（主数据源，第 8.3 章）。
//!
//! 接口清单：
//!   * `/api/qt/clist/get`            榜单（个股 / 板块），支持资金流、成交量、量比等排序
//!   * `/api/qt/ulist.np/get`         批量快照（指数 / 自选股）
//!   * `/api/qt/stock/kline/get`      分钟 K 线（历史重建用）
//!   * `/api/qt/stock/fflow/kline/get` 分钟资金流（历史重建用）
//!
//! 所有请求走多镜像主机轮换，任一镜像可用即可返回。

use serde_json::Value;
use std::sync::Arc;

use crate::models::{FlowPoint, IndexTick, MinutePoint, SectorTick, StockTick};
use crate::net::{HttpClient, NetError};

use super::{field_f64, field_str, secid, to_f64, to_opt_f64, to_str};

/// push2 榜单镜像（按可用性自动轮换）
pub const PUSH2_HOSTS: &[&str] = &[
    "push2.eastmoney.com",
    "1.push2.eastmoney.com",
    "7.push2.eastmoney.com",
    "82.push2.eastmoney.com",
    "push2delay.eastmoney.com",
];

/// push2his 历史镜像
pub const PUSH2HIS_HOSTS: &[&str] = &["push2his.eastmoney.com", "push2his.eastmoney.com"];

const REFERER: &str = "https://quote.eastmoney.com/";

/// 沪深京 A 股过滤器
pub const FS_ALL_A: &str = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048";
/// 行业板块
pub const FS_SECTOR_INDUSTRY: &str = "m:90+t:2+f:!50";
/// 概念板块
pub const FS_SECTOR_CONCEPT: &str = "m:90+t:3+f:!50";
/// 地域板块
pub const FS_SECTOR_REGION: &str = "m:90+t:1+f:!50";

/// 概念板块数量的"疑似被截断"阈值。
///
/// 东方财富全量概念板块有 400+ 个；一旦实际拿到的概念板块数不超过这个值，
/// 基本可以判定是走了降级通道（腾讯概念板块约 200 个）或被服务端截断，
/// 界面必须显式提示，而不是把"只覆盖了一半"当作全量展示。
pub const CONCEPT_TRUNCATION_HINT: usize = 250;

const STOCK_FIELDS: &str = "f12,f13,f14,f2,f3,f4,f5,f6,f7,f8,f10,f15,f16,f17,f18,\
f62,f66,f69,f72,f75,f78,f81,f84,f87,f184,f20,f21";

const SECTOR_FIELDS: &str = "f12,f14,f2,f3,f4,f5,f6,f8,f62,f104,f105,f128,f136,f140,f141,f184";

const INDEX_FIELDS: &str = "f1,f2,f3,f4,f5,f6,f12,f13,f14,f15,f16,f17,f18,f62";

/// 指数分钟 K 线（分时图原始数据）
#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct IndexMinuteBar {
    pub trade_date: String,
    pub minute: String,
    pub open: f64,
    pub close: f64,
    pub high: f64,
    pub low: f64,
    pub volume: f64,
    pub amount: f64,
}

/// 默认指数池
pub const DEFAULT_INDICES: &[(&str, &str)] = &[
    ("1.000001", "上证指数"),
    ("0.399001", "深证成指"),
    ("0.399006", "创业板指"),
    ("1.000300", "沪深300"),
    ("1.000688", "科创50"),
    ("0.899050", "北证50"),
];

pub struct EastmoneySource {
    http: Arc<HttpClient>,
    hosts: Vec<String>,
    his_hosts: Vec<String>,
}

impl EastmoneySource {
    pub fn new(http: Arc<HttpClient>) -> Self {
        Self {
            http,
            hosts: PUSH2_HOSTS.iter().map(|s| s.to_string()).collect(),
            his_hosts: PUSH2HIS_HOSTS.iter().map(|s| s.to_string()).collect(),
        }
    }

    fn hosts(&self) -> &[String] {
        &self.hosts
    }

    fn his_hosts(&self) -> &[String] {
        &self.his_hosts
    }

    /// 榜单原始请求
    async fn clist_raw(
        &self,
        fs: &str,
        fid: &str,
        fields: &str,
        size: usize,
        page: usize,
        descending: bool,
    ) -> Result<Vec<Value>, NetError> {
        Ok(self
            .clist_raw_page(fs, fid, fields, size, page, descending)
            .await?
            .0)
    }

    /// 榜单原始请求（返回数据数组与总数）
    pub async fn clist_raw_page(
        &self,
        fs: &str,
        fid: &str,
        fields: &str,
        size: usize,
        page: usize,
        descending: bool,
    ) -> Result<(Vec<Value>, usize), NetError> {
        let path = format!(
            "/api/qt/clist/get?pn={page}&pz={size}&po={po}&np=1&fltt=2&invt=2&dect=1&ut=fa5fd1943c7b386f172d6893dbfba10b&fid={fid}&fs={fs}&fields={fields}",
            page = page,
            size = size,
            po = if descending { 1 } else { 0 },
            fid = fid,
            fs = fs,
            fields = fields
        );
        let value: Value = self
            .http
            .get_json_rotating(self.hosts(), &path, Some(REFERER))
            .await?;
        let total = value
            .get("data")
            .and_then(|d| d.get("total"))
            .map(to_f64)
            .unwrap_or(0.0) as usize;
        Ok((extract_diff(&value), total))
    }

    /// 证券列表轻量分页（仅代码/市场/名称）。
    ///
    /// 注意：东财 clist 的 `pz` 实际被服务端限制为 100 条/页，
    /// 传更大的值不会生效 —— 全市场 5900 只需要分页约 60 次。
    pub async fn clist_symbol_page(
        &self,
        page: usize,
        size: usize,
    ) -> Result<(Vec<crate::universe::SymbolInfo>, usize), NetError> {
        let (items, total) = self
            .clist_raw_page(FS_ALL_A, "f12", "f12,f13,f14", size.min(100), page, true)
            .await?;
        let symbols: Vec<crate::universe::SymbolInfo> = items
            .iter()
            .filter_map(|v| {
                let code = field_str(v, "f12");
                if !crate::universe::is_a_share(&code) {
                    return None;
                }
                let name = field_str(v, "f14");
                if name.is_empty() {
                    return None;
                }
                Some(crate::universe::SymbolInfo::new(&code, &name))
            })
            .collect();
        Ok((symbols, total))
    }

    /// 分页拉取个股（全市场扫描用）
    pub async fn clist_page(
        &self,
        fs: &str,
        fid: &str,
        size: usize,
        page: usize,
        descending: bool,
    ) -> Result<(Vec<StockTick>, usize), NetError> {
        let (items, total) = self
            .clist_raw_page(fs, fid, STOCK_FIELDS, size, page, descending)
            .await?;
        Ok((items.iter().map(parse_stock_item).collect(), total))
    }

    /// 个股榜单
    pub async fn clist_stocks(
        &self,
        fid: &str,
        size: usize,
        descending: bool,
    ) -> Result<Vec<StockTick>, NetError> {
        let items = self
            .clist_raw(FS_ALL_A, fid, STOCK_FIELDS, size, 1, descending)
            .await?;
        let ticks: Vec<StockTick> = items.iter().map(parse_stock_item).collect();
        if super::is_meaningful(&ticks) {
            Ok(ticks)
        } else {
            Err(NetError::EmptyBody(format!("clist fid={fid} 无有效数据")))
        }
    }

    // 注意：这里**刻意不再提供"单页取板块"的接口**。
    // 早期版本用 `clist_sectors(kind, fid, size, ..)` 只请求第 1 页，
    // 导致概念板块被截断在 200 个（东财实际 400+），而界面上看不出被截断。
    // 现在统一走 `clist_sectors_all`，按 data.total 翻页取全。

    /// 板块列表（**翻页取全**）。
    ///
    /// # 为什么必须翻页
    ///
    /// 东财 clist 单页 `pz` 有服务端上限（实测个股列表被限制在 100 条/页），
    /// 且早期实现只请求第 1 页 —— 结果**概念板块被截断在 200 个**，
    /// 而东财的概念板块有 400+ 个。首页"板块资金流入流出对比"因此长期
    /// 只覆盖了不到一半的板块。
    ///
    /// 这里改为按响应里的 `data.total` 翻页取全，并返回 `total` 供上层
    /// 上报"板块总数"，让覆盖情况可被核对。
    pub async fn clist_sectors_all(
        &self,
        kind: &str,
        fid: &str,
        descending: bool,
    ) -> Result<(Vec<SectorTick>, usize), NetError> {
        let fs = match kind {
            "concept" => FS_SECTOR_CONCEPT,
            "region" => FS_SECTOR_REGION,
            _ => FS_SECTOR_INDUSTRY,
        };
        // 单页大小取服务端安全上限；页数上限给足冗余（600 个板块 = 6 页）
        const PAGE_SIZE: usize = 100;
        const MAX_PAGES: usize = 20;

        let mut out: Vec<SectorTick> = Vec::new();
        let mut total = 0usize;
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();

        for page in 1..=MAX_PAGES {
            let (items, t) =
                self.clist_raw_page(fs, fid, SECTOR_FIELDS, PAGE_SIZE, page, descending)
                    .await?;
            if page == 1 {
                total = t;
            }
            if items.is_empty() {
                break;
            }
            let got = items.len();
            for v in &items {
                let s = parse_sector_item(v, kind);
                // 翻页边界可能重叠，按代码去重
                if seen.insert(s.code.clone()) {
                    out.push(s);
                }
            }
            if should_stop_paging(out.len(), total, got, PAGE_SIZE) {
                break;
            }
        }

        if super::is_meaningful_sectors(&out) {
            let n = out.len();
            Ok((out, total.max(n)))
        } else {
            Err(NetError::EmptyBody(format!("板块 clist {kind} 无有效数据")))
        }
    }

    /// 批量快照
    pub async fn ulist(&self, secids: &[String]) -> Result<Vec<StockTick>, NetError> {
        if secids.is_empty() {
            return Ok(Vec::new());
        }
        let mut out = Vec::new();
        for chunk in secids.chunks(60) {
            let joined = chunk.join(",");
            let path = format!(
                "/api/qt/ulist.np/get?fltt=2&invt=2&np=1&secids={joined}&fields={STOCK_FIELDS}"
            );
            let value: Value = self
                .http
                .get_json_rotating(self.hosts(), &path, Some(REFERER))
                .await?;
            for item in extract_diff(&value) {
                out.push(parse_stock_item(&item));
            }
        }
        Ok(out)
    }

    /// 指数快照
    pub async fn indices(&self) -> Result<Vec<IndexTick>, NetError> {
        let secids: Vec<String> = DEFAULT_INDICES.iter().map(|(s, _)| s.to_string()).collect();
        let path = format!(
            "/api/qt/ulist.np/get?fltt=2&invt=2&np=1&secids={}&fields={INDEX_FIELDS}",
            secids.join(",")
        );
        let value: Value = self
            .http
            .get_json_rotating(self.hosts(), &path, Some(REFERER))
            .await?;
        let list = extract_diff(&value);
        let mut out: Vec<IndexTick> = list.iter().map(parse_index_item).collect();
        // 补齐名称（接口未返回时使用内置表）
        for idx in out.iter_mut() {
            if idx.name.is_empty() {
                if let Some((_, name)) = DEFAULT_INDICES
                    .iter()
                    .find(|(s, _)| s.ends_with(&idx.code))
                {
                    idx.name = (*name).to_string();
                }
            }
        }
        if super::is_meaningful_indices(&out) {
            Ok(out)
        } else {
            Err(NetError::EmptyBody("指数接口无有效数据".into()))
        }
    }

    /// 分钟 K 线（历史重建核心，第 4.11 章）
    ///
    /// 返回最近 `limit` 根分钟线，调用方按交易日过滤。
    pub async fn minute_kline(
        &self,
        code: &str,
        market: u8,
        limit: usize,
    ) -> Result<Vec<MinutePoint>, NetError> {
        let sid = secid(code, market);
        let path = format!(
            "/api/qt/stock/kline/get?secid={sid}&klt=1&fqt=1&lmt={limit}&end=20500101\
             &fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57"
        );
        let value: Value = self
            .http
            .get_json_rotating(self.his_hosts(), &path, Some(REFERER))
            .await?;
        let klines = value
            .get("data")
            .and_then(|d| d.get("klines"))
            .and_then(|k| k.as_array())
            .cloned()
            .unwrap_or_default();
        let mut out = Vec::with_capacity(klines.len());
        for line in klines {
            let text = to_str(&line);
            if let Some(p) = parse_minute_line(&text, code) {
                out.push(p);
            }
        }
        if out.is_empty() {
            Err(NetError::EmptyBody(format!("{code} 分钟K线为空")))
        } else {
            Ok(out)
        }
    }

    /// 分钟资金流（历史重建核心）
    pub async fn minute_flow(
        &self,
        code: &str,
        market: u8,
        limit: usize,
    ) -> Result<Vec<FlowPoint>, NetError> {
        self.minute_flow_by_secid(&secid(code, market), limit).await
    }

    /// 按 secid 直接取分钟资金流。
    ///
    /// # 主机选择（实测结论，勿随意改动）
    ///
    /// 分钟资金流 `fflow/kline` 只在 **push2** 主机可用；
    /// 用 push2his 会返回 `rc=0` 但 `klines` 为空数组 ——
    /// 早期实现正是用了 his_hosts，导致个股/大盘资金曲线长期为空。
    ///
    /// 字段顺序（实测）：`时间, 主力净流入, 小单, 中单, 大单, 超大单`，
    /// 且满足 `主力 = 大单 + 超大单`。
    pub async fn minute_flow_by_secid(
        &self,
        sid: &str,
        limit: usize,
    ) -> Result<Vec<FlowPoint>, NetError> {
        let lmt = if limit == 0 { 0 } else { limit.max(60) };
        let path = format!(
            "/api/qt/stock/fflow/kline/get?secid={sid}&klt=1&lmt={lmt}&fields1=f1,f2,f3,f7\
             &fields2=f51,f52,f53,f54,f55,f56"
        );
        let value: Value = self
            .http
            .get_json_rotating(self.hosts(), &path, Some(REFERER))
            .await?;
        let klines = value
            .get("data")
            .and_then(|d| d.get("klines"))
            .and_then(|k| k.as_array())
            .cloned()
            .unwrap_or_default();
        let mut out = Vec::with_capacity(klines.len());
        for line in klines {
            let text = to_str(&line);
            if let Some(p) = parse_flow_line(&text) {
                out.push(p);
            }
        }
        if out.is_empty() {
            Err(NetError::EmptyBody(format!("{sid} 资金流分钟为空")))
        } else {
            Ok(out)
        }
    }

    /// 指数分钟 K 线（分时图数据源）。
    ///
    /// 返回 `(时间, 开, 收, 高, 低, 成交量, 成交额)`，一个完整交易日 240 根。
    pub async fn index_minute_kline(
        &self,
        sid: &str,
        limit: usize,
    ) -> Result<Vec<IndexMinuteBar>, NetError> {
        let path = format!(
            "/api/qt/stock/kline/get?secid={sid}&klt=1&fqt=1&lmt={}&end=20500101\
             &fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57",
            limit.max(300)
        );
        let value: Value = self
            .http
            .get_json_rotating(self.his_hosts(), &path, Some(REFERER))
            .await?;
        let klines = value
            .get("data")
            .and_then(|d| d.get("klines"))
            .and_then(|k| k.as_array())
            .cloned()
            .unwrap_or_default();
        let mut out = Vec::with_capacity(klines.len());
        for line in klines {
            let text = to_str(&line);
            let parts: Vec<&str> = text.split(',').collect();
            if parts.len() < 7 {
                continue;
            }
            let stamp = parts[0].trim();
            let (date, minute) = match stamp.split_once(' ') {
                Some((d, t)) => (d.to_string(), t.to_string()),
                None => (String::new(), stamp.to_string()),
            };
            let f = |i: usize| parts[i].trim().parse::<f64>().unwrap_or(0.0);
            out.push(IndexMinuteBar {
                trade_date: date,
                minute,
                open: f(1),
                close: f(2),
                high: f(3),
                low: f(4),
                volume: f(5),
                amount: f(6),
            });
        }
        if out.is_empty() {
            Err(NetError::EmptyBody(format!("{sid} 指数分钟 K 线为空")))
        } else {
            Ok(out)
        }
    }

    /// 日 K 线（用于历史交易日校验）
    pub async fn daily_kline(
        &self,
        code: &str,
        market: u8,
        limit: usize,
    ) -> Result<Vec<(String, f64, f64, f64, f64, f64)>, NetError> {
        let sid = secid(code, market);
        let path = format!(
            "/api/qt/stock/kline/get?secid={sid}&klt=101&fqt=1&lmt={limit}&end=20500101\
             &fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57"
        );
        let value: Value = self
            .http
            .get_json_rotating(self.his_hosts(), &path, Some(REFERER))
            .await?;
        let klines = value
            .get("data")
            .and_then(|d| d.get("klines"))
            .and_then(|k| k.as_array())
            .cloned()
            .unwrap_or_default();
        let mut out = Vec::new();
        for line in klines {
            let text = to_str(&line);
            let parts: Vec<&str> = text.split(',').collect();
            if parts.len() >= 7 {
                out.push((
                    parts[0].to_string(),
                    parts[1].parse().unwrap_or(0.0),
                    parts[2].parse().unwrap_or(0.0),
                    parts[3].parse().unwrap_or(0.0),
                    parts[4].parse().unwrap_or(0.0),
                    parts[5].parse().unwrap_or(0.0),
                ));
            }
        }
        Ok(out)
    }
}

/// 从 clist/ulist 响应中提取数据数组，兼容 `diff` 为数组或对象两种形态
pub fn extract_diff(value: &Value) -> Vec<Value> {
    let Some(data) = value.get("data") else {
        return Vec::new();
    };
    if data.is_null() {
        return Vec::new();
    }
    if let Some(arr) = data.get("diff").and_then(|d| d.as_array()) {
        return arr.clone();
    }
    if let Some(obj) = data.get("diff").and_then(|d| d.as_object()) {
        return obj.values().cloned().collect();
    }
    if let Some(arr) = data.as_array() {
        return arr.clone();
    }
    Vec::new()
}

pub fn parse_stock_item(v: &Value) -> StockTick {
    let code = field_str(v, "f12");
    let market = {
        let m = field_f64(v, "f13");
        if m > 0.0 {
            m as u8
        } else {
            super::market_of(&code)
        }
    };
    let price = field_f64(v, "f2");
    let prev_close = field_f64(v, "f18");
    let mut tick = StockTick {
        code,
        name: field_str(v, "f14"),
        market,
        price,
        change_pct: field_f64(v, "f3"),
        change_amount: field_f64(v, "f4"),
        open: field_f64(v, "f17"),
        high: field_f64(v, "f15"),
        low: field_f64(v, "f16"),
        prev_close,
        volume: field_f64(v, "f5"),
        amount: field_f64(v, "f6"),
        amplitude: field_f64(v, "f7"),
        turnover_rate: field_f64(v, "f8"),
        volume_ratio: field_f64(v, "f10"),
        main_net_inflow: field_f64(v, "f62"),
        main_net_ratio: field_f64(v, "f184"),
        super_large_net: field_f64(v, "f66"),
        large_net: field_f64(v, "f72"),
        medium_net: field_f64(v, "f78"),
        small_net: field_f64(v, "f84"),
        is_st: false,
    };
    if tick.prev_close <= 0.0 && tick.change_pct != 0.0 && tick.price > 0.0 {
        let denom = 1.0 + tick.change_pct / 100.0;
        if denom.abs() > 1e-6 {
            tick.prev_close = tick.price / denom;
        }
    }
    if tick.amplitude == 0.0 && tick.prev_close > 0.0 && tick.high > 0.0 && tick.low > 0.0 {
        tick.amplitude = (tick.high - tick.low) / tick.prev_close * 100.0;
    }
    tick
}

pub fn parse_sector_item(v: &Value, kind: &str) -> SectorTick {
    let mut s = SectorTick {
        code: field_str(v, "f12"),
        name: field_str(v, "f14"),
        kind: kind.to_string(),
        change_pct: field_f64(v, "f3"),
        main_net_inflow: field_f64(v, "f62"),
        main_net_ratio: field_f64(v, "f184"),
        amount: field_f64(v, "f6"),
        volume: field_f64(v, "f5"),
        turnover_rate: field_f64(v, "f8"),
        up_count: field_f64(v, "f104") as i32,
        down_count: field_f64(v, "f105") as i32,
        leader_code: field_str(v, "f140"),
        leader_name: field_str(v, "f128"),
        leader_change_pct: field_f64(v, "f136"),
    };
    if s.up_count == 0 && s.down_count == 0 {
        if let Some(n) = to_opt_f64(v.get("f104").unwrap_or(&Value::Null)) {
            s.up_count = n as i32;
        }
    }
    s
}

pub fn parse_index_item(v: &Value) -> IndexTick {
    IndexTick {
        code: field_str(v, "f12"),
        name: field_str(v, "f14"),
        price: field_f64(v, "f2"),
        change_pct: field_f64(v, "f3"),
        change_amount: field_f64(v, "f4"),
        volume: field_f64(v, "f5"),
        amount: field_f64(v, "f6"),
        high: field_f64(v, "f15"),
        low: field_f64(v, "f16"),
        open: field_f64(v, "f17"),
        prev_close: field_f64(v, "f18"),
        main_net_inflow: to_opt_f64(v.get("f62").unwrap_or(&Value::Null)),
    }
}

/// 解析 `2026-09-30 09:35,开,收,高,低,量,额`
pub fn parse_minute_line(line: &str, code: &str) -> Option<MinutePoint> {
    let parts: Vec<&str> = line.split(',').collect();
    if parts.len() < 7 {
        return None;
    }
    let stamp = parts[0].trim();
    let (date_str, time_str) = stamp.split_once(' ')?;
    let date = chrono::NaiveDate::parse_from_str(date_str, "%Y-%m-%d").ok()?;
    let close: f64 = parts[2].parse().ok()?;
    Some(MinutePoint {
        code: code.to_string(),
        trade_date: date,
        minute: time_str.to_string(),
        price: close,
        volume: parts[5].parse().unwrap_or(0.0),
        amount: parts[6].parse().unwrap_or(0.0),
        main_net_inflow: 0.0,
    })
}

/// 解析资金流分钟线：`2026-09-30 09:35,主力净流入,小单,中单,大单,超大单,...`
pub fn parse_flow_line(line: &str) -> Option<FlowPoint> {
    let parts: Vec<&str> = line.split(',').collect();
    if parts.len() < 6 {
        return None;
    }
    let time = parts[0].split_whitespace().nth(1).unwrap_or(parts[0]).to_string();
    let f = |i: usize| parts.get(i).and_then(|s| s.trim().parse::<f64>().ok()).unwrap_or(0.0);
    Some(FlowPoint {
        time,
        main_net: f(1),
        small: f(2),
        medium: f(3),
        large: f(4),
        super_large: f(5),
    })
}

/// 单日分时数据（用于个股页面）
pub fn to_flow_series(points: &[FlowPoint]) -> Vec<FlowPoint> {
    points.to_vec()
}

pub fn _unused(v: &Value) -> f64 {
    to_f64(v)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn extracts_diff_as_array() {
        let v = json!({"rc":0,"data":{"diff":[{"f12":"000001"},{"f12":"600519"}]}});
        let items = extract_diff(&v);
        assert_eq!(items.len(), 2);
        assert_eq!(field_str(&items[0], "f12"), "000001");
    }

    #[test]
    fn extracts_diff_as_object() {
        let v = json!({"rc":0,"data":{"diff":{"0":{"f12":"000001"},"1":{"f12":"600519"}}}});
        let items = extract_diff(&v);
        assert_eq!(items.len(), 2);
    }

    #[test]
    fn handles_null_data() {
        assert!(extract_diff(&json!({"rc":0,"data":null})).is_empty());
        assert!(extract_diff(&json!({"rc":102})).is_empty());
    }

    #[test]
    fn parses_stock_item_fully() {
        let v = json!({
            "f12":"600519","f13":1,"f14":"贵州茅台","f2":1258.62,"f3":1.86,"f4":23.04,
            "f5":38331,"f6":4797250000.0,"f7":2.59,"f8":0.31,"f10":1.36,
            "f15":1270.0,"f16":1238.0,"f17":1240.0,"f18":1235.58,
            "f62":540347200.0,"f184":11.3,"f66":300000000.0,"f72":240347200.0,
            "f78":-100000000.0,"f84":-440347200.0
        });
        let t = parse_stock_item(&v);
        assert_eq!(t.code, "600519");
        assert_eq!(t.name, "贵州茅台");
        assert_eq!(t.market, 1);
        assert!((t.price - 1258.62).abs() < 1e-6);
        assert!((t.main_net_inflow - 5.403472e8).abs() < 1.0);
        assert!((t.volume_ratio - 1.36).abs() < 1e-6);
        assert_eq!(t.secid(), "1.600519");
    }

    #[test]
    fn parses_stock_item_with_placeholders() {
        let v = json!({"f12":"000001","f13":0,"f14":"平安银行","f2":"-","f3":"-","f62":"-"});
        let t = parse_stock_item(&v);
        assert_eq!(t.price, 0.0);
        assert_eq!(t.main_net_inflow, 0.0);
        assert_eq!(t.market, 0);
    }

    #[test]
    fn derives_prev_close_when_missing() {
        let v = json!({"f12":"000001","f13":0,"f14":"X","f2":11.0,"f3":10.0,"f18":0});
        let t = parse_stock_item(&v);
        assert!((t.prev_close - 10.0).abs() < 1e-6);
    }

    #[test]
    fn parses_sector_item() {
        let v = json!({
            "f12":"BK0447","f14":"通信设备","f2":3456.7,"f3":2.15,"f5":1.2e7,"f6":2.3e10,
            "f62":1.5e9,"f104":88,"f105":12,"f128":"中兴通讯","f136":7.2,"f140":"000063"
        });
        let s = parse_sector_item(&v, "industry");
        assert_eq!(s.code, "BK0447");
        assert_eq!(s.name, "通信设备");
        assert_eq!(s.up_count, 88);
        assert_eq!(s.leader_name, "中兴通讯");
        assert!((s.main_net_inflow - 1.5e9).abs() < 1.0);
    }

    #[test]
    fn parses_minute_kline_line() {
        let p = parse_minute_line("2026-09-30 09:35,11.36,11.39,11.41,11.33,20896,23775150.00", "000001")
            .unwrap();
        assert_eq!(p.code, "000001");
        assert_eq!(p.minute, "09:35");
        assert_eq!(p.trade_date.to_string(), "2026-09-30");
        assert!((p.price - 11.39).abs() < 1e-9);
        assert!((p.volume - 20896.0).abs() < 1e-6);
    }

    #[test]
    fn rejects_malformed_minute_line() {
        assert!(parse_minute_line("garbage", "000001").is_none());
        assert!(parse_minute_line("2026-09-30 09:35,1,2", "000001").is_none());
    }

    #[test]
    fn parses_flow_line() {
        let p = parse_flow_line("2026-09-30 09:35,1234567.0,-100.0,-200.0,300.0,400.0").unwrap();
        assert_eq!(p.time, "09:35");
        assert!((p.main_net - 1234567.0).abs() < 1e-6);
        assert!((p.super_large - 400.0).abs() < 1e-6);
    }

    #[test]
    fn index_parse_handles_missing_flow() {
        let v = json!({"f12":"000001","f14":"上证指数","f2":3842.19,"f3":0.31,"f18":3830.45});
        let i = parse_index_item(&v);
        assert_eq!(i.name, "上证指数");
        assert_eq!(i.main_net_inflow, None);
        assert!((i.price - 3842.19).abs() < 1e-9);
    }
}

/// 板块分页的终止判定（抽成纯函数便于单测）。
///
/// 关键点：**不能**把 `total == 0` 当成"已经取全"。
/// 服务端偶尔不返回 `data.total`，若按 `collected >= total` 直接判定，
/// 第 1 页就会被当成最后一页 —— 那正是"概念板块只剩 200 个"的老毛病。
fn should_stop_paging(collected: usize, total: usize, page_got: usize, page_size: usize) -> bool {
    // 服务端明确给出了总数，且已经取够
    if total > 0 && collected >= total {
        return true;
    }
    // 本页不足一页 → 已是最后一页（同时涵盖"本页为空"的情况）
    page_got < page_size
}

#[cfg(test)]
mod paging_tests {
    use super::should_stop_paging;

    /// 服务端声明 450 个概念板块：必须一直翻到取够，不能停在第一页
    #[test]
    fn keeps_paging_until_total_reached() {
        const SIZE: usize = 100;
        assert!(!should_stop_paging(100, 450, 100, SIZE));
        assert!(!should_stop_paging(200, 450, 100, SIZE));
        assert!(!should_stop_paging(400, 450, 100, SIZE));
        assert!(should_stop_paging(450, 450, 50, SIZE));
    }

    /// 服务端没给 total（返回 0）时**不能**在第 1 页就停 —— 这是老 bug 的根源
    #[test]
    fn missing_total_does_not_stop_after_first_page() {
        const SIZE: usize = 100;
        assert!(
            !should_stop_paging(100, 0, 100, SIZE),
            "total 缺失时若按 collected>=total 判定，第 1 页就会被当成最后一页"
        );
        assert!(!should_stop_paging(300, 0, 100, SIZE));
        assert!(should_stop_paging(350, 0, 50, SIZE));
    }

    /// 空页与短页都要终止，避免空转
    #[test]
    fn stops_on_empty_or_short_page() {
        const SIZE: usize = 100;
        assert!(should_stop_paging(0, 0, 0, SIZE), "空页必须终止");
        assert!(should_stop_paging(30, 86, 30, SIZE), "短页即最后一页");
        assert!(should_stop_paging(86, 86, 86, SIZE), "取够即终止");
    }
}
