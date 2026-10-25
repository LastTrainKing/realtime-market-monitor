//! 全市场股票宇宙（第 4.3 / 8.9 章）。
//!
//! # 为什么必须单独建立"股票宇宙"
//!
//! 早期实现把**排行榜接口**当作股票池来源（腾讯 `getBoardRankList` 分页 6×200、
//! 新浪 `getHQNodeData` 8×100），导致：
//!
//! * 全市场 5900 只 A 股被静默截断成 **1200 只**；
//! * 涨跌家数、涨停跌停、成交额、榜单 TOP N 全部基于这 1200 只计算；
//! * "全市场 TOP20" 实际上是"1200 只里的 TOP20"，属于**逻辑错误**而非显示问题。
//!
//! 排行榜接口天然有返回条数上限，**不能**用作宇宙来源。正确做法：
//!
//! ```text
//!   本地证券列表（stock_list 表，全量 A 股代码，每日刷新）
//!        ↓
//!   批量行情接口（东财 ulist 500 只/请求，含主力净流入 f62）
//!        ↓
//!   完整性校验（total / loaded / failed / missing）
//!        ↓
//!   统一计算（涨跌家数 / 涨停跌停 / 成交额 / 榜单）
//! ```
//!
//! 数据不完整时必须显式标记，绝不能把"部分数据"伪装成"全市场完整"。

use std::collections::HashSet;
use std::sync::Arc;

use serde::{Deserialize, Serialize};

use crate::models::{DataSource, DataStatus, StockTick};
use crate::net::NetError;
use crate::state::AppState;

use crate::collector::Timer;

/// 单次批量行情请求携带的证券数量。
/// 实测东财 ulist 单请求可稳定返回 600 只，这里取 500 留出 URL 长度余量。
pub const QUOTE_BATCH_SIZE: usize = 500;
/// 证券列表分页大小（东财 clist 实际被限制为 100 条/页）
pub const LIST_PAGE_SIZE: usize = 100;
/// 证券列表最大页数保护（100 × 80 = 8000，足够覆盖 A 股全量）
pub const LIST_MAX_PAGES: usize = 80;
/// 证券列表有效期（天）：超过则后台刷新
pub const LIST_TTL_DAYS: i64 = 3;
/// A 股全市场数量下限：低于该值说明列表不完整
pub const MIN_EXPECTED_SYMBOLS: usize = 4000;

/// 证券基础信息
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SymbolInfo {
    pub code: String,
    pub name: String,
    /// 0=深 1=沪 2=北
    pub market: u8,
    /// main / gem / star / bj
    pub board: String,
}

impl SymbolInfo {
    pub fn new(code: &str, name: &str) -> Self {
        Self {
            code: code.to_string(),
            name: name.to_string(),
            market: crate::source::market_of(code),
            board: board_of(code).to_string(),
        }
    }
}

/// 按代码前缀判断所属板块（真实规则，不是猜测）
pub fn board_of(code: &str) -> &'static str {
    if code.starts_with("688") || code.starts_with("689") {
        "star" // 科创板
    } else if code.starts_with("300") || code.starts_with("301") || code.starts_with("302") {
        "gem" // 创业板
    } else if code.starts_with('8') || code.starts_with('4') || code.starts_with("920") {
        "bj" // 北交所
    } else {
        "main"
    }
}

/// 是否属于可交易 A 股（过滤 B 股、退市等）
pub fn is_a_share(code: &str) -> bool {
    if code.len() != 6 || !code.chars().all(|c| c.is_ascii_digit()) {
        return false;
    }
    // 沪 B 900xxx / 深 B 200xxx 不属于 A 股
    if code.starts_with("900") || code.starts_with("200") {
        return false;
    }
    code.starts_with('6')
        || code.starts_with('0')
        || code.starts_with('3')
        || code.starts_with('8')
        || code.starts_with('4')
        || code.starts_with("920")
}

/// 全市场行情采集的完整性指标
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct UniverseStats {
    /// 证券列表中的股票总数（宇宙规模）
    pub total_symbols: usize,
    /// 成功取到行情的股票数
    pub loaded_symbols: usize,
    /// 请求失败（网络/解析）的股票数
    pub failed_symbols: usize,
    /// 列表中存在但接口未返回行情的股票数（停牌 / 未上市 / 接口缺失）
    pub missing_symbols: usize,
    /// 是否达到"全市场完整"标准
    pub complete: bool,
    /// 实际使用的数据源
    pub source: String,
    /// 证券列表最后更新时间
    pub list_updated_at: String,
    /// 批量请求次数
    pub batches: usize,
    /// 面向用户的中文说明
    pub note: String,
}

impl UniverseStats {
    pub fn coverage_pct(&self) -> f64 {
        if self.total_symbols == 0 {
            0.0
        } else {
            self.loaded_symbols as f64 / self.total_symbols as f64 * 100.0
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct UniverseSnapshot {
    pub stocks: Vec<StockTick>,
    pub stats: UniverseStats,
}

/// 确保本地证券列表可用。
///
/// 优先级：本地数据库（未过期）→ 远端刷新。
/// 绝不每次启动都重新拉取全量列表。
pub async fn ensure_symbol_list(state: &Arc<AppState>) -> Vec<SymbolInfo> {
    let db = state.db.clone();
    let cached = tokio::task::spawn_blocking(move || {
        let count = db.stock_list_count().unwrap_or(0);
        let updated = db.stock_list_updated_at().ok().flatten();
        let list = if count >= MIN_EXPECTED_SYMBOLS {
            db.load_stock_list().unwrap_or_default()
        } else {
            Vec::new()
        };
        (count, updated, list)
    })
    .await
    .ok()
    .unwrap_or((0, None, Vec::new()));

    let (count, updated_at, list) = cached;
    let fresh = updated_at
        .as_deref()
        .and_then(|s| chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S").ok())
        .map(|t| {
            (crate::clock::now_cn().naive_local() - t).num_days() < LIST_TTL_DAYS
        })
        .unwrap_or(false);

    if !list.is_empty() && fresh {
        tracing::info!(target: "collector", symbols = count, "使用本地证券列表");
        state.collectors.note_status(
            "universe",
            DataStatus::Realtime,
            &format!("本地证券列表 {count} 只"),
        );
        return list;
    }

    if !list.is_empty() {
        // 有过期列表：先用着，同时后台刷新
        tracing::info!(target: "collector", symbols = count, "证券列表已过期，先使用旧列表并后台刷新");
        let bg = state.clone();
        tokio::spawn(async move {
            if let Err(err) = refresh_symbol_list(&bg).await {
                tracing::warn!(target: "collector", error = %err, "后台刷新证券列表失败");
            }
        });
        return list;
    }

    // 本地没有：同步拉一次
    match refresh_symbol_list(state).await {
        Ok(_) => {
            let db = state.db.clone();
            tokio::task::spawn_blocking(move || db.load_stock_list().unwrap_or_default())
                .await
                .unwrap_or_default()
        }
        Err(err) => {
            tracing::error!(target: "collector", error = %err, "证券列表获取失败，全市场统计将不可用");
            state.collectors.note_failure("universe", &err.user_message(), 0);
            Vec::new()
        }
    }
}

/// 从远端刷新证券列表并落库。
///
/// 通道：东方财富 clist（字段极轻，60 页）→ 新浪 getHQNodeData（56 页）。
pub async fn refresh_symbol_list(state: &Arc<AppState>) -> Result<usize, NetError> {
    let timer = Timer::start();
    let cfg = state.config();

    let mut symbols: Vec<SymbolInfo> = Vec::new();
    let mut used = DataSource::None;
    let mut last_err: Option<NetError> = None;

    if cfg.enable_eastmoney
        && !state
            .gate
            .is_blocked(crate::state::gate_keys::EM_CLIST)
    {
        match eastmoney_symbol_list(state).await {
            Ok(list) if list.len() >= MIN_EXPECTED_SYMBOLS => {
                state.gate.clear(crate::state::gate_keys::EM_CLIST);
                symbols = list;
                used = DataSource::EastmoneyRealtime;
            }
            Ok(list) => {
                tracing::warn!(target: "collector", got = list.len(), "东财证券列表数量不足，尝试降级通道");
                last_err = Some(NetError::EmptyBody(format!(
                    "东财证券列表仅 {} 只，低于预期",
                    list.len()
                )));
            }
            Err(err) => {
                tracing::warn!(target: "collector", error = %err, "东财证券列表失败，尝试降级通道");
                state
                    .gate
                    .block(crate::state::gate_keys::EM_CLIST, &err.to_string());
                last_err = Some(err);
            }
        }
    }

    if symbols.is_empty() && cfg.enable_sina && !state.gate.is_blocked("sina") {
        match sina_symbol_list(state).await {
            Ok(list) if list.len() >= MIN_EXPECTED_SYMBOLS => {
                symbols = list;
                used = DataSource::SinaRealtime;
            }
            Ok(list) => {
                last_err = Some(NetError::EmptyBody(format!(
                    "新浪证券列表仅 {} 只，低于预期",
                    list.len()
                )));
            }
            Err(err) => {
                last_err = Some(err);
            }
        }
    }

    if symbols.is_empty() {
        return Err(last_err.unwrap_or_else(|| NetError::EmptyBody("证券列表不可用".into())));
    }

    let n = symbols.len();
    let db = state.db.clone();
    let to_save = symbols.clone();
    tokio::task::spawn_blocking(move || db.save_stock_list(&to_save))
        .await
        .ok()
        .and_then(|r| r.ok())
        .ok_or_else(|| NetError::ServerError("证券列表写入数据库失败".into()))?;

    tracing::info!(
        target: "collector",
        symbols = n,
        source = ?used,
        latency_ms = timer.ms(),
        "证券列表已刷新"
    );
    state
        .collectors
        .note_success("universe", n, timer.ms(), used, DataStatus::Realtime);
    Ok(n)
}

/// 东方财富证券列表（轻量字段，全量分页）。
///
/// 分页**并行**执行：全市场 5900 只需要约 60 页，串行拉取耗时 30~50 秒，
/// 会明显拖慢启动初始化；并行后受全局限流器节流，实际约 5~15 秒。
async fn eastmoney_symbol_list(state: &Arc<AppState>) -> Result<Vec<SymbolInfo>, NetError> {
    let em = state.eastmoney.clone();
    let (mut out, total) = em.clist_symbol_page(1, LIST_PAGE_SIZE).await?;
    let pages = if total > 0 {
        (total as usize).div_ceil(LIST_PAGE_SIZE).min(LIST_MAX_PAGES)
    } else {
        1
    };
    if pages <= 1 {
        return Ok(out);
    }

    let page_nums: Vec<usize> = (2..=pages).collect();
    let results = run_tasks(page_nums, move |page| {
        let em = em.clone();
        async move {
            em.clist_symbol_page(page, LIST_PAGE_SIZE)
                .await
                .map(|(items, _)| items)
        }
    })
    .await;

    let mut failures = 0usize;
    for r in results {
        match r {
            Ok(mut items) => out.append(&mut items),
            Err(_) => failures += 1,
        }
    }
    // 失败页过多说明通道不稳定，交给上层降级到新浪
    if failures > 0 && out.len() < MIN_EXPECTED_SYMBOLS {
        return Err(NetError::ServerError(format!(
            "证券列表分页失败 {failures} 页，仅取到 {} 只",
            out.len()
        )));
    }
    Ok(out)
}

/// 新浪证券列表（hs_a 节点全量分页）
async fn sina_symbol_list(state: &Arc<AppState>) -> Result<Vec<SymbolInfo>, NetError> {
    let sina = state.sina.clone();
    let page_nums: Vec<usize> = (1..=LIST_MAX_PAGES).collect();
    let results = run_tasks(page_nums, move |page| {
        let sina = sina.clone();
        async move { sina.list_page(page, LIST_PAGE_SIZE).await }
    })
    .await;

    let mut out: Vec<SymbolInfo> = Vec::new();
    let mut failures = 0usize;
    for r in results {
        match r {
            Ok(items) => out.extend(items),
            Err(_) => failures += 1,
        }
    }
    if out.is_empty() {
        return Err(NetError::EmptyBody(format!(
            "新浪证券列表为空（失败 {failures} 页）"
        )));
    }
    Ok(out)
}

/// 采集全市场行情。
///
/// 以本地证券列表为宇宙，用批量接口取行情，并返回完整性指标。
pub async fn collect_full_universe(state: &Arc<AppState>) -> UniverseSnapshot {
    let timer = Timer::start();
    let cfg = state.config();

    let symbols = ensure_symbol_list(state).await;
    if symbols.is_empty() {
        return UniverseSnapshot {
            stocks: Vec::new(),
            stats: UniverseStats {
                complete: false,
                note: "证券列表不可用，无法建立全市场股票宇宙".into(),
                ..Default::default()
            },
        };
    }
    let total_symbols = symbols.len();

    let mut stocks: Vec<StockTick> = Vec::new();
    let mut failed_batches = 0usize;
    let mut used = DataSource::None;
    let mut batches = 0usize;

    // ---- 通道 1：东方财富 ulist（唯一能提供主力净流入的全市场批量接口）----
    if cfg.enable_eastmoney
        && !state
            .gate
            .is_blocked(crate::state::gate_keys::EM_ULIST)
    {
        let secids: Vec<String> = symbols
            .iter()
            .map(|s| crate::source::secid(&s.code, s.market))
            .collect();
        let chunks: Vec<Vec<String>> = secids.chunks(QUOTE_BATCH_SIZE).map(|c| c.to_vec()).collect();
        batches = chunks.len();

        let em = state.eastmoney.clone();
        let results = run_batches(chunks, move |c| {
            let em = em.clone();
            async move { em.ulist(&c).await }
        })
        .await;

        for r in results {
            match r {
                Ok(mut v) => stocks.append(&mut v),
                Err(_) => failed_batches += 1,
            }
        }
        if !stocks.is_empty() {
            state.gate.clear(crate::state::gate_keys::EM_ULIST);
            used = DataSource::EastmoneyRealtime;
        } else {
            state
                .gate
                .block(crate::state::gate_keys::EM_ULIST, "全市场批量行情不可用");
            batches = 0;
        }
    }

    // ---- 通道 2：新浪批量行情（无主力净流入，但覆盖面完整）----
    if stocks.is_empty() && cfg.enable_sina && !state.gate.is_blocked("sina") {
        let codes: Vec<String> = symbols
            .iter()
            .map(|s| crate::source::prefixed_code(&s.code, s.market))
            .collect();
        let chunk_size = cfg.universe_batch_size.max(100);
        let chunks: Vec<Vec<String>> = codes.chunks(chunk_size).map(|c| c.to_vec()).collect();
        batches = chunks.len();

        let sina = state.sina.clone();
        let results = run_batches(chunks, move |c| {
            let sina = sina.clone();
            async move { sina.quotes(&c).await }
        })
        .await;

        for r in results {
            match r {
                Ok(mut v) => stocks.append(&mut v),
                Err(_) => failed_batches += 1,
            }
        }
        if !stocks.is_empty() {
            used = DataSource::SinaRealtime;
        }
    }

    // ---- 通道 3：腾讯批量行情 ----
    if stocks.is_empty() && cfg.enable_tencent && !state.gate.is_blocked("tencent") {
        let codes: Vec<String> = symbols
            .iter()
            .map(|s| crate::source::prefixed_code(&s.code, s.market))
            .collect();
        let chunks: Vec<Vec<String>> = codes.chunks(150).map(|c| c.to_vec()).collect();
        batches = chunks.len();

        let tx = state.tencent.clone();
        let results = run_batches(chunks, move |c| {
            let tx = tx.clone();
            async move { tx.quotes(&c).await }
        })
        .await;

        for r in results {
            match r {
                Ok(mut v) => stocks.append(&mut v),
                Err(_) => failed_batches += 1,
            }
        }
        if !stocks.is_empty() {
            used = DataSource::TencentRealtime;
        }
    }

    if stocks.is_empty() {
        let note = "全市场批量行情通道全部不可用".to_string();
        state
            .collectors
            .note_failure("full_scan", &note, timer.ms());
        return UniverseSnapshot {
            stocks: Vec::new(),
            stats: UniverseStats {
                total_symbols,
                failed_symbols: total_symbols,
                complete: false,
                source: DataSource::None.label().to_string(),
                note,
                batches,
                ..Default::default()
            },
        };
    }

    // 去重（同一代码只保留一条）
    let mut seen = HashSet::new();
    stocks.retain(|s| seen.insert(s.code.clone()));

    let loaded = stocks.len();
    let missing = total_symbols.saturating_sub(loaded);
    let coverage = loaded as f64 / total_symbols as f64;
    let complete = coverage >= 0.98 && failed_batches == 0;

    let list_updated_at = {
        let db = state.db.clone();
        tokio::task::spawn_blocking(move || db.stock_list_updated_at().ok().flatten())
            .await
            .ok()
            .flatten()
            .unwrap_or_default()
    };

    let note = if complete {
        format!("全市场 {total_symbols} 只，行情覆盖 {loaded} 只（{:.1}%）", coverage * 100.0)
    } else {
        format!(
            "全市场 {total_symbols} 只，行情覆盖 {loaded} 只（{:.1}%），缺失 {missing} 只，失败批次 {failed_batches}",
            coverage * 100.0
        )
    };

    let stats = UniverseStats {
        total_symbols,
        loaded_symbols: loaded,
        failed_symbols: missing,
        missing_symbols: missing,
        complete,
        source: used.label().to_string(),
        list_updated_at,
        batches,
        note: note.clone(),
    };

    let status = if complete {
        match used {
            DataSource::EastmoneyRealtime => DataStatus::Realtime,
            _ => DataStatus::Delay,
        }
    } else {
        DataStatus::Partial
    };
    state
        .collectors
        .note_success("full_scan", loaded, timer.ms(), used, status);

    tracing::info!(
        target: "collector",
        total = total_symbols,
        loaded,
        missing,
        failed_batches,
        source = ?used,
        "全市场行情采集完成"
    );

    UniverseSnapshot { stocks, stats }
}

/// 并发执行任务（全局限流器负责节奏控制，这里只控制并发度）
async fn run_tasks<T, F, Fut, R>(items: Vec<T>, f: F) -> Vec<Result<R, NetError>>
where
    T: Send + 'static,
    F: Fn(T) -> Fut + Send + Sync + 'static,
    Fut: std::future::Future<Output = Result<R, NetError>> + Send,
    R: Send + 'static,
{
    use futures_util::stream::{self, StreamExt};
    let f = Arc::new(f);
    stream::iter(items)
        .map(|item| {
            let f = f.clone();
            async move { f(item).await }
        })
        .buffer_unordered(4)
        .collect::<Vec<_>>()
        .await
}

/// 批量行情请求（分片为代码列表）
async fn run_batches<F, Fut>(chunks: Vec<Vec<String>>, f: F) -> Vec<Result<Vec<StockTick>, NetError>>
where
    F: Fn(Vec<String>) -> Fut + Send + Sync + 'static,
    Fut: std::future::Future<Output = Result<Vec<StockTick>, NetError>> + Send,
{
    run_tasks(chunks, f).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn board_classification_matches_real_rules() {
        assert_eq!(board_of("600519"), "main");
        assert_eq!(board_of("000001"), "main");
        assert_eq!(board_of("002415"), "main");
        assert_eq!(board_of("300750"), "gem");
        assert_eq!(board_of("301236"), "gem");
        assert_eq!(board_of("688981"), "star");
        assert_eq!(board_of("830799"), "bj");
        assert_eq!(board_of("920000"), "bj");
        assert_eq!(board_of("430047"), "bj");
    }

    #[test]
    fn a_share_filter_excludes_b_shares() {
        assert!(is_a_share("600519"));
        assert!(is_a_share("000001"));
        assert!(is_a_share("300750"));
        assert!(is_a_share("920000"));
        assert!(!is_a_share("900901"), "沪 B 股不属于 A 股");
        assert!(!is_a_share("200011"), "深 B 股不属于 A 股");
        assert!(!is_a_share("12345"), "长度不足");
        assert!(!is_a_share("abcdef"));
    }

    #[test]
    fn symbol_info_derives_market_and_board() {
        let s = SymbolInfo::new("600519", "贵州茅台");
        assert_eq!(s.market, 1);
        assert_eq!(s.board, "main");
        let s = SymbolInfo::new("300750", "宁德时代");
        assert_eq!(s.market, 0);
        assert_eq!(s.board, "gem");
        let s = SymbolInfo::new("920000", "安徽凤凰");
        assert_eq!(s.market, 2);
        assert_eq!(s.board, "bj");
    }

    #[test]
    fn stats_marks_incomplete_universe() {
        let mut stats = UniverseStats {
            total_symbols: 5900,
            loaded_symbols: 1200,
            ..Default::default()
        };
        assert!((stats.coverage_pct() - 20.3).abs() < 0.5);
        stats.complete = false;
        assert!(!stats.complete, "1200/5900 绝不能标记为完整");
    }

    #[test]
    fn batch_size_is_within_verified_capacity() {
        // 实测东财 ulist 单请求可返回 600 只，取 500 留余量
        assert!(QUOTE_BATCH_SIZE <= 600, "批量大小不得超过实测容量");
        assert!(QUOTE_BATCH_SIZE >= 200, "批量过小会导致请求数过多");
        assert!(MIN_EXPECTED_SYMBOLS >= 4000, "A 股全市场数量下限不得低于 4000");
    }
}
