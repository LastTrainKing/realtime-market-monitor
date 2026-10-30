# 数据边界（DATA-BOUNDARY）

> 本文定义本项目在「数据获取」上的硬边界，以及它的验证方式。
> 任何改动若突破这条边界，视为回归。

---

## 一、边界是什么

**本项目的 Shell 形态不具备任何联网取数能力。**

- 不发起 HTTP/HTTPS 请求
- 不解析域名
- 不建立 TCP 连接
- 不读取任何远程行情接口

数据只来自两个地方：

| 来源 | 说明 |
|---|---|
| 本地已落盘的面板/快照 | 历史版本留下的 `panel_YYYYMMDD.npy + .json` |
| 用户导入的文件 | CSV / JSON，经 `localio` 规范化成面板结构 |

---

## 二、为什么划这条线

上游行情接口已对爬虫来源做**风控标记**。继续调用不会"偶尔失败"，
而是会让**运行机器的 IP 被封禁**——影响的是使用者本人，不只是程序。

所以这不是"功能取舍"，是"别让用户被牵连"。

---

## 三、边界落在哪一行代码

两版都在**单一出口**处切断，而不是散落各处。

### Python 版

```
core/source.py      ← 三个网络源合并为单一 local；CAPS 联网能力全 False
core/flowdata.py    ← _get() 恒返回 None；26 个网络实现函数已删除
core/backfill.py    ← _fetch_one_daykline() 恒返回 []
core/tradecal.py    ← _fetch_days_from_daykline() 恒返回 []
core/opensnap.py    ← _fetch_list() 恒返回 []
```

所有取数路径最终都落到 `flowdata._get()`，而它恒返回 `None`。
上层按既有的失败降级路径处理：回落缓存 → 缓存也没有 → 返回空。

### Rust 版

```
net/client.rs       ← HttpClient 全部 get_* 立即返回 NetError::Disabled
```

`HttpClient` 原本就是全部外部请求的唯一出口（限流、去重、退避、熔断、轮换都在此）。
把它改成"立即拒绝"，等于在整个后端装了一个断流阀。

上层 `source/*`、`collector/*`、`history/*` **一行未改**，走既有错误路径。

---

## 四、怎么验证

### 4.1 静态（自动，CI 每次跑）

```bash
python flowlive-native/scripts/check_no_network.py .
```

| 检查 | 通过标准 |
|---|---|
| 网络库导入 | 运行时代码零 `urllib` / `http.client` / `socket` / `ssl` / `requests` / `httpx` / `aiohttp` |
| URL 字面量 | 运行时代码零 `http(s)://` |
| Cargo 依赖 | `Cargo.toml` 无 `reqwest` / `hyper` / `ureq` / `isahc` / `surf` |

**实测（2026-10-30 重跑）**：

```
检查文件数: 15
✓ 通过：运行时代码无网络库导入、无 URL 字面量、无 HTTP 依赖
```

退出码 0。**状态：已验证。**

#### 覆盖范围（重要）

这个脚本**不是全仓扫描**。它的覆盖边界是：

| 扫描 | 不扫描 |
|---|---|
| `flowlive-native/build/core/**/*.py` | `command-center/backend/src/**/*.rs` 的正文 |
| `flowlive-native/build/core2/**/*.py` | `flowlive-native/build/` 下的诊断脚本（`_diag*` / `bench_*` / `probe_*`） |
| `flowlive-native/build/app_main_v203.py` | `android/`、`web-dashboard/`、`legacy/` |
| 任意位置的 `Cargo.toml`（只查依赖声明） | 前端 `frontend/src/**` |

**Rust 源码正文不在扫描范围内**——因此 `source/*.rs` 里仍然保留着 `https://push2.eastmoney.com`
这类 URL 字符串。这不构成能力：没有 HTTP 客户端 crate，字符串只是死数据。
Rust 侧的能力保证靠**依赖树**（4.2）而不是字符串扫描。

若要把 Rust 正文也纳入扫描，需要先在 `source/*.rs` 里清掉 URL 常量——
那会牵动 `collector/*` 的调用点，属下一阶段工作，本轮不做。

### 4.2 依赖树（Rust）

```bash
cd command-center/backend
cargo tree | grep -E '(reqwest|ureq|isahc|surf) v'
```

应无输出。没有 HTTP 客户端 crate，就没有发起 HTTP 请求的能力。

### 4.3 运行期（需目标机器）

| 方法 | 通过标准 | 状态 |
|---|---|---|
| 断网启动 | 全功能可用，界面不报错 | **未验证** |
| 联网启动 + 抓包 | 进程生命周期内 **0 个出站请求** | **未验证** |
| 防火墙全阻断该进程 | 与联网时行为一致 | **未验证** |

> 这三项都需要在装有 PySide6 / WebView2 的目标机器上执行，
> 仓库与 CI 环境都做不到，因此一律标未验证，不写"应该没问题"。

### 4.4 单元测试（Rust）

`net::client::tests::every_request_is_refused` 断言四个请求方法全部返回 `Err`，
且拦截计数为 4。

**状态：未验证**。CI 的该步骤在查询时仍为 `in_progress`（编译测试二进制耗时较长），
未看到最终 conclusion，不写"通过"。

---

## 五、允许什么、禁止什么

| 行为 | 允许 | 说明 |
|---|---|---|
| 读本地面板文件 | ✅ | 核心能力 |
| 导入用户 CSV/JSON | ✅ | 离线复盘终端形态 |
| 写报告到工作区 | ✅ | 受权限闸门与配额约束 |
| 调用远程行情接口 | ❌ | **本条即边界** |
| 通过代理/镜像绕过 | ❌ | 同上，任何形式 |
| 由 Agent 工具发起取数 | ❌ | `market-agent` 的工具集同样零网络 |

---

## 六、改动守则

1. **不要为了"能跑"临时打开网络**。缺数据就缺数据，降级比联网安全。
2. **新增数据来源必须先过本文第四节的三层验证**。
3. **不要把 URL 常量留在运行时代码里**。即使当前无人调用，静态检查也会拦。
4. 若确需联网能力，应作为**独立可选模块**，默认不编译、不打包、不启用，
   并在 README 显著位置标注风险。

---

## 七、相关文档

- [flowlive-native/SHELL.md](../flowlive-native/SHELL.md) — Python 版改造说明
- [command-center/SHELL.md](../command-center/SHELL.md) — Rust 版改造说明
- [DISCLAIMER.md](../DISCLAIMER.md) — 免责声明
- [SECURITY.md](../SECURITY.md) — 安全说明
