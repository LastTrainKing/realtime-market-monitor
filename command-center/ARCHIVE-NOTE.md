# 归档说明（本目录由归档整理生成，非原作者文档）

本目录是 **CapitalFlowCommandCenter（资金流向指挥中心）v1.1.0** 的源码，
从交付包 `资金流向指挥中心_v1.1.0_源码包.zip`（5,351,995 B，169 个条目）**原样解包**，
未修改任何文件。

## 这不是原生版的升级版

指挥中心是**换语言、换架构的重新实现**，不是 `FlowLiveNative` 的升级：

| | FlowLiveNative（原生版） | 本目录（指挥中心） |
|---|---|---|
| 技术栈 | Python + PySide6 + matplotlib | Tauri 2 + Rust(Axum) + React 18 + TS + ECharts |
| 版本区间 | v1.2.5 → v3.0.6（2026-09-17 ~ 09-23） | v4.0.0 前身 → v1.0.0 → v1.1.0（09-25 ~ 10-02） |
| 版本号体系 | 满 9 进 1（`3.0.9` 之后是 `3.1.0`） | 常规语义化版本，重写后从 `1.0.0` 重新起算 |

**同一个仓库里同时出现 `v3.0.6` 和 `v1.1.0`，它们没有先后关系。**

## 目录

```
command-center/
├── README.md              原作者的项目说明
├── backend/               Rust 后端（Axum + Tokio + rusqlite + DuckDB）
│   ├── src/               api / cache / clock / collector / config / db /
│   │                      engine / history / instance / logging / models /
│   │                      net / paths / scheduler / source / state / security
│   └── Cargo.toml / Cargo.lock
├── frontend/              React + TypeScript + Vite + Zustand + ECharts
│   └── src/               api / components / hooks / pages / stores / styles / utils
├── tauri/                 桌面外壳（Rust + tauri.conf.json + icons + gen）
├── scripts/               构建 / 验收 / 打包脚本（10 个）
├── docs/                  ★ 作者的技术文档
│   ├── ARCHITECTURE.md    架构
│   ├── DATA_MODEL.md      数据模型
│   ├── DATA_SOURCES.md    数据源
│   ├── API.md             接口
│   ├── DEVELOPMENT.md     开发
│   ├── KNOWN_ISSUES.md    已知问题
│   ├── VERSION.md         版本
│   ├── schema.sql         数据库结构
│   ├── 项目完成报告.md      ★ 86 KB，19 章，含逐轮修复与实测数据
│   ├── evidence/          验证证据（Parquet 样本，**未入库**，见下）
│   └── screenshots/       运行截图
├── delivery/              交付文档（README / 使用说明 / FINAL_DELIVERY / FINAL_SELF_CHECK）
└── predecessor-v4.0.0/    前身原型的 3 份文档
```

## 未纳入本仓库的部分

| 内容 | 原因 |
|---|---|
| `docs/evidence/*.parquet`（5 个） | 行情数据样本（Parquet），属数据不属代码；`.gitignore` 已排除 |
| 构建产物（`target/`、`node_modules/`） | 可由 `Cargo.lock` / `package-lock.json` 重建 |
| `CapitalFlowCenter_Final_v1.1.0.zip`（59 MB） | 含 5,579 个 Parquet 数据文件 |
| `资金流向指挥中心_1.1.0_x64-setup.exe`（14.6 MB） | 二进制不分发，见 [../releases/README.md](../releases/README.md) |

## 前身 v4.0.0（2026-09-25）

`predecessor-v4.0.0/` 只有 3 份文档，**没有源码**：

| 文件 | 价值 |
|---|---|
| `本次更新说明.txt` | 记录了采集链路换血（1000 次/轮 → 约 10 次/轮）与数据源间歇性断连的诊断结论 |
| `数据源故障诊断报告.md` | 完整诊断过程，含**结论更正** |
| `安装说明.txt` | 安装与目录结构 |

该版本的二进制（`MyApplication.exe` 4,730,368 B 等）未入库。

## 阅读建议

1. 先看 `docs/项目完成报告.md` 的第 1、2 章（架构与文件结构），
   再看第 11、12、17 章（三轮源码评审修复）—— 那部分最能体现工程判断；
2. 想跑起来看 `docs/DEVELOPMENT.md` 与 [../docs/installation.md](../docs/installation.md) 第五节；
3. 想知道有哪些没做完，看 `docs/KNOWN_ISSUES.md` 与项目完成报告每章末尾的「未完成」小节。

## ⚠️ 安全提示

v1.0.0 的**早期构建**存在本地 API 无鉴权 + `CorsLayer::allow_origin(Any)` 的问题
（本机任意网页都可能尝试调用 `POST /api/shutdown`、`PUT /api/config`）。

**本目录是 v1.1.0 源码，已包含修复**（128 位随机令牌 + CORS 白名单）。
如果你手上还有更早的构建，请勿运行。详见 [../SECURITY.md](../SECURITY.md)。
