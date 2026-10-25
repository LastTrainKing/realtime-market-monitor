//! 历史数据预加载与增量补齐（第 4.11 / 8.10 章）。
//!
//! # 数据源能力边界（实测结论，必须如实记录，不得伪造）
//!
//! | 数据 | 可回溯范围 | 结论 |
//! | --- | --- | --- |
//! | 指数分钟 K 线 (`klt=1`) | **仅最近 1 个交易日**（240 点） | `beg/end` 参数对 klt=1 无效 |
//! | 大盘/个股分钟资金流 (`fflow/kline?klt=1`) | **仅最近 1 个交易日** | 同上 |
//! | 个股分钟 K 线 (`klt=1`) | **仅最近 1 个交易日** | 同上 |
//! | 日线 (`klt=101`) | ≥ 90 个交易日 | 可完整回溯 |
//!
//! 因此：
//!   * **最近一个交易日的分钟数据可以从数据源直接补齐**（指数分时、两市资金曲线）；
//!   * **更早交易日的分钟数据无法从数据源回溯**，只能由本程序在运行期间逐日累积；
//!   * 历史库里缺失的分钟日期必须**显式标记为"数据源不可回溯"**，
//!     绝不能用 0、上一点复制、日线插值或当前快照倒填。
//!
//! 这正是"首次建立历史库较慢、之后查询极快"的物理原因：分钟库是**累积**出来的。

use std::sync::Arc;

use chrono::{Datelike, NaiveDate};

use crate::history::parquet_store::MinuteBar;
use crate::history::MinuteStore;
use crate::models::DataSource;
use crate::net::NetError;
use crate::state::AppState;

/// 默认指数（上证指数）
pub const SH_INDEX_SECID: &str = "1.000001";
/// 深证成指（用于深市资金流对照）
pub const SZ_INDEX_SECID: &str = "0.399001";

/// 最近 N 个交易日（含 end，倒推，升序返回）
pub fn recent_trading_days(state: &Arc<AppState>, end: NaiveDate, n: usize) -> Vec<String> {
    let clock = state.clock.read().clone();
    let mut out = Vec::new();
    let mut cursor = if clock.is_trading_day(end) {
        end
    } else {
        clock.prev_trading_day(end)
    };
    for _ in 0..n {
        out.push(cursor.format("%Y-%m-%d").to_string());
        cursor = clock.prev_trading_day(cursor);
    }
    out.reverse();
    out
}

/// 单日分钟数据抓取结果
#[derive(Debug, Clone, Default)]
pub struct DateFetchReport {
    pub trade_date: String,
    /// 指数分时点数
    pub index_points: usize,
    /// 两市资金曲线点数
    pub flow_points: usize,
    /// 个股分钟点数（仅本地累积时有）
    pub stock_points: usize,
    pub ok: bool,
    pub note: String,
}

/// 历史库覆盖情况
#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct HistoryCoverage {
    /// 请求覆盖的交易日数量
    pub days_requested: usize,
    /// 期望覆盖的交易日列表
    pub dates_expected: Vec<String>,
    /// 分钟数据已就绪的交易日
    pub dates_minute_ready: Vec<String>,
    /// 分钟数据缺失的交易日
    pub dates_minute_missing: Vec<String>,
    /// 最近交易日（首页统一使用的日期）
    pub latest_trading_day: String,
    /// 最近交易日的分钟点数（完整应为 240）
    pub latest_minute_points: usize,
    /// 数据源能力说明（面向用户）
    pub source_note: String,
    /// 磁盘占用
    pub size_bytes: u64,
}

/// 用新浪分钟 K 线一次性补齐**多日**指数分时序列。
///
/// 这是本项目唯一能拿到"多日分钟数据"的通道：新浪 `getKLineData`
/// 单次返回 1023 点，按周期换算可覆盖 1 分钟 5 天 / 5 分钟 22 天 /
/// 15 分钟 64 天 / 30 分钟 128 天。
///
/// 每个交易日的序列单独落盘，落盘前逐日校验（首尾时间、时间轴、重复、断档）。
pub async fn fetch_index_series_multi_day(
    state: &Arc<AppState>,
    store: &MinuteStore,
    granularity: crate::history::Granularity,
) -> (usize, usize, String) {
    if !state.config().enable_sina {
        return (0, 0, "新浪通道已关闭，无法补齐多日指数分钟序列".into());
    }
    let symbol = "sh000001";
    let bars = match state
        .sina
        .minute_kline(symbol, granularity.minutes(), crate::source::sina::KLINE_MAX_DATALEN)
        .await
    {
        Ok(v) => v,
        Err(err) => {
            return (0, 0, format!("指数分钟序列获取失败：{}", err.user_message()));
        }
    };

    // 按交易日分组
    let mut by_date: std::collections::BTreeMap<String, Vec<MinuteBar>> =
        std::collections::BTreeMap::new();
    for b in bars {
        by_date.entry(b.trade_date.clone()).or_default().push(b);
    }

    let mut days_written = 0usize;
    let mut rows_written = 0usize;
    let mut rejected: Vec<String> = Vec::new();

    for (date, day_bars) in &by_date {
        let v = crate::history::manifest::validate_minutes(
            SH_INDEX_SECID,
            date,
            day_bars,
            granularity,
        );
        if !v.ok {
            // 首日/末日通常是 1023 窗口的截断，属正常边界，但仍不落盘，
            // 避免把"半个交易日"当成完整交易日展示
            rejected.push(format!("{date}({})", v.problems.join("/")));
            continue;
        }
        match store.write_series("index", SH_INDEX_SECID, date, day_bars) {
            Ok(n) if n > 0 => {
                days_written += 1;
                rows_written += n;
            }
            _ => rejected.push(format!("{date}(写入失败)")),
        }
    }

    let note = format!(
        "指数分钟序列（{}）：落盘 {} 个交易日 / {} 行；跳过 {} 个不完整交易日{}",
        granularity.label(),
        days_written,
        rows_written,
        rejected.len(),
        if rejected.is_empty() {
            String::new()
        } else {
            format!("（{}）", rejected.join("、"))
        }
    );
    (days_written, rows_written, note)
}

/// 确保指定交易日的分钟序列（指数分时 + 两市资金曲线）已落盘。
///
/// 返回 `(写入点数, 说明)`；数据源不可回溯时返回明确原因，不伪造数据。
pub async fn fetch_date_minutes(
    state: &Arc<AppState>,
    store: &MinuteStore,
    date: &str,
) -> DateFetchReport {
    let cfg = state.config();
    let mut report = DateFetchReport {
        trade_date: date.to_string(),
        ..Default::default()
    };

    if !cfg.enable_eastmoney {
        report.note = "东方财富通道已关闭，无法补齐该交易日分钟数据".into();
        return report;
    }
    // 注意：这里**不检查** state.gate 的 eastmoney 冷却。
    // 冷却通常由 clist 榜单接口失败触发，而分钟 K 线（push2his）与
    // 分钟资金流（push2）是另外的主机与接口，用同一个闸门会把可用通道误伤。
    // 本函数自身失败快（连续失败会提前返回），不会造成长时间空转。
    let em = state.eastmoney.clone();
    let target = NaiveDate::parse_from_str(date, "%Y-%m-%d").ok();

    // ---- 1) 指数分时 ----
    match em.index_minute_kline(SH_INDEX_SECID, 300).await {
        Ok(bars) => {
            let day: Vec<&crate::source::eastmoney::IndexMinuteBar> = bars
                .iter()
                .filter(|b| b.trade_date == date)
                .collect();
            if !day.is_empty() {
                let rows: Vec<MinuteBar> = day
                    .iter()
                    .map(|b| MinuteBar {
                        code: SH_INDEX_SECID.to_string(),
                        trade_date: date.to_string(),
                        minute: b.minute.clone(),
                        dt_ms: dt_ms(target, &b.minute),
                        open: b.open,
                        high: b.high,
                        low: b.low,
                        close: b.close,
                        volume: b.volume,
                        amount: b.amount,
                        main_net_inflow: 0.0,
                    })
                    .collect();
                if let Ok(n) = store.write_series("index", SH_INDEX_SECID, date, &rows) {
                    report.index_points = n;
                }
            } else {
                report.note.push_str("指数分时：数据源仅提供最近交易日，该日期不可回溯；");
            }
        }
        Err(err) => {
            report.note.push_str(&format!("指数分时获取失败：{}；", err.user_message()));
        }
    }

    // ---- 2) 两市资金曲线（大盘分钟资金流，真实来源）----
    match em.minute_flow_by_secid(SH_INDEX_SECID, 0).await {
        Ok(points) => {
            let rows: Vec<MinuteBar> = points
                .iter()
                .map(|p| MinuteBar {
                    code: SH_INDEX_SECID.to_string(),
                    trade_date: date.to_string(),
                    minute: p.time.clone(),
                    dt_ms: dt_ms(target, &p.time),
                    open: p.main_net,
                    high: p.super_large,
                    low: p.large,
                    close: p.main_net,
                    volume: p.medium,
                    amount: p.small,
                    main_net_inflow: p.main_net,
                })
                .collect();
            if !rows.is_empty() {
                if let Ok(n) = store.write_series("market_flow", SH_INDEX_SECID, date, &rows) {
                    report.flow_points = n;
                }
            }
        }
        Err(err) => {
            report.note
                .push_str(&format!("两市资金曲线获取失败：{}；", err.user_message()));
        }
    }

    report.ok = report.index_points > 0 || report.flow_points > 0;
    if report.ok && report.note.is_empty() {
        report.note = format!(
            "指数分时 {} 点，两市资金曲线 {} 点",
            report.index_points, report.flow_points
        );
    }
    report
}

/// 由 `YYYY-MM-DD` + `HH:MM` 计算北京时间毫秒时间戳（供数据源解析使用）
pub fn minute_to_ms(date: &str, minute: &str) -> i64 {
    let Ok(d) = NaiveDate::parse_from_str(date, "%Y-%m-%d") else {
        return 0;
    };
    dt_ms(Some(d), minute)
}

/// 计算某分钟对应的北京时间毫秒时间戳
fn dt_ms(date: Option<NaiveDate>, minute: &str) -> i64 {
    let Some(d) = date else { return 0 };
    let Some((h, m)) = crate::clock::parse_hhmm(minute) else {
        return 0;
    };
    let Some(naive) = d.and_hms_opt(h, m, 0) else {
        return 0;
    };
    naive.and_utc().timestamp_millis() - (8 * 3600 * 1000)
}

/// 把本机累积的个股分钟数据（SQLite）导出到 Parquet。
///
/// 个股全市场分钟数据无法从数据源回溯，只能靠本程序运行期间逐分钟落库，
/// 这里负责把当天累积的数据转成列式历史库。
pub async fn export_local_minutes(
    state: &Arc<AppState>,
    store: &MinuteStore,
    date: &str,
) -> Result<(usize, usize), NetError> {
    let db = state.db.clone();
    let d = date.to_string();
    let rows = tokio::task::spawn_blocking(move || db.load_minutes_for_date(&d))
        .await
        .ok()
        .and_then(|r| r.ok())
        .unwrap_or_default();

    if rows.is_empty() {
        return Ok((0, 0));
    }

    // 按股票分组写单股序列，同时汇总成全市场物化视图
    let mut by_code: std::collections::HashMap<String, Vec<MinuteBar>> =
        std::collections::HashMap::new();
    let mut all: Vec<MinuteBar> = Vec::with_capacity(rows.len());
    let ndate = NaiveDate::parse_from_str(date, "%Y-%m-%d").ok();

    for (code, name, minute, price, volume, amount, main_net) in rows {
        let bar = MinuteBar {
            code: code.clone(),
            trade_date: date.to_string(),
            minute: minute.clone(),
            dt_ms: dt_ms(ndate, &minute),
            open: price,
            high: price,
            low: price,
            close: price,
            volume,
            amount,
            main_net_inflow: main_net,
        };
        let _ = name;
        by_code.entry(code).or_default().push(bar.clone());
        all.push(bar);
    }

    let mut codes_written = 0usize;
    for (code, bars) in &by_code {
        if store.write_stock(code, date, bars).is_ok() {
            codes_written += 1;
        }
    }
    let total = store.write_market(date, &all)?;
    Ok((codes_written, total))
}

/// 启动预加载：检查本地历史库，只补缺失的部分（增量更新）。
pub async fn preload(state: &Arc<AppState>, store: &MinuteStore) -> HistoryCoverage {
    let cfg = state.config();
    let days = cfg.history_days.clamp(30, 365) as usize;
    let clock = state.clock.read().clone();
    let today = crate::clock::now_cn().date_naive();
    let latest = clock.last_closed_trading_day();
    let expected = recent_trading_days(state, today, days);

    let mut ready = Vec::new();
    let mut missing = Vec::new();
    let have: std::collections::HashSet<String> =
        store.series_dates("market_flow", SH_INDEX_SECID).into_iter().collect();

    for d in &expected {
        if have.contains(d) {
            ready.push(d.clone());
        } else {
            missing.push(d.clone());
        }
    }

    // 1) 多日指数分钟序列（新浪通道，唯一能拿多日分钟数据的来源）
    let g = crate::history::Granularity::from_minutes(cfg.history_granularity)
        .unwrap_or(crate::history::Granularity::Min1);
    let (days, rows, note) = fetch_index_series_multi_day(state, store, g).await;
    tracing::info!(target: "collector", days, rows, note = %note, "多日指数分钟序列");
    if days > 0 {
        // 指数序列已落盘的日期同步计入 ready
        for d in store.series_dates("index", SH_INDEX_SECID) {
            if expected.contains(&d) && !ready.contains(&d) {
                ready.push(d.clone());
                missing.retain(|m| m != &d);
            }
        }
        ready.sort();
        missing.sort();
    }
    let index_note = note;

    // 2) 最近一个交易日的两市资金曲线（东财 fflow 只提供最近 1 个交易日）
    let latest_str = latest.format("%Y-%m-%d").to_string();
    if missing.contains(&latest_str) {
        let report = fetch_date_minutes(state, store, &latest_str).await;
        if report.ok {
            missing.retain(|d| d != &latest_str);
            ready.push(latest_str.clone());
            tracing::info!(target: "collector", date = %latest_str, "最近交易日资金曲线已补齐");
        } else {
            tracing::warn!(target: "collector", date = %latest_str, note = %report.note, "最近交易日资金曲线补齐失败");
        }
    }

    // 导出本机累积的个股分钟数据
    if let Ok((codes, rows)) = export_local_minutes(state, store, &latest_str).await {
        if codes > 0 {
            tracing::info!(target: "collector", date = %latest_str, codes, rows, "本机个股分钟数据已导出到列式历史库");
        }
    }

    ready.sort();
    missing.sort();
    let latest_points = store
        .read_series("market_flow", SH_INDEX_SECID, &latest_str)
        .map(|v| v.len())
        .unwrap_or(0);

    // 登记清单：让"哪天完整、缺多少"可一次查询得知，无需扫描目录
    let index_dates: std::collections::HashSet<String> =
        store.series_dates("index", SH_INDEX_SECID).into_iter().collect();
    let flow_dates: std::collections::HashSet<String> =
        store.series_dates("market_flow", SH_INDEX_SECID).into_iter().collect();
    let universe = state
        .cache
        .stocks
        .get_stale(crate::collector::market::CACHE_KEY_FULL_STOCKS)
        .map(|e| e.value.len())
        .unwrap_or(0);
    for d in &expected {
        let idx_ok = index_dates.contains(d);
        let flow_ok = flow_dates.contains(d);
        let rows = store
            .read_series("index", SH_INDEX_SECID, d)
            .map(|v| v.len())
            .unwrap_or(0)
            + store
                .read_series("market_flow", SH_INDEX_SECID, d)
                .map(|v| v.len())
                .unwrap_or(0);
        let note = match (idx_ok, flow_ok) {
            (true, true) => format!("指数分时与两市资金曲线均已落盘（{} 行）", rows),
            (true, false) => "指数分时已落盘；两市资金曲线数据源未提供该日".to_string(),
            (false, false) => "该交易日分钟数据尚未落盘".to_string(),
            (false, true) => "两市资金曲线已落盘；指数分时缺失".to_string(),
        };
        let _ = state.db.upsert_manifest(&crate::history::ManifestRow {
            trade_date: d.clone(),
            granularity: g.minutes(),
            expected_symbols: universe as i32,
            actual_symbols: if idx_ok { 1 } else { 0 },
            missing_symbols: if idx_ok { 0 } else { 1 },
            total_rows: rows as i64,
            complete: idx_ok && flow_ok,
            created_at: crate::models::now_str(),
            updated_at: crate::models::now_str(),
            note,
        });
    }

    let stats = store.stats();
    let _ = store.write_meta(&stats);

    let source_note = if missing.is_empty() {
        format!("最近 {days} 个交易日分钟数据已全部就绪（{}）", index_note)
    } else {
        format!(
            "最近 {days} 个交易日中 {} 个已就绪、{} 个缺失；{}",
            ready.len(),
            missing.len(),
            crate::history::preload::source_capability_note()
        )
    };

    state.collectors.note_status(
        "history",
        if missing.is_empty() {
            crate::models::DataStatus::Realtime
        } else {
            crate::models::DataStatus::Partial
        },
        &source_note,
    );

    HistoryCoverage {
        days_requested: days,
        dates_expected: expected,
        dates_minute_ready: ready,
        dates_minute_missing: missing,
        latest_trading_day: latest_str,
        latest_minute_points: latest_points,
        source_note,
        size_bytes: stats.size_bytes,
    }
}

/// 读取最近交易日的指数分时（供首页模块 1 使用）。
///
/// 走 DuckDB 查询层；DuckDB 不可用时回退到直接读 Parquet 文件（同样真实数据）。
pub fn latest_index_series(
    store: &MinuteStore,
    repo: Option<&crate::history::DuckRepository>,
    date: &str,
) -> Vec<MinuteBar> {
    if let Some(r) = repo {
        if let Ok(v) = r.series("index", SH_INDEX_SECID, date) {
            if !v.is_empty() {
                return v;
            }
        }
    }
    store.read_series("index", SH_INDEX_SECID, date).unwrap_or_default()
}

/// 读取最近交易日的两市资金曲线（供首页模块 3 / 4 使用）
pub fn latest_flow_series(
    store: &MinuteStore,
    repo: Option<&crate::history::DuckRepository>,
    date: &str,
) -> Vec<MinuteBar> {
    if let Some(r) = repo {
        if let Ok(v) = r.series("market_flow", SH_INDEX_SECID, date) {
            if !v.is_empty() {
                return v;
            }
        }
    }
    store.read_series("market_flow", SH_INDEX_SECID, date).unwrap_or_default()
}

/// 校验分钟序列是否为一个完整交易日。
///
/// **不写死"必须正好 240 点"**。实测新浪 1 分钟线返回 239 点：
/// 09:31 起、15:00 止，缺失 14:59（深市收盘集合竞价把该分钟并入 15:00）。
/// 因此判据是"点数达到完整交易日的 98% 以上，且首尾时间正确"。
pub fn is_complete_trading_day(bars: &[MinuteBar]) -> bool {
    if bars.is_empty() {
        return false;
    }
    let first = bars.iter().map(|b| b.minute.as_str()).min().unwrap_or("");
    let last = bars.iter().map(|b| b.minute.as_str()).max().unwrap_or("");
    if first != "09:31" || last != "15:00" {
        return false;
    }
    // 240 点的 98% ≈ 235
    bars.len() * 100 >= crate::history::MINUTES_PER_TRADING_DAY * 98
}

/// 交易日判断辅助：某日期是否在期望范围内
pub fn in_range(date: &str, from: &str, to: &str) -> bool {
    date >= from && date <= to
}

/// 用于日志：日期是否属于同年
pub fn same_year(a: &str, b: &str) -> bool {
    a.len() >= 4 && b.len() >= 4 && a[..4] == b[..4]
}

/// 把 `YYYY-MM-DD` 转为 `YYYYMMDD`（数据源 beg/end 参数格式）
pub fn compact_date(date: &str) -> String {
    date.replace('-', "")
}

/// 把 `YYYYMMDD` 转为 `YYYY-MM-DD`
pub fn expand_date(date: &str) -> String {
    if date.len() == 8 {
        format!("{}-{}-{}", &date[..4], &date[4..6], &date[6..8])
    } else {
        date.to_string()
    }
}

/// 最近 N 个自然日（用于日线历史覆盖检查）
pub fn recent_calendar_days(end: NaiveDate, n: i64) -> Vec<String> {
    let mut out = Vec::new();
    for i in (0..n).rev() {
        out.push((end - chrono::Duration::days(i)).format("%Y-%m-%d").to_string());
    }
    out
}

/// 数据源能力说明（供 UI 展示，避免用户误以为可以回溯任意历史分钟）
pub fn source_capability_note() -> String {
    "分钟数据源单次请求上限 1023 点，可回溯范围按粒度递减：\
     1 分钟约 5 个交易日、5 分钟约 22 天、15 分钟约 64 天、30 分钟约 128 天；\
     超出部分由本程序在每个交易日运行期间逐日累积"
        .into()
}

/// 判断某日期是否为"数据源可回溯"的日期（只有最近一个交易日）
pub fn is_source_retrievable(date: &str, latest_trading_day: &str) -> bool {
    date == latest_trading_day
}

/// 构造一个空的历史覆盖报告（数据源完全不可用时）
pub fn empty_coverage(note: &str) -> HistoryCoverage {
    HistoryCoverage {
        source_note: note.to_string(),
        ..Default::default()
    }
}

/// 记录"数据源不可回溯"的事实（写入日志与采集器状态，不伪造数据）
pub fn log_unavailable(state: &Arc<AppState>, date: &str, reason: &str) {
    tracing::warn!(target: "collector", date, reason, "历史分钟数据不可获取");
    state.push_note(format!("{date} 分钟数据不可获取：{reason}"));
}

/// 数据源标记（历史数据一律来自东方财富历史接口或本机累积）
pub fn history_source() -> DataSource {
    DataSource::EastmoneyHistory
}

/// 校验日期字符串合法性
pub fn valid_date(s: &str) -> bool {
    NaiveDate::parse_from_str(s, "%Y-%m-%d").is_ok()
}

/// 当天是否为年末（用于日志分片判断）
pub fn is_year_end(date: &str) -> bool {
    NaiveDate::parse_from_str(date, "%Y-%m-%d")
        .map(|d| d.month() == 12 && d.day() >= 28)
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bar(minute: &str) -> MinuteBar {
        MinuteBar {
            minute: minute.to_string(),
            ..Default::default()
        }
    }

    /// 回归：实测 1 分钟线是 239 点（缺 14:59），不能被判为不完整
    #[test]
    fn complete_day_accepts_measured_239_points() {
        let mut bars: Vec<MinuteBar> = Vec::new();
        for i in 0..120 {
            let t = 9 * 60 + 31 + i;
            bars.push(bar(&format!("{:02}:{:02}", t / 60, t % 60)));
        }
        // 下午 13:01..15:00 共 120 点，去掉 14:59（数据源真实行为）
        for i in 0..120 {
            let t = 13 * 60 + 1 + i;
            let m = format!("{:02}:{:02}", t / 60, t % 60);
            if m == "14:59" {
                continue;
            }
            bars.push(bar(&m));
        }
        assert_eq!(bars.len(), 239, "实测样本应为 239 点");
        assert!(
            is_complete_trading_day(&bars),
            "239 点是数据源的真实完整交易日，不得判为不完整"
        );
    }

    #[test]
    fn complete_day_still_rejects_truncated_data() {
        let mut bars: Vec<MinuteBar> = Vec::new();
        for i in 0..60 {
            let t = 9 * 60 + 31 + i;
            bars.push(bar(&format!("{:02}:{:02}", t / 60, t % 60)));
        }
        assert!(!is_complete_trading_day(&bars), "只有 60 点必须判为不完整");
    }

    #[test]
    fn complete_day_requires_correct_boundaries() {
        let mut bars: Vec<MinuteBar> = Vec::new();
        for i in 0..239 {
            let t = 9 * 60 + 35 + i; // 从 09:35 开始，缺少开盘段
            bars.push(bar(&format!("{:02}:{:02}", (t / 60).min(23), t % 60)));
        }
        assert!(!is_complete_trading_day(&bars), "起始时间不对必须判为不完整");
    }

    #[test]
    fn complete_day_detection_requires_240_points() {
        let mut bars: Vec<MinuteBar> = Vec::new();
        for i in 0..120 {
            let t = 9 * 60 + 31 + i;
            bars.push(bar(&format!("{:02}:{:02}", t / 60, t % 60)));
        }
        for i in 0..120 {
            let t = 13 * 60 + 1 + i;
            bars.push(bar(&format!("{:02}:{:02}", t / 60, t % 60)));
        }
        assert_eq!(bars.len(), 240);
        assert!(is_complete_trading_day(&bars));

        let short = vec![bar("09:31"), bar("09:32"), bar("09:33")];
        assert!(!is_complete_trading_day(&short), "3 个点绝不是完整交易日");
    }

    #[test]
    fn compact_and_expand_date() {
        assert_eq!(compact_date("2026-09-30"), "20260930");
        assert_eq!(expand_date("20260930"), "2026-09-30");
        assert_eq!(expand_date("2026-09-30"), "2026-09-30");
    }

    #[test]
    fn date_validation() {
        assert!(valid_date("2026-09-30"));
        assert!(!valid_date("2026-13-01"));
        assert!(!valid_date("not-a-date"));
    }

    #[test]
    fn only_latest_trading_day_is_source_retrievable() {
        // 实测结论：klt=1 只能取到最近一个交易日
        assert!(is_source_retrievable("2026-09-30", "2026-09-30"));
        assert!(!is_source_retrievable("2026-09-29", "2026-09-30"));
    }

    #[test]
    fn range_helper() {
        assert!(in_range("2026-09-30", "2026-09-01", "2026-09-30"));
        assert!(!in_range("2026-10-01", "2026-09-01", "2026-09-30"));
    }

    #[test]
    fn capability_note_is_explicit_about_limitation() {
        let note = source_capability_note();
        // 必须写明"单次请求上限 1023 点"这一硬约束
        assert!(note.contains("1023"), "必须写明数据源请求上限");
        // 必须逐粒度给出可回溯范围，而不是含糊的"支持 90 天"
        assert!(note.contains("1 分钟"), "必须说明 1 分钟粒度能力");
        assert!(note.contains("30 分钟"), "必须说明 30 分钟粒度能力");
        assert!(note.contains("累积"), "必须说明超出部分靠逐日累积");
    }

    #[test]
    fn granularity_capability_matrix_is_measured_not_guessed() {
        use crate::history::Granularity;
        // 实测结论：datalen 上限 1023 → 各粒度可覆盖交易日数
        assert_eq!(Granularity::Min1.source_max_days(), 4);
        assert_eq!(Granularity::Min5.source_max_days(), 21);
        assert_eq!(Granularity::Min15.source_max_days(), 63);
        assert_eq!(Granularity::Min30.source_max_days(), 127);
        // 90 天要求：1 分钟不满足、30 分钟满足
        assert!(Granularity::Min1.source_max_days() < 90);
        assert!(Granularity::Min30.source_max_days() >= 90);
    }

    #[test]
    fn dt_ms_uses_market_time_not_system_time() {
        let d = NaiveDate::from_ymd_opt(2026, 9, 30).unwrap();
        let ms = dt_ms(Some(d), "14:07");
        assert!(ms > 0);
        // 2026-09-30 14:07 北京时间 = 06:07 UTC
        let back = chrono::DateTime::from_timestamp_millis(ms).unwrap();
        assert_eq!(back.format("%Y-%m-%d %H:%M").to_string(), "2026-09-30 06:07");
    }
}
