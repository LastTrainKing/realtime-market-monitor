# DATA MODEL — 数据结构说明

> 版本 1.1.0 · 完整建表语句见同目录 `schema.sql`（由运行库导出，可直接对照）。

---

## 1. 存储总览

| 存储 | 位置 | 内容 |
| --- | --- | --- |
| SQLite | `%APPDATA%\CapitalFlowCenter\data\database.sqlite` | 快照、榜单事件、股票列表、自选股、历史库清单 |
| Parquet | `%APPDATA%\CapitalFlowCenter\data\history\` | 历史分钟数据（列式，按日期分区） |
| 配置 | `%APPDATA%\CapitalFlowCenter\config\config.json` | 用户配置 |
| 缓存 | `%APPDATA%\CapitalFlowCenter\cache\` | 端口号、访问令牌、最后状态 |
| 日志 | `%APPDATA%\CapitalFlowCenter\logs\` | `app` / `backend` / `network` / `error` 四个文件 |

**为什么 SQLite 与 Parquet 并存**：SQLite 适合"小、频繁、带主键更新"的记录
（快照、事件、列表）；分钟级行情是"大、只追加、按时间范围扫描"，
放在 Parquet 里可以只读需要的列与行组，比 SQLite 快一到两个数量级。

---

## 2. SQLite 表（15 张）

### 2.1 元信息

| 表 | 说明 |
| --- | --- |
| `meta` | 键值对。当前含 `schema_version = 4`（迁移版本号，见第 4 节） |
| `app_state` | 运行时状态键值对（重启后恢复用） |

### 2.2 股票池与自选

| 表 | 主键 | 关键列 | 说明 |
| --- | --- | --- | --- |
| `stock_list` | `code` | `name` `market` `board` `active` | **全市场股票宇宙的唯一权威来源**。`market`: 0=深 1=沪 2=北；`board`: main/gem/star/bj。实测 5571 行 |
| `watchlist` | `code` | `name` `created_at` | 自选股 |

### 2.3 榜单与事件

| 表 | 主键 | 说明 |
| --- | --- | --- |
| `ranking_events` | 自增 `id` + 唯一索引 `(trade_date, stock_code, rank_type, rank_time)` | **事件驱动**的上榜记录。首次进入记一条；停留期间不重复记；掉出后再次进入记新的一条。唯一索引保证同一分钟同一榜单同一股票只记一次 |
| `board_episodes` | `(trade_date, board_id, stock_code)` | 由 `ranking_events` 归并出的"上榜区间"。`appearances` = 上榜次数；`active` 标记当前是否仍在榜；`history` 是出现时间点的 JSON 数组。**重启后由本表回灌计数**，避免归零 |

### 2.4 行情与资金

| 表 | 主键 | 说明 |
| --- | --- | --- |
| `minute_data` | `(trade_date, code, minute)` | 程序运行期间落库的分钟行情。实测 155,733 行 |
| `flow_minute` | `(trade_date, scope, code, minute)` | 分钟资金流（含主力/超大单/大单/中单/小单） |
| `stock_daily` | `(trade_date, code)` | 个股日线快照（复盘用） |
| `sector_daily` | `(trade_date, code)` | 板块日线快照（复盘用） |

### 2.5 早盘节点

| 表 | 主键 | 说明 |
| --- | --- | --- |
| `snapshots` | `(trade_date, node, origin)` | 早盘节点快照（09:35/09:40/09:45）。`origin` 区分实时采集与历史重建 |
| `sector_nodes` | `(trade_date, node)` | **板块快照**。`node` 取值：早盘节点名，或 `latest`（当日最新板块快照，供非交易时段读取）。**落盘时连数据来源一起存**，否则收盘后无法判断这批数据来自哪条通道 |

### 2.6 历史库清单

| 表 | 主键 | 说明 |
| --- | --- | --- |
| `history_manifest` | `(trade_date, granularity)` | 每个交易日每个粒度的覆盖清单：期望股票数 / 实际股票数 / 缺失数 / 总行数 / 是否完整 / 说明 |
| `history_symbol_status` | `(trade_date, granularity, code)` | 每只股票的落盘状态（行数、首末时间、是否完整） |

---

## 3. Parquet 历史库布局

```
data/history/
├── series/
│   ├── kind=index/key=1.000001/date=2026-09-30.parquet        指数分时（239 点）
│   └── kind=market_flow/key=1.000001/date=2026-09-30.parquet  大盘资金流（240 点）
├── minute/
│   └── code=600519/date=2026-09-30.parquet                    单只股票分钟
└── minute_market/
    └── date=2026-09-30.parquet                                全市场分钟物化视图
```

**列定义**（`MinuteBar`）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `code` | Utf8 | 证券代码（指数序列用 `1.000001` 形式） |
| `trade_date` | Utf8 | `YYYY-MM-DD` |
| `minute` | Utf8 | `HH:MM`，**行情时间**，非系统时间 |
| `dt_ms` | Int64 | 北京时间毫秒时间戳（范围查询用） |
| `open` `high` `low` `close` | Float64 | 价格 |
| `volume` `amount` | Float64 | 成交量 / 成交额 |
| `main_net_inflow` | Float64 | 主力净流入 |

> **资金流序列的列复用**：`market_flow` 序列里 `open`/`close` 存主力净流入、
> `high` 存超大单、`low` 存大单、`volume` 存中单、`amount` 存小单。
> 这是为了让两类序列共用同一张 schema。**读取方必须知道这一点**，
> 否则会把"超大单"当成"当日最高价"。

**写入约定**

* 写入前按 `minute` 排序；
* 先写临时文件再原子 `rename`，避免半成品被读到；
* 全市场物化视图按 `(minute, code)` 排序，row group 大小 6000，
  使"按分钟查询"能命中单个 row group。

**查询约定（关键陷阱）**

所有 `read_parquet` 必须显式写 `hive_partitioning = false`。
否则 DuckDB 会把目录名 `code=600519` 识别为 Hive 分区列并推断成 BIGINT，
**覆盖文件内的 Utf8 `code` 列**，查询静默返回 0 行且不报错。

---

## 4. 迁移（schema_version）

迁移逻辑在 `backend/src/db/mod.rs`：启动时读取 `meta.schema_version`，
逐版本执行增量语句，最后写回新版本号。

| 版本 | 变更 |
| --- | --- |
| 1 | 初始表结构：`snapshots` / `minute_data` / `stock_daily` / `sector_daily` / `watchlist` / `app_state` |
| 2 | 新增 `ranking_events` + 唯一索引（事件驱动计数） |
| 3 | 新增 `board_episodes`（上榜区间归并） |
| 4 | `board_episodes` 增加 `active` / `history` 两列（`ALTER TABLE`，用于重启后回灌计数）；新增 `stock_list` / `sector_nodes` / `history_manifest` / `history_symbol_status` |

当前运行库：`meta.schema_version = 4`。

**迁移是幂等的**：建表用 `CREATE TABLE IF NOT EXISTS`，
加列前会检查列是否已存在，重复启动不会报错。

**恢复方式**：删掉 `database.sqlite` 后重新启动程序，后端会自动建表并迁移到最新版本，
只是历史数据会丢失。要保留数据请直接使用交付包里的 `data/database.sqlite`。

---

## 5. 数据量参考（交付时的实际值）

| 对象 | 数量 |
| --- | --- |
| `stock_list` | 5,571 行 |
| `minute_data` | 155,733 行 |
| `ranking_events` | 2,828 行 |
| `board_episodes` | 96 行 |
| `history_manifest` | 90 行 |
| `snapshots` | 3 行 |
| Parquet 文件 | 5,576 个 / 约 26 MB |
| `database.sqlite` | 约 22 MB（`integrity_check = ok`） |
