//! 历史静态数据层（第 4.11 / 8.10 章）。
//!
//! 职责边界：
//!   * `parquet_store` —— 分钟数据的列式落盘与高效查询（分区裁剪 + 列裁剪 + row group 统计裁剪）
//!   * `preload`       —— 历史数据预加载与增量补齐
//!
//! 时间轴约定：所有历史数据的"时间"一律是**行情时间**，
//! 绝不使用系统时间或抓取时间作为历史数据的横轴。

pub mod duck;
pub mod fetcher;
pub mod manifest;
pub mod parquet_store;
pub mod preload;

pub use duck::DuckRepository;
pub use fetcher::{FetchPlan, FetchReport, HistoricalFetcher};
pub use manifest::{Granularity, ManifestRow, SymbolStatus, ValidationReport};
pub use parquet_store::{MinuteBar, MinuteStore, StoreStats, MINUTES_PER_TRADING_DAY};
pub use preload::minute_to_ms;
