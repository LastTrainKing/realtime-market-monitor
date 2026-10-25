# 许可状态

## 一、本仓库代码未授予开源许可

**本项目（`realtime-market-monitor`）的原创代码没有附带任何开源许可证。**

归档时对原项目做了完整核查：

- 原始目录中**不存在** `LICENSE` / `LICENCE` / `COPYING` 文件；
- 源码、文档、构建脚本中**没有**版权许可声明（copyright license notice）；
- 唯一出现的许可证文件是 **PyInstaller 打包产物内第三方库自带的**
  （numpy、PySide6/Qt、matplotlib、certifi 等），与本项目自身代码无关。

因此，按著作权法默认规则：**保留所有权利（All rights reserved）**。

这意味着，在没有得到版权所有者明确书面许可的情况下，你**不应**：

- 复制、修改、再分发本项目的原创代码；
- 将本项目代码用于商业用途；
- 以本项目名义发布衍生作品。

**本仓库公开可见，不等于授权使用。** 公开托管（public hosting）与开源授权
（open-source licensing）是两件事 —— 作者没有选择开源许可，本归档也**不会**
替作者补一个（例如擅自添加 MIT / Apache-2.0），因为那会造成「代码已被正式授权」
的错误印象。

> 若你是版权所有者并希望采用某个许可证，请自行添加 `LICENSE` 文件并更新本节。
> 常见的可选项：MIT（最宽松）、Apache-2.0（含专利授权）、AGPL-3.0（要求衍生作品开源）。

## 二、阅读与研究的边界

在**不复制、不再分发**的前提下，浏览本仓库的代码与文档用于学习、研究、
评论，通常属于合理使用的讨论范畴（具体以你所在司法辖区的法律为准）。
本仓库的文档（`README.md`、`docs/` 下由本次归档整理的说明文字）同样适用上述保留条款。

## 三、第三方依赖与许可

本项目**运行**时依赖以下第三方组件。它们各自适用自己的许可证，
**分发基于本项目的产物时必须同时遵守这些许可证**。

### 原生版（FlowLiveNative）

| 组件 | 许可证（以官方发布为准） | 说明 |
|---|---|---|
| Python (CPython 3.13) | PSF License | 打包产物内嵌 |
| PySide6 / Qt for Python | LGPL-3.0（Qt 部分另有商业许可选项） | **LGPL 有动态链接与替换要求**，商业分发前须自行评估 |
| matplotlib | PSF-based（matplotlib license，BSD 兼容） | |
| numpy | BSD-3-Clause | |
| requests / urllib3 / certifi / charset_normalizer / idna | Apache-2.0 / MIT / MPL-2.0 / MIT / MIT | 经 PyInstaller `hiddenimports` 打包 |
| PyInstaller | GPL-2.0 **with bootloader exception** | 打包工具；其例外条款允许分发打包产物 |

> ⚠️ **PySide6/Qt 的 LGPL 是最需要注意的一项**：如果你要对外分发本项目的
> exe 产物，需要满足 LGPL 关于「允许用户替换 Qt 库」等要求，
> 或取得 Qt 商业许可。本归档**不分发**任何 exe，因此不涉及该义务；
> 但你自己构建并分发时需要处理。

### 指挥中心（CapitalFlowCommandCenter）

| 组件 | 许可证 |
|---|---|
| Tauri | MIT / Apache-2.0 |
| Rust 生态依赖 | 见 `command-center/backend/Cargo.lock` 中各 crate 的 license 字段 |
| React / Vite / ECharts / Zustand | MIT / MIT / Apache-2.0 / MIT |
| DuckDB | MIT |
| WebView2 Runtime | Microsoft 许可（随 Windows 分发，不随本项目分发） |

完整清单请以 `command-center/backend/Cargo.lock`、`command-center/frontend/package-lock.json`
为准；本表仅列主要组件。

### 数据

行情数据来自第三方公开端点，**其权利归数据提供方所有**。
本仓库**不包含**任何行情数据快照（`data/`、`*.parquet` 均未上传），
仅包含代码与文档。

## 四、商标

"东方财富"、"腾讯财经"、"同花顺"等名称是其各自权利人的商标。
本项目与上述公司**没有任何关联、赞助或背书关系**，文中提及仅用于说明数据来源。
