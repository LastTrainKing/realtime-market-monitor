# Command Center · Shell 版（无数据读取能力）

> 本文说明 `command-center`（Tauri + Rust + React 版）的 Shell 形态。

---

## 一、做法：在唯一出口切断

Python 版的做法是"换掉数据源实现"。Rust 版的数据源耦合更深——
`state.rs` 直接持有三个源实例，`collector/*` 与 `history/*` 按源的方法签名调用，
`history/preload.rs` 还引用了源模块里的常量。整层删除需要重写大半个后端。

因此这里采用**单点切断**：

```
              ┌─────────────────────────────────────┐
   source/    │ eastmoney.rs  sina.rs  tencent.rs   │   ← 原样保留
              └──────────────┬──────────────────────┘
                             │ 全部调用
              ┌──────────────▼──────────────────────┐
   net/       │  HttpClient  ← 只改这一个文件        │   ← 所有方法立即返回
              │              NetError::Disabled      │      Err(Disabled)
              └─────────────────────────────────────┘
```

`HttpClient` 原本就是**全部外部请求的唯一出口**（全局限流、去重、退避、
熔断、主机轮换都集中在这里）。把它改成"立即拒绝"，等于在整个后端
装了一个断流阀——没有任何代码路径能绕过去。

### 为什么保留上层而不删

上层按这些签名调用，保留签名就能让它们走**既有的错误降级路径**：
拿不到数据 → 回落缓存 → 缓存也没有 → 返回空。
程序仍能启动、渲染、计算，只是没有新数据。

这与 Python 版的原则一致：**换实现，不砍层**。

---

## 二、改了什么

| 文件 | 改动 |
|---|---|
| `backend/src/net/client.rs` | 重写。移除 `reqwest`，所有 `get_*` 方法立即返回 `NetError::Disabled`。新增 `blocked_count()` 统计拦截次数。保留 `decode_auto` / `parse_json_body` 两个纯函数供本地解析复用 |
| `backend/Cargo.toml` | 移除 `reqwest` 依赖 |
| `backend/src/logging.rs` | 日志过滤串去掉 `reqwest=warn` |
| `.github/workflows/shell-verify.yml` | 新增。CI 跑静态守卫 + `cargo check` + 依赖树检查 + 单元测试 |

**未改动**：`source/*`、`collector/*`、`engine/*`、`history/*`、`api/*`、
`scheduler.rs`、前端全部代码。

---

## 三、能力保证

### 3.1 静态

```bash
python flowlive-native/scripts/check_no_network.py .
```

检查：运行时代码无网络库导入、无 URL 字面量、`Cargo.toml` 无 HTTP 客户端依赖。

### 3.2 依赖树

```bash
cd command-center/backend
cargo tree | grep -E '(reqwest|ureq|isahc|surf) v'   # 应无输出
```

没有 HTTP 客户端 crate，就没有发起 HTTP 请求的能力。
URL 字符串仍存在于 `source/*.rs`，但没有客户端去使用它们，等价于死数据。

### 3.3 运行期

单元测试 `net::client::tests::every_request_is_refused` 断言：
四个请求方法全部返回 `Err`，且拦截计数为 4。

**状态：未验证。** 该测试已写进 CI，但查询时该步骤仍为 `in_progress`
（编译测试二进制耗时较长），未看到最终 conclusion。

---

## 四、两种形态：Rust 版没有这个开关

Python 版有 `FLOWLIVE_SHELL_MODE=pure|replay`。**Rust 版没有。**

`grep -rn "SHELL_MODE\|shell_mode" command-center/backend/src/` 无结果——
这一版的行为是固定的：所有取数请求被拒，本地读取路径可用。
不存在"纯空壳 / 离线复盘终端"的切换，也没有对应的环境变量或配置项。

### 4.1 本地读取路径确实存在

| 文件 | 提供的接口 | 是否经过 `HttpClient` |
|---|---|---|
| `history/parquet_store.rs` | `query_stock` / `query_market_at` / `query_market_range` / `query_codes` / `read_series` / `series_dates` / `available_dates` | **否** |
| `history/duck.rs` | `stock_minute` / `market_at` / `market_range` / `series` / `trading_dates` / `stock_row_count` | **否** |
| `history/manifest.rs` | 清单解析（纯结构体） | **否** |

这些接口直接读本地 Parquet / DuckDB，不碰网络层。

### 4.2 但 `history/` 也有会抓数的一半

| 文件 | 行为 |
|---|---|
| `history/fetcher.rs` | `fetch_date()` 内部调 `sina.minute_kline()` |
| `history/preload.rs` | `fetch_index_series_multi_day()` 调 `em.index_minute_kline()`；`fetch_date_minutes()` 调 `sina.minute_kline()` |

这些调用现在全部拿到 `Err(NetError::Disabled)`，会走既有错误路径。

### 4.3 因此：回放是否可用，未验证

**不能写"离线回放天然可用"。** 准确的说法是：

- 纯本地读接口存在且不经过网络层（**代码事实**）；
- 抓数接口会失败（**代码事实**）；
- **"用已有 Parquet 在界面上回放"这个端到端流程，没有运行证据**（**未验证**）。

要验证它，需要在装有 Rust + WebView2 的机器上构建并实际打开界面看历史页签。
CI 只做 `cargo check` 与单测，不证明这件事。

---

## 五、尚未完成（如实标注）

1. **本机未编译**：本机无 Rust 工具链。编译正确性由 CI 保证——
   `cargo check` 与「依赖树无 HTTP 客户端」两步**均已 success**，
   见 [run 41040147833](https://github.com/LastTrainKing/realtime-market-monitor/actions/runs/41040147833)。
2. **单元测试结论未取到**：同上，该步骤查询时仍在跑。
3. **未做前端改动**：前端不直接访问网络（设计如此），故无需改动。
4. **未做 Tauri 打包**：需要 Windows + WebView2 环境，在目标机器上执行。
5. **`collector/*` 仍在**：它们现在每次调用都拿到 `Err(Disabled)`，
   会走降级路径。彻底删除它们需要先抽出 `Snapshot` DTO，属下一阶段工作。
6. **`source/*.rs` 里的 URL 常量未删**：没有 HTTP 客户端，这些字符串是死数据。
   清掉它们会牵动 `collector/*` 的调用点，属下一阶段工作。
7. **离线回放未验证**：见 [§四](#四两种形态rust-版没有这个开关)。
8. **无 `FLOWLIVE_SHELL_MODE` 开关**：两种形态只存在于 Python 版。

---

## 六、免责

本项目为学习研究用途，不构成投资建议。
Shell 版的设计目标之一，就是彻底切断对第三方行情接口的访问。
