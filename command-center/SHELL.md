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

---

## 四、两种形态

与 Python 版对齐：

| 形态 | 行为 |
|---|---|
| **纯空壳** | 界面可启动，全部面板为空，无导入入口 |
| **离线复盘终端** | 读本地 Parquet 历史库回放（`history/` 模块本就只读本地） |

Rust 版的 `history/` 从 Parquet 读数据，不经过 `HttpClient`，
因此**离线回放天然可用**，不需要额外改造。

---

## 五、尚未完成（如实标注）

1. **未在本地编译验证**：本机无 Rust 工具链。改动的编译正确性由 CI 的
   `cargo check` 保证——推上去后请看 Actions 结果。
2. **未做前端改动**：前端不直接访问网络（设计如此），故无需改动。
3. **未做 Tauri 打包**：需要 Windows + WebView2 环境，在目标机器上执行。
4. **`collector/*` 仍在**：它们现在每次调用都拿到 `Err(Disabled)`，
   会走降级路径。彻底删除它们需要先抽出 `Snapshot` DTO，
   属下一阶段工作（见重构方案）。

---

## 六、免责

本项目为学习研究用途，不构成投资建议。
Shell 版的设计目标之一，就是彻底切断对第三方行情接口的访问。
