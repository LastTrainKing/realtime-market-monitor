# HTML 看板 → 纯原生桌面软件：可行性与实施方案

> 2026-09-19　|　目标：**不依赖任何内嵌浏览器 / WebView / 网页技术**，构建真正的原生桌面应用
> 本文所有规模数字与结论均来自对现有代码的实测统计

---

## 一、先把话说清楚：这是「重写」，不是「打包」

你现在的 `资金流向.exe` 本质是「本地服务 + Edge 应用窗口」，
页面仍然是 HTML/CSS/JS，只是外面套了个 exe 壳。

要**彻底去掉浏览器内核**，就必须放弃这条路线——因为浏览器引擎本身
就是"渲染网页"的东西。替换掉它，意味着：

> **界面层必须用原生 GUI 控件与原生绘图 API 重新实现一遍。**

这不叫打包，叫**重写界面层**。数据层和算法层可以完整复用（下面详述），
但 UI 层是绕不过去的。

### 现有页面规模（实测）

| 组成 | 行数 | 说明 |
|---|---|---|
| HTML 结构 | 166 | 12 个面板的骨架 |
| CSS | 156 | 全部样式 |
| **JS** | **2,579** | 74 个函数 |
| 合计 | 2,901 | |

其中 **JS 的构成**（这决定了重写工作量）：

| 类别 | 出现次数 | 重写难度 |
|---|---|---|
| **Canvas 绘图调用（`ctx.`）** | **173** | ★★★ 高——所有图表要改用原生绘图重画 |
| DOM 操作（查询/改内容） | 33 | ★★ 中——改用控件模型 |
| localStorage 读写 | 12 | ★ 低——换成 SQLite/JSON |
| 网络请求（fetch） | 8 | ★ 低——改用 Python 的 urllib |

**173 处 Canvas 绘制是最大的一块**：主力资金曲线、辨识度时间轴、
主角更替泳道图、阶段对比、散点图……全部要重画。

---

## 二、一个关键发现：你的数据层和算法层已经是 Python 了

这是**决定性的好消息**——重写量因此大幅缩小。项目里已经存在：

| 现有资产 | 规模 | 内容 | 复用方式 |
|---|---|---|---|
| `server.py` | **1,013 行** | `fetch_sector_list` / `fetch_kline` / `fetch_index_trend` / `fetch_snapshot_all` / `resolve_session` / 板块清洗 | **直接 `import`** |
| `flowtool/salience.py` | 361 行 | 辨识度打分 / 事件检测 / 题材聚类 / 叙述生成 / 时间轴摘要 | 直接 `import` |
| `flowtool/render.py` | 339 行 | `fan_chart` / `salience_timeline` / `stage_sheet` / `leader_timeline` —— **那四张图的 matplotlib 版已经写好了** | 直接 `import` |
| `flowtool/universe.py` | 97 行 | 统计桶剔除 | 直接 `import` |
| `flowtool/flowdata.py` | 213 行 | 数据抓取（requests 版） | 直接 `import` |

**关键结论**：

- `server.py` **零第三方依赖**（只用标准库 `urllib` + `concurrent.futures`）
- `render.py` 里的四个绘图函数**已经用 matplotlib 实现了**——
  matplotlib 是**原生绘图库，不是网页技术**，可以直接在原生窗口里用
- 也就是说：**数据 + 算法 + 图表绘制，三样都已经有了**

重写工作主要集中在**把它们装配成一个原生 GUI 应用**。

---

## 三、技术选型：三条真正原生的路线

「原生」的判定标准：**不使用任何浏览器/WebView 内核**。以下三条都满足。

### 路线 1：Python + PySide6（Qt Widgets）★ 推荐

| 维度 | 情况 |
|---|---|
| 原生性 | ✅ Qt Widgets 是**原生控件**（Windows 上是真正的原生窗口与控件） |
| 图表 | ✅ matplotlib 的 `QtAgg` 后端直接绘制到 Qt 画布（QPainter/AGG），**非 Canvas/WebGL** |
| 依赖获取 | `pip install PySide6`（约 245 MB） |
| 打包 | PyInstaller，产物约 **60~90 MB** |
| 现有代码复用 | ✅ **最优**——所有 Python 层直接复用 |
| 界面质量 | 现代、可定制主题 |
| 本机实测 | ✅ **已跑通**（见第六节） |

### 路线 2：Python + Tkinter

| 维度 | 情况 |
|---|---|
| 原生性 | ✅ 标准库 GUI，原生控件 |
| 图表 | matplotlib 的 `TkAgg` 后端 |
| 依赖获取 | **零额外依赖**（但本机托管 Python 被裁剪掉了 tkinter，需装官方完整版 Python） |
| 打包 | 产物约 **50~70 MB** |
| 现有代码复用 | ✅ 同路线 1 |
| 界面质量 | 朴素（Windows 经典灰色控件） |

### 路线 3：C# / WPF 或 WinForms

| 维度 | 情况 |
|---|---|
| 原生性 | ✅✅ 最原生（Windows 平台原生框架） |
| 依赖获取 | 需装 .NET SDK（约 200 MB） |
| 打包 | 自包含约 **70 MB** / 框架依赖约 **2 MB** |
| 现有代码复用 | ❌ **数据层与算法层要全部用 C# 重写**（1,000+ 行抓取 + 361 行算法） |
| 界面质量 | 现代（WPF 支持矢量、动画） |

**不推荐路线 3 的唯一理由**：为了"更原生"而重写已经存在的 1,000+ 行
数据层与算法层，性价比极低，且引入"两套实现不一致"的长期风险。

### 明确排除

| 方案 | 排除理由 |
|---|---|
| Electron / NW.js | 内嵌 Chromium，正是你要去掉的东西 |
| Tauri | 仍然用系统 WebView2 渲染 HTML，**本质还是套壳** |
| 任意 WebView 方案 | 同上 |

> 注意：**matplotlib 不算"网页技术"**。它是在原生画布上用 AGG/Cairo
> 做光栅化绘图的科学计算绘图库，与浏览器渲染无关。

---

## 四、具体做法：分五步实施

### 第 0 步：环境准备（约 10 分钟）

```bash
python -m venv native_env
native_env/Scripts/pip install PySide6 matplotlib numpy
# 注意：若使用国内镜像源，PySide6 可能报 "from versions: none"，
#      换默认源即可（本机实测清华源对这几个包同步有问题）
```

### 第 1 步：抽出可复用内核（已基本完成）

把 `server.py` 作为 `core` 模块接进来（它有 `__main__` 守卫，import 安全）：

```python
CORE = os.path.join(os.path.dirname(__file__), "core")
sys.path.insert(0, CORE)
import server          # 复用 fetch_* / resolve_session
```

数据层 API（已实测可用）：

```python
server.resolve_session()      -> {交易日, 是否实时}
server.fetch_sector_list()    -> [{code, name, kind, secid}]
server.fetch_kline(secid)     -> [(HH:MM, 累计净流入亿元)]
server.fetch_index_trend()    -> [(date, HH:MM, 点位, 成交量)]
server.fetch_snapshot_all()   -> {code: (名称, 亿元, 类型)}
```

### 第 2 步：搭原生窗口骨架

```
QMainWindow
├── 原生菜单栏     QMenuBar      （文件 / 视图 / 帮助）
├── 原生工具栏     QToolBar      （刷新、状态信息）
├── 中央区         QTabWidget    （12 个面板 → 建议合并为 5~6 个标签页）
│   ├── 总览       matplotlib 画布（资金曲线 + 指数）
│   ├── 榜单       QTableWidget
│   ├── 关键节点   QTableWidget 或 QListWidget
│   └── 时间轴     matplotlib 画布
├── 原生状态栏     QStatusBar    （交易日 / 进度条）
└── 后台线程       QThread       （抓数据，避免卡界面）
```

**关键模式**：网络请求必须放 `QThread`，通过 `Signal` 回主线程更新 UI——
这是原生应用的基本纪律，也是与网页版（异步 fetch）差别最大的一点。

### 第 3 步：控件映射表（网页 → 原生）

| 网页实现 | 原生替代 | 工作量 |
|---|---|---|
| `<table>` 榜单 | `QTableWidget` | ★ 低 |
| `<div class="card">` 卡片 | `QGroupBox` / `QFrame` + `QVBoxLayout` | ★ 低 |
| `<canvas>` 折线图/时间轴 | matplotlib（`QtAgg`）嵌入 | ★★ 中——但 `render.py` 已有实现 |
| CSS 主题/响应式 | `QSS` 样式表 + `QHBoxLayout` 弹性布局 | ★★ 中 |
| `localStorage` | **SQLite**（标准库 `sqlite3`）或 JSON 文件 | ★ 低 |
| `fetch` + `async` | `urllib` + `QThread` | ★ 低 |
| 页面折叠区 `<details>` | `QToolBox` 或 `QSplitter` | ★ 低 |

### 第 4 步：数据持久化换掉 localStorage

网页版把 30 交易日归档放在 localStorage；原生版改用 **SQLite**：

```sql
CREATE TABLE day_summary(
  trade_date TEXT PRIMARY KEY,   -- YYYY-MM-DD
  payload    TEXT NOT NULL,      -- JSON
  created_at TEXT NOT NULL
);
```

好处：无 5 MB 上限、可 SQL 查询、不会因清缓存丢失。

### 第 5 步：打包

```bash
pyinstaller --onefile --noconsole --icon app.ico ^
            --name 资金流向 --add-data "core;core" native_app.py
```

**注意**：PySide6 打包需要显式排除无关 Qt 模块以控制体积
（`--exclude-module PySide6.QtWebEngineCore` 等，否则会误打进去上百 MB 的
QtWebEngine —— 那反而又把浏览器内核打回来了）。

---

## 五、工作量评估

| 模块 | 现状 | 原生版工作量 |
|---|---|---|
| 数据抓取 | ✅ Python 已有（1,013 行） | **0**（直接复用） |
| 分析算法 | ✅ Python 已有（361 行） | **0**（直接复用） |
| 图表绘制 | ✅ matplotlib 版已有（339 行） | **小**（嵌入 Qt 画布 + 微调样式） |
| 榜单/卡片表格 | 需重写 | **中**（QTableWidget + 卡片控件） |
| 时间轴/泳道图 | 需重写 | **中**（matplotlib 版可参照） |
| 布局与主题 | 需重写 | **中** |
| 事件交互 | 需重写 | **中低** |
| 持久化 | 需换 | **低** |
| 打包 | — | **低** |

**总体判断**：相比"从零写一个原生应用"，因为三层核心已存在，
实际新增代码量约 **1,500~2,000 行 Python**（对应 2,579 行 JS 的 UI 部分），
而不是重写全部 2,901 行。

**分阶段交付建议**：
1. **P0（骨架）**：主窗口 + 状态栏 + 后台抓数据 + 榜单表格 ← *已完成，见第六节*
2. **P1（核心视图）**：资金曲线 + 指数 + 辨识度榜
3. **P2（完整功能）**：关键节点 / 主角时间轴 / 阶段对比 / 题材簇
4. **P3（体验）**：主题切换、自动刷新、SQLite 归档、打包发布

---

## 六、已完成的 PoC（证明可行）

我直接在原生栈上做了一个可运行的验证原型：

```
work/native_build/
├── native_app.py      原型（约 250 行，PySide6）
├── core/server.py     复用的数据层
└── shots/             自动截图
```

**实测结果**（非估算）：

```
窗口         : 正常创建，原生菜单栏/工具栏/标签页/表格/状态栏全部渲染
数据抓取     : 成功，427 个板块、200 条快照
表格渲染     : 成功，板块名全部正确
图表绘制     : matplotlib QtAgg 后端正常
浏览器依赖   : 无（全程未加载任何 HTML/CSS/JS）
```

截图见 `work/native_build/shots/native2_*.png`——可以直接看到
Windows 原生标题栏、原生菜单、原生表格控件。

**已知待完善**（属 P1/P2 范围）：
- 净额显示为 0.00 —— 因为今天（2026-09-19）是**周六**，非交易日无当日资金流，非缺陷
- session 的日期字段名待对齐
- 资金曲线目前是占位；接 `flowtool/render.py` 的 `fan_chart` 即可出正式图

---

## 七、风险与注意事项

| 风险 | 说明 | 应对 |
|---|---|---|
| **PyInstaller 误打包 QtWebEngine** | PySide6 含 QtWebEngine，若被打进去会多出上百 MB，等于又把浏览器内核带回来了 | 打包时显式 `--exclude-module PySide6.QtWebEngine*`，并在打包后检查产物里无 `QtWebEngineProcess.exe` |
| **中文显示** | matplotlib 默认字体不含中文 | 已设 `font.sans-serif = ["Microsoft YaHei"]` + `axes.unicode_minus=False` |
| **UI 线程阻塞** | 网页版是异步 fetch，原生版若在主线程抓数据会假死 | 网络请求一律放 `QThread` + `Signal` |
| **matplotlib 性能** | 400 条曲线同时重绘会卡 | 限制绘制条数（如 Top 10）+ `blit` 局部刷新 |
| **两套实现漂移** | 若继续维护网页版，逻辑会分叉 | 建议：以 Python 为唯一后端，网页版也改成调用同一套 `core` |
| **打包体积** | 60~90 MB，比套壳版（7.8 MB）大很多 | 这是"真原生"的代价；可用 `--onedir` 换启动速度 |
| **安装包** | 若要做安装程序需 NSIS/Inno Setup | P3 阶段再考虑；先用绿色版 exe |

---

## 八、结论与建议

**可行，且代价比预期小**——因为数据层、算法层、绘图层都已经是 Python 实现。

**推荐路线：Python + PySide6 + matplotlib(QtAgg) + PyInstaller**

理由：
1. **零浏览器、零 WebView、零 HTML/CSS/JS** —— 完全满足"真原生"要求
2. **三层核心（数据/算法/图表）直接复用**，新增代码约 1,500~2,000 行
3. **本机已实测跑通**：原生窗口 + 真实数据 + 原生表格 + 原生绘图
4. 打包后是**单个自包含 exe**，不依赖系统上的任何浏览器

**需要接受的取舍**：
- 产物从 7.8 MB 涨到 60~90 MB
- 界面细节的打磨（主题、动画、响应式）需要额外投入
- 这是一次**重构**，不是一次打包；建议按 P0→P3 分阶段推进，每阶段都能独立运行

**如果只想尽快用上**：继续用现在的套壳 exe（已可用）。
**如果要"彻底原生"**：按本文方案推进，P0 骨架已经搭好并验证通过。
