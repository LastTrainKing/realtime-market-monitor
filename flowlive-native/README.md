# FlowLiveNative · v3.0.6 源码树

本目录是原生桌面版的**最终状态源码**（`VERSION = "3.0.6"`，2026-09-23）。
目录结构与原作者工作区**保持一致** —— 因为大部分脚本用相对路径找 `core/`、`data/`，
挪动位置会直接失效。

> **★ 如果你只是想用它看盘，而不是读代码：优先 `v2.0.4` 或 `v2.0.1`。**
> 那两个版本只走东方财富单源、概念板块有完整分钟曲线；v3.0.6 默认源是腾讯，
> 腾讯没有板块分钟历史。取舍见 [../README.md §4.4](../README.md#44-版本选用建议仓库所有者标注)。
>
> **如果你要用 v3.0.6 但想要完整分钟曲线**：在数据目录写
> `data/source.json` → `{"provider": "eastmoney"}`（或设环境变量
> `FLOWLIVE_SOURCE=eastmoney`），然后接受东财限流的风险。

## 目录

```
build/
├── app_main_v203.py           主程序（约 503 KB，单文件）
├── core/                      分析内核与数据层（14 个 .py）
├── core2/tradecal.py          ★ 同名覆盖副本，两份必须同步
├── assets/app.ico             运行期窗口图标（构建必需）
├── docs/                      v3.0.3 遗留待实测清单、节点可重建性判定
├── tools/                     部署 / 诊断 / 打包辅助脚本（23 个）
├── test_*.py                 离线单元测试
├── verify_*.py / verify_*.js 结构与行为验证
├── bench_*.py                基准测试
├── 资金流向原生版*.spec        PyInstaller 打包配置（v2.0.1 ~ v3.0.6 逐版保留）
├── build_v30x.py             一键构建 + 体积对账
└── deploy*.py                部署脚本（含事务式部署）
```

## 文件命名的一个坑

主程序文件名 `app_main_v203.py` 里的 `v203` **是历史遗留，与版本号无关**。
真正的版本号看文件内的 `VERSION` 常量。

> 这个文件在 v2.0.2 时叫 `app_main_v202.py`，v2.0.3 起改名 `app_main_v203.py`，
> 之后 v2.1.0 → v3.0.6 一直在**同一个文件上就地修改**，只递增 `VERSION`。
> 所以：**v2.1.0 ~ v3.0.6 各版本只有最终状态的源码**，中间状态不可还原。

## 快速开始

```bash
cd build
python -m venv .venv
.venv/Scripts/pip install PySide6 matplotlib numpy
.venv/Scripts/python app_main_v203.py
```

详细步骤见 [../docs/installation.md](../docs/installation.md)。

## 改代码前必读

1. `core2/tradecal.py` 会**覆盖** `core/tradecal.py`（打包时 `core2` 在 `sys.path` 里优先）。
2. 改排序 = 改截断 —— 先搜所有 `[:N]`。
3. 修「某个函数」要按**语义**找，不能按函数名找（同一逻辑有多份实现，含 JS 重写）。
4. 打包前必须清 `core/__pycache__` 与 `core2/__pycache__`，否则可能静默加载旧 `.pyc`。
5. 日志文案不要用非 GBK 字符（`⚠` `★` `✅`）—— frozen 版 stdout 是 GBK。

完整清单见 [../docs/troubleshooting.md](../docs/troubleshooting.md) 第三节，
以及 [../docs/archive-2026-09-21/技术交接文档/](../docs/archive-2026-09-21/技术交接文档/)。

## 未包含的内容

| 内容 | 原因 |
|---|---|
| `data/` | 运行期面板缓存（`panel_YYYYMMDD.npy` 等），属行情数据 |
| `logs/` | 运行日志，含本机路径与请求明细 |
| `shots/` | 截图产物 |
| `native_env/` | 打包专用虚拟环境（321 MB，可由依赖重建） |
| `build_v30x/` `dist_v30x/` | PyInstaller 构建中间产物与输出（含 `_internal/`） |
| `tmp/` | 开发中间脚本与备份 |
| `__pycache__/` `*.pyc` | 字节码 |

这些目录在 U 盘原始位置**完整保留**，未被修改或删除。
