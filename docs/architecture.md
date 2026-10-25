# 架构说明

> 本文描述 **v3.0.6 原生版** 的实际结构（以源码为准），并附指挥中心 v1.1.0 的对照。
> 未在源码中确认的能力不会出现在本文里。

## 一、整体分层

```
┌──────────────────────────────────────────────────────────────┐
│  app_main_v203.py   单文件主程序（约 503 KB）                  │
│  ├─ UI 层：6 母模块 / 14 子模块 / 40 面板（PySide6 QWidget）    │
│  ├─ 图表层：matplotlib FigureCanvasQTAgg（FanChart / IndexChart │
│  │           / FlowBarChart / ThemeBoard / LeaderGrid / StageGrid）│
│  ├─ 调度层：QTimer 自刷新 + Worker(QThread) 后台抓取            │
│  └─ 启动自检：tab tree OK / panel registry OK                 │
├──────────────────────────────────────────────────────────────┤
│  core/  分析内核与数据层（纯 Python，不依赖 Qt）                │
│   source → flowdata → store/archive → salience → 视图数据       │
├──────────────────────────────────────────────────────────────┤
│  外部：东方财富 push2 集群 / 腾讯财经 web.ifzq.gtimg.cn          │
└──────────────────────────────────────────────────────────────┘
```

**关键设计约束**：`core/` 里的模块不 import PySide6 —— 这样它们能在
无 GUI 的环境下被测试脚本直接调用（`test_*.py` 全部走这条路）。

## 二、core/ 模块职责

| 模块 | 职责 | 关键点 |
|---|---|---|
| `source.py` | 数据源注册、能力声明、解析、腾讯实现 | 8 项能力声明；`DEFAULT_PROVIDER = TENCENT`；`skip_reason(cap)` 统一跳过措辞 |
| `flowdata.py` | 全市场板块分时资金流抓取 + 面板落盘（约 124 KB，最大模块） | 长连接池 + urllib 兜底；AIMD 自适应并发；滑动窗口熔断 + 硬熔断；面板「不降级」守卫 |
| `salience.py` | 辨识度五维加权、主线聚类、关键节点时间轴、主角更替（约 47 KB） | 行中性化 `resid = dV5 - dV5.mean(axis=1, keepdims=True)` —— 任一列 NaN 会污染整行 |
| `opensnap.py` | 定点全市场快照（板块约 1000 + 个股约 5900，约 3 秒） | 准点采集 vs 补采，`on_time` 字段如实标注 |
| `tradingtime.py` | 交易时段判定（盘前/集合竞价/上午/午休/下午/盘后） | `PHASE_REFRESH_MS` 盘中 60 s，午休 `None`（不刷新） |
| `tradecal.py` | 交易日历 | 磁盘优先；能力不足时走本地缓存兜底 |
| `backfill.py` | 日级资金流历史补全 | 替代源无此能力时返回空并说明原因 |
| `watchlist.py` | 实时榜单累积池 | 只增不减，避免榜单抖动 |
| `leader.py` | 龙头领先反应（点火/跳水拐点最先动的股票） | 依赖 `sector_members` 能力 |
| `universe.py` | 板块池清洗 | 剔除统计桶与行业层级重复；「过滤前提失效」时退化为不过滤 |
| `store.py` / `archive.py` | 存储与 30 交易日滚动归档 | 原子写；按 `(数据目录, 日期)` 加进程内锁 |
| `server.py` | 本地 HTTP 服务（供网页版复用） | 仅监听 127.0.0.1 |

### 2.1 `core2/tradecal.py` 是什么

`core2/` 只有 `tradecal.py` 一个源文件（外加一个历史 `.pyc`）。
它是**同名覆盖副本** —— 打包时 `datas=[('core','core'), ('core2','core2')]`，
`core2` 在 `sys.path` 中优先级更高，因此实际加载的是 `core2/tradecal.py`。

**纪律**：两份必须同步修改。v3.0.6 的能力守卫改动就是两份一起改的，
并在注释里写明了这条纪律。

## 三、数据流

```
① 板块清单
   source.sector_list()  →  universe.clean_universe()  →  板块池
   （东财约 1000 个 → 清洗后约 400+ 概念 + 31 一级行业）

② 盘中分钟资金流
   东财：fflow/kline 按板块逐个取当天全部分时（1000 板块 = 1000 次请求，不可约）
   腾讯：无分钟历史 → 逐分钟累积；当天首个点用「种子行」建表

③ 面板（panel_YYYYMMDD.npy / .json）
   flowdata.build_panel()  →  data/panel_*.npy（矩阵：板块 × 分钟）
   meta 含 times / names / partial_history / gap_from / gap_to

④ 特征
   salience.build_features()  →  F（F.V 累计净额、F.codes、F.names…）

⑤ 视图
   40 个面板各自从 F 取数；图表走增量渲染或整幅重绘
```

## 四、刷新与网络控制（v3.0.6 的实际参数）

| 机制 | 参数 | 说明 |
|---|---|---|
| 刷新档位 | 1/2/3/5/10/15 **分钟** | 默认 1 分钟；秒级档已从菜单删除 |
| 盘中上限 | `REFRESH_CAP_MS = 60_000` | 盘后结算窗终点 `15:10` |
| 跨分钟守卫 | 记住上次真实抓取的交易分钟 | 同分钟直接跳过 → 每分钟真实抓取恒为 1 次 |
| 批量快照 | `SPOT_BATCH = 200` | 单次 `ulist.np` 的 secid 个数 |
| 自适应并发 | AIMD，区间 [3, 调用方上限] | 失败率 >15% 减半，<2% 加一 |
| 域名熔断 | 窗口 10 s / ≥12 样本 / 失败率 ≥0.85 → 打开 4 s | 打开期间**只发首次尝试、不重试** |
| 升级硬熔断 | 连续 5 轮打开且零成功 → 冷却 ×3 | 期间连首次尝试都不发 |
| 长连接探测超时 | `KA_PROBE_TIMEOUT = 3.0` | 仅在通道「未证实可用」时生效 |
| 面板不降级 | `PANEL_KEEP_RATIO = 0.6` | 同交易日板块数不足本地 60% 时保留本地面板 |
| 缺口插值上限 | `SPOT_MAX_GAP_MIN = 6` | 连续缺失 >6 分钟不再插值，保留 NaN 让曲线断开 |

> ⚠️ **默认数据源是腾讯**，不是东方财富。切回东财的三条入口见
> [data-source-risk.md](data-source-risk.md#四如何切换数据源)。

## 五、UI 结构（两级页签）

```
总览（单页）
大盘 ─┬─ 市场全景
      └─ 开盘检测
个股 ─┬─ 个股异动
      └─ 个股精选
资金排行 ─┬─ 异动雷达
          ├─ 辨识度榜
          ├─ 行业概况
          └─ 实时榜单
板块题材 ─── 题材聚簇
盘中复盘 ─┬─ 实时事件
          ├─ 主角更替
          ├─ 阶段对比
          └─ 关键节点
```

`TAB_GROUPS` 是唯一权威来源，`TAB_TREE` / `GROUP_INDEX` / `GROUP_POS` / `PAGE_GROUP`
都由它派生；`PANELS` 登记 40 个面板，启动时做守恒断言
`sorted(运行期面板id) == sorted(PANEL_IDS)`。

**禁止按位置定位**：项目反复踩过「按字面量下标切页」的坑，
新增了 `goto_module()` / `goto_page_name()` 按名换算。

## 六、指挥中心 v1.1.0 的架构对照

```
capital_flow_center.exe   Tauri 2 外壳（窗口/托盘/生命周期/后端启动）
   ├─ React UI（WebView2 渲染，零业务逻辑）
   └─ backend/capital_backend.exe   Rust Sidecar（CREATE_NO_WINDOW）
         ├─ Axum HTTP（127.0.0.1，端口 47821 起自动选择）
         ├─ Collector（index / market / sector / capital / history）
         ├─ Engine（九榜 / 开盘检测 / 异动 / 精选 / 快照重建）
         ├─ Cache（内存 TTL+LRU → SQLite WAL → Parquet 历史）
         ├─ Scheduler（8 个独立任务，分级刷新）
         └─ Net（限流 / 去重 / 退避 / 熔断 / 冷却 / 半开单探针）
```

设计原则（来自项目文档）：**业务逻辑零进 React、零进 Tauri Command**，
全部走 HTTP REST + JSON；每个响应携带 `status / source / updated_at / age_secs`，
前端按状态着色（绿=实时 / 黄=缓存或重建 / 红=异常）。

`market_time.rs` 把五个时间概念拆开：`system_time` / `server_time` /
`trade_date` / `data_timestamp` / `data_fetch_time`，并有
`assert_label_consistent()` 守卫：**非实时数据不允许出现「实时」标签**。

详细文档见 `command-center/docs/ARCHITECTURE.md` 与 `command-center/docs/DATA_MODEL.md`。
