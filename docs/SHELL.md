# Shell 版总入口

> 本文件是 **Shell 版（无数据读取能力）** 的唯一入口。
> 从仓库根 README 点一次到这里，再点一次到任何一份细节文档。

**状态**：分支预览，未打 tag。
**日期**：2026-10-30。
**分支**：`shell/no-data-read`。

---

## 一、这是什么

把两个看盘版本（Python 原生版、Tauri 指挥中心版）改造成**不具备联网取数能力**的发布形态：
保留界面、算法与复盘能力，移除全部联网取数实现。

### 为什么做

上游行情接口已对爬虫来源做**风控标记**。继续调用不会"偶尔失败"，
而是会让**运行机器的 IP 被封禁**——影响的是使用者本人，不只是程序。

所以这不是功能取舍，是**别让用户被牵连**。

---

## 二、需求映射

原始需求三条。下表把每条落到「代码位置 → 文档章节 → 验收方式 → 当前状态」，
一眼能看出哪条已落地、哪条只是设计。

| # | 原始需求 | 代码落点 | 文档章节 | 验收方式 | 状态 |
|---|---|---|---|---|---|
| 1 | 移除两个版本的数据读取能力 | `core/flowdata.py`（`_get` 恒 `None`）<br>`core/source.py`（三源合一）<br>`net/client.rs`（全部 `Err(Disabled)`） | [DATA-BOUNDARY.md](DATA-BOUNDARY.md) §三 | `check_no_network.py` 静态守卫 + `cargo check` + 依赖树 | **已验证** |
| 2a | 发布「纯空壳」 | `localio.mode()` 返回 `pure` 时拒绝导入 | [flowlive-native/SHELL.md](../flowlive-native/SHELL.md) §二 | 单元级：`import_enabled()` 为 `False` | **已验证（仅 Python API 层）** |
| 2b | 发布「离线复盘终端」 | `core/localio.py` 的 `import_csv` / `import_json` / `list_panels` / `export_csv` | [flowlive-native/SHELL.md](../flowlive-native/SHELL.md) §六 | CI「离线回放链路」步骤 + 本地重跑 | **已验证（仅 Python API 层）** |
| 2c | 两种形态在界面切换 | 无 | — | — | **未做**：`app_main_v203.py` 未 import `localio`，全仓零 UI 调用点；Rust 版无此开关 |
| 3 | Agent 化 / 可私人定制 | `market-agent/`（`agent_core` + `market_tools` + `cli`） | [market-agent/README.md](https://github.com/LastTrainKing/market-agent/blob/main/README.md) | `python tests/test_core.py`（10 项） | **已验证（CLI 形态；无前端）** |

> **需求 2 的关键限定**：两种形态目前只存在于 **Python 的 API 层**。
> 两个版本的**界面都没有接导入入口**，用户无法在界面里导入数据。
> Rust 版连 `FLOWLIVE_SHELL_MODE` 这个环境变量都不存在。
> 这一条不是"没写完"，是本轮明确的范围：**只做文档，不改 UI**。

---

## 三、两种形态（当前生效范围）

| 形态 | 开关 | 能力 | 生效范围 |
|---|---|---|---|
| **离线复盘终端**（默认） | `FLOWLIVE_SHELL_MODE=replay` | `localio` 提供导入与回放 API | **仅 Python API 层**；界面未接 |
| **纯空壳** | `FLOWLIVE_SHELL_MODE=pure` | `localio.import_enabled()` 返回 `False`，导入 API 抛 `RuntimeError` | **仅 Python API 层**；界面未接 |

```bash
# Windows cmd
set FLOWLIVE_SHELL_MODE=replay

# bash
export FLOWLIVE_SHELL_MODE=replay
```

默认 `replay`。`pure` 的意义是"证明这个程序可以在完全没有数据入口的情况下启动"，
而不是"给用户用的版本"。

**Rust 版没有这个开关。** 它的行为固定：所有取数请求被拒，本地读取路径可用。

---

## 四、两版切断点（文件级）

### Python 原生版（flowlive-native）

| 文件 | 做法 |
|---|---|
| `core/flowdata.py` | 2,531 → 1,101 行。删 26 个网络实现函数；8 个取数函数改空返回；`_get()` 恒 `None` |
| `core/source.py` | 663 → 155 行。三源（eastmoney / tencent / auto）合并为单一 `local`；`CAPS` 联网能力全 `False` |
| `core/backfill.py` | `_fetch_one_daykline()` 恒 `[]` |
| `core/tradecal.py` / `core2/tradecal.py` | `_fetch_days_from_daykline()` 恒 `[]` |
| `core/opensnap.py` | `_fetch_list()` 恒 `[]` |
| `core/server.py` | 删除（旧 HTML 版后端，含代理，无引用） |

### Rust 指挥中心（command-center）

| 文件 | 做法 |
|---|---|
| `backend/src/net/client.rs` | 重写。移除 `reqwest`，全部 `get_*` 立即返回 `NetError::Disabled`；新增 `blocked_count()` |
| `backend/Cargo.toml` | 移除 `reqwest` |
| `backend/src/logging.rs` | 日志过滤串去掉 `reqwest=warn` |

**未改动**：`source/*`、`collector/*`、`engine/*`、`api/*`、`scheduler.rs`、前端全部代码。

---

## 五、5 分钟验收

全部命令从**仓库根目录**执行。

### 5.1 静态守卫（10 秒）

```bash
python flowlive-native/scripts/check_no_network.py .
```

实测输出（2026-10-30 重跑）：

```
检查文件数: 15
✓ 通过：运行时代码无网络库导入、无 URL 字面量、无 HTTP 依赖
```

退出码 0。**状态：已验证。**

### 5.2 导入冒烟（10 秒）

```bash
cd flowlive-native/build && python -c "
import sys, tempfile, importlib
sys.path.insert(0, 'core')
import flowdata as fd; fd.set_data_dir(tempfile.mkdtemp())
for m in ['source','store','archive','universe','tradingtime','tradecal',
          'salience','opensnap','leader','watchlist','backfill','localio']:
    importlib.import_module(m)
import source as src
assert src.active() == 'local'
print('12 模块导入正常，数据源 =', src.active())
"
```

**状态：已验证。**

### 5.3 离线回放链路（10 秒）

```bash
cd flowlive-native/build && python -c "
import sys, tempfile, os
sys.path.insert(0, 'core')
import flowdata as fd; d = tempfile.mkdtemp(); fd.set_data_dir(d)
import localio
p = os.path.join(d, 's.csv')
open(p,'w',encoding='utf-8-sig').write('时间,板块代码,板块名称,主力净额\n09:31,BK1036,半导体,1.5e8\n09:32,BK1036,半导体,2.1e8\n')
meta, mat = localio.import_csv(p, '20261030')
print('导入成功', mat.shape)
"
```

**状态：已验证。**

### 5.4 Rust 编译与依赖树（CI）

```bash
cd command-center/backend && cargo check --all-targets
cargo tree | grep -E '(reqwest|ureq|isahc|surf) v'   # 应无输出
```

**状态：已验证（CI）**。见 [§六 CI 结论](#六ci-结论)。

### 5.5 运行期（断网启动 / 抓包）

| 方法 | 通过标准 | 状态 |
|---|---|---|
| 断网启动原生版 | 界面正常，无报错 | **未验证**（需目标机器 + PySide6 环境） |
| 抓包观察出站请求 | 0 个请求 | **未验证**（需目标机器） |
| 防火墙全阻断该进程 | 行为不变 | **未验证** |

---

## 六、CI 结论

| 仓库 | Job | 结论 | 链接 |
|---|---|---|---|
| realtime-market-monitor | 无网络能力守卫 | ✅ success | [run 41040147833](https://github.com/LastTrainKing/realtime-market-monitor/actions/runs/41040147833) |
| realtime-market-monitor | Python 语法与导入 | ✅ success（含「导入冒烟」「离线回放链路」两步） | 同上 |
| realtime-market-monitor | Rust 后端编译 → `cargo check` | ✅ success | 同上 |
| realtime-market-monitor | Rust 后端编译 → 依赖树检查 | ✅ success（无 HTTP 客户端） | 同上 |
| realtime-market-monitor | Rust 后端编译 → 单元测试 | ⏳ 查询时仍 in_progress（编译测试二进制耗时较长） | 同上 |
| market-agent | test（10 项 + 边界守卫） | ✅ success | [run 41040154868](https://github.com/LastTrainKing/market-agent/actions/runs/41040154868) |

> **注意**：Rust 的 `cargo check` 与依赖树检查已通过，说明代码可编译、依赖树干净。
> 但 `cargo test` 尚未看到最终结论，因此**「单元测试 `every_request_is_refused` 通过」标为未验证**。

---

## 七、已知限制

| # | 限制 | 状态 |
|---|---|---|
| 1 | **界面未接导入入口**：`app_main_v203.py` 未 import `localio`，用户无法在 UI 里导入数据 | 未做（本轮只做文档） |
| 2 | **Rust 版无 `FLOWLIVE_SHELL_MODE`**：两种形态只存在于 Python API 层 | 未做 |
| 3 | **Rust 离线回放未验证**：`parquet_store` / `duck` 有纯本地读接口，但 `history/fetcher.rs`、`preload.rs` 会主动抓数（现返回 `Err(Disabled)`），端到端回放没有运行证据 | 未验证 |
| 4 | **无分钟级资金流历史**：原版靠联网取，现在只能靠导入 | 设计限制 |
| 5 | **板块成分股 / 全市场快照不可用**：依赖成分股数据的模块会跳过 | 设计限制 |
| 6 | **交易日历只读本地缓存**：缓存过期后按工作日推算兜底 | 设计限制 |
| 7 | **未打包**：无 exe / 安装包。PyInstaller 需 PySide6 环境，Tauri 需 Rust + WebView2，均在目标机器执行 | 未做 |
| 8 | **Rust 未本机编译**：本机无 cargo，编译由 CI 验证（已通过 `cargo check`） | 已由 CI 覆盖 |
| 9 | **未在真机验证断网启动 / 抓包** | 未验证 |

---

## 八、文档导航

| 文档 | 内容 |
|---|---|
| [DATA-BOUNDARY.md](DATA-BOUNDARY.md) | 数据边界定义、代码落点、三层验证、改动守则 |
| [../flowlive-native/SHELL.md](../flowlive-native/SHELL.md) | Python 版：改了什么、数据从哪来、已知限制 |
| [../command-center/SHELL.md](../command-center/SHELL.md) | Rust 版：单点切断原理、验证状态、未完成项 |
| [https://github.com/LastTrainKing/market-agent/blob/main/README.md](https://github.com/LastTrainKing/market-agent/blob/main/README.md) | Agent 工作台：快速开始、架构、权限模型、测试 |
| [README.md](README.md) | docs 目录索引 |

---

## 九、范围边界

**本轮覆盖**：

- `realtime-market-monitor` 的两个版本（flowlive-native、command-center）
- `market-agent`（新建仓库）

**不在本轮范围**：

- `ai-workbench`（43 MB）：未改动。它是 Agent 内核的来源，抽包属独立任务。
- `deepseek-harness-batch-executor`（75 KB）：未改动。

---

## 十、免责

本项目为学习研究用途，不构成投资建议。
Shell 版的设计目标之一，就是彻底切断对第三方行情接口的访问。
