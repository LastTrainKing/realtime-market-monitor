//! 二级缓存 / 持久化：SQLite（第 4.7 / 8.7 / 8.14 章）。
//!
//! 表结构覆盖：节点快照、榜单事件（事件驱动）、分钟数据（历史重建）、
//! 板块资金流分钟、交易日汇总、运行状态。所有写入走 WAL，读取走连接池。

use anyhow::{Context, Result};
use chrono::NaiveDate;
use parking_lot::Mutex;
use rusqlite::{params, Connection, OptionalExtension};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use crate::models::{
    DbStats, FlowPoint, IntradaySnapshot, MinutePoint, RankingEvent, ReviewDay, StockTick,
};

pub const SCHEMA_VERSION: i64 = 4;

const SCHEMA_SQL: &str = r#"
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    trade_date TEXT NOT NULL,
    node       TEXT NOT NULL,
    origin     TEXT NOT NULL,
    status     TEXT NOT NULL,
    source     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload    TEXT NOT NULL,
    PRIMARY KEY (trade_date, node, origin)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_date ON snapshots(trade_date);

CREATE TABLE IF NOT EXISTS ranking_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date    TEXT NOT NULL,
    stock_code    TEXT NOT NULL,
    stock_name    TEXT NOT NULL,
    rank_type     TEXT NOT NULL,
    rank_time     TEXT NOT NULL,
    rank_position INTEGER NOT NULL,
    value         REAL NOT NULL,
    source        TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ranking_event
    ON ranking_events(trade_date, stock_code, rank_type, rank_time);
CREATE INDEX IF NOT EXISTS idx_ranking_date ON ranking_events(trade_date);
CREATE INDEX IF NOT EXISTS idx_ranking_code ON ranking_events(trade_date, stock_code);

-- 榜单停留区间（事件驱动的唯一权威状态）
-- appearances = 上榜"事件"次数：首次进入 +1，持续停留不变，掉出后重入再 +1
-- active      = 当前是否仍在榜
-- history     = 每次"进入"的时间点（JSON 数组），用于重启后完整还原
CREATE TABLE IF NOT EXISTS board_episodes (
    trade_date    TEXT NOT NULL,
    board_id      TEXT NOT NULL,
    stock_code    TEXT NOT NULL,
    stock_name    TEXT NOT NULL,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    best_rank     INTEGER NOT NULL,
    last_rank     INTEGER NOT NULL,
    max_value     REAL NOT NULL,
    appearances   INTEGER NOT NULL DEFAULT 1,
    active        INTEGER NOT NULL DEFAULT 1,
    history       TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (trade_date, board_id, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_episodes_code ON board_episodes(trade_date, stock_code);

CREATE TABLE IF NOT EXISTS minute_data (
    trade_date TEXT NOT NULL,
    code       TEXT NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    minute     TEXT NOT NULL,
    price      REAL NOT NULL,
    volume     REAL NOT NULL,
    amount     REAL NOT NULL,
    main_net   REAL NOT NULL DEFAULT 0,
    change_pct REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (trade_date, code, minute)
);
CREATE INDEX IF NOT EXISTS idx_minute_date ON minute_data(trade_date, minute);

CREATE TABLE IF NOT EXISTS flow_minute (
    trade_date  TEXT NOT NULL,
    scope       TEXT NOT NULL,
    code        TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    minute      TEXT NOT NULL,
    main_net    REAL NOT NULL,
    super_large REAL NOT NULL DEFAULT 0,
    large       REAL NOT NULL DEFAULT 0,
    medium      REAL NOT NULL DEFAULT 0,
    small       REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (trade_date, scope, code, minute)
);
CREATE INDEX IF NOT EXISTS idx_flow_date ON flow_minute(trade_date, scope, code);

-- 本地证券列表：全市场股票宇宙的唯一权威来源。
-- 关键：绝不能用"排行榜接口"当宇宙 —— 排行榜天然有返回条数上限，
-- 会把 5900 只 A 股截断成 1200 只，导致涨跌家数、涨停跌停、成交额全部错误。
CREATE TABLE IF NOT EXISTS stock_list (
    code       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    market     INTEGER NOT NULL,   -- 0=深 1=沪 2=北
    board      TEXT NOT NULL,      -- main / gem / star / bj
    updated_at TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_stock_list_active ON stock_list(active);

-- 历史数据状态清单：避免每次启动扫描 50 万个文件才知道"哪天全不全"
CREATE TABLE IF NOT EXISTS history_manifest (
    trade_date       TEXT NOT NULL,
    granularity      INTEGER NOT NULL,
    expected_symbols INTEGER NOT NULL DEFAULT 0,
    actual_symbols   INTEGER NOT NULL DEFAULT 0,
    missing_symbols  INTEGER NOT NULL DEFAULT 0,
    total_rows       INTEGER NOT NULL DEFAULT 0,
    complete         INTEGER NOT NULL DEFAULT 0,
    note             TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (trade_date, granularity)
);

-- 每只股票每个交易日的落盘状态（用于增量补齐时精确定位缺失）
CREATE TABLE IF NOT EXISTS history_symbol_status (
    trade_date  TEXT NOT NULL,
    granularity INTEGER NOT NULL,
    code        TEXT NOT NULL,
    row_count   INTEGER NOT NULL DEFAULT 0,
    first_dt    TEXT NOT NULL DEFAULT '',
    last_dt     TEXT NOT NULL DEFAULT '',
    complete    INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (trade_date, granularity, code)
);
CREATE INDEX IF NOT EXISTS idx_hss_date ON history_symbol_status(trade_date, granularity);

-- 节点板块快照：保证历史复盘时板块时间轴与股票时间轴一致
CREATE TABLE IF NOT EXISTS sector_nodes (
    trade_date TEXT NOT NULL,
    node       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT '',
    payload    TEXT NOT NULL,
    PRIMARY KEY (trade_date, node)
);
CREATE INDEX IF NOT EXISTS idx_sector_nodes_date ON sector_nodes(trade_date);

CREATE TABLE IF NOT EXISTS stock_daily (
    trade_date  TEXT NOT NULL,
    code        TEXT NOT NULL,
    name        TEXT NOT NULL,
    close       REAL NOT NULL,
    change_pct  REAL NOT NULL,
    volume      REAL NOT NULL,
    amount      REAL NOT NULL,
    main_net    REAL NOT NULL,
    turnover    REAL NOT NULL DEFAULT 0,
    volume_ratio REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (trade_date, code)
);

CREATE TABLE IF NOT EXISTS sector_daily (
    trade_date  TEXT NOT NULL,
    code        TEXT NOT NULL,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'industry',
    change_pct  REAL NOT NULL,
    main_net    REAL NOT NULL,
    amount      REAL NOT NULL,
    PRIMARY KEY (trade_date, code)
);

CREATE TABLE IF NOT EXISTS watchlist (
    code       TEXT PRIMARY KEY,
    name       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"#;

struct DbInner {
    path: PathBuf,
    pool: Mutex<Vec<Connection>>,
    max_conns: usize,
    last_write: Mutex<String>,
}

#[derive(Clone)]
pub struct Db {
    inner: Arc<DbInner>,
}

struct PooledConn {
    inner: Arc<DbInner>,
    conn: Option<Connection>,
}

impl Drop for PooledConn {
    fn drop(&mut self) {
        if let Some(conn) = self.conn.take() {
            let mut pool = self.inner.pool.lock();
            if pool.len() < self.inner.max_conns {
                pool.push(conn);
            }
        }
    }
}

impl std::ops::Deref for PooledConn {
    type Target = Connection;
    fn deref(&self) -> &Connection {
        self.conn.as_ref().expect("pooled connection present")
    }
}

impl std::ops::DerefMut for PooledConn {
    fn deref_mut(&mut self) -> &mut Connection {
        self.conn.as_mut().expect("pooled connection present")
    }
}

impl Db {
    pub fn open(path: &Path) -> Result<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).context("创建数据库目录失败")?;
        }
        let conn = Self::new_connection(path)?;
        conn.execute_batch(SCHEMA_SQL)
            .context("初始化数据库表结构失败")?;

        let version: Option<i64> = conn
            .query_row("SELECT value FROM meta WHERE key='schema_version'", [], |r| {
                r.get::<_, String>(0)
            })
            .optional()?
            .and_then(|v| v.parse().ok());
        if version != Some(SCHEMA_VERSION) {
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('schema_version',?1)
                 ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                params![SCHEMA_VERSION.to_string()],
            )?;
        }

        // 轻量迁移：为既有数据库补齐新增列（不丢历史数据）
        let cols: Vec<String> = {
            let mut stmt = conn.prepare("PRAGMA table_info(board_episodes)")?;
            let rows = stmt.query_map([], |r| r.get::<_, String>(1))?;
            rows.filter_map(|r| r.ok()).collect()
        };
        if !cols.is_empty() {
            if !cols.iter().any(|c| c == "active") {
                conn.execute(
                    "ALTER TABLE board_episodes ADD COLUMN active INTEGER NOT NULL DEFAULT 1",
                    [],
                )
                .ok();
            }
            if !cols.iter().any(|c| c == "history") {
                conn.execute(
                    "ALTER TABLE board_episodes ADD COLUMN history TEXT NOT NULL DEFAULT '[]'",
                    [],
                )
                .ok();
            }
        }

        // 完整性检查（第 6.10 章：崩溃后自动恢复）
        let integrity: String =
            conn.query_row("PRAGMA quick_check", [], |r| r.get(0)).unwrap_or_default();
        if !integrity.eq_ignore_ascii_case("ok") {
            tracing::warn!(target: "app", "数据库完整性检查异常：{integrity}，尝试修复");
            let _ = conn.execute_batch("PRAGMA wal_checkpoint(TRUNCATE); VACUUM;");
        }

        Ok(Self {
            inner: Arc::new(DbInner {
                path: path.to_path_buf(),
                pool: Mutex::new(vec![conn]),
                max_conns: 4,
                last_write: Mutex::new(String::new()),
            }),
        })
    }

    fn new_connection(path: &Path) -> Result<Connection> {
        let conn = Connection::open(path).context("打开 SQLite 失败")?;
        conn.busy_timeout(std::time::Duration::from_secs(5))?;
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")
            .ok();
        Ok(conn)
    }

    fn conn(&self) -> Result<PooledConn> {
        let existing = self.inner.pool.lock().pop();
        let conn = match existing {
            Some(c) => c,
            None => Self::new_connection(&self.inner.path)?,
        };
        Ok(PooledConn {
            inner: self.inner.clone(),
            conn: Some(conn),
        })
    }

    pub fn path(&self) -> &Path {
        &self.inner.path
    }

    fn touch(&self) {
        *self.inner.last_write.lock() = crate::models::now_str();
    }

    /// 在阻塞线程池中执行数据库操作（避免阻塞 tokio 运行时）
    pub async fn call<T, F>(&self, f: F) -> Result<T>
    where
        T: Send + 'static,
        F: FnOnce(&Connection) -> Result<T> + Send + 'static,
    {
        let this = self.clone();
        tokio::task::spawn_blocking(move || {
            let conn = this.conn()?;
            f(&conn)
        })
        .await
        .context("数据库任务被取消")?
    }

    /// 同步版本，供测试与启动初始化使用
    pub fn call_sync<T, F>(&self, f: F) -> Result<T>
    where
        F: FnOnce(&Connection) -> Result<T>,
    {
        let conn = self.conn()?;
        f(&conn)
    }

    // ---------- 快照 ----------

    pub fn save_snapshot(&self, snap: &IntradaySnapshot) -> Result<()> {
        let conn = self.conn()?;
        let payload = serde_json::to_string(snap)?;
        conn.execute(
            "INSERT INTO snapshots(trade_date,node,origin,status,source,created_at,payload)
             VALUES(?1,?2,?3,?4,?5,?6,?7)
             ON CONFLICT(trade_date,node,origin) DO UPDATE SET
               status=excluded.status, source=excluded.source,
               created_at=excluded.created_at, payload=excluded.payload",
            params![
                snap.trade_date,
                snap.node,
                snap.origin,
                format!("{:?}", snap.status),
                format!("{:?}", snap.source),
                snap.created_at,
                payload
            ],
        )?;
        self.touch();
        Ok(())
    }

    pub fn load_snapshots(&self, trade_date: &str) -> Result<Vec<IntradaySnapshot>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT payload FROM snapshots WHERE trade_date=?1 ORDER BY node ASC",
        )?;
        let rows = stmt.query_map(params![trade_date], |r| r.get::<_, String>(0))?;
        let mut out = Vec::new();
        for row in rows {
            let raw = row?;
            if let Ok(snap) = serde_json::from_str::<IntradaySnapshot>(&raw) {
                out.push(snap);
            }
        }
        Ok(out)
    }

    /// 写入/更新历史清单
    pub fn upsert_manifest(&self, row: &crate::history::ManifestRow) -> Result<()> {
        let conn = self.conn()?;
        conn.execute(
            "INSERT INTO history_manifest
             (trade_date,granularity,expected_symbols,actual_symbols,missing_symbols,
              total_rows,complete,note,created_at,updated_at)
             VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?9)
             ON CONFLICT(trade_date,granularity) DO UPDATE SET
               expected_symbols=excluded.expected_symbols,
               actual_symbols=excluded.actual_symbols,
               missing_symbols=excluded.missing_symbols,
               total_rows=excluded.total_rows,
               complete=excluded.complete,
               note=excluded.note,
               updated_at=excluded.updated_at",
            params![
                row.trade_date,
                row.granularity as i64,
                row.expected_symbols as i64,
                row.actual_symbols as i64,
                row.missing_symbols as i64,
                row.total_rows,
                row.complete as i64,
                row.note,
                crate::models::now_str()
            ],
        )?;
        self.touch();
        Ok(())
    }

    /// 读取历史清单
    pub fn load_manifest(&self) -> Result<Vec<crate::history::ManifestRow>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT trade_date,granularity,expected_symbols,actual_symbols,missing_symbols,
                    total_rows,complete,note,created_at,updated_at
             FROM history_manifest ORDER BY trade_date DESC, granularity ASC",
        )?;
        let rows = stmt.query_map([], |r| {
            Ok(crate::history::ManifestRow {
                trade_date: r.get(0)?,
                granularity: r.get::<_, i64>(1)? as u32,
                expected_symbols: r.get::<_, i64>(2)? as i32,
                actual_symbols: r.get::<_, i64>(3)? as i32,
                missing_symbols: r.get::<_, i64>(4)? as i32,
                total_rows: r.get(5)?,
                complete: r.get::<_, i64>(6)? == 1,
                note: r.get(7)?,
                created_at: r.get(8)?,
                updated_at: r.get(9)?,
            })
        })?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    /// 批量写入单只股票落盘状态
    pub fn save_symbol_status(&self, rows: &[crate::history::SymbolStatus]) -> Result<usize> {
        if rows.is_empty() {
            return Ok(0);
        }
        let mut conn = self.conn()?;
        let tx = conn.transaction()?;
        {
            let now = crate::models::now_str();
            let mut stmt = tx.prepare(
                "INSERT INTO history_symbol_status
                 (trade_date,granularity,code,row_count,first_dt,last_dt,complete,updated_at)
                 VALUES(?1,?2,?3,?4,?5,?6,?7,?8)
                 ON CONFLICT(trade_date,granularity,code) DO UPDATE SET
                   row_count=excluded.row_count, first_dt=excluded.first_dt,
                   last_dt=excluded.last_dt, complete=excluded.complete,
                   updated_at=excluded.updated_at",
            )?;
            for r in rows {
                stmt.execute(params![
                    r.trade_date,
                    r.granularity as i64,
                    r.code,
                    r.row_count as i64,
                    r.first_dt,
                    r.last_dt,
                    r.complete as i64,
                    now
                ])?;
            }
        }
        tx.commit()?;
        self.touch();
        Ok(rows.len())
    }

    /// 某交易日已登记的股票数
    pub fn manifest_symbol_count(&self, trade_date: &str, granularity: u32) -> Result<usize> {
        let conn = self.conn()?;
        let n: i64 = conn.query_row(
            "SELECT COUNT(*) FROM history_symbol_status WHERE trade_date=?1 AND granularity=?2",
            params![trade_date, granularity as i64],
            |r| r.get(0),
        )?;
        Ok(n as usize)
    }

    /// 覆盖写入证券列表（全量替换，避免退市股残留）
    pub fn save_stock_list(&self, symbols: &[crate::universe::SymbolInfo]) -> Result<usize> {
        let mut conn = self.conn()?;
        let tx = conn.transaction()?;
        tx.execute("UPDATE stock_list SET active=0", [])?;
        {
            let now = crate::models::now_str();
            let mut stmt = tx.prepare(
                "INSERT INTO stock_list(code,name,market,board,updated_at,active)
                 VALUES(?1,?2,?3,?4,?5,1)
                 ON CONFLICT(code) DO UPDATE SET
                   name=excluded.name, market=excluded.market,
                   board=excluded.board, updated_at=excluded.updated_at, active=1",
            )?;
            for s in symbols {
                stmt.execute(params![s.code, s.name, s.market as i64, s.board, now])?;
            }
        }
        tx.commit()?;
        self.touch();
        Ok(symbols.len())
    }

    /// 读取证券列表
    pub fn load_stock_list(&self) -> Result<Vec<crate::universe::SymbolInfo>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT code,name,market,board FROM stock_list WHERE active=1 ORDER BY code",
        )?;
        let rows = stmt.query_map([], |r| {
            Ok(crate::universe::SymbolInfo {
                code: r.get(0)?,
                name: r.get(1)?,
                market: r.get::<_, i64>(2)? as u8,
                board: r.get(3)?,
            })
        })?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    /// 证券列表最后更新时间（判断是否需要刷新）
    pub fn stock_list_updated_at(&self) -> Result<Option<String>> {
        let conn = self.conn()?;
        let v: Option<String> = conn
            .query_row(
                "SELECT MAX(updated_at) FROM stock_list WHERE active=1",
                [],
                |r| r.get(0),
            )
            .optional()?;
        Ok(v)
    }

    pub fn stock_list_count(&self) -> Result<usize> {
        let conn = self.conn()?;
        let n: i64 = conn.query_row(
            "SELECT COUNT(*) FROM stock_list WHERE active=1",
            [],
            |r| r.get(0),
        )?;
        Ok(n as usize)
    }

    /// 保存某交易日某节点的板块快照（与股票快照同一时间轴）
    pub fn save_sector_node(
        &self,
        trade_date: &str,
        node: &str,
        source: &str,
        sectors: &[crate::models::SectorTick],
    ) -> Result<()> {
        let conn = self.conn()?;
        let payload = serde_json::to_string(sectors)?;
        conn.execute(
            "INSERT INTO sector_nodes(trade_date,node,created_at,source,payload)
             VALUES(?1,?2,?3,?4,?5)
             ON CONFLICT(trade_date,node) DO UPDATE SET
               created_at=excluded.created_at, source=excluded.source, payload=excluded.payload",
            params![trade_date, node, crate::models::now_str(), source, payload],
        )?;
        self.touch();
        Ok(())
    }

    /// 读取某交易日某节点的板块快照；不存在则返回 None（调用方必须标注为不可用）
    pub fn load_sector_node(
        &self,
        trade_date: &str,
        node: &str,
    ) -> Result<Option<Vec<crate::models::SectorTick>>> {
        Ok(self
            .load_sector_node_meta(trade_date, node)?
            .map(|(_, v)| v))
    }

    /// 读取板块快照，并一并返回落盘时的数据来源。
    ///
    /// 首页板块模块需要如实告知"这批板块来自哪个通道、共多少个"——
    /// 降级到腾讯时覆盖数明显少于东财全量，不说明就等于把截断藏起来。
    pub fn load_sector_node_meta(
        &self,
        trade_date: &str,
        node: &str,
    ) -> Result<Option<(String, Vec<crate::models::SectorTick>)>> {
        let conn = self.conn()?;
        let row: Option<(String, String)> = conn
            .query_row(
                "SELECT source, payload FROM sector_nodes WHERE trade_date=?1 AND node=?2",
                params![trade_date, node],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?;
        Ok(row.and_then(|(src, payload)| {
            serde_json::from_str::<Vec<crate::models::SectorTick>>(&payload)
                .ok()
                .map(|v| (src, v))
        }))
    }

    pub fn snapshot_nodes(&self, trade_date: &str) -> Result<Vec<String>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT node FROM snapshots WHERE trade_date=?1 AND origin='realtime' ORDER BY node",
        )?;
        let rows = stmt.query_map(params![trade_date], |r| r.get::<_, String>(0))?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    // ---------- 榜单事件（事件驱动，第 8.13 章）----------

    /// 记录一次上榜事件；返回 true 表示这是一次“新的”上榜事件
    pub fn record_ranking_event(&self, ev: &RankingEvent) -> Result<bool> {
        let conn = self.conn()?;
        let changed = conn.execute(
            "INSERT OR IGNORE INTO ranking_events
             (trade_date,stock_code,stock_name,rank_type,rank_time,rank_position,value,source)
             VALUES(?1,?2,?3,?4,?5,?6,?7,?8)",
            params![
                ev.trade_date.to_string(),
                ev.stock_code,
                ev.stock_name,
                ev.rank_type,
                ev.rank_time,
                ev.rank_position as i64,
                ev.value,
                ev.source
            ],
        )?;
        self.touch();
        Ok(changed > 0)
    }

    /// 记录一次榜单停留状态，返回 `true` 表示这是一次**新的进入事件**。
    ///
    /// 事件驱动语义（第 8.13 章）：
    ///   * 首次进入          → appearances = 1，记一次事件
    ///   * 持续停留（active）→ 只更新排名/时间，**不增加事件数**
    ///   * 掉出后重新进入    → appearances += 1，记一次事件
    pub fn record_episode(
        &self,
        trade_date: &str,
        board_id: &str,
        code: &str,
        name: &str,
        time: &str,
        rank: usize,
        value: f64,
    ) -> Result<bool> {
        let conn = self.conn()?;
        let existing: Option<(i64, String)> = conn
            .query_row(
                "SELECT active,history FROM board_episodes
                 WHERE trade_date=?1 AND board_id=?2 AND stock_code=?3",
                params![trade_date, board_id, code],
                |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)),
            )
            .optional()?;

        match existing {
            Some((active, history_json)) => {
                if active == 1 {
                    // 持续停留：绝不重复计数
                    conn.execute(
                        "UPDATE board_episodes
                         SET last_seen=?1, last_rank=?2, best_rank=MIN(best_rank,?2),
                             max_value=MAX(max_value,?3), stock_name=?4
                         WHERE trade_date=?5 AND board_id=?6 AND stock_code=?7",
                        params![time, rank as i64, value, name, trade_date, board_id, code],
                    )?;
                    self.touch();
                    Ok(false)
                } else {
                    // 掉出后重新进入 → 新增一次事件
                    let mut history: Vec<String> =
                        serde_json::from_str(&history_json).unwrap_or_default();
                    history.push(time.to_string());
                    let history_json = serde_json::to_string(&history)?;
                    conn.execute(
                        "UPDATE board_episodes
                         SET last_seen=?1, last_rank=?2, best_rank=MIN(best_rank,?2),
                             max_value=MAX(max_value,?3), stock_name=?4,
                             appearances=appearances+1, active=1, history=?5
                         WHERE trade_date=?6 AND board_id=?7 AND stock_code=?8",
                        params![
                            time, rank as i64, value, name, history_json,
                            trade_date, board_id, code
                        ],
                    )?;
                    self.touch();
                    Ok(true)
                }
            }
            None => {
                let history = serde_json::to_string(&vec![time.to_string()])?;
                conn.execute(
                    "INSERT INTO board_episodes
                     (trade_date,board_id,stock_code,stock_name,first_seen,last_seen,
                      best_rank,last_rank,max_value,appearances,active,history)
                     VALUES(?1,?2,?3,?4,?5,?5,?6,?6,?7,1,1,?8)",
                    params![trade_date, board_id, code, name, time, rank as i64, value, history],
                )?;
                self.touch();
                Ok(true)
            }
        }
    }

    /// 标记本轮已掉出榜单的区间（不清除，便于重新进入时累计事件）
    pub fn deactivate_missing_episodes(
        &self,
        trade_date: &str,
        board_id: &str,
        present_codes: &[String],
    ) -> Result<usize> {
        let conn = self.conn()?;
        if present_codes.is_empty() {
            let n = conn.execute(
                "UPDATE board_episodes SET active=0
                 WHERE trade_date=?1 AND board_id=?2 AND active=1",
                params![trade_date, board_id],
            )?;
            self.touch();
            return Ok(n);
        }
        // 使用临时表避免超长 IN 列表
        conn.execute_batch("CREATE TEMP TABLE IF NOT EXISTS _present(code TEXT PRIMARY KEY)")?;
        conn.execute("DELETE FROM _present", [])?;
        {
            let mut stmt = conn.prepare("INSERT OR IGNORE INTO _present(code) VALUES(?1)")?;
            for c in present_codes {
                stmt.execute(params![c])?;
            }
        }
        let n = conn.execute(
            "UPDATE board_episodes SET active=0
             WHERE trade_date=?1 AND board_id=?2 AND active=1
               AND stock_code NOT IN (SELECT code FROM _present)",
            params![trade_date, board_id],
        )?;
        self.touch();
        Ok(n)
    }

    /// 某交易日全部上榜区间（用于九榜统计 / 个股精选）
    pub fn load_episodes(
        &self,
        trade_date: &str,
    ) -> Result<Vec<EpisodeRow>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT board_id,stock_code,stock_name,first_seen,last_seen,best_rank,last_rank,
                    max_value,appearances,active,history
             FROM board_episodes WHERE trade_date=?1 ORDER BY stock_code",
        )?;
        let rows = stmt.query_map(params![trade_date], |r| {
            let history_json: String = r.get(10)?;
            Ok(EpisodeRow {
                board_id: r.get(0)?,
                code: r.get(1)?,
                name: r.get(2)?,
                first_seen: r.get(3)?,
                last_seen: r.get(4)?,
                best_rank: r.get::<_, i64>(5)? as usize,
                last_rank: r.get::<_, i64>(6)? as usize,
                max_value: r.get(7)?,
                appearances: r.get::<_, i64>(8)?.max(1) as u32,
                active: r.get::<_, i64>(9)? == 1,
                history: serde_json::from_str(&history_json).unwrap_or_default(),
            })
        })?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    /// 读取某交易日的全部上榜事件（按时间升序），用于重启后回灌事件统计
    pub fn load_ranking_events(&self, trade_date: &str) -> Result<Vec<RankingEvent>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT trade_date,stock_code,stock_name,rank_type,rank_time,rank_position,value,source
             FROM ranking_events WHERE trade_date=?1 ORDER BY rank_time ASC",
        )?;
        let rows = stmt.query_map(params![trade_date], |r| {
            let date: String = r.get(0)?;
            Ok(RankingEvent {
                trade_date: NaiveDate::parse_from_str(&date, "%Y-%m-%d")
                    .unwrap_or_else(|_| crate::clock::now_cn().date_naive()),
                stock_code: r.get(1)?,
                stock_name: r.get(2)?,
                rank_type: r.get(3)?,
                rank_time: r.get(4)?,
                rank_position: r.get::<_, i64>(5)? as usize,
                value: r.get(6)?,
                source: r.get(7)?,
            })
        })?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    pub fn count_events(&self, trade_date: &str, code: &str) -> Result<u32> {
        let conn = self.conn()?;
        let n: i64 = conn.query_row(
            "SELECT COUNT(*) FROM ranking_events WHERE trade_date=?1 AND stock_code=?2",
            params![trade_date, code],
            |r| r.get(0),
        )?;
        Ok(n as u32)
    }

    // ---------- 分钟数据 ----------

    pub fn save_minutes(&self, points: &[MinutePoint], names: &std::collections::HashMap<String, String>) -> Result<usize> {
        if points.is_empty() {
            return Ok(0);
        }
        let mut conn = self.conn()?;
        let tx = conn.transaction()?;
        {
            let mut stmt = tx.prepare(
                "INSERT INTO minute_data
                 (trade_date,code,name,minute,price,volume,amount,main_net,change_pct)
                 VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9)
                 ON CONFLICT(trade_date,code,minute) DO UPDATE SET
                   price=excluded.price, volume=excluded.volume, amount=excluded.amount,
                   main_net=excluded.main_net, change_pct=excluded.change_pct",
            )?;
            for p in points {
                let name = names.get(&p.code).cloned().unwrap_or_default();
                stmt.execute(params![
                    p.trade_date.to_string(),
                    p.code,
                    name,
                    p.minute,
                    p.price,
                    p.volume,
                    p.amount,
                    p.main_net_inflow,
                    0.0
                ])?;
            }
        }
        tx.commit()?;
        self.touch();
        Ok(points.len())
    }

    pub fn load_minutes_at(&self, trade_date: &str, minute: &str) -> Result<Vec<MinutePoint>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT code,trade_date,minute,price,volume,amount,main_net
             FROM minute_data WHERE trade_date=?1 AND minute=?2",
        )?;
        let rows = stmt.query_map(params![trade_date, minute], |r| {
            let code: String = r.get(0)?;
            let date: String = r.get(1)?;
            Ok(MinutePoint {
                code,
                trade_date: NaiveDate::parse_from_str(&date, "%Y-%m-%d")
                    .unwrap_or_else(|_| crate::clock::now_cn().date_naive()),
                minute: r.get(2)?,
                price: r.get(3)?,
                volume: r.get(4)?,
                amount: r.get(5)?,
                main_net_inflow: r.get(6)?,
            })
        })?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    /// 某一分钟内所有股票（含名称），用于重建榜单
    pub fn load_minutes_at_with_names(
        &self,
        trade_date: &str,
        minute: &str,
    ) -> Result<Vec<(String, String, f64, f64, f64, f64)>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT code,name,price,volume,amount,main_net FROM minute_data
             WHERE trade_date=?1 AND minute=?2",
        )?;
        let rows = stmt.query_map(params![trade_date, minute], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, f64>(2)?,
                r.get::<_, f64>(3)?,
                r.get::<_, f64>(4)?,
                r.get::<_, f64>(5)?,
            ))
        })?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    pub fn load_stock_minutes(&self, trade_date: &str, code: &str) -> Result<Vec<MinutePoint>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT code,trade_date,minute,price,volume,amount,main_net FROM minute_data
             WHERE trade_date=?1 AND code=?2 ORDER BY minute",
        )?;
        let rows = stmt.query_map(params![trade_date, code], |r| {
            let date: String = r.get(1)?;
            Ok(MinutePoint {
                code: r.get(0)?,
                trade_date: NaiveDate::parse_from_str(&date, "%Y-%m-%d")
                    .unwrap_or_else(|_| crate::clock::now_cn().date_naive()),
                minute: r.get(2)?,
                price: r.get(3)?,
                volume: r.get(4)?,
                amount: r.get(5)?,
                main_net_inflow: r.get(6)?,
            })
        })?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    /// 读取某交易日的全部分钟数据（用于导出到列式历史库）
    #[allow(clippy::type_complexity)]
    pub fn load_minutes_for_date(
        &self,
        trade_date: &str,
    ) -> Result<Vec<(String, String, String, f64, f64, f64, f64)>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT code, name, minute, price, volume, amount, main_net
             FROM minute_data
             WHERE trade_date = ?1
             ORDER BY minute, code",
        )?;
        let rows = stmt.query_map(params![trade_date], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, f64>(3)?,
                r.get::<_, f64>(4)?,
                r.get::<_, f64>(5)?,
                r.get::<_, f64>(6)?,
            ))
        })?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    /// 某交易日分钟数据覆盖的**不同股票数**（用于计算重建覆盖率）
    pub fn minute_stock_count(&self, trade_date: &str) -> Result<usize> {
        let conn = self.conn()?;
        let n: i64 = conn.query_row(
            "SELECT COUNT(DISTINCT code) FROM minute_data WHERE trade_date=?1",
            params![trade_date],
            |r| r.get(0),
        )?;
        Ok(n as usize)
    }

    pub fn minute_row_count(&self, trade_date: &str) -> Result<usize> {
        let conn = self.conn()?;
        let n: i64 = conn.query_row(
            "SELECT COUNT(*) FROM minute_data WHERE trade_date=?1",
            params![trade_date],
            |r| r.get(0),
        )?;
        Ok(n as usize)
    }

    // ---------- 资金流分钟 ----------

    pub fn save_flow_points(
        &self,
        trade_date: &str,
        scope: &str,
        code: &str,
        name: &str,
        points: &[FlowPoint],
    ) -> Result<()> {
        if points.is_empty() {
            return Ok(());
        }
        let mut conn = self.conn()?;
        let tx = conn.transaction()?;
        {
            let mut stmt = tx.prepare(
                "INSERT INTO flow_minute
                 (trade_date,scope,code,name,minute,main_net,super_large,large,medium,small)
                 VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)
                 ON CONFLICT(trade_date,scope,code,minute) DO UPDATE SET
                   main_net=excluded.main_net, super_large=excluded.super_large,
                   large=excluded.large, medium=excluded.medium, small=excluded.small",
            )?;
            for p in points {
                stmt.execute(params![
                    trade_date,
                    scope,
                    code,
                    name,
                    p.time,
                    p.main_net,
                    p.super_large,
                    p.large,
                    p.medium,
                    p.small
                ])?;
            }
        }
        tx.commit()?;
        self.touch();
        Ok(())
    }

    pub fn load_flow_points(
        &self,
        trade_date: &str,
        scope: &str,
        code: &str,
    ) -> Result<Vec<FlowPoint>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT minute,main_net,super_large,large,medium,small FROM flow_minute
             WHERE trade_date=?1 AND scope=?2 AND code=?3 ORDER BY minute",
        )?;
        let rows = stmt.query_map(params![trade_date, scope, code], |r| {
            Ok(FlowPoint {
                time: r.get(0)?,
                main_net: r.get(1)?,
                super_large: r.get(2)?,
                large: r.get(3)?,
                medium: r.get(4)?,
                small: r.get(5)?,
            })
        })?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    // ---------- 每日汇总 ----------

    pub fn save_stock_daily(&self, trade_date: &str, ticks: &[StockTick]) -> Result<()> {
        if ticks.is_empty() {
            return Ok(());
        }
        let mut conn = self.conn()?;
        let tx = conn.transaction()?;
        {
            let mut stmt = tx.prepare(
                "INSERT INTO stock_daily
                 (trade_date,code,name,close,change_pct,volume,amount,main_net,turnover,volume_ratio)
                 VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)
                 ON CONFLICT(trade_date,code) DO UPDATE SET
                   close=excluded.close, change_pct=excluded.change_pct, volume=excluded.volume,
                   amount=excluded.amount, main_net=excluded.main_net, turnover=excluded.turnover,
                   volume_ratio=excluded.volume_ratio",
            )?;
            for t in ticks {
                stmt.execute(params![
                    trade_date,
                    t.code,
                    t.name,
                    t.price,
                    t.change_pct,
                    t.volume,
                    t.amount,
                    t.main_net_inflow,
                    t.turnover_rate,
                    t.volume_ratio
                ])?;
            }
        }
        tx.commit()?;
        self.touch();
        Ok(())
    }

    pub fn load_stock_daily(&self, trade_date: &str) -> Result<Vec<StockTick>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT code,name,close,change_pct,volume,amount,main_net,turnover,volume_ratio
             FROM stock_daily WHERE trade_date=?1 ORDER BY main_net DESC",
        )?;
        let rows = stmt.query_map(params![trade_date], |r| {
            Ok(StockTick {
                code: r.get(0)?,
                name: r.get(1)?,
                price: r.get(2)?,
                change_pct: r.get(3)?,
                volume: r.get(4)?,
                amount: r.get(5)?,
                main_net_inflow: r.get(6)?,
                turnover_rate: r.get(7)?,
                volume_ratio: r.get(8)?,
                ..Default::default()
            })
        })?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    // ---------- 复盘 / 状态 ----------

    pub fn review_days(&self, limit: usize) -> Result<Vec<ReviewDay>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT d.trade_date,
                    COALESCE(s.cnt,0),
                    COALESCE(e.cnt,0),
                    COALESCE(m.cnt,0),
                    COALESCE(s.first_node,''),
                    COALESCE(s.last_node,'')
             FROM (SELECT DISTINCT trade_date FROM (
                     SELECT trade_date FROM snapshots
                     UNION SELECT trade_date FROM ranking_events
                     UNION SELECT trade_date FROM minute_data
                   )) d
             LEFT JOIN (SELECT trade_date, COUNT(*) cnt, MIN(node) first_node, MAX(node) last_node
                        FROM snapshots GROUP BY trade_date) s ON s.trade_date=d.trade_date
             LEFT JOIN (SELECT trade_date, COUNT(*) cnt FROM board_episodes GROUP BY trade_date) e
                        ON e.trade_date=d.trade_date
             LEFT JOIN (SELECT trade_date, COUNT(*) cnt FROM minute_data GROUP BY trade_date) m
                        ON m.trade_date=d.trade_date
             ORDER BY d.trade_date DESC LIMIT ?1",
        )?;
        let rows = stmt.query_map(params![limit as i64], |r| {
            let snapshots: i64 = r.get(1)?;
            let events: i64 = r.get(2)?;
            let minutes: i64 = r.get(3)?;
            Ok(ReviewDay {
                trade_date: r.get(0)?,
                snapshots: snapshots as usize,
                events: events as usize,
                minute_rows: minutes as usize,
                first_node: r.get(4)?,
                last_node: r.get(5)?,
                complete: snapshots >= 3,
            })
        })?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    pub fn set_state(&self, key: &str, value: &str) -> Result<()> {
        let conn = self.conn()?;
        conn.execute(
            "INSERT INTO app_state(key,value) VALUES(?1,?2)
             ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            params![key, value],
        )?;
        self.touch();
        Ok(())
    }

    pub fn get_state(&self, key: &str) -> Result<Option<String>> {
        let conn = self.conn()?;
        Ok(conn
            .query_row("SELECT value FROM app_state WHERE key=?1", params![key], |r| {
                r.get::<_, String>(0)
            })
            .optional()?)
    }

    pub fn watchlist(&self) -> Result<Vec<(String, String)>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare("SELECT code,name FROM watchlist ORDER BY created_at")?;
        let rows = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    pub fn add_watch(&self, code: &str, name: &str) -> Result<()> {
        let conn = self.conn()?;
        conn.execute(
            "INSERT OR REPLACE INTO watchlist(code,name,created_at) VALUES(?1,?2,?3)",
            params![code, name, crate::models::now_str()],
        )?;
        Ok(())
    }

    pub fn remove_watch(&self, code: &str) -> Result<()> {
        let conn = self.conn()?;
        conn.execute("DELETE FROM watchlist WHERE code=?1", params![code])?;
        Ok(())
    }

    /// 清理过期数据，防止数据库无限膨胀（第 8.19 章）
    pub fn purge_older_than(&self, days: u32) -> Result<usize> {
        let cutoff = (crate::clock::now_cn().date_naive()
            - chrono::Duration::days(days as i64))
        .to_string();
        let conn = self.conn()?;
        let mut removed = 0usize;
        for sql in [
            "DELETE FROM snapshots WHERE trade_date < ?1",
            "DELETE FROM ranking_events WHERE trade_date < ?1",
            "DELETE FROM board_episodes WHERE trade_date < ?1",
            "DELETE FROM minute_data WHERE trade_date < ?1",
            "DELETE FROM flow_minute WHERE trade_date < ?1",
            "DELETE FROM stock_daily WHERE trade_date < ?1",
            "DELETE FROM sector_daily WHERE trade_date < ?1",
            "DELETE FROM sector_nodes WHERE trade_date < ?1",
        ] {
            removed += conn.execute(sql, params![cutoff]).unwrap_or(0);
        }
        if removed > 0 {
            let _ = conn.execute_batch("PRAGMA wal_checkpoint(TRUNCATE);");
            tracing::info!(target: "app", removed, cutoff, "已清理过期历史数据");
        }
        Ok(removed)
    }

    pub fn stats(&self) -> DbStats {
        let size = std::fs::metadata(&self.inner.path).map(|m| m.len()).unwrap_or(0);
        let mut stats = DbStats {
            path: self.inner.path.display().to_string(),
            size_bytes: size,
            last_write: self.inner.last_write.lock().clone(),
            ..Default::default()
        };
        if let Ok(conn) = self.conn() {
            let count = |sql: &str| -> u64 {
                conn.query_row(sql, [], |r| r.get::<_, i64>(0))
                    .unwrap_or(0)
                    .max(0) as u64
            };
            stats.snapshots = count("SELECT COUNT(*) FROM snapshots");
            stats.ranking_events = count("SELECT COUNT(*) FROM ranking_events");
            stats.minute_rows = count("SELECT COUNT(*) FROM minute_data");
            stats.trading_days = count("SELECT COUNT(DISTINCT trade_date) FROM snapshots");
        }
        stats
    }
}

#[derive(Debug, Clone)]
pub struct EpisodeRow {
    pub board_id: String,
    pub code: String,
    pub name: String,
    pub first_seen: String,
    pub last_seen: String,
    pub best_rank: usize,
    pub last_rank: usize,
    pub max_value: f64,
    /// 上榜事件次数
    pub appearances: u32,
    /// 当前是否仍在榜
    pub active: bool,
    /// 每次进入的时间点
    pub history: Vec<String>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{DataSource, DataStatus};

    fn temp_db() -> (Db, tempfile::TempDir) {
        let dir = tempfile::tempdir().unwrap();
        let db = Db::open(&dir.path().join("test.sqlite")).unwrap();
        (db, dir)
    }

    fn sample_snapshot(node: &str) -> IntradaySnapshot {
        IntradaySnapshot {
            trade_date: "2026-09-30".into(),
            node: node.into(),
            origin: "realtime".into(),
            status: DataStatus::Realtime,
            source: DataSource::EastmoneyRealtime,
            created_at: "2026-09-30 09:35:00".into(),
            volume_top: vec![],
            netflow_top: vec![],
            ratio_top: vec![],
            anomaly_volume_top: vec![],
            anomaly_netflow_top: vec![],
            sector_netflow_top: vec![],
            breadth: Default::default(),
            note: String::new(),
        }
    }

    #[test]
    fn schema_created_and_snapshot_roundtrip() {
        let (db, _dir) = temp_db();
        db.save_snapshot(&sample_snapshot("09:35")).unwrap();
        db.save_snapshot(&sample_snapshot("09:40")).unwrap();
        let loaded = db.load_snapshots("2026-09-30").unwrap();
        assert_eq!(loaded.len(), 2);
        assert_eq!(loaded[0].node, "09:35");
        let nodes = db.snapshot_nodes("2026-09-30").unwrap();
        assert_eq!(nodes, vec!["09:35", "09:40"]);
    }

    /// 板块快照必须连**数据来源**一起落盘并可读回。
    ///
    /// 首页板块模块要如实告知"这批板块来自哪条通道、共多少个"——
    /// 只存 payload 不存来源，降级到腾讯（覆盖数少一半）就无法被识别。
    #[test]
    fn sector_node_snapshot_keeps_source_and_counts() {
        let (db, _dir) = temp_db();
        let mk = |code: &str, name: &str, kind: &str, net: f64| crate::models::SectorTick {
            code: code.into(),
            name: name.into(),
            kind: kind.into(),
            main_net_inflow: net,
            change_pct: 1.5,
            ..Default::default()
        };
        let sectors = vec![
            mk("BK1", "医药生物", "industry", 60.24e8),
            mk("BK2", "生物医药", "concept", 51.1e8),
            mk("BK3", "芯片概念", "concept", -196.0e8),
        ];
        db.save_sector_node("2026-09-30", "latest", "腾讯财经", &sectors)
            .unwrap();

        // 旧的仅返回 payload 的接口仍可用
        let only = db.load_sector_node("2026-09-30", "latest").unwrap().unwrap();
        assert_eq!(only.len(), 3);

        // 带来源的接口必须把来源读回来
        let (src, back) = db
            .load_sector_node_meta("2026-09-30", "latest")
            .unwrap()
            .unwrap();
        assert_eq!(src, "腾讯财经", "来源必须随快照一起落盘并可读回");
        assert_eq!(back.len(), 3);
        assert_eq!(back[0].kind, "industry");
        // 同一天再次写入应覆盖而不是追加
        db.save_sector_node("2026-09-30", "latest", "东方财富实时", &sectors[..1])
            .unwrap();
        let (src2, back2) = db
            .load_sector_node_meta("2026-09-30", "latest")
            .unwrap()
            .unwrap();
        assert_eq!(src2, "东方财富实时");
        assert_eq!(back2.len(), 1);
        // 不存在的日期必须返回 None，调用方据此标注"不可用"而不是伪造
        assert!(db
            .load_sector_node_meta("1999-01-01", "latest")
            .unwrap()
            .is_none());
    }

    #[test]
    fn ranking_event_is_idempotent() {
        let (db, _dir) = temp_db();
        let ev = RankingEvent {
            trade_date: NaiveDate::from_ymd_opt(2026, 9, 30).unwrap(),
            stock_code: "000001".into(),
            stock_name: "平安银行".into(),
            rank_type: "main_net_flow".into(),
            rank_time: "09:35".into(),
            rank_position: 1,
            value: 1.0e8,
            source: "eastmoney".into(),
        };
        assert!(db.record_ranking_event(&ev).unwrap(), "首次应写入");
        assert!(!db.record_ranking_event(&ev).unwrap(), "重复刷新不应重复计数");
        assert_eq!(db.count_events("2026-09-30", "000001").unwrap(), 1);
    }

    #[test]
    fn episode_tracks_first_and_last_seen() {
        let (db, _dir) = temp_db();
        let first = db
            .record_episode("2026-09-30", "volume", "000001", "平安银行", "09:35", 5, 100.0)
            .unwrap();
        assert!(first, "首次进入应为新事件");
        let second = db
            .record_episode("2026-09-30", "volume", "000001", "平安银行", "09:40", 3, 200.0)
            .unwrap();
        assert!(!second, "持续停留不应重复计数");
        let rows = db.load_episodes("2026-09-30").unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].first_seen, "09:35");
        assert_eq!(rows[0].last_seen, "09:40");
        assert_eq!(rows[0].best_rank, 3);
        assert_eq!(rows[0].max_value, 200.0);
        assert_eq!(rows[0].appearances, 1, "持续停留事件数应保持 1");
        assert!(rows[0].active);
        assert_eq!(rows[0].history, vec!["09:35".to_string()]);
    }

    /// 事件驱动语义在持久化层必须与内存层一致
    #[test]
    fn episode_reentry_increments_appearances() {
        let (db, _dir) = temp_db();
        let d = "2026-09-30";
        db.record_episode(d, "volume", "000001", "平安银行", "09:35", 1, 10.0)
            .unwrap();
        // 掉出榜单
        db.deactivate_missing_episodes(d, "volume", &[]).unwrap();
        let rows = db.load_episodes(d).unwrap();
        assert!(!rows[0].active, "应被标记为已掉出");

        // 重新进入
        let reentry = db
            .record_episode(d, "volume", "000001", "平安银行", "10:05", 2, 20.0)
            .unwrap();
        assert!(reentry, "掉出后重新进入应记为新事件");
        let rows = db.load_episodes(d).unwrap();
        assert_eq!(rows[0].appearances, 2);
        assert!(rows[0].active);
        assert_eq!(rows[0].history.len(), 2);
    }

    /// 只有真正掉出的标的才被标记非活跃
    #[test]
    fn deactivate_keeps_still_present_entries_active() {
        let (db, _dir) = temp_db();
        let d = "2026-09-30";
        for c in ["000001", "000002"] {
            db.record_episode(d, "volume", c, c, "09:35", 1, 1.0).unwrap();
        }
        db.deactivate_missing_episodes(d, "volume", &["000002".to_string()])
            .unwrap();
        let rows = db.load_episodes(d).unwrap();
        let a = rows.iter().find(|r| r.code == "000001").unwrap();
        let b = rows.iter().find(|r| r.code == "000002").unwrap();
        assert!(!a.active, "掉出的应标记为非活跃");
        assert!(b.active, "仍在榜的必须保持活跃");
    }

    /// 事件表只记录"进入事件"，重启回灌才不会被重复计数
    #[test]
    fn ranking_events_only_record_entry_events() {
        let (db, _dir) = temp_db();
        let d = "2026-09-30";
        // 首次进入 -> 写事件
        assert!(db.record_episode(d, "volume", "000001", "A", "09:35", 1, 1.0).unwrap());
        // 持续停留 3 轮 -> 不写事件
        for t in ["09:40", "09:45", "09:50"] {
            assert!(!db.record_episode(d, "volume", "000001", "A", t, 1, 1.0).unwrap());
        }
        assert_eq!(db.count_events(d, "000001").unwrap(), 0, "事件表由调用方按需写入");
        assert_eq!(db.load_episodes(d).unwrap()[0].appearances, 1);
    }

    #[test]
    fn minute_data_roundtrip() {
        let (db, _dir) = temp_db();
        let date = NaiveDate::from_ymd_opt(2026, 9, 30).unwrap();
        let points = vec![MinutePoint {
            code: "000001".into(),
            trade_date: date,
            minute: "09:35".into(),
            price: 12.3,
            volume: 1000.0,
            amount: 1.23e6,
            main_net_inflow: 5.0e6,
        }];
        let mut names = std::collections::HashMap::new();
        names.insert("000001".to_string(), "平安银行".to_string());
        assert_eq!(db.save_minutes(&points, &names).unwrap(), 1);
        let loaded = db.load_minutes_at("2026-09-30", "09:35").unwrap();
        assert_eq!(loaded.len(), 1);
        assert_eq!(loaded[0].code, "000001");
        assert!((loaded[0].price - 12.3).abs() < 1e-9);
        assert_eq!(db.minute_row_count("2026-09-30").unwrap(), 1);
        let with_names = db.load_minutes_at_with_names("2026-09-30", "09:35").unwrap();
        assert_eq!(with_names[0].1, "平安银行");
    }

    #[test]
    fn stock_daily_and_review() {
        let (db, _dir) = temp_db();
        let mut tick = StockTick::default();
        tick.code = "600519".into();
        tick.name = "贵州茅台".into();
        tick.price = 1500.0;
        tick.main_net_inflow = 2.0e8;
        db.save_stock_daily("2026-09-30", &[tick]).unwrap();
        let loaded = db.load_stock_daily("2026-09-30").unwrap();
        assert_eq!(loaded.len(), 1);
        assert_eq!(loaded[0].name, "贵州茅台");

        db.save_snapshot(&sample_snapshot("09:35")).unwrap();
        let days = db.review_days(10).unwrap();
        assert!(!days.is_empty());
        assert_eq!(days[0].trade_date, "2026-09-30");
    }

    #[test]
    fn state_and_watchlist() {
        let (db, _dir) = temp_db();
        db.set_state("last_node", "09:45").unwrap();
        assert_eq!(db.get_state("last_node").unwrap().as_deref(), Some("09:45"));
        db.add_watch("000001", "平安银行").unwrap();
        assert_eq!(db.watchlist().unwrap().len(), 1);
        db.remove_watch("000001").unwrap();
        assert!(db.watchlist().unwrap().is_empty());
    }

    #[test]
    fn purge_removes_old_rows() {
        let (db, _dir) = temp_db();
        db.save_snapshot(&IntradaySnapshot {
            trade_date: "2020-01-02".into(),
            ..sample_snapshot("09:35")
        })
        .unwrap();
        let removed = db.purge_older_than(30).unwrap();
        assert!(removed >= 1);
        assert!(db.load_snapshots("2020-01-02").unwrap().is_empty());
    }

    #[test]
    fn stats_reports_counts() {
        let (db, _dir) = temp_db();
        db.save_snapshot(&sample_snapshot("09:35")).unwrap();
        let stats = db.stats();
        assert_eq!(stats.snapshots, 1);
        assert_eq!(stats.trading_days, 1);
        assert!(stats.path.ends_with("test.sqlite"));
    }
}
