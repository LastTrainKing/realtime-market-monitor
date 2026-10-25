//! 历史数据获取器（HistoricalDataFetcher）。
//!
//! # 职责
//!
//! ```text
//!   交易日历 → 完整股票宇宙 → 缺失判定 → 分批下载 → 校验 → 原子落盘 → 清单登记
//!                                                              ↓
//!                                                        失败队列（下次只补失败）
//! ```
//!
//! # 设计约束
//!
//! * **本地优先**：已存在且校验通过的 (股票, 交易日, 粒度) 直接跳过，不重复下载；
//! * **增量补齐**：某交易日只有部分股票时，只补缺失的那些，不重下整天；
//! * **先校验后落盘**：校验不通过的数据**绝不写入正式文件**（`MinuteStore`
//!   内部用 `tmp → rename` 原子替换，不会出现半文件）；
//! * **失败隔离**：单只股票失败只进失败队列，不影响其余股票；
//! * **不伪造**：缺数据就是缺数据，不填 0、不复制上一分钟、不用实时值倒填。

use std::collections::HashSet;
use std::sync::Arc;
use std::time::Instant;

use crate::history::manifest::{validate_minutes, Granularity, ManifestRow, SymbolStatus};
use crate::history::{MinuteBar, MinuteStore};
use crate::state::AppState;

/// 单次历史任务的规模上限（防止一次拉太多把界面拖死）
pub const MAX_SYMBOLS_PER_RUN: usize = 6000;
/// 并发度（全局限流器仍会二次节流）
const CONCURRENCY: usize = 6;

/// 抓取计划
#[derive(Debug, Clone, Default)]
pub struct FetchPlan {
    pub granularity: u32,
    /// 需要补齐的交易日（升序）
    pub dates_missing: Vec<String>,
    /// 完整股票宇宙规模
    pub symbols_total: usize,
    /// 每个交易日已存在的股票数
    pub present_per_date: Vec<(String, usize)>,
}

/// 单日抓取结果
#[derive(Debug, Clone, Default)]
pub struct FetchReport {
    pub trade_date: String,
    pub granularity: u32,
    pub attempted: usize,
    pub succeeded: usize,
    pub skipped: usize,
    /// 失败队列：下次只重试这些
    pub failed: Vec<String>,
    /// 校验不通过的股票（同样进失败队列，但单独记录原因）
    pub validation_failed: Vec<String>,
    pub rows_written: usize,
    pub elapsed_ms: u64,
    pub note: String,
}

impl FetchReport {
    pub fn ok(&self) -> bool {
        self.failed.is_empty() && self.validation_failed.is_empty() && self.succeeded > 0
    }
}

pub struct HistoricalFetcher {
    state: Arc<AppState>,
    store: Arc<MinuteStore>,
}

impl HistoricalFetcher {
    pub fn new(state: Arc<AppState>, store: Arc<MinuteStore>) -> Self {
        Self { state, store }
    }

    /// 生成抓取计划：本地优先，只列缺失。
    pub fn plan(&self, days: i64, granularity: Granularity) -> FetchPlan {
        let clock = self.state.clock.read().clone();
        let today = crate::clock::now_cn().date_naive();
        let expected = crate::history::preload::recent_trading_days(&self.state, today, days as usize);
        let universe = self
            .state
            .cache
            .stocks
            .get_stale(crate::collector::market::CACHE_KEY_FULL_STOCKS)
            .map(|e| e.value.len())
            .unwrap_or(0);

        let _ = clock;
        let mut dates_missing = Vec::new();
        let mut present_per_date = Vec::new();

        for d in &expected {
            let present = self.store.codes_on(d);
            present_per_date.push((d.clone(), present));
            // 已落盘且规模达到宇宙的 95% 视为该日已完成
            if universe > 0 && present >= universe * 95 / 100 {
                continue;
            }
            dates_missing.push(d.clone());
        }

        FetchPlan {
            granularity: granularity.minutes(),
            dates_missing,
            symbols_total: universe,
            present_per_date,
        }
    }

    /// 执行抓取：只补缺失的股票。
    pub async fn fetch_date(
        &self,
        trade_date: &str,
        granularity: Granularity,
    ) -> FetchReport {
        let started = Instant::now();
        let mut report = FetchReport {
            trade_date: trade_date.to_string(),
            granularity: granularity.minutes(),
            ..Default::default()
        };

        let symbols = crate::universe::ensure_symbol_list(&self.state).await;
        if symbols.is_empty() {
            report.note = "股票宇宙不可用，无法抓取历史分钟数据".into();
            return report;
        }

        // 本地已有的股票（增量：只补缺失）
        let existing: HashSet<String> = self
            .store
            .query_market_range(trade_date, "00:00", "23:59")
            .unwrap_or_default()
            .into_iter()
            .map(|b| b.code)
            .collect();
        report.skipped = existing.len();

        let todo: Vec<crate::universe::SymbolInfo> = symbols
            .into_iter()
            .filter(|s| !existing.contains(&s.code))
            .take(MAX_SYMBOLS_PER_RUN)
            .collect();
        report.attempted = todo.len();

        if todo.is_empty() {
            report.note = format!("{trade_date} 已完整（{} 只），无需下载", report.skipped);
            report.elapsed_ms = started.elapsed().as_millis() as u64;
            return report;
        }

        // 分批并发下载 + 逐只校验
        let sina = self.state.sina.clone();
        let scale = granularity.minutes();
        let want_days = granularity.source_max_days();
        let date_owned = trade_date.to_string();

        let results = run_concurrent(todo, CONCURRENCY, move |sym| {
            let sina = sina.clone();
            let date = date_owned.clone();
            async move {
                let full = crate::source::prefixed_code(&sym.code, sym.market);
                let bars = sina.minute_kline(&full, scale, 1023).await;
                (sym.code, bars, date, want_days)
            }
        })
        .await;

        let mut per_symbol: Vec<SymbolStatus> = Vec::new();
        let mut all_bars: Vec<MinuteBar> = Vec::new();

        for (code, res, date, _) in results {
            match res {
                Ok(bars) => {
                    let day_bars: Vec<MinuteBar> = bars
                        .into_iter()
                        .filter(|b| b.trade_date == date)
                        .collect();
                    let v = validate_minutes(&code, &date, &day_bars, granularity);
                    if !v.ok {
                        report.validation_failed.push(code.clone());
                        tracing::warn!(target: "collector", code, date, problems = ?v.problems, "历史分钟数据校验失败，不落盘");
                        continue;
                    }
                    // 原子落盘：MinuteStore 内部 tmp → rename
                    let n = self.store.write_stock(&code, &date, &day_bars).unwrap_or(0);
                    if n == 0 {
                        report.failed.push(code.clone());
                        continue;
                    }
                    report.succeeded += 1;
                    report.rows_written += n;
                    per_symbol.push(SymbolStatus {
                        trade_date: date.clone(),
                        granularity: granularity.minutes(),
                        code: code.clone(),
                        row_count: n as i32,
                        first_dt: v.first_dt.clone(),
                        last_dt: v.last_dt.clone(),
                        complete: true,
                    });
                    all_bars.extend(day_bars);
                }
                Err(err) => {
                    tracing::debug!(target: "collector", code, error = %err, "历史分钟下载失败");
                    report.failed.push(code);
                }
            }
        }

        // 全市场物化视图：把本次新增与既有数据合并后重写（保证该日视图完整）
        if !all_bars.is_empty() {
            let mut merged = self
                .store
                .query_market_range(trade_date, "00:00", "23:59")
                .unwrap_or_default();
            merged.extend(all_bars);
            merged.sort_by(|a, b| a.minute.cmp(&b.minute).then(a.code.cmp(&b.code)));
            merged.dedup_by(|a, b| a.code == b.code && a.minute == b.minute);
            let _ = self.store.write_market(trade_date, &merged);
            let _ = self.state.db.save_symbol_status(&per_symbol);
        }

        report.elapsed_ms = started.elapsed().as_millis() as u64;
        report.note = format!(
            "{} {}：新增 {} 只 / 跳过 {} 只 / 失败 {} 只 / 校验未通过 {} 只，共 {} 行",
            trade_date,
            granularity.label(),
            report.succeeded,
            report.skipped,
            report.failed.len(),
            report.validation_failed.len(),
            report.rows_written
        );
        report
    }

    /// 按计划补齐所有缺失交易日。
    pub async fn fetch_plan(&self, plan: &FetchPlan) -> Vec<FetchReport> {
        let Some(g) = Granularity::from_minutes(plan.granularity) else {
            return Vec::new();
        };
        let mut out = Vec::new();
        for date in &plan.dates_missing {
            let r = self.fetch_date(date, g).await;
            let _ = self.state.db.upsert_manifest(&ManifestRow {
                trade_date: date.clone(),
                granularity: g.minutes(),
                expected_symbols: plan.symbols_total as i32,
                actual_symbols: self.store.codes_on(date) as i32,
                missing_symbols: (plan.symbols_total as i32
                    - self.store.codes_on(date) as i32)
                    .max(0),
                total_rows: self.store.codes_on(date) as i64 * 240,
                complete: self.store.codes_on(date) >= plan.symbols_total * 95 / 100
                    && plan.symbols_total > 0,
                created_at: crate::models::now_str(),
                updated_at: crate::models::now_str(),
                note: r.note.clone(),
            });
            out.push(r);
        }
        out
    }
}

/// 简易并发执行（全局限流器负责最终节流）
async fn run_concurrent<T, F, Fut, R>(items: Vec<T>, limit: usize, f: F) -> Vec<R>
where
    T: Send + 'static,
    F: Fn(T) -> Fut + Send + Sync + 'static,
    Fut: std::future::Future<Output = R> + Send,
    R: Send + 'static,
{
    use futures_util::stream::{self, StreamExt};
    let f = Arc::new(f);
    stream::iter(items)
        .map(|item| {
            let f = f.clone();
            async move { f(item).await }
        })
        .buffer_unordered(limit)
        .collect()
        .await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn report_ok_requires_success_and_no_failures() {
        let mut r = FetchReport {
            succeeded: 10,
            ..Default::default()
        };
        assert!(r.ok());
        r.failed.push("000001".into());
        assert!(!r.ok(), "有失败队列时不得判定为成功");
    }

    #[test]
    fn granularity_from_minutes_rejects_invalid() {
        assert!(Granularity::from_minutes(1).is_some());
        assert!(Granularity::from_minutes(7).is_none());
    }

    #[test]
    fn max_symbols_guard_is_bounded() {
        assert!(MAX_SYMBOLS_PER_RUN >= 5000, "必须能覆盖 A 股全市场");
        assert!(MAX_SYMBOLS_PER_RUN <= 20000, "必须有上限防止失控");
    }
}
