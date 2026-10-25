# FINAL DELIVERY — 最终交付说明

> 项目：资金流向指挥中心 AI Edition
> 版本：**1.1.0**
> 构建日期：2026-10-02
> 平台：Windows x64

---

## 1. 这个包里有什么

```
CapitalFlowCenter/
├── FINAL_DELIVERY.md          ← 本文件
├── FINAL_SELF_CHECK.md        ← 最终自检报告（含 NOT TESTED 项）
├── release/                   ← 可直接运行与安装
│   ├── CapitalFlowCenter.exe           桌面主程序（Tauri 外壳）
│   ├── backend/
│   │   └── capital_backend.exe         后端服务（含 DuckDB）
│   └── installer/
│       └── 资金流向指挥中心_1.1.0_x64-setup.exe    Windows 安装包
├── source/                    ← 完整可重建源码（自洽的工程根目录）
│   ├── README.md
│   ├── backend/               Rust 后端（Axum + SQLite + Parquet/DuckDB）
│   ├── frontend/              React + TypeScript 前端（Vite）
│   ├── tauri/                 Tauri 2.x 桌面外壳（配置、能力、图标、外壳源码）
│   ├── scripts/               构建 / 验收 / 打包脚本
│   └── docs/                  文档（与本包 docs/ 同源）
├── data/                      ← 数据库与历史数据
│   ├── database.sqlite        运行期 SQLite（已校验 integrity_check = ok）
│   ├── schema.sql             完整建表语句（14 张业务表 + 10 个索引）
│   └── history/               历史静态分钟库（Parquet，5576 个文件）
├── config/                    ← 配置
│   ├── config.json            默认/当前配置
│   └── window.json            窗口状态
├── resources/                 ← 二进制资源
│   └── icons/                 应用图标（16 个：png + ico）
├── tests/                     ← 测试
│   ├── scripts/               E2E 与验收脚本
│   └── frontend/              前端测试文件副本
├── docs/                      ← 全部文档
│   ├── 项目完成报告.md        19 章完整开发报告（含 8 轮问题修复记录）
│   ├── 使用说明.md            用户使用说明
│   ├── ARCHITECTURE.md        架构说明
│   ├── API.md                 接口说明（27 个接口）
│   ├── DATA_SOURCES.md        数据源说明（含实测限制）
│   ├── DATA_MODEL.md          数据结构说明（含迁移记录）
│   ├── DEVELOPMENT.md         开发/构建说明
│   ├── KNOWN_ISSUES.md        已知问题与限制
│   ├── VERSION.md             版本信息（含产物 SHA-256）
│   ├── schema.sql             数据库 schema（与 data/ 内同源）
│   ├── evidence/              实测证据（真实 Parquet 样本）
│   └── screenshots/           历史界面截图（**v0.1.0**，见第 4 节说明）
└── scripts/                   ← 构建脚本（与 source/scripts 同源，便于直接执行）
```

> `source/scripts`、`source/docs` 与顶层 `scripts/`、`docs/` 是**同源副本**。
> 这样做是为了让两件事同时成立：
> ① `source/` 是一个自洽的工程根目录，进去就能 `bash scripts/build-release.sh`；
> ② 顶层按用途分类，拿到包的人不用先钻进源码目录就能找到文档和脚本。

---

## 2. 快速开始

### 2.1 只想用（推荐）

```
双击 release/installer/资金流向指挥中心_1.1.0_x64-setup.exe
```

无需管理员权限（`installMode = currentUser`），安装后桌面出现快捷方式。
**启动后请确认左上角显示 `v1.1.0`** —— 这是判断是否装成新版的唯一可靠依据。

> 首次启动约需 **75 秒**才能显示全市场统计（其中证券列表刷新约 28 秒）。
> 这段时间首页如实显示"全市场扫描进行中"，不会退回小样本数字。
> 证券列表本地缓存 3 天，之后启动很快。

### 2.2 不想安装，直接运行

```
双击 release/CapitalFlowCenter.exe
```

该目录下 `backend/capital_backend.exe` 是它需要的后端，**两者必须放在一起**。
数据仍写在 `%APPDATA%\CapitalFlowCenter`。

### 2.3 恢复数据（可选）

把 `data/database.sqlite` 复制到 `%APPDATA%\CapitalFlowCenter\data\database.sqlite`，
把 `data/history/` 复制到 `%APPDATA%\CapitalFlowCenter\data\history\`。
程序启动后即可看到交付时的历史数据。

> **不要删除 `data/history/`**：这是逐日累积的历史分钟库，
> 数据源只提供最近一个交易日，删掉后早期日期无法重新取回。

### 2.4 重新构建

```bash
cd source

# 1) 后端（首次会编译 bundled SQLite 与 DuckDB，约 7 分钟）
export DUCKDB_DISABLE_UNITY_BUILD=1     # ← 必须，否则 libduckdb-sys 编译失败
cd backend && cargo build --release && cd ..

# 2) 前端
cd frontend && npm install && npm run build && cd ..

# 3) 桌面外壳 + 安装包
cd tauri && npm install && npx tauri build && cd ..
```

或直接：

```bash
cd source && bash scripts/build-release.sh
```

环境要求见 `docs/DEVELOPMENT.md`：Rust 1.98.1 + MSVC 工具链 + Node 22.22.2。

---

## 3. 校验信息

| 文件 | 字节 | SHA-256 |
| --- | --- | --- |
| `release/CapitalFlowCenter.exe` | 11,953,152 | `67e9823d84f8d74ee5cc26f414297247ab866fc4e2142362cc7b12815eda8bd8` |
| `release/backend/capital_backend.exe` | 48,776,192 | `66e35d747142b45bdb9342950fc9637772dcd3cd3ecf81c5cf0abe1a34c7eb47` |
| `release/installer/资金流向指挥中心_1.1.0_x64-setup.exe` | 14,584,262 | `eab6c1ae5569256d129ff3984e700b16047b4082d218e5b3a01baca79e4fdceb` |

**版本号一致性**：`backend/Cargo.toml`、`tauri/Cargo.toml`、`tauri/tauri.conf.json`、
`frontend/package.json`、`tauri/package.json`、安装包文件名、界面徽标 —— 全部为 `1.1.0`。

**产物由本包源码产出**：交付前执行了完整的 `scripts/build-release.sh`
（后端测试 → 后端 Release → 前端类型检查 + 测试 → 前端生产构建 → Tauri + NSIS）。
其中**后端源码两次构建之间未改动，重建后 SHA-256 与上次完全相同**，可复现性得到验证。

**自检结果**：后端单元测试 213 通过 / 0 失败；前端 56 通过 / 0 失败；
E2E 接口验收 41 通过 / 0 失败；首页四模块验收通过。详见 `FINAL_SELF_CHECK.md`。

---

## 4. 未随包交付的文件（按用户要求逐项说明）

| 文件 / 目录 | 作用 | 为什么不打包 | 如何恢复 | 是否影响直接运行 |
| --- | --- | --- | --- | --- |
| `node_modules/`（前端、Tauri） | 前端依赖包 | 属于可再生的依赖缓存，体积数百 MB 且含平台相关的二进制；打包会显著增大体积且可能因路径差异失效 | 在 `source/frontend` 与 `source/tauri` 下执行 `npm install`。**版本已由随包的 `package-lock.json` 精确锁定**，恢复结果与原环境一致 | **否**。`release/` 里的 EXE 与安装包已编译完成，直接运行不需要任何 Node 依赖 |
| `backend/target/`、`tauri/target/` | Rust 编译中间产物 | 数 GB 的构建缓存；且 Rust 依赖按绝对路径与工具链指纹缓存，换机器后无法直接复用 | 执行 `cargo build --release`（首次约 7 分钟）。**版本已由随包的 `Cargo.lock` 精确锁定** | **否**。同上 |
| `frontend/dist/` | 前端构建输出 | 可再生；且其内容已嵌入 `release/CapitalFlowCenter.exe` | `npm run build` | **否** |
| `logs/*.log` | 运行日志 | 属于运行痕迹与噪音，且可能包含本机路径信息 | 程序运行后自动重建 | **否** |
| `cache/token` | 本地 API 访问令牌 | **安全考虑**：这是本机访问凭证，不应随包分发；且每次启动都会重新随机生成 | 程序启动时自动生成 | **否**。新环境首次启动会生成新的令牌 |
| `cache/port`、`cache/last_state.json`、`runtime.json` | 端口号与运行状态 | 与具体机器/进程绑定，换环境后无意义 | 程序启动时自动重建 | **否** |
| 运行截图（当前版本 1.1.0） | 展示最新界面 | **本环境无法生成**：WebView2 走 DirectComposition 合成，`PrintWindow` 与屏幕 DC 都只能拿到纯色帧（详见 `docs/KNOWN_ISSUES.md` C 节） | 在有可交互桌面会话的机器上执行 `powershell -File scripts/capture-screenshots.ps1 -OutDir <纯英文路径>`（脚本会自动丢弃空白帧） | **否**。截图不是运行所需资源 |
| 本机专用字体 | 界面字体 | 未使用任何自带字体文件，界面字体全部来自系统字体栈（PingFang SC / 微软雅黑 / HarmonyOS Sans 等） | 无需恢复 | **否** |

> **关于 `docs/screenshots/`**：其中的 9 张截图是**真实截图**（色数 7800–12500，非空白帧），
> 但拍摄于 **v0.1.0（2026-10-01）**，界面为当时的"市场全景"旧布局。
> 保留它们是为了留档，**不代表 1.1.0 的界面**。1.1.0 的首页已改为四个核心模块，
> 且不再存在"活跃股样本 1200 只"。查阅时请注意版本差异。

---

## 5. 已知限制（摘要，完整见 docs/KNOWN_ISSUES.md）

1. **1 分钟历史数据最多回溯 4 个交易日** —— 数据源 `datalen` 上限 1023 点，
   90 天的 1 分钟数据物理上不可能。把 `history_granularity` 改为 `30` 可立即获得 127 天覆盖。
2. **板块无历史分时序列** —— 数据源不提供历史交易日的板块分钟资金流。
3. **降级到腾讯时板块覆盖约 231 个**（东财有 400+），界面会显式告警。
4. **本机对东方财富 API 不可达**，程序自动走腾讯/新浪降级并如实标注来源。
5. **分钟成交量未做大/中/小单分级** —— 缺逐笔数据，任何分级都只能是估算。

### NOT TESTED（未实际验证，不得视为通过）

| 项 | 影响直接运行？ |
| --- | --- |
| 东财大盘资金流的覆盖范围（沪市 / 沪深两市） | 否（功能正常，仅文案保守） |
| 应用窗口在实际桌面会话中的渲染效果 | 否（需用户本机双击确认） |
| 东财概念板块的准确总数 | 否（运行时以接口声明为准） |
| `history_granularity=30` 下 90 天库的实际填充 | 否（可选配置） |
| 连续运行数日的稳定性 | 否（已有限流/熔断/增量落盘保护） |
| macOS / Linux 构建 | 否（交付目标即 Windows x64） |

---

## 6. 一句话

拿到这个包，你可以：**直接安装运行**、**保存源码**、**保存数据库与历史数据**、
**保存配置与资源**、**随时重新构建**。所有必需文件都在包内；
未打包的只有可再生的依赖缓存、运行痕迹与本机凭证 —— 且已在上表中逐项说明原因与恢复方式。
