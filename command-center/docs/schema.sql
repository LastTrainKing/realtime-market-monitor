-- 资金流向指挥中心 AI Edition — SQLite schema dump
-- 来源：运行期数据库 %APPDATA%\CapitalFlowCenter\data\database.sqlite
-- 用途：交付归档 + 结构核对；重建空库请直接运行程序（后端会自动建表并迁移）

CREATE TABLE app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE board_episodes (
    trade_date    TEXT NOT NULL,
    board_id      TEXT NOT NULL,
    stock_code    TEXT NOT NULL,
    stock_name    TEXT NOT NULL,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    best_rank     INTEGER NOT NULL,
    last_rank     INTEGER NOT NULL,
    max_value     REAL NOT NULL,
    appearances   INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1, history TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (trade_date, board_id, stock_code)
);

CREATE TABLE flow_minute (
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

CREATE TABLE history_manifest (
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

CREATE TABLE history_symbol_status (
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

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE minute_data (
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

CREATE TABLE ranking_events (
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

CREATE TABLE sector_daily (
    trade_date  TEXT NOT NULL,
    code        TEXT NOT NULL,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'industry',
    change_pct  REAL NOT NULL,
    main_net    REAL NOT NULL,
    amount      REAL NOT NULL,
    PRIMARY KEY (trade_date, code)
);

CREATE TABLE sector_nodes (
    trade_date TEXT NOT NULL,
    node       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT '',
    payload    TEXT NOT NULL,
    PRIMARY KEY (trade_date, node)
);

CREATE TABLE snapshots (
    trade_date TEXT NOT NULL,
    node       TEXT NOT NULL,
    origin     TEXT NOT NULL,
    status     TEXT NOT NULL,
    source     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload    TEXT NOT NULL,
    PRIMARY KEY (trade_date, node, origin)
);

CREATE TABLE stock_daily (
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

CREATE TABLE stock_list (
    code       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    market     INTEGER NOT NULL,   -- 0=深 1=沪 2=北
    board      TEXT NOT NULL,      -- main / gem / star / bj
    updated_at TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE watchlist (
    code       TEXT PRIMARY KEY,
    name       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_episodes_code ON board_episodes(trade_date, stock_code);

CREATE INDEX idx_flow_date ON flow_minute(trade_date, scope, code);

CREATE INDEX idx_hss_date ON history_symbol_status(trade_date, granularity);

CREATE INDEX idx_minute_date ON minute_data(trade_date, minute);

CREATE INDEX idx_ranking_code ON ranking_events(trade_date, stock_code);

CREATE INDEX idx_ranking_date ON ranking_events(trade_date);

CREATE INDEX idx_sector_nodes_date ON sector_nodes(trade_date);

CREATE INDEX idx_snapshots_date ON snapshots(trade_date);

CREATE INDEX idx_stock_list_active ON stock_list(active);

CREATE UNIQUE INDEX uq_ranking_event
    ON ranking_events(trade_date, stock_code, rank_type, rank_time);
