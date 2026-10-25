# 资金流向指挥中心 AI Edition

> Capital Flow Command Center AI Edition — 面向 A 股交易研究的 Windows 桌面实时盯盘终端

一款普通 Windows 用户**双击即可使用**的桌面软件：安装后自动创建桌面快捷方式，双击进入全屏实时指挥界面，
自动采集、清洗、计算并展示盘中资金行为与市场结构。

---

## 一、最终用户使用方式

```
下载安装包  资金流向指挥中心_1.0.0_x64-setup.exe
        ↓
双击安装（无需任何技术操作，不需要管理员权限）
        ↓
桌面出现快捷方式「资金流向指挥中心」
        ↓
双击快捷方式 → 全屏启动 → 进入实时盯盘界面
```

**用户不需要**：安装开发环境、输入命令、启动后台服务、打开浏览器、修改配置、手动运行脚本。

---

## 二、技术架构

| 层次 | 技术 | 说明 |
| --- | --- | --- |
| 桌面外壳 | **Tauri 2.x** | 只负责窗口管理、生命周期、系统托盘、启动/关闭后端、EXE 封装 |
| 前端 | **React 18 + TypeScript(strict) + Vite** | 只负责渲染、交互、动画、图表；**不含任何业务逻辑** |
| 状态管理 | **Zustand** | 细粒度 selector，避免整页重渲染 |
| 样式 | **CSS + CSS Modules** | Steam Big Picture 风格深色玻璃拟态 |
| 图表 | **ECharts 5** | 增量更新（`setOption` 合并），不销毁重建 |
| 后端 | **Rust + Axum + Tokio + Reqwest + Serde** | 全部业务逻辑唯一归属 |
| 数据库 | **SQLite（rusqlite, WAL）** | 节点快照 / 榜单事件 / 分钟数据 / 用户配置 |
| 日志 | **tracing + tracing-appender** | app / backend / network / error 四类日志分文件 |
| 通信 | **HTTP REST + JSON（127.0.0.1）** | 前后端完全解耦 |

### 进程模型

```
资金流向指挥中心.exe  (Tauri Shell)
        │
        ├── React UI Renderer（WebView2）
        │
        └── backend/capital_backend.exe  (Rust Sidecar，无窗口后台运行)
                  ├── Axum HTTP Server
                  ├── Data Collector（指数 / 个股 / 板块 / 资金 / 历史）
                  ├── Data Processing（清洗 / 归一化 / 异常过滤）
                  ├── Analysis Engine（九榜 / 开盘检测 / 异动 / 精选）
                  ├── Cache System（内存 → SQLite → 历史归档）
                  ├── Scheduler（统一任务调度）
                  └── Error Recovery（限流 / 退避 / 熔断 / 降级 / 冷却）
```

---

## 三、目录结构

```
capital-flow-command-center/
├── backend/                    # Rust 后端（业务逻辑全部在此）
│   └── src/
│       ├── main.rs             # 进程入口、启动/关闭流程
│       ├── api/                # REST 路由、DTO、处理器
│       ├── cache.rs            # 一级缓存（TTL + LRU）
│       ├── clock.rs            # TradingClock 统一时间服务
│       ├── collector/          # 采集器（market/sector/index/capital/history）
│       ├── config.rs           # 配置驱动框架（九榜可配置）
│       ├── db/                 # SQLite 表结构与仓储
│       ├── engine/             # 计算引擎（ranking / snapshot / indicator）
│       ├── instance.rs         # 单实例控制
│       ├── logging.rs          # 日志系统
│       ├── models.rs           # 领域模型（含真实性元数据）
│       ├── net/                # 限流 / 熔断 / 退避 / 去重 / 主机轮换
│       ├── paths.rs            # 生产级目录解析
│       ├── scheduler.rs        # 统一任务调度
│       ├── source/             # 数据源（eastmoney / tencent / sina）
│       └── state.rs            # 应用共享状态 + 数据源冷却闸门
├── frontend/                   # React 前端（纯展示层）
│   └── src/
│       ├── api/                # REST 客户端与类型
│       ├── components/         # BigCard / Chart / Navigation / Splash / ErrorBoundary …
│       ├── hooks/              # 启动引导 / 全屏 / 轮询
│       ├── pages/              # 首页 / 市场 / 资金 / 板块 / 个股 / 异动 / 复盘 / 设置
│       ├── stores/             # Zustand 全局状态
│       └── styles/             # 全局样式与设计令牌
├── tauri/                      # 桌面外壳
│   ├── src/lib.rs              # 窗口 / 托盘 / 后端进程生命周期
│   ├── tauri.conf.json         # 全屏、无边框、NSIS 打包配置
│   └── icons/                  # 应用图标
└── scripts/
    ├── build-release.sh        # 一键发布构建（测试 → 构建 → 打包）
    ├── e2e-api-check.sh        # 接口端到端验收
    └── check-dashboard.sh      # 数据完整性检查
```

---

## 四、核心能力

### 4.1 实时市场感知

- **指数行情**：上证 / 深证 / 创业板 / 沪深300 / 科创50 / 北证50
- **九榜系统**（完全配置驱动，可随时启停、扩展）：
  1. 主力净流入　2. 主力净占比　3. 成交量　4. 成交额
  5. 量比　　　　6. 换手率　　　7. 涨跌幅　8. 板块净流入　9. 板块涨幅
- **市场概况**：涨跌家数、涨跌停、两市成交额、主力净流入、情绪分
- **资金曲线**：两市主力净流入分时曲线
- **板块资金流**：行业 + 概念共 200+ 板块排名与图表

### 4.2 早盘节点检测（09:35 / 09:40 / 09:45）

每个节点生成：
- **开盘检测**：成交量 Top5、主力净流入 Top5
- **个股异动**：量比 Top10、成交量 Top10、主力净流入 Top10

### 4.3 历史回溯（关键设计）

**解决"程序没开就没有早盘数据"的问题**，三条重建通道：

1. 本机 SQLite 分钟数据（程序运行期间持续落库）
2. 东方财富分钟 K 线（可累计出节点成交量 / 成交额）
3. 腾讯分时接口（直接提供节点累计成交量 / 成交额，自带交易日可校验）

状态严格区分：`AVAILABLE` / `WAITING` / `PARTIAL` / `DATA_SOURCE_LIMITATION` / `UNAVAILABLE`，
指标整体缺失时**隐藏该榜单并说明原因**，绝不展示一排 0 或伪造数据。

### 4.4 九榜统计 · 个股精选

**事件驱动计数**（非刷新计数）：

```
首次进入榜单        → 记 1 次事件
持续停留            → 不重复计数
掉出后再次进入      → 新增 1 次事件
```

### 4.5 数据稳定性设计

| 机制 | 实现 |
| --- | --- |
| 请求限流 | 每秒 / 每分钟 / 最小间隔 / 并发上限 四重令牌桶 |
| 请求去重 | 同一 URL 并发请求合并为一次 |
| 指数退避 | 1s → 2s → 4s → 8s（叠加抖动，避免雪崩） |
| 熔断 | 按主机维度，连续失败进入 Open，冷却后半开探测 |
| 主机轮换 | push2 多镜像自动切换；网络级故障立即停止轮换 |
| 数据源冷却 | 通道整体失败后冷却 180 秒，避免每轮重新超时 |
| 多源降级 | 东方财富 → 腾讯财经 → 新浪财经 → 本地缓存 |
| 分级刷新 | 指数 3s / 板块 10s / 个股 20s / 全市场 90s / 分钟落库 60s |
| 非交易时段 | 自动降频但**不停采**，保证打开即有最近交易日数据 |

### 4.6 真实性与透明度

所有数据均携带三个字段：

```json
{ "value": 128.5, "status": "CACHE", "source": "tencent_realtime", "updated_at": "09:45:20" }
```

界面按状态显示绿（实时）/ 黄（缓存·重建·延迟）/ 红（异常·限制），
并展示数据来源与最后更新时间，**禁止统一显示"暂无数据"**。

---

## 五、开发与构建

### 环境要求

- Rust 1.80+（MSVC 工具链）
- Node.js 20+
- Windows 10 / 11 x64（需 WebView2 运行时，Win10 1803+ 通常已内置）

### 常用命令

```bash
# 后端
cd backend
cargo test                     # 单元测试（130 个）
cargo run -- --self-check      # 真实数据通道自检
cargo build --release

# 前端
cd frontend
npm install
npm run test                   # Vitest（39 个）
npm run build

# 一键发布构建（测试 + 构建 + NSIS 安装包）
bash scripts/build-release.sh

# 端到端接口验收
bash scripts/e2e-api-check.sh
```

### 产物

| 文件 | 说明 |
| --- | --- |
| `tauri/target/release/bundle/nsis/资金流向指挥中心_1.0.0_x64-setup.exe` | Windows 安装包（约 6.1 MB） |
| `tauri/target/release/capital_flow_center.exe` | 主程序 |
| `backend/target/release/capital_backend.exe` | 后端服务 |

---

## 六、运行期数据位置

| 类型 | 路径 |
| --- | --- |
| 数据根目录 | `%APPDATA%\CapitalFlowCenter` |
| 数据库 | `%APPDATA%\CapitalFlowCenter\data\database.sqlite` |
| 配置 | `%APPDATA%\CapitalFlowCenter\config\config.json` |
| 日志 | `%APPDATA%\CapitalFlowCenter\logs\{app,backend,network,error}.log` |
| 安装目录 | `%LOCALAPPDATA%\资金流向指挥中心` |

用户数据与程序文件分离，**升级只需替换程序，不需要迁移任何数据**。

---

## 七、快捷键

| 按键 | 功能 |
| --- | --- |
| `F11` | 全屏 / 窗口模式切换 |
| `1` ~ `8` | 快速切换页面 |
| `←` `→` + `Enter` | 键盘导航 |
| `Esc` | 关闭弹窗 |

---

## 八、已知限制

1. **东方财富接口的可用性受网络环境影响**。当主通道不可用时，软件自动切换到腾讯 / 新浪通道；
   切换后部分口径（如主力净占比）会按定义推导，板块榜单容量会略小。
2. **历史节点重建的资金流明细**依赖东方财富分钟资金流接口；该接口不可用时，
   成交量与价格仍可完整重建，资金榜会明确标注为"部分重建"并隐藏。
3. **量比在历史重建场景**需要历史日均成交量作为基线；基线不足时该榜单会被隐藏并说明原因。
4. **股票池只有一个：全市场**（约 5571 只），来自**本地证券列表 + 批量行情**，
   不依赖排行榜分页接口，因此在东方财富被阻断时也能从腾讯 / 新浪取全。
   早期版本存在第二个"活跃股样本"池（由排行榜接口拼出，上限 1200 只），**已彻底移除**——
   那种池子按成交额 / 换手率排序，属于**有偏样本**，用它算涨跌家数会系统性失真。
   全市场股票池未就绪时界面显示"扫描进行中"，不展示任何基于小样本的数字。
