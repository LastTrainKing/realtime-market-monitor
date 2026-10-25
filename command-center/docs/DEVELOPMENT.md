# DEVELOPMENT — 开发与构建说明

> 版本 1.1.0 · 本文档中的所有命令都在本项目上**实际执行过**。

---

## 1. 环境要求

| 项 | 本机实际版本 | 说明 |
| --- | --- | --- |
| 操作系统 | Windows 10/11 x64 | Tauri 桌面外壳仅支持 Windows 目标 |
| Rust | **1.98.1** (`48a229cea`, 2026-09-01) | 需要 MSVC 工具链（`x86_64-pc-windows-msvc`） |
| Cargo | 1.98.1 | — |
| Node.js | **22.22.2** | 构建前端与 Tauri CLI |
| npm | 随 Node | — |
| C++ 生成工具 | MSVC（Visual Studio Build Tools） | 编译 `rusqlite`（bundled SQLite）与 `duckdb`（bundled） |
| WebView2 Runtime | Windows 11 自带 | Windows 10 需安装 |

**为什么需要 MSVC 而不是 MinGW**：`rusqlite` 与 `duckdb` 都使用 `bundled` 特性，
会编译 C/C++ 源码，必须用 MSVC 工具链。

---

## 2. 目录结构

```
capital-flow-command-center/
├── backend/            Rust 后端（Axum 服务 + 采集 + 历史库）
│   ├── src/
│   └── Cargo.toml
├── frontend/           React + TypeScript 前端（Vite）
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── tauri/              Tauri 2.x 桌面外壳（相当于 src-tauri）
│   ├── src/lib.rs
│   ├── tauri.conf.json
│   ├── capabilities/
│   └── icons/
├── scripts/            构建 / 验收 / 打包脚本
├── docs/              文档（含 schema.sql 与实测证据）
└── README.md
```

> 说明：Tauri 目录名为 `tauri/`（而非默认的 `src-tauri/`），
> 因为外壳工程独立于前端工程，两者平级。

---

## 3. 依赖安装

```bash
# 后端（Rust）—— 首次会编译 bundled SQLite 与 DuckDB，耗时较长
cd backend && cargo fetch && cd ..

# 前端（Node）
cd frontend && npm install && cd ..

# Tauri CLI
cd tauri && npm install && cd ..
```

**首次编译 DuckDB 很慢**（本机实测约 **7 分 10 秒**），且必须设置：

```bash
export DUCKDB_DISABLE_UNITY_BUILD=1
```

否则 `libduckdb-sys` 的 unity build 会编译失败（退出码 2）。
该变量只在**首次**编译 DuckDB 时需要；之后有缓存即可。

> 若在受限环境中构建，注意编译过程需要解压 DuckDB 源码包，
> 沙箱化的临时目录权限会导致 `PermissionDenied`。

---

## 4. 常用命令

### 4.1 后端

```bash
cd backend

# 单元测试（213 个）
cargo test --lib

# 调试构建
cargo build

# 发布构建（产物 backend/target/release/capital_backend.exe，约 48.8 MB）
cargo build --release

# 单独运行后端（调试用，需指定端口与数据目录）
./target/release/capital_backend.exe --port 47821 --data-dir ./tmp-data
```

### 4.2 前端

```bash
cd frontend

# 类型检查
npx tsc -b --pretty false

# 单元测试（56 个 / 6 个文件）
npx vitest run

# 生产构建（产物 frontend/dist）
npm run build

# 开发服务器（端口 5183）
npm run dev
```

### 4.3 桌面外壳与安装包

```bash
cd tauri
npx tauri build      # 产物 tauri/target/release/ 与 bundle/nsis/*.exe
```

### 4.4 一键发布构建

```bash
bash scripts/build-release.sh
```

流程：后端测试 → 后端 Release → 前端类型检查 + 测试 → 前端生产构建 →
Tauri 构建 + NSIS 安装包 → 打印产物清单（**按 `tauri.conf.json` 的版本号精确匹配安装包**，
并提示目录中遗留的其它版本）。

> 若 `frontend/dist` 已存在，Vite 会先清空它；
> 在受限环境下该删除动作可能被安全策略拦截，可先手动清空再构建。

---

## 5. 验收脚本

| 脚本 | 覆盖 | 依赖 |
| --- | --- | --- |
| `scripts/e2e-api-check.sh` | 启动 → 鉴权边界 → 只读/写接口 → 数据真实性 → 优雅关闭（41 项断言） | 需已构建的后端 |
| `scripts/verify-homepage-modules.sh` | 首页四个核心模块端到端 | 使用 `docs/evidence/parquet-history` 下的真实样本 |
| `scripts/verify-history-system.sh` | 历史静态分钟数据系统 | 需联网 |
| `scripts/verify-history-fix.sh` | 第二轮评审问题回归 | 需联网 |
| `scripts/count-stats.sh` | 统计代码规模（交付报告中的数字必须由它产出） | — |
| `scripts/package-delivery.sh` | 生成源码包与交付包 | 需已生成安装包 |
| `scripts/capture-screenshots.ps1` | 采集运行截图 | **需可交互桌面会话**，见下 |

### 截图脚本的注意事项

WebView2 走 DirectComposition 硬件合成：

* `PrintWindow(PW_RENDERFULLCONTENT)` 在本环境返回**纯色空帧**（只有窗口背景色）；
* 桌面 DC `CopyFromScreen` 在应用全屏时也拿不到内容。

脚本因此改为"**按采样色数判断空白帧并丢弃**"，从机制上防止产出假截图。
脚本本体保持**纯 ASCII**（Windows PowerShell 5.1 会按 ANSI 读取无 BOM 的 `.ps1`），
非 ASCII 输出路径通过 `-OutDir` 参数传入。

---

## 6. 配置项（`config.json`）

| 分组 | 关键项 | 默认 |
| --- | --- | --- |
| `refresh` | `index_interval_secs` / `sector_interval_secs` / `market_interval_secs` / `full_scan_interval_secs` / `snapshot_interval_secs` / `minute_persist_secs` | 3 / 10 / 20 / 90 / 15 / 60 |
| `net` | `requests_per_second` / `requests_per_minute` / `max_concurrent` / `timeout_secs` / `retry_max` / `breaker_failure_threshold` | 4 / 180 / 4 / 8 / 3 / 5 |
| 数据源开关 | `enable_eastmoney` / `enable_sina` / `enable_tencent` | 均 true |
| 历史库 | `history_days` / `history_granularity` | 90 / 1（分钟） |
| 榜单 | `boards[]`（id/name/kind/scope/size/enabled/descending） | 九榜 |
| 节点 | `nodes` | `09:35` / `09:40` / `09:45` |
| 其它 | `top_n` / `top_open` / `holidays` / `stock_batch_size` / `cache_ttl_secs` / `retention_days` | — |

配置在启动时校验：越界值会回落到默认值而不是让程序带病运行。

---

## 7. 调试与排错

| 现象 | 排查方向 |
| --- | --- |
| 后端未就绪 | 看 `logs/backend.log.*`；外壳会轮询 `/api/health` |
| 接口全 401 | `cache/token` 是否生成、前端是否拿到令牌（外壳启动竞态已处理，会自动重试一次） |
| 历史查询返回 0 行 | 检查 `read_parquet` 是否漏写 `hive_partitioning = false` |
| 全市场统计迟迟不出 | 首次启动需刷新证券列表（本机实测约 28 秒），之后缓存 3 天 |
| 板块只有 200 多个 | 当前走的是降级通道（腾讯单次上限 200）；看 `/api/sector/compare` 的 `source` 字段 |
| 东财接口全部 CONNECT_FAILED | 本机网络/代理对该域名不可达；程序会自动切换腾讯/新浪 |

日志分四个文件：`app`（业务事件）、`backend`（服务生命周期）、
`network`（每个请求的结果与延迟）、`error`（仅告警与错误）。
