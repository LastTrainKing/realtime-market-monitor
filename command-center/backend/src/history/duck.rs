//! DuckDB 历史数据查询层。
//!
//! # 为什么用 DuckDB
//!
//! 历史库是 5571 只 × 90 天 ≈ 50 万个 Parquet 文件、上亿行。
//! 用 DuckDB 的 `read_parquet()` 可以直接对 Parquet 做 SQL 查询，
//! 并且支持**分区裁剪**（只打开命中的文件）与**谓词下推**（利用 row group 统计跳过数据块），
//! 不需要把数据先导入数据库。
//!
//! # 使用约束
//!
//! * 查询必须带 `code` / `trade_date` / `minute` 过滤条件，禁止 `SELECT *` 全表扫描；
//! * 只 SELECT 需要的列（列式存储下未选中的列不会被读取）；
//! * 任何路径参数都先做格式校验，杜绝 SQL 拼接风险。
//!
//! 本层是**唯一**允许拼接 Parquet 路径的地方；业务层与前端不得自行拼路径。
//!
//! # 必须显式关闭 Hive 分区推断
//!
//! 目录布局形如 `code=600519/date=2026-09-30.parquet`，DuckDB 会**自动**把它
//! 识别成 Hive 分区表，并把 `600519` 推断为 `BIGINT` 分区列 —— 这会覆盖文件内
//! 真正的 `VARCHAR` 类型 `code` 列，导致按列索引取值时类型不匹配、
//! 查询结果全为空且不报错。
//!
//! 因此所有 `read_parquet` 调用都必须带 `hive_partitioning = false`。

use std::path::{Path, PathBuf};

use parking_lot::Mutex;

use crate::history::MinuteBar;
use crate::net::NetError;

/// 时间轴合法性校验（防止把用户输入拼进 SQL）
fn valid_code(code: &str) -> bool {
    code.len() == 6 && code.chars().all(|c| c.is_ascii_digit())
}

fn valid_date(d: &str) -> bool {
    d.len() == 10 && d.as_bytes()[4] == b'-' && d.as_bytes()[7] == b'-'
}

fn valid_minute(m: &str) -> bool {
    if m.len() != 5 || m.as_bytes()[2] != b':' {
        return false;
    }
    // 严格校验 HH:MM 数值范围，防止 "25:99" 之类被拼进 SQL
    let hh = &m[0..2];
    let mm = &m[3..5];
    match (hh.parse::<u32>(), mm.parse::<u32>()) {
        (Ok(h), Ok(min)) => h < 24 && min < 60,
        _ => false,
    }
}

/// 只读取必要列的 SELECT 列表
const COLS: &str = "code, trade_date, minute, dt_ms, open, high, low, close, volume, amount, main_net_inflow";

pub struct DuckRepository {
    root: PathBuf,
    conn: Mutex<duckdb::Connection>,
}

impl DuckRepository {
    /// `root` 为历史库根目录（其下有 `minute/`、`minute_market/`、`series/`）
    pub fn open(root: PathBuf) -> Result<Self, NetError> {
        let conn = duckdb::Connection::open_in_memory()
            .map_err(|e| NetError::ServerError(format!("DuckDB 初始化失败: {e}")))?;
        Ok(Self {
            root,
            conn: Mutex::new(conn),
        })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    /// 单只股票在指定日期区间的分钟数据（按 code/date 分区裁剪）
    pub fn stock_minute(
        &self,
        code: &str,
        date_from: &str,
        date_to: &str,
        minute_from: Option<&str>,
        minute_to: Option<&str>,
    ) -> Result<Vec<MinuteBar>, NetError> {
        if !valid_code(code) || !valid_date(date_from) || !valid_date(date_to) {
            return Err(NetError::ServerError("历史查询参数非法".into()));
        }
        // 分区裁剪：目录形如 minute/code=600519/date=*.parquet
        let glob = self
            .root
            .join("minute")
            .join(format!("code={code}"))
            .join("date=*.parquet");
        if !glob.exists() && glob.parent().map(|p| !p.exists()).unwrap_or(true) {
            return Ok(Vec::new());
        }
        let mut sql = format!(
            "SELECT {COLS} FROM read_parquet('{}', hive_partitioning = false) \
             WHERE trade_date BETWEEN '{}' AND '{}'",
            sql_path(&glob),
            date_from,
            date_to
        );
        if let Some(f) = minute_from.filter(|m| valid_minute(m)) {
            sql.push_str(&format!(" AND minute >= '{f}'"));
        }
        if let Some(t) = minute_to.filter(|m| valid_minute(m)) {
            sql.push_str(&format!(" AND minute <= '{t}'"));
        }
        sql.push_str(" ORDER BY trade_date, minute");
        self.query(&sql)
    }

    /// 指定交易日某分钟的全市场数据（命中单个 row group）
    pub fn market_at(&self, date: &str, minute: &str) -> Result<Vec<MinuteBar>, NetError> {
        if !valid_date(date) || !valid_minute(minute) {
            return Err(NetError::ServerError("历史查询参数非法".into()));
        }
        let file = self
            .root
            .join("minute_market")
            .join(format!("date={date}.parquet"));
        if !file.exists() {
            return Ok(Vec::new());
        }
        let sql = format!(
            "SELECT {COLS} FROM read_parquet('{}', hive_partitioning = false) WHERE minute = '{}' ORDER BY code",
            sql_path(&file),
            minute
        );
        self.query(&sql)
    }

    /// 指定交易日的时间范围全市场数据
    pub fn market_range(
        &self,
        date: &str,
        minute_from: &str,
        minute_to: &str,
    ) -> Result<Vec<MinuteBar>, NetError> {
        if !valid_date(date) || !valid_minute(minute_from) || !valid_minute(minute_to) {
            return Err(NetError::ServerError("历史查询参数非法".into()));
        }
        let file = self
            .root
            .join("minute_market")
            .join(format!("date={date}.parquet"));
        if !file.exists() {
            return Ok(Vec::new());
        }
        let sql = format!(
            "SELECT {COLS} FROM read_parquet('{}', hive_partitioning = false) \
             WHERE minute BETWEEN '{}' AND '{}' ORDER BY minute, code",
            sql_path(&file),
            minute_from,
            minute_to
        );
        self.query(&sql)
    }

    /// 读取指数 / 资金曲线序列
    pub fn series(&self, kind: &str, key: &str, date: &str) -> Result<Vec<MinuteBar>, NetError> {
        if !valid_date(date) {
            return Err(NetError::ServerError("历史查询参数非法".into()));
        }
        let file = self
            .root
            .join("series")
            .join(format!("kind={kind}"))
            .join(format!("key={key}"))
            .join(format!("date={date}.parquet"));
        if !file.exists() {
            return Ok(Vec::new());
        }
        let sql = format!(
            "SELECT {COLS} FROM read_parquet('{}', hive_partitioning = false) ORDER BY minute",
            sql_path(&file)
        );
        self.query(&sql)
    }

    /// 已落盘的交易日列表。
    ///
    /// 同时统计**全市场物化视图**与**序列分区**（指数分时 / 两市资金曲线）——
    /// 两者都属于历史数据，只算前者会导致"指数序列明明落盘了却显示 0 个交易日"。
    pub fn trading_dates(&self) -> Result<Vec<String>, NetError> {
        let mut all: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();

        for sub in ["minute_market", "minute"] {
            let dir = self.root.join(sub);
            if !dir.exists() {
                continue;
            }
            let glob = if sub == "minute" {
                dir.join("code=*").join("date=*.parquet")
            } else {
                dir.join("date=*.parquet")
            };
            let sql = format!(
                "SELECT DISTINCT trade_date FROM read_parquet('{}', hive_partitioning = false)",
                sql_path(&glob)
            );
            if let Ok(v) = self.query_dates(&sql) {
                all.extend(v);
            }
        }

        let sdir = self.root.join("series");
        if sdir.exists() {
            let glob = sdir
                .join("kind=*")
                .join("key=*")
                .join("date=*.parquet");
            let sql = format!(
                "SELECT DISTINCT trade_date FROM read_parquet('{}', hive_partitioning = false)",
                sql_path(&glob)
            );
            if let Ok(v) = self.query_dates(&sql) {
                all.extend(v);
            }
        }

        Ok(all.into_iter().collect())
    }

    /// 单只股票在某交易日的行数（用于完整性校验，只扫一个文件）
    pub fn stock_row_count(&self, code: &str, date: &str) -> Result<usize, NetError> {
        if !valid_code(code) || !valid_date(date) {
            return Ok(0);
        }
        let file = self
            .root
            .join("minute")
            .join(format!("code={code}"))
            .join(format!("date={date}.parquet"));
        if !file.exists() {
            return Ok(0);
        }
        let sql = format!(
            "SELECT COUNT(*) AS n FROM read_parquet('{}', hive_partitioning = false)",
            sql_path(&file)
        );
        let conn = self.conn.lock();
        let mut stmt = conn
            .prepare(&sql)
            .map_err(|e| NetError::ServerError(format!("DuckDB 预编译失败: {e}")))?;
        let n: i64 = stmt
            .query_row([], |r| r.get(0))
            .map_err(|e| NetError::ServerError(format!("DuckDB 查询失败: {e}")))?;
        Ok(n as usize)
    }

    /// 某交易日已落盘的股票数（不加载全量数据，只做 COUNT DISTINCT）
    pub fn market_symbol_count(&self, date: &str) -> Result<usize, NetError> {
        if !valid_date(date) {
            return Ok(0);
        }
        let file = self
            .root
            .join("minute_market")
            .join(format!("date={date}.parquet"));
        if !file.exists() {
            return Ok(0);
        }
        let sql = format!(
            "SELECT COUNT(DISTINCT code) AS n FROM read_parquet('{}', hive_partitioning = false)",
            sql_path(&file)
        );
        let conn = self.conn.lock();
        let mut stmt = conn
            .prepare(&sql)
            .map_err(|e| NetError::ServerError(format!("DuckDB 预编译失败: {e}")))?;
        let n: i64 = stmt
            .query_row([], |r| r.get(0))
            .map_err(|e| NetError::ServerError(format!("DuckDB 查询失败: {e}")))?;
        Ok(n as usize)
    }

    fn query(&self, sql: &str) -> Result<Vec<MinuteBar>, NetError> {
        let conn = self.conn.lock();
        let mut stmt = conn
            .prepare(sql)
            .map_err(|e| NetError::ServerError(format!("DuckDB 预编译失败: {e}")))?;
        let rows = stmt
            .query_map([], |r| {
                // 列类型必须与 Parquet schema 严格一致。
                // 早期实现用 `filter_map(|r| r.ok())` 把读取失败的行**静默丢弃**，
                // 结果一旦出现类型不匹配就会表现为"查询返回 0 行且没有任何报错"，
                // 极难定位。这里改为把错误向上传播。
                Ok(MinuteBar {
                    code: r.get::<_, String>(0)?,
                    trade_date: r.get::<_, String>(1)?,
                    minute: r.get::<_, String>(2)?,
                    dt_ms: r.get::<_, i64>(3)?,
                    open: r.get::<_, f64>(4)?,
                    high: r.get::<_, f64>(5)?,
                    low: r.get::<_, f64>(6)?,
                    close: r.get::<_, f64>(7)?,
                    volume: r.get::<_, f64>(8)?,
                    amount: r.get::<_, f64>(9)?,
                    main_net_inflow: r.get::<_, f64>(10)?,
                })
            })
            .map_err(|e| NetError::ServerError(format!("DuckDB 查询失败: {e}")))?;

        let mut out = Vec::new();
        for row in rows {
            match row {
                Ok(b) => out.push(b),
                Err(e) => {
                    return Err(NetError::ServerError(format!(
                        "DuckDB 行解析失败（列类型与 Parquet schema 不匹配）: {e}"
                    )))
                }
            }
        }
        Ok(out)
    }

    fn query_dates(&self, sql: &str) -> Result<Vec<String>, NetError> {
        let conn = self.conn.lock();
        let mut stmt = conn
            .prepare(sql)
            .map_err(|e| NetError::ServerError(format!("DuckDB 预编译失败: {e}")))?;
        let rows = stmt
            .query_map([], |r| r.get::<_, String>(0))
            .map_err(|e| NetError::ServerError(format!("DuckDB 查询失败: {e}")))?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    /// DuckDB 版本（自检用，证明查询层确实在用 DuckDB）
    pub fn version(&self) -> String {
        let conn = self.conn.lock();
        conn.query_row("SELECT version()", [], |r| r.get::<_, String>(0))
            .unwrap_or_else(|_| "unknown".into())
    }
}

/// 把路径转成 DuckDB 可用的 SQL 字符串字面量（统一正斜杠，转义单引号）
fn sql_path(p: &Path) -> String {
    p.to_string_lossy().replace('\\', "/").replace('\'', "''")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::history::MinuteStore;

    /// 使用**工作区内**的临时目录。
    ///
    /// 系统临时目录（%TEMP%）会被沙箱拦截，DuckDB 读不到那里的文件，
    /// 会造成"写入成功但查询返回 0 行"的假失败。
    fn setup() -> (DuckRepository, MinuteStore, crate::testutil::WorkspaceTempDir) {
        let dir = crate::testutil::workspace_temp_dir("duck");
        let root = dir.path().join("history");
        let store = MinuteStore::new(root.clone());
        let repo = DuckRepository::open(root).unwrap();
        (repo, store, dir)
    }

    fn bar(code: &str, date: &str, minute: &str, close: f64) -> MinuteBar {
        MinuteBar {
            code: code.into(),
            trade_date: date.into(),
            minute: minute.into(),
            dt_ms: crate::history::minute_to_ms(date, minute),
            open: close,
            high: close,
            low: close,
            close,
            volume: 100.0,
            amount: 1000.0,
            main_net_inflow: 5.0,
        }
    }

    #[test]
    fn duckdb_is_really_used() {
        let (repo, _s, _d) = setup();
        let v = repo.version();
        assert!(
            v.to_lowercase().contains("duckdb") || v.contains('.'),
            "DuckDB 版本查询应成功，实际 {v}"
        );
    }

    #[test]
    fn queries_stock_minute_range() {
        let (repo, store, _d) = setup();
        let bars: Vec<MinuteBar> = ["09:31", "10:00", "14:07", "15:00"]
            .iter()
            .map(|m| bar("600519", "2026-09-30", m, 100.0))
            .collect();
        store.write_stock("600519", "2026-09-30", &bars).unwrap();

        let all = repo
            .stock_minute("600519", "2026-09-30", "2026-09-30", None, None)
            .unwrap();
        assert_eq!(all.len(), 4);

        let win = repo
            .stock_minute("600519", "2026-09-30", "2026-09-30", Some("10:00"), Some("14:07"))
            .unwrap();
        assert_eq!(win.len(), 2);
        assert_eq!(win[0].minute, "10:00");
        assert_eq!(win[1].minute, "14:07");
    }

    #[test]
    fn queries_market_at_minute() {
        let (repo, store, _d) = setup();
        let mut bars = Vec::new();
        for i in 0..30 {
            for m in ["09:31", "14:07", "15:00"] {
                bars.push(bar(&format!("{i:06}"), "2026-09-30", m, i as f64));
            }
        }
        store.write_market("2026-09-30", &bars).unwrap();

        let at = repo.market_at("2026-09-30", "14:07").unwrap();
        assert_eq!(at.len(), 30, "某分钟应返回全市场 30 只");
        assert!(at.iter().all(|b| b.minute == "14:07"));
    }

    #[test]
    fn rejects_illegal_parameters() {
        let (repo, _s, _d) = setup();
        // 防注入：非法代码/日期直接拒绝，不进入 SQL
        assert!(repo
            .stock_minute("600519'; DROP TABLE x--", "2026-09-30", "2026-09-30", None, None)
            .is_err());
        assert!(repo.stock_minute("600519", "2026/09/30", "2026-09-30", None, None).is_err());
        assert!(repo.market_at("2026-09-30", "25:99").is_err());
    }

    #[test]
    fn missing_data_returns_empty_not_error() {
        let (repo, _s, _d) = setup();
        assert!(repo
            .stock_minute("999999", "2026-09-30", "2026-09-30", None, None)
            .unwrap()
            .is_empty());
        assert!(repo.market_at("1999-01-01", "09:31").unwrap().is_empty());
        assert!(repo.series("index", "1.000001", "1999-01-01").unwrap().is_empty());
    }

    #[test]
    fn counts_without_loading_full_data() {
        let (repo, store, _d) = setup();
        let mut bars = Vec::new();
        for i in 0..50 {
            for m in ["09:31", "09:32", "15:00"] {
                bars.push(bar(&format!("{i:06}"), "2026-09-30", m, 1.0));
            }
        }
        store.write_market("2026-09-30", &bars).unwrap();
        store.write_stock("000001", "2026-09-30", &[
            bar("000001", "2026-09-30", "09:31", 1.0),
            bar("000001", "2026-09-30", "09:32", 1.0),
        ]).unwrap();

        assert_eq!(repo.market_symbol_count("2026-09-30").unwrap(), 50);
        assert_eq!(repo.stock_row_count("000001", "2026-09-30").unwrap(), 2);
        assert_eq!(repo.stock_row_count("999999", "2026-09-30").unwrap(), 0);
    }

    #[test]
    fn lists_trading_dates() {
        let (repo, store, _d) = setup();
        for d in ["2026-09-28", "2026-09-29", "2026-09-30"] {
            store.write_market(d, &[bar("600519", d, "09:31", 1.0)]).unwrap();
        }
        let dates = repo.trading_dates().unwrap();
        assert_eq!(
            dates,
            vec![
                "2026-09-28".to_string(),
                "2026-09-29".to_string(),
                "2026-09-30".to_string()
            ]
        );
    }

    #[test]
    fn reads_series_with_duckdb() {
        let (repo, store, _d) = setup();
        let bars = vec![
            bar("1.000001", "2026-09-30", "09:31", 3838.0),
            bar("1.000001", "2026-09-30", "15:00", 3842.19),
        ];
        store.write_series("index", "1.000001", "2026-09-30", &bars).unwrap();
        let got = repo.series("index", "1.000001", "2026-09-30").unwrap();
        assert_eq!(got.len(), 2);
        assert_eq!(got[0].minute, "09:31");
        assert_eq!(got[1].minute, "15:00");
        assert!((got[1].close - 3842.19).abs() < 1e-6);
    }
}
