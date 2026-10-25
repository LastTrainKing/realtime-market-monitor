# 排障

> 排障的第一原则：**先分清是「代码问题」还是「接口限流」**。
> 两者表现很像（都是没数据），但处理方式完全相反 ——
> 代码问题要改，限流要**停**。

## 一、30 秒分类

看日志（`logs/app_YYYYMMDD.log`）：

| 日志里出现 | 判定 | 怎么办 |
|---|---|---|
| `preflight ok · vX.Y.Z · frozen=True` 且随后有 `loaded ok` | 程序正常启动 | 往下看数据问题 |
| `LOAD FAILED` / `数据加载过程中出现异常` / `Traceback` | **代码问题** | 见第三节 |
| 大量 `RemoteDisconnected` / 请求失败 / 冷却命中 | **接口限流** | 见第二节，先停 |
| 什么都没写、窗口也不出 | 启动期崩溃 | 见第四节 |

---

## 二、没数据 / 数据不全（最常见）

### 2.1 症状：首页只有几个板块（例如「就绪 · 5 个板块」）

**这是最经典的限流后遗症**，不是「数据源没有板块」。

原因链：面板被截断 → 剩下的几乎全是二/三级行业 →
`universe.clean_universe` 的行业白名单只留 31 个一级行业 → 清洗后只剩几个。

**v3.0.6 已修**：`clean_universe` 增加了「过滤前提失效」退化
（面板够大但清洗后既绝对少又占比低 → 退化为不过滤），并加了
`HIST_MIN_POINTS = 10` 的无历史剔除。但**已经落盘的截断面板无法修复**。

处理：

1. 确认版本 ≥ v3.0.6（日志第一行有版本号）；
2. 如果是旧版本或旧面板，删掉当天的 `data/panel_YYYYMMDD.npy` 与 `.json`，
   在接口可用时重新抓一次；
3. 若当天已经过去（分钟接口只给最近一个交易日），**这一天就是没有了** ——
   不要试图用插值、填 0 或复制上一点来「补」，那会画出假曲线。

### 2.2 症状：曲线是断的（中间一段没有）

这是**如实断开**，不是 bug。触发条件：

- 连续缺失超过 `SPOT_MAX_GAP_MIN = 6` 分钟 → 保留 NaN，不插值；
- 用腾讯源时，概念板块当天没有分钟历史 → 曲线只有当前一个点。

> 为什么不插值：渲染层的 x 轴是**真实分钟序号**，11:30 在 x=119、14:34 在 x=182。
> 直接相接会让 matplotlib 画出一条横跨 63 格的直线，等于**编造 94 分钟走势**。

### 2.3 症状：状态栏显示 `源 tencent(替代)`

正常。v3.0.6 起默认数据源是腾讯（因为东财接口当时已被封）。
切回东财见 [data-source-risk.md](data-source-risk.md#四如何切换数据源)。

用腾讯源时以下功能**不可用**（源码中如实返回空并说明，不是 bug）：

- 板块分钟资金流历史 → 概念板块当天没有曲线；
- 板块成分股 → 龙头领先反应不可用；
- 日级资金流历史 → `backfill` 返回空；
- 全市场个股快照 → 开盘异动节点采不到（`capture_node` 会抛异常并写日志）；
- 交易日历 → 走本地缓存兜底。

### 2.4 症状：开盘检测显示「该节点未采集」

正常。09:36 / 09:41 / 09:46 三个节点是**定点快照**，程序当时没运行就没有。
v3.0.2 起有「补采」，但补采拿到的是**当下累计值**，会如实标注「· HH:MM补采」，
不会拿收盘值冒充节点值。

> v3.0.1 之前这个提示会被截断成「该…」（列宽问题），已在 v3.0.1 修复。

---

## 三、代码层已知坑（改代码前必读）

### 3.1 `core2/tradecal.py` 覆盖 `core/tradecal.py`

打包时 `core2` 在 `sys.path` 中优先级更高，**实际加载的是 `core2` 那份**。
改 `core/tradecal.py` 而不改 `core2/`，改动不会生效。

### 3.2 四份实现 / 六个副本必须同步

同一套分析逻辑存在多份实现（`core/salience.py`、打包产物内副本、
命令行工具副本、JS 重写版）。**改一处必须同步全部**。

> 教训：修「某个函数」要按**语义**找，不能按函数名找 ——
> 换语言的副本搜函数名永远找不到。

### 3.3 改排序 = 改截断

任何按序输出的函数，改排序键之前先搜所有 `[:N]` / `.slice(0, N)`。

真实事故：`timeline_summary(F)[:24]` 原本依赖强度序才有「取最强 24 条」的语义。
改成时间序后，**同一行代码静默变成「取最早的 24 条」** ——
尾盘整段消失、行数还是 24、不报错、不缺行。

### 3.4 GBK 编码

frozen 版 stdout 是 GBK。日志文案里出现非 GBK 字符（`⚠` `★` `✅` `⚡`）
会抛 `UnicodeEncodeError`，**一句日志文案能把整个数据加载搞崩**，
界面变成 `LOAD FAILED: 'gbk' codec can't encode character '\u26a0'`。

已在 `app_main_v203.py` 顶部装 `reconfigure(errors="replace")` 兜底，
并有 `test_universe_v306.py` 用子进程 + GBK stdout 做回归守卫。
**新增文案时仍然不要用非 GBK 字符**。

### 3.5 单点列会污染整个矩阵

`salience.build_features` 的行中性化 `dV5 - dV5.mean(axis=1, keepdims=True)`
只要某时刻**任意一列**是 NaN，该行均值即 NaN → 顺着这一行污染全部板块。
面板里混入「只有 1 个点」的列时，界面会「大部分数据不显示」。
v3.0.6 已加 `HIST_MIN_POINTS` 过滤，但如果你自己造面板，仍要注意。

### 3.6 `_internal/` 与 exe 必须成对更新

只换 exe → 新程序配旧依赖 → 各种 `ModuleNotFoundError` 或诡异行为。
部署脚本已加「exe 字节数 + `_internal` 文件数」对账，不一致即判 FAILED。

### 3.7 `.pyc` 静默加载旧逻辑

`app_main_v203.py` 把 `core/` 插入 `sys.path`。
`core/__pycache__/flowdata.cpython-313.pyc` 与源码同版本、mtime 匹配时
**是会被真正加载的** —— 一旦出现「改了源码但 `.pyc` 更旧」的组合，
就会静默跑旧逻辑。**打包前必须清 `core/__pycache__` 与 `core2/__pycache__`。**

---

## 四、启动问题

| 症状 | 排查 |
|---|---|
| 双击没反应、窗口不出 | 用命令行运行 `python app_main_v203.py` 看 stderr；frozen 版看 `logs/` 最新文件 |
| 连点弹多个「已在运行中」 | v2.0.3 起单实例锁已提前到 preflight 之前；旧版会有此问题 |
| 首屏很久（>30 s） | 看日志是「旧面板先行版」还是「全量补抓」。全量补抓 1000 板块 = 1000 次请求，受限流影响可能 180 s（有全局时限） |
| 界面全白 / 全空但日志正常 | 检查是否面板被截断（见 2.1）；v3.0.6 之前版本无「不降级守卫」 |
| 指挥中心提示「尚未连接到本地服务」 | 安全软件把 `capital_flow_backend.exe` 拦了，加白名单 |
| 指挥中心黑屏 | 需要 WebView2 运行时（Win10/11 一般自带） |

---

## 五、跑测试

```bash
cd flowlive-native/build
.venv/Scripts/python -m pytest test_*.py -q      # 若已装 pytest
# 或直接跑单个脚本（多数脚本自带 main）
.venv/Scripts/python verify_v210_structure.py
.venv/Scripts/python verify_v301_panels.py
```

**离线可跑的**：`test_latency_v306.py`(74)、`test_livegap_v306.py`(53)、
`test_source_v306.py`(69)、`test_universe_v306.py`(25)、
`test_flatline_guard.py`(18)、`test_render_incremental.py`(11)、
`test_index_incremental.py`(9)、`test_bar_guard.py`(16)、
`test_spot_parallel.py`(9)、`verify_v210_structure.py`(14)、
`verify_v301_panels.py`(36)。

**需要环境的**：`test_exe_native.py`、`test_shortcut*.py`（需要已部署的 exe
与桌面快捷方式）；`chk115.py` 是 **HTML 版**守卫（检查截图数量、`t_hover`），
其失败项与网络层改动无关。

> ⚠️ 本次归档**未执行**上述任何测试（环境无 PySide6）。上面的项数来自
> 项目自带的开发日志，属于作者的当时记录。

---

## 六、提 Issue 时请附上

1. 版本号（日志第一行的 `preflight ok · vX.Y.Z`）；
2. 出问题的时间点（精确到分钟）；
3. 当时的数据源（状态栏 `源 xxx` 或日志里的 `source ·` 行）；
4. 相关日志片段（**请先删掉本机路径与自选股内容**）；
5. 是「完全没数据」还是「只有部分板块/部分分钟」。

**不要**附：完整日志原文（含路径）、数据库文件、任何凭据。
