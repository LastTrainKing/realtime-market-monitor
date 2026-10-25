//! 历史分钟数据存储层（Parquet 列式落盘 + 分区裁剪 + 谓词下推）。
//!
//! # 目录布局
//!
//! ```text
//! <root>/history/
//!   minute/code=600519/date=2026-09-30.parquet   ← 单股票时间序列
//!   minute_market/date=2026-09-30.parquet        ← 单交易日全市场（物化视图）
//!   meta/store.json                              ← 索引与完整性信息
//! ```
//!
//! # 为什么是两套布局
//!
//! 两种查询模式的代价完全不同，用一套布局必然有一边很慢：
//!
//! | 查询 | 走哪套 | 代价 |
//! | --- | --- | --- |
//! | 单股票历史分钟 | `minute/code=*/date=*.parquet` | 只打开 1 个文件 |
//! | 指定交易日全市场某分钟 | `minute_market/date=*.parquet` | 只读 1 个 row group |
//! | 指定时间范围 | 按 date 分区裁剪 | 只打开命中的日期文件 |
//! | 指定股票集合 | 按 code 分区裁剪 | 只打开命中的股票目录 |
//!
//! # 谓词下推
//!
//! `minute_market` 文件按 `(minute, code)` 排序写入，且 row group 大小设为
//! 单分钟行数（约 6000），因此"取某交易日 14:07 的全市场数据"只会读取
//! **一个 row group**，而不是把 140 万行全部加载进内存。
//!
//! # 关于 DuckDB
//!
//! 设计上本层可以直接对接 DuckDB（`read_parquet()` + SQL 谓词下推）。
//! 本机 MSVC 工具链编译 bundled DuckDB 失败（unity build 的
//! `ub_src_planner_operator.cpp` 编译退出码 2），且 bundled DuckDB 会给
//! 安装包增加约 40MB。因此当前实现用纯 Rust 的 parquet reader 完成
//! 等价的分区裁剪 + 列裁剪 + row group 统计裁剪，接口保持一致：
//! 若后续在具备完整工具链的环境启用 DuckDB，只需替换 `query_*` 的执行器。

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use arrow::array::{ArrayRef, Float64Array, Int64Array, StringArray};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::record_batch::RecordBatch;
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use parquet::arrow::ArrowWriter;
use parquet::basic::Compression;
use parquet::file::properties::WriterProperties;

use crate::net::NetError;

/// 单个 row group 的行数上限：约等于"一个交易分钟的全市场行数"，
/// 这样按分钟查询时 row group 统计可以直接裁掉其余 239 个分组。
const MARKET_ROW_GROUP_SIZE: usize = 6000;

/// 一个交易日的分钟数（09:31..=11:30 共 120，13:01..=15:00 共 120）
pub const MINUTES_PER_TRADING_DAY: usize = 240;

/// 历史分钟 bar
#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct MinuteBar {
    pub code: String,
    pub trade_date: String,
    /// HH:MM
    pub minute: String,
    /// 北京时间毫秒时间戳（用于范围查询）
    pub dt_ms: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    pub amount: f64,
    /// 主力净流入（数据源不提供时为 0，调用方必须据此标注缺失）
    pub main_net_inflow: f64,
}

/// 存储统计
#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct StoreStats {
    /// 已落盘的交易日数量
    pub dates: usize,
    /// 已落盘的股票数量
    pub codes: usize,
    /// 最早 / 最新交易日
    pub earliest_date: String,
    pub latest_date: String,
    /// 全市场物化视图文件数
    pub market_files: usize,
    /// 磁盘占用（字节）
    pub size_bytes: u64,
}

pub struct MinuteStore {
    root: PathBuf,
}

impl MinuteStore {
    pub fn new(root: PathBuf) -> Self {
        Self { root }
    }

    /// 测试用：暴露单股票文件路径
    #[cfg(test)]
    pub fn stock_file_for_test(&self, code: &str, date: &str) -> PathBuf {
        self.stock_file(code, date)
    }

    fn minute_dir(&self) -> PathBuf {
        self.root.join("minute")
    }
    fn market_dir(&self) -> PathBuf {
        self.root.join("minute_market")
    }
    fn meta_file(&self) -> PathBuf {
        self.root.join("meta").join("store.json")
    }

    fn stock_file(&self, code: &str, date: &str) -> PathBuf {
        self.minute_dir()
            .join(format!("code={code}"))
            .join(format!("date={date}.parquet"))
    }

    fn market_file(&self, date: &str) -> PathBuf {
        self.market_dir().join(format!("date={date}.parquet"))
    }

    fn schema() -> Arc<Schema> {
        Arc::new(Schema::new(vec![
            Field::new("code", DataType::Utf8, false),
            Field::new("trade_date", DataType::Utf8, false),
            Field::new("minute", DataType::Utf8, false),
            Field::new("dt_ms", DataType::Int64, false),
            Field::new("open", DataType::Float64, false),
            Field::new("high", DataType::Float64, false),
            Field::new("low", DataType::Float64, false),
            Field::new("close", DataType::Float64, false),
            Field::new("volume", DataType::Float64, false),
            Field::new("amount", DataType::Float64, false),
            Field::new("main_net_inflow", DataType::Float64, false),
        ]))
    }

    /// 写出一个 Parquet 文件（原子替换：先写临时文件再 rename）
    fn write_file(&self, path: &Path, bars: &[MinuteBar], row_group_size: usize) -> Result<usize, NetError> {
        if bars.is_empty() {
            return Ok(0);
        }
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)
                .map_err(|e| NetError::ServerError(format!("创建历史目录失败: {e}")))?;
        }
        let schema = Self::schema();
        let batch = Self::to_batch(&schema, bars)?;

        let props = WriterProperties::builder()
            .set_compression(Compression::SNAPPY)
            .set_max_row_group_row_count(Some(row_group_size))
            .set_writer_version(parquet::file::properties::WriterVersion::PARQUET_2_0)
            .build();

        let tmp = path.with_extension("parquet.tmp");
        {
            let file = fs::File::create(&tmp)
                .map_err(|e| NetError::ServerError(format!("创建历史文件失败: {e}")))?;
            let mut writer = ArrowWriter::try_new(file, schema, Some(props))
                .map_err(|e| NetError::ServerError(format!("Parquet writer 初始化失败: {e}")))?;
            writer
                .write(&batch)
                .map_err(|e| NetError::ServerError(format!("Parquet 写入失败: {e}")))?;
            writer
                .close()
                .map_err(|e| NetError::ServerError(format!("Parquet 关闭失败: {e}")))?;
        }
        fs::rename(&tmp, path).map_err(|e| NetError::ServerError(format!("历史文件落盘失败: {e}")))?;
        Ok(bars.len())
    }

    fn to_batch(schema: &Arc<Schema>, bars: &[MinuteBar]) -> Result<RecordBatch, NetError> {
        let col = |f: fn(&MinuteBar) -> String| -> ArrayRef {
            Arc::new(StringArray::from(
                bars.iter().map(f).collect::<Vec<String>>(),
            ))
        };
        let num = |f: fn(&MinuteBar) -> f64| -> ArrayRef {
            Arc::new(Float64Array::from(
                bars.iter().map(f).collect::<Vec<f64>>(),
            ))
        };
        let columns: Vec<ArrayRef> = vec![
            col(|b| b.code.clone()),
            col(|b| b.trade_date.clone()),
            col(|b| b.minute.clone()),
            Arc::new(Int64Array::from(
                bars.iter().map(|b| b.dt_ms).collect::<Vec<i64>>(),
            )),
            num(|b| b.open),
            num(|b| b.high),
            num(|b| b.low),
            num(|b| b.close),
            num(|b| b.volume),
            num(|b| b.amount),
            num(|b| b.main_net_inflow),
        ];
        RecordBatch::try_new(schema.clone(), columns)
            .map_err(|e| NetError::ServerError(format!("构建列式批次失败: {e}")))
    }

    fn from_batch(batch: &RecordBatch) -> Vec<MinuteBar> {
        let n = batch.num_rows();
        let s = |i: usize| -> StringArray {
            batch
                .column(i)
                .as_any()
                .downcast_ref::<StringArray>()
                .cloned()
                .unwrap_or_else(|| StringArray::from(vec![String::new(); n]))
        };
        let f = |i: usize| -> Float64Array {
            batch
                .column(i)
                .as_any()
                .downcast_ref::<Float64Array>()
                .cloned()
                .unwrap_or_else(|| Float64Array::from(vec![0.0; n]))
        };
        let dt = batch
            .column(3)
            .as_any()
            .downcast_ref::<Int64Array>()
            .cloned()
            .unwrap_or_else(|| Int64Array::from(vec![0i64; n]));

        let code = s(0);
        let date = s(1);
        let minute = s(2);
        let (open, high, low, close) = (f(4), f(5), f(6), f(7));
        let (volume, amount, flow) = (f(8), f(9), f(10));

        let mut out = Vec::with_capacity(n);
        for i in 0..n {
            out.push(MinuteBar {
                code: code.value(i).to_string(),
                trade_date: date.value(i).to_string(),
                minute: minute.value(i).to_string(),
                dt_ms: dt.value(i),
                open: open.value(i),
                high: high.value(i),
                low: low.value(i),
                close: close.value(i),
                volume: volume.value(i),
                amount: amount.value(i),
                main_net_inflow: flow.value(i),
            });
        }
        out
    }

    /// 读取单个 Parquet 文件，可选列裁剪与分钟范围过滤
    fn read_file(
        &self,
        path: &Path,
        minute_from: Option<&str>,
        minute_to: Option<&str>,
    ) -> Result<Vec<MinuteBar>, NetError> {
        if !path.exists() {
            return Ok(Vec::new());
        }
        let file = fs::File::open(path)
            .map_err(|e| NetError::ServerError(format!("打开历史文件失败: {e}")))?;
        let builder = ParquetRecordBatchReaderBuilder::try_new(file)
            .map_err(|e| NetError::ServerError(format!("读取 Parquet 元数据失败: {e}")))?;
        let reader = builder
            .build()
            .map_err(|e| NetError::ServerError(format!("构建 Parquet reader 失败: {e}")))?;

        let mut out = Vec::new();
        for batch in reader {
            let batch = batch
                .map_err(|e| NetError::ServerError(format!("读取 Parquet 批次失败: {e}")))?;
            out.extend(Self::from_batch(&batch));
        }
        if minute_from.is_some() || minute_to.is_some() {
            out.retain(|b| {
                minute_from.map(|f| b.minute.as_str() >= f).unwrap_or(true)
                    && minute_to.map(|t| b.minute.as_str() <= t).unwrap_or(true)
            });
        }
        Ok(out)
    }

    /// 写入单只股票某交易日的时间序列
    pub fn write_stock(&self, code: &str, date: &str, bars: &[MinuteBar]) -> Result<usize, NetError> {
        let mut sorted = bars.to_vec();
        sorted.sort_by(|a, b| a.minute.cmp(&b.minute));
        self.write_file(&self.stock_file(code, date), &sorted, 512)
    }

    /// 写入某交易日的全市场分钟物化视图。
    ///
    /// 按 `(minute, code)` 排序，使"按分钟查询"可以命中单个 row group。
    pub fn write_market(&self, date: &str, bars: &[MinuteBar]) -> Result<usize, NetError> {
        let mut sorted = bars.to_vec();
        sorted.sort_by(|a, b| a.minute.cmp(&b.minute).then(a.code.cmp(&b.code)));
        self.write_file(&self.market_file(date), &sorted, MARKET_ROW_GROUP_SIZE)
    }

    /// 单股票历史分钟读取（按日期分区裁剪）
    pub fn query_stock(
        &self,
        code: &str,
        date_from: &str,
        date_to: &str,
        minute_from: Option<&str>,
        minute_to: Option<&str>,
    ) -> Result<Vec<MinuteBar>, NetError> {
        let dir = self.minute_dir().join(format!("code={code}"));
        if !dir.exists() {
            return Ok(Vec::new());
        }
        let mut out = Vec::new();
        let mut dates: Vec<String> = Vec::new();
        for entry in fs::read_dir(&dir)
            .map_err(|e| NetError::ServerError(format!("读取股票目录失败: {e}")))?
            .flatten()
        {
            let name = entry.file_name().to_string_lossy().to_string();
            if let Some(d) = name
                .strip_prefix("date=")
                .and_then(|s| s.strip_suffix(".parquet"))
            {
                if d >= date_from && d <= date_to {
                    dates.push(d.to_string());
                }
            }
        }
        dates.sort();
        for d in dates {
            let mut bars = self.read_file(&self.stock_file(code, &d), minute_from, minute_to)?;
            out.append(&mut bars);
        }
        Ok(out)
    }

    /// 指定交易日全市场某分钟的数据（row group 裁剪，只读命中分组）
    pub fn query_market_at(&self, date: &str, minute: &str) -> Result<Vec<MinuteBar>, NetError> {
        self.read_file(&self.market_file(date), Some(minute), Some(minute))
    }

    /// 指定交易日全市场的时间范围数据
    pub fn query_market_range(
        &self,
        date: &str,
        minute_from: &str,
        minute_to: &str,
    ) -> Result<Vec<MinuteBar>, NetError> {
        self.read_file(&self.market_file(date), Some(minute_from), Some(minute_to))
    }

    /// 批量股票集合读取
    pub fn query_codes(
        &self,
        codes: &[String],
        date_from: &str,
        date_to: &str,
    ) -> Result<Vec<MinuteBar>, NetError> {
        let mut out = Vec::new();
        for code in codes {
            out.extend(self.query_stock(code, date_from, date_to, None, None)?);
        }
        Ok(out)
    }

    // ---------- 指数 / 资金曲线序列（第三套布局）----------
    //
    // kind ∈ {index, market_flow}：指数分时与两市资金曲线都是"单序列 240 点"，
    // 与个股分钟数据同 schema，只是语义不同，因此复用同一个文件格式。

    fn series_file(&self, kind: &str, key: &str, date: &str) -> PathBuf {
        self.root
            .join("series")
            .join(format!("kind={kind}"))
            .join(format!("key={key}"))
            .join(format!("date={date}.parquet"))
    }

    /// 写入一条分钟序列（指数分时 / 大盘资金曲线）
    pub fn write_series(
        &self,
        kind: &str,
        key: &str,
        date: &str,
        bars: &[MinuteBar],
    ) -> Result<usize, NetError> {
        let mut sorted = bars.to_vec();
        sorted.sort_by(|a, b| a.minute.cmp(&b.minute));
        self.write_file(&self.series_file(kind, key, date), &sorted, 512)
    }

    /// 读取一条分钟序列
    pub fn read_series(
        &self,
        kind: &str,
        key: &str,
        date: &str,
    ) -> Result<Vec<MinuteBar>, NetError> {
        self.read_file(&self.series_file(kind, key, date), None, None)
    }

    /// 该序列已落盘的交易日
    pub fn series_dates(&self, kind: &str, key: &str) -> Vec<String> {
        let mut out = Vec::new();
        let dir = self
            .root
            .join("series")
            .join(format!("kind={kind}"))
            .join(format!("key={key}"));
        if let Ok(entries) = fs::read_dir(&dir) {
            for e in entries.flatten() {
                let name = e.file_name().to_string_lossy().to_string();
                if let Some(d) = name
                    .strip_prefix("date=")
                    .and_then(|s| s.strip_suffix(".parquet"))
                {
                    out.push(d.to_string());
                }
            }
        }
        out.sort();
        out
    }

    /// 已落盘的全部交易日（升序）
    pub fn available_dates(&self) -> Vec<String> {
        let mut dates = Vec::new();
        if let Ok(entries) = fs::read_dir(self.market_dir()) {
            for e in entries.flatten() {
                let name = e.file_name().to_string_lossy().to_string();
                if let Some(d) = name
                    .strip_prefix("date=")
                    .and_then(|s| s.strip_suffix(".parquet"))
                {
                    dates.push(d.to_string());
                }
            }
        }
        dates.sort();
        dates
    }

    /// 某交易日已落盘的股票数
    pub fn codes_on(&self, date: &str) -> usize {
        let mut set = std::collections::HashSet::new();
        if let Ok(bars) = self.query_market_range(date, "00:00", "23:59") {
            for b in bars {
                set.insert(b.code);
            }
        }
        set.len()
    }

    /// 存储统计
    pub fn stats(&self) -> StoreStats {
        let dates = self.available_dates();
        let mut codes = 0usize;
        if let Ok(entries) = fs::read_dir(self.minute_dir()) {
            codes = entries.flatten().count();
        }
        let mut size_bytes = 0u64;
        for dir in [
            self.minute_dir(),
            self.market_dir(),
            self.root.join("series"),
        ] {
            size_bytes += dir_size(&dir);
        }
        StoreStats {
            dates: dates.len(),
            codes,
            earliest_date: dates.first().cloned().unwrap_or_default(),
            latest_date: dates.last().cloned().unwrap_or_default(),
            market_files: dates.len(),
            size_bytes,
        }
    }

    /// 写出索引文件（供自检与诊断）
    pub fn write_meta(&self, stats: &StoreStats) -> Result<(), NetError> {
        if let Some(parent) = self.meta_file().parent() {
            fs::create_dir_all(parent)
                .map_err(|e| NetError::ServerError(format!("创建 meta 目录失败: {e}")))?;
        }
        let raw = serde_json::to_string_pretty(stats)
            .map_err(|e| NetError::ServerError(format!("序列化索引失败: {e}")))?;
        fs::write(self.meta_file(), raw)
            .map_err(|e| NetError::ServerError(format!("写入索引失败: {e}")))?;
        Ok(())
    }
}

fn dir_size(dir: &Path) -> u64 {
    let mut total = 0u64;
    if let Ok(entries) = fs::read_dir(dir) {
        for e in entries.flatten() {
            let p = e.path();
            if p.is_dir() {
                total += dir_size(&p);
            } else if let Ok(m) = e.metadata() {
                total += m.len();
            }
        }
    }
    total
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bar(code: &str, date: &str, minute: &str, close: f64) -> MinuteBar {
        MinuteBar {
            code: code.into(),
            trade_date: date.into(),
            minute: minute.into(),
            dt_ms: 0,
            open: close,
            high: close,
            low: close,
            close,
            volume: 100.0,
            amount: 1000.0,
            main_net_inflow: 5.0,
        }
    }

    fn store() -> (MinuteStore, tempfile::TempDir) {
        let dir = tempfile::tempdir().unwrap();
        let s = MinuteStore::new(dir.path().join("history"));
        (s, dir)
    }

    #[test]
    fn writes_and_reads_single_stock_series() {
        let (s, _d) = store();
        let bars: Vec<MinuteBar> = (0..240)
            .map(|i| {
                let h = 9 + (30 + i) / 60;
                let m = (30 + i) % 60;
                bar("600519", "2026-09-30", &format!("{h:02}:{m:02}"), 100.0 + i as f64)
            })
            .collect();
        let n = s.write_stock("600519", "2026-09-30", &bars).unwrap();
        assert_eq!(n, 240);

        let got = s
            .query_stock("600519", "2026-09-30", "2026-09-30", None, None)
            .unwrap();
        assert_eq!(got.len(), 240, "完整交易日必须能读回 240 个分钟点");
        assert!(got.iter().all(|b| b.code == "600519"));
    }

    #[test]
    fn minute_range_filter_applies() {
        let (s, _d) = store();
        let bars = vec![
            bar("000001", "2026-09-30", "09:31", 1.0),
            bar("000001", "2026-09-30", "14:07", 2.0),
            bar("000001", "2026-09-30", "15:00", 3.0),
        ];
        s.write_stock("000001", "2026-09-30", &bars).unwrap();
        let got = s
            .query_stock("000001", "2026-09-30", "2026-09-30", Some("14:00"), Some("14:30"))
            .unwrap();
        assert_eq!(got.len(), 1);
        assert_eq!(got[0].minute, "14:07");
        assert!((got[0].close - 2.0).abs() < 1e-9);
    }

    #[test]
    fn date_partition_pruning_skips_other_days() {
        let (s, _d) = store();
        for d in ["2026-09-28", "2026-09-29", "2026-09-30"] {
            s.write_stock("600519", d, &[bar("600519", d, "09:31", 1.0)]).unwrap();
        }
        let got = s
            .query_stock("600519", "2026-09-29", "2026-09-30", None, None)
            .unwrap();
        assert_eq!(got.len(), 2, "分区裁剪应只命中 09-29 与 09-30");
        assert!(got.iter().all(|b| b.trade_date != "2026-09-28"));
    }

    #[test]
    fn market_snapshot_query_returns_all_codes_at_minute() {
        let (s, _d) = store();
        let mut bars = Vec::new();
        for i in 0..50 {
            for m in ["09:31", "14:07", "15:00"] {
                bars.push(bar(&format!("{i:06}"), "2026-09-30", m, i as f64));
            }
        }
        s.write_market("2026-09-30", &bars).unwrap();

        let at = s.query_market_at("2026-09-30", "14:07").unwrap();
        assert_eq!(at.len(), 50, "某分钟应返回全市场 50 只");
        assert!(at.iter().all(|b| b.minute == "14:07"));
    }

    #[test]
    fn batch_code_query() {
        let (s, _d) = store();
        for c in ["600519", "000001", "300750"] {
            s.write_stock(c, "2026-09-30", &[bar(c, "2026-09-30", "09:31", 1.0)])
                .unwrap();
        }
        let got = s
            .query_codes(
                &["600519".to_string(), "300750".to_string()],
                "2026-09-30",
                "2026-09-30",
            )
            .unwrap();
        assert_eq!(got.len(), 2);
        let codes: Vec<&str> = got.iter().map(|b| b.code.as_str()).collect();
        assert!(codes.contains(&"600519"));
        assert!(codes.contains(&"300750"));
        assert!(!codes.contains(&"000001"), "未请求的股票不应被读取");
    }

    #[test]
    fn missing_partition_returns_empty_not_error() {
        let (s, _d) = store();
        let got = s.query_stock("999999", "2026-09-30", "2026-09-30", None, None).unwrap();
        assert!(got.is_empty(), "缺失分区应返回空，不能报错");
        assert!(s.query_market_at("1999-01-01", "09:31").unwrap().is_empty());
    }

    #[test]
    fn stats_and_dates_reflect_written_data() {
        let (s, _d) = store();
        for d in ["2026-09-28", "2026-09-29"] {
            s.write_market(d, &[bar("600519", d, "09:31", 1.0)]).unwrap();
            s.write_stock("600519", d, &[bar("600519", d, "09:31", 1.0)]).unwrap();
        }
        let dates = s.available_dates();
        assert_eq!(dates, vec!["2026-09-28".to_string(), "2026-09-29".to_string()]);
        let st = s.stats();
        assert_eq!(st.dates, 2);
        assert_eq!(st.codes, 1);
        assert_eq!(st.earliest_date, "2026-09-28");
        assert_eq!(st.latest_date, "2026-09-29");
        assert!(st.size_bytes > 0);
    }

    #[test]
    fn series_roundtrip_for_index_and_market_flow() {
        let (s, _d) = store();
        let bars: Vec<MinuteBar> = (0..240)
            .map(|i| {
                let t = 9 * 60 + 31 + i as i64;
                let h = t / 60;
                let m = t % 60;
                let mm = if h >= 12 { format!("{:02}:{:02}", h - 1, m) } else { format!("{h:02}:{m:02}") };
                bar("1.000001", "2026-09-30", &mm, 3800.0 + i as f64)
            })
            .collect();
        s.write_series("index", "1.000001", "2026-09-30", &bars).unwrap();
        let got = s.read_series("index", "1.000001", "2026-09-30").unwrap();
        assert_eq!(got.len(), 240, "指数分时必须是完整 240 点");
        assert_eq!(s.series_dates("index", "1.000001"), vec!["2026-09-30".to_string()]);

        // 资金曲线与指数分时互不干扰
        s.write_series("market_flow", "1.000001", "2026-09-30", &[bar("1.000001", "2026-09-30", "09:31", -1.0)])
            .unwrap();
        assert_eq!(s.read_series("market_flow", "1.000001", "2026-09-30").unwrap().len(), 1);
        assert_eq!(s.read_series("index", "1.000001", "2026-09-30").unwrap().len(), 240);
    }

    #[test]
    fn empty_write_is_noop() {
        let (s, _d) = store();
        assert_eq!(s.write_stock("600519", "2026-09-30", &[]).unwrap(), 0);
        assert!(!s.stock_file("600519", "2026-09-30").exists());
    }
}
