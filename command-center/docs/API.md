# API — 本地 REST 接口说明

> 版本 1.1.0 · 全部接口只监听 `127.0.0.1`，除 `/api/health` 外**均需** `X-CF-Token` 请求头。
> 令牌由后端启动时随机生成，写入 `%APPDATA%\CapitalFlowCenter\cache\token`。

## 0. 通用约定

**鉴权**

```http
GET /api/dashboard HTTP/1.1
Host: 127.0.0.1:47821
X-CF-Token: <cache/token 文件内容>
```

缺少或错误令牌一律返回 `401 {"ok":false,"message":"本地访问令牌校验失败"}`。

**数据真实性信封** `Envelope<T>`

所有行情类接口都返回统一信封，前端据此展示"状态 / 来源 / 更新时间 / 说明"：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `data` | T | 业务数据 |
| `status` | string | `REALTIME` / `CACHE` / `REBUILD` / `DELAY` / `ERROR` / `UNAVAILABLE` / `WAITING` / `PARTIAL` / `DATA_SOURCE_LIMITATION` |
| `source` | string | 数据源枚举（`eastmoney_realtime` / `tencent_realtime` / `sina_realtime` / `memory_cache` / `database_replay` / `none`） |
| `source_label` | string | 数据源中文名 |
| `status_label` | string | 状态中文名 |
| `updated_at` | string | 该数据最后一次成功刷新的时间 |
| `age_secs` | number | 距今秒数 |
| `message` | string | 面向用户的原因说明（缺失/降级时非空） |

---

## 1. 生命周期

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | **免鉴权**。返回 `{status, version, service, uptime_secs, trade_date, market_status}`。外壳用它判断后端就绪 |
| GET | `/api/system/status` | 运行状态：采集器清单、网络统计、数据库统计、缓存统计、冷却中的通道 |
| GET | `/api/config` | 读取当前配置 |
| PUT | `/api/config` | 保存配置（会做范围校验并落盘） |
| POST | `/api/shutdown` | 优雅停机：保存状态 → 停止调度 → 退出进程 |
| POST | `/api/client-log` | 前端异常上报，写入后端日志（离线排查用） |

---

## 2. 首页四个核心模块

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/snapshot` | **统一数据快照**。返回 `time`（时间上下文）、`trade_date`、`series_date`、`series_fallback`、`index_series`、`flow_series`、`index_prev_close`、`index_summary`、`flow_summary`、`coverage`、`capability_note`、`missing_note`。首页四个模块**共用这一份数据**，保证交易日一致 |
| GET | `/api/timeline/point?minute=HH:MM[&trade_date=YYYY-MM-DD]` | 时点数据面板。返回该分钟的指数点位与主力/超大单/大单/中单/小单累计值，与 `/api/snapshot` 的曲线**同源同交易日** |
| GET | `/api/sector/compare` | 板块资金流入/流出对比。返回 `inflow_top` / `outflow_top` / `source` / `board_count` / `industry_count` / `concept_count` / `concept_truncated` / `note` |
| GET | `/api/history/status` | 历史库诊断：`duckdb_version`、`dates`、`coverage`、`manifest`、`granularity_capability` |
| POST | `/api/history/sync` | 触发一次历史增量补齐（后台执行） |

`series_date` 与 `trade_date` 不一致时 `series_fallback = true`，界面必须标注"非当天"。

---

## 3. 市场与榜单

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/dashboard` | 首页聚合（指数 / 市场概况 / 板块 / 九榜 / 个股 TOP / 资金曲线 / 节点） |
| GET | `/api/market/indices` | 指数行情（上证/深证/创业板/沪深300/科创50/北证50） |
| GET | `/api/market/breadth` | 涨跌家数、涨跌停、成交额、主力净流入、情绪分。附 `total_symbols` / `missing_symbols` / `complete` |
| GET | `/api/market/sectors?kind=&limit=` | 板块列表（`kind` 可选 `industry` / `concept`） |
| GET | `/api/boards` | 九个榜单的完整结果 |
| GET | `/api/boards/{id}` | 单个榜单 |
| GET | `/api/stocks?limit=N` | 全市场股票池（按主力净流入排序） |
| GET | `/api/stocks/{code}` | 个股详情（行情 + 资金曲线 + 事件聚合 + 分钟序列） |
| GET | `/api/capital/curve` | 大盘资金曲线（内存序列，数据源现成口径优先） |

榜单 id：`main_net_flow`、`main_net_ratio`、`volume`、`amount`、`volume_ratio`、`turnover`、`change_pct`、`sector_net_flow`、`sector_change_pct`。

---

## 4. 早盘节点 / 异动 / 精选 / 复盘

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/nodes` | 节点状态列表（09:35 / 09:40 / 09:45） |
| GET | `/api/nodes/{node}` | 单节点快照（成交量 Top、主力净流入 Top、板块 Top、广度） |
| POST | `/api/nodes/{node}/rebuild` | 触发该节点重建（基于历史分钟数据，不使用当前板块数） |
| GET | `/api/opening` | 开盘检测：每节点成交量 Top5 + 主力净流入 Top5 |
| GET | `/api/anomaly` | 个股异动：量比 Top10 / 成交量 Top10 / 主力净流入 Top10 |
| GET | `/api/selection` | 个股精选（事件驱动上榜统计） |
| GET | `/api/review/days` | 可复盘交易日列表 |
| GET | `/api/review/{date}` | 指定交易日的复盘数据 |

---

## 5. 自选股

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/watchlist` | 读取自选股 `[[code, name], ...]` |
| POST | `/api/watchlist` | 新增，body `{code, name?}`；非法代码返回 `ok:false` |
| DELETE | `/api/watchlist/{code}` | 删除 |

---

## 6. 错误约定

| 状态码 | 场景 |
| --- | --- |
| `200` | 业务成功（**注意**：业务级失败也用 200 + `ok:false`，例如非法股票代码） |
| `400` | 参数格式错误（如 `review/{date}` 日期格式非法） |
| `401` | 令牌缺失或错误 |
| `503` | 数据尚未就绪（如榜单仍在计算）。**不是故障**，前端应展示等待态 |
| `500` | 服务端异常（用户看不到技术细节，仅返回中文说明） |

**没有 `404` 语义的"空数据"**：数据缺失一律用 `Envelope.status` +
`message` 表达，绝不返回空数组让前端自己猜。

---

## 7. 自动化验收脚本

| 脚本 | 覆盖 |
| --- | --- |
| `scripts/e2e-api-check.sh` | 启动 → 就绪 → 鉴权边界 → 只读接口 → 写接口 → 数据真实性 → 优雅关闭（41 项断言，写接口失败同样计入 FAIL） |
| `scripts/verify-homepage-modules.sh` | 首页四个核心模块端到端 |
| `scripts/verify-history-system.sh` | 历史静态分钟数据系统 |
| `scripts/verify-history-fix.sh` | 第二轮评审问题回归 |
