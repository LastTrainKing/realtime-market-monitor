# docs 索引

> 本目录存放 Shell 版（无数据读取能力）的说明文档。
> 入口：[SHELL.md](SHELL.md)。

---

## 阅读顺序

| 顺序 | 文档 | 读它做什么 |
|---|---|---|
| 1 | [SHELL.md](SHELL.md) | **从这里开始**。定位、需求映射、两种形态、5 分钟验收、已知限制 |
| 2 | [DATA-BOUNDARY.md](DATA-BOUNDARY.md) | 数据边界划在哪、怎么验证、改动守则 |
| 3 | [../flowlive-native/SHELL.md](../flowlive-native/SHELL.md) | Python 版的改造细节与数据格式 |
| 4 | [../command-center/SHELL.md](../command-center/SHELL.md) | Rust 版的单点切断与验证状态 |

Agent 部分见 [https://github.com/LastTrainKing/market-agent/blob/main/README.md](https://github.com/LastTrainKing/market-agent/blob/main/README.md)。

---

## 术语

全文统一使用以下四个词，不混用：

| 术语 | 含义 |
|---|---|
| **Shell 版** | 不具备联网取数能力的发布形态总称 |
| **纯空壳** | Shell 版的一种：无任何数据入口（`FLOWLIVE_SHELL_MODE=pure`） |
| **离线复盘终端** | Shell 版的另一种：可从本地文件导入并回放（`replay`，默认） |
| **数据边界** | 程序允许/禁止触碰数据的界线，见 DATA-BOUNDARY.md |

---

## 状态标签

文档里的状态只用四种，不用"应该""理论上""基本可用"：

| 标签 | 含义 |
|---|---|
| **已验证** | 有命令输出或 CI 结论支撑 |
| **未验证** | 未执行，或执行了但没拿到结论 |
| **失败** | 执行了且不通过 |
| **未做** | 明确不在本轮范围 |

---

## 其他目录

本仓库还有归档性质的文档（版本记录、接口说明等），不在 Shell 版范围内，
从仓库根 [README.md](../README.md) 的目录进入。
