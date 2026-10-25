# -*- coding: utf-8 -*-
"""资金流向 · 纯原生桌面版

★ 本程序不含任何浏览器内核 / WebView / HTML / CSS / JS。
  窗口、菜单、工具栏、表格全部是 Qt 原生控件；
  图表是 matplotlib 在 Qt 画布上原生绘制（AGG 光栅化），不是网页 canvas。

数据层与算法层复用既有 Python 实现（core/ 目录）：
  server.py    实时抓取（零第三方依赖）
  flowdata.py  全量分钟数据抓取 + 本地缓存
  salience.py  辨识度打分 / 事件检测 / 题材聚类 / 时间轴
  universe.py  统计桶剔除
"""
import os, sys, time, json, traceback, threading
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(BASE, "core")
# ★★ v2.0.2：core2 优先于 core —— 存放被文件锁挡住的模块修正版。
#   （core/tradecal.py 被 WorkBuddy 快照组件锁定，无法写入/改名，
#    core2/ 放同名修正副本；后 insert 的排 sys.path 最前，优先生效。
#    锁释放后应把 core2 内容并回 core 并删除本段与 core2 目录。）
CORE2 = os.path.join(BASE, "core2")
if os.path.isdir(CORE):
    sys.path.insert(0, CORE)
if os.path.isdir(CORE2):
    sys.path.insert(0, CORE2)

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize
from PySide6.QtGui import (QAction, QIcon, QFont, QColor, QPalette, QPainter,
                           QShortcut, QKeySequence)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QLabel, QStatusBar,
    QToolBar, QSplitter, QProgressBar, QMessageBox, QFrame, QSizePolicy,
    QAbstractItemView, QTextBrowser, QComboBox, QDialog, QPushButton,
    QScrollArea, QInputDialog,
)

# ★★ v3.0.6：**先给 stdout/stderr 装上"永不因编码崩"的保险**，再做别的。
#
#   【实测事故（2026-09-23 18:38，部署版 exe）】
#     frozen 版的 stdout 编码是 **GBK**（中文 Windows 的默认代码页），
#     而代码里有一句 `print("  [universe] ⚠ 一级行业里只有 …")` ——
#     `⚠` 是 U+26A0，**不在 GBK 里** → `UnicodeEncodeError` 直接冒到
#     Worker 的错误处理，界面上就是：
#         LOAD FAILED: UnicodeEncodeError: 'gbk' codec can't encode …
#         LOAD FAILED reason: 数据加载过程中出现异常
#     也就是说：**一句日志文案把整个数据加载搞崩了。**
#
#   【为什么必须从根上兜，而不是逐个改文案】
#     · 这类字符不止 ⚠ 一个（★ ⚡ ✅ 等都可能踩）；
#     · 它在开发机上**完全复现不出来**（那里的 stdout 是 UTF-8），
#       只在 frozen + 中文 Windows 上炸 —— 靠人眼审查根本防不住；
#     · 失败的代价极高（整个面板加载失败），而修法只有一行。
#   所以：把两个流的编码错误处理改成 `replace`（编不出的字符变 `?`），
#   从此任何文案都不可能再让程序崩掉。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")        # Python 3.7+
    except Exception:                                # noqa: BLE001
        pass

# ★★ v2.0.3：matplotlib **延迟加载**（启动延迟优化，实测依据见 ensure_mpl）。
#   旧版这里直接 `import matplotlib`（实测 ~1.0s），模块 import 阶段就付掉，
#   窗口出现要 ~1.9s。现在只在模块里放占位名，真正 import 推迟到
#   ensure_mpl() 首次调用（窗口显示后 + Worker 线程启动时，两者幂等互斥）。
#   Bbox/Figure/FigureCanvasQTAgg/matplotlib 四个名字由 ensure_mpl 注入模块
#   全局 —— 所有引用点都在**运行期**才解析它们（类工厂、悬停取景框等）。
matplotlib = None          # type: ignore
Figure = None              # type: ignore
FigureCanvasQTAgg = None   # type: ignore
Bbox = None                # type: ignore

_MPL_LOCK = threading.Lock()
_MPL_DONE = False


def ensure_mpl():
    """加载 matplotlib 并定义全部图表类（v2.0.3 启动延迟优化核心）。

    ★ 实测（2026-09-22，native_env，暖缓存）：
        PySide6.QtWidgets import ≈ 160ms
        matplotlib import        ≈ 1028ms   ← 启动大头
        numpy import             ≈ 0ms（matplotlib 已连带）
        6 张画布创建              ≈ 3ms
      结论：「双击 → 窗口出现」的 ~1.9s 里，一半以上是 matplotlib 的
      import。把它从模块 import 挪到窗口显示之后，窗口即刻可见；
      图表画布在 _materialize_charts() 里补建（数据本来就要 1~5s 才到，
      用户无感）。

    ★ 线程模型：Worker.run() 启动时也会先调一次 ensure_mpl()（在**后台
      线程**里做 import），与 UI 线程的窗口构建并行 —— 谁先到谁执行，
      另一方在锁上等一小会儿即可；双重检查 + _MPL_LOCK 保证只做一次。

    ★ 幂等：可任意次调用；已完成时 O(1) 返回。
    """
    global _MPL_DONE, matplotlib, Figure, FigureCanvasQTAgg, Bbox
    if _MPL_DONE:
        return
    with _MPL_LOCK:
        if _MPL_DONE:                        # 双重检查：等锁期间别人做完了
            return
        import matplotlib as _mpl
        _mpl.use("QtAgg")                    # 原生 Qt 画布
        from matplotlib.backends.backend_qtagg import (
            FigureCanvasQTAgg as _FC)
        from matplotlib.figure import Figure as _Fig
        from matplotlib.transforms import Bbox as _Bbox
        _mpl.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
        _mpl.rcParams["axes.unicode_minus"] = False
        matplotlib, Figure, FigureCanvasQTAgg, Bbox = _mpl, _Fig, _FC, _Bbox
        _MC, _Fan, _Idx, _Thm, _Ld, _Stg, _Flw = _define_chart_classes()
        g = globals()
        g["MplCanvas"], g["FanChart"], g["IndexChart"] = _MC, _Fan, _Idx
        g["ThemeBoard"], g["LeaderGrid"] = _Thm, _Ld
        g["StageGrid"], g["FlowBarChart"] = _Stg, _Flw
        _MPL_DONE = True

VERSION = "3.0.6"

# ★★ v3.0.5（2026-09-23 盘中）：修复「资金曲线尾部一条平直横线」。
#
#   【用户报告】「又又又有bug …… 为什么有一段数据，没采集呢，这一段直接是横线了」
#     截图：资金曲线画到约 **10:20** 后，从那里一直到 **15:00** 是一条平直横线。
#     状态栏证据："分钟点 50 · 分时到 10:20 · 数据日 2026-09-23（实盘）"
#     —— 说明**时间轴只有 50 个点**（09:31~10:20），曲线却画满了整个横轴。
#
#   ── 根因（两层，缺一不成）──────────────────────────────────
#     ① **面板两文件读写均非原子**：
#          panel_{tag}.json  →  meta["times"]（时间标签）
#          panel_{tag}.npy   →  mat（时间×板块 矩阵，行序对应 times）
#        写：`build_panel` / `refresh_panel_spot` 都是**先 .npy 后 .json**；
#        读：`load_panel` 是**先 .json 后 .npy**。
#        盘中 UI 刷新线程与另一轮刷新并发时，就可能读到
#        「**新矩阵 + 旧标签**」的跨版本组合。本机实测：
#          panel_20260922.npy = (240, 1000)，panel_20260923.npy 盘中 = (50, 995)
#        —— 列口径一致，混起来**完全不报错**，是典型的静默错配。
#
#     ② **`_x_positions` 会为"没有时间标签的点"续出假序号**：
#        旧代码 `raw = raw + [None] * (n - len(raw))`，这些 None 在下方循环里
#        被"上一个 + 1"补成序号。于是 times=50 / n=240 时，尾部一路续到
#        **239 = 15:00**，曲线被拉满整个横轴。
#        再叠加 `_fill_gaps` 的端点外推（重复末值）→ 尾部成了一条**平直横线**。
#
#   ── 修法（三层防御，任一层都能单独拦住横线）────────────────
#     A. `flowdata.load_panel`：**对齐守卫** —— 读到 `mat.shape[0] != len(times)`
#        时，按 times 截断矩阵并记日志。时间标签是横轴的唯一依据，
#        没有标签的数据点画出来一定落到错误时刻上，比不画更糟。
#     B. `app_main._x_positions`：**不再续假序号** —— xpos 长度改为
#        `min(len(times), n)`，即"有标签才有点"。契约变更写进 docstring。
#     C. `FanChart.plot` / `try_update_series`：取数统一为
#        `k = min(len(s), len(xpos))` 后 `s/xs` 同时截断；删除旧的
#        `range(len(s))` 回退分支（那正是把多余行按数组下标落到 15:00 的写法）。
#     D. `_market_stats`：源头把 `series` 裁到 `len(F.times)`（防御纵深）。
#
#   ── 验证 ───────────────────────────────────────────────
#     · 新增 `test_flatline_guard.py`：**18 PASS / 0 FAIL**，六组断言：
#         A `_x_positions` 契约（长度=min(len(times),n)、严格递增、不越界）
#         B ★核心：times=50/n=240 → 末位 **49（10:20）**，不是 239（15:00）
#         C FanChart **真实渲染** 240 点数据 + 50 标签 → 每条线 x ≤ 49
#         D 一致时行为不变（times=n → 末位 n-1）
#         E `load_panel` 对齐守卫（240 行 → 截断到 50 行；一致时不动）
#         F 不可解析标签 → 退回下标（旧行为不破坏）
#     · 回归全绿：snap_catchup 36 / bar_guard 16 / render_incremental 11 /
#       index_incremental 9 / catchup_ui 11 / spot_parallel 9 /
#       flatline_guard 18 —— 合计 **110 PASS / 0 FAIL**。
#     · 真实数据复现对比（`diag_mismatch.py`）：
#         修复前：xpos[-3:] = [237, 238, 239] → 曲线拖到 15:00（截图原样）
#         修复后：xpos[:50]，末位 49 → 曲线止于 10:20，右侧留白（符合设计）
#
#   ── 诚实边界 ──────────────────────────────────────────
#     · 根因①的**写侧**未加文件锁/临时文件原子替换（改动面大、盘中风险高）。
#       当前用"读侧对齐守卫 + 渲染层以 times 为准"把症状彻底消除；
#       写侧原子化列为后续项（见 v3.0.6 待办）。
#     · "右侧留白"是**设计行为**而非缺陷：横轴固定 9:30–15:00 全程 240 格，
#       盘中数据只到当前分钟就画到当前分钟 —— 留白表示"尚未走到"。
#       见 `FanChart.plot` 内该处注释。

# ★★ v3.0.4（2026-09-23 盘中）：时间处理算法重构 + 两处数据缺失根治。
#
#   【用户报告的两个现场问题】
#     ① 「开盘检测、个股异动等板块仍提示该节点未采集」；
#     ② 「部分板块无法读取数据」。
#   两个都实测复现、定位到根因、修复并验证。
#
#   ── 问题① 根因（★ 有两层，第二层是"真·算法缺陷"）────────────────
#
#   【第一层｜判定被误杀】`_snap_tick` 用 `if days and tag not in days: return`
#   判"是否交易日"。但实测确认：**`fflow/daykline` 只返回"已完成交易日的日线"**，
#   今天的日线要收盘后才生成 —— 所以**盘中取回的日历永远不含今天**
#   （实测 09-23 盘中联网取回，日历最后一天仍是 09-22）。
#   → 每个交易日盘中都命中这条守卫、**直接 return**：
#       · 准点采集（due_nodes）不执行；
#       · v3.0.2 精心写的补采（catchup_nodes）更是**一次都跑不到**。
#   ★ 同一个程序里 `_auto_tick` 却用 `weekday>=5` 判周末、**完全不排节假日**，
#     国庆（10-01 是周四）会全天每 3 秒打接口。两个线程对"今天开不开市"
#     给出相反答案 —— 这是时段判断散落成 4 份的必然结果。
#
#   【第二层｜补采状态机写错 —— 这才是"仍提示"的直接原因】
#   用户第二次反馈：「该节点未采集的bug**还是**没有修复」，注意那个"**还是**"
#   以及最初的措辞是「开盘检测、个股异动**等板块仍**提示」——
#   症状不是"一个都没采到"，而是**部分节点长期残留**。
#   审计 `_snap_tick` / `_on_snap_done` 后确证是一个**真·算法缺陷**：
#     · `catchup_nodes` 会**正确返回全部**待补节点（实测三节点全缺时返回 3 条）；
#     · 但 `_snap_tick` 每轮只取 `ck[0]` 补**一个**；
#     · 且 `_on_snap_done` 里 `if _late: self._catchup_done = True`
#       在**第一次补采成功后就置位** ——
#     ⇒ 第 1 轮补 m5 → 成功 → 置位 → 之后所有 tick 跳过补采 →
#       **m10 / m15 永远补不到**，界面长期残留两块「该节点未采集」。
#   ★ 复现证据（纯逻辑复刻旧状态机，6 轮 tick）：
#       旧：captured=['m5']，build_payload 仍缺失 ['m10','m15'] → 界面 2 块未采集
#       新：captured=['m5','m10','m15']，missing=[] → 界面 0 块
#   ★ 修法：把"补采是否结束"的判据从"**曾经成功过一次**"改成
#     "**当天已无待补节点**"。仍一次只补一个（避免 3×70 请求瞬时齐发触发限流），
#     但每轮 tick 接着补下一个 → 三节点 45 秒内补齐，且任意时刻不超过 1 个采集线程。
#   ★ 为什么此前"源码级验证通过"却没效果：见下方【部署链路的双重失效】。
#
#   ── 问题② 根因 ────────────────────────────────────────────
#   实测 1000 个板块 8 并发抓取：首轮成功 919/996（**失败 7.6%**），
#   但把"失败"的那些隔几分钟单独重抓 → **100% 成功**。
#   即失败是**瞬时限流**，不是永久不可达。
#   而原代码 `if rows: results[secid] = …` 对失败**不做任何补救**，
#   只在末尾报一个"919/996"的总数 —— 那 7.6% 在面板里就是永久缺列。
#   同类问题也出现在 `capture_node`：限流时 `_fetch_list` 返回**空列表**，
#   而 `build_payload` 只判"节点是否存在"→ 界面拿到"存在但为空"的节点，
#   渲染出一片**空白**（比"未采集"提示更让人困惑）。
#
#   ── 【部署链路的双重失效】（★ 解释"为什么修了却还是没修复"）──────
#   第二次反馈的完整答案是**两个独立的失败叠加**，缺一不可：
#     (a) **判定/状态机缺陷**（上文第一层 + 第二层）—— 代码本身是错的；
#     (b) **修复从未到达用户运行的程序** —— 用户实测的 exe 是 **v3.0.2**，
#         打包于 01:18；`_internal/core/` 下**没有 tradingtime.py**，
#         `opensnap.py` 还是 `workers=6` 的旧版。我改的是 build 源码树。
#   更隐蔽的是 (b) 里还有第二重错位：
#     · 桌面快捷方式 `资金流向.lnk` 实际指向 `%USERPROFILE%\Desktop\exe\`；
#     · 而 `deploy.py` 一直在往 `%LOCALAPPDATA%\FlowLiveNative\` 写
#       —— 那个目录里的 exe 停在 09-21，**根本不是用户双击的那个**。
#   ⇒ 结论：**必须把部署目标改成用户真正运行的位置**，否则"修十遍"都无效。
#     本次已把 v3.0.4 直接部署到 `Desktop\exe\最新版\`。
#

#   ── 修复 ─────────────────────────────────────────────────
#   一、新增 `core/tradingtime.py` —— 时间处理的**唯一权威**。
#       · 两级时段模型：
#           一级（用户可见 4 段）：盘前 / 集合竞价 / 盘中 / 盘后
#           二级（内部 8 子相）：pre_dawn / auction_open / pre_open /
#               morning / noon / afternoon / close_auction / post
#         ★ 旧代码**完全没有集合竞价**（09:15~09:30 一律当"盘前"）。
#       · 交易日判定四层证据链：周末 → 运行期观测自校正 → 日历命中/区间内
#         → 日历区间外但新鲜（`calendar_live`，盘中常态）→ 工作日推断兜底。
#       · `note_data_date()` 自校正：抓到"实际数据日期"与今天不符即确证休市
#         （长假第一天靠它兜住，代价仅"多抓一次"）。
#       · 时段边界 + 各时段刷新上限（盘中 10 秒，午休/盘前不刷新）。
#   二、`_snap_tick` / `_auto_tick` 全部改走 tradingtime：
#       · 节点采集不再被误杀（`calendar_live` 正确识别盘中）；
#       · 自动刷新首次获得**法定节假日过滤**。
#   二·补、**修复补采状态机**（第二层根因，见上）：
#       · `_snap_tick`：`ck[0]` 仍一次只补一个，但"停止补采"的判据改为
#         **`当天的三个节点全部已在快照里`**，而不是"曾经补成功过一次"；
#       · `_on_snap_done`：**删掉 `if _late: self._catchup_done = True`** ——
#         它把"补了一次"误当成"补完了"，是 m10/m15 永久残留的直接元凶；
#       · 效果：三节点全缺时逐轮补 m5→m10→m15（45 秒内），界面 0 块未采集。
#   三、采集失败重试与完整性校验：
#       · `build_panel` 失败板块**串行补抓**（实测挽回 13/15，284→297/300）；
#       · `_fetch_list` 首页重试(6次)+失败页补抓，并发 6→3、3 列表并发 3→2
#         （峰值 18→6，实测：18 并发时 70 请求齐发必触发限流）；
#       · `capture_node` **完整性校验**：sectors<600 或 stocks<3000 即抛异常
#         **不落盘** —— 宁可明确失败并重试，也不落盘残缺数据
#         （"部分残缺"比"全空"更危险，界面会展示一个错误的小样本排行）。
#       · `build_payload` 把"存在但为空"的节点如实标为 missing(empty)。
#   四、`flowdata.log_failed_sectors()`：失败板块留痕到
#       `failed_sectors.jsonl`（排查"是否总是同一批"→ 决定是板块问题还是限流）。
#   五、**部署目标纠正**：直接部署到用户实际运行的位置
#       （`Desktop\exe\最新版\`，由桌面快捷方式 `资金流向.lnk` 指向），
#       不再只写 `%LOCALAPPDATA%\FlowLiveNative\`。
#
#   ── 验证（全部在盘中真实环境完成）──────────────────────────
#     · 三节点采集：m15 准点(on_time=True) / m5 / m10 补采，
#       每个 sectors=1000 · stocks=5919（修复前是 0 / 0）。
#     · `build_panel` limit=300：284→297（补抓挽回 13/15）。
#     · `test_snap_catchup` 27→**31→36 PASS**（新增 4 条空节点保护断言 +
#       5 条补采状态机回归断言）。
#     · ★★ 补采状态机缺陷**显式复现**（纯逻辑复刻，6 轮 tick）：
#         旧状态机：captured=['m5']，payload 仍缺失 ['m10','m15'] → 界面 2 块未采集
#         新状态机：captured=['m5','m10','m15']，missing=[] → 界面 0 块
#       —— 旧行为与用户现场症状（"**等板块仍**提示"）逐字吻合。
#     · 部署链路核对：`资金流向.lnk` → `%USERPROFILE%\Desktop\exe\`；
#       该处 `版本.txt` 曾为 3.0.2、`_internal/core/` 无 tradingtime.py
#       → 确证"修复从未到达用户程序"。
#     · 边界逐分钟扫描：09:14→盘前 / 09:15→集合竞价 / 09:25→静默 /
#       09:30→盘中，全部正确。
#
#   ── 诚实边界 ─────────────────────────────────────────────
#     · `keep-alive 通道探测失败，回退 urllib` 在限流窗口内仍会出现；
#       实测东财**直连与走代理都是 200**（夜间曾直连 000），
#       说明该日志是"限流副作用"而非环境问题 —— **未据此改主链路**。
#     · 长假第一天的"日历区间外"会被 `calendar_live` 放行一次，
#       依赖 `note_data_date` 自校正 —— 已在 tradingtime 文档中写明。
#     · 补采（on_time=False）拿到的是**该时刻累计值**，不是节点原值 ——
#       东财不提供历史 clist 快照，这是物理限制，代码无法绕过；
#       程序用"卡片标题后缀 + 榜单标注"把这个区别摆在明面上。

# ★★ v3.0.3（2026-09-23）：性能重构 —— 采集/处理/渲染三路优化 + 1~10 秒自刷新。
#   用户授权：「可自由修改算法、数据结构和代码实现」，目标是"尽可能降低各类延迟
#   （开屏/算法/采集/渲染）"，并把盘中自刷新调到 **1~10 秒、越频繁越好**。
#
#   【先测后改】全部改动都基于本机实测基线（bench_v304_baseline.py /
#   bench_v304_load.py / bench_v304_charts.py），不靠感觉调参：
#       采集(1000板块)  302.2 ms   ← 最大瓶颈
#       build_features    9.3 ms   ← 已足够快
#       FanChart 整幅    92~112 ms ← 高频刷新的主要成本
#       IndexChart 整幅   64.0 ms
#       ThemeBoard 整幅   64.8 ms
#       FlowBarChart      18.8 ms
#       LeaderGrid/Stage  10.1 ms
#       六图全量合计     280.1 ms
#       表格 40×7         0.6 ms   ← 可忽略
#
#   一、采集：fetch_spot_batch **分片并行**
#       改动前 `for` 串行，5 个分片排队 = 5×RTT（打桩 60ms → 302ms）；
#       改动后 ThreadPoolExecutor 并发 → **62.5ms（4.85×）**。
#       安全性依据：_TLS 是 threading.local（每线程独立连接），
#       且结果写入各自 secid 的独立键，无共享可变状态。
#       ★ 过程中抓到并修掉一个**静默数据丢失**缺陷：并发度判定误写成
#         `if w == 1:`（只发第一个分片 → 1000 个板块只回 200 个，且不报错），
#         已改为 `if len(chunks) == 1:` 并在注释里钉死。
#
#   二、渲染：FanChart / IndexChart 新增 **增量更新**（try_update_series）
#       高频刷新时曲线集合与名称不变、只是末端加点，用 set_data 原地更新，
#       绕开 `fig.clear()` 全量重建：
#         FanChart   92.03 ms → 1.18 ms  （78.3×）
#         IndexChart 65.82 ms → 1.44 ms  （45.6×）
#       全程**保守回退**：曲线数量/名称/方向指纹不符、首次绘制、空数据、
#       单点数据、任何异常 → 一律返回 False 并退回完整重建（行为与旧版一致）。
#       ★ ThemeBoard / FlowBarChart / LeaderGrid / StageGrid **故意不做**增量：
#         它们的成本在 patch 集合与归一化版式，做增量风险高于收益；
#         真正的解法是它们的更新频率天然低（题材/阶段变化不像资金曲线连续）。
#
#   三、自刷新：档位改 1/2/3/5/10/30/60 秒，**默认 3 秒**（原 30 秒）
#       + REFRESH_MIN_GAP_SEC 3.0 → 0.8（否则 1 秒档会被阈值静默否决）
#       + 新增 **跨分钟守卫**（这是支撑 1~10 秒的关键）：
#         数据源是分钟粒度，同一分钟内重复请求拿回的是同一个值。
#         记住上次真实抓取的交易分钟（用 flowdata.clamp_to_bar 取刻度，
#         与面板写入口径一致），同分钟直接跳过。
#         效果：**无论定时器设 1 秒还是 10 秒，每分钟真实抓取恒为 1 次**；
#         且跨分钟后立即抓、不等下一拍 —— 所以"调快档位"确实更快，
#         同时请求量与 30 秒档完全一样（不会招致限流）。
#
#   四、开屏：未改动。现有实现已是最优（matplotlib 延迟 import + 后台预热 +
#       画布延迟建立 + 首屏只画可见页）。实测 build_features 仅 9.3ms，
#       开屏时间由 matplotlib import（~1s，已并行化）主导，无可压空间。
#
#   五、测试（全部新增，全绿）：
#       test_spot_parallel.py       9 项  并行正确性 + 提速 + 边界
#       test_render_incremental.py 11 项  增量正确性 + 6 项回退守卫 + 提速
#       test_index_incremental.py   9 项  增量正确性 + 4 项回退守卫 + 提速
#       test_bar_guard.py          16 项  跨分钟守卫全场景
#       test_ka_probe_timeout.py   14 项  探测期短超时（v3.0.3 补丁，见第六节）
#       既有回归：test_snap_catchup 27 + test_catchup_ui 11 + verify_v301_panels 36
#       → 合计 136 项全绿
#
#   六、补丁（2026-09-23 01:50~02:00，同版本内追加，不升版本号）：
#       核心 flowdata.py 新增 **探测期短超时**（KA_PROBE_TIMEOUT=3.0）。
#       起因：夜间东财接口关闭期间实测，进程启动要连做 6 次 keep-alive 探测，
#             每次最长等 15s（内部还会重试 1 次 → 单次最坏 2×15s）。
#       机理（读 code 确认）：_KA_STATE 未 proven 时，探测失败 6 次会把整条
#             长连接通道关掉（_ka_off），于是走 urllib 兼容通道。
#       改法：**仅在通道未证实可用时**把超时压到 3s；一旦某次成功
#             （_ka_note(True) → proven），立刻恢复调用方给的完整超时。
#             即：**正常盘中的超时语义一字未改**，改动只作用于"还没证明好用"
#             的那几次尝试。最坏总代价 180s → 36s（5×）。
#       ★ 诚实边界：实测东财在**接口关闭**时是"0.32s 快速拒绝"，不是沉默超时，
#         所以这个改动对"东财接口不可用"场景的实际收益很小（约 2~3 秒）；
#         它真正兜住的是"网络黑洞"（连接既不成功也不拒绝）——那种情况才有 5× 收益。
#       ★ 同时**推翻了一个错误假设**：曾以为"本机挂了代理 → keep-alive 直连
#         永远不可用"。实测证伪 —— _keepalive_once 直连 baidu 首次 407ms、
#         复用 58ms，**通道本身是好的**；那条 `[net] keep-alive 通道探测失败`
#         日志的真实成因是**东财当时在拒绝连接**。故**没有**为此改主链路。
#
#   七、打包与部署基建（本轮新增，不改运行逻辑）：
#       资金流向原生版_v303.spec  v3.0.3 打包配置（与 v302 参数一致）
#       build_v303.py             一键构建 + **体积对账**（防打包环境混入 pandas）+ 版本标记
#       deploy.py（重写）         源目录**自动探测**（不再硬编码 dist_v125_onedir）
#                                 + 部署后完整性对账（exe 字节数 / _internal 文件数）
#       ★ 旧 deploy.py 硬编码 `SRC_DIR = dist_v125_onedir`，每次改版本要手工改，
#         忘改就**把旧构建当新版本部署**（与"版本号只在标题栏"同类假成功陷阱）。
#       ★ 构建产物：exe 8.0MB + _internal 142.5MB / 451 文件（无 pandas 混入）。


# ★★ v3.0.2（2026-09-23）：开盘节点**盘中/盘后补采** + 开机自启（用户需求 A+B）。
#   一、问题（用户原话）：「东方财富接口在收盘后仍可获取当日静态数据，
#       因此即便已过开盘后十五分钟，理论上也应能获取此前的静态数据……
#       能不能把这种『该节点未采集』bug 给修了，这一看就不合理」。
#   二、技术事实（必须先说清，否则会做出错误承诺）：
#       clist 返回的是**当日累计值**，收盘后确实仍能取到 —— 用户判断对的部分；
#       但取到的是**当天最终的累计值**，**不是 09:36 那一刻的值**。
#       接口不提供历史 clist 快照，所以补采**无法复原节点时刻的真实数据**。
#   三、因此采用「A 打底 + B 兜底」（用户选定）：
#       · A = 开机自启（tools/make_autostart.ps1）→ 保证节点时刻程序在运行，
#             采到的是**真节点数据**（on_time=True，界面无任何标注）；
#       · B = 盘中/盘后补采（opensnap.catchup_nodes + _snap_tick）→
#             错过节点时补一轮，但**如实标注**真实采集时刻
#             （on_time=False，标题显示「… · 10:05补采」）。
#   四、绝不做的事：让补采的累计值冒充节点值。宁可标题上带"补采"二字，
#       也不给用户一个看起来是 09:36、实际是 10:05 的数字。
#   五、新增 opensnap.catchup_nodes / _is_on_time / CATCHUP_FROM / CATCHUP_UNTIL；
#       capture_node 新增 trigger 参数与 on_time/planned 字段（旧快照兼容）；
#       build_payload 新增 on_time/late/cap_hm（旧快照按 captured_at 现算）；
#       新增 _set_card_title(panel_id, text) 按注册表 id 精确改标题；
#       单元测试 test_snap_catchup.py（27 项全绿）。

# ★★★ 版本号命名与递增规则（v3.0.1 起生效，务必遵守）★★★
#   格式：MAJOR.MINOR.PATCH（三段十进制）。
#   递增规则：**逐段 +1，满 9 进 1**（不是常见的"满 10 进 1"）。
#     · PATCH 段取值 0~9；3.0.9 的下一个版本是 **3.1.0**（不是 3.0.10，也不是 3.1.0 之外的写法）；
#     · MINOR 段同理取值 0~9；3.9.0 的下一个版本是 4.0.0；
#     · 任何一段都不出现两位以上的数字 —— 每段都是**单个十进制位**。
#   为什么用"9 进 1"而不是"10 进 1"：用户明确要求，且本项目的版本号会被
#     拼进 exe 文件名与「关于」对话框，单数字段更短、更易口头传达，
#     也不会出现 3.0.10 与 3.0.1 视觉上易混的情况。
#   何时进哪一段（本项目约定）：
#     · PATCH：修 bug、调样式、改文案、压留白等**不改结构**的改动；
#     · MINOR：新增/重排页面（如 v2.1.0 的两级页签重构）、新增面板；
#     · MAJOR：数据源或整体架构换代（如 v1→v2 的 UI 框架更换）。
#   ⚠ 修改 VERSION 时必须同步三处，缺一即版本号对外不一致：
#       ① 本文件 VERSION 常量；
#       ② 构建目录的 `dist_vXXX/版本.txt`（由构建脚本写入）；
#       ③ `更新日志.md` 顶部新增一条对应版本的记录。
#
# ★★ v3.0.1（2026-09-23）：面板（孙模块）注册表 + 四字命名合规 + 提示行修复。
#   一、**新增第三层面板注册表 `PANELS`**（40 个面板，见下方定义）。
#       背景：此前只有母模块（TAB_GROUPS）与子模块两级有"唯一权威清单"，
#       最底层的面板（卡片）没有注册表、名称或编号 —— 漏挂、错挂、重复挂
#       都无法被自动发现。用户要求"为第三个面板层补充并完善注册方式与
#       存储位置、命名规则与编号方案，确保结构一致且可扩展"，据此实现：
#         · 存储位置 = 本文件 `PANELS` 列表（唯一来源，紧跟 TAB_GROUPS）；
#         · 编号方案 = `面板id`，格式 `子模块key.面板key`，全局唯一、稳定不变；
#         · 命名规则 = 显示名与 id 分离（id 英文点分、显示名中文可改）；
#         · 注册方式 = 建卡时传 `panel_id=...`，写入 QFrame 的 panelId 动态属性；
#         · 对应关系 = 每条 PANELS 记录同时声明所属母模块与子模块，
#                      并自动派生 `PANELS_BY_SUB` 供逐页核对；
#         · 守恒断言 = 启动自检 + verify_v301_panels.py（36 项）双重校验
#                      "运行期实际挂载集合 == PANELS 登记集合"。
#   二、**子模块名「题材簇」→「题材聚簇」**：全量审计后唯一不符"四字"
#       规则的子模块（3 字）。面板层保留描述性长名（用户已确认只约束
#       子模块层，见当次对话）。
#   三、**修复「该节点未采集」提示被截断为「该…」**：原 `_miss_row` 把整句
#       说明塞进第 1 列（ResizeToContents，被挤窄）→ 用户既看不懂为空原因，
#       表格下方还留大片空白。改为整行合并居中显示完整文案（新增 `_Hint`）。
#       证据：shots/v301_layout/v301_2_个股.png（修复前）→
#             shots/v301_fix/fix_2_个股.png（修复后）。
#   四、**留白压缩的边界已探明并记录**（见 make_table 注释）：v2.1.0 的
#       边距收紧（8→6 / 8→6 / 7→5 / 行高 24→22 / 表头 28→26）已把版面压到位。
#       实测有数据时"个股异动"3×3 网格**零浪费**填满一屏；剩余留白仅出现在
#       "还没来得及采数据"的时段，属内容自然占位，**不可再压**（压了会
#       把数据态一并压坏，实测留白率反升至 77%）。详见 make_table 注释。

# ★★ v2.1.0（2026-09-23）：两级页签重构 + 空白区压缩（用户需求）。
#   一、顶层 14 页签 → 6 母模块，母模块内再分子页（见 TAB_GROUPS）。
#       **未删除、未合并任何子模块**，仅重新归位与排序。
#   二、UI 密度：用户反馈「大部分地方有很长很大块的空白区」。
#       见下方布局常量与 make_* 的边距改动 —— 卡片内边距、行高、
#       页面外边距、KPI 高度全部收紧一档，表格纵向均分表高。
#   三、可回退：原件完整备份在 build/_rollback_20260923/。

# ★★ v2.0.10（2026-09-22 晚）：批注精简 + 区间极值（用户需求）。
#   一、总览「主力资金累计曲线」批注精简：删除全部静态描述（配色/量程/
#       横轴/图例/交互），仅保留一行时点说明 —— 悬停时
#       「时点 HH:MM，右端标签已切换为该分钟数据，移出恢复收盘值」，
#       未悬停时「时点 HH:MM，右端标签为收盘金额」。
#   二、市场全景新增「区间极值」板块（第二行两卡）：
#       · 主力吸筹前十：最新点 − 盘中最低点，差值降序前十；
#       · 主力出货前十：最新点 − 盘中最高点，仅保留负差值、按回撤降序前十；
#       · 口径与总览累计曲线完全一致（F.V 列，亿元），_market_stats 统一计算。

# ★★ v2.0.9（2026-09-22 晚）：性能验证后的三项优化（用户需求）。
#   一、绝对 URL 也走长连接池（core/flowdata.py）—— fetch_spot_batch /
#       opensnap._fetch_list 等此前永远走 urllib 逐请求建连（每次
#       TCP+TLS 握手），现拆 host+path 优先长连接、失败回退 urllib。
#       盘中增量刷新与开盘快照采集在真实（直连）环境下可再提速。
#   二、on_data 拆两段：表格/KPI/开盘检测等先上屏（事件循环先刷一次），
#       图表延后半拍绘制 —— 缓存通道首屏**可见内容**提前约 0.3s。
#   三、开屏窗口的剩余差距经逐段测量确认由解释器/PySide6 导入与系统
#       文件缓存（Defender 扫描）主导（源码实测 UI 构建仅 321ms），
#       代码侧已无安全可动的大头 —— 建议部署目录加 Defender 排除名单。

# ★★ v2.0.8（2026-09-22 晚）：悬停性能根治 + 标签带自适应（用户反馈两点）。
#   一、光标不跟手（逐段计时定位，offscreen 1450px 画布）：
#     · 修复前每次换分钟 154ms（mark 71.8 + labels 82.2），鼠标划动队列爆炸；
#     · 根因 ①：v2.0.6 的双快照互相打靶 —— 标签带快照重建用 self.draw()，
#       会作废竖线快照 → 下一次 _mark 又全幅渲染 → 循环。修复：背景快照
#       重建改走 QtAgg 基类裸渲染（_raw_draw），不清任何缓存；
#     · 根因 ②：20 个 matplotlib Text 的 draw_artist 单条 1.5~3ms（字符串
#       一变字形布局全重来）= 35ms/次。修复：右端标签改用 **Qt 覆盖层**
#       （FanLabelOverlay，QPainter 画 20 条字符串 <0.5ms，与画布分层）。
#     · 修复后：换分钟全周期 <1ms（mark 0.26 + overlay 0.3），跟手。
#   二、图表留白过多：标签带宽度按最宽标签实测自适应（90~280px），
#     不再固定砍 22% —— 宽屏下曲线区拉到最长，内容占比更饱满。

# ★★ v2.0.7（2026-09-22 晚）：页面布局调整（用户需求）。
#   · 「个股精选」从「个股异动」页移出，独立成页整宽展示（数据/口径不变）；
#   · 「个股异动」九张 Top10 表去掉滚动容器，3×3 网格行列等分直接铺满一屏，
#     每张表的 10 行用 QHeaderView.Stretch 纵向均分表高，不留底部空白；
#     窗口过矮时表内自滚动，不溢出不重叠。

# ★★ v2.0.6（2026-09-22 晚）：悬停联动融入曲线右端标签（用户需求）。
#   · 删除市场全景页的独立「时点数据面板」（lbl_hover / t_hover 整块）；
#   · 悬停到总览曲线的某一分钟 → 每条曲线右端标签实时切换为**该分钟**
#     的资金数据，随鼠标移动逐分钟更新；移出恢复各曲线终点收盘值；
#   · 实现要点：标签文字变更走**标签带独立 blit**（_label_frame /
#     _ensure_label_bg / _labels_blit），restore 后只重画 20 个 Text，
#     不触发整幅 draw —— 悬停流畅性不回退（v1.2.2 的 0.16ms 纪律延续）；
#   · on_fan_hover 精简为总览页一行时点摘要（score_table 每分钟重算随之移除）。

# ★★ v2.0.5（2026-09-22 傍晚）：主力资金累计曲线标注改造（用户需求）。
#   · 每条曲线最右端终点处直接绘制文字标签「板块名 收盘金额（XX.X 亿）」，
#     颜色与曲线一致；
#   · **彻底废弃底部全局图例**（v1.1.6 的图例带/列数二分/底部留白整套机制
#     移除：_legend_outside / _reserve_bottom / _make_legend / FAN_LEG_*），
#     省掉来回对照颜色辨认曲线的动作；
#   · 盯盘软件式布局：右侧独立标签带（ax 宽度压缩 22%）+ 最小间距收敛
#     防叠字 + 引线接回终点；曲线区因此多出原图例带的整块高度。
#     实现见 FanChart._end_labels。

# ★★ v2.0.4（2026-09-22 下午）：新增「开盘检测」「个股异动」两页（用户需求一/二）。
#   数据层 core/opensnap.py：开盘后 5/10/15 分钟（09:36/09:41/09:46 触发，
#   宽限 60s）对全市场做定点快照 —— 板块 1000 个 + 个股约 5900 个，
#   字段：成交量(累计)/成交额(累计)/量比/主力净流入(累计)。
#   ★ 快照是"当时的市场状态"，接口没有历史 → 程序没在节点运行就是真缺失，
#     绝不晚采冒充（见 opensnap.GRACE_SEC 注释）。
#   页面：开盘检测 = 三节点列 × 成交量/净流入 Top5（板块）；
#         个股异动 = 三节点列 × 量比/成交量/净流入 Top10（个股）
#                   + 子级「个股精选」榜（九榜出现次数降序，9/9 第一梯队）。

# ★★ v2.0.3（2026-09-22）：启动延迟专项（自检 v2.0.2b 日志后定位的三个点）。
#   日志证据（app_20260922.log）：3 秒内 4 条 preflight ok（用户等不及连点 4 次，
#   其中 3 次各弹一个「已在运行中」）、窗口 1853ms 才出现、首屏数据 6861ms。
#   三项修复：
#     ① 单实例锁提前到 preflight 之前 —— 重复点击的进程瞬间退出并尝试把
#        已有窗口拉到前台，不再各跑一遍 preflight、各弹一个对话框；
#     ② 两段式首屏 —— 盘中启动时本地面板虽然落后（缺口>6分钟）但**可用**，
#        先把它渲染上屏（~2s），全量补抓在后台继续，完成后自动刷新
#        （旧版要干等全量抓完才见数据，~7s）；
#     ③ matplotlib 延迟加载 —— import ~1.0s 挪到窗口显示后执行，
#        窗口出现提前约 1 秒（见 ensure_mpl / _materialize_charts）。

# ★★ v2.0.2（2026-09-22）：修「非交易时段 / 接口被限流时程序完全不可用」。
#   起因：用户凌晨打开程序，界面清空并弹「数据加载失败」，
#        而本地明明存着 0918（上周五）和 0921（本周一）的完整全天数据。
#   三个缺陷，逐个修：
#     ① 【数据被删 · 最严重】启动时的 30 交易日窗口清理会误删**最近**一天
#        的真实数据。根因见 tradecal.last_trusted 的说明：日历缓存若是在
#        上一个交易日**盘中**写的，就缺那一格；次日启动联网失败回退到这份
#        过期缓存，旧代码用 approx=False 让它蒙混过关，于是 purge 把那一格
#        （真实交易日）当"窗口外"删掉了 —— 数据永久丢失。
#        → 现在日历来源不可信时一律跳过窗口清理。
#     ② 【不合理地要求联网】_plan_fetch 只认 panel_{今天}.npy，本地即便有
#        0918/0921 也算"无本地底表"，直接 full fetch → 限流时必然失败 →
#        界面清空。现在：非盘中直接复用最近一天；盘中抓失败也回退到本地
#        最近一天（并按"历史回看"语义走，不生长实时榜单池、不污染历史存档）。
#     ③ 【静态数据也依赖联网】板块清单（代码/名称）抓不到就 raise，导致整个
#        面板建不起来。现在抓到就落盘 sectors.json，抓不到读缓存 ——
#        板块清单本质是静态数据，没理由每次都要联网。

# ★ v2.0.1：进程启动时刻（模块级，import 完就算）。
#   用于在日志里给出"从进程启动到首屏数据上屏"的端到端耗时 ——
#   优化首屏如果只量"某一段"，很容易得出"已经很快了"的错误结论，
#   必须有一个从进程开始计的锚点才能看出大头在哪（实测：大头是
#   Python + PySide6 + matplotlib 的 import，其次才是绘图）。
T_BOOT = time.perf_counter()

# ★★ v1.2.5：全天关键节点榜改为**按时间先后**排列（用户报「排列无序、杂乱」）。
#
#   根因：`salience.timeline_summary()` 结尾是
#       rows.sort(key=lambda r: -r["strength"])          ← 按强度倒序
#   这个函数最初是给「收盘解说」的文字稿用的（只念前几条，谁劲爆谁靠前），
#   但后来被复用成**表格**的数据源，「全天关键节点」页与 HTML 报告的
#   「全天关键节点榜」都是它 —— 表格里按强度排，时间轴就完全跳跃：
#       09:42 → 09:45 → 10:15 → 09:51 → 09:48 → 11:00 → 11:03 → 10:57 …
#
#   改法：主键改成 `_time_key(time)`（HH*60+MM 的分钟数）升序，
#   `idx` 作次级键，`-strength` 作三级键（同刻内强的在前，保证全序）。
#
#   ★ 为什么排序键不用横轴那套「分钟序号」（09:31=0…15:00=239）：
#     那是为画图定位设计的（午休不占格，否则图上留两小时空白）；
#     排序要的是真实钟点先后，HH*60+MM 跨午休依然单调。两套口径用途不同。
#
#   ★ 连带修正的两处（不改会静默退化，最难发现的那一类）：
#     · `timeline_summary(F)[:24]` / `[:18]` —— 这些截断原先**依赖强度序**
#       才有"取最强的 N 条"语义。改成时间序后同一行代码会变成"取最早的 N 条"，
#       尾盘整段消失、行数还一样、不报错。改为显式参数 `topn=N`：
#       先按强度选出 N 条，再按时间摆好 —— 把"选谁"和"怎么摆"分开。
#     · 界面 hint 原写「按强度排序」，与新的行序不符 → 改为「按时间先后
#       排列（早→晚）· 强度见末列」。
#
# ★★ v1.2.4：曲线与横轴的时间同步修复（用户报「曲线只画到十点多」）。
#
#   根因 (b)：曲线 x 用的是**数组下标** `range(len(s))`，而横轴刻度用的是
#   **分钟序号** `_minute_index()`（09:31=0 … 15:00=239）。两套坐标系只有在
#   "面板一分钟都不缺"时才恰好相等 —— 而盘中面板必然有缺口：
#     · 增量刷新每次只追加"当前这一分钟"（程序没运行的分钟永久缺）；
#     · 个别分钟接口返回空行（fetch_sector_kline 里 continue）。
#   实测 2026-09-21 11:17 的面板：42 个点覆盖 09:31~11:43，真实跨度 107
#   分钟，末端却落在下标 41 → 横轴上读作 **10:12**，比真实数据落后 65 分钟。
#
#   修复三处（缺一不可）：
#     ① 绘图按**分钟序号**落点（_x_positions）→ 数据到几点，曲线画到几点；
#     ② 悬停/准星把"鼠标格"换算回"数据行号"（_row_at_x）→ 图与表一致；
#     ③ 数据侧堵住缺口来源（clamp_to_bar 不再写非交易分钟；
#        缺口 > SPOT_MAX_GAP_MIN 时交回全量重抓，kline 一次给完整分时）。
#
# ★★ v1.2.3：盘中刷新周期（毫秒）与"缓存陈旧"阈值。
#
#   用户需求：「盘中抓取数据不是很频繁，要求 30 秒甚至 10 秒一抓」。
#
#   能做到 10 秒的关键是 v1.2.3 新增的**批量快照增量刷新**
#   （flowdata.refresh_panel_spot，实测 ~350ms / 5~6 次请求）——
#   原先的刷新只能全量重抓（1000 次请求 / 2~10 秒），
#   10 秒周期在那种口径下根本来不及，且必然被数据源限流。
#
#   周期档位与实测成本：
#     10 秒 → 6 次请求 / 10 秒   = 0.6 请求/秒   ← 峰值档（用户理想值）
#     30 秒 → 6 次请求 / 30 秒   = 0.2 请求/秒   ← 默认档（用户要求）
#     5 分钟 → 6 次请求 / 300 秒 = 0.02 请求/秒  ← 旧的固定档（已退役）
#   对照：旧全量刷新一次就是 1000 次请求，**一次就超过 30 秒档跑 8 分钟的
#   总量**。所以缩短周期不但不增加压力，反而大幅降低 —— 因为换了通道。
AUTO_MS_OPTIONS = [
    ("1 分钟（推荐 · 与数据源分钟粒度对齐）", 1 * 60 * 1000),
    ("2 分钟", 2 * 60 * 1000),
    ("3 分钟", 3 * 60 * 1000),
    ("5 分钟", 5 * 60 * 1000),
    ("10 分钟", 10 * 60 * 1000),
    ("15 分钟（最省）", 15 * 60 * 1000),
]
# ★★ v3.0.6：默认档从 3 秒改为 **1 分钟**，档位全部改为**分钟级**。
#
#   【为什么改】2026-09-23 本机 IP 被东财 push2 集群封禁，复盘结论是
#   "单位时间请求总量"踩线。虽然跨分钟守卫已经把**真实抓取**压到每分钟 1 次，
#   但界面每秒 tick 一次仍会带来两个坏处：
#     ① 每一拍都要走一遍"时段判定 / 忙碌态 / 数据新鲜度"判断，
#        而且**用户会以为程序在不停请求**，体感与"被限流"直接挂钩；
#     ② 秒级档位下"跨分钟后立即抓"这一下，会与别的后台任务（异动补采、
#        龙头分析）撞在同一秒，瞬时并发更高 —— 这正是最容易踩线的形态。
#
#   【为什么不损失数据新鲜度】数据源本来就是**分钟粒度**：
#     同一交易分钟内反复请求拿回的是**完全相同的累计值**（见 _auto_tick 的
#     跨分钟守卫）。所以 1 分钟档拿到的东西与 1 秒档**完全一样**，
#     区别只是"跨分钟后多久去取"（最多 60 秒 vs 最多 1 秒）。
#     用最多 60 秒的等待，换"绝不因为刷新本身触发限流"，是划算的。
#
#   ★ 旧的 1/2/3/5/10/30 秒档**已从菜单移除**（不是隐藏，是删掉）：
#     留着它们等于把"再被限流一次"的开关摆在用户面前。
AUTO_MS_DEFAULT = 1 * 60 * 1000

# ★★ v2.0.1：「刷新」= 取最新（用户要求），所以陈旧阈值从 60 秒收紧到 3 秒。
#
#   为什么不是 0（即"每次刷新都无条件抓"）：
#     自动刷新 30 秒一轮、用户可能连点 F5 —— 若没有下限，短时间内会重复
#     发同一批请求，既无意义又容易触发数据源限流（实测批量快照通道
#     5~6 次请求/轮，连点几下就是几十次）。
#   为什么是 3 秒而不是 60 秒：
#     上一版把阈值定在 60 秒，于是"刚点过刷新、再点一次"会静默什么都不做 ——
#     用户看到的就是"刷新没反应 / 还是旧数据"，正是他要修的那个体验。
#     3 秒足够吸收连点，又不可能让用户感觉到"我点了它却没去拿新的"。
#
# ★★ v3.0.4：从 3.0 秒收紧到 **0.8 秒**（配合 1~10 秒档位）。
#   为什么必须收紧：这个阈值是"两次真实抓取之间的最小间隔"。
#   如果还留在 3 秒，那么选了「1 秒档」后会有 2/3 的 tick 被它挡掉 ——
#   用户设了 1 秒却每 3 秒才更新一次，属于**设置被静默否决**。
#   0.8 秒的取值理由：
#     · 略小于最快档（1 秒），保证 1 秒档每个 tick 都能真的去抓；
#     · 仍能吸收"同一瞬间的连点"（人手连点间隔通常 >0.15s，
#       而真正的重复防护由 _auto_tick 的跨分钟守卫承担，见那里）。
#   ★ 注意它**不是**限流器 —— 限流靠的是每分钟的跨分钟守卫 +
#     批量快照通道本身的低频特性（每分钟最多一次真实抓取）。
#   ★ v3.0.6：档位已全部改为**分钟级**（见 AUTO_MS_OPTIONS），
#     所以这里不再需要"略小于最快档"这个约束。但**不要**因此把它调大：
#     它的职责是"吸收人手连点"，而不是限流 —— 真正的限流由
#     `_auto_tick` 的跨分钟守卫 + `flowdata._NET_GATE` + 全局熔断三层承担。
#     调大它只会让"用户点了刷新却没反应"那个老问题回来。
REFRESH_MIN_GAP_SEC = 0.8


def source_tag():
    """状态栏用的数据源标签。

    ★ v3.0.6：**只在用替代源时才返回非空** —— 东财是正常状态，不必占地方；
      一旦用了替代源就必须显示，因为数据能力不同（腾讯源没有分钟级资金流
      历史），不显示会让人把"能力缺失"误判成"程序坏了"。
    """
    try:
        import source as _src
        p = _src.active()
        return "" if p == _src.EASTMONEY else f"{p}(替代)"
    except Exception:                                    # noqa: BLE001
        return ""


# ==================================================================== 布局常量
# ★ v1.1.6：把间距/边距收成常量，避免"改一处漏一处"。
#   起因：总览页写的是 setSpacing(7)，其余 8 个页面写的是 setSpacing(8) ——
#   同一套界面里两种节奏，切标签页时能看出细微差别。根因是当初逐页手写数字。
#   现在统一从这里取，要调也只调一处。
#
# ★★ v2.1.0（2026-09-23）：全体收紧一档 —— 用户反馈「大部分地方有很长很大块
#   的空白区」。旧值 (8,6,8,8)/8/7 是给"14 页签 + 卡片套卡片"的稀疏版面定的，
#   在 1480×940 下实测每页四周有 20~40px 纯空白、表格底部还有半屏空行。
#   现在：页边距 8→6、区块间距 8→6、卡片间距 7→5。
#   ⚠ 这些常量被 14 个子模块共用，改一处全局生效 —— 仍只调这一处。
PAD_PAGE = (6, 4, 6, 6)      # 页面级 layout 外边距（左,上,右,下）
GAP_PAGE = 6                 # 页面内主要区块之间的间距
GAP_CARD = 5                 # 同排卡片之间的间距（与卡片内边距视觉对齐）

# ★★ v2.1.0：子页签（母模块内部）专用尺寸。
#   为什么单独一套：顶层页签是"主标签"，需要更大点击区与更重的视觉；
#   子页签是"次级切换"，若沿用同一尺寸，一个母模块里挂 4 个子页就会
#   占掉整条横向空间 —— 那正是要消掉的空白与拥挤的来源。
SUB_TAB_H = 27               # 子页签条高度（顶层是 auto，约 34px）
SUB_TAB_PAD = "5px 13px"     # 子页签内边距（顶层为 8px 17px）
SUB_PANE_PAD = (0, 0, 0, 0)  # 子页容器自身不再加边距（避免与 PAD_PAGE 叠两层）

# ★★ v2.0.1：序号/排名列的**统一宽度**。
#   为什么单独提出来：这类列在 8 张表里都出现，各写一个数字必然漂移
#   （v1.2.5 时「排名」是 46、「#」是 46，而「序号」两个字需要更宽）。
#   统一成一个常量，改一次全部生效，视觉上所有表的第 1 列也对得齐。
#   46 只够放一个「#」；「序号」两字 + 单元格内边距实测需要 54。
SEQ_COL_W = 54


# ==================================================================== 路径与日志
def portable_root():
    """便携模式根目录：exe（或脚本）所在的那个目录。

    ★ v1.2.6 新增（v2.0.1 沿用）。为什么需要它：
      本程序默认把数据写到 %LOCALAPPDATA%\\FlowLiveNative\\ —— 装在本机时没问题，
      但用户要求"整个文件夹搬到 D 盘后，历史数据要跟着走、新数据也要写到 D 盘"。
      硬编码 %LOCALAPPDATA% 做不到这一点（搬到哪都写回原位置）。

    ★ 判定依据：**必须存在一个"有内容的 data 目录"**。

      为什么不用更宽松的条件（如"存在 data 或 logs 或 shots 任一"）：
        构建目录下天然有 shots/（自检截图）和空的 data/，
        开发期跑一次测试就会生成空 data/ —— 用"目录存在"判会把**构建目录**
        也当成便携部署，于是开发机上的数据被写进源码树，非常难排查。
        （实测：第一版判据就在这里误判了。）

      为什么不用"加一个 flag 文件"：
        flag 需要用户理解并手工创建，一旦漏建就静默写回 C 盘 ——
        与本需求"迁移后必须写 D 盘"直接冲突。

      "data 目录里有文件" 是**客观事实**：迁移时数据一并拷过去即自动生效，
      空目录不算（既避开构建目录误判，也保证首次运行仍走原位置）。
    """
    if getattr(sys, "frozen", False):
        # 打包后：exe 所在目录（onedir 时 _MEIPASS 在它下面一层）
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        # 源码直跑：脚本所在目录
        base = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, "data")
    try:
        if os.path.isdir(d) and any(os.scandir(d)):
            return base
    except OSError:
        pass
    return None


def app_home():
    """应用主目录（数据 / 日志 / 截图 / 锁文件都挂在这里）。

    ★ v1.2.6：便携部署时 = exe 同级目录；否则回落 %LOCALAPPDATA%\\FlowLiveNative。
      把 data/logs/shots/app.lock 全部收口到这一个函数下，
      才能做到"整个文件夹搬到哪、数据就跟到哪"。
    """
    pr = portable_root()
    if pr:
        os.makedirs(pr, exist_ok=True)
        return pr
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    p = os.path.join(base, "FlowLiveNative")
    os.makedirs(p, exist_ok=True)
    return p


def app_data_dir():
    """用户可写数据目录。

    ★ onefile 打包后 core/ 被解压到只读的 _MEIPASS 临时目录，
      而且每次启动路径都会变，绝不能把缓存写在那里。

    ★ v1.2.6：改为挂在 app_home() 下 —— 便携部署时即为 exe 同级 data/。
    """
    p = os.path.join(app_home(), "data")
    os.makedirs(p, exist_ok=True)
    return p


def current_data_dir():
    """当前生效的数据目录 —— **所有** 面板/归档读写都应走这里。

    ★ 为什么不让各处直接调 app_data_dir()（v1.1.5 修）：
      面板的读写路径由 flowdata.DATA 决定（启动时设为 app_data_dir()，
      但归档、回看下拉、窗口清理曾各自直接算 app_data_dir()）——
      生产环境两者恰好相等所以看不出问题，可一旦数据目录被改写
      （测试注入、或将来支持自定义路径），"面板存在这里、索引却读那里"
      就会表现为"明明有数据，回看列表却是空的"。
      统一到一个函数，谁改都只改一处。
    """
    import flowdata
    d = getattr(flowdata, "DATA", None)
    return d or app_data_dir()


def ensure_user_data_dir():
    """确保 flowdata.DATA 落在用户可写目录 —— 但**不覆盖**已显式指定的路径。

    ★ 为什么需要这个函数（v1.1.5 修）：
      onefile 打包后 flowdata 自带的 DATA 指向 _MEIPASS 下的只读目录，
      后台线程启动时必须改到 %LOCALAPPDATA%\\FlowLiveNative\\data。
      但旧写法是**无条件** `flowdata.DATA = app_data_dir()` ——
      谁在这个线程之后想用别的目录（测试注入、将来支持自定义数据路径），
      都会被它悄悄改回默认值，症状就是"数据明明在那儿却读不到"。
      现在只在 DATA 仍是模块默认值时才重定向。
    """
    import flowdata
    cur = getattr(flowdata, "DATA", "") or ""
    default = os.path.join(getattr(flowdata, "ROOT", "") or "", "data")
    try:
        same = os.path.normcase(os.path.abspath(cur)) == \
            os.path.normcase(os.path.abspath(default))
    except Exception:                                    # noqa: BLE001
        same = True
    if not cur or same:
        flowdata.DATA = app_data_dir()
    # 向后兼容：salience 早期是 `from flowdata import DATA`（值拷贝），
    # 改了源必须同步。v1.1.6 已把 salience 改成惰性引用（不再产生脱钩），
    # 这里保留一句 setattr 兜底 —— 万一将来又有人加回顶层 import，
    # 也不会出现"两份 DATA 各说各话"。用 setattr 避免 import salience 失败时崩。
    try:
        import salience
        setattr(salience, "DATA", flowdata.DATA)
    except Exception:                                    # noqa: BLE001
        pass
    return flowdata.DATA


def shots_dir():
    """自检截图目录。

    注意：运行时 __file__ 指向 _MEIPASS 临时目录（随时被删），
    所以截图绝不能写在 BASE 下。

    ★ v1.2.6：跟随 app_home() —— 便携部署时截图也落在 D 盘，不污染 C 盘。
      仍保留 FLOWLIVE_SHOTS 环境变量作为显式覆盖（自检/比对时钉死输出位置）。
    """
    p = os.environ.get("FLOWLIVE_SHOTS") or os.path.join(app_home(), "shots")
    os.makedirs(p, exist_ok=True)
    return p


def app_log_path():
    """日志路径。★ v1.2.6：跟随 app_home()，便携部署时日志写 D 盘。"""
    d = os.path.join(app_home(), "logs")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"app_{datetime.now():%Y%m%d}.log")


# ==================================================================== 应用图标
# ★★ v2.0.1 新增：补齐运行时图标（用户反馈「exe 缺软件图标」）。
#
#   【为什么是"缺"而不是"没有"】打包时 spec 里写了 icon=['app.ico']，
#   所以**资源管理器里的 exe 文件图标是有的**（实测 PE 资源表含
#   RT_ICON=3 / RT_GROUP_ICON=14）。
#   但代码里从头到尾没有调用过 setWindowIcon —— 于是：
#     · 窗口左上角、任务栏按钮、Alt+Tab 切换器显示的都是 **Qt 默认图标**；
#     · 从任务栏"固定到任务栏"之后，钉住的也是那个默认图标。
#   也就是说：**盘符上看有图标，用起来到处都没有图标**。
#
#   【修法三件事】
#     ① 把 app.ico 作为数据文件打进 bundle（spec 的 datas 里加一条），
#        否则打包后运行时那个路径根本不存在；
#     ② 运行期按「_MEIPASS → exe 同级 → 源码目录」顺序找一个真实存在的 ico；
#     ③ 给 QApplication 设图标（覆盖所有窗口 + 任务栏），
#        再显式给主窗口设一次（部分平台的窗口管理器只认窗口级图标）。
#
#   【还差一步：Windows 任务栏的图标归属】
#     即使设了 windowIcon，Windows 仍可能把进程归到 python.exe/默认分组下，
#     从而在任务栏上显示别的图标。解决办法是声明 AppUserModelID（见
#     _set_app_user_model_id）—— 它告诉 shell"这个进程是独立的一个应用"。
APP_ICON_NAME = "app.ico"


def app_icon_path():
    """定位 app.ico；找不到返回 None（调用方降级为不设图标，不抛异常）。"""
    cands = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "") or ""
        if meipass:
            cands.append(os.path.join(meipass, APP_ICON_NAME))
            # onedir：真实 exe 同级也放一份（_MEIPASS 就在它下面一层）
        try:
            cands.append(os.path.join(
                os.path.dirname(os.path.abspath(sys.executable)), APP_ICON_NAME))
        except Exception:                                # noqa: BLE001
            pass
    cands.append(os.path.join(BASE, APP_ICON_NAME))
    for p in cands:
        try:
            if p and os.path.exists(p):
                return p
        except Exception:                                # noqa: BLE001
            continue
    return None


def app_icon():
    """返回应用 QIcon。取不到文件时返回空 QIcon（Qt 会退回默认图标，不崩）。"""
    try:
        p = app_icon_path()
        if p:
            return QIcon(p)
    except Exception as e:                               # noqa: BLE001
        log_line(f"app icon load failed: {e}")
    return QIcon()


def _set_app_user_model_id(appid="Abu.FlowLive.Native"):
    """声明本进程的 AppUserModelID（Windows 任务栏图标归属）。

    不声明的话，任务栏会把进程并到 python.exe / 默认分组下，
    于是"设置了窗口图标却还是显示别的图标"。失败不影响使用 ——
    非 Windows 平台或受限环境直接跳过。
    """
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(appid)
        return True
    except Exception:                                    # noqa: BLE001
        return False


def log_line(msg):
    """写一行运行日志。

    ★★ v2.0.1：主日志写不进去时**降级到备用文件名**，不再静默丢弃。

    【为什么改】本版实测到一个真实场景（便携部署目录被整体替换后）：
      旧的 `app_YYYYMMDD.log` 处于"新进程无法追加"的状态 ——
      **exe** 打开它报 `PermissionError: [Errno 13]`，
      而同一个文件用普通 Python 追加却完全正常（属性也只有 ARCHIVE，
      重命名/删除都能做）。把该文件删掉后，exe 立刻就能新建并正常写入。
      也就是说：**文件本身处于一种让 frozen 程序无法写入的状态**。

    【旧行为的后果有多严重】原实现是 `try: ... except Exception: pass` ——
      于是一旦命中这个状态，**整台机器的运行日志全部消失**：
      preflight、加载结果、失败原因、性能数据，一条都没有。
      排查时表现为"程序在跑，但日志是空的"，比报错更难定位
      （本轮就是靠比截图文件大小才发现的）。

    【现行为】主路径失败 → 依次尝试 `app_YYYYMMDD.<n>.log`（最多 3 个）。
      宁可日志文件名字不规整，也绝不让日志消失。
      全部失败才放弃（此时已无计可施，但至少不会掩盖问题）。
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    base = app_log_path()
    for p in [base] + [base[:-4] + f".{i}.log" for i in (1, 2, 3)]:
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
            return
        except Exception:                                # noqa: BLE001
            continue                                     # 换下一个文件名再试

# 主题（A 股约定：红涨绿跌）
# 说明：浅色与深色两套必须保持相同的 key 集合，
#       QSS 与 matplotlib 两条渲染路径都从这里取色，改一处即可全局生效。
THEMES = {
    "light": dict(
        bg="#ffffff",          # 页面底色
        fg="#16181d",          # 主文字
        sub="#6b7280",         # 次要文字
        muted="#9aa1ae",       # 更弱的说明文字
        grid="#e9ecf1",        # 网格线 / 分隔线
        card="#f7f8fb",        # 卡片底色
        card_alt="#eef1f6",    # 次级卡片 / 表头底色
        border="#dfe3ea",      # 边框
        hover="#eef2f8",       # 悬浮态
        sel_bg="#e3ebfa",      # 选中态底色
        header="#f3f5f9",      # 表头
        up="#e0393e",          # 涨（红）
        up_soft="#fdecec",     # 涨的浅底
        dn="#12a05c",          # 跌（绿）
        dn_soft="#e8f7ef",     # 跌的浅底
        accent="#2f6fe4",      # 强调色
        accent_soft="#e8f0fe",
        axis="#c7ccd6",        # 坐标轴
    ),
    "dark": dict(
        bg="#17191e",
        fg="#e9ecf1",
        sub="#a3aab6",
        muted="#7c838f",
        grid="#2a2e36",
        card="#1f2229",
        card_alt="#252932",
        border="#31363f",
        hover="#272c35",
        sel_bg="#2b3648",
        header="#22262e",
        up="#ff5f63",
        up_soft="#3a2226",
        dn="#3ecf8e",
        dn_soft="#1d3329",
        accent="#6f9bff",
        accent_soft="#232e45",
        axis="#434a56",
    ),
}

# ---------------------------------------------------------------- 涨跌配色
# ★ v1.2.1：**移除「红涨绿跌 ⇄ 绿涨红跌」切换按钮**（用户要求）。
#   配色方向固定为 **红涨绿跌（A 股约定）**。
#
#   保留 `coloring()` 这个统一取色入口，而不是把调用点全部展开 ——
#   全代码库零硬编码颜色（28 个取色点都经 th["up"]/th["dn"]），
#   入口留着，日后若要恢复方向切换只需在这里加一个开关，
#   不需要再去改绘图/填表函数。现在它是一个恒等映射（返回原字典）。
#
#   历史背景：v1.1.5 曾支持反向配色，当时的实现方式是交换 up/dn 与
#   up_soft/dn_soft。该能力现已下线 —— 但**交换逻辑本身是正确的**，
#   若将来要恢复，照抄下面注释里的两行即可，不要在每个绘图点单独判断。
def coloring(th):
    """统一「涨跌取色」入口 —— 返回 th 本身（红涨绿跌，A 股约定）。

    调用方拿到的 th["up"] 表示「涨」的颜色、th["dn"] 表示「跌」的颜色。
    若日后要恢复方向切换，在此处按开关交换：
        out = dict(th)
        out["up"], out["dn"] = th["dn"], th["up"]
        out["up_soft"], out["dn_soft"] = th["dn_soft"], th["up_soft"]
    """
    return th


def build_qss(th):
    """全局样式表。

    Qt 原生的 Fusion 风格相当素，这里统一给菜单栏/工具栏/标签页/表格/
    滚动条/状态栏上样式，让整体看起来更像现代桌面软件。
    ★ 只影响外观，不改任何控件行为。
    """
    return f"""
    QMainWindow, QWidget#pageRoot {{ background: {th['bg']}; }}
    QWidget {{ color: {th['fg']}; font-family: "Microsoft YaHei", "Segoe UI", sans-serif; }}

    /* ---------------- 菜单栏 ---------------- */
    QMenuBar {{
        background: {th['card']};
        border-bottom: 1px solid {th['border']};
        padding: 2px 6px;
    }}
    QMenuBar::item {{ padding: 5px 11px; border-radius: 5px; background: transparent; }}
    QMenuBar::item:selected {{ background: {th['hover']}; }}
    QMenuBar::item:disabled {{ color: {th['muted']}; }}
    QMenu {{
        background: {th['bg']};
        border: 1px solid {th['border']};
        border-radius: 8px;
        padding: 5px;
    }}
    QMenu::item {{ padding: 6px 26px 6px 14px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {th['accent_soft']}; color: {th['accent']}; }}
    QMenu::item:disabled {{ color: {th['muted']}; }}
    QMenu::separator {{ height: 1px; background: {th['border']}; margin: 5px 8px; }}

    /* ---------------- 工具栏 ---------------- */
    QToolBar {{
        background: {th['card']};
        border-bottom: 1px solid {th['border']};
        padding: 5px 8px;
        spacing: 6px;
    }}
    QToolBar QToolButton {{
        padding: 5px 13px;
        border: 1px solid {th['border']};
        border-radius: 6px;
        background: {th['bg']};
        color: {th['fg']};
    }}
    QToolBar QToolButton:hover {{
        background: {th['accent_soft']};
        border-color: {th['accent']};
        color: {th['accent']};
    }}
    QToolBar QToolButton:pressed {{ background: {th['sel_bg']}; }}
    /* ★ v3.0.6：顶部信息栏（板块/分钟点/分时到/数据日/数据源）——
       比按钮小一档、弱一色，只作信息呈现，不与操作项争视觉。 */
    QLabel#tbInfo {{ color: {th['muted']}; font-size: 11px; padding-left: 6px; }}
    QToolBar QToolButton:disabled {{
        color: {th['muted']}; background: {th['card']};
        border-color: {th['border']};
    }}
    /* ★ 可勾选按钮的选中态（v1.1.6 补）：
       原来只有 :hover / :pressed，**没有 :checked** —— 工具栏那个
       「绿涨红跌」开关（checkable QAction 渲染成 QToolButton）勾上之后
       外观毫无变化，用户无法从界面判断当前到底是哪个配色方向。
       现在选中态用强调色实底 + 白字，与标签页选中态同一套视觉语言。 */
    QToolBar QToolButton:checked {{
        background: {th['accent']};
        border-color: {th['accent']};
        color: #ffffff;
        font-weight: 600;
    }}
    QToolBar QToolButton:checked:hover {{
        background: {th['accent']};
        border-color: {th['accent']};
        color: #ffffff;
    }}

    /* ---------------- 下拉框（回看日期）---------------- */
    /* ★ v1.1.6 补：此前 QComboBox 完全没样式，用的是 Fusion 默认外观，
       在自定义主题里显得"外来户"——边框、圆角、内边距都与旁边的
       工具按钮与卡片对不上。 */
    QComboBox {{
        background: {th['bg']};
        border: 1px solid {th['border']};
        border-radius: 6px;
        padding: 4px 9px;
        min-height: 20px;
        color: {th['fg']};
    }}
    QComboBox:hover {{ border-color: {th['accent']}; }}
    QComboBox:focus {{ border-color: {th['accent']}; }}
    QComboBox:disabled {{
        color: {th['muted']}; background: {th['card']};
        border-color: {th['border']};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: center right;
        width: 20px;
        border: none;
        background: transparent;
    }}
    QComboBox::down-arrow {{
        /* 纯 CSS 画三角，避免依赖图标资源（打包后图标路径易丢） */
        width: 0; height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {th['sub']};
        margin-right: 6px;
    }}
    QComboBox::down-arrow:disabled {{ border-top-color: {th['muted']}; }}
    QComboBox QAbstractItemView {{
        background: {th['bg']};
        border: 1px solid {th['border']};
        border-radius: 6px;
        padding: 4px;
        outline: none;
        selection-background-color: {th['accent_soft']};
        selection-color: {th['accent']};
    }}

    /* ---------------- 复选框（菜单里的开关）---------------- */
    QCheckBox {{ color: {th['fg']}; spacing: 7px; padding: 3px 2px; }}
    QCheckBox::indicator {{
        width: 15px; height: 15px;
        border: 1px solid {th['border']};
        border-radius: 4px;
        background: {th['bg']};
    }}
    QCheckBox::indicator:hover {{ border-color: {th['accent']}; }}
    /* 选中态用"底色填充"表达，不引图标资源 */
    QCheckBox::indicator:checked {{
        background: {th['accent']};
        border-color: {th['accent']};
    }}
    QCheckBox::indicator:disabled {{
        background: {th['card']}; border-color: {th['border']};
    }}

    /* ---------------- 焦点可见性（键盘导航）---------------- */
    /* ★ v1.1.6 补：此前全局没有 :focus 样式，用 Tab 键走一遍界面
       完全看不出焦点落在哪个控件上（纯鼠标用户不受影响，
       但这是可访问性的硬缺口）。
       这里只给"需要键盘操作"的控件加焦点环，避免给每个 QLabel
       也描边导致界面花掉。 */
    QPushButton:focus, QToolButton:focus, QComboBox:focus,
    QTableWidget:focus, QTextBrowser:focus {{
        border: 1px solid {th['accent']};
    }}
    QTableWidget:focus {{ outline: none; }}

    /* ---------------- 标签页 ---------------- */
    QTabWidget::pane {{
        border: none;
        background: {th['bg']};
    }}
    QTabBar {{ qproperty-drawBase: 0; }}
    QTabBar::tab {{
        background: transparent;
        color: {th['sub']};
        padding: 8px 17px;
        margin: 5px 3px 0 0;
        border-radius: 7px;
        font-size: 12.5px;
    }}
    QTabBar::tab:hover {{ background: {th['hover']}; color: {th['fg']}; }}
    QTabBar::tab:selected {{
        background: {th['accent']};
        color: #ffffff;
        font-weight: 600;
    }}

    /* ---------------- 子页签（★ v2.1.0 母模块内部）----------------
       与顶层页签的区别是**刻意做出来的**：顶层是"实底强调 + 白字"，
       子层是"下划线指示 + 强调色文字" —— 一眼能看出层级，不会误认成
       又一排平级主标签。选用下划线而非实底还有一个实际理由：
       一个母模块里最多 4 个子页，实底会让半条横栏变成色块、喧宾夺主。
       ★ objectName 是 subTabs（见 _make_group_page），此处只匹配子层，
         不影响顶层 QTabBar::tab。 */
    QTabWidget#subTabs::pane {{
        border: none;
        background: {th['bg']};
        top: -1px;
    }}
    QTabWidget#subTabs > QTabBar::tab {{
        background: transparent;
        color: {th['muted']};
        padding: {SUB_TAB_PAD};
        margin: 0px 2px 0 0;
        border: none;
        border-bottom: 2px solid transparent;
        border-radius: 0px;
        font-size: 12px;
    }}
    QTabWidget#subTabs > QTabBar::tab:hover {{
        color: {th['fg']};
        background: {th['hover']};
    }}
    QTabWidget#subTabs > QTabBar::tab:selected {{
        color: {th['accent']};
        border-bottom: 2px solid {th['accent']};
        background: transparent;
        font-weight: 600;
    }}
    /* 子页签栏本身不再画基线：下划线已经承载"当前在哪一节" */
    QTabWidget#subTabs > QTabBar {{ qproperty-drawBase: 0; }}

    /* ---------------- 卡片容器 ---------------- */
    QFrame#card {{
        background: {th['bg']};
        border: 1px solid {th['border']};
        border-radius: 10px;
    }}
    /* ★★ v3.0.6 重设计（用户需求）：「卡片信息平铺、层级弱、重点不突出」
       「不要边框过重」→ 三处改动：
         · 边框色从 border 换成 **grid**（更淡一档）—— 从"框住"变成"轻分隔"；
         · 圆角 10 → 8px，更克制；
         · 数值字号 21 → **26px / 字重 800**，小字 11.5/10.5 → **10.5/10px**
           —— 主次拉开三档（数值 / 标题 / 说明），一眼先看到数字。 */
    QFrame#kpi {{
        background: {th['card']};
        border: 1px solid {th['grid']};
        border-radius: 8px;
    }}
    QLabel#cardTitle {{
        color: {th['fg']};
        font-size: 12.5px;
        font-weight: 600;
        padding: 0px;
    }}
    QLabel#cardHint {{ color: {th['muted']}; font-size: 11px; }}
    QLabel#sectionTitle {{
        color: {th['fg']};
        font-size: 13px;
        font-weight: 700;
    }}
    QLabel#kpiTitle {{ color: {th['sub']}; font-size: 10.5px; }}
    QLabel#kpiValue {{ font-size: 26px; font-weight: 800; }}
    QLabel#kpiSub {{ color: {th['muted']}; font-size: 10px; }}

    /* ---------------- 表格 ---------------- */
    QTableWidget {{
        background: {th['bg']};
        alternate-background-color: {th['card']};
        border: 1px solid {th['border']};
        border-radius: 8px;
        gridline-color: transparent;
        selection-background-color: {th['sel_bg']};
        selection-color: {th['fg']};
        outline: none;
    }}
    QTableWidget::item {{ padding: 3px 7px; border: none; }}
    QTableWidget::item:selected {{ background: {th['sel_bg']}; color: {th['fg']}; }}
    QHeaderView {{ background: transparent; }}
    QHeaderView::section {{
        background: {th['header']};
        color: {th['sub']};
        padding: 5px 7px;
        border: none;
        border-bottom: 1px solid {th['border']};
        font-weight: 600;
        font-size: 11.5px;
    }}
    QHeaderView::section:first {{ border-top-left-radius: 7px; }}
    QHeaderView::section:last {{ border-top-right-radius: 7px; }}
    QTableCornerButton::section {{ background: {th['header']}; border: none; }}
    /* ★ 行悬浮反馈（v1.1.6 补）：30 行的榜单表，鼠标划过时没有高亮，
       很容易看串行。选中态已由 selection-background-color 处理，
       这里补 hover —— 比选中态更浅一档，两者叠加不冲突。 */
    QTableWidget::item:hover {{ background: {th['hover']}; }}

    /* ---------------- 滚动条 ---------------- */
    QScrollBar:vertical {{
        background: transparent; width: 11px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {th['axis']}; border-radius: 5px; min-height: 32px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {th['sub']}; }}
    QScrollBar:horizontal {{
        background: transparent; height: 11px; margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {th['axis']}; border-radius: 5px; min-width: 32px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {th['sub']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    /* ---------------- 状态栏 ---------------- */
    QStatusBar {{
        background: {th['card']};
        /* ★ v3.0.6（用户需求）：「底部状态栏…缩小字号，弱色显示」
           → 顶部分隔线也一并换成 grid（更淡），整条栏退成"背景信息"。 */
        border-top: 1px solid {th['grid']};
        color: {th['muted']};
        font-size: 11px;
        padding: 2px 8px;
        /* ★ 固定最小高度（v1.1.6 补）：进度条显示/隐藏会改变状态栏的
           内容高度，导致整个窗口内容区上下抖动 2~3px。
           钉死高度后，加载开始/结束时布局不再跳。 */
        min-height: 18px;
    }}
    QStatusBar::item {{ border: none; }}
    QProgressBar {{
        border: 1px solid {th['border']};
        border-radius: 5px;
        background: {th['bg']};
        height: 9px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{ background: {th['accent']}; border-radius: 4px; }}

    /* ---------------- 其他 ---------------- */
    QTextBrowser {{
        background: {th['bg']};
        border: 1px solid {th['border']};
        border-radius: 8px;
        padding: 10px;
    }}
    QSplitter::handle {{ background: {th['grid']}; }}
    QMessageBox {{ background: {th['card']}; }}
    QToolTip {{
        background: {th['card_alt']};
        color: {th['fg']};
        border: 1px solid {th['border']};
        border-radius: 6px;
        padding: 4px 7px;
    }}
    """


# ==================================================================== 后台线程
def _pretty_date(tag):
    """"20260919" -> "2026-09-19"；非日期格式原样返回。"""
    s = str(tag or "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _env_render_all():
    """首屏是否强制绘制**全部**图表（诊断开关）。

    ★ v2.0.1：默认关（按页延迟绘制，首屏最快，见 CHART_PAGE 注释）。
      设环境变量 FLOWLIVE_RENDER_ALL=1 → 恢复"一次性全画"的旧行为。

    保留这个开关的用途有两个，都不是"给用户用的"：
      · A/B 对照测量（首屏到底省了多少，必须能一键切回旧行为才量得准）；
      · 排查"某页的图没出来"时，用它排除"延迟绘制没触发"这一种可能。
    """
    return str(os.environ.get("FLOWLIVE_RENDER_ALL", "")).strip() == "1"


def _fnum(x, default=0.0):
    """安全的 float()：解析失败返回默认值而不是抛异常。"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class Worker(QThread):
    """所有网络与计算都在这里做，绝不阻塞 UI 线程"""
    progress = Signal(str)
    done = Signal(dict)
    failed = Signal(str)
    # ★★ v2.0.3 两段式首屏：全量补抓开始前，先把**本地面板上已有的
    #   （虽然落后但完整可用）**数据先发一版上屏。payload 结构与 done 相同，
    #   另带 `_early: True` 标记（on_data 据此跳过归档/龙头分析等只该做
    #   一次的动作）。
    early = Signal(dict)

    def __init__(self, tag=None, force=False, local_only=False, incremental=False):
        super().__init__()
        # ★ 缓存必须按「日期」隔离（v1.0.5 修复）：
        #   旧版固定 tag="today"，文件名恒为 panel_today.npy —— 第二天启动时
        #   缓存还在，程序直接把**昨天**的数据当今天显示，界面没有任何提示。
        #   改成当天日期后，每个交易日天然一份新缓存；旧 panel_today 若是
        #   今天抓的会先迁移复用，避免平白重抓一次。
        self.tag = tag or datetime.now().strftime("%Y%m%d")
        self.force = force
        # ★ 历史回看模式（v1.1.0）：只读本地已存面板，绝不发起抓取，
        #   也绝不迁移/清理缓存 —— 那些动作只属于实盘加载路径。
        self.local_only = local_only
        # ★★ 增量刷新模式（v1.2.3，高频刷新用）：
        #   True  → 只走「批量快照」通道（5~6 次请求 / ~350ms），
        #           把最新一分钟接到已有面板上；**不重抓全量 kline**。
        #   False → 原全量路径（1000 次请求 / 2~10 秒），建底表或兜底用。
        #   两者共用同一份面板文件，切换对界面透明。
        #   ★ 增量失败（无底表 / 接口异常）时，run() 内部**自动降级**
        #     为全量，保证任何一次刷新都不会"什么都没发生"。
        self.incremental = incremental
        self._cal_days, self._cal_approx = [], False

    def _archive_by_data_date(self, data_dir):
        """把刚抓到的面板按**真实数据日期**归档（v1.1.0 行为，v1.2.3 抽成方法）。

        周末/节假日抓取时，接口返回的是前一交易日的数据，
        文件必须归到那一天名下 —— 否则存储里会多出「周六」这种
        非交易日面板，污染 30 日窗口与回看列表。
        """
        try:
            with open(os.path.join(data_dir, f"panel_{self.tag}.json"),
                      encoding="utf-8") as fmeta:
                dd = (json.load(fmeta).get("data_date") or "").strip()
            if dd and dd != self.tag and len(dd) == 8:
                for ext in ("npy", "json"):
                    os.replace(
                        os.path.join(data_dir, f"panel_{self.tag}.{ext}"),
                        os.path.join(data_dir, f"panel_{dd}.{ext}"))
                idx_src = os.path.join(data_dir, f"index_{self.tag}.json")
                if os.path.exists(idx_src):
                    os.replace(idx_src,
                               os.path.join(data_dir, f"index_{dd}.json"))
                # ★ index_all（东财全A）：漏了它会导致归档后全A 曲线消失
                iall_src = os.path.join(data_dir, f"index_all_{self.tag}.json")
                if os.path.exists(iall_src):
                    os.replace(iall_src,
                               os.path.join(data_dir, f"index_all_{dd}.json"))
                log_line(f"data date is {dd}, archived under it "
                         f"(fetched on {self.tag})")
                self.tag = dd
        except Exception as e:                           # noqa: BLE001
            log_line(f"data_date archive skipped: {e}")

    def _panel_age_sec(self, data_dir):
        """本地面板距现在多少秒（按文件 mtime）。取不到返回 None。"""
        try:
            p = os.path.join(data_dir, f"panel_{self.tag}.npy")
            return max(0.0, time.time() - os.path.getmtime(p))
        except Exception:                                # noqa: BLE001
            return None

    def _panel_gap_min(self, data_dir):
        """本地面板**时间轴**落后当前几分钟。取不到返回 None。

        ★★ 与 _panel_age_sec 的区别（v1.2.4 关键）：
          `_panel_age_sec` 看的是**文件 mtime**，回答"这个文件多久没写了"；
          本方法看的是**面板最后一分钟距现在几格**，回答"数据本身落后多少"。
          两者会在同一个场景下给出相反答案：程序开着不关、自动刷新每 30 秒
          都在写文件（mtime 永远新鲜），但若数据源那几分钟恰好没返回新分钟，
          mtime 是新的、时间轴却停在原地 —— 此时该补数据，而不是"文件很新就别管"。
          反过来，午休/盘后 mtime 会一直变旧，但时间轴并没有"落后"
          （休市本就无新数据）—— 故缺口口径里把休市时段夹到边界
          （见 flowdata.spot_gap_minutes）。
        """
        try:
            import flowdata                      # 局部导入（本文件刻意不模块级依赖三方库）
            p = os.path.join(data_dir, f"panel_{self.tag}.json")
            with open(p, encoding="utf-8") as f:
                meta = json.load(f)
            times = meta.get("times") or []
            tail = flowdata.spot_gap_minutes(times)
            # ★★ v3.0.6：**快速先行版留下的空洞必须能被识别出来**，
            #   否则它会被永久留在面板里（曲线一直是断的）。
            #
            #   【为什么不能用"时间轴内部跳跃"来判定】——这里踩过一次，务必记住：
            #     先行版补的占位行用的是**真实交易分钟标签**
            #     （11:31 之后接着 13:01…14:33），所以时间轴在"分钟序号"上
            #     **是完全连续的**，`panel_internal_gap_min` 会算出 0。
            #     空洞实际体现在**数值整行为 NaN**，而不是标签缺失。
            #     更糟的是：先行版把"当前分钟"补到了尾部，于是**尾部缺口也变成 0**
            #     —— 只看尾部会判定"面板很新"→ 永远不再走全量补齐。
            #   所以这里直接读 `partial_history` 标记（由
            #   `refresh_panel_spot(allow_gap=True)` 写入），用 gap_from/gap_to
            #   换算出真实缺失的分钟数。全量重抓成功后 build_panel 会写一份
            #   全新的 meta（不含该标记），判定自然复位。
            hole = 0
            if meta.get("partial_history"):
                a = flowdata._minute_idx(meta.get("gap_from"))
                b = flowdata._minute_idx(meta.get("gap_to"))
                if a is not None and b is not None and b > a:
                    hole = b - a - 1
                else:
                    # 认不出具体范围时，给一个"必须补齐"的量（> SPOT_MAX_GAP_MIN）
                    hole = 7
            # 内部跳跃也一并考虑：历史面板（旧版本写入 / 数据源缺口）可能
            # 真的缺标签，那种情况由 panel_internal_gap_min 兜住。
            inner = flowdata.panel_internal_gap_min(times)
            best = max(hole, inner)
            if tail is None:
                return best
            return max(tail, best)
        except Exception:                                # noqa: BLE001
            return None

    @staticmethod
    def _latest_local_panel(data_dir):
        """本地已有的最新一个**交易日**面板 tag（YYYYMMDD）；没有则 None。

        ★★ v2.0.2 新增。用途：今天抓不到数据时，把本地最近一天摆出来 ——
          这是"断网也该能看到上周五/这周一"的落点。
        只认 panel_YYYYMMDD.npy 这种严格日期名；panel_today.* 这类
        历史命名不参与（它们由 _migrate_legacy_cache 处理）。
        """
        best = None
        try:
            for f in os.listdir(data_dir):
                if not (f.startswith("panel_") and f.endswith(".npy")):
                    continue
                tag = f[len("panel_"):-len(".npy")]
                if len(tag) != 8 or not tag.isdigit():
                    continue
                if best is None or tag > best:
                    best = tag
        except Exception:                                # noqa: BLE001
            return None
        return best

    def _plan_fetch(self, data_dir, panel_path):
        """决定本次刷新走哪条通道 —— 返回 `(plan, why)`。

        plan ∈ {"full", "spot", "cache"}：
          full  → 全量重抓（约 1000 次请求 / 2~10 秒）
          spot  → 批量快照增量刷新（5~6 次请求 / 约 0.35 秒）
          cache → 直接读本地已有的面板文件（不联网）

        ★★ v2.0.1：把这段判定从 run() 里抽出来单独成方法，因为它现在是
          "点刷新就能看到最新"这个用户承诺的**唯一实现点**，必须能被
          单独测试（见 tools/verify_v201.py 的问题二断言）。
          原先它散在 if/elif/else 三个分支里，既测不了、也看不出全貌。

        判定顺序（自上而下，命中即止）：
          ① 没有底表            → full（增量只能续在已有面板尾部）
          ② force               → full（内部"重建"语义；界面入口已移除）
          ③ 非交易时段          → cache（盘中无新数据，白抓一次没意义）
          ④ 缓存刚更新过(<3s)   → cache（吸收连点，避免重复请求）
          ⑤ 时间轴缺口 > 阈值   → full（增量补不回中间断掉的分钟）
          ⑥ 其余                → spot（取最新）
        """
        try:
            import flowdata
        except Exception:                                # noqa: BLE001
            flowdata = None
        if not os.path.exists(panel_path):
            # ★★ v2.0.2：今天没有底表时，先看本地有没有**更早交易日**的面板。
            #   旧逻辑到这里直接 full fetch → 断网/限流时必然失败 →
            #   界面清空并弹「数据加载失败」，而本地明明躺着 0918/0921 的
            #   完整全天数据。用户遇到的就是这个：非交易时段打开、接口被限流，
            #   结果连本地历史都看不到。
            #   判定：非盘中，或今天本就不是交易日 → 直接复用最近一天；
            #        盘中且今天是交易日 → 仍去抓今天的（抓不到再由 run() 回退）。
            fb = self._latest_local_panel(data_dir)
            #   ★ 判据只用 _in_trading_hours()，**不要**再要求"今天在交易日历里":
            #     日历取自日K接口，只含**已收盘**的交易日 —— 盘中它天然不含今天,
            #     加这个条件会让"盘中该去抓今天的"永远不成立，直接退化成
            #     盘中也在看昨天的数据（实测踩到）。
            #     _in_trading_hours 已经排除了周末；节假日例外由 run() 的
            #     抓取失败回退兜住 —— 抓不到就用本地的，不会空屏。
            if fb and fb != self.tag and not self._in_trading_hours():
                return "local", f"今天无数据，复用本地最近数据 {fb}"
            return "full", "无本地底表（首次建表）"
        if self.force:
            return "full", "force 指定全量重建"
        if not self._in_trading_hours():
            return "cache", "非交易时段，读本地数据"
        age = self._panel_age_sec(data_dir)
        if age is not None and age < REFRESH_MIN_GAP_SEC:
            return "cache", f"缓存 {age:.1f}s 前刚更新"
        gap = self._panel_gap_min(data_dir)
        limit = getattr(flowdata, "SPOT_MAX_GAP_MIN", 6) if flowdata else 6
        if gap is not None and gap > limit:
            return "full", f"时间轴缺口 {gap} 分钟"
        return "spot", "增量刷新取最新"

    @staticmethod
    def _in_trading_hours():
        """是否允许请求实时数据（统一委托 tradingtime，避免午休/节假日分叉）。"""
        now = datetime.now()
        try:
            import tradingtime
            session = tradingtime.classify(now)
            return bool(session.is_trading_day and
                        (session.tradable or session.auto_refresh))
        except Exception:                                # noqa: BLE001
            # 核心时段模块不可用时保留最小兜底，至少不放行周末。
            if now.weekday() >= 5:
                return False
            hm = now.hour * 100 + now.minute
            return 915 <= hm <= 1505

    def _migrate_legacy_cache(self, data_dir):
        """把旧版固定名缓存（panel_today.*）迁移为今天的日期名，仅限今天抓的。"""
        p = os.path.join(data_dir, f"panel_{self.tag}.npy")
        if os.path.exists(p):
            return
        legacy_npy = os.path.join(data_dir, "panel_today.npy")
        if not os.path.exists(legacy_npy):
            return
        try:
            same_day = datetime.fromtimestamp(
                os.path.getmtime(legacy_npy)).date() == datetime.now().date()
        except Exception:                                # noqa: BLE001
            same_day = False
        if not same_day:
            return
        for a, b in ((legacy_npy, p),
                     (os.path.join(data_dir, "panel_today.json"),
                      os.path.join(data_dir, f"panel_{self.tag}.json")),
                     (os.path.join(data_dir, "index_today.json"),
                      os.path.join(data_dir, f"index_{self.tag}.json"))):
            if os.path.exists(a):
                try:
                    os.replace(a, b)
                except Exception:                        # noqa: BLE001
                    pass

    @staticmethod
    def _purge_old_cache(data_dir, keep_days=10):
        """删除 keep_days 天前的旧日缓存（每天约 2~3 MB，不清理会无限累积）。"""
        try:
            cutoff = time.time() - keep_days * 86400
            for f in os.listdir(data_dir):
                if not f.startswith(("panel_", "index_")):
                    continue
                if not f.endswith((".npy", ".json")):
                    continue
                fp = os.path.join(data_dir, f)
                try:
                    if os.path.getmtime(fp) < cutoff:
                        os.remove(fp)
                except Exception:                        # noqa: BLE001
                    pass
        except Exception:                                # noqa: BLE001
            pass

    def _gather(self, data_dir, grow_pool=True):
        """从当前面板文件构建整套界面 payload（v2.0.3 自 run() 抽出）。

        抽出来的原因：两段式首屏需要**同一套构建逻辑跑两遍** ——
          · 先行版（全量补抓开始前）：grow_pool=False，读现有面板出图；
          · 最终版（补抓完成后）：grow_pool=True，正常生长榜单池。
        只把「特征计算 → 榜单 → 事件 → 时间轴 → 叙述」整条纯计算链放这里；
        网络抓取与缓存维护仍留在 run()（它们只该发生一次）。

        grow_pool=False 时榜单池**只读不写**：pool.update()/flush() 都不调，
        否则 early/final 两轮会把 rounds 虚增一轮、is_new 高亮被提前消耗。
        """
        import salience
        import flowdata

        self.progress.emit("计算特征与辨识度…")
        F = salience.build_features(self.tag)
        last = len(F.times) - 1

        out = {"F": F, "last": last, "times": list(F.times),
               "date_tag": self.tag,
               "view_mode": "history" if self.local_only else "live"}

        # ★ 数据来源（live/backfill）与交易日历（v1.1.0）：
        #   界面据此标注「实盘 / 合成」，并填充回看日期下拉框。
        try:
            import store
            out["source"] = store.panel_source(self.tag, data_dir) or "live"
        except Exception:                            # noqa: BLE001
            out["source"] = "live"
        out["calendar_days"] = self._cal_days
        out["calendar_approx"] = self._cal_approx

        # ★ 东财全A 分时（v1.1.5）：与上证并列展示，覆盖沪深全市场。
        #   旧面板/合成日没有这个文件 → 返回 []，界面按"缺"降级。
        try:
            out["index_all"] = flowdata.load_index_all(self.tag)
        except Exception:                            # noqa: BLE001
            out["index_all"] = []

        self.progress.emit("生成榜单…")
        out["board"] = salience.score_table(F, last, topn=30)
        out["board_out"] = salience.score_table(F, last, topn=30, direction="out")

        # ★ 实时榜单（累积池 · v1.1.7 用户需求）：
        #   「开盘时按辨识度筛约 15 个入榜；盘中检测到盘口异动立即追加；
        #     已入选的全天保留，只增不减。」
        #   —— 池子在后台线程里生长（Worker 每轮跑一次），因为要读盘/写盘；
        #      写盘是原子的，UI 线程只负责把 view() 的结果铺进表格。
        #   ★ 只在**实盘视图**上生长：历史回看的是已经定稿的一天，
        #     不该再往里加东西（而且那种"边看历史边长榜"的语义也说不通）。
        #     历史日直接读当天落盘的池子快照 —— 它就是那天真实长出来的样子。
        try:
            import watchlist as _wl
            pool = _wl.Pool(self.tag, data_dir)
            n_new = 0                      # 本轮新进条数（仅实盘有意义）
            if self.local_only:
                # 历史回看：不生长，只把当天存档读出来展示
                out["pool_note"] = ("历史日 · 读取当日收盘快照"
                                    if pool.rows else "历史日 · 当日无榜单存档")
            elif not grow_pool:
                # ★★ v2.0.3 两段式首屏：先行版**只读不写** —— 榜单生长每轮
                #   只许发生一次，early/final 两轮都 update() 会把 rounds
                #   虚增一轮、is_new 高亮也被提前消耗掉。
                out["pool_note"] = "正在补齐缺失分钟，榜单稍后更新…"
            else:
                self.progress.emit(
                    f"更新实时榜单（{'开盘入榜' if not pool.rows else '检测异动'}）…")
                n_new, n_all = pool.update(
                    F, last, salience_mod=salience,
                    top_n=WATCH_TOP_N, kind="concept")
                out["pool_note"] = (f"本轮新增 {n_new} 个 · 累计 {n_all} 个"
                                    f"（第 {pool.rounds} 轮）")
                log_line(f"watchlist {self.tag}: +{n_new} -> {n_all} "
                         f"round={pool.rounds}")
            # ★★ 顺序很关键（v1.2.1 修复）：**先 view() 再 flush()**。
            #   flush() 会把所有 is_new 清成 False（语义：本轮高亮一次，
            #   下一轮不再亮）。旧代码先 flush 再 view →
            #   view() 读到的 is_new 恒为 False →
            #   「实时榜单」页的「▲ 新进」前缀与强调色**永远不会出现**。
            #   这个 bug 不报错、不崩，只是那个功能静默失效，很难察觉。
            #   正确顺序：update(标记新进) → view(取走高亮标记) → flush(清零+落盘)。
            out["watch"] = pool.view()
            out["watch_stats"] = pool.stats()
            out["watch_rounds"] = pool.rounds
            out["watch_updated"] = pool.updated
            out["watch_new_n"] = int(n_new) if not self.local_only else 0
            if not self.local_only and grow_pool:
                pool.flush()
            # 基础榜（不生长）—— 与累积池并排展示，一个看"当下"、一个看"全天"
            out["watch_base_in"] = salience.score_table(
                F, last, topn=WATCH_BASE_N, kind="concept", direction="in")
            out["watch_base_out"] = salience.score_table(
                F, last, topn=WATCH_BASE_N, kind="concept", direction="out")
            # ★★ v2.0.1：再补两张**按 5 分钟增量**排的榜（用户要求
            #   「实时榜单页下半部分共四个模块」）。
            #   口径差异见 salience.velocity_table 的 docstring：
            #   上面两张回答"今天谁最强"，下面两张回答"此刻钱在往哪去"。
            #   四个榜共用 WATCH_BASE_N 条数，视觉上对称、也便于横向比对。
            out["watch_d5_in"] = salience.velocity_table(
                F, last, topn=WATCH_BASE_N, kind="concept", direction="in")
            out["watch_d5_out"] = salience.velocity_table(
                F, last, topn=WATCH_BASE_N, kind="concept", direction="out")
            # ★★ v2.0.1：异动雷达（问题十的可见成果）。
            #   它与上面几个榜的**口径差异**见 salience.anomaly_radar：
            #   辨识度榜偏"体量"，5 分钟榜偏"速度"，雷达偏"相对自身异常"。
            #   三者并列，细分题材（光纤/电子布/被动元件…）才有机会被看到 ——
            #   这也是"不许套用名单"的实现方式：全部指标都来自实时数据。
            out["radar_in"] = salience.anomaly_radar(
                F, last, topn=RADAR_N, direction="in")
            out["radar_out"] = salience.anomaly_radar(
                F, last, topn=RADAR_N, direction="out")
        except Exception as e:                       # noqa: BLE001
            out["watch"] = []
            out["watch_stats"] = (0, 0, 0, 0)
            out["watch_err"] = f"{type(e).__name__}: {e}"
            log_line(f"watchlist failed: {e}")

        # ★ 一级行业只有 31 个，之前只取 top10，两张表各留出 40% 的空白。
        #   全量取出来后表格自然铺满，也不用滚动。
        out["industry"] = salience.industry_overview(F, last, topn=31)

        # ★ 全市场汇总：总览 KPI 卡与流入/流出对比图的取数来源。
        #   概念层与行业层混在一起统计会有重复计入，这里只统计概念层，
        #   与榜单口径保持一致（榜单也是以概念为主）。
        # ★★ v2.0.1：快照龙头（领涨股）—— 覆盖**全部板块**、
        #   随每次刷新更新，是「实时榜单 → 龙头选手」列的主来源。
        #   与 leader 分析（拐点上先动手的那只，只覆盖少量在榜板块）
        #   互为补充：这里先铺底，leader 结果到了再覆盖更精确的值。
        try:
            out["spot_leaders"] = flowdata.load_spot_leaders(self.tag)
        except Exception as e:                       # noqa: BLE001
            out["spot_leaders"] = {}
            log_line(f"spot leaders load skipped: {e}")

        self.progress.emit("汇总全市场资金…")
        try:
            out["market"] = _market_stats(F, salience, last)
        except Exception as e:                       # noqa: BLE001
            out["market"] = {}
            out["market_err"] = str(e)

        self.progress.emit("检测事件…")
        # ★ 原来只跑「收盘那一刻」的 detect_events，实测只得 4 条，
        #   表格空出 60% 高度。改成扫全天事件流，表格才填得满。
        out["events"] = _event_stream(F, salience)

        self.progress.emit("聚合题材簇…")
        try:
            # ★ v1.1.8：topn 语义已改为「**每侧**条数」（不再是两侧合计）。
            #   传 8 → 流入 8 条 + 流出 8 条 = 共 16 条，用户要的
            #   「红 8 绿 8 对称」由此一次到位。
            #
            #   顺带修掉一个真实缺陷：旧版 topn=12 走的是"按配额分配"
            #   （out_ratio=0.34 → 流出配额 round(12*0.34)=4），把流出侧
            #   **硬截断成 4 条**。而当天负向概念板块有 91 个、能聚出 8 个
            #   负簇 —— 用户看到的"流出只有 4 条"是配额砍的，不是市场只有
            #   4 条。现在两侧各自取满，不再互相挤占。
            th = salience.cluster_themes(F, last, topn=8)
            out["themes"] = th
            out["n_theme_in"] = sum(1 for c in th
                                    if float(c.get("total_yi", 0) or 0) > 0)
            out["n_theme_out"] = len(th) - out["n_theme_in"]
        except Exception as e:
            out["themes"] = []
            out["themes_err"] = str(e)

        self.progress.emit("生成时间轴…")
        out["timeline"] = salience.timeline_summary(F)

        self.progress.emit("计算主角更替与阶段对比…")
        out["leaders"] = _leader_timeline(F, salience)
        out["stages"] = _stage_snapshot(F, salience)

        self.progress.emit("生成文字叙述…")
        try:
            out["narrate"] = salience.narrate(F, last)
        except Exception:
            out["narrate"] = ""
        try:
            out["session"] = salience.session_report(F)
        except Exception:
            out["session"] = ""

        out["n_sectors"] = len(F.codes)
        out["n_minutes"] = len(F.times)
        return out

    def run(self):
        try:
            # 图表后端只能在 GUI 线程初始化；Worker 只做数据处理，避免 QtAgg
            # backend 在后台线程导入导致偶发的跨线程 GUI 初始化问题。
            import numpy as np
            import salience
            import flowdata

            # ★ 缓存目录统一重定向到用户目录（见 ensure_user_data_dir 的说明）。
            #   注意：salience 里是 `from flowdata import DATA, load_panel`，
            #   它拿到的是导入那一刻的**值拷贝**，所以必须在 import salience
            #   之前就改好 flowdata.DATA，否则缓存会写到只读的 _MEIPASS。
            #   v1.1.5：改为"仅在仍是默认值时才重定向"，避免覆盖调用方指定的目录。
            ensure_user_data_dir()

            data_dir = flowdata.DATA
            p = os.path.join(data_dir, f"panel_{self.tag}.npy")

            if self.local_only:
                # ---- 历史回看：纯本地读取（v1.1.0）
                if not os.path.exists(p):
                    raise RuntimeError(
                        f"{self.tag} 的本地数据不存在：可能尚未补全，"
                        f"或已被 30 日窗口自动清理。")
                self.progress.emit("读取本地历史数据…")
            else:
                # ---- 实盘加载：迁移旧缓存 → 交易日历 → 窗口清理 → 抓取
                self._migrate_legacy_cache(data_dir)
                try:
                    import tradecal
                    import store
                    # ★★ v2.0.2 启动提速（需求二）：非交易时段绝不联网取日历。
                    #   用户反馈"启动比第一版慢约 11 秒"，最大单一来源就是这里
                    #   —— 网络不畅时 daykline 请求要干等 10 秒超时。
                    #   非交易时段不会有新数据写入、30 日窗口不会越界，purge
                    #   无必要；日历用本地缓存（供回看下拉框）即可。
                    #   若缓存过期 → tradecal 标记 trusted=False，下方据此
                    #   跳过 purge，数据安全不受影响；若缓存是今天写的，
                    #   purge 照常执行（可信）。
                    days, approx = tradecal.get_trading_days(
                        refresh=self._in_trading_hours())
                    self._cal_days, self._cal_approx = days, approx
                    # ★★ v2.0.2：还要问一句"这份日历可不可信"。
                    #   approx 只覆盖"工作日推断"一种情形；而**联网失败 →
                    #   回退过期盘缓存**同样危险，且它 approx=False 会蒙混过关。
                    #   2026-09-22 的事故就是这么发生的：9/21 盘中写下的日历
                    #   缺 9/21 那一格，次日凌晨 purge 把 9/21 全天数据删了。
                    cal = tradecal.last_trusted()
                    if approx:
                        # 日历只是工作日推断时**不清理**（节假日会被误判成
                        # 非交易日，可能在窗口边界误删真实数据），只记日志。
                        log_line("calendar approx (weekday fallback); purge skipped")
                    elif not cal.get("trusted"):
                        # ★★ v2.0.2：日历来源不可信 → 一律不清理。
                        #   宁可让窗口多留几天数据（占几十 MB），
                        #   也绝不冒"把真实交易日当窗口外删掉"的风险。
                        log_line("calendar untrusted (src=%s); purge skipped "
                                 "to protect local data" % cal.get("src"))
                    else:
                        # ★ 30 交易日窗口（v1.1.0 用户需求）：
                        #   始终只保留最近 30 个交易日 + 今天；
                        #   写入第 31 天的数据后，最早且超出窗口的那天自动删除。
                        keep = set(days[-30:]) | {self.tag}
                        doomed = store.purge_beyond_window(keep, data_dir)
                        if doomed:
                            log_line(f"purge beyond 30-trading-day window: {doomed}")
                        # ★ 归档索引同步裁剪（v1.1.5）：面板被清掉后，
                        #   归档里不该再留着它的记录 —— 否则回看列表会出现
                        #   "有记录却打不开"的条目。保持两套窗口严格一致。
                        try:
                            import archive as _arc
                            dropped = _arc.prune_to(keep, data_dir)
                            if dropped:
                                log_line(f"archive pruned: {dropped}")
                        except Exception as e2:           # noqa: BLE001
                            log_line(f"archive prune skipped: {e2}")
                        # ★ 实时榜单池同窗口裁剪（v1.1.7）：每天的
                        #   watchlist_YYYYMMDD.json 也要跟着 30 日窗口走，
                        #   否则回看窗口清掉面板后，池子文件会无限累积。
                        try:
                            import watchlist as _wl
                            wdropped = _wl.purge_beyond_window(keep, data_dir)
                            if wdropped:
                                log_line(f"watchlist pruned: {wdropped}")
                        except Exception as e3:           # noqa: BLE001
                            log_line(f"watchlist prune skipped: {e3}")
                except Exception as e:                   # noqa: BLE001
                    log_line(f"calendar/purge skipped: {e}")

                # ★ 周末启动且最近交易日的面板已在本地 → 直接复用（v1.1.0）：
                #   分钟接口只返回最近交易日数据，周末重抓只是浪费流量；
                #   抓到后也会按真实数据日期归档（见下方 rename 逻辑）。
                if (not self.force and datetime.now().weekday() >= 5
                        and self._cal_days):
                    last_td = self._cal_days[-1]
                    if last_td < self.tag and os.path.exists(
                            os.path.join(data_dir, f"panel_{last_td}.npy")):
                        log_line(f"weekend launch: reuse {last_td} panel "
                                 f"instead of fetching under {self.tag}")
                        self.tag = last_td
                        p = os.path.join(data_dir, f"panel_{self.tag}.npy")

                # ----------------------------------------------------------
                # ★★ v2.0.1：抓取通道统一为**一棵决策树**（抽成 _plan_fetch，
                #   便于单测；原先是 if/elif/else 三分支各写一遍判定）。
                #
                #   目标（把用户两项要求合并成一个动作）：
                #     · 「刷新后要能直接看到最新数据」—— 交易时段内任何一次
                #       刷新（启动 / F5 / 自动刷新）都真的去取当前这一分钟；
                #     · 「降低盘中读取延迟、提高刷新频率」—— 取数走**增量通道**
                #       （批量快照，5~6 次请求 / 约 0.35 秒），不是全量 1000 次请求。
                plan, why = self._plan_fetch(data_dir, p)
                # ★★ v2.0.3 两段式首屏：全量补抓**开始前**，本地面板里已有的
                #   数据先发一版上屏 —— 它虽缺最近几分钟，但此前的每一分钟
                #   都是真的。旧版直接进 1000 次请求的全量抓取，用户要干等
                #   2~10 秒才见到第一张图（v2.0.2b 实测 since_boot=6861ms）。
                #   --shot 自检跳过：截图必须等**最终**数据才可复现。
                if (plan == "full" and os.path.exists(p)
                        and "--shot" not in sys.argv):
                    try:
                        _t0e = time.perf_counter()
                        _early = self._gather(data_dir, grow_pool=False)
                        _early["_early"] = True
                        self.early.emit(_early)
                        log_line(f"early paint · stale panel shown in "
                                 f"{(time.perf_counter() - _t0e) * 1000:.0f}ms"
                                 f"（先行版 · 全量补抓随后：{why}）")
                    except Exception as e:                   # noqa: BLE001
                        log_line(f"early paint skipped: "
                                 f"{type(e).__name__}: {e}")

                # ----------------------------------------------------------
                # ★★ v3.0.6：**快速先行版（live-lite）** —— 用户反馈
                #   「我等了两分钟，到现在没有加载出来」。
                #
                #   【实测病因】日志显示窗口 2.9s 就出来了、旧面板 109ms 就上屏，
                #   但 **全量补抓跑了 190 秒**（14:34:42 → 14:37:54）：
                #     14:34:42 early paint · stale panel（时间轴缺口 94 分钟）
                #     14:34:44 loaded ok · sectors=426 minutes=120   ← 只有上午
                #     14:37:54 loaded ok · minutes=218 times=09:31~14:38 ← 才补全
                #   这 190 秒里界面上**只有上午 11:30 之前的旧数据**，体感就是
                #   "没加载出来"。而用户真正想先看到的"现在这一刻的资金流"，
                #   其实只要 5~6 次请求 / 约 0.3 秒就能拿到 ——
                #   `refresh_panel_spot` 用 ulist.np 一次就能取回 200 个板块的
                #   **当前累计净额**，1000 个板块总共也就 5 个分片。
                #
                #   【为什么以前不做】增量通道有个缺口守卫：面板落后超过
                #   SPOT_MAX_GAP_MIN(6) 分钟就拒绝追加 —— 因为旧口径下"把当前
                #   分钟直接接在 11:30 后面"会让渲染层（x 轴是**真实分钟序号**）
                #   画出一条横跨 63 格的直线，等于编造 94 分钟走势。
                #   本版把这件事做对了：`allow_gap=True` 会把中间缺失的分钟
                #   **显式补成 NaN 占位行**，再配合 `_fill_gaps` 的长缺口守卫
                #   （>6 分钟不插值）→ 曲线在那一段**如实断开**，
                #   既给了用户当前真实数据，又没有伪造任何走势。
                #
                #   【代价】约 0.3~1 秒 + 5~6 次请求。相对省下的 190 秒等待
                #   可以忽略；即使这一步失败也只是回到原行为（continue）。
                if (plan == "full" and os.path.exists(p)
                        and "--shot" not in sys.argv):
                    try:
                        _t0l = time.perf_counter()
                        _ml, _al = flowdata.refresh_panel_spot(
                            self.tag, allow_gap=True)
                        if _ml is not None and _al is not None:
                            _live = self._gather(data_dir, grow_pool=False)
                            _live["_early"] = True
                            # ★ 与"旧面板先行版"区分开：这一版**已经含当前分钟**
                            #   的真实值，只是中间缺口还没补 —— 状态栏文案不同，
                            #   否则用户会以为看到的还是旧数据。
                            _live["_live_only"] = True
                            self.early.emit(_live)
                            log_line(
                                f"live-lite paint · 当前分钟已补上 in "
                                f"{(time.perf_counter() - _t0l) * 1000:.0f}ms"
                                f"（{_al.shape[0]}×{_al.shape[1]}；"
                                f"历史缺口 {_ml.get('gap_from')}~"
                                f"{_ml.get('gap_to')} 后台补齐中）")
                        else:
                            log_line("live-lite paint skipped（增量快照未取到）")
                    except Exception as e:                   # noqa: BLE001
                        log_line(f"live-lite paint failed: "
                                 f"{type(e).__name__}: {e}")

                if plan == "full":
                    log_line(f"full fetch · {why}")
                    self.progress.emit("抓取全量分钟数据（约 1000 个板块）…")
                    try:
                        # ★★ v3.0.6 延迟修复：这里原先是 `workers=24`
                        #   —— 固定 24 并发打 1000 次请求，正是 push2 最敏感
                        #   的突发形态，实测会把两个域名成片推进冷却，
                        #   总耗时反而暴涨（2m42s 只到 800/1000）。
                        #   现在 workers 只作**硬上限**，真实并发由
                        #   `flowdata._NET_GATE` 按失败率 AIMD 自适应
                        #   （起点 8，区间 [3, 16]）；同时加了 180s 全局时限，
                        #   到点即用已得数据建表，不再无界等待。
                        flowdata.build_panel(
                            self.tag, workers=flowdata.NET_CONCURRENCY_MAX,
                            on_progress=self.progress.emit)
                    except Exception as e:               # noqa: BLE001
                        # ★★ v2.0.2：全量抓取失败不再直接把界面清空报错。
                        #   本地只要还留着任何一个交易日的数据，就先摆出来 ——
                        #   用户的原话：「哪怕都拿不到，应该也能拿到上周五和
                        #   这周一的本地数据啊」。只有本地**一片空白**时才抛错。
                        fb = self._latest_local_panel(data_dir)
                        if not fb:
                            raise
                        log_line("full fetch failed (%s: %s); "
                                 "fallback to local %s"
                                 % (type(e).__name__, e, fb))
                        self.tag = fb
                        p = os.path.join(data_dir, f"panel_{self.tag}.npy")
                        plan, why = "local", f"抓取失败，回退本地 {fb}"
                        # ★ 回退到历史日 → 必须按"历史回看"语义走，
                        #   否则实时榜单池会把**今天**检测到的异动写进
                        #   9/21 的池子存档里，污染那天定稿的数据。
                        self.local_only = True
                        self.progress.emit(
                            f"实时接口不可用，改用本地 {fb} 的完整数据…")
                    if plan == "full":
                        self._archive_by_data_date(data_dir)
                        p = os.path.join(data_dir, f"panel_{self.tag}.npy")
                        # ★★ v2.0.1：建表后顺手补一次「龙头快照」（5~6 次请求 /
                        #   约 0.35 秒）。不补的话，界面的「龙头选手」列要等到
                        #   下一次增量刷新才有内容 —— 首次使用时那一列空着最扎眼。
                        #   失败无妨：它只是附加信息，下一次刷新还会再取。
                        try:
                            n_ld = flowdata.refresh_spot_leaders(self.tag)
                            if n_ld:
                                log_line(f"spot leaders after full build: {n_ld}")
                        except Exception as e:           # noqa: BLE001
                            log_line(f"spot leaders after full build skipped: {e}")
                elif plan == "spot":
                    # 增量刷新（约 0.35 秒）。
                    #
                    # ★★ v2.0.1：失败**不再自动降级为全量**。
                    #   旧行为是"增量失败 → 全量 1000 次请求"。在自动刷新
                    #   30 秒一轮的前提下，数据源一旦抖动就会退化成
                    #   "每 30 秒发 1000 次请求"的风暴 —— 既慢又必然被限流，
                    #   而且用户完全不知道发生了什么。
                    #   现在：失败就沿用现有面板（它也是有效数据），下一轮
                    #   自会重试。真正必须全量的情况只有"没有底表"，
                    #   那在判定 ① 就已经拦住了。
                    self.progress.emit("增量刷新（批量快照）…")
                    try:
                        m2, a2 = flowdata.refresh_panel_spot(
                            self.tag, on_progress=self.progress.emit)
                        if m2 is not None and a2 is not None:
                            log_line(f"spot refresh ok · "
                                     f"{a2.shape[0]}×{a2.shape[1]} · tag={self.tag}")
                        else:
                            log_line("spot refresh returned nothing; keep panel")
                            self.progress.emit("本次无新增分钟，沿用现有数据…")
                    except Exception as e:               # noqa: BLE001
                        log_line(f"spot refresh failed (kept panel): {e}")
                        self.progress.emit("增量刷新未成功，沿用现有数据…")
                else:
                    if plan == "local":
                        # ★★ v2.0.2：今天没有可用数据 → 用本地最近交易日兜底。
                        #   ★ 必须把 self.tag 一并切过去（2026-09-22 凌晨自检
                        #     抓到的漏改：只切了 local_only，build_features 仍在
                        #     找 panel_{今天}.json → 直接 FileNotFoundError）。
                        #   按"历史回看"语义处理：只读、不生长实时榜单池。
                        fb = self._latest_local_panel(data_dir)
                        if fb:
                            self.tag = fb
                            p = os.path.join(data_dir, f"panel_{self.tag}.npy")
                        self.local_only = True
                        log_line(f"use local fallback · {why}")
                        self.progress.emit(
                            f"使用本地 {self.tag} 的数据（非今日实时）…")
                    else:
                        log_line(f"use local cache · {why}")
                        self.progress.emit("读取本地缓存…")

            out = self._gather(data_dir, grow_pool=True)

            self.done.emit(out)
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")


class OpenSnapWorker(QThread):
    """开盘检测定点快照线程（v2.0.4）。

    抓一个节点的全市场快照（~70 页请求 / 6 并发 / 实测 2~4 秒），
    网络密集，与 LeaderWorker 同理必须独立线程。
    done(key, tag, node) 由 MainWindow 落盘并刷新两页。
    """

    progress = Signal(str)
    done = Signal(str, str, dict)
    failed = Signal(str, str)

    def __init__(self, minutes, key, tag, trigger=None):
        super().__init__()
        self.minutes = minutes
        self.key = key
        self.tag = tag
        # ★ v3.0.2：计划触发时刻（"09:36" 等）。capture_node 用它判定本次是
        #   "准点采集"还是"错过后的补采"，结果写进 node["on_time"]，界面据此标注。
        self.trigger = trigger

    def run(self):
        try:
            import opensnap
            node = opensnap.capture_node(self.minutes, self.trigger)
            if not node.get("sectors") and not node.get("stocks"):
                self.failed.emit(self.key, "快照为空（接口无响应或被限流）")
                return
            self.done.emit(self.key, self.tag, node)
        except Exception as e:                           # noqa: BLE001
            self.failed.emit(self.key, f"{type(e).__name__}: {e}")


class LeaderWorker(QThread):
    """龙头领先反应分析（v1.1.5）：抓成分股 + 个股分钟资金流 → 打分。

    单独线程的原因同 BackfillWorker：这是**网络密集**任务
    （每板块 8~16 只候选 × 多个板块，即使限速也要十几秒），
    绝不能卡住界面；关窗时也需要被 closeEvent 正确等待。

    ★ 只在**实盘日**有意义：个股分钟资金流接口同样只服务最近交易日，
      历史合成日的分钟曲线本就是合成的，拿它算"龙头谁先动手"没有意义。
      所以调用方只在 source == "live" 且未回看时触发。
    """
    progress = Signal(str)
    done = Signal(list)
    failed = Signal(str)

    def __init__(self, payload):
        super().__init__()
        self.payload = payload          # dict：面板轴/代码/名称/矩阵/在榜板块

    def run(self):
        try:
            import flowdata
            import leader
            ensure_user_data_dir()
            p = self.payload
            rows = leader.analyze_boards(
                p["times"], p["codes"], p["names"], p["V"], p["boards"],
                on_progress=self.progress.emit)
            self.done.emit(rows)
        except Exception as e:                            # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()[-400:]}")


class BackfillWorker(QThread):
    """历史数据补全（v1.1.0）：日级真实总额 + 分钟形态合成。

    与 Worker 分离的原因：补全是分钟级耗时的后台任务（约 1000 个板块的
    日级数据 + 限流退避），绝不能阻塞实盘加载；且补全中途关窗也要能
    被 closeEvent 正确等待。
    """
    progress = Signal(str)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, days):
        super().__init__()
        self.days = list(days)

    def run(self):
        try:
            import flowdata
            import backfill
            # 与 Worker 同理：先把数据目录重定向到用户可写目录
            ensure_user_data_dir()
            stats = backfill.run_backfill(
                self.days, data_dir=flowdata.DATA,
                on_progress=self.progress.emit)
            self.done.emit(stats)
        except Exception as e:                            # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()[-400:]}")


def _event_stream(F, salience, step=3, per_slot=2, top_n=140):
    """全天事件流 —— 「实时事件」页的取数来源。

    ★ 为什么不用 salience.detect_events(F, last)：
      那是「只看收盘那一刻」的事件快照，实测全天只剩 4 条（且全是排名跃迁），
      表格下面空出 60% 的高度。
    ★ 为什么不用 salience.timeline_summary：
      它每个时点只保留 strength 最大的 1 条，79 条里 56 条是
      「排名跃迁」，信息太单一。
      这里扫描全天、每个时点最多保留 per_slot 条（按强度取），
      既填满了表格，事件类型也丰富得多。

    ★★ v2.0.1：排序口径改为「时间**降序**（晚→早）」。
      用户明确要求：「将实时事件模块排序改为从晚到早（如 9:50 采集的事件
      显示在 10:20 采集的事件下方）」。

      改动要点（不只是把 sort 取反，否则会丢掉全天的时序覆盖）：
        · 排序键：`(时间降序, 强度降序)` 两层。同一时刻内仍然是"越劲爆越靠前"，
          所以读取顺序是"先看最新、再看最新的哪件最要紧"。
        · 数量控制：**原先按强度取前 60 条**，直接配合降序会得到
          "强度前 60 名里再按时间倒着排"—— 早盘的强事件被挤出榜单，
          用户往前滚动时会发现早上那块凭空消失了。
          现在改为**按密度控制**：每个时点最多 per_slot 条，
          于是全天每个时段都在表里、条数上限只作安全阀。
          实测全天约 75~95 条，表格一屏滚得完，又不至于单调。
        · 时间解析失败的行（脏数据）沉底，不会被当成"最早的"挤到最上面。
    """
    out = []
    T = len(F.times)
    win = getattr(salience, "VEL_WIN", 3)
    tk = getattr(salience, "time_key", None)
    for i in range(win, T, step):
        try:
            evs = salience.detect_events(F, i)
        except Exception:                                # noqa: BLE001
            continue
        picked = [e for e in evs if isinstance(e, dict)]
        # 同一时点按强度取前 per_slot 条（原实现全留，事件密集时会刷屏）
        picked.sort(key=lambda e: -float(e.get("strength", 0) or 0))
        # ★★ v2.0.1：**按类型去重**后再取数。
        #
        #   为什么需要：detect_events 现在一次会返回几十条（候选池从
        #   "前 80 个"扩到全量之后，同刻同类型的命中变多了）。
        #   若只按强度硬取前 2 条，很可能两条都是「排名跃迁」——
        #   表格看起来塞满了，信息量却和只有一条时一样。
        #   改为"同一时刻优先取不同类型"，同样的行数能覆盖更多种异动。
        #   （跨类型强度已做归一化，见 salience 规则 6 的注释，所以
        #     "谁最强"在同类型内部仍然成立。）
        chosen, seen_t = [], set()
        for e in picked:
            t = e.get("type")
            if t in seen_t:
                continue
            chosen.append(e)
            seen_t.add(t)
            if len(chosen) >= max(1, per_slot):
                break
        if not chosen and picked:
            chosen = picked[:1]
        for e in chosen:
            out.append({"time": F.times[i], "idx": i, **e})

    def _k(r):
        """时间降序（晚→早）→ 同刻强度降序 → 行序降序（稳定全序）。"""
        t = tk(r.get("time")) if callable(tk) else None
        s = -float(r.get("strength", 0) or 0)
        ix = -int(r.get("idx", 0) or 0)
        if t is None:
            return (1, 0, ix, s)          # 时间解析不出 → 沉底
        return (0, -t, ix, s)

    out.sort(key=_k)
    return out[:top_n]


def _fan_pick(ranked, total, dynamic=None):
    """为「主力资金累积曲线」选线 —— 兼顾**体量**与**当下动向**。

    ★★ v2.0.1 新增（问题九：「优化选择算法，确保能清楚展示主力动向与情绪动向」）。

    【原来怎么选】`ins[:10]` —— 单纯按当日累计净额取前 10。
      问题在于累计净额是**存量**指标：最强的那几个上午就定型了。
      收盘时图上十条线里常有三四条"上午冲高、之后横着不动" ——
      它们能回答"今天谁最强"，但完全看不出"**现在**钱在往哪流"。
      而用户要的"主力动向 / 情绪动向"恰恰是后者。

    【现在怎么选】每个方向 k 条 = 体量线 + 动向线：
      · 体量线（k − dynamic 条）：按累计净额取 —— 回答"谁在维持强势"；
      · 动向线（dynamic 条）：按 |近 5 分钟增量| 取、且**与体量线不重复** ——
        回答"刚刚谁开始动"。
      不重复是关键：重复选等于白白少一条线、图上少一档信息。
      实测同一天里两类名单经常完全不重叠（收盘时点：累计榜是"华为概念/央国企改革"
      这类老面孔，5 分钟榜是另一批），合并后图上才同时有"存量"和"增量"两种形状。

    【为什么不做加权综合分（如 0.5×体量 + 0.5×速度）】
      综合分看着更"科学"，但选出来的线**无法解释** ——
      看图的人会问"这条线凭什么在图里"。分成明确两类之后，
      一眼就能区分"这几条是体量龙头、那几条是新动向"，信息分层比混在一起更有用。

    `ranked` 需已按方向排好序（流入按净额降序、流出按升序）；
    每项需含 `code`、`d5`（近 5 分钟增量）。
    """
    k = max(2, int(total) // 2)
    dyn = max(1, int(dynamic)) if dynamic is not None else max(2, k * 3 // 10)
    dyn = min(dyn, k - 1)                      # 至少留一条体量线
    base = list(ranked[:k - dyn])
    taken = {r.get("code") for r in base}
    pool = [r for r in ranked if r.get("code") not in taken]
    # 动向线按 |d5| 取 —— 对流出侧而言，"5 分钟净流出最快"与
    # "由流出转为流入（尾盘回流）"都会命中，两者都是值得画出来的动向。
    pool.sort(key=lambda r: -abs(_fnum(r.get("d5"), 0.0)))
    return base + pool[:dyn]


def _market_stats(F, salience, i):
    """全市场资金汇总 —— 总览 KPI 与流入/流出对比图的取数入口。

    返回字段：
      n_in / n_out        净流入 / 净流出的板块个数
      sum_in / sum_out    流入合计 / 流出合计（亿）
      net_total           全市场净额（亿）＝ sum_in + sum_out
      top_in / top_out    最强流入 / 最强流出（含名称与金额），供 KPI 卡用
      in_list / out_list  资金曲线用的选线（含分钟序列与 5 分钟增量）
      index_pct           上证指数当日涨跌幅 %
    """
    import numpy as np

    vals = np.asarray(F.V[i], dtype=float)
    # 只统计概念层：行业层与概念层高度重叠，混统计会重复计入
    mask = np.asarray(F.concept_mask, dtype=bool)
    idxs = np.where(mask)[0]
    if len(idxs) == 0:                                   # 兜底：没有概念层就用全部
        idxs = np.arange(len(vals))

    # ★ v2.0.1：把"近 5 分钟增量"一并带上 —— 它只用于**选线**
    #   （见 _fan_pick），曲线画的仍是累计值，口径不变。
    d5row = np.asarray(F.dV5[i], dtype=float)
    # ★★ v3.0.5：曲线长度**以时间标签为准**（用户报告「尾部一条横线」）。
    #   F.V 的行数理论上等于 len(F.times)，但面板由 .json(times) 与 .npy(矩阵)
    #   两个文件分别读写，并发刷新时可能读到"新矩阵 + 旧标签"的组合。
    #   一旦 V 比 times 长，series 就会多出**没有时刻可落位**的点；渲染层会
    #   把多出来的点续到横轴末端（15:00）并因端点外推画成平直横线。
    #   这里先把源头裁到同一长度：没有时间标签的数据点，语义上无法定位。
    _T = len(F.times)
    _rows_ok = (F.V.shape[0] == _T)
    rows = []
    for j in idxs:
        code = F.codes[j]
        series = [float(x) for x in F.V[:, j]]
        if not _rows_ok:
            series = series[:_T]
        rows.append({
            "code": code,
            "name": F.names.get(code, code),
            "kind": F.kinds.get(code, ""),
            "net": float(vals[j]),
            "d5": float(d5row[j]) if j < len(d5row) else 0.0,
            "series": series,
        })
    if not _rows_ok:
        log_line(f"[chart guard] F.V {F.V.shape[0]} 行 ≠ times {_T} 个 → "
                 f"资金曲线按 times 截断（防止出现无标签的假横线）")

    ins = sorted([r for r in rows if r["net"] > 0],
                 key=lambda r: -r["net"])
    outs = sorted([r for r in rows if r["net"] < 0],
                  key=lambda r: r["net"])

    # ★★ v3.0.6：**资金曲线只在"有足够历史"的板块里选线**。
    #
    #   【为什么必须这么做】实测（2026-09-23，替代源并入后的面板）：
    #     并入的 834 个板块**只有当前这一分钟**的值（其余分钟如实留空），
    #     而它们当日的累计净额往往是最大的 —— 于是"按累计净额取前 10"
    #     选出来的**全是只有 1 个点的板块**，图上就变成十几个孤立点，
    #     看上去就是"数据曲线不显示"。
    #   【修法】选线候选池先按"有效分钟点数"过滤；
    #     ★ **过滤只作用于选线**，累计榜 `top_in/top_out`、汇总
    #       `sum_in/sum_out/n_in/n_out` 的口径**一字不改** ——
    #       那些是"当下存量"的正确口径，不该被历史长度影响。
    #   【兜底】若过滤后候选太少（< 4 条），退回全量 ——
    #     宁可画点，也不能把图搞成空的。
    def _n_valid(s):
        return sum(1 for x in s if x == x)          # x == x 用来排除 NaN

    _fan_pool = [r for r in rows if _n_valid(r.get("series") or [])
                 >= FAN_MIN_POINTS]
    # ★★ v3.0.6：概念层"一个够长的都没有"时，**改用有历史的行业板块**兜底。
    #
    #   【为什么需要】实测（2026-09-23，替代源并入后的面板）：
    #     资金曲线原本只在**概念层**里选线（`F.concept_mask`），
    #     而那天概念层**一个板块都没有够长的历史**（老面板的列全是行业，
    #     新并入的概念只有当前一分钟）→ 候选池为空 → 图上只剩孤立点。
    #     但同一份面板里，**行业层有 122 个板块带着完整的 240 分钟真实曲线**
    #     —— 数据就在那儿，只是选线时够不到。
    #   【口径】兜底用的仍是 `F.V`（亿元，主力净额累计），
    #     与概念层**完全同口径**，只是板块类型不同；因此**如实记日志**说明
    #     "这一屏曲线画的是行业而非概念"，不让人误以为选线逻辑变了。
    _fan_fallback = False
    if len(_fan_pool) < 4:
        _alt = []
        for _j in np.where(~mask)[0]:
            _s = [float(x) for x in F.V[:, _j]]
            if not _rows_ok:
                _s = _s[:_T]
            if _n_valid(_s) < FAN_MIN_POINTS:
                continue
            _c = F.codes[_j]
            _alt.append({
                "code": _c,
                "name": F.names.get(_c, _c),
                "kind": F.kinds.get(_c, ""),
                "net": float(vals[_j]),
                "d5": float(d5row[_j]) if _j < len(d5row) else 0.0,
                "series": _s,
            })
        if len(_alt) >= 4:
            _fan_pool = _alt
            _fan_fallback = True
            log_line(f"[fan] 概念层无足够历史（≥{FAN_MIN_POINTS} 点）→ "
                     f"曲线改用**有历史的行业板块** {len(_alt)} 个"
                     f"（同为 F.V 亿元口径，仅板块类型不同）")
    if len(_fan_pool) < 4:
        _fan_pool = rows
    _n_drop = len(rows) - len(_fan_pool)
    if _n_drop > 0 and not _fan_fallback:
        log_line(f"[fan] 选线池按历史长度过滤：{len(rows)} → {len(_fan_pool)}"
                 f"（剔除 {_n_drop} 个不足 {FAN_MIN_POINTS} 个有效点的板块，"
                 f"它们只出现在累计榜、不出现在曲线里）")
    ins_f = sorted([r for r in _fan_pool if r["net"] > 0],
                   key=lambda r: -r["net"])
    outs_f = sorted([r for r in _fan_pool if r["net"] < 0],
                    key=lambda r: r["net"])

    sum_in = sum(r["net"] for r in ins)
    sum_out = sum(r["net"] for r in outs)

    # 指数涨跌幅
    index_pct = None
    try:
        if F.index:
            p = min(len(F.index) - 1, i)
            base = float(F.index[0][1])
            cur = float(F.index[p][1])
            if base:
                index_pct = (cur - base) / base * 100
    except Exception:                                     # noqa: BLE001
        index_pct = None

    # ★★ v2.0.10：区间极值（市场全景「区间极值」板块的数据源）。
    #   与资金曲线**同一口径**：F.V 的列就是各板块的主力资金累计曲线（亿元）。
    #   · 吸筹：最新点 − 盘中最低点，差值越大 = 从盘中低点回升越多；
    #   · 出货：最新点 − 盘中最高点，仅保留差值为负（始终未收复盘中高点），
    #     差值越小 = 距盘中高点回撤越大。各取前十。
    range_ra, range_sh = [], []
    # ★ v3.0.6：同样只在"有足够历史"的板块里取极值 —— 只有 1 个点的板块
    #   必然 lo == hi == latest，差值恒为 0，会**凭空占满**吸筹/出货榜。
    for r in _fan_pool:
        s = r["series"]
        if not s:
            continue
        latest = s[-1]
        lo = min(s)
        hi = max(s)
        range_ra.append({"code": r["code"], "name": r["name"],
                         "net": latest, "low": lo, "diff": latest - lo})
        range_sh.append({"code": r["code"], "name": r["name"],
                         "net": latest, "high": hi, "diff": latest - hi})
    range_ra = sorted(range_ra, key=lambda r: -r["diff"])[:10]
    range_sh = sorted([r for r in range_sh if r["diff"] < 0],
                      key=lambda r: r["diff"])[:10]

    return {
        "n_in": len(ins), "n_out": len(outs),
        "sum_in": round(sum_in, 2), "sum_out": round(sum_out, 2),
        "net_total": round(sum_in + sum_out, 2),
        "top_in": ins[:10], "top_out": outs[:10],
        # ★ 资金曲线选线（v2.0.1 改为"体量 + 动向"混合，见 _fan_pick）：
        #   每个方向 FAN_TOP_N//2 条，两条腿合计 20 条
        #   （沿用 v1.1.5 用户要求「不少于 20 个板块概念标的」的口径）。
        "in_list": _fan_pick(ins_f, FAN_TOP_N),
        "out_list": _fan_pick(outs_f, FAN_TOP_N),
        "index_pct": index_pct,
        "n_total": len(rows),
        "range_ra": range_ra, "range_sh": range_sh,
    }


def _leader_timeline(F, salience, rows=16):
    """每个时间片取辨识度前三，构成「主角更替」序列。

    ★ 取样必须**均匀覆盖全天**，不能顺次取到上限就停。
      旧实现是 `for i in range(5, T, 6)`，配合 rows=12 的上限，
      取满 12 行就 break —— 12 行 × 6 分钟只够到 10:42，
      于是「主角更替」的时间永远停在十点多。这是本次修的根因。
      现在改成在 [开盘后 5 分钟, 收盘] 区间上**等距**取样 rows 个点，
      最后一个点必定落在收盘时刻，全天都被覆盖到。
    """
    T = len(F.times)
    if T < 3:
        return []
    start = min(5, T - 1)
    if rows <= 1:
        idxs = [T - 1]
    else:
        span = T - 1 - start
        idxs = sorted({start + round(span * k / (rows - 1)) for k in range(rows)})

    out = []
    for i in idxs:
        top = salience.score_table(F, i, topn=3)
        if len(top) < 3:
            continue
        out.append({"time": F.times[i],
                    "top": [{"name": r["name"], "net": r["net_yi"]} for r in top[:3]]})
    return out


# 阶段对比的六个时点。用固定时刻而不是「等分切片」，因为这几个点是
# 盘中的天然分界（早盘冲高 / 上午定调 / 午前收尾 / 午后开盘 / 尾盘前 / 收盘），
# 与第一版报告里的口径保持一致。
STAGE_MARKS = ["09:45", "10:30", "11:30", "13:30", "14:15", "15:00"]


def _stage_snapshot(F, salience, marks=None, k=6):
    """一天切六个时点，每格给出该时点的**流入 Top k + 流出 Top k**。

    ★ 旧实现每格只取单边 top8（全是流入），而且画图时只有文字没有量柱，
      一条 8 行的文字塞进半屏高的格子里 —— 既看不到流出，也留下大片空白。
      现在补成双向 + 带量柱，格子自然被填满。
    """
    marks = marks or STAGE_MARKS
    out = []
    for m in marks:
        if m not in F.times:
            continue                       # 盘中尚未走到的时点跳过
        i = F.times.index(m)
        r_in = salience.score_table(F, i, topn=k)
        r_out = salience.score_table(F, i, topn=k, direction="out")
        out.append({
            "time": m,
            "rows": [{"name": r["name"], "net": r["net_yi"]} for r in r_in],
            "outs": [{"name": r["name"], "net": r["net_yi"]} for r in r_out],
        })
    return out


# ==================================================================== 图表类工厂
# ★★ v2.0.3（2026-09-22，启动延迟优化）：7 个图表类的**定义**从模块级
#   挪进 _define_chart_classes() 工厂，由 ensure_mpl() 在首次调用时执行。
#   为什么：matplotlib 的 import 实测 ~1.0s（native_env 实测 1028ms，
#   占「双击→窗口出现」1853ms 的大头），而类定义继承 FigureCanvasQTAgg，
#   逼着模块在 import 阶段就付出这 1 秒。挪进工厂后，matplotlib 的
#   import 与画布创建都推迟到**窗口显示之后**
#   （MainWindow._materialize_charts），窗口出现提前约 1 秒。
#   约束：类方法引用的模块级常量/助手（UP_SEQ、_minute_index、THEMES、
#   coloring…）全部留在模块级 —— 方法体在**调用时**才解析全局名，
#   而任何 ensure_mpl() 调用都发生在模块 import 完成之后，天然就绪。
_CHART_CLASS_NAMES = ("MplCanvas", "FanChart", "IndexChart", "ThemeBoard",
                      "LeaderGrid", "StageGrid", "FlowBarChart")


def _define_chart_classes():
    """定义全部 matplotlib 图表类并返回（仅供 ensure_mpl() 调用）。"""
    class MplCanvas(FigureCanvasQTAgg):
        def __init__(self, h=3.6, w=8.0):
            self.fig = Figure(figsize=(w, h), dpi=100, facecolor=THEMES["light"]["bg"])
            super().__init__(self.fig)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.th = coloring(THEMES["light"])      # 涨跌方向在此生效

        def apply_theme(self, th):
            """切换配色。

            ★ v1.2.2：悬停竖线也必须跟着换色。
              它的颜色是在**创建那一刻**从 self.th["accent"] 固化的，
              切到深色主题后仍留着浅色主题的蓝（实测 #2f6fe4 不变，
              而 dark 主题的 accent 是 #6f9bff）—— 深色底上一条暗蓝竖线几乎看不见。
              这里统一补一次重新着色（只有 FanChart 有竖线，用 getattr 兜底）。
            """
            self.th = th
            self.fig.set_facecolor(th["bg"])
            vl = getattr(self, "_vline", None)
            if vl is not None:
                try:
                    vl.set_color(th["accent"])
                except Exception:                            # noqa: BLE001
                    pass
            self.draw()

        def _style(self, ax):
            th = self.th
            ax.set_facecolor(th["bg"])
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)
            for s in ("left", "bottom"):
                ax.spines[s].set_color(th.get("axis", th["grid"]))
            ax.tick_params(colors=th["sub"], labelsize=8)
            ax.grid(True, color=th["grid"], linewidth=.8, axis="y", alpha=.9)
            return th

        def blank(self, msg="暂无数据"):
            self.fig.clear()
            ax = self.fig.add_subplot(111)
            ax.set_axis_off()
            ax.text(.5, .5, msg, ha="center", va="center", color=self.th["sub"],
                    fontsize=12, transform=ax.transAxes)
            self.draw()
    # ---------------------------------------------------- 右端标签覆盖层
    class FanLabelOverlay(QWidget):
        """主力累计曲线右端标签的 **Qt 覆盖层**（v2.0.8 悬停卡顿根治）。

        ★ 为什么离开 matplotlib：v2.0.6 用 20 个 matplotlib Text artist 做
          右端标签，悬停换分钟时 `draw_artist` 单条 1.5~3ms（字符串一变、
          字形布局全部重来，无法缓存）× 20 条 = 35ms/次 —— 光标不跟手。
          改用 Qt：QPainter 画 20 条字符串 **< 0.5ms**，且只重绘覆盖层
          这一小块 widget，与 matplotlib 的画布/快照体系完全解耦。

        ★ 关键设计：
          · 覆盖层是画布的**子 widget**，铺满画布、自身透明，
            只在右侧标签带内落笔 —— 悬停竖线的轴区 blit 在下层画布进行，
            两者天然分层，互不覆盖；
          · `WA_TransparentForMouseEvents` —— 鼠标事件必须全部穿透到画布，
            悬停检测不受覆盖层遮挡；
          · 位置每帧由 ax.get_position() 现算（画布尺寸/窗口缩放自动跟随），
            文字用 Qt 字体（Microsoft YaHei 8pt，与 matplotlib 7.9pt 观感一致）。
        """

        def __init__(self, chart):
            super().__init__(chart)
            self._chart = chart
            self._items = []          # [{name,color,series,end,q,disp}]
            self._hover_k = None      # 悬停分钟（数据行号）；None=显示终点值
            # ★ v3.0.6：**全部**曲线句柄 + 鼠标 y —— 供"悬浮框"用。
            #   为什么存 Line2D 句柄而不是数据副本：增量刷新路径
            #   （try_update_series）只调 set_data 原地改句柄，
            #   存句柄就**永远读到最新数据**，不必在两条路径里各同步一次。
            self._lines = []
            self._hover_y = None      # 鼠标所在的数据 y（亿元）
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.setAttribute(Qt.WA_NoSystemBackground, True)
            self.setAttribute(Qt.WA_TranslucentBackground, True)

        def set_items(self, items):
            """整批替换标签内容（plot/换数据/换主题时调用）。"""
            self._items = list(items or [])
            self._hover_k = None
            self.update()

        def set_lines(self, lines):
            """登记全部曲线句柄（plot() 里调用一次；增量刷新不必重调）。"""
            self._lines = list(lines or [])
            self.update()

        def set_hover(self, k, ydata=None):
            """悬停到数据行 k（None=移出，恢复终点值）。值变才重绘。

            ydata: 鼠标所在的数据 y（亿元）。★ v3.0.6 新增 —— 悬浮框要据此
                   找"离鼠标最近的那条曲线"。
            """
            if k != self._hover_k or ydata != self._hover_y:
                self._hover_k = k
                self._hover_y = ydata
                self.update()

        def paintEvent(self, _ev):
            if not self._items:
                return
            chart = self._chart
            fig = chart.fig
            if not fig.axes:
                return
            ax = fig.axes[0]
            W, H = self.width(), self.height()
            p = ax.get_position()
            ax_l, ax_r = p.x0 * W, (p.x0 + p.width) * W
            ax_t, ax_b = (1.0 - p.y0 - p.height) * H, (1.0 - p.y0) * H
            y0, y1 = ax.get_ylim()
            span = (y1 - y0) or 1.0
            painter = QPainter(self)
            try:
                painter.setFont(QFont("Microsoft YaHei", 8))
                x_txt = ax_r + 6.0
                for it in self._items:
                    s = it["series"]
                    k = self._hover_k
                    if k is not None and s and 0 <= k < len(s):
                        v = s[k]
                    else:
                        v = it["end"]
                    y_px = ax_t + (1.0 - it["q"]) * (ax_b - ax_t)
                    painter.setPen(QColor(it["color"]))
                    painter.drawText(
                        int(x_txt), int(y_px + 4),
                        f'{"▲" if it.get("up") else "▼"} {it["name"]} {v:.1f}亿')

                # ---- ★★ v3.0.6 悬浮框（用户需求）----
                #   「鼠标悬停在折线，弹出悬浮框显示板块名称、实时净额」。
                #   为什么必须有它：末端只标了 10 条高亮曲线，**其余 10 条**
                #   如果悬停也读不到，就变成"看得见线条、叫不出名字"。
                #   做法：拿鼠标所在的数据 y，在**全部**曲线里找最近的一条
                #   （存的是 Line2D 句柄，增量刷新后也是最新数据）。
                k = self._hover_k
                if k is not None and self._hover_y is not None and self._lines:
                    x0, x1 = ax.get_xlim()
                    xspan = (x1 - x0) or 1.0
                    best, bd = None, None
                    for ln in self._lines:
                        yd = ln.get_ydata()
                        if k >= len(yd):
                            continue
                        vv = float(yd[k])
                        if vv != vv:                       # NaN 跳过
                            continue
                        dd = abs(vv - self._hover_y)
                        if bd is None or dd < bd:
                            best, bd = (ln, vv), dd
                    if best is not None:
                        ln, vv = best
                        fy = (min(max(vv, y0), y1) - y0) / span
                        _ty = ax_t + (1.0 - fy) * (ax_b - ax_t)
                        _fx = (k - x0) / xspan
                        _tx = ax_l + _fx * (ax_r - ax_l)
                        _txt = f'{ln.get_label()}  {vv:+.1f} 亿'
                        _fm = painter.fontMetrics()
                        _tw = _fm.horizontalAdvance(_txt) + 16
                        _thh = _fm.height() + 8
                        _bx = min(max(_tx + 12.0, 2.0), max(2.0, W - _tw - 2.0))
                        _by = min(max(_ty - _thh - 8.0, 2.0),
                                  max(2.0, H - _thh - 2.0))
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(QColor(0, 0, 0, 165))
                        painter.drawRoundedRect(int(_bx), int(_by),
                                                int(_tw), int(_thh), 4, 4)
                        painter.setPen(QColor(ln.get_color()))
                        painter.drawText(int(_bx + 8), int(_by + _thh - 6), _txt)
                        painter.setBrush(Qt.NoBrush)
            except Exception:                                # noqa: BLE001
                pass
            finally:
                painter.end()

    class FanChart(MplCanvas):
        """主力资金累计曲线 —— 20 条（流入前 10 + 流出后 10）+ 悬停联动。

        ★ v1.1.5 三项改造（用户需求）：
          1) 标的数：从 8 条提到 **20 条**（前 10 流入 + 后 10 流出），
             与 HTML 版 buildFan 的 topn=20 口径一致；
          2) 横轴：固定为 **9:30–15:00 完整交易时段**，刻度等距
             （09:30/10:00/…/15:00 共 11 个整点），不再随"数据到了几点"伸缩 ——
             盘中看图时曲线该长多长一眼就有数，不同时刻截图的横轴也一致；
          3) 悬停联动：鼠标移到横轴某一分钟，**左侧数据面板切换到该时点**，
             并在图上画一条竖线标记（见 MainWindow.on_fan_hover）。

        ★ v1.1.7 三项改造（用户需求）：
          1) 流出曲线由**虚线改连续实线** —— 两侧一律实线，方向改用颜色族区分
             （暖=流入 / 冷=流出，与图例文字着色同源）；
          2) 跳点 / 空值 / 缺失分钟做**衔接填充**（见 _fill_gaps），
             曲线全程连贯、不出现断裂或空白段；
          3) 纵轴量程**自适应**（见 _fan_ylim）：按当前已加载数据算 min/max +
             8% 余量 + 最小跨度保护，早盘（数据少、跨度小）与尾盘（跨度上千亿）
             都能完整合理展示，不再靠 matplotlib 的固定百分比边距。

        ★ v1.1.6 图例改造：图例从绘图区**内部**移到**轴下方**的独立图例带。
          旧写法两块 ax.legend 各占绘图区高 39%，20 条曲线铺满时找不到空位，
          实测 4800 个数据点里有 790 个（16.5%）被图例压住。
          现在由 _legend_outside（二分定列数）+ _reserve_bottom（实测高度留白）
          协作，把曲线与图例彻底分开（见两个方法的 docstring）。

        配色：参考 HTML 版，同向多条线按 UP_SEQ / DN_SEQ 取不同色相，
          而不是全部同一个红/绿（20 条同色必然糊成一团）。

        ★★ v2.0.5 标注改造（用户需求）：**彻底废弃底部图例**，改为每条曲线
          在最右端终点处直接标注「板块名 收盘金额（XX.X 亿）」，文字颜色与
          曲线一致 —— 参考实时盯盘软件的通行做法，省掉来回对照颜色的动作。
          标签钉在绘图区右侧的**独立标签带**里（位置稳定、永不遮挡曲线），
          末端值接近时用最小间距收敛防止叠字，被推离的标签用细引线接回终点
          （见 _end_labels 的 docstring）。v1.1.6 的图例带/列数二分整套机制
          （_legend_outside / _reserve_bottom / _make_legend / FAN_LEG_*）
          随之全部移除。
        """

        # 悬停回调：fn(idx:int, hm:str) —— 由 MainWindow 接上
        hover_cb = None

        def __init__(self, h=3.6, w=8.0):
            super().__init__(h, w)
            self._vline = None
            self._hover_px = None
            self._n = 0                  # 当前曲线覆盖的分钟数（悬停范围）
            self._times = []
            # ★ v1.2.4：曲线的横坐标（分钟序号）与是否按分钟序号定位。
            #   _xpos 与数据行号一一对应，供悬停把"鼠标横坐标"换算回"数据行号"
            #   （见 _row_at_x）；_x_mapped=False 表示该面板认不出时间标签、
            #   已退回"x=下标"的旧口径。
            self._xpos = []
            self._x_mapped = False
            self._plot_x0 = 0.0          # 绘图区像素边界（用于把鼠标 x 换算成分钟）
            self._plot_x1 = 1.0
            # ★ v1.2.2 局部重绘（blit）状态 —— 见 _mark / _ensure_blit_bg 的 docstring：
            #   _blit_bg    —— **不含竖线**的绘图区背景快照（copy_from_bbox 的产物）
            #   _blit_bbox  —— 该快照对应的几何指纹；尺寸/位置一变即判定失效
            self._blit_bg = None
            self._blit_bbox = None
            # ★★ v3.0.4 增量更新现场（见 try_update_series）：
            #   _inc_handles       —— (流入 handles, 流出 handles)，供 set_data 复用
            #   _inc_names         —— (流入名称序列, 流出名称序列)，指纹比对用
            #   _inc_ends          —— 曲线终点信息（供覆盖层标签）
            #   _inc_leader_lines  —— 当前画的引线，量程变时移除重画
            #   _inc_th            —— 配色表（增量路径不重跑 _style，沿用旧表）
            self._inc_handles = None
            self._inc_names = None
            self._inc_ends = None
            self._inc_leader_lines = []
            self._inc_th = None
            self._last_leader_lines = []
            # ★ v3.0.4：IndexChart 的增量现场（见其 try_update_series）
            self._inc_ready = False
            self._inc_line1 = None
            self._inc_line2 = None
            self._inc_fill1 = None
            self._inc_have2 = False
            # ★★ v2.0.6 悬停联动（右端标签版）：
            #   _label_texts —— _end_labels 画出的 Text artist（带 _fan_* 元数据）
            #   _label_bg/_label_bbox —— 标签带背景快照（**不含文字**），
            #       悬停换分钟时 restore 后重画新文字，避免整幅重绘
            self._label_texts = []
            self._label_bg = None
            self._label_bbox = None
            # matplotlib 的鼠标事件：进入/离开/移动/按下
            self.mpl_connect("motion_notify_event", self._on_motion)
            self.mpl_connect("axes_leave_event", self._on_leave)
            self.setMouseTracking(True)
            # ★★ v2.0.8：右端标签改用 Qt 覆盖层（悬停联动 <0.5ms，见类注释）
            self._overlay = FanLabelOverlay(self)
            self._overlay.setGeometry(self.rect())
            self._overlay.show()

        # ---------------------------------------------------------- blit 基础设施
        BLIT_PAD = 2.0          # 背景快照取景框相对轴框的外扩像素（见 _blit_frame）

        def draw(self):
            """整幅重绘。

            ★ v1.2.2 覆写：**任何整幅重绘之后，背景快照必然失效**。
              这是唯一可靠的作废点 —— plot() 换数据、apply_theme() 换配色、
              窗口 resize（Qt 后端内部会 draw_idle → draw），三者的共同下游都是这里。
              漏掉任何一条，都会拿"旧数据的背景"去贴竖线，
              表现为竖线周围一圈陈旧像素（幽灵残影）。
            """
            super().draw()
            self._invalidate_blit()

        def _raw_draw(self):
            """渲染一帧但**不触发快照作废**（v2.0.8 悬停卡顿修复的核心）。

            ★ 背景：背景快照的重建本身需要一次渲染。v2.0.6 起 FanChart 有
              **两张**快照（竖线的轴区 + 标签带），而重建用的是 `self.draw()` ——
              它会先作废**全部**快照，于是两张快照互相打靶：
                  换分钟 → 标签带快照失效 → 全幅渲染 → 竖线快照被作废
                  → 下一次 _mark 又全幅渲染 → 标签带快照又被作废 → ……
              实测每次换分钟要付 **2 次全幅渲染 ≈ 154 ms**（offscreen 1450px
              画布逐段计时：mark 71.8 + labels 82.2），这正是"光标不跟手"。
              ✔ 解法：重建时改走 QtAgg 基类的裸渲染 —— 同样画出全新的一帧，
                但不碰本类的快照缓存，两张快照从此稳定。
            """
            FigureCanvasQTAgg.draw(self)

        def resizeEvent(self, ev):
            """画布尺寸变化 → 标签覆盖层跟着铺满（v2.0.8）。"""
            super().resizeEvent(ev)
            ov = getattr(self, "_overlay", None)
            if ov is not None:
                ov.setGeometry(self.rect())
                ov.update()

        def _invalidate_blit(self):
            """作废背景快照（下次悬停时惰性重建）。"""
            self._blit_bg = None
            self._blit_bbox = None

        def _bg_bbox_key(self, ax):
            """背景快照的几何指纹：绘图区四边 + 控件像素尺寸。

            ★ 必须带控件尺寸：窗口缩放后 Agg 缓冲整体重建，
              旧的 region 拷贝与新缓冲尺寸不一致，restore 会错位。
            """
            bb = ax.bbox
            return (round(bb.x0, 1), round(bb.y0, 1), round(bb.x1, 1),
                    round(bb.y1, 1), self.width(), self.height())

        def _blit_frame(self, ax):
            """背景快照 / 局部重绘所用的取景框：轴框外扩 BLIT_PAD px，并夹在画布内。

            ★★ 为什么必须外扩（v1.2.2 逐像素验证抓到的残影根因）★★★★★★★★★★
              `copy_from_bbox` 会把边界**取整**。而线宽 1px 的竖线是以中心线为轴
              左右各 0.5px 落笔、再经抗锯齿铺开，它的**最上一行像素会落到
              取整后的保存区域之外**：
                  轴顶 display y = 333.53  →  缓冲行 367-333.53 = 33.47
                  copy_from_bbox 取整 → 从**第 34 行**开始保存
                  而竖线在第 33 行仍留下 3 个抗锯齿像素（实测颜色 96,144,232）
              这一行既然没被 save，`restore_region` 自然也不会去擦它 ——
              于是鼠标每划过一列，就在顶部留下一个小点；
              连续划 40 列后连成一条断断续续的横虚线（实测差异 117 px）。
              × 表面看像"抗锯齿问题"，实为**取景框少了一行**。
              ✔ 外扩 2px 把所有"贴边溢出"的像素一并纳入，残影彻底消失。
              代价：每次多拷贝约 1.7% 的像素，实测仍在 0.2 ms 量级。
            """
            b = ax.bbox
            fw = self.fig.bbox.width
            fh = self.fig.bbox.height
            return Bbox.from_extents(
                max(0.0, b.x0 - self.BLIT_PAD), max(0.0, b.y0 - self.BLIT_PAD),
                min(fw, b.x1 + self.BLIT_PAD), min(fh, b.y1 + self.BLIT_PAD))

        def _ensure_blit_bg(self, ax):
            """取（必要时重建）不含竖线的绘图区背景快照；不支持 blit 时返回 None。

            ★ 重建只需**一次**整幅 draw（实测约 120 ms），此后每次鼠标移动
              都是纯 blit（约 0.16 ms）。所以这一次开销摊到整段悬停操作里可忽略。
            ★ 快照必须在**竖线不可见**时抓 —— 否则竖线会被"烤"进背景，
              之后每次 restore 都把它贴回来，永远擦不掉。
            ★ 为什么不给竖线设 `animated=True`（matplotlib Cursor 的做法）：
              animated 的 artist 在**整幅 draw 时会被跳过**。一旦 blit 路径因故
              退回 `draw_idle()`，竖线就会整条消失 —— 静默失败，比"慢"严重得多。
              本实现用「抓快照前显式 set_visible(False)」达到同样的干净背景，
              且退回路径能正常画出竖线，少一个会悄悄坏掉的开关。
            """
            key = self._bg_bbox_key(ax)
            if getattr(self, "_blit_bg", None) is not None \
                    and getattr(self, "_blit_bbox", None) == key:
                return self._blit_bg
            if not self.supports_blit:
                return None
            vl = self._vline
            try:
                if vl is not None:
                    vl.set_visible(False)
                # ★★ v2.0.8：self.draw() 会作废全部快照（含标签带的），
                #   双快照互相打靶 → 每次换分钟 2 次全幅渲染（实测 154ms）。
                #   改走基类裸渲染：帧照样渲染出来，缓存一个都不清。
                self._raw_draw()
                self._blit_bg = self.copy_from_bbox(self._blit_frame(ax))
                self._blit_bbox = key
            except Exception:                        # noqa: BLE001
                self._invalidate_blit()
                return None
            finally:
                if vl is not None:
                    vl.set_visible(True)
            return self._blit_bg

        # ---------------------------------------------------------- 悬停换算
        def _row_at_x(self, xdata):
            """鼠标所在横坐标（分钟序号）→ **数据行号**（0..n-1）。

            ★★ v1.2.4：曲线改按分钟序号落点后，这个换算必须跟着改。
              旧实现直接 `int(round(ev.xdata))` —— 那是在"x=下标"时才成立；
              现在 x 是分钟序号，两者在有缺口时会差出几十格（实测 11:17 的那次
              差 66）。不换算的话，鼠标停在 11:17 那一列、左侧面板却显示 10:11
              的数据 —— 图与表对不上，是最难被发现的错位。

            映射失败（退回下标模式）时降级为恒等映射，与旧行为逐点一致。
            """
            if not self._xpos or not self._x_mapped:
                return int(round(xdata))
            # xpos 严格单调 → 可二分找最近点
            import bisect
            i = bisect.bisect_left(self._xpos, xdata)
            if i <= 0:
                return 0
            if i >= len(self._xpos):
                return len(self._xpos) - 1
            # 比较左右哪边更近
            lo, hi = self._xpos[i - 1], self._xpos[i]
            return (i - 1) if (xdata - lo) <= (hi - xdata) else i

        def _on_motion(self, ev):
            if self._n <= 1 or ev.inaxes is None or ev.xdata is None:
                self._on_leave(ev)
                return
            # xdata 是"分钟序号"（0..239）→ 先换算成数据行号
            k = self._row_at_x(ev.xdata)
            if k < 0 or k >= self._n:
                self._on_leave(ev)
                return
            if k == self._hover_px:
                return
            self._hover_px = k
            self._mark(k)
            # ★★ v2.0.8：右端标签联动 —— 换分钟时把标签切到该分钟的数值
            #   （Qt 覆盖层 QPainter 重画，<0.5ms；不碰 matplotlib 画布）。
            #   ★ v3.0.6：一并把鼠标的 y 传下去，悬浮框据此找最近曲线。
            try:
                self._apply_hover_labels(k, getattr(ev, "ydata", None))
            except Exception:                            # noqa: BLE001
                pass
            if callable(self.hover_cb):
                try:
                    self.hover_cb(k, self._times[k] if k < len(self._times) else "")
                except Exception:                            # noqa: BLE001
                    pass

        def _on_leave(self, _ev=None):
            """鼠标离开绘图区 → 擦掉竖线。

            ★ v1.2.2：旧写法直接 `draw_idle()`（整幅重绘，实测 120 ms）。
              鼠标沿绘图区边缘滑动时，`_on_motion` 在 inaxes=None 分支会反复调它，
              每次都是 120 ms —— 又一个卡顿源。
              现在改为：**把不含竖线的背景快照贴回去**（0.16 ms）即可擦干净。
              快照不可用（或几何已变）时才退回 `draw_idle()`。
            """
            if self._hover_px is None:
                return
            self._hover_px = None
            # ★★ v2.0.8：鼠标移出 → 标签恢复各曲线终点收盘值
            #   （Qt 覆盖层只重绘自身，与竖线擦除的轴区 blit 互不影响）。
            try:
                self._apply_hover_labels(None)
            except Exception:                            # noqa: BLE001
                pass
            # ★ v2.0.10：通知外部恢复默认批注（时点行回到收盘值句）
            if callable(self.hover_cb):
                try:
                    self.hover_cb(-1, "")
                except Exception:                        # noqa: BLE001
                    pass
            vl, self._vline = self._vline, None
            if vl is None:
                return
            ax = self.fig.axes[0] if self.fig.axes else None
            bg = getattr(self, "_blit_bg", None)
            if (ax is not None and bg is not None and self.supports_blit
                    and getattr(self, "_blit_bbox", None) == self._bg_bbox_key(ax)):
                try:
                    self.restore_region(bg)      # 背景里本就没有竖线 → 一贴即净
                    self.blit(self._blit_frame(ax))
                    try:
                        vl.remove()
                    except Exception:                    # noqa: BLE001
                        pass
                    return
                except Exception:                        # noqa: BLE001
                    self._invalidate_blit()
            try:
                vl.remove()
            except Exception:                                # noqa: BLE001
                pass
            self.draw_idle()

        def _mark(self, k):
            """在指定分钟画竖线标记。

            ★★ v1.2.2 光标跟随延迟修复（核心改动）★★★★★★★★★★★★★★★★★★★★
            【问题】旧实现每移动到一个新分钟就 `self.draw_idle()`，触发**整幅画布重绘**。
                    实测（本机 1002×315 画布、20 条曲线 + 20 条图例）：
                         draw() 全量重绘 ......... 120.66 ms
                         ├ 图例 legend.draw() ....  80.37 ms  ← 独占 2/3
                         └ 其余（20 条曲线+坐标轴）  40.29 ms
                    而鼠标滑动时事件间隔约 8 ms → 队列以 **15.4×** 速度堆积，
                    表现为「光标越划越滞后、松手后才追上」，即用户反馈的"不跟手"。
                    端到端实测：平均每事件 122.90 ms 同步工作 = 7.4× 的 60Hz 预算。

            【方案】改用 matplotlib 官方的 **blit 局部重绘**（与 widgets.Cursor 同源）：
                        ① 抓一张**不含竖线**的绘图区背景快照（copy_from_bbox）
                        ② 每次移动：restore_region(背景) → draw_artist(竖线) → blit(bbox)
                    实测这一步 **0.16 ms**，比全量重绘快 **约 750×**，
                    彻底进入 60Hz 预算（16.7 ms）以内。

            【为什么快这么多】blit 只把竖线所在的那一条像素重新画进 Agg 缓冲，
            再让 Qt 重绘该矩形；图例那 80 ms 的文本布局完全绕过了。

            【失效处理】见 draw() 覆写 —— 换数据/换主题/改窗口尺寸都会作废快照，
            下次悬停惰性重建（仅一次全量 draw，可忽略）。
            ★ 快照失效判断用**几何指纹**（轴四边 + 控件尺寸），而不是靠标志位轮询：
              漏一次就会拿错位背景去贴，屏幕上表现为竖线旁一串陈旧像素。
            """
            ax = self.fig.axes[0] if self.fig.axes else None
            if ax is None:
                return
            # ★★ v1.2.4：k 是**数据行号**，竖线必须画在该行对应的**分钟序号**上。
            #   曲线改按分钟序号落点后，两者在有缺口时不等 —— 直接拿 k 当 x 画，
            #   竖线会跑到曲线左边（错位量与曲线压缩量相同），准星指的不是它压住的那条线。
            xk = self._xpos[k] if (self._xpos and 0 <= k < len(self._xpos)) else k
            if self._vline is None:
                # ★ zorder=3（原为 1）—— 这是 blit 方案的**必要**配套，不是随手调风格：
                #   blit 时 `ax.draw_artist(vline)` 是直接把竖线画进已含曲线的背景，
                #   效果上**恒在最上层**；而整幅 draw 会严格按 zorder 排序，
                #   zorder=1 的竖线被 20 条曲线（默认 zorder=2）盖在下面。
                #   两条路径于是画出**不同**的画面 —— 实测逐像素比对差 327 px
                #   （差异全部落在竖线所在的 3 像素列内，且曲线压线处最明显）。
                #   把 zorder 提到曲线之上，两条路径才真正等价（实测差异 0 px）。
                #   顺带也是更合理的观感：准星被 20 条线切成一截截反而看不清。
                self._vline = ax.axvline(xk, color=self.th["accent"],
                                         linewidth=1.0, alpha=.75, zorder=3)
            else:
                self._vline.set_xdata([xk, xk])

            bg = self._ensure_blit_bg(ax)
            if bg is None:                       # 后端不支持 blit → 退回旧路径
                self.draw_idle()
                return
            try:
                frame = self._blit_frame(ax)
                self.restore_region(bg)
                ax.draw_artist(self._vline)
                self.blit(frame)                 # ★ 与外扩后的快照同一取景框
            except Exception:                    # noqa: BLE001
                # ★ 任何一步失败都必须退回全量重绘，并把快照作废 ——
                #   静默吞掉会让竖线彻底不显示（比慢更糟）。
                self._invalidate_blit()
                self.draw_idle()

        # ---------------------------------------------------------- 绘图
        def plot(self, data):
            """data: [{name, series:[...], net, direction, times:[...]}]"""
            self.fig.clear()
            # ★ v3.0.4：fig.clear() 之后旧 artist 全部失效，必须清掉增量现场，
            #   否则 try_update_series() 会拿着已销毁的 Line2D 去 set_data
            #   （轻则无效、重则抛异常，且画面停在上一次的内容）。
            self._inc_handles = None
            self._inc_names = None
            self._inc_leader_lines = []
            self._last_leader_lines = []
            self._vline = None
            self._hover_px = None
            self._overlay.set_items([])              # ★ v2.0.8 标签随整幅重建
            ax = self.fig.add_subplot(111)
            th = self._style(ax)
            if not data:
                self._n = 0
                self.blank()
                return

            ins = [d for d in data if d.get("direction", "in") == "in"]
            outs = [d for d in data if d.get("direction", "in") == "out"]

            # ★★ v1.2.4：先把横坐标算出来 —— **按分钟序号定位，不按数组下标**。
            #   详见 _x_positions 的 docstring（这是"曲线只画到十点多"的根因修复）。
            #   times 取自 data[0]，全部曲线共用同一条时间轴。
            times = (data[0].get("times") or []) if data else []
            n = max((len(d["series"]) for d in data), default=0)
            xpos, x_mapped = _x_positions(times, n)
            self._n = n
            self._times = list(times)
            self._xpos = list(xpos)
            # x 定位器：供悬停把"鼠标所在分钟"换算回**数据行号**。
            #   映射成功时用二分查最近的点；退回下标模式时就是恒等映射。
            self._x_mapped = x_mapped

            def _draw(rows, is_up):
                handles = []
                seq = seq_for(is_up)          # ★ 跟随配色方向（反向时两族对调）
                for seq_i, d in enumerate(rows):
                    # ★ v1.1.7：曲线一律**连续实线**（用户需求）。
                    #   旧写法流出侧用 `(0, (4, 2.2))` 虚线，本意是"与流入区分"，
                    #   但实盘看盘时虚线在密集交叉处断成一格一格，反而看不清走势
                    #   ——「主力资金净流出」是要盯的曲线，不是装饰。
                    #   现在两侧都用实线，改由**颜色族**（暖/冷）区分方向，
                    #   与图例文字着色同源，配色方向翻转时自动对调。
                    s = _fill_gaps(d["series"])
                    # ★★ v1.2.4：x 用分钟序号（xpos），不再用 range(len(s))。
                    #   缺分钟时下标会短于真实跨度，曲线被整体压缩到左侧，
                    #   末端读出来的时刻比真实数据旧得多（实测差 66 分钟）。
                    #   ★★ v3.0.5：xpos 长度以 times 为准（见 _x_positions）。
                    #   若曲线比时间标签多（数据文件不一致），**只画有标签的部分**
                    #   —— 旧写法在这里退回 `range(len(s))`，会把多出来的行按
                    #   数组下标落到横轴上，末端一路到 239(15:00) 变成假横线。
                    _k = min(len(s), len(xpos))
                    s = s[:_k]
                    xs = xpos[:_k]
                    # 按序取色（HTML 版做法）：流入走暖色族、流出走冷色族
                    c = seq[seq_i % len(seq)]
                    # ★★ v3.0.6：**高亮 + 弱化**。rows 已按净额排序
                    #   （`_fan_pick` 取的就是 Top-N），所以前 FAN_HL_N 条
                    #   正是该方向净额最大的 5 条 —— 它们才是用户真正要盯的。
                    #   其余降到 FAN_DIM_ALPHA 作背景，鼠标悬停仍可读。
                    hl = seq_i < FAN_HL_N
                    d["_hl"] = hl          # ★ v3.0.6：供底部图例筛选
                    ln, = ax.plot(
                        xs, s, color=c,
                        linewidth=((1.9 if is_up else 1.7) if hl else 1.0),
                        linestyle="-",
                        alpha=(.95 if hl else FAN_DIM_ALPHA),
                        label=d["name"],
                        zorder=(3 if hl else 2))
                    handles.append(ln)
                    # ★★ v2.0.5：收集曲线终点，供右端直接标注「名称 收盘金额」。
                    #   s 一并带上 —— v2.0.6 悬停联动要按分钟取曲线中间值。
                    #   ★ v3.0.6：带上 hl / is_up —— 末端**只标 hl 的那些**，
                    #     且标签前缀用 ▲/▼ 明确方向（颜色族不再独占方向语义）。
                    if s and len(list(xs)):
                        ends.append({"x": float(list(xs)[-1]),
                                     "y": float(s[-1]),
                                     "name": d["name"], "color": c,
                                     "s": s, "hl": hl, "is_up": is_up})
                return handles

            ends = []
            h_in = _draw(ins, True)
            h_out = _draw(outs, False)

            # ★★ v3.0.4：登记"可增量更新"的现场 —— 供 update_series() 复用。
            #   为什么需要它：高频刷新（1~10 秒）下，每轮都走本方法开头的
            #   `fig.clear()` 会把 20 条 Line2D + 引线 + 覆盖层全部销毁重建，
            #   实测单图 20.9ms / 六图 106.3ms（见 bench_v304_load.py）。
            #   而盘中刷新时**曲线集合与名称几乎不变，只是每条多了一两个点** ——
            #   用 set_data 原地改数据可以绕开整个重建。
            #   ★ 指纹只认「曲线名称序列 + 方向 + 配色」：任何一项变了就
            #     必须走完整重建（增删曲线、反向配色都会让 artist 失去意义）。
            self._inc_names = ([d["name"] for d in ins], [d["name"] for d in outs])
            self._inc_handles = (h_in, h_out)
            self._inc_ends = ends
            self._inc_th = th

            ax.axhline(0, color=th["sub"], linewidth=.9, linestyle="--", alpha=.6)
            ax.set_ylabel("累计主力净额（亿元）", fontsize=9, color=th["sub"])

            # ★ 横轴固定为 9:30–15:00 完整时段（240 分钟），刻度等距。
            #   盘中数据只到当前分钟 → 曲线画到那里为止，右半段留白表示"尚未走到"，
            #   而不是把已有的数据拉伸铺满整宽（那会让人误判进度）。
            ax.set_xlim(0, TRADING_MINUTES - 1)
            # 刻度位置：与 TRADING_TICKS 一一对应，相邻间隔严格 30 分钟。
            # ★ 09:30 落在 0 号位（与首个数据点 09:31 同一格）—— 若按真实分钟
            #   放到 -1，matplotlib 会把 xlim 左端撑到 -1，横轴就不是 [0,239] 了
            #   （B8 实测踩过：标签越界一样会改视野）。
            tick_pos, tick_lab = [], []
            for lab in TRADING_TICKS:
                pos = _minute_index(lab)
                if pos is None:                       # 09:30 → 容错落在 0 号位
                    pos = 0
                tick_pos.append(pos)
                tick_lab.append(lab)
            ax.set_xticks(tick_pos)
            ax.set_xticklabels(tick_lab, fontsize=7.4, color=th["sub"])
            ax.set_xlim(0, TRADING_MINUTES - 1)       # 刻度可能撑动视野，再钉一次
            # ★★ v3.0.6（用户需求）：「网格线过多，干扰折线阅读」→ 弱化。
            #   浅灰**细虚线** + 低透明度，横纵都画但都比折线弱一档，
            #   让网格退成"参考刻度"而不是与曲线争视觉。
            #   set_axisbelow(True) 保证网格一定在曲线**之下**。
            ax.grid(True, color=th["grid"], linewidth=.6,
                    linestyle=(0, (2, 3)), alpha=.30)
            ax.set_axisbelow(True)

            # ★ 纵轴量程：按**当前已加载的数据**动态算（v1.1.7 用户需求）。
            #   必须放在所有 set_xticks / set_xlim **之后、午休标注之前**：
            #   · 之前：后面没有任何一步会再动 ylim，设定的值不会被覆盖；
            #   · 午休标注要取 ylim[0] 当下沿定位（见下），必须在它之前定好。
            #   量程取自**填充后**的实际绘制数据（h_in/h_out 的 ydata），
            #   而不是原始 d["series"] —— 两者在有空值时会差一截，
            #   用原始数据算会让量程比真实曲线略窄，峰谷仍然贴边。
            _ylim = _fan_ylim([ln.get_ydata() for ln in (h_in + h_out)])
            if _ylim is not None:
                ax.set_ylim(*_ylim)

            # ★★ v3.0.6（用户需求）：「Y 轴标签、网格线过多」→ 限档 + 弱化。
            #   · 纵轴最多 5 档（原来按 matplotlib 默认，密的时候能到 8~10 档）；
            #   · 刻度文字用弱色（th["sub"]）、刻度线缩短；
            #   · 去掉上/右边框，左/下边框调细调淡 —— 「不要厚重黑粗边框」。
            try:
                from matplotlib.ticker import MaxNLocator
                ax.yaxis.set_major_locator(MaxNLocator(nbins=5, prune="both"))
            except Exception:                            # noqa: BLE001
                pass
            ax.tick_params(axis="y", labelsize=7.4, colors=th["sub"], length=2)
            ax.tick_params(axis="x", length=2, colors=th["sub"])
            for _sp in ("top", "right"):
                ax.spines[_sp].set_visible(False)
            for _sp in ("left", "bottom"):
                ax.spines[_sp].set_color(th["sub"])
                ax.spines[_sp].set_linewidth(.6)
                ax.spines[_sp].set_alpha(.45)

            # ★★ v3.0.6（用户需求）：左上角标「时点 HH:MM · 右端为收盘金额」。
            #   旧版没有任何标题，读者得靠底部一行小字才知道右端标签是什么。
            #   ★ 用 transAxes 定位（与数据无关），曲线怎么缩放它都在左上角。
            _tlast = (times[-1] if times else "") or ""
            if _tlast:
                ax.text(0.006, 0.972,
                        f"时点 {_tlast}   ·   右端标签为收盘金额",
                        transform=ax.transAxes, fontsize=7.6, va="top", ha="left",
                        color=th["sub"], alpha=.9, zorder=6)

            # 午休分界：11:30(119) 与 13:01(120) 在轴上是相邻两格，光看刻度看不出
            # "中间断了两小时"。画一条淡色分界竖线，读者才知道此处是跳空的。
            ax.axvline(119.5, color=th["sub"], linewidth=.9, linestyle=":",
                       alpha=.5)
            ax.text(119.5, ax.get_ylim()[0], " 午休", fontsize=6.8,
                    color=th["sub"], alpha=.75, va="bottom", ha="left")

            # ★★ v2.0.5：曲线末端直接标注「板块名 收盘金额」，彻底替代底部图例。
            #   （v1.1.6 的图例带 / 列数二分 / 底部留白整套机制已随本版移除，
            #     演进理由与算法见 _end_labels 的 docstring。）
            #   顺序：tight_layout 排内边距 → _end_labels 右侧留标签带并画标注
            #   → 整幅 draw → 预建 blit 背景快照。
            # ★★ v3.0.6（用户需求）：「在图表侧边放精简图例，代替密密麻麻
            #   贴在折线旁边的文字」。末端现在只标高亮 10 条，其余 10 条若
            #   既无标签、又只能靠悬停，就成了"看得见线、叫不出名"。
            #   ★ 放在**轴下方**而不是右侧标签带：右侧要留给端点标注，
            #     两处都放会重新挤成一团 —— 那正是本次要修的问题。
            try:
                _rest_h = [h for h, dd in zip(h_in + h_out, ins + outs)
                           if not dd.get("_hl")]
                _rest_l = [dd["name"] for dd in (ins + outs)
                           if not dd.get("_hl")]
                if _rest_h:
                    _lg = ax.legend(_rest_h, _rest_l, loc="upper center",
                                    bbox_to_anchor=(0.5, -0.085), ncol=5,
                                    fontsize=6.5, frameon=False,
                                    handlelength=1.1, columnspacing=1.2,
                                    handletextpad=.45, borderaxespad=0.0)
                    for _t in _lg.get_texts():
                        _t.set_color(th["muted"])
                    _lg.set_zorder(1)
            except Exception:                            # noqa: BLE001
                pass

            self.fig.tight_layout(pad=.45)
            # 标签带的宽度必须每次从完整绘图区重算；增量刷新若从已收窄的位置
            # 再减一次，会让曲线区逐轮压缩到最小宽度。
            self._label_axes_base = ax.get_position().frozen()
            self._end_labels(ends, ax, th)
            # ★ v3.0.6：把全部曲线句柄交给覆盖层 —— 悬浮框据此在**所有**曲线里
            #   找最近的一条（末端只标了 10 条，其余 10 条靠它才读得到名字）。
            self._overlay.set_lines(h_in + h_out)
            self.draw()
            # ★ v1.2.2：整幅 draw 已到此结束、几何也已落定，**顺手把 blit 背景快照建好**
            #   （此刻 _vline 必为 None，背景天然不含竖线）。
            #   若不预建，第一次悬停时还得再付一次 120 ms 的整幅 draw ——
            #   表现为"鼠标刚移上去的第一下明显一顿"。这里预建后，
            #   首次悬停就是 0.16 ms 的纯 blit。
            self._ensure_blit_bg(ax)

        # -------------------------------------------------- 增量更新（v3.0.4）
        def try_update_series(self, data):
            """尝试**原地更新**曲线数据，而不是整幅重建。成功返回 True。

            ★★ 为什么需要它（高频刷新的核心优化）：
              `plot()` 开头是 `self.fig.clear()` —— 销毁并重建全部 artist。
              基线实测（bench_v304_load.py）：
                  单图整幅重绘   20.9 ms（24 线 × 240 点）
                  六图全量重绘  106.3 ms
              而盘中每轮刷新（目标 1~10 秒）其实**只有末端多了 1 个点**，
              曲线集合、名称、方向、配色都没变。用 set_data 改 20 条线的
              xdata/ydata 只需亚毫秒级，把 20.9ms 压到 ~1ms 量级。

            ★ 什么时候**必须**放弃增量（返回 False，退回 plot()）：
              ① 曲线数量或名称序列不一致 → 集合变了，artist 对不上；
              ② 方向/配色指纹变了；
              ③ 首次绘制（没有登记的现场）；
              ④ 任何异常 —— **安全第一**：宁可退回全量重建，也不要用
                 错位的 artist 画出错位的图（那是比慢严重得多的问题）。
            ★ 为什么不做"部分更新"（只更新变化的那几条）：曲线之间共享
              纵轴量程，量程一变所有曲线的视觉位置都变，逐条判断反而更复杂
              且容易不一致。整组更新是成本与正确性的最优平衡点。
            """
            try:
                if getattr(self, "_inc_handles", None) is None:
                    return False
                ins = [d for d in (data or []) if d.get("direction", "in") == "in"]
                outs = [d for d in (data or []) if d.get("direction", "in") == "out"]
                names = ([d["name"] for d in ins], [d["name"] for d in outs])
                # 指纹：曲线名称序列必须逐项一致（含顺序 —— 顺序决定配色）
                if names != getattr(self, "_inc_names", None):
                    return False
                h_in, h_out = self._inc_handles
                if len(h_in) != len(ins) or len(h_out) != len(outs):
                    return False

                times = (data[0].get("times") or []) if data else []
                n = max((len(d.get("series") or []) for d in data), default=0)
                if n <= 1:
                    return False
                xpos, x_mapped = _x_positions(times, n)

                ends = []
                for rows, handles in ((ins, h_in), (outs, h_out)):
                    for d, ln in zip(rows, handles):
                        s = _fill_gaps(d["series"])
                        # ★★ v3.0.5：与 plot() 同一口径 —— 只画有标签的点
                        #   （详见 _x_positions 与 plot() 内 _draw 的注释）
                        _k = min(len(s), len(xpos))
                        s = s[:_k]
                        xs = xpos[:_k]
                        ln.set_data(list(xs), s)
                        if s:
                            ends.append({"x": float(list(xs)[-1]),
                                         "y": float(s[-1]),
                                         "name": d["name"], "color": ln.get_color(),
                                         "s": s})

                # 悬停映射现场同步（否则竖线/标签会按旧时间轴换算）
                self._n = n
                self._times = list(times)
                self._xpos = list(xpos)
                self._x_mapped = x_mapped

                ax = self.fig.axes[0] if self.fig.axes else None
                if ax is None:
                    return False
                # 纵轴量程可能因新数据而扩展/收缩 —— 与 plot() 保持同一口径
                _ylim = _fan_ylim([ln.get_ydata() for ln in (h_in + h_out)])
                if _ylim is not None:
                    ax.set_ylim(*_ylim)

                # ★ 末端引线是"静态画一次"的 Line2D（见 _end_labels），
                #   量程一变它们就错位 —— 简单可靠的做法是**移除后重画**：
                #   引线只有个位数条，成本远低于整幅重建。
                for ln in list(getattr(self, "_inc_leader_lines", []) or []):
                    try:
                        ln.remove()
                    except Exception:                    # noqa: BLE001
                        pass
                self._inc_leader_lines = []
                self._inc_ends = ends
                # 覆盖层（Qt QPainter）重排标签 —— 亚毫秒，不碰 matplotlib
                self._end_labels(ends, ax, getattr(self, "_inc_th", None)
                                 or self._style(ax))
                # 引线登记（_end_labels 内部会新建，登记以便下次清理）
                self._inc_leader_lines = list(
                    getattr(self, "_last_leader_lines", []) or [])
                # set_data 只改 artist，不保证 QtAgg 在当前事件循环主动提交新帧；
                # 同时旧 blit 背景不能再用于悬停，否则会恢复旧曲线像素。
                self._invalidate_blit()
                self.draw_idle()
                return True
            except Exception:                            # noqa: BLE001
                return False

        # ------------------------------------------------ 末端标签（v2.0.5/2.0.8）
        def _end_labels(self, ends, ax, th):
            """每条曲线右端终点处直接标注「板块名 收盘金额（XX.X 亿）」，
            彻底替代底部图例（v2.0.5，用户需求）。

            ★★ v2.0.8：标签改为 **Qt 覆盖层（FanLabelOverlay）绘制**，
              不再是 matplotlib Text —— 悬停换分钟时 QPainter 重画 20 条
              字符串 <0.5ms，而 Text artist 的 draw_artist 要 35ms
              （字符串一变、字形布局全重来），这正是"光标不跟手"的元凶。
              本方法只负责：量标签带宽度 → 收窄轴 → 把标签项交给覆盖层。

            参考实时盯盘软件（同花顺 / 东财分时盘口右侧）的通行做法：
              · 标签钉在绘图区右侧的**独立标签带**里 —— 位置稳定、
                与曲线区物理隔离，永不遮挡曲线；
              · 文字颜色与曲线一致（流入暖色族 / 流出冷色族）；
              · 20 条收盘金额常挤在一起，用**最小间距收敛**（按终点值升序
                自下而上推开，相邻至少隔一行字高，超出轴顶整体下压）防叠字；
              · 标签带宽度**按最宽标签实测自适应**（90~280px），不再固定 22%
                —— 宽屏下曲线区拉到最长，消除大片留白（用户反馈）。
            """
            # ★★ v3.0.6（用户需求）：**只标注高亮曲线** —— 前 5 流入 + 前 5 流出
            #   （高亮规则见 _draw 里的 FAN_HL_N）。旧版把 20 条**全部**标在右端，
            #   收盘时"半导体材料/锂电池"这类极端跳变的线挤成一堆，
            #   就是用户报的"右侧文字大量重叠、扎堆看不清"。
            #   其余曲线仍画出来（弱化显示），悬停时照常联动读数。
            ends = [d for d in ends if d.get("hl")]
            if not ends:
                self._overlay.set_items([])
                return
            # ---- ① 量最宽标签（Qt 字体度量）→ 标签带自适应收窄 ----
            qf = QFont("Microsoft YaHei", 8)
            from PySide6.QtGui import QFontMetrics
            fm = QFontMetrics(qf)
            wmax = max((fm.horizontalAdvance(
                f'{"▲" if d.get("is_up") else "▼"} {d["name"]} {d["y"]:.1f}亿')
                for d in ends), default=110.0)
            # 完整 plot() 会保存未腾标签带的基准位置；增量路径必须从该基准
            # 重算，不能在已经缩窄的当前位置上反复减去标签带宽度。
            p0 = getattr(self, "_label_axes_base", None) or ax.get_position().frozen()
            fig_w_px = max(60.0, self.fig.get_figwidth() * self.fig.dpi)
            # ★ v3.0.6：留白从 +26 加到 +40、上限 280 → 340。
            #   实测（uicheck_0_总览.png）：「▲汽车电子电气系统 -33.0亿」这类
            #   13 字标签会顶到画布右缘，末尾数字被切掉半个 —— 标签带必须
            #   按**最宽标签实测宽度**给足余量，而不是按经验值凑。
            band_px = min(max(wmax + 40.0, 100.0), 340.0)
            _new_w = max(0.45, p0.width - band_px / fig_w_px)
            ax.set_position([p0.x0, p0.y0, _new_w, p0.height])
            fig_h_px = max(60.0, self.fig.get_figheight() * self.fig.dpi)
            ax_h_px = max(60.0, p0.height * fig_h_px)
            gap_frac = 12.5 / ax_h_px            # 相邻标签最小间距 ≈ 一行字高
            pad_top, pad_bot = gap_frac * .6, gap_frac * .3

            # ---- ② 终点值 → 轴内分数（0~1），按值升序 ----
            y0, y1 = ax.get_ylim()
            span = (y1 - y0) or 1.0
            x0, x1 = ax.get_xlim()
            xspan = (x1 - x0) or 1.0
            items = sorted(
                ({"fx": (min(max(e["y"], y0), y1) - y0) / span,
                  "gx": (e["x"] - x0) / xspan, **e}
                 for e in ends),
                key=lambda d: d["fx"])
            if not items:
                self._overlay.set_items([])
                return
            # ---- ③ 最小间距收敛 ----
            pos = [d["fx"] for d in items]
            for i in range(1, len(pos)):
                pos[i] = max(pos[i], pos[i - 1] + gap_frac)
            over = pos[-1] - (1.0 - pad_top)
            if over > 0:
                pos = [q - over for q in pos]
            pos[0] = max(pos[0], pad_bot)
            for i in range(1, len(pos)):
                pos[i] = max(pos[i], pos[i - 1] + gap_frac)
            # ---- ④ 引线（matplotlib 静态画一次）+ 标签交给覆盖层 ----
            t_by_name = {d["name"]: d for d in items}
            # ★ v3.0.4：登记本次画的引线，供 try_update_series() 移除重画。
            #   引线依赖 ylim，量程一变就错位；条数极少（个位数），
            #   移除重画的成本远低于整幅重建。
            self._last_leader_lines = []
            for d, q in zip(items, pos):
                d["q"] = q
                displaced = abs(q - d["fx"]) > gap_frac * .35
                if displaced:
                    _ln, = ax.plot([d["gx"], 1.004], [d["fx"], q],
                                   transform=ax.transAxes, color=d["color"],
                                   linewidth=.7, alpha=.55, clip_on=False, zorder=3)
                    self._last_leader_lines.append(_ln)
            self._overlay.set_items([
                {"name": d["name"], "color": d["color"],
                 "series": d.get("s") or [], "end": d["y"], "q": d["q"],
                 # ★ v3.0.6：带上方向 —— 标签前缀用 ▲/▼ 明示流入/流出。
                 #   颜色族不再独占方向语义（高区分度配色下同族颜色会跨色相）。
                 "up": bool(d.get("is_up"))}
                for d in items])

        def _apply_hover_labels(self, k, ydata=None):
            """悬停联动主体（v2.0.6 引入，v2.0.8 改走 Qt 覆盖层）。

            k = 数据行号（_on_motion 已算好）；k=None 表示鼠标移出 →
            恢复各曲线终点时刻的收盘值。QPainter 重画 <0.5ms，
            与竖线的轴区 blit 并行不悖（分层：覆盖层在画布之上）。
            """
            self._overlay.set_hover(k, ydata)
    class IndexChart(MplCanvas):
        """指数分时（v1.1.5：上证 + 东财全A 双线）。

        ★ 参考 HTML 版顶栏「上证指数 + 东财全A」的做法：
          上证代表上交所，全A（secid 47.800000）覆盖沪深全市场 —— 两个一起看，
          才能区分「是上交所权重在动，还是全市场都在动」。
          两条线各有自己的基准价（各自开盘价），都以涨跌幅 % 呈现，
          这样不同点位量级的指数可以同框比较。
        """

        def plot(self, idx, idx_all=None):
            """idx / idx_all: [(HH:MM, 点位)]；idx_all 为空则只画上证。"""
            self.fig.clear()
            # ★ v3.0.4：登记增量现场（见 try_update_series）
            self._inc_ready = False
            self._inc_line1 = None
            self._inc_line2 = None
            self._inc_fill1 = None
            self._inc_have2 = False
            ax = self.fig.add_subplot(111)
            th = self._style(ax)
            if not idx and not idx_all:
                self.blank()
                return

            def _series(rows):
                """转成涨跌幅序列，返回 (times, chg)。"""
                if not rows:
                    return [], []
                t = [x[0] for x in rows]
                v = [x[1] for x in rows]
                base = v[0]
                return t, [(x - base) / base * 100 if base else 0.0 for x in v]

            t1, c1 = _series(idx)
            t2, c2 = _series(idx_all)

            # 上证：主题强调色（与 HTML 版顶栏一致，主指数用蓝）
            if c1:
                self._inc_line1, = ax.plot(range(len(c1)), c1, color=th["accent"],
                                           linewidth=1.8, label="上证指数")
                self._inc_fill1 = ax.fill_between(range(len(c1)), c1, 0,
                                                  color=th["accent"], alpha=.08)
            # 东财全A：第二条线用可区分的橙色，虚线以便重叠时也分得清
            if c2:
                self._inc_line2, = ax.plot(range(len(c2)), c2, color="#f0932b",
                                           linewidth=1.6,
                                           linestyle=(0, (5, 2.2)), label="东财全A")
            self._inc_have2 = bool(c2)

            ax.axhline(0, color=th["sub"], linewidth=.9, linestyle="--", alpha=.7)

            times = t1 or t2
            if times:
                k = max(1, len(times) // 8)
                ax.set_xticks(list(range(0, len(times), k)))
                ax.set_xticklabels(times[::k], fontsize=8)
            ax.set_ylabel("涨跌幅 %", fontsize=9, color=th["sub"])
            if c1 and c2:
                ax.legend(fontsize=8, frameon=False, loc="upper left", ncol=2,
                          labelcolor=th["fg"])
            self.fig.tight_layout(pad=.45)
            self.draw()
            # ★ 只有在**两条线都存在**时才允许增量：只画上证时若后面来了全A，
            #   需要新建 artist，交给完整重建更安全。
            self._inc_ready = bool(c1 and c2)

        def try_update_series(self, idx, idx_all=None):
            """指数图增量更新（v3.0.4）。成功返回 True。

            ★ 为什么值得做：实测 IndexChart 整幅重建 **64ms**，与它只有 2 条线
              完全不成比例 —— 成本几乎全在 fig.clear() + tight_layout + draw()。
              盘中每秒刷新时，指数只是末端多一个点，用 set_data 可把 64ms 压到
              ms 级。占六图总成本的 22.9%（bench_v304_charts.py）。
            ★ 保守点：只在"上次也画了两条线"且本次**两条线都有数据**时增量；
              任一侧从无到有/从有到无都退回重建 —— artist 增删不在增量范围内。
            """
            try:
                if not getattr(self, "_inc_ready", False):
                    return False
                if not idx or not idx_all:
                    return False
                if self._inc_line1 is None or self._inc_line2 is None:
                    return False

                def _series(rows):
                    t = [x[0] for x in rows]
                    v = [x[1] for x in rows]
                    base = v[0]
                    return t, [(x - base) / base * 100 if base else 0.0 for x in v]

                t1, c1 = _series(idx)
                t2, c2 = _series(idx_all)
                if not c1 or not c2:
                    return False

                self._inc_line1.set_data(range(len(c1)), c1)
                self._inc_line2.set_data(range(len(c2)), c2)
                # ★ fill_between 的 PolyCollection 必须整块换掉：
                #   它没有 set_data，只能用 get_paths/remove 重建。
                #   量级只有 240 点，成本可忽略。
                ax = self.fig.axes[0] if self.fig.axes else None
                if ax is None:
                    return False
                if self._inc_fill1 is not None:
                    try:
                        self._inc_fill1.remove()
                    except Exception:                    # noqa: BLE001
                        pass
                self._inc_fill1 = ax.fill_between(
                    range(len(c1)), c1, 0,
                    color=self._inc_line1.get_color(), alpha=.08)

                times = t1 or t2
                if times:
                    k = max(1, len(times) // 8)
                    ax.set_xticks(list(range(0, len(times), k)))
                    ax.set_xticklabels(times[::k], fontsize=8)
                ax.set_xlim(-0.5, max(len(c1), len(c2)) - 0.5)
                # 新分钟可能突破旧纵轴量程；增量路径也必须与完整 plot() 一样
                # 更新可视范围并显式请求 QtAgg 刷新。
                ax.relim()
                ax.autoscale_view(scalex=False, scaley=True)
                self.draw_idle()
                return True
            except Exception:                            # noqa: BLE001
                return False


    # ---------------------------------------------------------------- 题材簇速览
    class ThemeBoard(MplCanvas):
        """主线簇总览图：**左流出 / 右流入** 双面板，两栏柱条一律**从左往右**长。

        ★ 版式演进（三次，别走回头路）：

          v1.0.4 —— 共用零轴的蝴蝶图（左流出向左伸 / 右流入向右伸），左右独立刻度。
            问题：量级差 6~15 倍时，共用刻度下流出柱不到 14px，等于看不见。

          v1.1.8 —— 改成「红 8 绿 8 对称」：两侧同条数、共用一把刻度、
            柱长加最小可见长度（保序映射）。
            问题：共用刻度下流入最大 1828 亿 是流出最大 116 亿 的 15.8 倍，
            流出柱被压得极短；用户反馈「太不协调了」。

          v1.2.0（当前）—— 用户三条明确要求：
            ① **相对长度**：两侧**各自按本侧最大值归一**，每侧最长柱都铺满
               （各占网格比例 = 本侧 value / 本侧 max）。两侧量级差异改由
               **面板标题**里的「最长 xxx 亿」说明，不靠柱长体现。
               → 版式好看、两侧都撑得开；代价是**两侧柱长不能横向比金额**，
                 所以标题必须写清楚各自的最长值，避免误读。
            ② **流出柱也向右伸**：两栏都是「左边起点 → 向右长」，
               x 轴不再有零轴"脊"，两栏并排、方向一致。
            ③ **名称贴左侧、金额贴柱尾右侧**（用户选定）—— 排版在两栏之间对齐。
        """

        def plot(self, data, topn=8):
            """data: 簇列表；topn: **每侧固定画几条**（红 8 / 绿 8 对称）。

            ★ 用户需求链（别丢）：
              ①「题材簇也改成红 8 绿 8 对称好看」→ 两侧同条数、行高一致；
              ②「大小太不协调了，相对长度就行」→ 两侧各自按本侧最大值归一；
              ③「净流出那一栏的条柱也往右」→ 流出柱改为向右伸，两栏同向。

            ★ 两侧凑满 topn 条：不足的一侧从**同方向**未入选的簇里补
              （按净额绝对值最小优先）。补位**不跨方向借** —— 正净额的簇画到
              "流出"面板上，柱子语义自相矛盾，那种"对称"是假的。
              真凑不满就两侧一起收缩到较短的那侧（宁可两边都少，不要一多一少），
              并在标题里标注「含 N 条补位」。
            """
            self.fig.clear()
            if not data:
                self.blank()
                return

            rows = []
            for i, c in enumerate(data):
                if not isinstance(c, dict):
                    continue
                try:
                    tot = float(c.get("total_yi", 0) or 0)
                except Exception:                            # noqa: BLE001
                    tot = 0.0
                mem = c.get("members") or []
                lead = ""
                if mem and isinstance(mem[0], dict):
                    lead = str(mem[0].get("name", ""))
                rows.append({
                    "name": str(c.get("seed") or c.get("core") or f"簇{i + 1}"),
                    "n": c.get("n_members", len(mem) if isinstance(mem, list) else 0),
                    "tot": tot,
                    "lead": lead,
                })
            if not rows:
                self.blank()
                return

            # ---- 先按方向分池（v1.1.8：两侧**同一条数**，红 8 / 绿 8）
            #   分两步分配，避免"两侧都借、借出重复"：
            #     ① 先按各自方向取前 topn 条（本侧优先，体量大的在前）；
            #     ② 某侧不足时，从**尚未被任何一侧选中**的簇里补 —— 按
            #        「净额绝对值最小」优先（用户选定）。补位不跨方向借：
            #        正净额的簇画到"流出"面板上，柱子方向与标题语义相反，
            #        那种"对称"是假的（看着齐，读起来自相矛盾）。
            #        真不够就**少画几条**并在标题里说明 —— 少画是真实情况，乱补是造假。
            pool_in = sorted([r for r in rows if r["tot"] > 0], key=lambda r: -r["tot"])
            pool_out = sorted([r for r in rows if r["tot"] <= 0], key=lambda r: r["tot"])

            ins = pool_in[:topn]
            outs = pool_out[:topn]
            # 已被选中的名字集合：补位时用它与各侧去重，杜绝同一条目出现在两侧
            used = {id(r) for r in ins} | {id(r) for r in outs}

            def _fill(pool, got, k):
                """从同侧剩余里补到 k 条（不跨向）。返回补位条数。"""
                if len(got) >= k:
                    return 0
                n = 0
                for r in pool[len(got):]:
                    if len(got) >= k:
                        break
                    if id(r) in used:                        # 已被对侧选中 → 跳过
                        continue
                    got.append(r)
                    used.add(id(r))
                    n += 1
                return n

            n_fill_in = _fill(pool_in, ins, topn)
            n_fill_out = _fill(pool_out, outs, topn)

            # ★ 两侧行数严格相等才谈得上"对称"。
            #   若某一侧连补位都凑不满（当天该方向的簇真的不够），就两侧一起
            #   收缩到较短的那一边 —— **宁可两侧都少，也不要一边 8 一边 5**。
            n_row = min(len(ins), len(outs))
            if n_row:
                ins, outs = ins[:n_row], outs[:n_row]
            ins.sort(key=lambda r: -r["tot"])
            outs.sort(key=lambda r: r["tot"])

            th = self.th
            ax1 = self.fig.add_subplot(1, 2, 1)
            ax2 = self.fig.add_subplot(1, 2, 2)
            for a in (ax1, ax2):
                self._style(a)
                a.set_yticks([])
                a.set_xticks([])
                a.grid(False)
                for s in ("left", "bottom", "right"):
                    a.spines[s].set_visible(False)

            # ---- ★ v1.2.0：**相对长度** —— 两侧各自按本侧最大值归一
            #
            #   用户反馈 v1.1.8 的「共用刻度」版："大小太不协调了，相对长度就行"。
            #   原因是量级差太大：实测 2026-09-18 流入最大 1828.3 亿 vs 流出最大
            #   115.7 亿，相差 15.8 倍。共用一把尺时流出柱最长也只占 6%，8 条挤在
            #   左下角一小坨，与右侧 8 根长柱并列非常不协调。
            #
            #   现在每侧**独立归一**：柱长占比 = |value| / 本侧 max。每侧最长柱都
            #   铺满可用宽度，两侧视觉重量相当。
            #
            #   ★ 代价必须说清楚：**两侧柱长不能再横向比金额**了。所以
            #     ① 面板标题里写明「最长 xxx 亿」——量级差异改由文字承载；
            #     ② 每根柱尾仍标**真实金额**（标签不参与归一，永远是真值）。
            #     这样"看着协调"和"读着准确"两头都不丢。
            #
            #   ★ 最短柱仍要保下限：归一后最短那条可能只有 0.4% 宽（流出侧最末
            #     7.2 亿 vs 本侧 115.7 亿 = 6.2%……但若某侧极值悬殊会更小），
            #     所以保留**保序**的线性映射，把 [lo,hi] 映射到 [MIN_FRAC, 1.0]。
            #     与 v1.1.8 的三分支保序映射同一个道理：**上端固定、保序**，
            #     绝不能把最小值单抬到下限了事（那会出现负斜率、大小反转）。
            MIN_FRAC = 0.10           # 最短柱不小于本侧满格的 10%（视觉可辨）

            def _norm_of(rs):
                """返回该侧 (真实值 → 归一化长度 [MIN_FRAC, 1]) 的**保序**映射。

                ★ 保序是硬约束：变换后的大小关系必须与真实值一致。
                  记侧内绝对值区间 [lo, hi]，映射到 [MIN_FRAC, 1.0]：
                      k = (1 - MIN_FRAC) / (hi - lo)     ← 恒正，不会反转
                      f(v) = MIN_FRAC + (|v| - lo) * k
                  边界：hi == lo（单条 / 全等值）→ 一律画满格 1.0；
                        hi <= 0（无有效值）→ 返回 0。
                  符号在调用处处理（两侧都是向右伸，不需要负长度）。
                """
                if not rs:
                    return lambda v: 0.0
                absv = [abs(r["tot"]) for r in rs]
                lo, hi = min(absv), max(absv)
                if hi <= 0:
                    return lambda v: 0.0
                if hi <= lo:                                # 单条 / 全等值 → 满格
                    return lambda v: 1.0
                k = (1.0 - MIN_FRAC) / (hi - lo)
                def _f(v):
                    a = MIN_FRAC + (abs(v) - lo) * k
                    return max(0.0, min(1.0, a))
                return _f

            _norm_fn = {"in": _norm_of(ins), "out": _norm_of(outs)}

            # ---- 版面：名称 / 柱区 / 金额 **三列都在轴内**（v1.2.0 修正）
            #
            #   ★ 第一版把名称列画在 axes 之外（x<0）、靠 subplots_adjust 留边距，
            #     实测两个毛病：① 左页边 0.055 只够 ~50px，中文名被画布切掉半边；
            #     ② 名称右对齐到 0 而柱条也从 0 起，视觉上柱子"盖住"了名字。
            #   改成三列**全部落在轴 x 范围内**（轴 xlim 固定 [0,1]），
            #   外部页面边距只需常规值 —— 文字永不越界。
            #
            #     NAME 列  x ∈ [0.00, 0.30]   贴 x=0.30 右对齐
            #     BAR  区  x ∈ [0.32, 0.82]   满格 = 0.50 宽，从 0.32 向右长
            #     VAL  列  x ∈ [0.84, 1.00]   贴柱尾右侧，左对齐
            #
            #   ★ 两栏用**同一套 x 坐标**，所以排版逐像素对齐（不随各自 max 变）。
            #
            #   ★★ 三列宽度的标定方法（第一版又踩了）：
            #     金额列最宽形态 =「20只 +1828.3亿」≈ 12 个全角字符，
            #     8.2pt 下约 95px。单栏轴宽 ≈ (1850*0.94/2)/(1+0.22) ≈ 700px，
            #     所以金额列至少要 95/700 ≈ 0.14 的轴宽 —— 且**是满格时才需要**，
            #     因为标签跟柱尾走。第一版 BAR_W=0.50、X0=0.32 → 满格柱尾落在
            #     0.82，只剩 0.18……看似够，但 0.18*700=126px 里还有 0.02 的间隙，
            #     实测仍被裁（"+1828.3亿" 显示成 "+182"）。原因是最长那条标签
            #     前面还有「20只 」，全角字符按 2 倍宽算 → 实际需 ~110px。
            #     现在 BAR_W 收到 0.44、X0 提到 0.30 → 满格柱尾 0.74，
            #     金额列 0.74~1.00 = 0.26 轴宽 ≈ 182px，绰绰有余。
            X0 = 0.30                 # 柱区起点
            BAR_W = 0.44              # 柱区满格宽度
            X_NAME = X0 - 0.02        # 名称右对齐位置

            def _draw(ax, rs, color, fills, title):
                """两栏统一：**柱条一律从 x=X0 向右伸**（用户 v1.2.0 要求）。

                ★ v1.1.8 时左栏是向左伸的（蝴蝶图对称），用户明确改成
                  「净流出那一栏的条柱也往右」—— 两栏同向，x 轴不再有零轴"脊"。
                """
                if not rs:
                    ax.set_axis_off()
                    ax.text(.5, .5, "当日无该方向主线", ha="center", va="center",
                            color=th["sub"], fontsize=10, transform=ax.transAxes)
                    return

                _f = _norm_fn["in" if "流入" in title else "out"]
                mx = max((abs(r["tot"]) for r in rs), default=1.0) or 1.0

                for k, r in enumerate(rs):
                    v = r["tot"]
                    w = _f(v) * BAR_W                      # 归一化比例 × 柱区宽
                    ax.barh(k, w, left=X0, height=.62, color=color,
                            alpha=.90, zorder=3)
                    nm = r["name"] if len(r["name"]) <= 8 else r["name"][:8] + "…"
                    # ★ 名称贴柱区左侧（右对齐），金额贴柱尾右侧（左对齐）
                    ax.text(X_NAME, k, nm, va="center", ha="right",
                            fontsize=8.4, color=th["fg"], zorder=4)
                    ax.text(X0 + w + 0.02, k, f'{r["n"]}只  {v:+.1f}亿', va="center",
                            ha="left", fontsize=8.2, color=color, zorder=4)

                ax.set_xlim(0.0, 1.0)
                ax.set_ylim(len(rs) - .5, -.5)               # 两侧行数相同 → 行高一致
                total = sum(r["tot"] for r in rs)
                got = len(rs) - fills
                note = f" · 含 {fills} 条补位" if fills else ""
                # ★ 相对长度下两侧不可比金额 → 标题必须写明本侧「最长」值
                ax.set_title(f'{title}主线 {got} 条{note} · 合计 {total:+.0f} 亿 '
                             f'· 最长 {mx:.0f} 亿',
                             fontsize=8.6, color=color, pad=5)

                # ★ 柱区起点竖线（两栏同向后的"零起点"参考）
                ax.axvline(X0, color=th["sub"], linewidth=.8, alpha=.45, zorder=2)

            # 左：流出（绿）／右：流入（红）—— **都向右伸**
            _draw(ax1, outs, th["dn"], n_fill_out, "流出")
            _draw(ax2, ins, th["up"], n_fill_in, "流入")

            # ★ 相对长度必须显式说明，否则用户会拿两栏柱长去比金额
            ax1.text(.5, -.085, "左绿 = 流出 / 右红 = 流入，两栏柱条均向右；"
                                "柱长按各栏最大值归一（两侧不可横向比较金额，"
                                "真实金额见柱尾标签）",
                     transform=ax1.transAxes, ha="center", va="top",
                     fontsize=7.0, color=th["sub"], alpha=.85)

            # 三列都在轴内 → 页面边距只需常规值
            self.fig.subplots_adjust(left=.045, right=.985, top=.92, bottom=.12,
                                     wspace=.22)
            self.draw()


    # ---------------------------------------------------------------- 主角更替
    class LeaderGrid(MplCanvas):
        """每个时间片一行，行内三个色块（辨识度前三）+ 一个信息列。

        ★ 色块高度收紧到 y+0.06 起、占 0.88 行高，行与行之间几乎无缝，
          不再在一屏里留出大片横向空白。
        ★ 右侧新增「三强合计 / 环比」信息列：
            Σ 净额  —— 该时刻三个主角的净额之和，衡量主线整体吸金强度
            环比    —— 与上一时刻相比的增减，能看出主线是在变强还是熄火
          这是原版没有、但一眼有用的信息，正好把右侧的空白利用起来。
        """

        def plot(self, data):
            self.fig.clear()
            if not data:
                self.blank()
                return
            th = self.th
            ax = self.fig.add_subplot(111)
            ax.set_facecolor(th["bg"])
            ax.set_axis_off()
            rows = len(data)
            ax.set_xlim(-.30, 3.62)
            ax.set_ylim(0, rows)

            prev_sum = None
            # data 按时间正序；画的时候自上而下 = 时间从早到晚，所以反转
            for r, item in enumerate(data):
                y = rows - 1 - r
                top = item["top"]
                tot = sum(c["net"] for c in top)
                for c, cell in enumerate(top):
                    v = cell["net"]
                    color = th["up"] if v >= 0 else th["dn"]
                    ax.add_patch(matplotlib.patches.FancyBboxPatch(
                        (c + .015, y + .06), .97, .88,
                        boxstyle="round,pad=0.006,rounding_size=0.05",
                        linewidth=0, facecolor=color, alpha=.93))
                    nm = cell["name"]
                    if len(nm) > 6:
                        nm = nm[:6] + "…"
                    ax.text(c + .5, y + .62, nm, ha="center", va="center",
                            color="white", fontsize=8.4, fontweight="bold")
                    ax.text(c + .5, y + .28, f"{v:+.2f}", ha="center", va="center",
                            color="white", fontsize=7.4, alpha=.92)

                # 时间标签（左侧）
                ax.text(-.05, y + .5, item["time"], ha="right", va="center",
                        color=th["sub"], fontsize=8.2)

                # 信息列：三强合计 + 环比上一时刻
                ax.text(3.12, y + .62, f"Σ {tot:+.1f}", ha="center", va="center",
                        color=th["up"] if tot >= 0 else th["dn"], fontsize=8.2,
                        fontweight="bold")
                if prev_sum is None:
                    d_txt, d_col = "—", th["muted"]
                else:
                    diff = tot - prev_sum
                    d_txt = f"{diff:+.1f}"
                    d_col = th["up"] if diff >= 0 else th["dn"]
                ax.text(3.12, y + .28, f"环比 {d_txt}", ha="center", va="center",
                        color=d_col, fontsize=7.2, alpha=.92)
                prev_sum = tot

            self.fig.tight_layout(pad=.30)
            self.draw()


    # ---------------------------------------------------------------- 阶段对比
    class StageGrid(MplCanvas):
        """一天 n 个时点，每格列出该时点的流入 Top{k} + 流出 Top{k}。

        ★ 量柱是这一格的主角：名称右对齐贴柱尾、柱子长度正比于金额、
          数值右对齐在格末。三个元素对齐在同一条竖线上，扫一眼就能比出
          哪个时点资金最集中、哪个时点开始撤。
        """

        # 版面常量（相对**格宽**的比例，不是整图比例！）。
        #   ★ 踩过的坑：第一版把 NAME_W/VAL_W 当成整图比例来算，
        #     于是 area_w = cell_w - .07 - .30 - .17 - .05 = 0.333 - 0.59 < 0，
        #     柱宽被 max(.004, ...) 兜成 0.004 —— 肉眼看不到任何量柱。
        #     这里的三个比例之和必须 < 1，且按格宽缩放。
        NAME_R = .34          # 名称区占格宽比例（右对齐贴柱尾）
        VAL_R = .17           # 数值区占格宽比例（右对齐贴格右）
        PADL_R = .03          # 左侧留白占格宽比例

        def _one(self, ax, cx, cy, cell_w, item, k):
            """画一格。cx/cy 是格左下角，cell_w 是格宽（高固定 1.0）"""
            th = self.th
            ins = item.get("rows") or []
            outs = item.get("outs") or []
            rows = ins + outs
            if not rows:
                return

            mx = max([abs(r["net"]) for r in rows] + [0.01])
            pad_l = cell_w * self.PADL_R
            name_w = cell_w * self.NAME_R
            area_x = cx + pad_l + name_w          # 柱起点
            # 名称区与数值区都按格宽缩放，剩下的才是柱区 —— 保证恒为正
            area_w = max(.01, cell_w * (1 - self.PADL_R - self.NAME_R - self.VAL_R))

            # 表头：时点 + 该时点流入合计
            ax.text(cx + pad_l, cy + .965, item["time"], fontsize=10,
                    color=th["fg"], fontweight="bold", va="center")
            tot = sum(r["net"] for r in ins)
            ax.text(cx + cell_w * .30, cy + .965, f'流入合计 {tot:+.0f} 亿',
                    fontsize=8.2, color=th["up"], va="center")

            # ★ 内容必须在格内尽量铺满：上沿 .91、下沿 .04，只用掉 0.87 格高。
            #   之前是 .82 起、每行 step=.70/12 —— 内容只占 0.80 格高，
            #   行与行之间留出 0.20 格的空白，一屏里就是 80+px 的横向白带。
            span = .87 / max(1, len(rows))
            bh = span * .60
            n_in = len(ins)
            for j, r in enumerate(rows):
                yy = cy + .91 - (j + .5) * span
                v = r["net"]
                # 流入段与流出段之间加一条分隔线，一眼分清上下半段
                if j == n_in and n_in and len(outs):
                    sep = cy + .91 - j * span
                    ax.plot([cx + pad_l, cx + cell_w - pad_l], [sep, sep],
                            color=th["axis"], linewidth=.8, alpha=.9, zorder=2)
                col = th["up"] if v >= 0 else th["dn"]
                # 名称（右对齐，贴柱尾）
                nm = r["name"]
                if len(nm) > 6:
                    nm = nm[:6] + "…"
                ax.text(cx + pad_l + name_w, yy, nm, fontsize=7.6,
                        color=th["fg"], ha="right", va="center")
                # 量柱（长度正比于金额）
                bw = max(.002, abs(v) / mx * area_w)
                ax.add_patch(matplotlib.patches.Rectangle(
                    (area_x, yy - bh / 2), bw, bh,
                    linewidth=0, facecolor=col, alpha=.90))
                # 数值（右对齐贴格右缘）
                ax.text(cx + cell_w - pad_l, yy, f"{v:+.1f}", fontsize=7.6,
                        color=col, ha="right", va="center")

        def plot(self, data, k=6):
            self.fig.clear()
            if not data:
                self.blank()
                return
            th = self.th
            ax = self.fig.add_subplot(111)
            ax.set_facecolor(th["bg"])
            ax.set_axis_off()
            cols = 3 if len(data) > 2 else len(data)
            rows = (len(data) + cols - 1) // cols
            cell_w = 1.0 / cols
            ax.set_xlim(0, 1.0)
            ax.set_ylim(0, rows)

            for t in range(1, cols):
                ax.axvline(t * cell_w, color=th["grid"], linewidth=.9, alpha=.85)
            for t in range(1, rows):
                ax.axhline(t, color=th["grid"], linewidth=.9, alpha=.85)

            for idx, item in enumerate(data):
                cx = (idx % cols) * cell_w
                cy = rows - 1 - (idx // cols)
                self._one(ax, cx, cy, cell_w, item, k)

            self.fig.tight_layout(pad=.35)
            self.draw()


    # ---------------------------------------------------------------- 流入流出对比
    class FlowBarChart(MplCanvas):
        """中心对称双向条形图：右侧红=净流入 TopN，左侧绿=净流出 TopN。

        ★ 这是总览补齐"只看涨、看不到跌"的核心图。
          左右共用一条零轴，一眼就能比较「流入最强」与「流出最猛」的量级。

        标注策略：名称+数值合并成一个标签，贴在**条形外侧端**。
          （曾试过"名称靠中轴、数值在端点"，结果流入的名称画到了左侧流出区，
            两侧互相串位 —— 所以名称必须跟着自己那根条形走。）
        横轴范围固定为 ±2.1×maxabs：文本宽度（磅）与数据量纲无关，但这个比例
          能保证最长的名称也放得下，且与 maxabs 的大小无关。
        """

        def plot(self, data_in, data_out, topn=10):
            self.fig.clear()
            ax = self.fig.add_subplot(111)
            th = self._style(ax)
            ax.grid(False)

            din = [d for d in (data_in or []) if d.get("net", 0) > 0][:topn]
            dout = [d for d in (data_out or []) if d.get("net", 0) < 0][:topn]
            if not din and not dout:
                self.blank()
                return

            n = max(len(din), len(dout))
            maxabs = max([abs(d["net"]) for d in din + dout] + [0.01])
            off = maxabs * .022

            def _name(x):
                x = str(x)
                return x if len(x) <= 7 else x[:7] + "…"

            def _draw(rows, sign):
                for k, d in enumerate(rows):
                    v = d["net"]
                    if sign > 0:
                        # 流入：条形 0 → v（向右），标签贴在右端之外
                        ax.barh(k, v, height=.62, color=th["up"], alpha=.92, zorder=3)
                        ax.text(v + off, k, f'{_name(d["name"])} {v:+.1f}',
                                va="center", ha="left", fontsize=7.8,
                                color=th["up"], zorder=4)
                    else:
                        # 流出：条形 v → 0（向左），标签贴在左端之外
                        ax.barh(k, v, height=.62, color=th["dn"], alpha=.92, zorder=3)
                        ax.text(v - off, k, f'{_name(d["name"])} {v:+.1f}',
                                va="center", ha="right", fontsize=7.8,
                                color=th["dn"], zorder=4)

            _draw(din, +1)
            _draw(dout, -1)

            ax.axvline(0, color=th["axis"], linewidth=1.1, zorder=5)
            ax.set_xlim(-maxabs * 2.1, maxabs * 2.1)
            ax.set_ylim(n - 0.4, -0.6)          # 第 1 名在最上方

            ax.set_yticks([])
            tick_v = maxabs * .5
            ax.set_xticks([-tick_v, 0, tick_v])
            ax.set_xticklabels([f"{-tick_v:.0f}", "0", f"{tick_v:.0f}"], fontsize=7.5)
            for s in ("left", "bottom"):
                ax.spines[s].set_visible(False)
            ax.tick_params(axis="x", colors=th["sub"], length=0)

            ax.set_title("← 净流出（亿）        净流入（亿）→", fontsize=9,
                         color=th["sub"], pad=6)
            self.fig.tight_layout(pad=.38)
            self.draw()
    return (MplCanvas, FanChart, IndexChart, ThemeBoard,
            LeaderGrid, StageGrid, FlowBarChart)


# ---------------------------------------------------------------- 资金曲线
# 曲线配色序列（参考 HTML 版 UP_SEQ / DN_SEQ）：
#   20 条线用同一个红/绿会糊成一片，改成像 HTML 版那样按序取不同色相 ——
#   同属"涨"的色系内做区分，既保持红涨绿跌的语义，又能一眼追踪单条线。
#   ★ v1.2.1：配色方向已固定为红涨绿跌，这两个序列不再需要对调。
#     （v1.1.5 曾随方向翻转，当时的教训保留在 seq_for 的注释里：
#      "只让 th['up']/th['dn'] 互换、却把色序写死" 会造成
#      表格与曲线配色不一致 —— 现在没有方向开关，这条坑自然消失。）
# ★★ v3.0.6 重设计（用户需求）：「多条折线颜色接近、线条密集、视觉区分弱」。
#
#   【旧设计的问题】UP_SEQ 十条全是红/橙系、DN_SEQ 十条全是青/蓝系 ——
#   语义清楚（暖=流入、冷=流出），但**同一色系内十条线必然趋同**，
#   20 条叠在一张图上就是一团糊。用户原话："颜色接近，视觉区分弱"。
#
#   【新设计：高亮 + 弱化（highlight + context）】比"再换一套 20 色"有效得多：
#     · 每个方向的**前 5 条**（= 该方向净额最大的 5 条，`_fan_pick` 已排序）
#       用高饱和、彼此色相拉开的颜色 + 稍粗线宽；
#     · 其余 10 条**统一降透明度**（FAN_DIM_ALPHA）并减细 —— 作为"背景信息"
#       存在，不抢视线；鼠标悬停仍可读（见 _apply_hover_labels）。
#   ★ 为什么不做"20 个完全不同的色相"：20 条线无论怎么配色都会挤，
#     真正能解决"看不清"的是**视觉层次**，不是更多的颜色。
#   ★ 方向语义不靠颜色族单独承担，另有三处冗余线索：
#     末端标签前缀 ▲/▼、标签文字色（红/绿）、图例分组标题。
UP_SEQ = ["#ff4d5e", "#ff9f43", "#ffd93d", "#f368a8", "#c56cf0",
          "#ff8a80", "#ffb74d", "#fff176", "#f8a5c2", "#d7aefb"]
DN_SEQ = ["#22d3a0", "#38bdf8", "#5b8def", "#2dd4bf", "#7c5cff",
          "#69d9b6", "#7cc7f5", "#8fa3e8", "#6ee0d2", "#a58cff"]

#: 每个方向**高亮**（粗线 + 不透明 + 端点标名）的条数。用户要求
#  「只保留 Top5 流入、Top5 流出板块在折线端点标注名称 + 金额」。
FAN_HL_N = 5
#: 非高亮曲线的透明度 —— 作为背景信息存在，不抢视线。
FAN_DIM_ALPHA = 0.30


def seq_for(is_up):
    """取该给「净流入 / 净流出」曲线用的色序（暖色族=涨，冷色族=跌）。

    is_up=True  → 净流入那条线用暖色族
    is_up=False → 净流出那条线用冷色族

    ★ 配色方向固定为红涨绿跌（v1.2.1 起无方向开关），因此这里不再对调。
      如果将来恢复了方向切换，**必须**同时在这里对调 warm/cool ——
      实测踩过：只换 th["up"]/th["dn"] 而不换曲线色序，
      会得到"表格红了、曲线还绿着"的半截效果（逐像素比对 0_总览页才发现）。
    """
    return UP_SEQ if is_up else DN_SEQ

# 曲线参与统计的标的数：前 10 流入 + 后 10 流出 = 20 条
# （与 HTML 版 buildFan 的 `topn=20` 口径一致；用户要求"不少于 20 个"）
FAN_TOP_N = 20
#: 资金曲线选线要求的最少**有效分钟点数**（v3.0.6）。
#  ★ 为什么是 10：与 `salience.cluster_themes` 的最小行数门槛一致 ——
#    少于 10 个点既画不出形状、也谈不上"走势"，只会是几个孤立点。
#    详见 `_market_stats` 里选线池过滤的说明。
FAN_MIN_POINTS = 10
# ★ v2.0.5：FAN_LEG_FS / FAN_LEG_NCOL（底部图例的字号与兜底列数）已随
#   图例机制一并移除 —— 曲线改为右端终点直接标注「板块名 收盘金额」，
#   标注样式见 FanChart._end_labels。

# ★ v1.1.7 实时榜单（累积池）参数
#   WATCH_TOP_N   开盘入榜条数 —— 用户要求「约 15 个」，含义是**每个方向各 15**：
#                 只算总数的话，若这 15 个恰好全是流入，流出侧一个都看不见，
#                 而"谁在被资金抛弃"正是要盯的另一半。
#   WATCH_BASE_N  基础榜（Worker 一次性算出的当天结果）固定条数，同样双向各 15。
WATCH_TOP_N = 15
WATCH_BASE_N = 15
#   RADAR_N  「异动雷达」每侧条数（v2.0.1 新增）。取 24 而不是 15：
#            雷达的用途是**发现**（高召回、看全貌），不是"只看前几名"。
#            两侧各 24 条，一屏滚动能看完全天的异动面貌。
RADAR_N = 24

# ★ v1.2.1 页签清单（唯一权威来源）
#   顺序 = addTab 的调用顺序，二者必须一致。
#   用途：① 视图菜单按它生成 Ctrl+1..N 快捷键；
#         ② 需要"跳到某页"时用 PAGE_INDEX["页名"] 取索引 ——
#            不要写字面量数字，也不要用 tabs.count()-1。
#
#   ★ 为什么单独抽出来（踩过的坑）：原先 on_fail 用 `setCurrentIndex(
#     tabs.count()-1)` 跳"最后一页"去看失败原因，而那一页恰好是
#     「数据状态」。v1.2.1 删掉该页后，count()-1 就变成了「实时榜单」——
#     失败时把用户甩到榜单页，且不报任何错。凡是"按位置"而不是"按名字"
#     定位页签的写法都属脆性写法，增删页签时必然静默错位。
#   「数据状态」页已于 v1.2.1 删除，故不在表中。
#   v2.0.1 新增「市场全景」（问题九）与「异动雷达」（问题十）。
#
# ★★ v2.1.0（2026-09-23）：**两级页签重构**（用户需求）。
#
#   需求原话：「除了打开的首页总览这个模块，其他所有模块和所有子模块全部打包，
#   全部整理后重排，重新建设新的模块再把这些子模块打包，再一个界面中的各级
#   子模块需要贴合一个主题，母模块标签需要符合这个主题，切记，目前所有的子模块
#   都是有用的，禁止删除任一子模块，只是重新整合排列，UI 要整齐简洁。」
#
#   ── 为什么改（问题诊断）────────────────────────────────────────────
#   旧版把 14 个页签**平铺**在一行。同主题内容散落在互不相邻的页上：
#     · 「资金排行」类被拆到 4 页：异动雷达 / 辨识度榜 / 行业概况 / 实时榜单
#     · 「盘中复盘」类被拆到 4 页：实时事件 / 主角更替 / 阶段对比 / 关键节点
#     · 「大盘」类被拆到 2 页：市场全景 / 开盘检测
#   结果是：读一个主题要在 4 个页签之间来回切；而 14 个页签一行排不下，
#   QTabBar 溢出成左右滚动箭头 —— 后面的页签用户根本看不见。
#
#   ── 怎么改（方案）──────────────────────────────────────────────────
#   顶层只留 **6 个母模块**（术语见下方 TAB_TREE），每个母模块内部用**子页签**
#   承载它自己的子模块。母模块标签 = 其下所有子模块的**共同主题**。
#
#   ★ 关键约束「禁止删除任一子模块」的落地方式：
#     子模块的构建代码**一行都没有删、没有合并**，只是各自的 addTab 目标
#     从 self.tabs 改成所属母模块内部的 sub-QTabWidget。
#     因此：控件、表名（t_*）、图表（ch_*）、填充逻辑、--shot 断言
#     全部原样保留 —— 这是"物理隔离 + 归位"，不是裁剪。
#
#   ★ 兼容性：PAGE_NAMES 保持为**全部 14 个子模块名**（顺序即 PAGE_INDEX），
#     它继续是 Ctrl+1..N、"跳页"、chk115 断言 H3 的唯一权威来源。
#     母模块清单另立 TAB_TREE —— 两者职责不同，不要混用：
#       PAGE_INDEX[子模块名] → 在**扁平子模块序**中的下标
#       PAGE_NAMES           → 扁平子模块序（= 旧版 14 页签，兼容不变）
#       TAB_TREE             → 母模块 → 子模块名列表（顶层真实结构）
PAGE_NAMES = ["总览", "市场全景", "开盘检测", "个股异动", "个股精选", "异动雷达",
              "辨识度榜", "行业概况", "实时事件", "题材聚簇", "主角更替",
              "阶段对比", "关键节点", "实时榜单"]
PAGE_INDEX = {nm: i for i, nm in enumerate(PAGE_NAMES)}

# ★★ v2.1.0：母模块 → 子模块（顶层真实结构；总览不设子页）。
#   顶层标签即是主题名；括号里的英文是给 --shot 文件名的安全后缀用的。
#   ⚠ 子模块名必须与 PAGE_NAMES 中的名字逐字一致（自检 TAB TREE MISMATCH）。
TAB_GROUPS = [
    ("总览", []),                                        # 首页，无子页
    ("大盘", ["市场全景", "开盘检测"]),
    ("个股", ["个股异动", "个股精选"]),
    # ★ 子页顺序 = 该母模块内 addTab 的调用顺序。这里让「异动雷达」排在
    #   「辨识度榜」之前 —— 与旧版 14 页签的相对顺序一致（雷达原在辨识度榜前），
    #   老用户按肌肉记忆切页时不会错位。
    ("资金排行", ["异动雷达", "辨识度榜", "行业概况", "实时榜单"]),
    # ★ v3.0.1：子模块名「题材簇」→「题材聚簇」。
    #   起因：用户要求"子模块命名除总览外一律四字、不得二字"。
    #   全量审计后只有这一个不合规（3 字）。选「聚簇」而不是「簇群/聚类」：
    #   本页讲的正是"多个细分板块**聚**成同一条主线"的归属关系，
    #   "聚"字直接对应语义（见上方 v2.1.0 关于母模块划分的说明）。
    #   ⚠ 只改**子模块名**；面板显示名「题材簇预览」保持不动（属于面板层）。
    ("板块题材", ["题材聚簇"]),
    ("盘中复盘", ["实时事件", "主角更替", "阶段对比", "关键节点"]),
]
TAB_TREE = {g: subs for g, subs in TAB_GROUPS}
# 母模块名 → 顶层下标（视图菜单 → 跳到某母模块）
GROUP_INDEX = {g: i for i, (g, _s) in enumerate(TAB_GROUPS)}
# 子模块名 → 母模块名（反查；用于"跳页"与图表归属换算）
PAGE_GROUP = {sub: g for g, subs in TAB_GROUPS for sub in subs}
# 扁平子模块序（= PAGE_NAMES）与"母模块中的位置"互查：
#   GROUP_POS[子模块名] = (顶层下标, 该母模块内子页下标)
#   总览是唯一没有子页的顶层项 → 不进入本表。
GROUP_POS = {}
for _gi, (_g, _subs) in enumerate(TAB_GROUPS):
    for _si, _nm in enumerate(_subs):
        GROUP_POS[_nm] = (_gi, _si)

# ★★ v2.2.0：面板（孙模块）清单 —— **孙模块层级的唯一权威来源**。
#
#   需求原话：「界面分为母模块和子模块，母模块下面有大概 30 个孙模块，
#   子模块下面有 30 个孙模块，要求这一个不少。」
#
#   ── 为什么要专门立这张表 ──────────────────────────────────────────
#   在 v2.1.0 及以前，"孙模块"（面板）只是**视觉概念**：
#   面板由 make_card() 建出来后挂进某个 QVBoxLayout，**代码里没有任何地方
#   记录"应该有几个"**。于是"一个不少"这句要求无法被机器验证 ——
#   少挂一个卡片不会抛异常、不会报错、日志里也没有痕迹，
#   只有用户盯着屏幕才发现"这页怎么少了一块"。
#
#   子模块层有 TAB_GROUPS 作权威源，所以能写出
#     sorted(实际挂载) == sorted(PAGE_NAMES)
#   这样的守恒断言。**面板层现在照做**：本表即面板层的 TAB_GROUPS。
#
#   ── 表的结构 ─────────────────────────────────────────────────────
#   每项 = (panel_id, 显示名, 所属母模块, 所属子模块)
#     panel_id  稳定标识符（英文点分），**不随界面文案变化** ——
#               守恒断言比的是 id 集合，所以改标题不会让校验失效。
#     display   给人看的名字（仅用于日志与文档，不参与断言）。
#
#   ⚠ id 一旦分配**不得改名**（改名等于删旧增新，守恒断言会立刻报 mismatch）。
#     要调整的是面板的**位置/尺寸/样式**，id 应保持稳定。
#
#   ── 计数口径（2026-09-23 与用户确认）────────────────────────────
#   循环生成的面板**按实际面板数**计入，不按"逻辑模块"合并：
#     · 开盘检测 = 3 节点 × 2 指标 = **6 个面板**（不是 1 个）
#     · 个股异动 = 3 节点 × 3 指标 = **9 个面板**（不是 1 个）
#   理由：它们在 Qt 控件树里确实是 6 / 9 个独立卡片，
#   各自有独立的表格控件、独立的数据填充、独立的屏幕层断言 ——
#   按"1 个"记会掩盖"其中某一张没挂上"这类真实缺陷。
#   用户已确认「全部保留」，故本表共 **40 个面板**。
PANELS = [
    # ---------------------------------------------------------- 总览（无子页）
    ("overview.fan_curve",       "主力资金累计曲线",     "总览", None),

    # ---------------------------------------------------------- 大盘 / 市场全景
    ("market_overview.index",    "指数分时（上证+东财全A）", "大盘", "市场全景"),
    ("market_overview.flow_bar", "主力资金流入/流出对比",  "大盘", "市场全景"),
    ("market_overview.range_in", "区间极值·主力吸筹前十",  "大盘", "市场全景"),
    ("market_overview.range_out", "区间极值·主力出货前十", "大盘", "市场全景"),

    # ---------------------------------------------------------- 大盘 / 开盘检测
    #   3 个时间节点 × 2 项指标（成交量 Top5 / 主力净流入 Top5）
    ("open_check.m5_vol",   "开盘后5分钟·成交量Top5",     "大盘", "开盘检测"),
    ("open_check.m5_net",   "开盘后5分钟·主力净流入Top5", "大盘", "开盘检测"),
    ("open_check.m10_vol",  "开盘后10分钟·成交量Top5",    "大盘", "开盘检测"),
    ("open_check.m10_net",  "开盘后10分钟·主力净流入Top5", "大盘", "开盘检测"),
    ("open_check.m15_vol",  "开盘后15分钟·成交量Top5",    "大盘", "开盘检测"),
    ("open_check.m15_net",  "开盘后15分钟·主力净流入Top5", "大盘", "开盘检测"),

    # ---------------------------------------------------------- 个股 / 个股异动
    #   3 个时间节点 × 3 项指标（量比 / 成交量 / 主力净流入）
    ("stock_move.m5_lb",   "开盘后5分钟·量比Top10",       "个股", "个股异动"),
    ("stock_move.m5_vol",  "开盘后5分钟·成交量Top10",     "个股", "个股异动"),
    ("stock_move.m5_net",  "开盘后5分钟·主力净流入Top10", "个股", "个股异动"),
    ("stock_move.m10_lb",  "开盘后10分钟·量比Top10",      "个股", "个股异动"),
    ("stock_move.m10_vol", "开盘后10分钟·成交量Top10",    "个股", "个股异动"),
    ("stock_move.m10_net", "开盘后10分钟·主力净流入Top10", "个股", "个股异动"),
    ("stock_move.m15_lb",  "开盘后15分钟·量比Top10",      "个股", "个股异动"),
    ("stock_move.m15_vol", "开盘后15分钟·成交量Top10",    "个股", "个股异动"),
    ("stock_move.m15_net", "开盘后15分钟·主力净流入Top10", "个股", "个股异动"),

    # ---------------------------------------------------------- 个股 / 个股精选
    ("stock_pick.nine_boards", "个股精选·九榜出现次数排名", "个股", "个股精选"),

    # ---------------------------------------------------------- 资金排行 / 异动雷达
    ("radar.inflow",  "异动雷达·流入", "资金排行", "异动雷达"),
    ("radar.outflow", "异动雷达·流出", "资金排行", "异动雷达"),

    # ---------------------------------------------------------- 资金排行 / 辨识度榜
    ("salience.inflow",  "主力净流入榜", "资金排行", "辨识度榜"),
    ("salience.outflow", "主力净流出榜", "资金排行", "辨识度榜"),

    # ---------------------------------------------------------- 资金排行 / 行业概况
    ("industry.inflow",  "行业净流入", "资金排行", "行业概况"),
    ("industry.outflow", "行业净流出", "资金排行", "行业概况"),

    # ---------------------------------------------------------- 资金排行 / 实时榜单
    ("watch.pool",      "板块概念实时榜单（累积池）", "资金排行", "实时榜单"),
    ("watch.now_in",    "当下辨识度榜·流入",         "资金排行", "实时榜单"),
    ("watch.now_out",   "当下辨识度榜·流出",         "资金排行", "实时榜单"),
    ("watch.now_d5in",  "当下辨识度榜·五分钟净流入",  "资金排行", "实时榜单"),
    ("watch.now_d5out", "当下辨识度榜·五分钟净流出",  "资金排行", "实时榜单"),

    # ---------------------------------------------------------- 板块题材 / 题材簇
    ("theme.cluster_in",  "流入主线",     "板块题材", "题材聚簇"),
    ("theme.cluster_out", "流出主线",     "板块题材", "题材聚簇"),
    ("theme.board",       "题材簇预览",   "板块题材", "题材聚簇"),
    ("theme.leader",      "龙头领先反应", "板块题材", "题材聚簇"),

    # ---------------------------------------------------------- 盘中复盘 / 实时事件
    ("replay.events", "盘中异动事件", "盘中复盘", "实时事件"),

    # ---------------------------------------------------------- 盘中复盘 / 主角更替
    ("replay.leaders", "主角更替", "盘中复盘", "主角更替"),

    # ---------------------------------------------------------- 盘中复盘 / 阶段对比
    ("replay.stages", "阶段对比", "盘中复盘", "阶段对比"),

    # ---------------------------------------------------------- 盘中复盘 / 关键节点
    ("replay.key_nodes", "全天关键节点", "盘中复盘", "关键节点"),
]
PANEL_IDS = [p[0] for p in PANELS]
# 面板 id → (显示名, 母模块, 子模块)
PANEL_INFO = {pid: (disp, g, s) for pid, disp, g, s in PANELS}
# 子模块名 → 该子页应有的面板 id 列表（守恒断言用）
PANELS_BY_SUB = {}
for _pid, _disp, _g, _s in PANELS:
    PANELS_BY_SUB.setdefault(_s if _s is not None else _g, []).append(_pid)

# ★★ v2.0.1：图表 → 所属页签。
#   用途：**按页延迟绘制** —— 首屏只画用户真正看得见的那一页的图。
#
#   为什么需要（这是首屏提速的关键，先量后改得出的结论）：
#     实测（tools/prof_v201.py）整条数据处理链只要 94 ms：
#       build_features 39ms / 事件流 16ms / 时间轴 16ms / 其余都 <10ms。
#     也就是说"首屏慢"根本不慢在算法上 —— 真正的开销是
#     **把 6 张 matplotlib 图表一次性全画完**（每条曲线都要重新光栅化，
#     20 条线 + 图例的 fan 图最重），而这 6 张图里有 5 张在别的页签上，
#     用户打开程序时一眼都看不到。
#     一次性全画 = 让用户为看不见的东西等待。
#
#   代价与边界：切页时会补画该页的图（几十到几百毫秒，一次性的），
#   换来首屏少画 5 张。数据更新的处理（填表）仍然全量做 ——
#   切页时不必等数据，只等绘制。
#   ★★ v2.1.0：值由「子模块名」改为「**母模块名**」。
#     图表的按页延迟绘制现在按顶层页签判定 —— 因为切到「资金排行」这个
#     母模块时，它的子页可能停在任意一个（子页签有自己的 currentChanged）。
#     判定"可见"的口径必须与"顶层是否可见"一致，否则会出现
#     "切到母模块但图不画"的静默空白。
#     ⚠ 这里必须用母模块名（见 TAB_GROUPS），不是子模块名。
CHART_PAGE = (
    ("ch_fan", "总览"),
    ("ch_index", "大盘"),
    ("ch_flow", "大盘"),
    ("ch_theme", "板块题材"),
    ("ch_lead", "盘中复盘"),
    ("ch_stage", "盘中复盘"),
)

# 完整交易时段固定为 9:30–15:00（用户要求：横轴统一、刻度均匀、视觉一致）
TRADING_MINUTES = 240          # 09:31~11:30(120) + 13:01~15:00(120)
# 横轴刻度：与 HTML 版 drawFan 的 ticks 完全一致（9 个）。
# ★ 为什么没有 13:00：轴口径是"午休不占位次"，11:30=119、13:01=120 只差 1 格，
#   两个标签都画必然糊成一团 —— HTML 版正是因此只标到 11:30、下一格直接跳 13:30。
#   午休边界改用一条分界竖线表达（见 FanChart.plot），信息不丢、标签不挤。
TRADING_TICKS = ["09:30", "10:00", "10:30", "11:00", "11:30",
                 "13:30", "14:00", "14:30", "15:00"]


def _minute_index(hm):
    """'HH:MM' → 全天第几分钟（0..239，A股连续竞价 240 分钟）。

    09:31 = 0 … 11:30 = 119，13:01 = 120 … 15:00 = 239。
    午间休市（11:30–13:00）不占位次，这样横轴刻度才能等距。
    """
    try:
        h, m = str(hm).split(":")[:2]
        t = int(h) * 60 + int(m)
    except Exception:                                    # noqa: BLE001
        return None
    if 9 * 60 + 31 <= t <= 11 * 60 + 30:
        return t - (9 * 60 + 31)
    if 13 * 60 + 1 <= t <= 15 * 60:
        return 120 + (t - (13 * 60 + 1))
    if t == 9 * 60 + 30:          # 容错：个别板块从 09:30 就有数据
        return 0
    return None


def _minute_index_clamped(hm):
    """同 `_minute_index`，但把**休市时段的钟点**夹到最近的交易分钟。

    ★ v1.2.4：用于消化"脏刻度"。旧版本曾把午休/盘后的本地时刻
      （11:31~12:59、15:00 之后）直接当标签写进面板时间轴
      （实测末尾出现过 11:43），这些标签 `_minute_index` 认不出、
      横坐标无处安放。它们**语义上**等于"上午收盘 / 全天收盘那一刻的快照"，
      因此夹到边界才是准确的落位：
        11:31~12:59 → 119（11:30）；15:00 之后 → 239（15:00）；
        09:31 之前  → 0（开盘）。
      真正的"认不出"（空串、非时间文本）仍返回 None，交由调用方退回下标口径。
    """
    v = _minute_index(hm)
    if v is not None:
        return v
    try:
        h, m = str(hm).split(":")[:2]
        t = int(h) * 60 + int(m)
    except Exception:                                    # noqa: BLE001
        return None
    if t < 9 * 60 + 31:
        return 0
    if t < 13 * 60 + 1:
        return 11 * 60 + 30 - (9 * 60 + 31)               # → 119
    return TRADING_MINUTES - 1                            # → 239


def _x_positions(times, n):
    """把 times 换算成横轴坐标「分钟序号」（0..239），供曲线定位。

    ★★ v1.2.4 核心修复：**曲线必须按分钟序号定位，不能按数组下标** ★★
    ────────────────────────────────────────────────────────────────
    【问题】曲线原先写 `ax.plot(range(len(s)), s)` —— x 用的是**数组下标**；
      而横轴刻度是 `_minute_index()` 算出来的**分钟序号**。两套坐标系只有
      在「面板一分钟都不缺」时才恰好相等。而盘中面板必然有缺口：
        · 增量刷新每次只追加「当前这一分钟」（中间没开程序就永久缺）；
        · 个别分钟接口返回空行（fetch_sector_kline 里 `continue`）。
      实测 2026-09-21 11:17 的面板：41 个点覆盖 09:31~11:17（真实跨度
      **106 分钟**），最后一点却画在下标 **40** —— 横轴上读出来是 **10:11**，
      于是「曲线只画到十点多、追不上最新数据」。
      缺得越多，曲线被压缩得越靠左，偏差是**累积**的，不是固定偏移。

    【修法】按每个时间点自己的分钟序号落点：数据到几点，曲线就画到几点。

    返回 (xpos, mapped)：
      xpos   —— 横坐标列表，**长度 = min(len(times), n)**，**严格单调递增**
      mapped —— True 表示按分钟序号定位；False 表示后退回数组下标

    ★★ v3.0.5 契约变更：xpos 长度**不再保证等于 n**，而是**以 times 为准**。
      原因见函数体注释（times 短于曲线时，旧实现会续出假序号把曲线拉到 15:00）。
      调用方按 `xpos[:len(s)]` 取用，天然兼容更短的返回值。

    ★ 退化规则（保证既有行为与测试不变）：
      · n <= 0、times 为空、times 全部无法解析（例如测试用的 [""]×T）→
        返回 `list(range(n)), False`，与旧实现逐点一致。
    ★ 单调化三招（缺一不可，否则缺分钟的 panel 会画出锯齿）：
      ① 首点之前若缺（理论上不该有：面板首分钟恒为 09:31），用
         「首个已知序号 − 距离」倒推，而不是写 0（写 0 会让开头挤成一坨）；
      ② 中间的空洞用「上一个 + 1」前向填充（宁可略微外推，也不留同值）；
      ③ 遇到 ≤ 上一个的点强制 +1 —— 面板里混进重复标签、或旧版本写入过
         非交易分钟（11:35、12:05 这类）时，保证 x 仍然严格递增。
    """
    if n <= 0:
        return [], False
    if not times:
        return list(range(n)), False
    raw = [_minute_index_clamped(t) for t in times[:n]]
    # ★★ v3.0.5 修复（用户报告：「为什么有一段数据没采集呢，这一段直接是横线了」）
    #   【旧行为】times 比曲线短时，这里 `raw = raw + [None] * (n - len(raw))`，
    #     后面的 None 会在下方循环里被「上一个 + 1」**续出假序号**。
    #     times=50、n=240（矩阵来自另一个更长的面板）时，尾部序号一路续到
    #     **239** —— 曲线被拉满整个横轴到 15:00；再叠加 `_fill_gaps` 的端点外推
    #     （重复末值），尾部就成了**一条平直横线**。
    #   【修法】时间标签是横轴的唯一依据：**没有标签就不该有点**。
    #     这里改为**只对 times 覆盖到的前 len(times) 个点出坐标**，返回长度
    #     与 times 一致（可能短于 n）。调用方本就是 `xpos[:len(s)]` 取用，
    #     长度变短只会让曲线少画几个无标签的点 —— 这正是我们要的。
    #     同时把 mapped 保持 True（有标签的点仍按真实分钟落位）。
    _nt = min(len(times), n)
    raw = [_minute_index_clamped(t) for t in times[:_nt]]
    if not any(v is not None for v in raw):
        return list(range(_nt)), False     # 一个都认不出来 → 退回下标

    k0 = next(i for i, v in enumerate(raw) if v is not None)
    base = raw[k0]
    for i in range(k0 - 1, -1, -1):        # ① 头部倒推
        base = max(0, base - 1)
        raw[i] = base

    out = []
    prev = None
    for v in raw:                          # ② 前向填充 + ③ 强制递增
        if v is None:
            v = (prev + 1) if prev is not None else 0
        elif prev is not None and v <= prev:
            v = prev + 1
        out.append(int(v))
        prev = v
    return out, True


def _fill_gaps(series, max_interp=6):
    """把一条曲线的跳点 / 空值 / 缺失分钟衔接成**连续不间断**的序列。

    ★ v1.1.7 用户需求：「遇到跳点、空值或数据缺失区间时，曲线需保持连贯
      不断开，不得出现断裂或空白段。」

    为什么原样画会断：
      matplotlib 对 NaN 的行为是**跳过并在断点两侧留空**。面板数据里有三类
      非数值来源，任一出现就会在图上撕开一道口子：
        ① 板块晚于全市场首个分钟才有数据（开盘头几分钟）；
        ② 数据源个别分钟返回空行（fetch_sector_kline 会 `continue`）；
        ③ JSON 里存成 null / 字符串（`float()` 失败）。
      `flowdata.build_panel` 只在**落盘时**做了前向填充，但 `_market_stats`
      取的 `series` 直接来自 `F.V[:, j]`（numpy 数组，中间列仍可能是 NaN），
      而且 `None` 从 JSON 读回来也不会被自动补。

    衔接策略（三段式，逐段回退，保证「一定画得出来」）：
      1. **前向填充**：用上一分钟的值顶上 —— 语义是「这一分钟没有新成交，
         累计净额不变」，这是资金累计口径下最自然、也是建面板时的同一口径；
      2. **前向回填**：序列**开头**若仍缺（板块晚于全市场首分钟才露面），
         用后一个已知值补 —— 注意这里刻意用「后一个已知值」而不是硬写 0：
         若该板块首个真实值本就是 -3.2 亿，写 0 会在图上凭空造出一段
         从 0 下滑的假走势；
      3. **整体兜底**：整条全空（极端情况）→ 补 0，画一条贴零轴的平线，
         而不是留一段空白。

    ★★ v3.0.6（max_interp）：**长缺口不再插值，改成如实断开。**
      起因：v3.0.6 新增的"快速先行版"会先把**当前这一分钟**补上，而它
      与上一段真实数据之间可能隔着几十到上百分钟（实测 11:30 → 14:34，
      93 个缺失分钟）。若沿用"一律插值"，`np.interp` 会把这段跨 93 格的
      空洞用一条直线补上 —— 那是**编造出来的走势**，正是本项目反复修过的
      "假横线"缺陷（v3.0.5 刚修过同类问题）。
      新口径：
        · 连续缺失 **≤ max_interp（默认 6，与 SPOT_MAX_GAP_MIN 同口径）**：
          照旧插值 —— 语义是"这一分钟没有新成交，累计净额不变"，这是真实
          且合理的假设；
        · 连续缺失 **> max_interp**：**保留 NaN**，让曲线在那里断开，
          如实呈现"这段没有数据"（全量补齐后这些位置会被真实值填上）。
      为什么阈值取 6：与 `flowdata.SPOT_MAX_GAP_MIN` 完全一致 —— 那是项目
      里"还能当成'抖动'来看"的缺口上限，超过它就该走全量补齐而不是靠插值
      糊过去。两个口径共用一个数字，避免以后各改各的。

    ★ 返回值长度**必须与入参一致**（x 轴是按分钟序号排的 range(len)），
      长度一旦变了整条曲线会横向错位到别的时刻上。
    """
    # ★ numpy 是**函数内**导入的（本项目刻意不让 native_app 在模块级依赖三方库，
    #   见文件头「零第三方依赖」约束）。这一点必须写清楚：本文件里所有在
    #   模块级/函数级用到 np 的地方都得自己 import，否则 NameError 会被
    #   `except Exception` 吞掉、静默返回兜底值 —— 这个坑本轮就踩了：
    #   `_fill_gaps` 少这行 → 每条曲线都变成一列 0（"补齐"成了"抹平"），
    #   `_fan_ylim` 少这行 → 永远返回 None（自适应量程静默失效）。
    import numpy as np
    try:
        arr = np.asarray(series, dtype=float)
    except Exception:                                    # noqa: BLE001
        return [0.0] * len(series or [])
    if arr.size == 0:
        return []
    good = np.isfinite(arr)
    if good.all():
        return [float(x) for x in arr]                   # 没缺口：原样返回
    if not good.any():
        # 整条全空（极端情况）→ 贴零轴的平线，而不是留一段空白（原语义不变）
        return [0.0] * arr.size
    idx = np.arange(arr.size, dtype=float)
    # np.interp 天然是「最近的前后两点之间线性插值」：
    #   中间的空洞被两侧真实值夹着补上，两端延伸用最近端点值 ——
    #   正是我们要的「前向填充 + 开头回填」两步合一，且一次算完。
    out = np.interp(idx, idx[good], arr[good])
    # ★★ v3.0.6：把**过长的**连续缺失段还原成 NaN（见 docstring 的 max_interp）。
    #   短缺口（≤ max_interp）照旧插值 —— 那是真实且合理的"这一分钟没成交"；
    #   长缺口保留 NaN，让曲线断开，**不编造走势**。
    i, n = 0, arr.size
    while i < n:
        if good[i]:
            i += 1
            continue
        j = i
        while j < n and not good[j]:
            j += 1
        if (j - i) > max_interp:
            out[i:j] = np.nan
        i = j
    return [float(x) for x in out]


def _fan_ylim(curves):
    """累计曲线的 Y 轴量程 —— 按**当前已加载数据**动态算（v1.1.7 用户需求）。

    ★ 原实现完全没有 set_ylim，全靠 matplotlib 自动量程。这有两个问题：
       ① 自动量程的边距是「数据跨度的固定百分比」，早盘跨度只有几十亿时
          曲线会被撑满整幅图上下、看着像剧烈震荡；
          尾盘跨度上千亿时又贴边，最上面那条线的标签容易被轴顶切掉；
       ② 「午休」标注取 `ax.get_ylim()[0]` 定位 —— 量程是渲染时才算出来的，
          必须在 set_ylim **之后**再取，否则拿到的是上一轮的旧量程。

    设计口径：
      · 全部曲线（流入 + 流出）一起取 min/max，**共用一套量程** ——
        两侧都是净额（亿元），同一把尺子才可比；
      · 上留 8%、下留 8% 的余量，保证峰谷都不顶到轴框；
      · 强制量程**至少跨 M**（M = max(2.0, 数据跨度 × 15%)）：数据极度平坦
        （开盘头一分钟全员净额都是 0）时若按绝对值给 0±0.1，会把「全是 0」
        画成一条满幅横线，看着像异常放量。给个最小跨度，平淡就是平淡；
      · 0 轴必须落在量程内（下方可能全是流入、上方可能全是流出），
        否则 `axhline(0)` 那条参考线会被裁掉、看不见。
    """
    # ★ 同 _fill_gaps：np 必须在函数内导入（本项目模块级不依赖三方库）
    import numpy as np
    vals = []
    for c in curves or []:
        try:
            a = np.asarray(c, dtype=float)
        except Exception:                                # noqa: BLE001
            continue
        a = a[np.isfinite(a)]
        if a.size:
            vals.append((float(a.min()), float(a.max())))
    if not vals:
        return None
    lo = min(v[0] for v in vals)
    hi = max(v[1] for v in vals)
    lo = min(lo, 0.0)                                    # 保证 0 轴可见
    hi = max(hi, 0.0)
    span = hi - lo
    min_span = max(2.0, span * 0.15)
    if span < min_span:                                  # 太扁 → 以中点为心撑到 min_span
        mid = (lo + hi) / 2.0
        lo, hi = mid - min_span / 2.0, mid + min_span / 2.0
        span = hi - lo
    pad = span * 0.08 + 0.02
    return lo - pad, hi + pad


# ★★ v2.0.3：FanChart/IndexChart/ThemeBoard/LeaderGrid/StageGrid/
#   FlowBarChart 六个类的定义已并入上方 _define_chart_classes()。


# ==================================================================== 表格助手# 当前主题的引用：fill_table 这类模块级自由函数拿不到 MainWindow，
# 用它取「空表提示」等弱化色；toggle_theme 时同步更新。
_theme_ref = {"th": coloring(THEMES["light"])}


def make_table(headers, stretch_col=1, align=None, widths=None):
    """align: {col: 'l'|'r'|'c'}；未指定的列数字右对齐、文本左对齐。
    widths: {col: 固定像素宽}，用于给「排名」这类窄列定宽，避免 Stretch 抢走空间。

    ★ 列对齐是「序号与题材名间距过大」的根因所在：
      原来 fill_table 对 c>0 的列一律右对齐，而题材列又是 Stretch（实测 685px），
      于是题材名被推到列的**最右侧**，序号和题材名之间空出一大片。
      现在文本列统一左对齐并紧贴序号，数字列右对齐。
    ★★ v2.1.0：行高 24 → 22、表头 28 → 26（用户反馈空白过多）。
      一屏 30 行的榜单表因此省下 60px+ —— 这正是"表格下方总有一条空白"
      的主要来源之一。22px 仍 >= 20px 的最小行高，中文 11.5px 字不会挤。
    ★★ v2.1.0（第二处）：**默认纵向均分表高**。
      旧版只有「个股异动」页显式调了 verticalHeader().setSectionResizeMode(Stretch)，
      其余页面的表格都是"内容高度"——数据行少时表底就留一大片空白
      （比如「个股精选」只有 10 行，却占着一整页的高度）。
      现在统一在 make_table 里设为 Stretch 并把 minimumSectionSize 压到 19：
      行数少时行与行均匀铺开占满卡片，行数多时自动回到滚动 —— 与
      「个股异动」页原先的效果一致，且各页视觉统一。
      ⚠ 行数超过可视高度时 Stretch 会拒绝继续压缩，此时由滚动条接管，
        不会出现行高被压到看不见的情况（受 minimumSectionSize 保护）。
    """
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setWordWrap(False)
    t.setShowGrid(False)
    hh = t.horizontalHeader()
    hh.setStretchLastSection(False)
    if stretch_col is not None:
        hh.setSectionResizeMode(stretch_col, QHeaderView.Stretch)
    for c in range(len(headers)):
        if c != stretch_col:
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
    t._align_map = align or {}
    t._widths = widths or {}
    for c, w in (widths or {}).items():
        hh.setSectionResizeMode(c, QHeaderView.Fixed)
        t.setColumnWidth(c, w)
    # 紧凑行高：默认 30px 在 30 行的榜单上会多出近 200px 空白（v1.x 注释保留）
    # ★ v2.1.0：纵向均分表高 —— 行少时铺满卡片，消除表底大块空白
    vh = t.verticalHeader()
    vh.setDefaultSectionSize(22)
    vh.setMinimumSectionSize(19)
    vh.setSectionResizeMode(QHeaderView.Stretch)
    # 表体不参与"表格自身的最小高度"计算，避免把所属卡片顶高
    # ★★ v3.0.1 结论（留白压缩的**边界**，勿再尝试往下压）：
    #   曾试过把空表的纵向策略从 Expanding 改成 Maximum，想让 0 行的表
    #   收缩到「表头 + 一行提示」，从而消掉空表白块。实测结果**更差**：
    #   · 表格缩到 60px 后，卡片里剩下的空白并没有消失，只是从"表格内部
    #     的空白"变成"卡片内部的空白" —— 像素统计上留白率反而上升
    #     （辨识度榜 17.3% → 77.9%，个股精选 20.5% → 77.8%）；
    #   · QGridLayout 只有 row 0 有内容且未设 rowStretch 时，多余空间不再
    #     被表格吃掉，于是两张卡片被挤到页面底部，上半屏纯空白。
    #   根因：**这些页面的"空白"不是内边距，是"表格在等数据"**。
    #   数据到达后表格填满，空白自然消失；提前压掉只会让空态更难看。
    #   → 纵向保持 Expanding（表格撑满卡片），留白交由数据填充。
    #     这是"只压留白、不动结构"的正确边界：能压的是**内边距与行高**，
    #     不能压的是**内容的自然占位**。
    t.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    hh.setFixedHeight(26)
    return t


class _Hint:
    """整行提示（v3.0.1）。传给 fill_table 表示「这一行是一整句说明，
    请合并全部列居中显示」，而不是当成数据行塞进第 1 列被截成「该…」。

    与 `rows=[]`（空数据 → 固定文案「（暂无数据）」）的区别：
      · rows=[] 是"确实没有数据"的通用态；
      · _Hint 是"有一个**具体原因**要告诉用户"（如"该节点未采集"）。
    两者的渲染方式相同（整行合并居中弱化），只是文案来源不同。
    """

    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text


def fill_table(t, rows, colors=None):
    """rows: [[cell,...]]；colors: {col: callable(value)->QColor}

    对齐规则：align_map 指定的优先；否则第 0 列居中（序号），
    其余列按「能否解析成数字」自动判定 —— 数字右对齐、文本左对齐。

    ★ 空数据时给出一行居中的「暂无数据」提示（v1.0.5）：
      原先空表是一片纯空白，用户分不清「没加载到」还是「本来就没有」。
      注意 setSpan 必须在每次填充前 clearSpans，否则从提示态切回
      多行数据时合并单元格会残留。

    ★★ v1.2.2：**已定宽的表自动转为 Fixed 列宽**（悬停卡顿的第 3 个根因）
    【问题】`make_table` 给非 Stretch 列设的是 `ResizeToContents`。
            该模式下**每次 setItem 都会重新测量全部单元格的文本宽度**
            来重算列宽 —— 代价与"改了几个单元格"无关。
            实测（悬停面板 5 列 × 14 行，真实可见）：
                原样 fill_table ................ 12.73 ms
                列宽改 Fixed 后 fill_table .....  0.64 ms   ← 快 20×
            悬停时鼠标每换一个分钟就重建整表，这 12.7 ms 直接叠进手势延迟里，
            是"不跟手"的第三大来源（前两个是整幅重绘与标签驱动布局）。
    【修法】第一次填完（列宽已按内容算准）就把这些列**锁成 Fixed**，
            并把当时的宽度原样记下来。之后每次填充只是 setItem，
            不再触发测量；列宽**逐像素不变**（实测与原方案完全一致）。
    【★ 为什么必须 opt-in】锁宽之后，列不再随内容变长而自适应。
            悬停面板（`_freeze_widths=True`）每帧重建、内容始终是"榜单前 7+7"，
            长度稳定，锁宽只赚不亏；
            而**榜单类表格**要跨数据刷新承接不同长度的题材名 ——
            若首次填的是短名就锁死，之后长名会被省略号截断，属于真实功能退化。
            所以只给明确标记的表启用（opt-in），不全局生效。
    """
    align = getattr(t, "_align_map", {}) or {}
    t.clearSpans()
    # ★★ v3.0.1：**整行提示**通道（`rows` 传一个特殊的 _Hint 对象）。
    #   起因（真机截图抓到的真实缺陷）：「开盘检测 / 个股异动」在某节点
    #   未采集时调 `_miss_row(ncol, "该节点未采集（程序未在节点时刻运行）")`，
    #   把 11 个字的说明塞进**第 1 列** —— 而第 1 列是 ResizeToContents，
    #   被同表其它窄列挤到只剩几十像素，于是屏幕上显示成 **「该…」**，
    #   用户既看不懂为空的原因，表格下方还留着 170px 空白。
    #   （证据：shots/v301_layout/v301_2_个股.png，九张表全部显示「该…」）
    #   修法：与「（暂无数据）」同一条路子 —— **整行合并 + 居中 + 弱化色**，
    #   提示文字完整可读。不删任何提示、不改表格结构，只把提示放到该在的位置。
    if isinstance(rows, _Hint):
        t.setRowCount(1)
        it = QTableWidgetItem(rows.text)
        it.setTextAlignment(Qt.AlignCenter)
        it.setForeground(QColor(_theme_ref["th"]["muted"]))
        t.setSpan(0, 0, 1, t.columnCount())
        t.setItem(0, 0, it)
        try:
            t.verticalHeader().setSectionResizeMode(QHeaderView.Interactive)
        except Exception:                            # noqa: BLE001
            pass
        return
    if not rows:
        t.setRowCount(1)
        it = QTableWidgetItem("（暂无数据）")
        it.setTextAlignment(Qt.AlignCenter)
        it.setForeground(QColor(_theme_ref["th"]["muted"]))
        t.setSpan(0, 0, 1, t.columnCount())
        t.setItem(0, 0, it)
        # ★ v2.1.0：空表时**取消纵向均分**，让那一行占自然高度、表体留空。
        #   为什么：make_table 默认给 verticalHeader 设了 Stretch（行少时
        #   铺满卡片）。但"暂无数据"只有 1 行 —— Stretch 会把这一行撑满
        #   整张卡片，提示文字孤零零飘在巨大空白正中，比留白更难看。
        #   改回 Interactive 后提示紧贴表头，卡片下方留白由布局自然分配。
        #   ★ 数据到达后 fill_table 会再走一次非空分支并把 Stretch 装回去。
        try:
            t.verticalHeader().setSectionResizeMode(QHeaderView.Interactive)
        except Exception:                            # noqa: BLE001
            pass
        return
    t.setRowCount(len(rows))
    # ★ v2.1.0：有数据时恢复纵向均分（与 make_table 的默认口径一致）。
    #   每次填充都显式设一遍，避免"上一次是空表 → 这次有数据 → 仍停在
    #   Interactive"的状态残留（表格实例是长期复用的）。
    try:
        t.verticalHeader().setSectionResizeMode(QHeaderView.Stretch)
    except Exception:                                # noqa: BLE001
        pass
    for i, row in enumerate(rows):
        for c, txt in enumerate(row):
            it = QTableWidgetItem(str(txt))
            a = align.get(c)
            if a is None:
                a = "c" if c == 0 else None
            if a is None:
                # 自动判定：纯数字（含正负号、小数点、千分位）右对齐，否则左对齐
                s = str(txt).strip()
                try:
                    float(s.replace(",", "").replace("+", ""))
                    a = "r"
                except ValueError:
                    a = "l"
            flag = {"l": Qt.AlignLeft, "r": Qt.AlignRight}.get(a, Qt.AlignHCenter)
            it.setTextAlignment(flag | Qt.AlignVCenter)
            if colors and c in colors:
                col = colors[c](row)
                if col:
                    it.setForeground(col)
            t.setItem(i, c, it)
    if getattr(t, "_freeze_widths", False):
        _freeze_widths(t)


def _freeze_widths(t):
    """把 ResizeToContents 的列锁成 Fixed（宽度取当前值），仅做一次。

    ★ 只在**已经算准**之后锁：调用点在 fill_table 末尾，
      此时 Qt 已按本次内容更新过列宽，直接读 columnWidth 即可，不会估偏。
    ★ 用属性打标记，避免每次填表都重复遍历表头。
    ★ 加 `_freeze_widths` 开关（opt-in）：默认不动列宽策略，
      只有明确需要"高频重填且内容长度稳定"的表才启用 —— 见 fill_table 的说明。
    """
    if getattr(t, "_widths_frozen", False):
        return
    try:
        hh = t.horizontalHeader()
        frozen = {}
        for c in range(t.columnCount()):
            if hh.sectionResizeMode(c) == QHeaderView.ResizeToContents:
                frozen[c] = t.columnWidth(c)
        for c, w in frozen.items():
            hh.setSectionResizeMode(c, QHeaderView.Fixed)
            t.setColumnWidth(c, w)
    except Exception:                                # noqa: BLE001
        pass                                          # 锁不上就维持原样，不影响功能
    t._widths_frozen = True


# ==================================================================== 卡片组件
class KpiCard(QFrame):
    """指标卡：标题 + 大号数值 + 副标题。

    用于总览顶部一排关键数字，比纯表格直观得多。

    ★★ v2.0.1：修复「大号数值被上下白边裁掉」的问题。
      【症状】用户反馈总览顶部那排卡片里，中间的大字（如「+0.78%」「246 个」）
        上下被卡片边缘切掉一块，看着像被"白边"压住了。

      【根因】卡片的 minimumHeight 被写死成 68px，而内容实际需要更多：
             标题 11.5px + 数值 21px(粗) + 副标题 10.5px 三行，
             加上行距与上下内边距，实测约 80~84px。
          QVBoxLayout 在纵向空间紧张时（小屏笔记本、或总览页下面的图变高之后）
          会按 minimumHeight 压缩控件 —— 68 < 84，于是三行文字的**首尾各被削掉几像素**。
          这不是"白边遮挡"，是**卡片被压扁后文本溢出被裁**；
          之所以看起来像白边，是因为卡片底色（card）比页面底色（bg）浅一档。

      【修法】把高度从"写死的 68"改成"按当前字体的实际行高算出来"：
        · `sizeHint()` 返回 三行 fontMetrics 高度之和 + 行距 + 上下内边距；
        · 垂直策略设为 `Fixed` —— Fixed 控件的 minimumSize == sizeHint，
          布局**不会**再把它压到内容装不下；
        · 只留 2px 余量，不做"加高到很宽松"的处理 ——
          留白太大会把下面的图表挤小，用户明确要求过"也不能留白太大"。
        这样字体、DPI、缩放比例怎么变都自适应，不会再出现写死尺寸那一类问题。
    """

    def __init__(self, title, value="—", sub="", accent=None):
        super().__init__()
        self.setObjectName("kpi")
        # 原始（未省略）文本 —— set_value/resizeEvent 都以它为准重算
        self._raw_title = str(title or "")
        self._raw_value = str(value or "")
        self._raw_sub = str(sub or "")
        v = QVBoxLayout(self)
        # ★ v3.0.6：数值字号提到 26px 后，上下内边距与行距都要跟着给够 ——
        #   否则会重现 v2.0.1 那个"大字号被卡片上下边缘裁掉"的问题。
        #   sizeHint() 按 fontMetrics 自适应（见类 docstring），这里只调留白比例。
        v.setContentsMargins(13, 9, 13, 9)
        v.setSpacing(3)

        self.lb_title = QLabel(title)
        self.lb_title.setObjectName("kpiTitle")
        self.lb_value = QLabel(value)
        self.lb_value.setObjectName("kpiValue")
        if accent:
            self.lb_value.setStyleSheet(f"color:{accent};")
        self.lb_sub = QLabel(sub)
        self.lb_sub.setObjectName("kpiSub")
        # ★ 三行文字都不换行、超宽打省略号（v1.1.6）：
        #   窗口缩到最小尺寸（1020×660）时 6 张卡并排，每张只有 ~160px，
        #   副标题（如「上证 +1.23% · 全A +1.00%」）会被硬裁成半截字。
        #   按宽度打省略号后变成「上证 +1.23% · 全A …」，
        #   既保住布局不撑破，又能看出还有内容（悬停有完整 tooltip）。
        for _lb in (self.lb_title, self.lb_value, self.lb_sub):
            _lb.setWordWrap(False)
            _lb.setMinimumWidth(0)
            # 关键：允许 QLabel 收窄。否则 Stretch 布局下它按文字理想宽度
            #   索要空间，会把卡片撑变形、把整排挤歪。
            _lb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        v.addWidget(self.lb_title)
        v.addWidget(self.lb_value)
        v.addWidget(self.lb_sub)

        # ★★ v2.0.1：量高度之前必须先 `ensurePolished()`。
        #
        #   这是本项最容易"改了却没修好"的地方：
        #   QSS 里的 `font-size: 21px` **不会**在控件构造那一刻就落到 QFont 上，
        #   它是在控件 polish（首次显示前）阶段才应用的。
        #   如果直接在 __init__ 里量 fontMetrics，量到的是 app 默认的 9pt，
        #   算出来的高度只有 ~74px；而按 21px 真正渲染需要 ~84px。
        #   卡片又用 setMaximumHeight 把这个偏小的值**钉死**了 ——
        #   结果就是"代码里明明按内容算了高度，屏幕上的大字还是被切"。
        #   先 polish，再量，量到的才是最终会渲染的那套字体。
        self.ensurePolished()
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._recalibrate_height()

    def _recalibrate_height(self):
        """按当前（已 polish 的）字体重算并锁定高度。

        ★ 除了 __init__，showEvent 里还会再调一次：不同平台上 QSS 生效的
          确切时刻略有差异（有的要等窗口真正显示）。两处都算一遍，
          代价可忽略，换来的是"任何 DPI / 字体 / Qt 版本下都不会裁字"。
        """
        h = self._natural_height()
        if h != self.maximumHeight():
            self.setMinimumHeight(h)
            self.setMaximumHeight(h)
            self.updateGeometry()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._recalibrate_height()

    def _natural_height(self):
        """三行文字实际所需高度（含行距与内边距），最少 62px。

        ★ v2.1.0：下界 74 → 62，内边距余量 2 → 1（用户反馈空白过多）。
          74 是 v1.1.x 定的：当时 KPI 卡下面还压着两张图，行高宽松些好看。
          现在总览只有 KPI 行 + 一张大曲线，KPI 卡高 74px 显得虚胖 ——
          三行文字（11.5 / 21 / 10.5px）实际约 55px 就够，62 已留足呼吸。
          ⚠ 下界仍是"足够容纳三行"的安全值，不会裁字；
            真裁字由 showEvent 的二次 recalibrate 兜住（按实际 fontMetrics 算）。
        """
        try:
            lay = self.layout()
            m = lay.contentsMargins()
            fms = (self.lb_title.fontMetrics(), self.lb_value.fontMetrics(),
                   self.lb_sub.fontMetrics())
            inner = sum(fm.height() for fm in fms) + 2 * lay.spacing()
            return max(62, inner + m.top() + m.bottom() + 1)
        except Exception:                                # noqa: BLE001
            return 66                                    # 兜底：与实测值同量级

    def sizeHint(self):
        return QSize(super().sizeHint().width(), self._natural_height())

    def minimumSizeHint(self):
        return QSize(60, self._natural_height())

    def set_value(self, value, sub=None, color=None):
        """更新数值（及副标题、颜色）。

        ★ color 必须**无条件**写入（v1.1.6 修复）：
          旧写法 `if color: setStyleSheet(...)` 在 color 为 None 时直接跳过，
          于是 styleSheet 里**残留上一次的颜色** ——
             · 数据从"有涨跌幅"变成"—"（无数据）时，"—"还是红的/绿的；
             · 切换配色方向时，靠 fill_kpi 重新着色的卡片若走到无 color 分支，
               会停在旧方向的红绿上（"字变了底没变"的变体）。
          实测：先 set_value(color='#e0393e') 再 set_value(color=None)，
          styleSheet() 仍是 'color:#e0393e;' —— 确认是缺陷。
          改为每次显式设置：有 color 用之，无 color 则清空（回到 QSS 默认色）。
        """
        self._raw_value = str(value)
        if sub is not None:
            self._raw_sub = str(sub)
        # 无条件覆盖：None → 空串 = 交还给 QSS 的 #kpiValue 默认色
        self.lb_value.setStyleSheet(f"color:{color};" if color else "")
        self._apply_elide()

    # ---------------------------------------------------------- 窄窗口自适应
    def _apply_elide(self):
        """按当前宽度给三行文字打省略号（v1.1.6）。

        ★ 为什么需要：窗口缩到最小尺寸时 6 张 KPI 卡并排，每张只有 ~160px。
          副标题「上证 +1.23% · 全A +1.00%」这种长文本会被 QLabel 硬裁 ——
          显示成半截字且看不出后面还有内容，比省略号更难读。
          这里保留原始文本，按控件实际宽度重新计算省略形式；
          完整文本仍可通过 tooltip 查看。
        """
        try:
            fm_v = self.lb_value.fontMetrics()
            fm_s = self.lb_sub.fontMetrics()
            fm_t = self.lb_title.fontMetrics()
            avail = max(20, self.width() - 24)      # 扣掉左右内边距(12+12)
            self.lb_value.setText(
                fm_v.elidedText(self._raw_value, Qt.ElideRight, avail))
            self.lb_sub.setText(
                fm_s.elidedText(self._raw_sub, Qt.ElideRight, avail))
            self.lb_title.setText(
                fm_t.elidedText(self._raw_title, Qt.ElideRight, avail))
            # 省略后把完整内容放进 tooltip，鼠标一停就能看到全称
            if self._raw_value:
                self.lb_value.setToolTip(self._raw_value)
            if self._raw_sub:
                self.lb_sub.setToolTip(self._raw_sub)
        except Exception:                                # noqa: BLE001
            self.lb_value.setText(self._raw_value)
            self.lb_sub.setText(self._raw_sub)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._apply_elide()


def make_card(title=None, hint=None, body_layout=None, panel_id=None):
    """带标题栏的圆角卡片容器。返回 (外框, 内容区布局)。

    用法：
        card, body = make_card("主力净流入榜")
        body.addWidget(table)

    ★★ v2.2.0 新增 `panel_id` 参数：把该卡片登记为 PANELS 清单里的一个
      「面板（孙模块）」。登记方式是把 id 写进 frame 的 `panelId` 动态属性
      （**不覆盖 objectName** —— objectName 要留给 QSS 选择器 `#card`）。
      守恒断言据此数出运行期真实面板集合，与 PANELS 比对。

      panel_id=None 表示该卡片**不登记**（用于临时/装饰性卡片）。
      ⚠ 若某卡片忘了传 panel_id，它就不会进入守恒断言 —— 漏传不报错，
        只是那个面板永远无法被验证到。故新增卡片时**必须**同步 PANELS 表。

    ★ 边距刻意收紧（8/6/8/8）：卡片原先上下留 10~12px，一屏里两三层卡片
      叠加就是几十像素的纯空白。压到 6~8px 后视觉上仍然透气，但信息密度明显提升。

    ★★ v2.1.0：再收紧一档 —— 外边距 9,7,9,9 → 7,5,7,7、标题行间距 5 → 4。
      起因：用户反馈「大部分地方有很长很大块的空白区」。实测在 1480×940 下，
      一张卡片四周的纯空白（外边距 + 标题行 + 标题与内容间距）合计约 34px；
      一个母模块 4 张卡片叠起来就是 130px+ 的空白，占掉屏幕近 1/7。
      收紧后同一屏能多显示约 1 行表格内容。**不删任何标题/提示**，只压缩留白。
      子页签结构本身也省掉了一行（原来每个子模块各占一个顶层页签的整条高度）。
    """
    frame = QFrame()
    frame.setObjectName("card")
    if panel_id is not None:
        # ★ 用动态属性而不是 objectName：objectName 必须是 "card" 才能被
        #   QSS 的 `QFrame#card` 选中（改掉会立刻丢掉圆角与描边）。
        #   Qt 允许任意动态属性，且 findChildren 后可直接读。
        frame.setProperty("panelId", panel_id)
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(7, 5, 7, 7)
    outer.setSpacing(4)

    if title:
        head = QHBoxLayout()
        head.setSpacing(6)
        head.setContentsMargins(0, 0, 0, 0)
        lb = QLabel(title)
        lb.setObjectName("cardTitle")
        head.addWidget(lb)
        if hint:
            lb2 = QLabel(hint)
            lb2.setObjectName("cardHint")
            # ★★ v1.2.1 修复：提示文字必须**可折行 + 不参与最小宽度计算**。
            #
            #   原先是一个裸 QLabel：它的 sizeHint 宽度 = 整行文字的像素宽，
            #   而 QLabel 默认水平策略是 Preferred → **这个宽度会被算进
            #   整个页面的 minimumSizeHint**。于是提示文案越长，
            #   窗口的最小宽度就越大，用户根本没法把窗口缩窄。
            #
            #   实测后果：v1.2.0 把题材簇卡片的提示写长之后，
            #   该页最小宽度涨到 1600px，窗口最小尺寸变成 1606px ——
            #   连 1480px 都到不了（`--shot` 的尺寸钉死失效，
            #   分辨率 1366 的笔记本上更是直接放不下）。
            #   setWordWrap 让它折行；水平策略设为 Ignored 让它彻底退出
            #   "最小宽度"的计算（宽度由布局剩余空间给，配合 stretch=1）。
            lb2.setWordWrap(True)
            lb2.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            head.addWidget(lb2, 1)
        head.addStretch(1)
        outer.addLayout(head)

    body = body_layout if body_layout is not None else QVBoxLayout()
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(0)
    outer.addLayout(body, 1)
    return frame, body


def make_labeled_table(title, headers, hint=None, stretch_col=1,
                       align=None, widths=None, panel_id=None):
    """标题 + 表格的卡片，返回 (卡片, 表格)。

    ★★ v2.2.0：新增 `panel_id` 直通 make_card —— 本函数建的每一张卡片
      都是一个「面板（孙模块）」，必须登记（见 make_card 的说明）。
    """
    card, body = make_card(title, hint, panel_id=panel_id)
    t = make_table(headers, stretch_col=stretch_col, align=align, widths=widths)
    body.addWidget(t)
    return card, t


# ==================================================================== 主窗口
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"资金流向 · 原生版 v{VERSION}")
        # ★★ v2.0.1：窗口级图标（任务栏 / Alt+Tab / 左上角）。
        #   QApplication 级已经设过一次，这里再设一次是因为：
        #   Windows 上"任务栏按钮的图标"取的是**窗口**的 windowIcon，
        #   而某些窗口管理器在窗口创建早于 app 图标设置时会缓存空图标。
        #   两处都设，代价为零，可靠性翻倍。
        try:
            _ic = app_icon()
            if not _ic.isNull():
                self.setWindowIcon(_ic)
        except Exception:                                # noqa: BLE001
            pass
        if "--shot" in sys.argv:
            # 截图自检模式：尺寸钉死，保证「源码版 vs exe 版」逐字节可比
            self.resize(1480, 940)
        else:
            self._fit_initial_size()
        self.data = {}
        # ★★ v2.0.3：图表画布延迟创建的槽位登记表 ——
        #   每项 (attr名, 布局, stretch, 工厂lambda)，由 _materialize_charts 消费。
        self._chart_slots = []
        self.theme_name = "light"
        self.worker = None
        self.timer_auto = None
        self._closing = False
        # ★★ v3.0.4：默认档从 30 秒改为 3 秒（用户要求 1~10 秒、越频繁越好）。
        #   配套改动见 AUTO_MS_OPTIONS 与 REFRESH_MIN_GAP_SEC 的注释。
        self._auto_ms = AUTO_MS_DEFAULT
        # ★ v3.0.4：跨分钟守卫的游标 —— 记录上一次**真正发起抓取**的交易分钟。
        #   None 表示"还没抓过"（启动首轮必定立即抓，不会等一分钟）。
        self._last_fetch_bar = None
        # ---- v1.1.0 历史回看 ----
        self.backfiller = None                 # BackfillWorker 实例
        self.calendar_days = []                # 交易日历（YYYYMMDD 升序）
        self.calendar_approx = False           # 日历是否只是工作日推断兜底
        self.live_tag = datetime.now().strftime("%Y%m%d")   # 实盘 tag（今日）
        self._auto_backfill_done = False       # 每次启动最多自动补全一次
        # ★ v1.1.7：启动即锁定回看（`--date`）。**必须在 __init__ 就置位**，
        #   不能只靠 `current_tag() != live_tag` 在切完之后拦。
        #   踩过的坑：--shot --date 的流程是「先加载实盘 → 数据到位 → 才切历史日」，
        #   在这段实盘窗口里 current_tag() 仍等于 live_tag，_auto_tick 的守卫
        #   会放行 → reload(force=True) → Worker 跑 pool.update() 并 flush()。
        #   后果有二：① 榜单池被写盘、无端多出轮次（实测 exe 版因 onefile
        #   解压更慢、实盘窗口更长，比源码版多跑了整整一轮，池子 30→34）；
        #   ② 用户"准备回看"的这段时间里后台已经开始迭代，等他切回当日会发现
        #   池子里混着回看前的旧轮次。启动即抑制，才彻底。
        self._review_locked = "--date" in sys.argv
        # ★ v1.2.1：worker 忙时用户选的日期暂存于此，加载完成后补跳
        #   （见 load_history / on_data —— 旧实现把用户的选择直接丢掉）。
        self._pending_tag = None
        # ★ v1.2.1：归档落盘失败警告（由 on_data 写入、show_status 展示）
        self._archive_warn = ""
        # ---- v1.2.1 运行状态文本 ----
        #   原先由「数据状态」页承载，该页删除后改为缓存到这里，
        #   由 帮助 →「运行状态」弹窗查看。必须**在 _build_ui 之前**初始化，
        #   否则首屏加载前点菜单会拿到未定义属性。
        self.status_text = ""
        # ---- v1.1.5 龙头领先反应 ----
        self.leader_worker = None              # LeaderWorker 实例
        self.leader_rows = []                  # 最近一次分析结果
        self.leader_key = None                 # 已分析的数据标识（避免重复跑）
        self._leader_tag = None                # 本轮结果对应的交易日（缓存键）
        self.timer_leader = None               # 盘中周期性重跑（5 分钟）
        # ---- 悬停联动结果缓存（v1.1.6 性能）----
        self._hover_key = None                 # 缓存归属的数据标识（date_tag）
        self._hover_cache = {}                 # {分钟号: (榜单, 合计)}
        # ---- ★ v2.0.1：按页延迟绘制 ----
        #   登记"数据已更新、但图表还没重画"的那些图（见 CHART_PAGE）。
        #   必须在 _build_ui 之前初始化：_build_ui 里会连 currentChanged 信号，
        #   若首个信号早于本次赋值到达，on_tab_changed 会读到未定义属性。
        self._charts_dirty = set()
        self._build_ui()

    def _fit_initial_size(self):
        """★ 初始尺寸自适应屏幕（v1.0.5 修复）：
        旧版固定 1480x940，在 1366x768 这类小屏笔记本上直接超出屏幕。
        现在按可用区域（扣掉任务栏）收缩，并给出可读的最小尺寸下限，
        窄窗口下布局不会挤成一团。"""
        self.setMinimumSize(1020, 660)
        try:
            scr = QApplication.primaryScreen()
            av = scr.availableGeometry()
            w = min(1480, int(av.width() * 0.96))
            h = min(940, int(av.height() * 0.96))
        except Exception:                                # noqa: BLE001
            w, h = 1480, 940
        self.resize(w, h)

    def closeEvent(self, ev):
        """安全关闭：请求后台任务协作结束，绝不强制终止正在写缓存的线程。

        ``QThread.terminate()`` 可在 Python 网络调用或原子写入中间截断执行，
        会造成缓存损坏，并且逐线程串行等待会冻结窗口十余秒。这里改为统一
        请求中断、使用一个总等待预算；未结束时保留窗口并异步重试关闭。
        """
        if self.timer_auto is not None:
            self.timer_auto.stop()
        if getattr(self, "snap_timer", None) is not None:
            self.snap_timer.stop()

        threads = [t for t in (getattr(self, "snap_worker", None), self.worker,
                               self.backfiller, self.leader_worker)
                   if t is not None and t.isRunning()]
        if not threads:
            ev.accept()
            return

        self._closing = True
        for thread in threads:
            try:
                thread.requestInterruption()
            except Exception:                            # noqa: BLE001
                pass

        # 一个总预算而不是每个线程各等数秒；先给已经收尾的线程机会退出。
        deadline = time.monotonic() + 1.2
        for thread in threads:
            remain_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if remain_ms <= 0:
                break
            try:
                thread.wait(remain_ms)
            except Exception:                            # noqa: BLE001
                pass

        still_running = [t for t in threads if t.isRunning()]
        if still_running:
            ev.ignore()
            self.lbl.setText("正在安全结束后台任务，请稍候…")
            # 网络调用的超时由数据层控制；不杀线程，等其完成后再执行关闭。
            QTimer.singleShot(250, self.close)
            return
        ev.accept()

    # ---------------------------------------------------------- UI
    def _make_group_page(self, group_name, sub_names):
        """★ v2.1.0：为一个母模块建"外壳页"（内含子页签），返回 (外壳页, 子页签控件)。

        设计要点与理由：

        1. **外壳只做容器，不承载内容** —— 母模块的主题由标签本身表达，
           再在页内加一条"标题栏"只是重复信息、白占一行高度。
           所以外壳是零边距的：内容区完全交给子页（各子页自己带 PAD_PAGE），
           避免出现"页边距 + 子页边距"叠成两层空白（那正是要消掉的东西）。

        2. **子页签用 QTabWidget 而不是折叠面板/分段按钮**：
           子模块数量是 1~4 个且都要随时可见、一键切换，页签是最省的形态；
           折叠面板要两次点击才能看到内容，分段按钮则无法安放"关闭滚动"的语义。

        3. **setDocumentMode(True) 与顶层一致** —— 否则子层会画出 Win 原生
           的凸起边框，在扁平主题里格格不入。

        4. **objectName="subTabs"** 供 QSS 精确匹配（见 build_qss 的子页签段），
           这样"下划线式次级指示"只作用于子层，不会污染顶层页签。

        5. 子页签条高度用 setFixedHeight(SUB_TAB_H) 钉死：
           否则不同母模块的子页数量不同（1 vs 4）会算出不同高度，
           切母模块时整页内容会上下跳几像素。
        """
        shell = QWidget()
        shell.setObjectName("pageRoot")
        lay = QVBoxLayout(shell)
        lay.setContentsMargins(*SUB_PANE_PAD)   # ★ 零边距，见上文第 1 点
        lay.setSpacing(0)

        sub = QTabWidget()
        sub.setObjectName("subTabs")
        sub.setDocumentMode(True)
        try:
            sub.tabBar().setFixedHeight(SUB_TAB_H)
        except Exception:                        # noqa: BLE001
            pass
        lay.addWidget(sub)

        # 母模块登记（供跳页 / --shot 断言用；总览无子页不进这里）
        self.group_tabs[group_name] = sub
        return shell, sub

    def _build_ui(self):
        self._build_menu()
        self._build_toolbar()

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        # ★ v2.1.0：母模块名 → 其内部的子页签控件。总览不登记（它是单页）。
        self.group_tabs = {}

        # ---------------------------------------------------------- 1 总览
        # ★★ v2.0.1 结构（问题九）：KPI 卡行  →  放大的资金曲线。
        #   原来的「指数分时 | 流入流出对比」与「时点数据面板」已移到
        #   新页「市场全景」，理由见下方曲线段的注释。
        # ★★ v2.1.0：总览是**唯一不打包**的模块（用户明确要求"除了打开的
        #   首页总览这个模块"），仍是顶层单页、无子页。
        page = QWidget(); page.setObjectName("pageRoot")
        v = QVBoxLayout(page)
        v.setContentsMargins(*PAD_PAGE)
        v.setSpacing(GAP_PAGE)

        # --- KPI 卡行 ---
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(GAP_CARD)
        self.kpi_index = KpiCard("上证指数", "—", "")
        self.kpi_in = KpiCard("主力净流入板块", "—", "", accent=None)
        self.kpi_out = KpiCard("主力净流出板块", "—", "", accent=None)
        self.kpi_net = KpiCard("全市场净额", "—", "")
        self.kpi_top_in = KpiCard("最强流入", "—", "")
        self.kpi_top_out = KpiCard("最强流出", "—", "")
        self.kpi_cards = [self.kpi_index, self.kpi_in, self.kpi_out,
                          self.kpi_net, self.kpi_top_in, self.kpi_top_out]
        for c in self.kpi_cards:
            kpi_row.addWidget(c, 1)
        v.addLayout(kpi_row)

        # --- 主力资金累计曲线（★ v2.0.1：独占整页，高度约为原来的两倍）---
        #
        # ★★ 问题九的版面重构：原先这里是「曲线(7) + 时点面板(3)」并排，
        #   曲线只有 3 英寸高；上面还压着「指数分时 + 流入流出对比」两张图。
        #   结果是总览页三张图每张都被压扁 —— 而总览恰恰是打开程序第一眼看到的页。
        #
        #   现在：指数分时、流入/流出对比、时点数据面板**全部移到新页「市场全景」**，
        #   总览只留 KPI 卡行 + 这一张最大的曲线。
        #   为什么这样更好：
        #     · 曲线的价值全在"20 条线之间的分叉与拐点"，需要**垂直空间**才读得出来；
        #     · 指数与流向对比属于"大盘背景"，与"板块主力动向"是两类信息，
        #       混在一屏里反而彼此干扰；
        #     · KPI 卡行已经把"大盘怎么样"压缩成了六个数字，背景信息并未丢失。
        # ★★ v2.0.3：画布延迟创建 —— 此处先置 None，窗口显示后由
        #   _materialize_charts() 统一补建（matplotlib import ~1.0s 挪到
        #   窗口显示之后，双击 → 窗口出现提前约 1 秒）。
        self.ch_fan = None
        card_fan, body_fan = make_card("主力资金累计曲线",
                                       panel_id="overview.fan_curve")
        # 悬停摘要行：一个时刻只占一行，不挤压曲线高度。
        # ★ 为什么不在这里保留原来那张 7 行的表格：
        #   一屏里表格会把曲线再压掉三成高度，而"看某分钟前十名"是**查询式**需求，
        #   放到「市场全景」页整宽展示反而更清楚（一屏能看到流入+流出共 14 行）。
        #   这里只留一行摘要，保证"指着曲线就能读出这一点是多少"。
        self.lbl_fan_tip = QLabel("时点 ——，右端标签为收盘金额")
        self.lbl_fan_tip.setObjectName("cardHint")
        self.lbl_fan_tip.setWordWrap(False)
        self.lbl_fan_tip.setMinimumWidth(0)
        self.lbl_fan_tip.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        body_fan.addWidget(self.lbl_fan_tip)
        self._chart_slots.append(("ch_fan", body_fan, 1, lambda: FanChart(6.4)))
        v.addWidget(card_fan, 1)

        self.tabs.addTab(page, "总览")

        # ================================================== 母模块「大盘」
        # ★ v2.1.0：新开母模块。此后直到下一个"开母模块"标记之前的
        #   所有子模块，全部 addTab 进 _gp（该母模块的子页签）。
        _shell, _gp = self._make_group_page("大盘", TAB_TREE["大盘"])

        # ---------------------------------------------------------- 1.5 市场全景
        # ★★ v2.0.1 新增页（问题九：把三块内容"移出并单独开一个板块"）。
        #   内容 = 指数分时（上证 + 东财全A）+ 主力资金流入/流出对比 + 时点数据面板。
        #   版面：
        #     上排 左右并排 —— 指数分时(4) | 流入流出对比(5)（比例与原总览一致）
        #     下排 整宽     —— 时点数据面板（原来是 3/10 宽的窄侧栏，现在整宽）
        #   为什么下排整宽：那张表要显示"该分钟流入前 7 + 流出前 7"，
        #   窄侧栏里题材名会被省略号截断，宽版面下能完整显示。
        w = QWidget(); w.setObjectName("pageRoot")
        g = QGridLayout(w)
        g.setContentsMargins(*PAD_PAGE); g.setSpacing(GAP_PAGE)

        self.ch_index = None           # v2.0.3：延迟创建（见 _materialize_charts）
        card_idx, body_idx = make_card("指数分时（上证 + 东财全A）",
                                       "各自以开盘为基准的涨跌幅 · 全A 覆盖沪深全市场",
                                       panel_id="market_overview.index")
        self._chart_slots.append(("ch_index", body_idx, None, lambda: IndexChart(4.0)))
        g.addWidget(card_idx, 0, 0)

        self.ch_flow = None            # v2.0.3：延迟创建（见 _materialize_charts）
        card_flow, body_flow = make_card("主力资金流入 / 流出对比",
                                         "右侧红=流入 · 左侧绿=流出",
                                         panel_id="market_overview.flow_bar")
        self._chart_slots.append(("ch_flow", body_flow, None, lambda: FlowBarChart(4.0)))
        g.addWidget(card_flow, 0, 1)

        # ★★ v2.0.6：独立的「时点数据面板」已删除 —— 悬停联动改为
        #   **直接写进总览页累计曲线的右端标签**（鼠标指到哪一分钟，
        #   每条曲线的标签就显示那一天的该分钟数据，移出恢复终点值）。
        #   实现见 FanChart._apply_hover_labels / _labels_blit；
        #   总览页仅保留一行时点摘要（lbl_fan_tip，见 on_fan_hover）。
        #   （v1.2.2 时代关于 lbl_hover 撑宽侧栏的整段防御随之作废。）

        # ★★ v2.0.10：「区间极值」板块（第二行，左右两张卡）——
        #   吸筹 = 最新点 − 盘中最低点 降序前十；出货 = 最新点 − 盘中最高点
        #   仅保留负差值、按回撤降序前十。数据与总览累计曲线同口径（F.V 列）。
        card_ra, body_ra = make_card(
            "区间极值 · 主力吸筹前十",
            "最新点 − 盘中最低点，差值降序：尾盘较盘中低点回升最多的板块",
            panel_id="market_overview.range_in")
        self.t_ra = make_table(
            ["排名", "板块名称", "最新值(亿)", "盘中最低(亿)", "差值(亿)"],
            stretch_col=1, align={0: "c", 1: "l"},
            widths={0: 44, 2: 92, 3: 96, 4: 86})
        body_ra.addWidget(self.t_ra)
        g.addWidget(card_ra, 1, 0)
        card_sh, body_sh = make_card(
            "区间极值 · 主力出货前十",
            "最新点 − 盘中最高点，仅保留差值为负：距盘中高点回撤最大的板块",
            panel_id="market_overview.range_out")
        self.t_sh = make_table(
            ["排名", "板块名称", "最新值(亿)", "盘中最高(亿)", "差值(亿)"],
            stretch_col=1, align={0: "c", 1: "l"},
            widths={0: 44, 2: 92, 3: 96, 4: 86})
        body_sh.addWidget(self.t_sh)
        g.addWidget(card_sh, 1, 1)

        # ★★ v2.0.12：田字格**等分**（用户要求「四个模块均匀地按田字格分布」）。
        #   旧值列 4:5 —— 实测左列 647px / 右列 809px，**差 162px（20%）**：
        #   一眼看过去左栏明显窄一截，四宫格不是正方形，视觉重心右偏。
        #   那对 4:5 是从 v2.0.1「指数分时(4) | 流入流出对比(5)」沿用的旧比例，
        #   当时这两张图**并排在同一行**、按图重要性分宽窄还有道理；
        #   现在它们各自独占一格、上下两行都是「图表 / 表格」混排，
        #   再按旧比例分宽窄就只剩副作用了。
        #   改为 1:1 → 两列各 ≈ (1480−8−8−8)/2 = 728px，与已等分的两行
        #   （rowStretch 1:1）共同构成严格 2×2 田字格。
        #   ⚠ 修正列宽会改变图表画布宽度 → matplotlib 的字体/刻度布局随之重算，
        #     四张图的 PNG 都会变，属**预期变更**，受控比对时不能当回归看。
        g.setColumnStretch(0, 1); g.setColumnStretch(1, 1)
        g.setRowStretch(0, 1); g.setRowStretch(1, 1)
        _gp.addTab(w, "市场全景")

        # ---------------------------------------------------------- 开盘检测
        # ★★ v2.0.4（用户需求一）：开盘后 5/10/15 分钟节点的板块
        #   「成交量 Top5」与「主力净流入 Top5」。
        #   布局：三列 = 三个时间节点（同一节点的两项指标上下相邻、
        #   不同节点左右并排）→ 横向对比同指标跨节点、纵向对比同节点两指标。
        #   数据来自 core/opensnap.py 的定点快照；缺失节点诚实标注「未采集」。
        w = QWidget(); w.setObjectName("pageRoot")
        v_open = QVBoxLayout(w)
        v_open.setContentsMargins(*PAD_PAGE); v_open.setSpacing(GAP_PAGE)
        g_open = QGridLayout(); g_open.setSpacing(GAP_PAGE)
        self.t_open = {}                     # (node_key, kind) -> QTableWidget
        for _ci, (_mi, _trig, _lab, _key) in enumerate(
                self._snap_nodes()):
            key = _key; lab = _lab
            t_v = make_table(
                ["排名", "板块名称", "成交量(万手)", "成交额(亿)"],
                stretch_col=1, align={0: "c", 1: "l"},
                widths={0: 44, 2: 110, 3: 90})
            card_v, body_v = make_card(
                f"{lab} · 成交量 Top5",
                "按开盘以来累计成交量降序 · 节点时刻全市场快照",
                panel_id=f"open_check.{key}_vol")
            body_v.addWidget(t_v)
            t_v.setMinimumHeight(190)
            g_open.addWidget(card_v, 0, _ci)
            t_n = make_table(
                ["排名", "板块名称", "主力净流入(亿)"],
                stretch_col=1, align={0: "c", 1: "l"},
                widths={0: 44, 2: 130})
            card_n, body_n = make_card(
                f"{lab} · 主力净流入 Top5",
                "按开盘以来累计主力净流入降序",
                panel_id=f"open_check.{key}_net")
            body_n.addWidget(t_n)
            t_n.setMinimumHeight(190)
            g_open.addWidget(card_n, 1, _ci)
            self.t_open[(key, "vol")] = t_v
            self.t_open[(key, "net")] = t_n
        for _c in range(3):
            g_open.setColumnStretch(_c, 1)
        g_open.setRowStretch(0, 1); g_open.setRowStretch(1, 1)
        v_open.addLayout(g_open)
        _gp.addTab(w, "开盘检测")

        # ★ v2.1.0：母模块「大盘」装配完毕 —— 最后一次 addTab 后收口。
        self.tabs.addTab(_shell, "大盘")

        # ================================================== 母模块「个股」
        # ★ v2.1.0：2 个子模块同主题「个股层面的标的筛选」——
        #   异动（3 节点 × 3 指标 Top10）与 精选（九榜出现次数排名）。
        #   关系：异动是"看每张榜"，精选是"把九张榜合成一张"——
        #   同一批数据的两个视角，放同一母模块内切换最顺。
        _shell, _gp = self._make_group_page("个股", TAB_TREE["个股"])

        # ---------------------------------------------------------- 个股异动
        # ★★ v2.0.4（用户需求二）：3 节点 × 3 指标（量比/成交量/主力净流入）
        #   个股 Top10。表内同时给出三项指标数值，便于同场对比。
        # ★★ v2.0.7（用户需求）布局调整：
        #   · 子级「个股精选」榜移出，独立成页（见下一节）；
        #   · 九张 Top10 表**去掉滚动容器**，3×3 网格直接铺满整页——
        #     行/列等分拉伸（row stretch 1:1:1），每张表的 10 行用
        #     QHeaderView.Stretch **纵向均分表高**，刚好占满一屏；
        #     窗口过矮时表内自滚动，不会溢出或互相重叠。
        w = QWidget(); w.setObjectName("pageRoot")
        g_st = QGridLayout(w)
        g_st.setContentsMargins(*PAD_PAGE); g_st.setSpacing(GAP_PAGE)
        self.t_stock = {}                    # (node_key, metric) -> QTableWidget
        _METRICS = (("lb", "量比 Top10", "按节点时刻量比降序"),
                    ("vol", "成交量 Top10", "按开盘以来累计成交量(手)降序"),
                    ("net", "主力净流入 Top10", "按开盘以来累计主力净流入降序"))
        for _ci, (_mi, _trig, _lab, _key) in enumerate(self._snap_nodes()):
            key = _key; lab = _lab
            for _ri, (mk, mtitle, mhint) in enumerate(_METRICS):
                t = make_table(
                    ["排名", "名称", "代码", "量比", "成交量(万手)",
                     "净流入(亿)", "涨幅%"],
                    stretch_col=1,
                    align={0: "c", 1: "l", 2: "c"},
                    widths={0: 44, 2: 64, 3: 62, 4: 96, 5: 90, 6: 68})
                # ★ v2.0.7：10 行**纵向均分**表高 —— 表占满分到的格子，
                #   行与行均匀铺开，不留底部空白（总览网格等分的关键）。
                t.verticalHeader().setSectionResizeMode(QHeaderView.Stretch)
                card, body = make_card(f"{lab} · {mtitle}", mhint,
                                       panel_id=f"stock_move.{key}_{mk}")
                body.addWidget(t)
                g_st.addWidget(card, _ri, _ci)
                self.t_stock[(key, mk)] = t
        for _c in range(3):
            g_st.setColumnStretch(_c, 1)
        for _r in range(3):
            g_st.setRowStretch(_r, 1)
        _gp.addTab(w, "个股异动")

        # ---------------------------------------------------------- 个股精选
        # ★★ v2.0.7（用户需求）：从「个股异动」页移出，独立成页整宽展示。
        #   统计个股在 3 节点 × 3 指标共九个前十榜中的出现次数：
        #   九项全部上榜的排第一梯队（★），其余按次数降序、同次数按净流入降序。
        #   数据源与填充逻辑不变（self.t_select，fill_open_pages 统一填）。
        w = QWidget(); w.setObjectName("pageRoot")
        v_sel = QVBoxLayout(w)
        v_sel.setContentsMargins(*PAD_PAGE); v_sel.setSpacing(GAP_PAGE)
        card_sel, body_sel = make_card(
            "个股精选 · 九榜出现次数排名",
            "统计个股在 3 节点 × 3 指标共九个前十榜中的出现次数："
            "九项全部上榜的排第一梯队（★），其余按次数降序、同次数按净流入降序",
            panel_id="stock_pick.nine_boards")
        self.t_select = make_table(
            ["排名", "名称", "代码", "上榜次数", "上榜明细", "主力净流入(亿)"],
            stretch_col=4, align={0: "c", 1: "l", 2: "c", 3: "c"},
            widths={0: 52, 2: 76, 3: 78, 5: 120})
        body_sel.addWidget(self.t_select, 1)
        v_sel.addWidget(card_sel, 1)
        _gp.addTab(w, "个股精选")

        # ★ v2.1.0：母模块「个股」装配完毕。
        self.tabs.addTab(_shell, "个股")

        # ============================================ 母模块「资金排行」
        # ★ v2.1.0：4 个子模块同主题「资金在哪个板块、排第几」——
        #   辨识度（五维综合分排行）/ 雷达（相对自身波动率的异常扫描）/
        #   行业（一级行业层级拆解）/ 实时榜单（全天累积池，只增不减）。
        #   为什么归一类：都是**排行/扫描**视角；差别只在切面
        #   （累计分 / 异常 z / 行业层级 / 累积池），主体始终是"板块 + 资金"。
        _shell, _gp = self._make_group_page("资金排行", TAB_TREE["资金排行"])

        # ---------------------------------------------------------- 1.8 异动雷达
        # ★★ v2.0.1 新增页（问题十）。
        #
        #   为什么需要单独一页、而不是塞进已有的榜：
        #     「辨识度榜」的权重里"累计体量"占 0.34，大板块天然靠前；
        #     「5 分钟榜」看的是当下速度。两者都会让**体量小但异常明显**的
        #     细分题材（被动元件、电子布、光模块这类）排到看不见的位置。
        #     雷达把"相对**自身**波动率的异常"（|z|）放在最重的位置，
        #     专门补这一块 —— 用户点名的"光纤/光模块/PCB/存储尾盘回流"
        #     正是这一类。
        #
        #   两侧按**近 5 分钟增量的符号**分侧（而不是累计净额的符号）：
        #   异动关心的是"此刻在动"，累计是存量、会滞后。
        #   表中给出「识别原因」列，说明这条是被哪一维选中的 ——
        #   雷达是高召回的，不给原因就无法判断"真异动还是噪声"。
        w = QWidget(); w.setObjectName("pageRoot")
        g = QGridLayout(w)
        g.setContentsMargins(*PAD_PAGE); g.setSpacing(GAP_PAGE)
        _radar_align = {0: "c", 1: "l", 2: "r", 3: "r", 4: "r", 5: "r", 6: "l"}
        c_r1, self.t_radar_in = make_labeled_table(
            "异动雷达 · 流入（此刻资金异常涌入）",
            ["序号", "题材", "净额(亿)", "5分钟(亿)", "z", "雷达分", "识别原因"],
            hint="四项横截面分位加权：z 突异 / 速度异动 / 排名跃迁 / 尾盘资金回流"
                 "　·　不依赖任何板块名单，新出现的题材有异动即自动纳入",
            stretch_col=6, align=_radar_align, widths={0: SEQ_COL_W},
            panel_id="radar.inflow")
        c_r2, self.t_radar_out = make_labeled_table(
            "异动雷达 · 流出（此刻资金异常撤离）",
            ["序号", "题材", "净额(亿)", "5分钟(亿)", "z", "雷达分", "识别原因"],
            hint="方向按近 5 分钟增量的符号判定　·　识别原因 = 该条最突出的那一维",
            stretch_col=6, align=_radar_align, widths={0: SEQ_COL_W},
            panel_id="radar.outflow")
        g.addWidget(c_r1, 0, 0); g.addWidget(c_r2, 0, 1)
        g.setColumnStretch(0, 1); g.setColumnStretch(1, 1)
        _gp.addTab(w, "异动雷达")

        # ---------------------------------------------------------- 2 辨识度榜
        w = QWidget(); g = QGridLayout(w)
        g.setContentsMargins(*PAD_PAGE); g.setSpacing(GAP_PAGE)
        # 排名列定宽 46px + 居中：序号紧贴左侧，题材名左对齐紧跟其后，
        # 两者之间只隔一个单元格内边距（原来题材名被推到 685px 列的最右端）。
        RANK_W = {0: 46}
        RANK_A = {0: "c", 1: "l"}
        c1, self.t_in = make_labeled_table(
            "主力净流入榜", ["排名", "题材", "净额(亿)", "5分钟(亿)", "辨识度"],
            align=RANK_A, widths=RANK_W, panel_id="salience.inflow")
        c2, self.t_out = make_labeled_table(
            "主力净流出榜", ["排名", "题材", "净额(亿)", "5分钟(亿)", "辨识度"],
            align=RANK_A, widths=RANK_W, panel_id="salience.outflow")
        g.addWidget(c1, 0, 0); g.addWidget(c2, 0, 1)
        g.setColumnStretch(0, 1); g.setColumnStretch(1, 1)
        _gp.addTab(w, "辨识度榜")

        # ---------------------------------------------------------- 3 行业概况
        w = QWidget(); g = QGridLayout(w)
        g.setContentsMargins(*PAD_PAGE); g.setSpacing(GAP_PAGE)
        c1, self.t_ind_in = make_labeled_table(
            "行业净流入", ["排名", "一级行业", "净额(亿)", "5分钟(亿)", "辨识度"],
            align=RANK_A, widths=RANK_W, panel_id="industry.inflow")
        c2, self.t_ind_out = make_labeled_table(
            "行业净流出", ["排名", "一级行业", "净额(亿)", "5分钟(亿)", "辨识度"],
            align=RANK_A, widths=RANK_W, panel_id="industry.outflow")
        g.addWidget(c1, 0, 0); g.addWidget(c2, 0, 1)
        g.setColumnStretch(0, 1); g.setColumnStretch(1, 1)
        _gp.addTab(w, "行业概况")

        # ★ v2.1.0：母模块「资金排行」在这里**先收口**（顶层 addTab）。
        #   它的第 4 个子页「实时榜单」的构建代码物理上排在后面（原 8.5 节），
        #   届时用 group_tabs["资金排行"].addTab(...) 补挂即可 ——
        #   子页顺序由 addTab 调用顺序决定，与"外壳何时挂上顶层"无关。
        #   先收口的好处：顶层顺序严格等于 TAB_GROUPS 的声明顺序，
        #   不会因为某个子页的代码位置而在顶层跳序。
        self.tabs.addTab(_shell, "资金排行")

        # ============================================ 母模块「板块题材」
        # ★ v2.1.0：「题材簇」独立成一个母模块。
        #
        #   ★ 为什么它单独一组、不与「资金排行」合并 —— 这是本次划分里
        #     唯一"一子一母"的模块，值得说明：
        #     「题材簇」的主体是**题材之间的归属关系**（哪几个细分板块属于
        #     同一条主线、资金在主线内如何集中），它回答的是"资金在买什么故事"；
        #     而「资金排行」回答的是"哪个板块排第几"。前者是**结构**、
        #     后者是**排序**——硬塞进排行组会让母模块标签无法自洽
        #     （"资金排行"下设一个讲归属关系的页，标签就名不副实了）。
        #     用户要求"母模块标签须贴合其下子模块的共同主题"，
        #     所以宁可设一个单子页的母模块，也不做语义上不成立的合并。
        #   ★ 同时它也不能并入「盘中复盘」：那组的共同主题是"时间轴上的演进"，
        #     而题材簇是一张**截面**（此刻的归属关系），不含时间维度。
        _shell, _gp = self._make_group_page("板块题材", TAB_TREE["板块题材"])

        # ---------------------------------------------------------- 5 题材簇
        # ★ 2×2 象限布局（v1.1.5 用户需求）：
        #     左上 = 流入主线榜    右上 = 流出主线榜
        #     左下 = 题材簇预览（**只画前八 / 后八**，限制在本象限内）
        #     右下 = 龙头领先反应（新增区块）
        #   旧版把簇图横跨下两格铺满整行 —— 图被拉得又扁又空，
        #   而这页的空白正是用户点名要消掉的。
        w = QWidget(); g = QGridLayout(w)
        g.setContentsMargins(*PAD_PAGE); g.setSpacing(GAP_PAGE)

        c_in, self.t_th_in = make_labeled_table(
            "流入主线", ["主线", "成员", "总净额(亿)", "集中", "代表板块"],
            hint="按合计净额排序",
            stretch_col=4, align={0: "l", 1: "c", 3: "c"},
            panel_id="theme.cluster_in")
        c_out, self.t_th_out = make_labeled_table(
            "流出主线", ["主线", "成员", "总净额(亿)", "集中", "代表板块"],
            hint="净额为负 = 被资金抛弃",
            stretch_col=4, align={0: "l", 1: "c", 3: "c"},
            panel_id="theme.cluster_out")
        g.addWidget(c_in, 0, 0)
        g.addWidget(c_out, 0, 1)

        # 第三象限：题材簇预览（红八 / 绿八 对称，两栏柱条均向右）
        self.ch_theme = None           # v2.0.3：延迟创建（见 _materialize_charts）
        card_th, body_th = make_card(
            "题材簇预览",
            "左 = 流出 8 条（绿）· 右 = 流入 8 条（红）· 两栏柱条**均向右**伸 · "
            "柱长按各栏最大值归一 → 两侧同一条数、视觉重量相当；"
            "量级差异见各栏标题「最长」，真实金额见柱尾标签",
            panel_id="theme.board")
        self._chart_slots.append(("ch_theme", body_th, None, lambda: ThemeBoard(3.2)))
        g.addWidget(card_th, 1, 0)

        # 第四象限：龙头领先反应（新）
        card_ld, body_ld = make_card(
            "龙头领先反应",
            "龙头 = 在板块拐点上先动手的那只（不是净额最大）· 领先′ = 板块拐点 − 个股反应",
            panel_id="theme.leader")
        self.lbl_leader = QLabel("等待分析…（首次约需 10~30 秒，后台进行）")
        self.lbl_leader.setObjectName("cardHint")
        body_ld.addWidget(self.lbl_leader)
        self.t_leader = make_table(
            ["板块", "净额(亿)", "领涨龙头", "领先′", "先手", "领跌龙头", "领先′", "先手"],
            stretch_col=2, align={0: "l", 1: "r", 2: "l", 3: "r", 4: "c",
                                  5: "l", 6: "r", 7: "c"})
        body_ld.addWidget(self.t_leader, 1)
        g.addWidget(card_ld, 1, 1)
        g.setColumnStretch(0, 1); g.setColumnStretch(1, 1)
        g.setRowStretch(0, 3); g.setRowStretch(1, 5)
        _gp.addTab(w, "题材聚簇")

        # ★ v2.1.0：母模块「板块题材」装配完毕。
        self.tabs.addTab(_shell, "板块题材")

        # ============================================ 母模块「盘中复盘」
        # ★ v2.1.0：4 个子模块同主题「时间轴上的演进与回看」——
        #   事件流（什么时刻发生了什么）/ 主角更替（辨识度前三随时间怎么换）/
        #   阶段对比（6 个时点的横截面对照）/ 关键节点（全天叙事线）。
        #   共同点：**横轴都是时间**，都在回答"这一天是怎么一步步走过来的"，
        #   区别只是粒度（离散事件 / 色块序列 / 阶段切片 / 节点摘要）。
        _shell, _gp = self._make_group_page("盘中复盘", TAB_TREE["盘中复盘"])

        # ---------------------------------------------------------- 4 实时事件
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(*PAD_PAGE); v.setSpacing(GAP_PAGE)
        card, self.t_ev = make_labeled_table(
            "盘中异动事件", ["时刻", "事件", "题材", "说明", "强度"],
            hint="全天事件流 · 按时间倒序（晚→早）· 同刻按强度 · "
                 "加速流入 / 新晋 / 资金回流 / 失血 / 跳水 / 排名跃迁",
            stretch_col=3, align={0: "c", 1: "c", 2: "l", 3: "l"},
            panel_id="replay.events")
        v.addWidget(card, 1)   # ★ v2.1.0：整页只有一张卡 → 拉伸填满，消除页底空白
        _gp.addTab(w, "实时事件")

        # ---------------------------------------------------------- 6 主角更替
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(*PAD_PAGE); v.setSpacing(GAP_PAGE)
        self.ch_lead = None            # v2.0.3：延迟创建（见 _materialize_charts）
        card, body = make_card(
            "主角更替", "每行三个色块 = 该时刻辨识度前三 · 右侧为三强合计与环比",
            panel_id="replay.leaders")
        self._chart_slots.append(("ch_lead", body, None, lambda: LeaderGrid(6.0)))
        v.addWidget(card, 1)   # ★ v2.1.0：整页只有一张卡 → 拉伸填满，消除页底空白
        _gp.addTab(w, "主角更替")

        # ---------------------------------------------------------- 7 阶段对比
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(*PAD_PAGE); v.setSpacing(GAP_PAGE)
        self.ch_stage = None           # v2.0.3：延迟创建（见 _materialize_charts）
        card, body = make_card(
            "阶段对比", "一天 6 个时点 · 每格上半段红=流入 Top6 / 下半段绿=流出 Top6，柱长正比金额",
            panel_id="replay.stages")
        self._chart_slots.append(("ch_stage", body, None, lambda: StageGrid(6.0)))
        v.addWidget(card, 1)   # ★ v2.1.0：整页只有一张卡 → 拉伸填满，消除页底空白
        _gp.addTab(w, "阶段对比")

        # ---------------------------------------------------------- 8 关键节点
        # ★★ v1.2.5：排序口径由「按强度」改为「按时间先后（早→晚）」。
        #   本页标题是「全天关键节点」——「全天」二字决定了它是一条**时间轴**，
        #   首要信息是"几点发生了什么、事情怎么一步步演变"。
        #   原先按强度倒序排，时间完全跳跃：
        #       09:42 → 09:45 → 10:15 → 09:51 → 09:48 → 11:00 → 11:03 → 10:57 …
        #   同一时刻看不出先后，也读不出叙事线，用户直接反馈"排列无序、杂乱"。
        #   强度信息本来就独立成列（末列），需要找"最劲爆的"扫那一列即可，
        #   不必用行序来表达 —— 行序让给时间，信息量更大且不丢任何东西。
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(*PAD_PAGE); v.setSpacing(GAP_PAGE)
        card, self.t_kn = make_labeled_table(
            "全天关键节点", ["时刻", "事件", "题材", "说明", "强度"],
            hint="按时间倒序排列（晚→早）· 强度见末列",
            stretch_col=3,
            align={0: "c", 1: "c", 2: "l", 3: "l"},
            panel_id="replay.key_nodes")
        v.addWidget(card, 1)   # ★ v2.1.0：整页只有一张卡 → 拉伸填满，消除页底空白
        _gp.addTab(w, "关键节点")

        # ★ v2.1.0：母模块「盘中复盘」装配完毕。
        self.tabs.addTab(_shell, "盘中复盘")

        # ---------------------------------------------------------- 8.5 实时榜单
        # ★ v2.1.0：归入母模块「资金排行」—— 它的主体是"全天累积池排行榜"，
        #   与辨识度榜/雷达/行业同属"排行与扫描"族。
        #   ⚠ 这里必须**重新取回**「资金排行」的子页签控件：上面的代码已经在
        #     装配别的母模块时把 _gp 覆盖掉了（_gp 是复用的局部变量）。
        #     直接沿用此刻的 _gp 会把实时榜单错误地挂到「盘中复盘」下 ——
        #     而且不会报错，只是标签下多出一个不属于它的子页。
        #     用 group_tabs 按名取回，避免依赖"局部变量还指对地方"这种脆性前提。
        _gp = self.group_tabs["资金排行"]
        # ★ v1.1.7 新增页（用户需求）：「板块概念实时榜单」——
        #   开盘按辨识度选约 15 个入榜，盘中检测到盘口异动立即追加，
        #   已入选的全天保留（只增不减）。
        #
        # ★★ v2.0.1 版面重构（用户需求）：
        #   上半 = 累积池（只增不减，主角），新增「龙头选手」列、序号列改称「序号」；
        #   下半 = **四个**当下榜（原为两个）：
        #           当下辨识度榜 · 流入      | 当下辨识度榜 · 流出
        #           当下辨识度榜 · 五分钟净流入 | 当下辨识度榜 · 五分钟净流出
        #
        #   为什么要把"5 分钟"两榜补上：
        #     「累积净额榜」回答"今天谁最强"，而盘中真正想看的是"**此刻**钱在往哪去"。
        #     收盘前 5 分钟榜上常是一批全天没露过面的新面孔 —— 那才是异动发生的地方；
        #     只看累计榜会看到一堆"上午进的、现在正在被卖"的老面孔挂在高位。
        #     两套并列，才分得清"正在发生"与"已经发生"。
        #
        #   布局用 2×2 网格而不是一串并排 —— 四张表并排会把每张压到只剩一列内容，
        #   双行双列则每张都有足够宽度显示完整题材名。
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(*PAD_PAGE); v.setSpacing(GAP_PAGE)

        card_w, body_w = make_card(
            "板块概念实时榜单（累积池 · 只增不减）",
            "开盘按辨识度双向各取 "
            f"{WATCH_TOP_N} 个入榜 → 盘中检测到盘口异动立即追加 → "
            "已入选全天保留，任何情况不移除、不替换　·　"
            "龙头选手 = 该板块拐点上先动手的那只（来自龙头领先反应，未分析到显示 —）",
            panel_id="watch.pool")
        self.lbl_watch = QLabel("等待数据…")
        self.lbl_watch.setObjectName("cardHint")
        self.lbl_watch.setWordWrap(True)
        body_w.addWidget(self.lbl_watch)
        self.lbl_watch_leg = QLabel(
            "▲新进 = 本轮刚追加　·　原因 = 入选依据（首个原因＋后续追加）"
            "　·　首次/最近 = 入选时刻与最近一次触发时刻")
        self.lbl_watch_leg.setObjectName("cardHint")
        body_w.addWidget(self.lbl_watch_leg)
        # ★ v2.0.1：新增「龙头选手」列（插在名称之后），序号列由 # 改称「序号」
        self.t_watch = make_table(
            ["序号", "板块 / 概念", "龙头选手", "净额(亿)", "辨识度",
             "首次", "最近", "入选原因"],
            stretch_col=7, align={0: "c", 1: "l", 2: "l", 3: "r", 4: "r",
                                  5: "c", 6: "c", 7: "l"})
        body_w.addWidget(self.t_watch, 3)
        v.addWidget(card_w, 3)

        # ---- 下半部分：四个「当下榜」
        #   ★★ v2.0.2（需求三，二次更正）：用户明确要**四列**横向并排 ——
        #   四个模块一行排开、各占 1/4 宽，同一行就能横向对比
        #   「流入 vs 流出」「累计 vs 五分钟」。
        #   实现：QGridLayout 单行四列，列 stretch 均分。
        grid_w = QGridLayout(); grid_w.setSpacing(GAP_CARD)
        # 序号列宽度统一走 SEQ_COL_W（见布局常量区说明）
        SEQ_W = {0: SEQ_COL_W}
        SEQ_A = {0: "c", 1: "l"}
        c_b1, self.t_wb_in = make_labeled_table(
            "当下辨识度榜 · 流入",
            ["序号", "题材", "净额(亿)", "5分钟(亿)", "辨识度"],
            hint="按累计净额排序 · 全天体量口径",
            align=SEQ_A, widths=SEQ_W, panel_id="watch.now_in")
        c_b2, self.t_wb_out = make_labeled_table(
            "当下辨识度榜 · 流出",
            ["序号", "题材", "净额(亿)", "5分钟(亿)", "辨识度"],
            hint="按累计净额排序 · 全天体量口径",
            align=SEQ_A, widths=SEQ_W, panel_id="watch.now_out")
        c_b3, self.t_wb_d5in = make_labeled_table(
            "当下辨识度榜 · 五分钟净流入",
            ["序号", "题材", "净额(亿)", "5分钟(亿)", "辨识度"],
            hint="按近 5 分钟增量排序 · 此刻资金正在涌入",
            align=SEQ_A, widths=SEQ_W, panel_id="watch.now_d5in")
        c_b4, self.t_wb_d5out = make_labeled_table(
            "当下辨识度榜 · 五分钟净流出",
            ["序号", "题材", "净额(亿)", "5分钟(亿)", "辨识度"],
            hint="按近 5 分钟增量排序 · 此刻资金正在撤离",
            align=SEQ_A, widths=SEQ_W, panel_id="watch.now_d5out")
        for _i, _c in enumerate((c_b1, c_b2, c_b3, c_b4)):
            grid_w.addWidget(_c, 0, _i)
            grid_w.setColumnStretch(_i, 1)
        grid_w.setRowStretch(0, 1)
        v.addLayout(grid_w, 3)
        # ★ v2.1.0：挂回「资金排行」。
        #   ⚠ 必须按名取回子页签控件，**不能**用此刻的 _gp：
        #     _gp 是 _build_ui 里复用的局部变量，到这里它指向的是「盘中复盘」
        #     （本块的构建代码物理上排在盘中复盘装配之后）。
        #     直接沿用 _gp 会把「实时榜单」错挂到「盘中复盘」下 ——
        #     而且不报错，只是标签下多出一个不属于它的子页。
        #     这是本次重构里最容易静默出错的一处，故显式按名取。
        self.group_tabs["资金排行"].addTab(w, "实时榜单")

        # ★★ v2.0.1：给关键表格起名，供 `--shot` 自检把**屏幕上的真实字符串**
        #   导出成 *.table.txt，用来做「屏幕层」断言。
        #   为什么需要：数据层排好序 ≠ 屏幕有序，中间还隔着
        #   on_data → fill_table → QTableWidget 三层。只断言数据层会漏掉整条
        #   显示链路（历史上就漏过一次：某页表格行数不变、内容却悄悄变了）。
        #   命名而不靠 findChildren 的顺序，是因为控件遍历顺序不保证等于视觉顺序。
        for _t, _on in ((self.t_watch, "watch_pool"),
                        (self.t_wb_in, "watch_base_in"),
                        (self.t_wb_out, "watch_base_out"),
                        (self.t_wb_d5in, "watch_base_d5in"),
                        (self.t_wb_d5out, "watch_base_d5out")):
            try:
                _t.setObjectName(_on)
            except Exception:                            # noqa: BLE001
                pass

        # ★ v1.2.1：「数据状态」页已删除（用户要求精简界面）。
        #   原先它承担两件事：① 展示运行状态/说明长文；② 加载失败时容纳
        #   人话版的失败原因。删除后两件事各有归处，功能不丢：
        #     ① → 帮助菜单 →「运行状态」，弹一个只读对话框（内容与原来一致，
        #          文本缓存在 self.status_text，由 show_status() 生成）；
        #     ② → 状态栏文字 + 弹窗 + 日志文件（见 on_fail）。
        #   注意：这里刻意**不再** addTab，因此实时榜单成为最后一个页签 ——
        #   任何"跳到最后一页"的代码都必须改成按**页名/索引常量**定位，
        #   不能再用 tabs.count()-1（脆性写法，见 on_fail 的注释）。

        # ★★ v2.0.1：其余主要表格也统一命名（供 --shot 屏幕层导出/断言）。
        #   集中在一处声明，避免以后新增表格时漏命名 ——
        #   漏了不会报错，只是那个表永远无法被屏幕层验证到。
        for _t, _on in ((getattr(self, "t_ev", None), "event_stream"),
                        (getattr(self, "t_kn", None), "key_nodes"),
                        (getattr(self, "t_in", None), "board_in"),
                        (getattr(self, "t_out", None), "board_out"),
                        (getattr(self, "t_ind_in", None), "industry_in"),
                        (getattr(self, "t_ind_out", None), "industry_out"),
                        (getattr(self, "t_th_in", None), "theme_in"),
                        (getattr(self, "t_th_out", None), "theme_out"),
                        (getattr(self, "t_radar_in", None), "radar_in"),
                        (getattr(self, "t_radar_out", None), "radar_out"),
                        (getattr(self, "t_leader", None), "leader_grid")):
            if _t is not None:
                try:
                    _t.setObjectName(_on)
                except Exception:                        # noqa: BLE001
                    pass

        # ★★ v2.0.1：切页时补画该页的图表（配合 render_charts 的按页延迟绘制）。
        # ★★ v2.1.0：顶层改挂**子页签变化**信号 —— 因为母模块内部也可以切页，
        #   而"切到子页 → 该子页的图需要补画"同样要触发 lazy render。
        #   顶层信号保留（切母模块也要触发），两个信号都连到同一个处理函数，
        #   它内部按"当前可见的顶层 + 其子页"判定 —— 见 on_tab_changed。
        try:
            self.tabs.currentChanged.connect(self.on_tab_changed)
            for _g, _sub in self.group_tabs.items():
                _sub.currentChanged.connect(self.on_tab_changed)
            log_line("lazy-render hook connected "
                     f"(tabs.currentChanged + {len(self.group_tabs)} sub-tabs)")
        except Exception as _e:                          # noqa: BLE001
            log_line(f"lazy-render hook connect FAILED: {_e}")

        self.setCentralWidget(self.tabs)

        # ★★ v2.1.0 两级页签一致性自检（原 v1.2.1 单级自检的扩展）。
        #
        #   为什么必须自检：这套结构里"名字"承担了太多职责 ——
        #   Ctrl+N、PAGE_INDEX 跳页、CHART_PAGE 图表归属、--shot 断言，
        #   全部按名字查表。任何一处写错**都不会抛异常**，只会静默错位
        #   （跳错页 / 图不画 / 截错了页），是最难发现的一类缺陷。
        #
        #   现在校验三件事：
        #     ① 顶层页签序列 == TAB_GROUPS 的母模块名序列；
        #     ② 每个母模块内部的子页签序列 == TAB_TREE[母模块]；
        #     ③ 实际挂上的子模块**集合** == PAGE_NAMES 的集合，且无重复。
        #   ③ 是关键的**守恒断言**：它等价于"14 个子模块一个不少、一个不多、
        #   没有重复挂载"，正是用户"禁止删除任一子模块"这条要求的机器可验证形式。
        #
        #   ⚠ ③ 只校验集合与计数、**不校验顺序**：重构后同一母模块内子页可以
        #     重排（这正是本次任务要做的），顺序不再是守恒量；
        #     顺序的正确性由 ①② 分别校验（母模块序 + 每个母模块内的子页序）。
        #
        #   仍然只记日志不抛异常（生产环境宁可界面能开、留痕排查）。
        _top_now = [self.tabs.tabText(i) for i in range(self.tabs.count())]
        self._tab_names_actual = _top_now          # 兼容旧名（= 现在指顶层）
        self._group_names_actual = list(_top_now)
        _expect_top = [g for g, _s in TAB_GROUPS]
        if _top_now != _expect_top:
            log_line(f"TAB MISMATCH: top={_top_now} vs TAB_GROUPS={_expect_top}")
        _mounted = []
        self._sub_names_actual = {}
        for _g, _subs in TAB_GROUPS:
            if _g in self.group_tabs:
                _sn = [self.group_tabs[_g].tabText(i)
                       for i in range(self.group_tabs[_g].count())]
                self._sub_names_actual[_g] = _sn
                if _sn != list(_subs):
                    log_line(f"SUB TAB MISMATCH: {_g}: {_sn} vs {list(_subs)}")
                _mounted.extend(_sn)
            else:
                # 无子页的母模块（只有「总览」）—— 它自己就是一个顶层页，
                # 守恒校验时必须把它**本身的名字**计入，否则会误报"少了总览"。
                # ★ 这正是首次试运行真踩到的：初版只 extend(_subs)（空列表），
                #   日志立刻报 TAB TREE MISMATCH missing=['总览']。
                #   校验逻辑自己漏算 vs 真有模块丢失，必须能区分开 ——
                #   所以这里补上，让"通过"能被信赖（狼来了的校验等于没有校验）。
                _mounted.append(_g)
        if sorted(_mounted) != sorted(PAGE_NAMES):
            _miss = [x for x in PAGE_NAMES if x not in _mounted]
            _extra = [x for x in _mounted if x not in PAGE_NAMES]
            log_line(f"TAB TREE MISMATCH: mounted={_mounted} vs PAGE_NAMES={PAGE_NAMES} "
                     f"missing={_miss} extra={_extra}")
        else:
            log_line(f"tab tree OK: {len(_expect_top)} groups · "
                     f"{len(_mounted)} modules all mounted (sets equal)")

        # ★★ v2.2.0 面板（孙模块）守恒自检 —— 第三层级的"一个不少"。
        #
        #   需求原话：「母模块下面有大概 30 个孙模块，子模块下面有 30 个孙模块，
        #   要求这一个不少。」
        #
        #   上面那段自检只覆盖到**子模块**层（14 个）。面板层此前完全没有校验：
        #   少挂一张卡片不会抛异常、日志无痕，只有用户盯屏幕才发现"这页少了一块"。
        #   本段补上 —— 它等价于"PANELS 表里登记的每一张卡片都真的挂上了，
        #   且屏上不存在表里没登记的多余卡片"。
        #
        #   判据：遍历顶层 + 每个母模块的子页 + 总览页，收集所有
        #   objectName=="card" 且带 panelId 动态属性的卡片的 panelId 集合，
        #   与 PANELS 的 id 集合做 sorted 相等比较。
        #
        #   ⚠ 只比对**集合**（不看顺序、不看位置）：本次任务允许重排面板的
        #     位置与尺寸，顺序不是守恒量；集合才是"一个不少"的准确形式。
        #   ⚠ 与上面同样只记日志不抛异常 —— 生产环境宁可界面能开、留痕排查。
        try:
            from PySide6.QtWidgets import QFrame as _QF, QTabWidget as _QTW
            _found = []
            # ⚠ 控件树实测结构（必须按这个数才准）：
            #   · 「总览」= 顶层 QWidget，其下直接是卡片 → 数它；
            #   · 其余 5 个 = **母模块外壳 QWidget**，内部嵌着一个 QTabWidget
            #     （group_tabs[母模块]），真正的子页在那个 QTabWidget 里。
            #     若对外壳页调 findChildren，会把它**内部全部子页**的卡片
            #     一起数进来（大盘 4+6 会被数成 10）→ 总数 40 变 79。
            #   判据：外壳页 = 内部含有 group_tabs 中某个 QTabWidget 的顶层页。
            #   这属"校验逻辑自己数重了"，与"真有面板重复挂载"必须能区分。
            _shells = {id(_s) for _s in self.group_tabs.values()}
            for _i in range(self.tabs.count()):
                _pg = self.tabs.widget(_i)
                if _pg is None:
                    continue
                if any(id(_s) in _shells for _s in _pg.findChildren(_QTW)):
                    continue                      # 母模块外壳页 → 交给子页数
                for _c in _pg.findChildren(_QF):
                    _pid = _c.property("panelId")
                    if _pid:
                        _found.append(str(_pid))
            for _g, _sub in self.group_tabs.items():
                for _i in range(_sub.count()):
                    _pg = _sub.widget(_i)
                    if _pg is None:
                        continue
                    for _c in _pg.findChildren(_QF):
                        _pid = _c.property("panelId")
                        if _pid:
                            _found.append(str(_pid))
            self._panel_ids_actual = list(_found)
            _dup = sorted({x for x in _found if _found.count(x) > 1})
            self._panel_ids_expect = list(PANEL_IDS)
            if sorted(_found) != sorted(PANEL_IDS) or _dup:
                _pmiss = [x for x in PANEL_IDS if x not in _found]
                _pextra = [x for x in _found if x not in PANEL_IDS]
                log_line(f"PANEL MISMATCH: found={len(_found)} vs PANELS={len(PANEL_IDS)} "
                         f"missing={_pmiss} extra={_pextra} dup={_dup}")
            else:
                log_line(f"panel registry OK: {len(PANEL_IDS)} panels all mounted "
                         f"(sets equal)")
        except Exception as _pe:                          # noqa: BLE001
            log_line(f"panel registry check FAILED: {_pe}")

        sb = QStatusBar()
        self.setStatusBar(sb)
        self.lbl = QLabel("就绪")
        sb.addWidget(self.lbl)
        self.bar = QProgressBar(); self.bar.setMaximumWidth(220)
        self.bar.setRange(0, 0); self.bar.hide()
        sb.addPermanentWidget(self.bar)

        # ★ 首屏加载占位（v1.0.5）：数据到位前图表区先给出明确提示。
        #   ★★ v2.0.3：画布本身也已延迟到窗口显示后才创建（见
        #     _materialize_charts），"数据加载中…"的占位绘制挪到了那里 ——
        #     此处不再有 ch_* 可画，防止 AttributeError。

        # ★ v1.1.7：`--date` 启动时**跳过实盘首屏加载，直接进历史回看**。
        #   踩过的坑（真实缺陷，非仅影响截图比对）：
        #     旧逻辑是「先 reload 实盘 → --shot/--date 的 tick 再切历史日」。
        #     那次实盘 reload 走的是 local_only=False 路径，会执行
        #     `pool.update()` 并 `flush()` ——也就是**在用户还没看到界面时，
        #     就已经把"今天"的榜单池写到了磁盘上**。后果：
        #       ① 当天真实的池子快照被污染（多出一轮，30→34）；
        #       ② 行为依赖启动速度 —— exe（onefile 解压慢）能把首屏 reload
        #          完整跑完，源码版启动快则常被历史回看抢在前面。于是
        #          "同一份代码、同一份数据"跑出两种结果，排查时会误判成
        #          "打包导致渲染不一致"，实际是**输入不同**。
        #   直接跳过去，语义也更干净：--date 的意图就是"只看那天"，没有任何
        #   理由先去抓一次实盘。
        # ★★ v2.0.1：启动即取最新（incremental=True）**并且**尽快开始（80ms）。
        #
        #   传 incremental=True：Worker 会自己判断 ——
        #     · 交易时段 → 先就地出图（本地面板），随即增量补上当前这一分钟；
        #     · 非交易时段 → 直接读缓存（盘中无新数据，不必空跑一次网络）。
        #   旧版这里传 force=False（= 只读缓存），于是"刚启动看到的是
        #   十几分钟前的数据"，用户必须手动点一次「重新抓取」——
        #   那正是本次要修掉的体验。
        #
        #   200ms → 80ms：首屏链路现在只画一张图（见 CHART_PAGE），
        #   200ms 的"等窗口画完再加载"已经不再必要；80ms 足够躲开
        #   窗口尚未映射的那一帧，又不会让人感觉到"程序启动后卡了一下"。
        # ★★ v2.0.3：窗口显示后 120ms 补建图表画布 —— matplotlib 的
        #   import（~1.0s）就在这里发生，Worker 线程已在并行预热，
        #   通常只需等锁几十毫秒（见 _materialize_charts 的说明）。
        QTimer.singleShot(120, self._materialize_charts)

        # ★★ v2.0.4：开盘检测定点快照调度器（09:36 / 09:41 / 09:46 触发）。
        #   --shot 自检模式不启动（截图可复现 + 不在自检里打全市场请求）。
        # ★★ v3.0.2：新增 `_catchup_done`（B 兜底）。启动后若当天仍有缺失节点
        #   且已过其准点窗口，补采一轮并如实标注 —— 详见 _snap_tick。
        self.snap_worker = None
        self._catchup_done = False
        self.snap_timer = QTimer(self)
        self.snap_timer.timeout.connect(self._snap_tick)
        if "--shot" not in sys.argv:
            self.snap_timer.start(15000)
            # ★ 启动后 3 秒就检查一次补采（不等第一个 15s tick）——
            #   用户"早上打开程序"的场景下，这一下就是当天唯一一次补采机会，
            #   早 12 秒开抓，累计值就更接近节点（虽然仍标为补采）。
            QTimer.singleShot(3000, self._snap_tick)
        if "--date" in sys.argv:
            _i = sys.argv.index("--date")
            _d = sys.argv[_i + 1] if _i + 1 < len(sys.argv) else None
            if _d:
                QTimer.singleShot(80, lambda: self.load_history(_d))
                return
        QTimer.singleShot(80, lambda: self.reload(force=False, incremental=True))

    # ------------------------------------------------------ 图表延迟创建
    def _materialize_charts(self):
        """创建全部图表画布（v2.0.3 启动延迟优化第二段）。

        ★ 为什么：matplotlib 的 import（~1.0s）+ 画布创建原先在
          MainWindow.__init__ 里同步完成，逼得「双击 → 窗口出现」要 ~1.9s。
          现在 __init__ 只摆占位（ch_* = None），窗口先显示；show() 后
          120ms 由这里补建画布 —— 期间图表卡片短暂空白，而首屏数据本来
          就要 1~5s 才到，用户无感。
          Worker 线程已并行做 matplotlib import（run() 开头的 ensure_mpl），
          这里通常只需等锁几十毫秒。

        ★ 幂等：可安全多次调用（render_charts / on_data 里也会兜底调用）。
        """
        if getattr(self, "_charts_ready", False):
            return
        created = []
        try:
            ensure_mpl()
            for name, lay, stretch, factory in self._chart_slots:
                wdg = factory()
                setattr(self, name, wdg)
                created.append((name, lay, wdg))
                if stretch is None:
                    lay.addWidget(wdg)
                else:
                    lay.addWidget(wdg, stretch)
            self._chart_slots = []
            self._charts_ready = True
            for c in self._chart_widgets():
                c.blank("数据加载中…")
            # 数据若已先到（极快缓存/自检等待期），立即按已到数据渲染
            if (self.data or {}).get("F"):
                self.render_charts(only_visible=not _env_render_all())
        except Exception as e:                           # noqa: BLE001
            # 仅在全部实例化成功后立完成标志；清理已半建的画布，避免重试时重复插入。
            for name, lay, wdg in created:
                try:
                    lay.removeWidget(wdg)
                    wdg.deleteLater()
                    setattr(self, name, None)
                except Exception:                        # noqa: BLE001
                    pass
            self._charts_ready = False
            log_line(f"materialize charts FAILED: {type(e).__name__}: {e}")

    def _chart_widgets(self):
        """当前已创建的图表画布列表（未创建的自动跳过，v2.0.3）。"""
        return [c for c in (self.ch_index, self.ch_flow, self.ch_fan,
                            self.ch_lead, self.ch_stage, self.ch_theme)
                if c is not None]

    # ------------------------------------------------------ 开盘检测/个股异动
    @staticmethod
    def _snap_nodes():
        """开盘检测节点定义（惰性 import，打包后 core/ 在 _MEIPASS）。"""
        import opensnap
        return opensnap.SNAP_NODES

    def fill_open_pages(self):
        """用当天快照填「开盘检测」「个股异动」两页（v2.0.4）。

        数据源 core/opensnap.py（节点定点全市场快照）。
        触发点：on_data（每次数据刷新后）与节点采集完成（_on_snap_done）。
        tag 取当前数据日 —— 回看历史日时该日没有快照，页面诚实显示未采集。
        """
        import opensnap
        tag = ((self.data or {}).get("date_tag")
               or datetime.now().strftime("%Y%m%d"))
        p = opensnap.build_payload(tag)

        def _miss_row(ncol, hint):
            # ★★ v3.0.1：原来是 `[["—", hint] + ["—"] * (ncol - 2)]` —— 把提示
            #   塞进第 1 列。该列是 ResizeToContents，会被同表其它列挤窄，
            #   真机截图里整句被截成「该…」（见 v301_2_个股.png）。
            #   现在直接返回 _Hint —— fill_table 会整行合并居中显示完整文案。
            #   ncol 保留在签名里（调用点仍传），但已不需要 —— 合并列数由
            #   fill_table 按表格实际列数决定，比手传更可靠。
            return _Hint(hint)

        # ★★ v3.0.2：把「补采」如实标注到卡片标题上。
        #   规则：只有**补采**的节点才加后缀，准点采集的标题保持原样 ——
        #   否则正常情况下每个标题都挂着标注，反而看不清哪一个是补的。
        #   定位方式：按 panelId 找到卡片 QFrame，再找其中的 cardTitle QLabel。
        #   （这是 v3.0.1 建立的注册表带来的红利：有稳定 id 就能精确改到那一张卡。）
        def _mark_late(panel_id, base_title, node):
            if not node or node.get("missing") or node.get("on_time", True):
                self._set_card_title(panel_id, base_title)
                return
            self._set_card_title(panel_id, "%s · %s补采" % (
                base_title, node.get("cap_hm") or "晚时"))

        # ---- 开盘检测（板块 Top5 × 2 指标 × 3 节点）----
        for (key, kind), t in self.t_open.items():
            node = p["nodes"].get(key) or {}
            _lab = opensnap.NODE_LABEL.get(key, key)
            _mtitle = "成交量 Top5" if kind == "vol" else "主力净流入 Top5"
            _mark_late("open_check.%s_%s" % (key, kind),
                       "%s · %s" % (_lab, _mtitle), node)
            if node.get("missing"):
                ncol = 4 if kind == "vol" else 3
                fill_table(t, _miss_row(
                    ncol, "该节点未采集（程序未在节点时刻运行）"))
                continue
            if kind == "vol":
                rows = [[r["rank"], r["name"],
                         f"{r['vol_wan']:,.0f}", f"{r['amt_yi']:,.1f}"]
                        for r in node["sector_vol_top5"]]
            else:
                rows = [[r["rank"], r["name"], f"{r['net_yi']:+.2f}"]
                        for r in node["sector_net_top5"]]
            fill_table(t, rows)

        # ---- 个股异动（Top10 × 3 指标 × 3 节点）----
        _mkey = {"lb": "stock_lb_top10", "vol": "stock_vol_top10",
                 "net": "stock_net_top10"}
        _mtitle_map = {"lb": "量比 Top10", "vol": "成交量 Top10",
                       "net": "主力净流入 Top10"}
        for (key, mk), t in self.t_stock.items():
            node = p["nodes"].get(key) or {}
            _lab = opensnap.NODE_LABEL.get(key, key)
            _mark_late("stock_move.%s_%s" % (key, mk),
                       "%s · %s" % (_lab, _mtitle_map[mk]), node)
            if node.get("missing"):
                fill_table(t, _miss_row(
                    7, "该节点未采集（程序未在节点时刻运行）"))
                continue
            rows = [[r["rank"], r["name"], r["code"], f"{r['lb']:.2f}",
                     f"{r['vol_wan']:,.0f}", f"{r['net_yi']:+.2f}",
                     f"{r['pct']:+.2f}"]
                    for r in node[_mkey[mk]]]
            fill_table(t, rows)

        # ---- 个股精选（九榜出现次数）----
        # ★ v3.0.2：若三节点中有补采的，标题上加一次性说明（榜单依据含补采值）。
        _late_nodes = [k for k, nd in (p.get("nodes") or {}).items()
                       if not nd.get("missing") and not nd.get("on_time", True)]
        _title_sel = "个股精选 · 九榜出现次数排名"
        if _late_nodes:
            _title_sel += "（含补采数据）"
        self._set_card_title("stock_pick.nine_boards", _title_sel)
        sel = (p.get("select") or [])[:50]
        rows = [[("★" if r["all9"] else r["rank"]), r["name"], r["code"],
                 f"{r['count']}/9" + ("（全满贯）" if r["all9"] else ""),
                 "、".join(r["lists"]), f"{r['net_yi']:+.2f}"]
                for r in sel]
        fill_table(self.t_select, rows)

    def _set_card_title(self, panel_id, text):
        """按 panelId 改该卡片的标题文字（v3.0.2）。

        没有这个 id 的卡片（漏登记/尚未创建）静默跳过 —— 标题是装饰性信息，
        不该因为找不到卡片而抛异常打断整页填充。
        """
        try:
            for c in self.findChildren(QFrame):
                if str(c.property("panelId") or "") != panel_id:
                    continue
                for lb in c.findChildren(QLabel):
                    if lb.objectName() == "cardTitle":
                        if lb.text() != text:
                            lb.setText(text)
                        return
        except Exception:                            # noqa: BLE001
            pass

    def _snap_tick(self):
        """开盘检测节点触发检查（v2.0.4 起，每 15s 一次）。

        ★★ v3.0.2（用户需求 A 打底 + B 兜底 的 B）：新增**盘中/盘后补采**。
        触发条件：交易日（日历可信判定，日历不可用时退回周末排除）
        且当前时刻落在某节点的宽限窗内、该节点当天还没采。

        补采规则（catchup）：
          · 程序启动时若已过某节点的准点窗口、当天又没采到 → 补采一次；
          · 补采**只做一轮**（`_catchup_done` 置位），避免每 15s 反复重试；
          · 补采的数据**如实标注**为补采（node["on_time"]=False），
            标题显示真实采集时刻 —— 绝不冒充节点值。

        ★ 为什么补采只做一轮：补采拿回的是"当下累计值"，早补一刻钟比晚补好。
          反复重试不会让数据更接近节点时刻，只会反复打接口。
          若本轮失败（网络异常），下一轮 tick 会再试（因为 _catchup_done
          只在**成功**后置位）—— 见 _on_snap_done。
        """
        now = datetime.now()
        self._rollover_live_day(now)
        tag = now.strftime("%Y%m%d")
        # ★★ v3.0.4 修复（用户报告：「开盘检测、个股异动等板块仍提示该节点未采集」）
        #
        #   旧代码：  if now.weekday() >= 5: return
        #            days, _ = tradecal.get_trading_days(refresh=False)
        #            if days and tag not in days: return   # ← 就是这一句
        #
        #   为什么它是错的（2026-09-23 盘中实测确证）：
        #     `fflow/daykline` 返回的是**已完成交易日的日线**，今天的日线
        #     要收盘后才生成 —— 因此**盘中取回的日历永远不含今天**。
        #     实测：09-23 盘中联网取回，日历最后一天仍是 09-22。
        #     于是每个交易日盘中都会命中这个守卫，**直接 return**：
        #       · 准点采集（due_nodes）不执行；
        #       · v3.0.2 辛苦写的补采（catchup_nodes）也**一次都跑不到**
        #         —— 因为它在守卫之后，永远够不着。
        #     现象就是节点永远显示"未采集"，且补采形同虚设。
        #
        #   正确判据：用 tradingtime 的四层证据链。它能区分
        #     · "落在日历覆盖区间内却缺失" → 真的节假日（L4）
        #     · "晚于日历最新一天但日历新鲜" → 盘中常态，是交易日（L5）
        #   这样既不再误杀盘中，又真正获得法定节假日过滤。
        try:
            import tradingtime
            _ok, _why = tradingtime.is_trading_day(now)
            if not _ok:
                return
        except Exception as e:                    # noqa: BLE001
            # 兜底：模型不可用时退回最保守的周末判定（绝不能因为
            # 判定失败就放行非交易日，那会采到上一交易日的旧数据）
            log_line(f"tradingtime 不可用，退回周末判定: {type(e).__name__}: {e}")
            if now.weekday() >= 5:
                return
        try:
            import opensnap
        except Exception as e:                    # noqa: BLE001
            log_line(f"opensnap import failed: {e}")
            return
        if self.snap_worker is not None and self.snap_worker.isRunning():
            return                                # 上一节点还没抓完，本轮跳过

        # ① 准点窗口优先：采到的是真节点数据
        due = opensnap.due_nodes(tag)
        if due:
            minutes, key, trigger = due[0]
            self._start_snap(minutes, key, tag, trigger, late=False)
            return

        # ② 补采（B 兜底）：已过准点窗口、当天仍缺 → 补一个，标注为补采。
        #
        # ★★ v3.0.4 修复（用户报告：「开盘检测、个股异动等板块**仍**提示该节点未采集」）
        #
        #   【现场症状】不是"一个都没采到"，而是**部分节点长期停留在未采集**。
        #     日志证据（v3.0.2 部署包）：当天 m5 采到了、m10/m15 永远补不到。
        #
        #   【根因（真·算法缺陷，不是环境问题）】
        #     `catchup_nodes` 会**正确返回全部**待补节点（实测：三节点全缺时返回
        #     [(m5),(m10),(m15)] 三条），但这里只取 `ck[0]` 补**一个**；
        #     同时 `_on_snap_done` 里 `if _late: self._catchup_done = True`
        #     在**第一次补采成功后就置位** ——
        #     于是：第 1 轮 tick 补 m5 → 成功 → _catchup_done=True →
        #     之后所有 tick 都跳过补采分支 → m10 / m15 **永远补不到**。
        #     界面于是长期显示这两块"该节点未采集"。
        #
        #   【为什么"逐个补、但要补到底"而不是"一次全补"】
        #     一次补 3 个节点 = 3×70 个请求瞬时齐发，正是 v3.0.4 刚修掉的
        #     限流成因（峰值并发 18）。所以仍然**一次只补一个**，
        #     但把"补采是否结束"的判据从"曾经成功过一次"改成
        #     "**当天已无待补节点**" —— 每轮 tick 补一个，三节点 45 秒内补完，
        #     且任何时刻都不超过 1 个采集线程。
        if not self._catchup_done:
            ck = opensnap.catchup_nodes(tag, now)
            if ck:
                minutes, key, trigger = ck[0]
                self._start_snap(minutes, key, tag, trigger, late=True)
                return
            # 走到这里 == 当前**没有任何待补节点**（要么全采齐、要么还没到 09:31）。
            # ★ 判据必须落到"数据层面确实齐了"才算完 —— 不能因为
            #   catchup_nodes 返回空就直接置位（09:31 之前它本就返回空，
            #   此时置位会把当天后续三个节点全部挡掉）。
            have = opensnap.load_snap(tag).get("nodes") or {}
            _all = all(k in have for _m, _t, _l, k in opensnap.SNAP_NODES)
            if _all:
                self._catchup_done = True
                log_line("opensnap catchup · 当天节点已全部采齐，停止补采")

    def _start_snap(self, minutes, key, tag, trigger, late):
        """起一个节点快照线程（`late=True` 表示这是补采，不是准点采集）。"""
        self.snap_worker = OpenSnapWorker(minutes, key, tag, trigger)
        self.snap_worker.done.connect(self._on_snap_done)
        self.snap_worker.failed.connect(self._on_snap_fail)
        self.snap_worker.start()
        log_line(f"opensnap {'CATCHUP' if late else 'capture'} started · "
                 f"{key} (trigger {trigger})")

    def _on_snap_done(self, key, tag, node):
        """节点快照完成：落盘 → 刷新两页（v2.0.4；v3.0.2 增加补采日志）。"""
        try:
            import opensnap
            with opensnap._LOCK:
                snap = opensnap.load_snap(tag)
                snap["nodes"][key] = node
                opensnap.save_snap(tag, snap["nodes"])
            _late = not node.get("on_time", True)
            log_line(f"opensnap captured · {key} · sectors={len(node['sectors'])}"
                     f" stocks={len(node['stocks'])}"
                     f" at {node.get('captured_at')}"
                     f" planned={node.get('planned') or '?'}"
                     f" {'[补采]' if _late else '[准点]'}")
            # ★★ v3.0.4 修复（真·算法缺陷）：**不要**在这里置位 _catchup_done。
            #
            #   旧代码：  if _late: self._catchup_done = True
            #   它把"补采成功一次"当成了"补采任务结束"。而当天通常缺 2~3 个
            #   节点（程序晚开、或前两个节点窗口没赶上），于是：
            #     第 1 轮补 m5 成功 → 置位 → m10/m15 永远补不到 →
            #     界面长期残留两块「该节点未采集」。
            #   这正好对应现场症状"**等板块仍**提示未采集"（部分是好的）。
            #
            #   正确判据落在 `_snap_tick`：只有"当天已无待补节点"才停止补采。
            #   这里只负责落盘 + 刷新界面，不参与调度状态机。
            #   （去掉置位后，下一轮 tick 会自然接着补下一个节点。）
            self.fill_open_pages()
        except Exception as e:                    # noqa: BLE001
            log_line(f"opensnap save failed: {type(e).__name__}: {e}")

    def _on_snap_fail(self, key, msg):
        # ★ v3.0.2：失败**不**置位 _catchup_done → 下一轮 tick 会重试补采。
        log_line(f"opensnap capture FAILED · {key}: {msg}")

    def _build_menu(self):
        m = self.menuBar()
        f = m.addMenu("文件(&F)")
        # ★★ v2.0.1：「重新抓取」从界面移除（用户要求「不要让我看见」）。
        #
        #   能力并没有丢，而是**并进了「刷新」**：
        #     · 旧版两个入口语义割裂 ——
        #         「刷新」= 直接读本地缓存（盘中的话就是"几分钟前的旧数据"）；
        #         「重新抓取」= 强制全量重抓（1000 次请求，2~10 秒）。
        #       于是用户点「刷新」看不到最新数据，必须点「重新抓取」——
        #       他明确反馈过这个体验，并希望只有一个按钮、且那一个就是对的。
        #     · 现在「刷新」的语义改成"**立即拿最新数据**"：
        #       交易时段走增量通道（批量快照，约 0.3 秒），
        #       非交易时段直接读缓存（本就没有新数据，不必空跑一次网络）。
        #       整条判定在 Worker 内部完成，界面只有一个按钮、无需用户判断。
        #
        #   对象**照旧创建**但不加入任何菜单/工具栏 —— 因为 _set_busy()
        #   会把加载类入口一起禁用/启用；直接删掉会变成 AttributeError。
        self.act_refetch = QAction("重新抓取全部数据(&N)", self)
        self.act_refetch.setShortcut("Ctrl+R")
        self.act_refetch.triggered.connect(lambda: self.reload(force=True))
        self.act_refetch.setVisible(False)
        self.act_refetch.setEnabled(False)

        self.act_refresh = QAction("刷新(&R)", self)
        self.act_refresh.setShortcut("F5")
        self.act_refresh.setToolTip(
            "立即获取最新盘中数据。\n"
            "· 交易时段：走增量刷新（约 0.3 秒），拿到就是当前这一分钟\n"
            "· 非交易时段：直接读本地已存数据（盘中无新数据）")
        # ★★ v2.0.1：incremental=True —— 「刷新」= 取最新，不再只是读缓存
        self.act_refresh.triggered.connect(
            lambda: self.reload(force=False, incremental=True))
        f.addAction(self.act_refresh)
        f.addSeparator()
        a = QAction("退出(&X)", self)
        a.setShortcut("Ctrl+Q")
        a.triggered.connect(self.close)
        f.addAction(a)

        v = m.addMenu("视图(&V)")
        # ★★ v2.1.0：菜单按**两级结构**生成，与界面层级一一对应。
        #
        #   旧版：把 PAGE_NAMES 平铺成 14 个 Ctrl+1..14。重构后顶层只有 6 个，
        #   若继续平铺 14 项，菜单会与界面结构脱节（用户在菜单里找不到
        #   "资金排行"这个他看得见的标签）。
        #
        #   现在的快捷键分配（编号即"顶层序号"，与用户看到的一致）：
        #     Ctrl+1..6  → 跳顶层 6 个母模块（顺序 = TAB_GROUPS）
        #     Alt+1..N   → 在当前母模块里跳第 N 个子页（N 随母模块变化）
        #   为什么用手动 setShortcut 而不是 QAction 树：
        #     快捷键必须落在**可见的**菜单文本上才生效，而"某母模块的第 N 个子页"
        #     是随选中母模块变化的。用「顶层项 Ctrl+N」+「子页项 Alt+N」两套，
        #     每套都挂在真实存在的项上，不会出现"快捷键指向一个当前不存在的页"。
        #
        #   ⚠ 需要"跳到某页"时仍然用 PAGE_INDEX[名字] / GROUP_POS[名字] 取下标，
        #     不要写字面量数字、更不要用 count()-1（脆性写法，见 on_fail 注释）。
        for gi, (gname, subs) in enumerate(TAB_GROUPS):
            if not subs:
                # 总览：无子页，直接是顶层项
                act = QAction(gname, self)
                act.setShortcut(f"Ctrl+{gi + 1}")
                act.triggered.connect(
                    lambda _=False, k=gi: self.tabs.setCurrentIndex(k))
                v.addAction(act)
                continue
            gm = v.addMenu(f"{gname}(&{gi + 1})")
            _gact = QAction(f"打开「{gname}」", self)
            _gact.setShortcut(f"Ctrl+{gi + 1}")
            _gact.triggered.connect(
                lambda _=False, k=gi: self.tabs.setCurrentIndex(k))
            gm.addAction(_gact)
            gm.addSeparator()
            for si, sub in enumerate(subs):
                sact = QAction(sub, self)
                if si < 9:                              # Alt+1..9 够用（最多 4 个）
                    sact.setShortcut(f"Alt+{si + 1}")
                sact.triggered.connect(
                    lambda _=False, g=gname, s=si: self.goto_page_name(g, s))
                gm.addAction(sact)
        v.addSeparator()
        self.act_dark = QAction("暗色主题", self, checkable=True)
        self.act_dark.triggered.connect(self.toggle_theme)
        v.addAction(self.act_dark)

        v.addSeparator()
        # ★★ v1.2.3：自动刷新改为「开关 + 周期子菜单」。
        #   旧版写死 5 分钟且文案里带周期，改周期就得改代码。
        #   现在 1/2/3/5/10/30/60 秒七档，默认 3 秒（v3.0.4 用户要求）。
        #   用 QActionGroup 保证单选互斥（免得同时勾中两档）。
        self.act_auto = QAction(f"自动刷新（{self._auto_label(AUTO_MS_DEFAULT)}）",
                                self, checkable=True)
        self.act_auto.triggered.connect(self.toggle_auto)
        v.addAction(self.act_auto)

        m_iv = v.addMenu("刷新周期")
        try:
            from PySide6.QtGui import QActionGroup
            grp = QActionGroup(self)
            grp.setExclusive(True)
        except Exception:                                # noqa: BLE001
            grp = None
        self._auto_acts = []
        for lab, ms in AUTO_MS_OPTIONS:
            a = QAction(lab, self, checkable=True)
            a.setChecked(ms == AUTO_MS_DEFAULT)
            if grp is not None:
                grp.addAction(a)
            a.triggered.connect(lambda _=False, m=ms: self._set_auto_interval(m))
            m_iv.addAction(a)
            self._auto_acts.append((ms, a))

        t = m.addMenu("工具(&T)")
        # ★ 历史数据（v1.1.5 方案调整）：默认走 HTML 版那种「一天天攒」的模式 ——
        #   每个交易日实盘加载后自动归档一条，滚动保留最近 30 天。
        #   下面的批量合成是**可选的备选**：它用日级真实总额 + 分钟形态合成，
        #   能快速填满 30 天窗口，但分钟曲线不是真实数据（界面标「合成」）。
        self.act_backfill = QAction("批量合成历史数据（前 30 个交易日）(&B)", self)
        self.act_backfill.setToolTip(
            "可选操作：一次性把前 30 个交易日填满。\n"
            "· 收盘总额 = 东财日级真实数据\n"
            "· 分钟曲线 = 形态合成（接口不提供历史分钟资金流）\n"
            "回看下拉会把这些日期标注为「合成」。\n\n"
            "不点也不影响：每个交易日收盘后会自动归档真实数据，\n"
            "攒满 30 天后新的一天挤掉最早的一天。")
        self.act_backfill.triggered.connect(lambda: self.start_backfill(auto=False))
        t.addAction(self.act_backfill)
        # ★ 龙头领先反应（v1.1.5）：手动重跑入口。
        #   正常情况下实盘加载完会自动跑一轮；被限流或想换数据重算时用这里。
        self.act_leader = QAction("重新分析龙头领先反应(&L)", self)
        self.act_leader.triggered.connect(lambda: self.start_leader(force=True))
        t.addAction(self.act_leader)

        # ★★★ v3.0.6 数据源切换 —— **暗门**
        #
        #   用户要求：「找新的数据源替换，多试几个，并且**保留切换回东方财富
        #   数据的暗门**」。这里就是那扇门，共三条入口（优先级从高到低）：
        #     ① 环境变量 `FLOWLIVE_SOURCE=eastmoney|tencent|auto`
        #        —— 适合脚本/快捷方式里带参数临时强制；
        #     ② 数据目录下的 `source.json`（本菜单项写的就是它）；
        #     ③ 本菜单项 —— 默认**隐藏**，按 Ctrl+Shift+Alt+S 才现身。
        #
        #   ★ 为什么默认隐藏：正常使用不该看到它。数据源是个"设置错了会
        #     静默降级"的开关（例如强指到东财、而东财正被封，程序就什么都
        #     取不到），把它摆在明面上只会增加误操作。
        self.act_source = QAction("数据源…（高级 · 暗门）", self)
        self.act_source.setToolTip(
            "切换数据源。**默认是东财，本项平时不激活**。\n"
            "默认隐藏：按 Ctrl+Shift+Alt+S 显示本项。\n"
            "· eastmoney：东财（默认，能力最全）\n"
            "· tencent：腾讯（替代源，缺分钟级资金流历史）\n"
            "· auto：启动时探测，优先东财、不可用则切腾讯\n"
            "改完需重启程序生效。也可用环境变量 FLOWLIVE_SOURCE 覆盖。")
        self.act_source.triggered.connect(self.choose_source)
        self.act_source.setVisible(False)
        t.addAction(self.act_source)
        _sc = QShortcut(QKeySequence("Ctrl+Shift+Alt+S"), self)
        _sc.activated.connect(self._reveal_source_action)

        h = m.addMenu("帮助(&H)")
        a = QAction("关于", self)
        a.triggered.connect(lambda: QMessageBox.information(
            self, "关于",
            f"资金流向 · 纯原生桌面版 v{VERSION}\n\n"
            "实现方式：Python + PySide6(Qt Widgets) + matplotlib(QtAgg)\n"
            "不含任何浏览器内核、WebView、HTML/CSS/JS。\n\n"
            "数据：东方财富公开接口\n"
            "算法：core/salience.py（辨识度 / 事件 / 聚类 / 时间轴）\n\n"
            "历史回看：最近 30 个交易日（工具栏下拉选择）；\n"
            "历史日收盘资金总额为真实数据，分钟曲线为形态合成，"
            "界面以「合成」标注。\n\n"
            f"缓存：{app_data_dir()}\n"
            f"日志：{app_log_path()}"))
        h.addAction(a)
        # ★ v1.2.1：数据状态**页**已删除，其内容改由这里弹窗查看。
        #   放在「打开日志文件」之前，顺序与重要性一致（先看状态、再看日志）。
        a = QAction("运行状态(&S)…", self)
        a.setToolTip("查看运行状态与说明（原「数据状态」页的内容）")
        a.triggered.connect(self.show_status_dialog)
        h.addAction(a)
        a = QAction("打开数据目录", self)
        a.triggered.connect(self.open_data_dir)
        h.addAction(a)
        a = QAction("打开日志文件", self)
        a.triggered.connect(self.open_log)
        h.addAction(a)

    # ------------------------------------------------------ 数据源暗门（v3.0.6）
    def _reveal_source_action(self):
        """快捷键把隐藏的「数据源…」菜单项显出来（暗门第 ③ 条入口）。"""
        try:
            self.act_source.setVisible(True)
            self.lbl.setText("已显示「工具 → 数据源…（高级）」，改完需重启程序生效")
            log_line("source action revealed by Ctrl+Shift+Alt+S")
        except Exception as e:                           # noqa: BLE001
            log_line(f"reveal source action failed: {e}")

    def choose_source(self):
        """数据源选择对话框：写 `data/source.json`（暗门第 ② 条入口）。"""
        try:
            import source as _src
        except Exception as e:                           # noqa: BLE001
            QMessageBox.warning(self, "数据源", f"数据源层不可用：{e}")
            return
        cur, how = _src.resolve()
        items = [
            "eastmoney —— 东财（**默认**，原始数据源）",
            "tencent —— 腾讯（替代源，无分钟资金流历史）",
            "auto —— 启动时探测：优先东财，不可用则切腾讯",
        ]
        idx = {"eastmoney": 0, "tencent": 1, "auto": 2}.get(cur, 0)
        pick, ok = QInputDialog.getItem(
            self, "数据源（高级 · 暗门）",
            f"当前：{cur}（{how}）\n\n"
            f"默认是东财，本项平时**不激活**；只有在这里选了才会切换。\n\n"
            f"能力差异：\n"
            f"  · 东财：板块清单 / 当前资金流 / **分钟级资金流历史** / 成分股\n"
            f"  · 腾讯：板块清单 / 当前资金流 / 指数分时；"
            f"**无分钟级资金流历史、无成分股**\n\n"
            f"环境变量 FLOWLIVE_SOURCE 优先级高于此处设置。\n"
            f"选择：",
            items, idx, False)
        if not ok:
            return
        newp = ["eastmoney", "tencent", "auto"][items.index(pick)]
        try:
            p = _src.write_config(newp)
            log_line(f"source switched to {newp} (written {p})")
            QMessageBox.information(
                self, "数据源",
                f"已写入 {newp}\n\n{p}\n\n"
                f"（环境变量 FLOWLIVE_SOURCE 若已设置，会覆盖此设置）\n"
                f"重启程序后生效。")
        except Exception as e:                           # noqa: BLE001
            QMessageBox.warning(self, "数据源", f"写入失败：{e}")

    # ---------------------------------------------------------- 自动刷新
    def toggle_auto(self, checked):
        if checked:
            if self.timer_auto is None:
                self.timer_auto = QTimer(self)
                self.timer_auto.timeout.connect(self._auto_tick)
            ms = getattr(self, "_auto_ms", AUTO_MS_DEFAULT)
            self._start_auto(ms)
        else:
            if self.timer_auto is not None:
                self.timer_auto.stop()
            self.lbl.setText("自动刷新已关闭")
            log_line("auto-refresh OFF")

    def _start_auto(self, ms):
        """按指定周期启动自动刷新，并把周期写进状态栏文案。

        ★ v1.2.3：周期从写死的 5 分钟改为可选（10s / 30s / 1min / 5min）。
          真正的提速来自**换通道**（批量快照增量刷新，~350ms），
          而不是"把定时器调快" —— 后者在旧的全量通道下只会把数据源惹毛。
        """
        self._auto_ms = int(ms)
        if self.timer_auto is not None:
            self.timer_auto.start(int(ms))
        txt = self._auto_label(ms)
        self.lbl.setText(f"自动刷新已开启（每 {txt}）")
        log_line(f"auto-refresh ON · {ms} ms ({txt})")

    def _set_auto_interval(self, ms):
        """切换刷新周期（v1.2.3）。若自动刷新正在跑，立即按新周期重启。"""
        self._auto_ms = int(ms)
        # 主菜单项标题里写着周期，不同步会前后矛盾
        try:
            self.act_auto.setText(f"自动刷新（{self._auto_label(ms)}）")
        except Exception:                                # noqa: BLE001
            pass
        if self.timer_auto is not None and self.timer_auto.isActive():
            self._start_auto(ms)
        else:
            self.lbl.setText(f"刷新周期已设为 {self._auto_label(ms)}"
                             f"（勾选「自动刷新」后生效）")
        log_line(f"auto-refresh interval set to {ms} ms")

    @staticmethod
    def _auto_label(ms):
        if ms < 60000:
            return f"{ms // 1000} 秒"
        return f"{ms // 60000} 分钟"

    def _rollover_live_day(self, now=None):
        """进程跨自然日时重置只应作用于当天的运行态。"""
        now = now or datetime.now()
        tag = now.strftime("%Y%m%d")
        if tag == self.live_tag:
            return False
        old = self.live_tag
        self.live_tag = tag
        self._last_fetch_bar = None
        self._catchup_done = False
        self._auto_backfill_done = False
        self.leader_key = None
        self._leader_tag = None
        self.leader_rows = []
        log_line(f"live day rollover: {old} -> {tag}; reset intraday state")
        return True

    def _auto_tick(self):
        """只在交易时段（09:25~15:05）且没有正在跑的抓取时才刷新。

        ★ v1.2.3：刷新走**增量通道**（批量快照，~350ms），
          因此 10 秒周期也来得及 —— 上一轮还没跑完就跳过本轮，
          天然不会堆积（见下方 `worker.isRunning()` 守卫）。

        ★★ v3.0.4：新增**跨分钟守卫** —— 这是支撑 1~10 秒档的关键。
        【问题】数据源（东财 clist / ulist）是**分钟粒度**：
                同一交易分钟内反复请求，拿回的是**完全相同的累计值**。
                若把定时器调到 1 秒而不加判断，一分钟内会发出 60 次
                "注定拿到同一个数"的请求 —— 网络、CPU、渲染全是白烧，
                而且高频请求更容易触发数据源限流。
        【做法】记住上一次**真正抓到数据的交易分钟**，只有当前分钟
                与它不同时才发起新一轮；同分钟内直接 return。
                效果：无论定时器设 1 秒还是 10 秒，**每分钟的真实抓取次数
                恒定为 1 次**（跨分钟那一刻立即抓，不等下一拍）。
        【为什么这样反而是"更快"】：
                · 定时器设 1 秒 → 跨分钟后最多 1 秒内就抓到（比 10 秒档快 9 秒）；
                · 每分钟只花一次抓取的钱，所以可以把档位放心调快。
        【边界】初次启动、跨午休、跨日 → `_last_fetch_bar` 与当前不同 →
                立即抓，不会有"启动后要等一分钟"的迟滞。
        """
        now = datetime.now()
        self._rollover_live_day(now)
        # ★★ v3.0.4：时段判定统一交给 tradingtime（时间处理的唯一权威）。
        #
        #   修掉两个既有缺陷：
        #     ① 旧代码只判 `weekday() >= 5`（周末），**完全不排法定节假日** ——
        #        例如国庆 10-01 是周四，会命中原区间并**全天每 3 秒打一次接口**，
        #        每次都拿回"上一个交易日"的同一个值。而同一个程序里的
        #        快照线程（_snap_tick）当时却能识别节假日 ——
        #        两个线程对"今天开不开市"给出相反答案，这正是时段判断
        #        散落成 4 份的必然结果（见 tradingtime 模块头）。
        #     ② 旧代码的 `925 <= hm <= 1505` 是个**裸数字区间**，语义含糊：
        #        它把午休（11:30~13:00）也算作"可刷新"，靠 clamp_to_bar
        #        把时刻夹回 11:30 —— 结果是午休期间每 3 秒发一轮注定
        #        拿回同一个值的请求，纯属白烧。现在午休直接不刷新。
        _sess = None
        try:
            import tradingtime
            _sess = tradingtime.classify(now)
        except Exception as e:                            # noqa: BLE001
            log_line(f"tradingtime 不可用，退回旧判定: {type(e).__name__}: {e}")
        if _sess is not None:
            if not _sess.is_trading_day:
                return                                    # 周末 / 法定节假日 / 已确证休市
            if not _sess.auto_refresh:
                return                                    # 该时段不刷新（午休 / 盘前 / 盘后已定稿）
            # 时段切换时记一次日志（帮助排查，且不会刷屏）
            _pk = _sess.phase.key if _sess.phase else "-"
            if getattr(self, "_last_phase_key", None) != _pk:
                self._last_phase_key = _pk
                log_line(f"session -> {_sess.label} · auto_refresh={_sess.auto_refresh}"
                         f" · cap={_sess.phase_cap_ms}ms · day={_sess.day_reason}")
        else:
            # ---- 兜底：保持旧的保守行为（至少不能放行周末）----
            if now.weekday() >= 5:
                return
            hm = now.hour * 100 + now.minute
            if not (925 <= hm <= 1505):
                return
        if self.worker is not None and self.worker.isRunning():
            return
        # ★★ v1.2.1 修复：**只看 `_review_locked`，不再看 current_tag()**。
        #
        #   旧代码这里还有一道 `current_tag() != live_tag` 守卫，用途是
        #   "正在回看历史日时不自动刷新"。但它的判定依据是**下拉框选中值**，
        #   而下拉框会被 on_data 程序化同步到"数据所属日期"：
        #     · 交易日开盘前启动 → 接口给的是**上一个交易日**的数据，
        #       Worker 会按 data_date 把 tag 改成昨天 → 下拉框显示昨天 →
        #       current_tag() 永远 != live_tag（今天）→
        #       **自动刷新从此每轮都直接 return，永久停摆且毫无提示**；
        #     · 更糟的是用户也没法手动切回"今日"—— 下拉框只列本地真正有数据
        #       的日期，里面根本没有今天的条目（今天还没抓到过）。
        #
        #   `_review_locked` 才是正确判据：它只在**用户真的选了历史日**
        #   （load_history）或 **--date 启动**（__init__）时置位，
        #   与"数据日 ≠ 自然日"这种完全正常的情况无关，且覆盖全部回看场景
        #   （含 v1.1.7 修的"切过去之前的窗口期"）。因此一行足矣。
        if getattr(self, "_review_locked", False):
            return
        # ★★ v3.0.4：跨分钟守卫 —— 同一交易分钟内数据不会变，不重复抓。
        #   详见本方法 docstring 的【问题】/【做法】。
        #   ★ 只在**实盘视图 + 未锁定回看**时启用；`_review_locked` 已在上方返回。
        #   ★ 用 `clamp_to_bar()`（而非 now.strftime）取刻度：午休/盘后会被夹到
        #     合法交易分钟，与面板写入口径**完全一致**（见 flowdata.clamp_to_bar）。
        #     否则盘中 11:35 那次 tick 会拿到 "11:35" 与面板里的 "11:30" 不等，
        #     判定永远"跨分钟"→ 守卫失效。
        try:
            import flowdata as _fd
            _bar = _fd.clamp_to_bar(now)
        except Exception:                                # noqa: BLE001
            _bar = None
        if _bar is not None:
            if getattr(self, "_last_fetch_bar", None) == _bar:
                return                                   # 本分钟已抓过，跳过
            self._last_fetch_bar = _bar
        # ★ v1.2.3：盘中刷新走增量通道（~350ms），不再全量重抓。
        self.reload(force=False, incremental=True)

    def open_data_dir(self):
        p = app_data_dir()
        try:
            os.startfile(p)                               # noqa: S606  (Windows)
        except Exception as e:                            # noqa: BLE001
            QMessageBox.information(self, "数据目录", f"{p}\n\n({e})")

    def open_log(self):
        p = app_log_path()
        try:
            if os.path.exists(p):
                os.startfile(p)                           # noqa: S606  (Windows)
            else:
                QMessageBox.information(self, "日志文件", f"暂无日志：{p}")
        except Exception as e:                            # noqa: BLE001
            QMessageBox.information(self, "日志文件", f"{p}\n\n({e})")

    def _build_toolbar(self):
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)
        self.tb_refresh = QAction("刷新", self)
        self.tb_refresh.setToolTip(
            "立即获取最新盘中数据\n"
            "· 交易时段：增量刷新（约 0.3 秒），拿到的就是当前这一分钟\n"
            "· 非交易时段：直接读本地已存数据")
        # ★★ v2.0.1：与菜单「刷新(F5)」同一语义 —— 取最新，而不是"读旧缓存"
        self.tb_refresh.triggered.connect(
            lambda: self.reload(force=False, incremental=True))
        tb.addAction(self.tb_refresh)
        # ★★ v2.0.1：工具栏不再出现「重新抓取」按钮（用户要求隐藏）。
        #   之所以保留对象：_set_busy() 会遍历它统一禁用/启用加载入口。
        self.tb_refetch = QAction("重新抓取", self)
        self.tb_refetch.triggered.connect(lambda: self.reload(force=True))
        self.tb_refetch.setVisible(False)
        self.tb_refetch.setEnabled(False)
        tb.addSeparator()
        # ★ 历史回看日期选择（v1.1.0）：最近 30 个交易日。
        #   条目后缀标明数据性质：实盘 = 当日实抓分钟数据；
        #   合成 = 日级真实总额 + 分钟形态合成（接口不提供历史分钟数据）。
        tb.addWidget(QLabel(" 回看日期 "))
        self.combo_date = QComboBox()
        self.combo_date.setMinimumWidth(210)
        self.combo_date.setToolTip(
            "选择要回看的交易日（最近 30 个）\n"
            "· 实盘：当日实抓的分钟级数据\n"
            "· 合成：日级真实总额 + 分钟形态合成\n"
            "  （东财接口不提供历史分钟资金流，收盘总额保真）")
        # 用 activated 而非 currentIndexChanged：只有用户主动选择才触发加载，
        # 程序化刷新列表时不会误触发。
        self.combo_date.activated.connect(self.on_date_pick)
        tb.addWidget(self.combo_date)
        tb.addSeparator()
        self.tb_info = QLabel("  尚未加载")
        # ★ v3.0.6（用户需求）：「精简排版，不要挤占空间」——
        #   它是信息栏不是按钮，不该和工具栏按钮同字号同色（视觉上在抢位）。
        #   走对象名交给全局 QSS，字号小一档、颜色弱一档，主题切换自动跟随。
        self.tb_info.setObjectName("tbInfo")
        tb.addWidget(self.tb_info)

    # ---------------------------------------------------------- 主题
    def toggle_theme(self, checked):
        self.theme_name = "dark" if checked else "light"
        self.apply_theme()

    def apply_theme(self):
        """按当前主题重刷全局配色。

        ★ v1.2.1：配色方向已固定为红涨绿跌（A 股约定），这里不再同步任何
          "方向开关"的勾选状态 —— 原先那段"菜单项 / 工具栏按钮两处勾选态
          互相同步"的逻辑，随这两个控件一起删掉了；切换方法本身也已删除
          （全库无调用方）。若日后要恢复反向配色，只改 coloring() 一处。
        """
        base = THEMES[self.theme_name]
        th = coloring(base)                      # ★ 涨跌取色统一入口
        _theme_ref["th"] = th                    # 供 fill_table 等自由函数取色
        app = QApplication.instance()
        pal = QPalette()
        pal.setColor(QPalette.Window, QColor(th["card"]))
        pal.setColor(QPalette.WindowText, QColor(th["fg"]))
        pal.setColor(QPalette.Base, QColor(th["bg"]))
        pal.setColor(QPalette.AlternateBase, QColor(th["card"]))
        pal.setColor(QPalette.Text, QColor(th["fg"]))
        pal.setColor(QPalette.Button, QColor(th["card"]))
        pal.setColor(QPalette.ButtonText, QColor(th["fg"]))
        pal.setColor(QPalette.Highlight, QColor(th["accent"]))
        app.setPalette(pal)
        # ★ QSS 也要跟着换，否则深浅色只切了一半
        app.setStyleSheet(build_qss(th))
        # ★ ch_theme 原先漏在这个元组之外 —— 切暗色时「主线簇速览」不跟着换色，
        #   白底留在深色界面里非常刺眼。补进来。
        # ★ ch_theme 原先漏在这个元组之外 —— 切暗色时「主线簇速览」不跟着换色，
        #   白底留在深色界面里非常刺眼。补进来。
        #   ★ v2.0.3：改走 _chart_widgets() —— 画布未创建时自动跳过。
        for c in self._chart_widgets():
            c.apply_theme(th)
        self.fill_kpi()          # KPI 数值颜色依赖主题
        # ★ 表格里的红/绿前景色同样取自主题（v1.0.5 补）：
        #   旧版切主题只刷 KPI 和图表，表格颜色停留在旧主题 ——
        #   暗色底上显示的还是浅色版红绿，对比度明显不足。
        self.fill_all()
        self.render_charts()

    # ---------------------------------------------------------- 加载
    def _set_busy(self, busy):
        """★ 加载期间禁用全部刷新入口（v1.0.5）：
        菜单/工具栏的「刷新」「重新抓取」在抓取进行中变灰不可点，
        配合进度条给出明确的忙碌反馈，也防止重复触发抓取。"""
        # ★★ v2.0.1：「重新抓取」已从界面移除（并入「刷新」），
        #   这两个对象不再参与忙碌态切换 —— 否则 _set_busy(False)
        #   会把它们重新置为可用，留下一个"看不见但可被脚本触发"的入口。
        for a in (self.act_refresh, self.tb_refresh):
            if a is not None:
                a.setEnabled(not busy)
        # 加载期间锁定回看日期选择与补全入口，避免与 Worker 竞争
        self.combo_date.setEnabled(not busy)
        if getattr(self, "act_backfill", None) is not None and busy:
            self.act_backfill.setEnabled(False)
        elif getattr(self, "act_backfill", None) is not None:
            bf_busy = self.backfiller is not None and self.backfiller.isRunning()
            self.act_backfill.setEnabled(not bf_busy)
        self.bar.setVisible(busy)

    # ---------------------------------------------------------- 历史回看
    def current_tag(self):
        """当前应展示的日期 tag：下拉框选中值优先，默认今日。"""
        tag = self.combo_date.currentData()
        return tag or self.live_tag

    def _refresh_date_combo(self, select_tag=None):
        """重建回看日期下拉：**只列出本地真正有数据的交易日**。

        ★ v1.1.5 改动（用户需求「把目前没有的日期都删了，
          但是未来我一天天攒进去，要求能回看」）：
          旧版列出整个 30 日窗口并把缺数据的日期置灰 —— 但那些日期
          永远不会自己长出来（历史分钟资金流接口不提供），
          留着只是噪声。现在直接**只列有数据的**：
            · 实盘日：当日抓取的完整分钟面板（100% 真实）
            · 合成日：日级真实总额 + 分钟形态合成
          每天收盘后自动归档一条，攒满 30 天后挤掉最早的一天
          —— 与 HTML 版的 localStorage 归档同一套滚动逻辑。
        """
        import store
        import archive as _arc
        flow_data = current_data_dir()
        local = set(store.list_local_panels(flow_data))

        # 排序：最新的在最上面
        rows = sorted(local, reverse=True)
        want = select_tag or self.current_tag()

        self.combo_date.blockSignals(True)
        try:
            self.combo_date.clear()
            weekday = "一二三四五六日"
            for tag in rows:
                try:
                    wd = weekday[datetime.strptime(tag, "%Y%m%d").weekday()]
                except Exception:                        # noqa: BLE001
                    wd = "?"
                src = store.panel_source(tag, flow_data)
                mark = "合成" if src == "backfill" else "实盘"
                label = f"{_pretty_date(tag)} 周{wd} · {mark}"
                if tag == self.live_tag:
                    label = f"★ {label}（今日）"
                self.combo_date.addItem(label, userData=tag)
            if self.combo_date.count() == 0:
                self.combo_date.addItem("（暂无本地数据）", userData=None)
                self.combo_date.model().item(0).setEnabled(False)
                return
            # 选中当前应展示的日期
            for row in range(self.combo_date.count()):
                if self.combo_date.itemData(row) == want:
                    self.combo_date.setCurrentIndex(row)
                    break
            else:
                self.combo_date.setCurrentIndex(0)
        finally:
            self.combo_date.blockSignals(False)

    def on_date_pick(self, idx):
        """用户在下拉框里主动选了一个交易日。"""
        tag = self.combo_date.itemData(idx)
        if not tag:
            return
        self.goto_tag(tag)

    def goto_tag(self, tag):
        """切到指定交易日（下拉框选择与"补跳暂存日期"共用这一条路径）。

        ★ v1.2.1：从 on_date_pick 里抽出来，目的是让"加载中暂存的日期"
          （见 load_history / on_data）能与用户手动选择走**完全相同**的分支 ——
          否则补跳路径容易漏掉"切回今日要解锁 _review_locked"这类副作用。
        """
        if not tag:
            return
        if tag == self.live_tag and (self.data or {}).get("date_tag") == self.live_tag:
            return                              # 已停在今日实盘，无需动作
        if tag == self.live_tag:
            # ★ v1.1.7：回到今日实盘 → **解锁**自动刷新。
            #   不解锁的话这个开关一旦置位就再也回不去了：用户回看过一次，
            #   之后切回今日盘中也不会自动刷新，而且界面上毫无提示。
            #   注意 `--date` 启动的场景不在此列（那种启动是"就是想看历史"，
            #   用户不会去点今日），锁保持到进程结束是符合预期的。
            self._review_locked = False
            self.reload(force=False)            # 回到今日（走实盘缓存路径）
        else:
            self.load_history(tag)

    def load_history(self, tag):
        """加载某个历史交易日：与实盘完全相同的渲染管线，只是数据源是本地。"""
        # ★ v1.1.7：用户主动回看 → 同样锁定，不再自动刷新。
        #   放在最前面（含"正在加载中"提前返回的分支之前）——否则用户在
        #   worker 忙时点回看被挡回去了，锁定却没置上，下一轮 auto_tick 照样开跑。
        if tag != self.live_tag:
            self._review_locked = True
        if self.worker is not None and self.worker.isRunning():
            # ★★ v1.2.1 修复：这里原来只把下拉框"恢复成当前已展示的日期"，
            #   用户刚选的 tag **没有被任何变量保存**，也没有在加载完成后补做 ——
            #   注释写着"把用户想去的日期记下来，避免选择被吞掉"，
            #   代码做的却是相反的事（把选择吞掉并回滚显示）。
            #   现在真正记进 self._pending_tag，由 on_data 收尾时补跳。
            self._pending_tag = tag
            self.lbl.setText(f"正在加载中…稍后自动切换到 {_pretty_date(tag)}")
            self.combo_date.blockSignals(True)
            for row in range(self.combo_date.count()):
                if self.combo_date.itemData(row) == (self.data or {}).get("date_tag"):
                    self.combo_date.setCurrentIndex(row)
                    break
            self.combo_date.blockSignals(False)
            return
        self._set_busy(True)
        self.lbl.setText(f"读取 {_pretty_date(tag)} 历史数据…")
        self.worker = Worker(tag=tag, local_only=True)
        self.worker.progress.connect(self.lbl.setText)
        self.worker.done.connect(self.on_data)
        # ★★ v2.0.3 两段式首屏：先行版（本地面板旧数据）也走 on_data 上屏。
        #   （load_history 的 local_only Worker 不会发 early，连上无副作用。）
        self.worker.early.connect(self.on_data)
        self.worker.failed.connect(self.on_fail)
        self.worker.start()

    # ---------------------------------------------------------- 历史补全
    def start_backfill(self, auto=False):
        """补全前 30 个交易日。auto=True 表示启动后自动触发（每次运行最多一次）。"""
        if self.backfiller is not None and self.backfiller.isRunning():
            if not auto:
                self.lbl.setText("历史补全正在进行中…")
            return
        days30 = list((self.calendar_days or [])[-30:])
        if not days30:
            if not auto:
                QMessageBox.information(
                    self, "补全历史数据",
                    "交易日历尚未就绪（可能网络受限）。\n"
                    "请先成功加载一次实盘数据，再试。")
            return
        if self.calendar_approx:
            # ★ 推断日历不含节假日信息，可能把节假日也当成交易日写出
            #   错位面板 —— 与 purge 同理，此时一律不写，等真实日历。
            if not auto:
                QMessageBox.information(
                    self, "补全历史数据",
                    "当前只拿到「工作日推断」日历（数据源限流中），\n"
                    "无法区分节假日，为避免写出错位数据，本次不执行补全。\n\n"
                    "请稍后再试：日历接口恢复后会自动使用真实交易日历。")
            return
        self.act_backfill.setEnabled(False)
        self.backfiller = BackfillWorker(days30)
        self.backfiller.progress.connect(self.lbl.setText)
        self.backfiller.done.connect(self.on_backfill_done)
        self.backfiller.failed.connect(self.on_backfill_fail)
        self.backfiller.start()
        log_line(f"backfill started ({'auto' if auto else 'manual'}), "
                 f"window={days30[0]}~{days30[-1]}")

    def on_backfill_done(self, stats):
        self.act_backfill.setEnabled(True)
        w, s = stats.get("written", []), stats.get("skipped", [])
        msg = (f"历史补全完成：新写入 {len(w)} 天，已存在 {len(s)} 天。"
               if w else f"历史数据已完整：{len(s)} 天全部就绪。")
        self.lbl.setText(msg)
        log_line(f"backfill ok: written={w} skipped={len(s)} "
                 f"sectors={stats.get('n_sectors')} failed={stats.get('failed')}")
        self._refresh_date_combo()
        if w:
            QMessageBox.information(
                self, "补全历史数据",
                f"{msg}\n\n"
                "说明：历史日的收盘资金总额为东财真实数据，\n"
                "分钟曲线为形态合成（接口不提供历史分钟资金流），\n"
                "回看下拉框中以「合成」标注，与「实盘」严格区分。")

    def on_backfill_fail(self, msg):
        self.act_backfill.setEnabled(True)
        self.lbl.setText("历史补全失败 — 可能被数据源限流，请稍后从「工具」菜单重试")
        log_line("BACKFILL FAILED:\n" + msg)

    # ---------------------------------------------------------- 龙头领先反应
    def start_leader(self, force=False, auto=False):
        """启动龙头领先反应分析（后台线程）。

        ★ 触发条件（与 HTML 版「每 5 分钟跑一轮、只分析在榜板块」一致）：
          · 仅实盘日：历史合成日的分钟曲线是合成的，算"谁先动手"没有意义；
          · 同一份数据只跑一次（leader_key 去重），除非 force；
          · **--shot 自检模式：只读缓存、不联网** ——
            联网分析会让表格逐步填充、状态栏不停改写，截图不可复现；
            但完全跳过又会让第四象限在交付截图里永远空着，
            于是这里保留"读缓存"这一步：有缓存就展示（尺寸内容都稳定），
            没缓存就显示等待文案，两种情况都可复现。
        """
        d = self.data or {}
        if not d:
            return
        if (d.get("source") or "live") == "backfill" or \
                d.get("view_mode") == "history":
            # ★★ v1.2.1 修复：不能只改提示文字就 return。
            #   旧代码留下 self.leader_rows 原样不动、也不重绘表格 ——
            #   于是"先看今日实盘（跑完龙头）→ 再切历史日"时，
            #   第四象限**仍然显示今日（或上一次）的龙头表**，
            #   与旁边"龙头分析仅对实盘日有效"的提示直接自相矛盾，
            #   属于典型的"张冠李戴且无提示"。
            #   这里必须连数据一起清掉并重绘（清空后 fill_leader 会渲染空表）。
            if self.leader_rows:
                self.leader_rows = []
                try:
                    self.fill_leader()
                except Exception as e:                   # noqa: BLE001
                    log_line(f"leader clear on history failed: {e}")
            self.lbl_leader.setText("龙头分析仅对实盘日有效（历史日为合成数据）")
            return
        shot_mode = "--shot" in sys.argv and not force
        if self.leader_worker is not None and self.leader_worker.isRunning():
            return

        F = d.get("F")
        if F is None:
            return
        last_i = max(0, len(getattr(F, "times", []) or []) - 1)
        # 在榜板块：与 HTML 版 analyzeLeaders 完全同一口径 ——
        #   概念 前9 + 后9，行业 前5 + 后5（合计 28），去重。
        # ★ 为什么分两层各取前后几名：只看净额榜前列的话，
        #   "谁在领跌"永远不会被分析到（领跌板块在净额榜最下面）。
        boards, seen_bk = [], set()
        try:
            import salience as _sal

            def _push(rows):
                for r in (rows or []):
                    c = r.get("code")
                    if c and c not in seen_bk:
                        seen_bk.add(c)
                        boards.append(r)

            _push(_sal.score_table(F, last_i, 9, kind="concept", direction="in"))
            _push(_sal.score_table(F, last_i, 9, kind="concept", direction="out"))
            _push(_sal.score_table(F, last_i, 5, kind="industry", direction="in"))
            _push(_sal.score_table(F, last_i, 5, kind="industry", direction="out"))
        except Exception as e:                           # noqa: BLE001
            log_line(f"leader board build failed: {e}")
            boards = []
        if not boards:                                   # 兜底：用现成榜单
            try:
                _push = None
                for r in (d.get("board") or [])[:12]:
                    boards.append(r)
                for r in (d.get("board_out") or [])[:12]:
                    boards.append(r)
            except Exception:                            # noqa: BLE001
                boards = []
        if not boards:
            self.lbl_leader.setText("无在榜板块，跳过龙头分析")
            return

        key = (d.get("date_tag"), len(getattr(F, "times", []) or []))
        if not force and self.leader_key == key and self.leader_rows:
            return                                       # 同一份数据不重复跑
        self.leader_key = key
        self._leader_tag = d.get("date_tag")

        # ★ 先吃缓存（v1.1.5）：同一交易日内已经算过的板块直接展示，
        #   联网只跑缺的那些 —— 重开程序/切页回来后不用再空等几十秒。
        #   实测一轮 28 板块 ≈ 250 请求 / 42 秒，缓存命中后通常是 0 请求。
        cached, todo = [], list(boards)
        try:
            import leader as _ld
            cached, todo = _ld.from_cache(d.get("date_tag"), boards,
                                          current_data_dir())
        except Exception as e:                           # noqa: BLE001
            log_line(f"leader cache read skipped: {e}")
        if cached:
            self.leader_rows = cached
            self.fill_leader()
            log_line(f"leader cache hit: {len(cached)} boards")

        if shot_mode:
            # 自检：只用缓存，不启动任何联网线程（保证截图可复现）
            if not cached:
                self.lbl_leader.setText("等待分析…（首次约需 10~30 秒，后台进行）")
            return

        if not todo:
            self.lbl_leader.setText(
                f"已分析 {len(cached)} 个在榜板块（缓存）· 领先′ 为正 = 个股先动手")
            return

        self.lbl_leader.setText(
            f"龙头分析中…（{len(todo)} 个待算"
            + (f"，{len(cached)} 个已用缓存" if cached else "") + "）")

        # ★ v3.0.6：替代源（腾讯）**没有板块成分股接口** —— 龙头领先反应
        #   需要逐只成分股的分钟曲线，做不了。这里**如实提示并跳过**，
        #   而不是发一堆注定失败的请求（那只会让限流更严重）。
        #   注意位置：放在"读缓存并展示"之后，所以历史缓存仍然看得到。
        try:
            import source as _src
            if not _src.cap("sector_members"):
                self.lbl_leader.setText(
                    f"{_src.active()} 源不提供板块成分股 → 龙头领先反应不可用"
                    f"（{len(cached)} 个已有缓存仍展示；"
                    f"可用「工具→数据源…」切回东财）")
                log_line(f"leader skipped: {_src.active()} 源无 sector_members 能力")
                return
        except Exception:                                # noqa: BLE001
            pass

        payload = {
            "times": list(getattr(F, "times", []) or []),
            "codes": list(getattr(F, "codes", []) or []),
            "names": dict(getattr(F, "names", {}) or {}),
            "V": getattr(F, "V", None),
            "boards": todo,
            "tag": d.get("date_tag"),
        }
        if payload["V"] is None:
            return
        self.leader_worker = LeaderWorker(payload)
        self.leader_worker.progress.connect(self.lbl_leader.setText)
        self.leader_worker.done.connect(self.on_leader_done)
        self.leader_worker.failed.connect(self.on_leader_fail)
        self.leader_worker.start()
        log_line(f"leader analysis started, boards={len(todo)} "
                 f"(cached={len(cached)})")

    def on_leader_done(self, rows):
        rows = rows or []
        # 缓存命中 + 新算的一起展示
        if self.leader_rows:
            by_code = {r.get("code"): r for r in self.leader_rows}
            for r in rows:
                by_code[r.get("code")] = r
            rows = list(by_code.values())
        self.leader_rows = rows
        # ★ 落盘（v1.1.5）：只写 ok 的，避免把限流导致的空结果固化下来
        try:
            import leader as _ld
            ok_rows = [r for r in rows if r.get("ok")]
            if ok_rows:
                n = _ld.save_cache(getattr(self, "_leader_tag", None) or
                                   (self.data or {}).get("date_tag"),
                                   ok_rows, current_data_dir())
                if n:
                    log_line(f"leader cache saved: {n} boards")
        except Exception as e:                           # noqa: BLE001
            log_line(f"leader cache write skipped: {e}")
        ok = sum(1 for r in self.leader_rows if r.get("ok"))
        self.lbl_leader.setText(
            f"已分析 {len(self.leader_rows)} 个在榜板块"
            f"（其中 {ok} 个拿到龙头）· 领先′ = 板块拐点 − 个股反应，正数=个股先动手")
        self.fill_leader()
        # ★★ v2.0.1：龙头结果到位后**同时刷新实时榜单** —— 那一页新增的
        #   「龙头选手」列取自同一份数据。不刷的话，用户要等到下一次
        #   30 秒自动刷新才看得到列里的内容（首轮尤其明显：
        #   榜单先出、龙头后到，中间会空着一列）。
        try:
            self.fill_watch()
        except Exception:                                # noqa: BLE001
            pass
        log_line(f"leader analysis ok: rows={len(self.leader_rows)} with_leader={ok}")

    def on_leader_fail(self, msg):
        self.lbl_leader.setText("龙头分析失败 — 可能被数据源限流，稍后可从「工具」菜单重试")
        log_line("LEADER FAILED:\n" + msg)

    def fill_leader(self):
        """把龙头分析结果填进第四象限的表格。"""
        rows = self.leader_rows or []
        out = []
        for r in rows:
            if not r.get("ok"):
                continue
            up = (r.get("up") or [{}])[0]
            dn = (r.get("down") or [{}])[0]
            out.append([
                str(r.get("name", ""))[:12],
                f'{float(r.get("net", 0) or 0):+.1f}',
                str(up.get("name", "—"))[:10],
                f'{float(up.get("avg_lead", 0) or 0):.1f}' if up else "—",
                f'{up.get("hits", 0)}/{up.get("n", 0)}' if up else "—",
                str(dn.get("name", "—"))[:10],
                f'{float(dn.get("avg_lead", 0) or 0):.1f}' if dn else "—",
                f'{dn.get("hits", 0)}/{dn.get("n", 0)}' if dn else "—",
            ])
        # 按板块净额绝对值降序：最活跃的板块排最上面
        out.sort(key=lambda x: -abs(_fnum(x[1])))
        try:
            fill_table(self.t_leader, out)
        except Exception:                                # noqa: BLE001
            pass

    def reload(self, force=False, incremental=False):
        """重新加载数据。

        ★★ v2.0.1：「刷新」的语义统一为 —— **立即拿到最新数据**。

          force       —— True 走全量重抓（1000 次请求）。这是**内部**语义：
                         首次建表、时间轴缺口补齐、自检脚本会用到。
                         界面入口「重新抓取」已按用户要求移除（并入「刷新」）。
          incremental —— True 表示"允许走增量通道取最新"。界面上的
                         刷新(F5) / 工具栏「刷新」/ 自动刷新 全部传 True。

        至于"这次到底要不要联网、走全量还是增量" —— 由 Worker 内部的
        决策树按「是否交易时段 / 缓存多新 / 时间轴缺口多大」判定，
        界面层不再自己猜。这是修掉"点刷新有时新、有时还是旧数据"的关键：
        旧版把判定散在三个分支里，同一个按钮走哪条路取决于调用参数。
        """
        if self.worker is not None and self.worker.isRunning():
            self.lbl.setText("正在加载中，请稍候…")
            return
        self._set_busy(True)
        if incremental and not force:
            self.lbl.setText("获取最新盘中数据…")
        else:
            self.lbl.setText("正在重新抓取全部数据…" if force else "正在准备数据…")
        self.worker = Worker(force=force, incremental=incremental and not force)
        self.worker.progress.connect(self.lbl.setText)
        self.worker.done.connect(self.on_data)
        # ★★ v2.0.3 两段式首屏：先行版（本地面板旧数据）也走 on_data 上屏。
        #   （load_history 的 local_only Worker 不会发 early，连上无副作用。）
        self.worker.early.connect(self.on_data)
        self.worker.failed.connect(self.on_fail)
        self.worker.start()

    def on_fail(self, msg):
        """失败时给用户看得懂的提示；原始堆栈只进日志，不弹窗。

        ★ v1.1.6 补两件事（原来只改状态栏文字，界面其余部分毫无反应）：
          ① 图表区从「数据加载中…」切到**错误态**占位 ——
             否则用户看到的是"还在转圈"，分不清是卡住还是已经失败；
          ② 首次加载失败时清空/占位图表，避免它们停在旧数据上
             —— "状态栏说失败、图还在画昨天的数据"最容易误导。
             注意：仅在**尚无有效数据**时处理；已有数据后的刷新失败，
             保留旧数据比清成空白更有用。

        ★ v1.2.1：失败详情的去处调整（「数据状态」页已删除）：
          · 完整文本写入 self.status_text（帮助 →「运行状态」可查看）；
          · 关键原因同时写进日志文件；
          · 界面侧只保留状态栏一行 + 弹窗里的一句话原因。
          原先末尾那句 `setCurrentIndex(tabs.count()-1)`（跳到最后一页
          看原因）必须去掉 —— 删掉数据状态页后，count()-1 变成了
          「实时榜单」，失败时会把用户莫名其妙甩到榜单页且不报错。
        """
        self._set_busy(False)
        self.lbl.setText("加载失败 — 请检查网络后按 F5 重试")
        log_line("LOAD FAILED:\n" + msg)

        # ① 图表区错误态（首次失败无数据时才清，避免抹掉已加载的行情）
        if not (self.data or {}).get("F"):
            try:
                for c in self._chart_widgets():
                    c.blank("数据加载失败\n（帮助 → 运行状态 可看详情 · 按 F5 重试）")
                self.lbl_leader.setText("数据加载失败，龙头分析未运行")
            except Exception:                            # noqa: BLE001
                pass

        # 从异常类型推断人话解释
        head = msg.splitlines()[0] if msg else ""
        if "ModuleNotFoundError" in msg or "ImportError" in msg:
            reason = "程序组件缺失（打包不完整）"
        elif "getaddrinfo" in msg or "Name or service" in msg or "URLError" in msg:
            reason = "无法连接数据源（本机网络或 DNS 异常）"
        elif "板块清单为空" in msg or "未取到任何板块数据" in msg:
            reason = "数据源无响应或接口变更（可能被限流）"
        elif "PermissionError" in msg:
            reason = "缓存目录不可写"
        else:
            reason = "数据加载过程中出现异常"

        # ★ v1.2.1：失败详情不再写进已删除的「数据状态」页，改为
        #   ①缓存到 status_text（帮助 → 运行状态 可看）；②关键原因进日志。
        #   同时**不做任何页签跳转**（原先的 count()-1 在删页后会跳错）。
        detail = "\n".join([
            "数据加载失败",
            "=" * 52,
            "",
            f"【原因】{reason}",
            "",
            "【建议】",
            "  1. 确认本机可以正常访问互联网；",
            "  2. 按 F5 重试（「刷新」会自动去取最新数据，无需其他操作）；",
            "  3. 若为非交易时段，数据源可能尚未生成当日分钟数据，",
            "     此时显示的是最近一个有数据的交易日，属正常现象。",
            "",
            "【缓存目录】",
            f"  {app_data_dir()}",
            "",
            "【日志文件】",
            f"  {app_log_path()}",
            "",
            "【技术细节】（排查用，可直接忽略）",
            "-" * 52,
            msg,
        ])
        self.status_text = detail
        log_line(f"LOAD FAILED reason: {reason}")
        QMessageBox.warning(
            self, "数据加载失败",
            f"{reason}\n\n按 F5 可重试。\n"
            "详细原因见 帮助 →「运行状态」，或日志文件。")

    def on_data(self, d):
        # ★★ v2.0.3：early=True 表示这是"先行版"（本地面板的旧数据，
        #   全量补抓仍在后台跑）。归档/龙头分析/忙碌态等只该做一次的动作
        #   全部推迟到最终版（_early 不带）那次再做。
        _early = bool(d.get("_early"))
        self.data = d
        if not _early:
            self._set_busy(False)
        n1, n2 = d.get("n_sectors", 0), d.get("n_minutes", 0)
        tag = _pretty_date(d.get("date_tag", ""))
        src = d.get("source") or "live"
        src_cn = "合成" if src == "backfill" else "实盘"
        # ★ 状态栏明确标出「数据属于哪一天、是什么性质」：
        #   配合按日缓存与历史合成数据，用户一眼能确认自己看的
        #   不是隔日旧行情、也能区分实盘与合成（v1.1.0）
        # ★ 「实盘视图」按数据性质判定（v1.1.0）：周末启动时展示的虽是
        #   周五面板，但它是当前最新实盘视图，不是用户主动的历史回看。
        is_live_view = src != "backfill" and d.get("view_mode") != "history"
        mode_cn = "" if is_live_view else " · 历史回看"
        # ★★ v2.0.1：状态栏补上**数据新鲜度**两个时间戳。
        #
        #   旧文案只说"240 个分钟点"，用户无法一眼判断这是不是最新数据 ——
        #   而"刷新后看不到最新"正是本次要修的核心体验之一。
        #   现在并排给出：
        #     · 分时到 HH:MM    —— 数据本身到哪一分钟（数据侧）
        #     · 更新于 HH:MM:SS —— 本次上屏发生在什么时候（程序侧）
        #   两者一对照，"是不是最新的"不需要猜；盘中若发现"更新于"在动、
        #   "分时到"不动，也能立刻明白是数据源这几分钟没出新分钟，
        #   而不是程序没刷新。
        _times = d.get("times") or []
        last_hm = str(_times[-1]) if _times else ""
        upd_hm = datetime.now().strftime("%H:%M:%S")
        self.lbl.setText(
            f"就绪 · {n1} 个板块 · {n2} 个分钟点"
            + (f" · 分时到 {last_hm}" if last_hm else "")
            + (f" · 数据 {tag}（{src_cn}）" if tag else "")
            + mode_cn + f" · 更新于 {upd_hm}")
        if _early:
            # ★ v3.0.6：两种先行版文案要分开 ——
            #   · _live_only（快速先行版）：**已经含当前这一分钟的真实值**，
            #     缺的只是中间那段历史；说"正在补齐缺失分钟"会让人以为
            #     看到的还是旧数据，白等；
            #   · 否则（旧面板先行版）：确实是旧数据，如实说明。
            self.lbl.setText(
                self.lbl.text()
                + (" · 已含当前分钟，历史缺口后台补齐中…"
                   if d.get("_live_only")
                   else " · 正在后台补齐缺失分钟…"))
        self.tb_info.setText(
            f"  板块 {n1}   分钟点 {n2}"
            + (f"   分时到 {last_hm}" if last_hm else "")
            + (f"   数据日 {tag}（{src_cn}）" if tag else "")
            # ★ v3.0.6：用替代源时必须**在界面上看得到** —— 数据能力不同
            #   （腾讯源没有分钟级资金流历史），藏起来会让人以为程序坏了。
            + (f"   源 {source_tag()}" if source_tag() else ""))
        log_line(f"loaded ok · sectors={n1} minutes={n2} source={src} "
                 f"tag={d.get('date_tag')} "
                 f"times={(d.get('times') or ['?'])[0]}~{(d.get('times') or ['?'])[-1]}")

        # 实盘加载带回了新的交易日历 → 刷新回看下拉框
        if d.get("calendar_days"):
            self.calendar_days = list(d["calendar_days"])
            self.calendar_approx = bool(d.get("calendar_approx"))
            if self.calendar_approx:
                log_line("calendar is approx (weekday fallback)")
        self._refresh_date_combo(select_tag=d.get("date_tag"))

        # ★★ v2.0.1：三段各自 try + 计时。
        #   为什么必须分开包：这三个动作里任何一个抛出的异常，
        #   在 Qt 信号槽里都只打到 stderr、不中断事件循环 ——
        #   表现是"界面停住但程序不崩、日志无痕"。
        #   本版就靠这条纪律抓出了一个真实 bug（fill_all 里变量名冲突，
        #   见 fill_all 的注释）。分开包之后，任何一段失败都会留日志。
        _t0 = time.perf_counter()
        try:
            self.fill_kpi()
        except Exception as _e:                          # noqa: BLE001
            log_line(f"fill_kpi failed: {type(_e).__name__}: {_e}")
        _t1 = time.perf_counter()
        try:
            self.fill_all()
        except Exception as _e:                          # noqa: BLE001
            log_line(f"fill_all failed: {type(_e).__name__}: {_e}")
        _t2 = time.perf_counter()
        try:
            self.fill_open_pages()
        except Exception as _e:                          # noqa: BLE001
            log_line(f"fill_open_pages failed: {type(_e).__name__}: {_e}")
        _t2b = time.perf_counter()
        # ★★ v2.0.9：图表绘制**延后半拍**（singleShot(0) → 事件循环先回到主循环，
        #   表格与 KPI 立即上屏）—— 缓存通道首屏的**可见内容**提前约 0.3s，
        #   图表紧跟着绘制，总量不变。fill_open_pages（开盘检测/个股异动/个股精选）
        #   是纯本地快照读取，也一并提前。
        # ★★ v2.0.11 修复：上下文必须**显式传参**，不能靠闭包/作用域共享。
        #   根因：_on_data_charts 是 v2.0.9 从 on_data 拆出来的**独立方法**
        #   （目的是让事件循环先刷表格、图表延后半拍），但函数体里仍在引用
        #   on_data 的**局部变量** —— d / src / is_live_view / _early：
        #     · 5668 行归档守卫  `if is_live_view and not _early:`
        #     · 5703 行龙头守卫  `if is_live_view and not _early:`
        #   这些名字在 _on_data_charts 自己的作用域里根本不存在。
        #   而 lambda 是被 QTimer.singleShot 排到事件循环**下一拍**执行的，
        #   它抛出的 NameError 只会打到 stderr、**不中断事件循环**
        #   （Qt 信号槽内的异常语义）→ 表现是「界面停住、程序不崩、日志无痕」。
        #
        #   实测症状：题材簇页「龙头领先反应」永远停在「等待分析…」
        #   （start_leader 永不被调用），且同处尾部的实盘日归档
        #   （maybe_archive）也一并被静默跳过。
        #   ★ 为什么以前没人发现：--shot 自检里日志只记到 on_data charts，
        #     而 5668/5703 之后的代码全被跳过，日志里「安静得很正常」。
        #
        #   ★ 教训（写给未来的自己）：把一个方法拆成两半时，局部变量不会跟着走。
        #     拆完必须过一遍「这段还引用了谁」—— 用 symtable 静态扫
        #     「引用了但未定义的全局名」最可靠（不含注释/docstring 误报）。
        QTimer.singleShot(0, lambda: self._on_data_charts(
            _t2b, d, src, is_live_view, _early))
        _t3 = time.perf_counter()
        log_line(f"on_data UI · fill={(_t1 - _t0) * 1000:.0f}ms"
                 f"+{(_t2 - _t1) * 1000:.0f}ms"
                 f"+open={(_t2b - _t2) * 1000:.0f}ms "
                 f"since_boot={(_t3 - T_BOOT) * 1000:.0f}ms "
                 f"dirty={sorted(getattr(self, '_charts_dirty', [])) or '[]'}")

    def _on_data_charts(self, _t0, d, src, is_live_view, _early):
        """on_data 的第二段：图表绘制（v2.0.9 拆分，事件循环先刷表格）。

        ★★ v2.0.11：上下文（d / src / is_live_view / _early）改为**显式传参**。
          v2.0.9 拆分时把它们留在了 on_data 的局部作用域，本方法却继续引用 →
          每次调用必抛 NameError（被 Qt 吞掉、不中断事件循环）→
          尾部「实盘日归档」与「龙头领先反应」永久静默失效。
          详见调用点（on_data 里 singleShot 那段）的注释与 _scopechk.py 守卫。
        """
        # ★★ v2.0.1：only_visible=True —— 首屏只画「当前可见页」的图。
        #   理由与实测数据见 CHART_PAGE 的注释：数据处理只要 94 ms，
        #   首屏的时间几乎全花在"把 6 张图一次性画完"上，
        #   而其中 5 张在别的页签、用户此刻根本看不到。
        #   FLOWLIVE_RENDER_ALL=1 可强制全画（诊断/对照用，见 _env_render_all）。
        _t1 = time.perf_counter()
        try:
            self.render_charts(only_visible=not _env_render_all())
        except Exception as _e:                          # noqa: BLE001
            log_line(f"render_charts failed: {type(_e).__name__}: {_e}")
        _t2 = time.perf_counter()
        log_line(f"on_data charts · charts={(_t2 - _t1) * 1000:.0f}ms "
                 f"since_boot={(_t2 - T_BOOT) * 1000:.0f}ms "
                 f"dirty={sorted(getattr(self, '_charts_dirty', [])) or '[]'}")

        # ★ v1.2.1：补跳"加载中被暂存"的日期（见 load_history）。
        #   用 singleShot(0) 而不是直接调 —— on_data 是 worker 的 done 信号
        #   回调，此刻 self.worker 仍处于"运行中"的收尾态；直接同步再起一个
        #   worker 会撞上 load_history 开头的 isRunning() 判断，
        #   又变成一次"刚设上的 pending 被自己挡回去"。排到事件循环下一拍最稳。
        _pend = getattr(self, "_pending_tag", None)
        if _pend and _pend != d.get("date_tag"):
            self._pending_tag = None
            QTimer.singleShot(0, lambda t=_pend: self.goto_tag(t))
        elif _pend:
            self._pending_tag = None

        # ★ 启动后**不再自动批量合成历史数据**（v1.1.5 方案调整）：
        #   v1.1.0 会自动把前 30 个交易日用"日级真实总额 + 分钟形态合成"
        #   补齐；但用户明确要求改为 HTML 版那种「一天天攒」的模式 ——
        #   只有真实抓取的数据才值得回看，合成的分钟曲线会误导盘中判断。
        #   因此：
        #     · 归档由每个交易日的实盘加载自动完成（见上方 maybe_archive）；
        #     · 批量合成降级为**工具菜单里的手动选项**，默认不跑。
        #   会话持久化开关也能在这里扩展（如配置文件），当前保持简单：不自动跑。
        # ★ 实盘日归档（v1.1.5，移植自 HTML 版归档机制）：
        #   每个交易日的数据落一条归档记录，滚动保留最近 30 个交易日，
        #   攒满后新的一天挤掉最早的一天 —— 回看下拉就是照着它生长的。
        #   幂等 + 只在数据定稿（分钟点足够多）时写，详见 archive.py。
        #   ★ v2.0.3：先行版（_early）不归档 —— 数据未定稿，最终版会再做。
        if is_live_view and not _early:
            try:
                import archive as _arc
                res = _arc.maybe_archive(
                    d.get("date_tag"), len(d.get("times") or []),
                    d.get("n_sectors", 0), src, current_data_dir())
                # ★ v1.2.1 修复：必须看 res["ok"]。
                #   旧代码只判 action in (add, update) 就记"归档成功"，
                #   而 action 只表示"这次算下来属于新增/更新"，
                #   **落盘是否成功是另一个字段 ok**（archive._write 的返回值）。
                #   磁盘满 / 目录只读 / 文件被占用时 ok=False，
                #   旧代码照样打 "archived ..."，把静默失败伪装成成功 ——
                #   唯一线索只剩"回看下拉怎么不长了"。
                if res and res.get("action") in ("add", "update"):
                    if res.get("ok"):
                        log_line(f"archived {d.get('date_tag')}: "
                                 f"{res['action']} total={res['total']} "
                                 f"evicted={res.get('evicted')}")
                    else:
                        # 落盘失败：记日志 + 留一个持久警告（会被 show_status
                        # 收进「运行状态」对话框；不能只写 status_text，
                        # 因为紧随其后的 show_status() 会整体覆盖它）。
                        self._archive_warn = (
                            f"{d.get('date_tag')} 归档写入失败 —— "
                            f"请检查 {app_data_dir()} 是否可写（磁盘满/只读/被占用）")
                        log_line(f"archive WRITE FAILED ({d.get('date_tag')}): "
                                 f"action={res['action']} —— 归档未能落盘")
            except Exception as e:                        # noqa: BLE001
                log_line(f"archive skipped: {e}")

        # ★ 龙头领先反应（v1.1.5）：实盘视图加载完成后自动跑一轮。
        #   只对实盘日有效（历史日分钟曲线是合成的，算"谁先动手"无意义），
        #   --shot 自检模式下禁用（会改写表格与状态栏，破坏截图可复现性）。
        #   ★ v2.0.3：先行版（_early）不启动 —— 用旧数据算龙头还会占住
        #     leader_key，最终版到了反被去重跳过；等最终版再跑。
        if is_live_view and not _early:
            self.start_leader(auto=True)
        self.show_status()
        # ★★ v2.0.9：fill_open_pages 已提前到 on_data 第一段（表格/KPI 同批），
        #   此处不再重复调用。

    # ---------------------------------------------------------- KPI 卡
    def fill_kpi(self):
        """填充总览顶部 6 张指标卡 —— 流入与流出必须同时呈现。"""
        d = self.data
        th = coloring(THEMES[self.theme_name])
        m = d.get("market") or {}
        times = d.get("times") or []
        rng = f"{times[0]}–{times[-1]}" if times else ""

        # ★ 双指数（v1.1.5）：上证指数主位，东财全A 紧随其后显示在副标题里。
        #   全A 的涨跌幅由它自己的分时序列现算（与图上同一口径），
        #   取不到就整条省略 —— 不编数字。
        pct = m.get("index_pct")
        pct_all = None
        try:
            rows = d.get("index_all") or []
            if rows:
                base = float(rows[0][1])
                cur = float(rows[-1][1])
                if base:
                    pct_all = (cur - base) / base * 100
        except Exception:                                # noqa: BLE001
            pct_all = None

        sub_idx = rng
        if pct_all is not None:
            sub_idx = f"上证 {pct:+.2f}%  ·  全A {pct_all:+.2f}%" if pct is not None \
                else f"全A {pct_all:+.2f}%"
        if pct is None and pct_all is None:
            self.kpi_index.set_value("—", rng)
        elif pct is None:
            self.kpi_index.set_value(
                f"{pct_all:+.2f}%", f"东财全A（上证缺）  {rng}",
                color=(th["up"] if pct_all >= 0 else th["dn"]))
        else:
            self.kpi_index.set_value(
                f"{pct:+.2f}%", sub_idx,
                color=(th["up"] if pct >= 0 else th["dn"]))

        # 流入 / 流出 板块数
        n_in, n_out = m.get("n_in", 0), m.get("n_out", 0)
        n_tot = max(1, m.get("n_total", 0))
        self.kpi_in.set_value(
            f"{n_in} 个",
            f"占比 {n_in / n_tot * 100:.0f}%  合计 {m.get('sum_in', 0):+.0f} 亿",
            color=th["up"])
        self.kpi_out.set_value(
            f"{n_out} 个",
            f"占比 {n_out / n_tot * 100:.0f}%  合计 {m.get('sum_out', 0):+.0f} 亿",
            color=th["dn"])

        # 全市场净额
        net = m.get("net_total", 0)
        self.kpi_net.set_value(
            f"{net:+.1f} 亿",
            f"流入 {m.get('sum_in', 0):+.0f} / 流出 {m.get('sum_out', 0):+.0f}",
            color=(th["up"] if net >= 0 else th["dn"]))

        # 最强流入 / 最强流出
        # ★ 空数据必须显式回落（v1.1.6 修复）：原来只在 `if ti:` 时赋值，
        #   没有 else —— 当这一轮没有任何净流入板块时，卡片会**保留上一份
        #   数据的旧值**（实测：数据已清空，卡上仍是「+88.8 亿 / 半导体」）。
        #   用户看不出这是陈年残留，会直接当成当前行情读。
        ti = (m.get("top_in") or [None])[0] or {}
        to = (m.get("top_out") or [None])[0] or {}
        if ti:
            self.kpi_top_in.set_value(
                f'{ti.get("net", 0):+.1f} 亿',
                str(ti.get("name", ""))[:12], color=th["up"])
        else:
            self.kpi_top_in.set_value("—", "无净流入板块")
        if to:
            self.kpi_top_out.set_value(
                f'{to.get("net", 0):+.1f} 亿',
                str(to.get("name", ""))[:12], color=th["dn"])
        else:
            self.kpi_top_out.set_value("—", "无净流出板块")

    # ---------------------------------------------------------- 填表
    def _fmt_rows(self, rows):
        out = []
        for k, r in enumerate(rows, 1):
            out.append([k, r.get("name", ""), f'{r.get("net_yi", 0):+.2f}',
                        f'{r.get("d5_yi", 0):+.2f}', f'{r.get("salience", r.get("score", 0)):.2f}'
                        if isinstance(r.get("salience", r.get("score", 0)), (int, float)) else ""])
        return out

    def _net_color(self, row, idx=2):
        """按 `row[idx]` 的数值正负取涨跌色。

        ★ v2.0.1：列索引用参数传（原先写死 row[2]）。
          实时榜单的累积池表在 v2.0.1 插入了「龙头选手」列，
          净额从第 2 列挪到第 3 列 —— 写死索引会让那一列按题材名的
          长度去判红绿（金额列还在、颜色却完全错），而且不报错。
        """
        try:
            c = coloring(THEMES[self.theme_name])
            return QColor(c["up"]) if _fnum(row[idx]) >= 0 else QColor(c["dn"])
        except Exception:
            return None

    def _leader_map(self):
        """板块 → 龙头选手名。两个来源，优先级从高到低：

          ① **leader 领先反应**（`leader_rows`）：板块在拐点上**最先动手**的那只。
             语义更精细（回答"谁先动"），但只覆盖在榜的少量板块 ——
             它是网络密集任务（22 个板块 ≈ 250 次请求 / 42 秒），只能抽样跑。
          ② **快照领涨股**（`spot_leaders`，来自 flowdata 的 f128 字段）：
             覆盖**全部板块**、随每次刷新更新、零额外请求。

        实现上 ② 先铺底、① 后写覆盖 —— 用户问的是"这个板块现在的龙头是谁"，
        覆盖面优先；"拐点上先动手"是更细的追问，有则更好、没有也不影响。

        ★ 不编造：两个来源都没有该板块时不写入映射，界面显示「—」。
        """
        out = {}
        # ② 快照领涨股（铺底：覆盖全部板块）
        for secid, v in ((self.data or {}).get("spot_leaders") or {}).items():
            if not isinstance(v, dict):
                continue
            nm = str(v.get("lead") or "")
            if not nm:
                continue
            out[str(secid)] = nm
            if str(secid).startswith("90."):
                out[str(secid)[3:]] = nm          # 同时支持裸代码匹配
        # ① leader 分析结果（后写，覆盖同键的快照值）
        for r in (self.leader_rows or []):
            if not r.get("ok"):
                continue
            ups = r.get("up") or []
            nm = (ups[0].get("name") if ups else "") or ""
            if not nm:
                continue
            if r.get("code"):
                out[str(r["code"])] = nm
            if r.get("name"):
                out[str(r["name"])] = nm
        return out

    # ---------------------------------------------------------- 实时榜单（累积池）
    def fill_watch(self):
        """把累积池铺进「实时榜单」页（v1.1.7，v2.0.1 增列）。

        ★ v2.0.1 列结构变化（用户要求）：
            旧： # / 板块概念 / 净额 / 辨识度 / 首次 / 最近 / 入选原因
            新： 序号 / 板块概念 / **龙头选手** / 净额 / 辨识度 / 首次 / 最近 / 入选原因
          · 「#」改称「序号」（与中文界面其余部分一致）；
          · 新增「龙头选手」= 该板块拐点上先动手的那只个股
            （取自龙头领先反应分析，未分析到显示「—」，不编造）。

        着色口径：
          · 净额列 —— 跟随涨跌配色（注意列号已因插列而变，走 _net_color(row, 3)）；
          · 板块名列 —— 本轮**新进**的用强调色（accent），已在池中的用常态色，
            这样"盘中突发被追加进来"的那几条一眼就能挑出来（用户原话：
            「这样子就能清晰看到异动的板块和概念」）。
        """
        d = self.data or {}
        rows = d.get("watch") or []
        th = coloring(THEMES[self.theme_name])
        lmap = self._leader_map()

        def _leader_of(r):
            # code 优先（更精确），退回名称匹配；都没有则「—」
            for k in (str(r.get("code") or ""), str(r.get("name") or "")):
                if k and k in lmap:
                    return lmap[k]
            return "—"

        disp = []
        for r in rows:
            nm = str(r.get("name", ""))
            if r.get("is_new"):
                nm = "▲ " + nm
            disp.append([
                r.get("rank", ""), nm, _leader_of(r),
                f'{float(r.get("net_yi", 0) or 0):+.2f}',
                f'{float(r.get("salience", 0) or 0):.2f}',
                str(r.get("first_time", "")),
                str(r.get("last_time", "")),
                str(r.get("reason", "")),
            ])

        def _name_col(row):
            # 新进标记在文本里（▲），着色按池内状态分
            c = dict(th)
            return QColor(c["accent"] if row[1].startswith("▲") else c["fg"])

        def _leader_col(row):
            # 有龙头 → 强调色（信息有效）；「—」→ 弱色（避免一片高亮反而看不出重点）
            return QColor(th["fg"] if row[2] not in ("", "—") else th["muted"])

        fill_table(self.t_watch, disp,
                   {1: _name_col, 2: _leader_col,
                    3: lambda row: self._net_color(row, 3)})

        # 顶部状态行 + 两张当下榜
        n_all, n_in, n_out, n_ev = (list(d.get("watch_stats") or []) + [0, 0, 0, 0])[:4]
        note = d.get("pool_note") or ""
        rnd = d.get("watch_rounds") or 0
        upd = d.get("watch_updated") or ""
        if rows:
            self.lbl_watch.setText(
                f"累计 {n_all} 个（流入 {n_in} / 流出 {n_out}）· "
                f"其中盘口异动追加 {n_ev} 个 · 已刷新 {rnd} 轮"
                + (f" · 最近一轮 {upd}" if upd else "")
                + (f" · {note}" if note else ""))
        else:
            self.lbl_watch.setText(
                d.get("watch_err")
                or note or "尚无榜单数据（等待首轮加载）")

        self.lbl_watch_leg.setText(
            "▲ 新进 = 本轮刚追加　·　原因 = 入选依据（首个原因＋后续追加）"
            "　·　首次/最近 = 入选时刻与最近一次触发时刻")

        base_cols = {2: self._net_color, 3: self._net_color}
        fill_table(self.t_wb_in,
                   self._fmt_rows(d.get("watch_base_in") or []), base_cols)
        fill_table(self.t_wb_out,
                   self._fmt_rows(d.get("watch_base_out") or []), base_cols)
        # ★★ v2.0.1：下半部分补到四个模块（用户要求）——
        #   后两张按**近 5 分钟增量**排序，口径见 salience.velocity_table。
        #   四张表结构完全一致（序号/题材/净额/5分钟/辨识度），
        #   所以共用同一个 _fmt_rows 与着色映射，不必写四套。
        fill_table(self.t_wb_d5in,
                   self._fmt_rows(d.get("watch_d5_in") or []), base_cols)
        fill_table(self.t_wb_d5out,
                   self._fmt_rows(d.get("watch_d5_out") or []), base_cols)

    def fill_all(self):
        """把所有表格铺满数据 —— 按区块调度，**每块独立容错**。

        ★★ v2.0.1 重构（起因是一个被 --shot 自检抓出来的真实 bug）：
          【bug】原代码在本函数内用 `th` 表示「题材簇列表」
              （`th = d.get("themes") or []`），而全代码库的惯例是
              `th` = 主题色字典（`coloring(THEMES[...])`）。
              本版新增的雷达填充沿惯例写了 `th["accent"]` ——
              实际拿到的是**列表的下标访问**，抛 TypeError。
          【为什么难发现】Qt 信号槽里抛出的异常只打到 stderr、
              不会中断事件循环，于是：on_data 从那一行往后**全部不执行**
              → 图表不画、暂存日期不补跳、连耗时日志都不写。
              界面上只剩"数据加载中…"的占位，日志里一条错误都没有。
          【两层修法】
              ① 列表改名 `themes_rows`（消除与"主题色 th"惯例的冲突 ——
                 同名不同义是本项目最容易反复踩的一类坑）；
              ② 每个区块各自 try/except + 记日志：任何一块失败都不再
                 连累其它块，且**必然在日志里留下痕迹**。
        """
        d = self.data
        th = coloring(THEMES[self.theme_name])
        cols = {2: self._net_color, 3: self._net_color}

        def _seg(label, fn):
            try:
                fn()
            except Exception as e:                       # noqa: BLE001
                log_line(f"fill_all·{label} failed: {type(e).__name__}: {e}")

        def _boards():
            fill_table(self.t_in, self._fmt_rows(d.get("board") or []), cols)
            fill_table(self.t_out, self._fmt_rows(d.get("board_out") or []), cols)
        _seg("辨识度榜", _boards)

        _seg("实时榜单", self.fill_watch)

        def _industry():
            ind = d.get("industry") or {}
            fill_table(self.t_ind_in, self._fmt_rows(ind.get("in") or []), cols)
            fill_table(self.t_ind_out, self._fmt_rows(ind.get("out") or []), cols)
        _seg("行业概况", _industry)

        def _events():
            ev = d.get("events") or []
            fill_table(self.t_ev, [[e.get("time", ""), e.get("type", ""),
                                    e.get("name", ""), e.get("desc", ""),
                                    f'{_fnum(e.get("strength"), 0):.1f}']
                                   for e in ev])
        _seg("实时事件", _events)

        _seg("题材聚簇", self._fill_themes)
        _seg("关键节点", self._fill_keynodes)
        _seg("异动雷达", self._fill_radar)

        def _range_ext():
            """「区间极值」（v2.0.10）：吸筹/出货前十，数据来自 market 统计。

            ★★ v3.0.3 修复一处既有缺陷（--shot 自检日志里抓到的）：
              症状：启动日志出现 `fill_all·区间极值 failed: KeyError: 'low'`，
                    面板静默留空（异常被 _seg 的 try/except 吞掉，只留日志）。
              根因：本块原先**硬取** `r["low"]` / `r["high"]`，而启动首屏的
                    `market` 可能来自**早期/本地回退**路径 —— 那条路径上的
                    range 项不一定带 low/high 字段（字段集随版本演进过）。
                    一旦缺字段，整块（含 range_sh）一起失败。
              修法：改用 `.get()` 容错，缺失时该行**跳过**而不是整块崩掉；
                    并把跳过的条数写进日志，避免"静默少几行"。
              ★ 为什么是"跳过"而不是"填 0"：0 在资金语境里是**合法值**
                （净额刚好为 0），用 0 冒充缺失会让用户误读成"确实没动过"，
                比少一行更糟。
            """
            m = d.get("market") or {}
            ra = m.get("range_ra") or []
            sh = m.get("range_sh") or []
            rows_ra, skip_ra = [], 0
            for i, r in enumerate(ra):
                if "low" not in r or "diff" not in r:
                    skip_ra += 1
                    continue
                rows_ra.append([i + 1, r.get("name", ""),
                                f'{_fnum(r.get("net"), 0):+.1f}',
                                f'{_fnum(r["low"], 0):+.1f}',
                                f'{_fnum(r["diff"], 0):+.1f}'])
            rows_sh, skip_sh = [], 0
            for i, r in enumerate(sh):
                if "high" not in r or "diff" not in r:
                    skip_sh += 1
                    continue
                rows_sh.append([i + 1, r.get("name", ""),
                                f'{_fnum(r.get("net"), 0):+.1f}',
                                f'{_fnum(r["high"], 0):+.1f}',
                                f'{_fnum(r["diff"], 0):+.1f}'])
            dcol = {4: lambda row: self._net_color(row, 4)}
            fill_table(self.t_ra, rows_ra, dcol)
            fill_table(self.t_sh, rows_sh, dcol)
            if skip_ra or skip_sh:
                log_line(f"range_ext: 跳过缺字段的行 ra={skip_ra} sh={skip_sh}"
                         f"（数据源字段不全，非功能故障）")

        _seg("区间极值", _range_ext)

    # ---------------------------------------------------------- 各区块填充
    def _fill_keynodes(self):
        """全天关键节点（时间倒序，口径见 salience.timeline_summary）。"""
        d = self.data
        fill_table(self.t_kn, [[r.get("time", ""), r.get("type", ""),
                                r.get("name", ""), r.get("desc", ""),
                                f'{_fnum(r.get("strength"), 0):.1f}']
                               for r in (d.get("timeline") or [])])

    def _fill_radar(self):
        """异动雷达两张表（问题十）。

        列：序号 / 题材 / 净额(亿) / 5分钟(亿) / z / 雷达分 / 识别原因
        着色：两个金额列跟随涨跌色；「雷达分」用强调色（它是本页的主排序依据）；
              「识别原因」用弱色 —— 它是说明性文字，不该和金额抢注意力。
        """
        d = self.data
        th = coloring(THEMES[self.theme_name])

        def _rows(rows):
            out = []
            for k, r in enumerate(rows or [], 1):
                out.append([
                    k,
                    str(r.get("name", "")),
                    f'{_fnum(r.get("net_yi"), 0):+.2f}',
                    f'{_fnum(r.get("d5_yi"), 0):+.2f}',
                    f'{_fnum(r.get("z"), 0):+.2f}',
                    f'{_fnum(r.get("anomaly"), 0):.3f}',
                    str(r.get("why", "")),
                ])
            return out

        accent = QColor(th["accent"])
        muted = QColor(th["muted"])
        colmap = {2: lambda row: self._net_color(row, 2),
                  3: lambda row: self._net_color(row, 3),
                  5: lambda row: accent,
                  6: lambda row: muted}
        fill_table(self.t_radar_in, _rows(d.get("radar_in")), colmap)
        fill_table(self.t_radar_out, _rows(d.get("radar_out")), colmap)

    def _fill_themes(self):
        """题材簇两张表（流向 / 流出分开，避免流出被绝对值挤到看不见）。"""
        d = self.data
        themes_rows = d.get("themes") or []
        thc = coloring(THEMES[self.theme_name])

        def _nm(x):
            if isinstance(x, dict):
                return str(x.get("name") or x.get("code") or "")
            return str(x)

        def _row(c, idx):
            mem = c.get("members") or []
            # 成员从 8 个放宽到 12 个：这一列是 Stretch，写满更利于看出主线构成
            names = "、".join(_nm(m) for m in mem[:12])
            try:
                tot = f'{float(c.get("total_yi", 0)):+.2f}'
            except Exception:                            # noqa: BLE001
                tot = str(c.get("total_yi", ""))
            # 龙头集中度：最大成员占整簇的比重 —— 高=单龙头带跟风，低=整条线共振
            try:
                sh = f'{float(c.get("lead_share", 0)) * 100:.0f}%'
            except Exception:                            # noqa: BLE001
                sh = "—"
            return [str(c.get("seed") or c.get("core") or f"簇{idx}"),
                    c.get("n_members", len(mem) if isinstance(mem, list) else ""),
                    tot, sh, names]

        rows_in, rows_out = [], []
        for i, c in enumerate(themes_rows, 1):
            if not isinstance(c, dict):
                rows_in.append([str(c)[:60], "", "", "", ""])
                continue
            try:
                neg = float(c.get("total_yi", 0)) < 0
            except Exception:                            # noqa: BLE001
                neg = False
            (rows_out if neg else rows_in).append(_row(c, i))
        # 各自按体量排序：流入从大到小，流出从最负开始
        # ★ 用 _fnum 兜底：total_yi 异常时 _row 会返回非数字串，
        #   直接 float() 会让整个填充流程崩掉（v1.0.5 加固）
        rows_in.sort(key=lambda r: -_fnum(r[2]))
        rows_out.sort(key=lambda r: _fnum(r[2]))

        # 龙头集中度上色：≥50% 说明这条线靠单一龙头撑着（看龙头即可），用弱色标注；
        # <50% 为整条线共振，用方向色 —— 后者才是真正的「板块级行情」。
        def _conc_col(row, base):
            try:
                strong = float(str(row[3]).rstrip("%")) >= 50
            except Exception:                            # noqa: BLE001
                strong = False
            return QColor(base if strong else thc["sub"])

        up, dn = thc["up"], thc["dn"]
        fill_table(self.t_th_in, rows_in,
                   {2: lambda row: QColor(up),
                    3: lambda row: _conc_col(row, up)})
        fill_table(self.t_th_out, rows_out,
                   {2: lambda row: QColor(dn),
                    3: lambda row: _conc_col(row, dn)})

    # ---------------------------------------------------------- 画图
    def render_charts(self, only_visible=False):
        """绘制全部图表。

        ★★ v2.0.1：新增 `only_visible` —— **按页延迟绘制**。
          首屏（on_data）只画当前可见页的图，其余记入 self._charts_dirty，
          等用户切到那一页时由 on_tab_changed 补画。
          为什么这么改、以及为什么"数据处理不是瓶颈"见 CHART_PAGE 的说明。
        """
        # ★ v2.0.3：画布尚未创建（窗口刚显示、materialize 还没跑）→ 先补建。
        #   两段式首屏的 early 数据可能先于 singleShot(120) 到达。
        if not getattr(self, "_charts_ready", False):
            self._materialize_charts()
        d = self.data
        if not d:
            return
        F = d.get("F")
        m = d.get("market") or {}
        times = d.get("times") or []

        cur_page = ""
        try:
            # ★ v2.1.0：取**母模块名** —— CHART_PAGE 的值已改为母模块名，
            #   用 _visible_page_names() 统一口径（内部已兼容总览这种无子页项）。
            cur_page, _cur_sub = self._visible_page_names()
        except Exception:                                # noqa: BLE001
            cur_page = ""
        self._charts_dirty = set()

        def _should(name, page):
            """该图是否现在就画？不画则登记为脏，切页时补。"""
            if only_visible and page != cur_page:
                self._charts_dirty.add(name)
                return False
            return True

        # 指数（F.index 每项为 [HH:MM, 点位, 成交量]）
        if _should("ch_index", "大盘"):
            idx = []
            try:
                raw = getattr(F, "index", None) or []
                for r in raw:
                    if isinstance(r, (list, tuple)) and len(r) >= 2:
                        idx.append((str(r[0]), float(r[1])))
            except Exception:
                idx = []
            # ★ 东财全A（v1.1.5）：与上证同图对比
            idx_all = []
            try:
                for r in (d.get("index_all") or []):
                    if isinstance(r, (list, tuple)) and len(r) >= 2:
                        idx_all.append((str(r[0]), float(r[1])))
            except Exception:                            # noqa: BLE001
                idx_all = []
            if idx or idx_all:
                # ★★ v3.0.4：优先增量（只 set_data + 换 fill，不重建整图）
                if not self.ch_index.try_update_series(idx, idx_all):
                    self.ch_index.plot(idx, idx_all)
            elif (d.get("source") or "live") == "backfill":
                # ★ 历史日指数分钟行情不可得（东财接口限制，2026-09-19 五轮实测）。
                #   明确告知原因，而不是留一片空白让人误以为程序坏了。
                self.ch_index.blank("历史日指数行情不可得\n（接口限制 · 资金流数据不受影响）")
            else:
                self.ch_index.blank()

        # ★ 流入 / 流出双向对比图（总览补齐流出的核心图）
        if _should("ch_flow", "大盘"):
            self.ch_flow.plot(m.get("top_in") or [], m.get("top_out") or [],
                              topn=10)

        if _should("ch_fan", "总览"):
            # ★ 资金曲线：流入 10 + 流出 10 = 20 条（v1.1.5 口径不变；
            #   v2.0.1 改为"体量 + 动向"混合选线，见 _fan_pick）
            series = []
            try:
                half = FAN_TOP_N // 2
                for direction, rows in (("in", m.get("in_list") or []),
                                        ("out", m.get("out_list") or [])):
                    for r in rows[:half]:
                        series.append({
                            "name": r.get("name", ""),
                            "series": r.get("series") or [],
                            "net": r.get("net", 0),
                            "direction": direction,
                            "times": times,
                        })
            except Exception:                            # noqa: BLE001
                series = []
            # 兜底：market 缺失时退回旧逻辑，至少还能画流入
            if not series:
                try:
                    for r in (d.get("board") or [])[:8]:
                        code = r.get("code")
                        if code is None or code not in F.codes:
                            continue
                        j = F.codes.index(code)
                        series.append({
                            "name": r["name"],
                            "series": [float(x) for x in F.V[:, j]],
                            "net": r.get("net_yi", 0),
                            "direction": "in",
                            "times": times,
                        })
                except Exception:                        # noqa: BLE001
                    series = []
            # ★★ v3.0.4：优先走**增量更新**（只 set_data，不重建 artist）。
            #   盘中高频刷新（1~10 秒）下曲线集合/名称不变，只是末端加点，
            #   增量路径把单图 20.9ms 压到亚毫秒级（见 try_update_series）。
            #   任何不满足条件的情况（首次绘制、曲线增删、列名变化、异常）
            #   都会返回 False，此处**自动退回**完整 plot() —— 行为与旧版一致。
            if not self.ch_fan.try_update_series(series):
                self.ch_fan.plot(series)

        if _should("ch_lead", "盘中复盘"):
            self.ch_lead.plot(d.get("leaders") or [])
        if _should("ch_stage", "盘中复盘"):
            self.ch_stage.plot(d.get("stages") or [])
        if _should("ch_theme", "板块题材"):
            try:
                self.ch_theme.plot(d.get("themes") or [])
            except Exception:                            # noqa: BLE001
                pass

        # ★ 悬停联动回调（v1.1.5）：曲线鼠标移动 → 总览页一行时点摘要。
        #   ★★ v2.0.6：时点数据面板已删除，联动主体改为**曲线右端标签**
        #     （FanChart._apply_hover_labels 内部自管，不经此回调）；
        #     这里只剩 lbl_fan_tip 一行摘要的更新。
        #   注意它**不属于绘制**，无论本页画没画都要设好 ——
        #   否则切回总览后曲线不再响应悬停（且不报错，很难发现）。
        self.ch_fan.hover_cb = self.on_fan_hover
        # 未悬停时的引导文案：与 _build_ui 里的初始文案保持一致
        self.lbl_fan_tip.setText("时点 ——，右端标签为收盘金额")

    # ------------------------------------------------------ 页签跳转（v2.1.0）
    def goto_page_name(self, group, sub_index):
        """跳到「某个母模块的第 sub_index 个子页」（★ v2.1.0 两级结构）。

        为什么单独抽成一个方法：菜单快捷键、以后可能的外部调用
        （如"从失败提示跳到相关页"）都需要它，而写两处必然会漂移。
        内部只走 **按名查表**（GROUP_POS / GROUP_INDEX），不写任何字面量下标 ——
        增删母模块或子页时不会静默错位（这是本项目反复踩到的脆性写法）。
        """
        try:
            gi = GROUP_INDEX[group]
            self.tabs.setCurrentIndex(gi)
            sub = self.group_tabs.get(group)
            if sub is not None and 0 <= sub_index < sub.count():
                sub.setCurrentIndex(sub_index)
        except Exception as e:                           # noqa: BLE001
            log_line(f"goto_page_name failed: {group}#{sub_index}: {e}")

    def goto_module(self, page_name):
        """按**子模块名**跳到它所在的母模块并选中该子页（v2.1.0）。

        用途：把旧的"按子模块名跳页"语义完整保留下来 —— 旧版可以直接
        setCurrentIndex(PAGE_INDEX['异动雷达'])，重构后「异动雷达」不再是顶层页，
        必须换算成「资金排行 → 第 1 个子页」。
        总览无子页，单独处理。
        """
        try:
            pos = GROUP_POS.get(page_name)
            if pos is None:
                # 总览等单页母模块：直接是顶层项
                self.tabs.setCurrentIndex(GROUP_INDEX.get(page_name, 0))
                return
            self.tabs.setCurrentIndex(pos[0])
            sub = self.group_tabs.get(PAGE_GROUP.get(page_name, ""))
            if sub is not None and pos[1] < sub.count():
                sub.setCurrentIndex(pos[1])
        except Exception as e:                           # noqa: BLE001
            log_line(f"goto_module failed: {page_name}: {e}")

    def _visible_page_names(self):
        """当前**真正可见**的顶层 + 其子页，返回 (母模块名, 子模块名)。

        供 render_charts 的按页延迟绘制判定与 --shot 断言使用。
        子页取"当前选中的那个"——母模块内可能停在任意子页，
        只有它才是用户实际看得见的内容。
        """
        try:
            gi = self.tabs.currentIndex()
            gname = self.tabs.tabText(gi)
        except Exception:                                # noqa: BLE001
            return "", ""
        sub = self.group_tabs.get(gname)
        if sub is None:
            return gname, gname                          # 总览：母=子
        try:
            return gname, sub.tabText(sub.currentIndex())
        except Exception:                                # noqa: BLE001
            return gname, gname

    def on_tab_changed(self, idx):
        """切页时补画该页的图表（★ v2.0.1 按页延迟绘制 / v2.1.0 适配两级）。

        只在确有脏图时才动作 —— 每次切页都重画会让切页变卡，
        而"这一页的图本来就是最新的"时重画毫无收益。

        ★★ v2.1.0：判定口径由"顶层页名"改为"**该顶层下当前可见的子模块**"。
          原因：CHART_PAGE 仍按子模块陈述图表归属，但表里的图现在挂在
          母模块内部 —— 切母模块、或在母模块内切子页，都可能让某张图
          第一次进入可见状态。两个信号都连到本函数，这里统一按
          `_visible_page_names()` 重新判定，避免"两个信号各判一半"的错位。
        """
        dirty = getattr(self, "_charts_dirty", None)
        if not dirty:
            return
        gname, sname = self._visible_page_names()
        # 图表归属按**母模块**判定（CHART_PAGE 的值已改为母模块名）：
        # 只要该母模块可见，它内部某子页上的图就值得画。
        if any(p == gname for _n, p in CHART_PAGE if _n in dirty):
            # 只画当前可见的，其余继续留在 dirty 里等下一次切页
            self.render_charts(only_visible=True)
            log_line(f"lazy render · group={gname} page={sname} "
                     f"still_dirty={sorted(getattr(self, '_charts_dirty', []))}")
        # 其它页（表格页）没有待画图表 —— **不记日志**：
        # 切到无图页是绝大多数情况，每次记一条只会把日志淹掉。

    # ---------------------------------------------------------- 悬停联动
    def on_fan_hover(self, i, hm):
        """鼠标停在曲线的第 i 分钟 → 总览页一行时点说明（v2.0.10）。

        ★★ v2.0.10（用户要求）：批注精简 —— 只保留这一句时点说明，
          其余静态描述（配色/量程/横轴/图例/交互）已全部删除。
          i = -1 表示鼠标移出 → 恢复默认句。
        """
        times = (self.data or {}).get("times") or []
        if i is not None and i >= 0:
            hm_now = hm or (times[i] if 0 <= i < len(times) else "—")
            tip = f"时点 {hm_now}，右端标签已切换为该分钟数据，移出恢复收盘值"
        else:
            last = times[-1] if times else "—"
            tip = f"时点 {last}，右端标签为收盘金额"
        try:
            self.lbl_fan_tip.setText(tip)
        except Exception:                                # noqa: BLE001
            pass

    def show_status(self):
        """生成「运行状态与说明」长文，缓存到 self.status_text。

        ★ v1.2.1：原先它把文本直接写进「数据状态」页的 QTextBrowser。
          该页已删除，改为**只生成文本并缓存** ——
          查看入口移到 帮助菜单 →「运行状态」（弹只读对话框，内容完全一致）。
          这样删掉的只是一个常驻页签，诊断信息一条没少。
        """
        d = self.data
        F = d.get("F")
        src = d.get("source") or "live"
        src_cn = "合成（日级真实总额 + 分钟形态合成）" if src == "backfill" else "实盘"
        # ---- 本地存储盘点（v1.1.0）
        try:
            import store
            _ddir = current_data_dir()
            local = store.list_local_panels(_ddir)
            n_bf = sum(1 for t in local
                       if store.panel_source(t, _ddir) == "backfill")
            n_live = len(local) - n_bf
        except Exception:                                # noqa: BLE001
            local, n_bf, n_live = [], 0, 0
        # ---- 归档索引摘要（v1.1.5，移植自 HTML 版滚动归档）
        try:
            import archive as _arc
            arc_note = _arc.summary(current_data_dir())
        except Exception:                                # noqa: BLE001
            arc_note = "（归档模块未就绪）"
        cal = self.calendar_days or []
        lines = [
            f"资金流向 · 纯原生桌面版 v{VERSION}",
            "=" * 52,
            "",
            "【实现方式】",
            "  GUI 框架   : PySide6 (Qt Widgets) —— 原生控件",
            "  图表绘制   : matplotlib (QtAgg 后端) —— 原生画布",
            "  浏览器依赖 : 无（不含 WebView / HTML / CSS / JS）",
            "",
            "【数据来源】",
            "  东方财富公开接口",
            "  板块清单  /api/qt/clist/get",
            "  分钟资金流 /api/qt/stock/fflow/kline/get?klt=1（仅当日）",
            "  日级资金流 /api/qt/stock/fflow/daykline/get（历史补全锚点）",
            "",
            "【历史存储与交易日历】（v1.1.0 起，v1.1.5 对齐 HTML 版归档）",
            f"  交易日历   : {len(cal)} 天"
            + (f"（{_pretty_date(cal[0])} ~ {_pretty_date(cal[-1])}）" if cal else "（未就绪）"),
            "  回看窗口   : 最近 30 个交易日（超出窗口的最早数据自动删除）",
            f"  本地已存   : {len(local)} 天（实盘 {n_live} / 合成 {n_bf}）",
            f"  收盘归档   : {arc_note}",
            "  归档机制   : 每个交易日一条记录，滚动 30 天；新的一天挤掉最早的一天。",
            "              回看下拉只列出**本地真正有数据**的日期。",
            "  合成说明   : 历史日收盘总额为真实数据，分钟曲线为形态合成；",
            "              历史日指数分钟行情接口不可得，总览页该图显示占位。",
            "",
            "【本次加载】",
            f"  板块数     : {d.get('n_sectors', 0)}",
            f"  分钟点数   : {d.get('n_minutes', 0)}",
            f"  数据日期   : {_pretty_date(d.get('date_tag', ''))}（{src_cn}）"
            "（缓存按交易日隔离，跨日自动重抓）",
            f"  时间范围   : {(d.get('times') or ['?'])[0]} ~ {(d.get('times') or ['?'])[-1]}",
            f"  榜单条数   : {len(d.get('board') or [])}",
            f"  事件数     : {len(d.get('events') or [])}",
            f"  题材簇     : 流入 {d.get('n_theme_in', 0)} + 流出 {d.get('n_theme_out', 0)}"
            f"（合计 {len(d.get('themes') or [])}）",
            f"  关键节点   : {len(d.get('timeline') or [])}",
            "  实时榜单   : "
            + (lambda st: (f"累计 {st[0]} 个（流入 {st[1]} / 流出 {st[2]}）"
                           f"· 异动追加 {st[3]} 个 · 已刷新 "
                           f"{d.get('watch_rounds', 0)} 轮"
                           if st and st[0] else "（空）"))(
                list(d.get("watch_stats") or [])[:4] or None),
            f"  榜单机制   : 开盘按辨识度双向各取 {WATCH_TOP_N} 个 → "
            f"盘中盘口异动立即追加 → 只增不减",
            f"  榜单存档   : watchlist_YYYYMMDD.json（按交易日隔离，"
            f"随 30 日窗口滚动清理）",
            "",
            "【缓存位置】",
            f"  {app_data_dir()}",
            "",
            "【日志文件】",
            f"  {app_log_path()}",
            "",
            "【算法层（复用 core/salience.py）】",
            "  辨识度 = 净额排名 + 速度 + 排名跃迁 + 持续性 + 异动 z 的加权",
            "  事件   = 加速流入 / 新晋 / 资金回流 / 失血 / 跳水 …",
            "  聚类   = 龙头种子贪心（不用 union-find，避免单链串簇）",
            "           ★ 流入池 / 流出池各自独立聚类，两侧都出主线",
            "  集中度 = 最大成员占整簇比重，高 = 单龙头带跟风、低 = 全链共振",
            "",
            "【文字叙述】",
        ]
        nar = d.get("narrate")
        if isinstance(nar, str) and nar.strip():
            lines += ["  " + x for x in nar.splitlines()[:40]]
        else:
            lines.append("  （无）")
        err = d.get("themes_err")
        if err:
            lines += ["", "【题材簇异常】", "  " + err]
        # ★ v1.2.1：归档落盘失败的持久警告（见 on_data 里的归档段）
        _aw = getattr(self, "_archive_warn", "")
        if _aw:
            lines += ["", "【归档异常】", "  " + _aw]
        # ★ 只缓存文本，不直接写 UI（v1.2.1：数据状态页已删除）
        self.status_text = "\n".join(lines)

    def show_status_dialog(self):
        """帮助菜单入口：把运行状态以只读对话框弹出（替代已删除的数据状态页）。"""
        txt = getattr(self, "status_text", "") or "（尚未加载数据）"
        dlg = QDialog(self)
        dlg.setWindowTitle("运行状态与说明")
        dlg.resize(820, 640)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        br = QTextBrowser()
        br.setFont(QFont("Microsoft YaHei", 10))
        br.setPlainText(txt)
        lay.addWidget(br, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton("关闭")
        btn.clicked.connect(dlg.accept)
        row.addWidget(btn)
        lay.addLayout(row)
        dlg.exec()


def preflight():
    """启动自检：确认真实依赖都在。

    历史上踩过的坑：core/ 是通过 datas= 塞进 exe 的，PyInstaller 不做静态分析，
    于是 core/flowdata.py 里的 `import requests` 没被收集，打包后必然崩。
    现在 flowdata 已改为纯标准库，这里再兜一道底，出问题也给出可读提示。
    """
    problems = []
    stdlib_mods = ["json", "os", "time", "threading", "urllib.request",
                   "concurrent.futures", "traceback", "datetime"]
    for m in stdlib_mods:
        try:
            __import__(m)
        except Exception as e:                        # noqa: BLE001
            problems.append(f"标准库 {m}: {e}")

    # 正常启动不在窗口出现前导入 matplotlib：这是最重的 GUI 依赖，
    # 实际加载由窗口显示后的 _materialize_charts() 完成。自检/诊断模式仍做严格检查。
    runtime_mods = ["numpy", "PySide6"]
    if "--shot" in sys.argv or os.environ.get("FLOWLIVE_STRICT_PREFLIGHT") == "1":
        runtime_mods.append("matplotlib")
    for m in runtime_mods:
        try:
            __import__(m)
        except Exception as e:                        # noqa: BLE001
            problems.append(f"{m}: {e}")

    # core/ 模块（打包后位于 _MEIPASS/core）；
    # v1.1.0 新增 tradecal/store/backfill；v1.1.5 新增 archive/leader
    for m in ["flowdata", "salience", "universe", "tradecal", "store",
              "backfill", "archive", "leader", "opensnap"]:
        try:
            __import__(m)
        except Exception as e:                        # noqa: BLE001
            problems.append(f"core/{m}: {e}")

    # ★ v2.0.1：图标是可选项，**缺失不算故障**（程序照常跑，只是没图标），
    #   所以只记日志、不进 problems —— 否则打包时漏一个 ico 就会让程序起不来。
    try:
        if app_icon_path() is None:
            log_line(f"WARN app icon missing; looked near {BASE}")
    except Exception:                                 # noqa: BLE001
        pass

    if problems:
        log_line("PREFLIGHT FAILED:\n  " + "\n  ".join(problems))
    else:
        log_line(f"preflight ok · v{VERSION} · frozen={getattr(sys, 'frozen', False)}"
                 f" · BASE={BASE}")
    # ★ v2.0.1 诊断通道（**按需开启**）：把「路径判定」的结果打到 stdout。
    #   为什么需要：日志写在哪取决于 portable_root() 的判定，而一旦判定失败
    #   或写不进去，日志会静默消失 —— 在"所有落盘必须在本盘"的约束下，
    #   这是必须能一眼看穿的事（只依赖日志本身是看不到的：日志写错地方=没有日志）。
    #   默认关闭（正常使用零开销），`--shot` 自检或 FLOWLIVE_DIAG=1 时输出。
    if "--shot" in sys.argv or os.environ.get("FLOWLIVE_DIAG") == "1":
        try:
            print(f"[diag] frozen={getattr(sys, 'frozen', False)}", flush=True)
            print(f"[diag] sys.executable={sys.executable!r}", flush=True)
            print(f"[diag] BASE={BASE}", flush=True)
            print(f"[diag] portable_root={portable_root()!r}", flush=True)
            print(f"[diag] app_home={app_home()!r}", flush=True)
            print(f"[diag] log={app_log_path()!r}", flush=True)
            print(f"[diag] data={app_data_dir()!r}", flush=True)
            print(f"[diag] shots={shots_dir()!r}", flush=True)
            # ★ 写一条真实记录，验证日志路径**确实可写** ——
            #   这是"日志是不是真的落在这里"的唯一可靠证据。
            log_line("diag self-test (this line proves the log path is writable)")
            lp = app_log_path()
            try:
                with open(lp, "a", encoding="utf-8") as _f:
                    _f.write("")
                print("[diag] log write test=OK", flush=True)
            except Exception as _e:                      # noqa: BLE001
                print(f"[diag] log write test=FAILED "
                      f"{type(_e).__name__}: {_e}", flush=True)
        except Exception as _e:                          # noqa: BLE001
            print(f"[diag] failed: {type(_e).__name__}: {_e}", flush=True)
    return problems


_LOCK_FILE = None       # 模块级持有，保证进程存活期间锁不释放


def acquire_single_instance():
    """单实例锁：避免重复双击开出多份（每份都会抓 1000 个板块）。

    Windows 上用 msvcrt 锁定字节；失败说明已有实例在跑。
    注意：返回 False 只是"已有实例"，不是错误 —— 允许 --shot 自检模式绕过。
    """
    global _LOCK_FILE
    # ★ v1.2.6：锁文件跟随 app_home() —— 便携部署时就是 exe 同级的 app.lock
    #   （D:\exe\app.lock）。与数据/日志同源，整个文件夹搬走才不会锁错地方。
    d = app_home()
    p = os.path.join(d, "app.lock")
    try:
        f = open(p, "a+b")
        try:
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except Exception:                                # noqa: BLE001
            f.close()
            return False
        f.seek(0)
        f.truncate()
        f.write(f"{os.getpid()}\n".encode("ascii"))
        f.flush()
        _LOCK_FILE = f                                    # 持住，别关
        return True
    except Exception:                                     # noqa: BLE001
        return True                                       # 锁机制本身失败就不拦


def _activate_existing_window():
    """把已运行的实例窗口拉到前台（v2.0.3，Windows 专用，失败返回 False）。

    ★ 为什么需要：窗口出现前有 1~2s 的 import 空窗，用户「没反应」就会
      连点 —— v2.0.2b 的日志实测 3 秒内 4 个进程各跑了一遍 preflight。
      锁提前后，后来者能瞬间到这里；与其弹一个「已在运行中」的模态框
      （用户还得再点一次关），不如直接把已有窗口调到前面 —— 对
      「我就是要打开它」的用户，这正是他想要的结果。
    """
    try:
        import ctypes
        hwnd = ctypes.windll.user32.FindWindowW(
            None, f"资金流向 · 原生版 v{VERSION}")
        if not hwnd:
            return False
        ctypes.windll.user32.ShowWindow(hwnd, 9)          # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        return True
    except Exception:                                     # noqa: BLE001
        return False


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("资金流向")
    app.setStyle("Fusion")
    # 全局现代化样式（浅色为默认主题）
    app.setFont(QFont("Microsoft YaHei", 9))
    app.setStyleSheet(build_qss(THEMES["light"]))

    # ★★ v2.0.1：补齐运行时图标（用户反馈「exe 缺软件图标」）。
    #   三件事缺一不可，顺序也有讲究：
    #     ① 先声明 AppUserModelID —— 必须在**创建任何窗口之前**，
    #        否则 Windows 已经把进程归到默认分组，后面再设也不改任务栏图标；
    #     ② 再给 QApplication 设图标 —— 覆盖所有子窗口与对话框；
    #     ③ 主窗口在 __init__ 里还会显式设一次（部分窗口管理器只认窗口级）。
    _set_app_user_model_id()
    _ico = app_icon()
    if not _ico.isNull():
        app.setWindowIcon(_ico)
    else:
        # 换机器/裁剪部署时 ico 可能缺失 —— 记日志但不影响使用
        log_line(f"app icon not found (looked near {BASE})")

    # --shot 是无人值守自检，允许多开，不参与单实例约束
    self_check = "--shot" in sys.argv

    # ★★ v2.0.3：单实例锁**提前**到 preflight 之前。
    #   旧版顺序是 preflight → 锁，于是每个重复启动的进程都完整跑一遍
    #   preflight 再弹「已在运行中」—— v2.0.2b 日志里 3 秒 4 条 preflight ok
    #   就是这么来的。preflight 唯一的产出是"坏环境时给可读报错"，
    #   而重复进程根本活不到用上它；先锁、后检，重复进程瞬间退出。
    if not self_check and not acquire_single_instance():
        log_line("second instance blocked by single-instance lock")
        if _activate_existing_window():
            return 0
        QMessageBox.information(
            None, "资金流向",
            "程序已在运行中。\n\n请切换到已打开的窗口；"
            "若确实需要重开，请先关闭现有窗口。")
        return 0

    probs = preflight()
    if probs:
        QMessageBox.critical(
            None, "启动自检失败",
            "程序组件不完整，无法运行：\n\n" + "\n".join(probs[:6]) +
            f"\n\n日志：{app_log_path()}")
        return 2

    w = MainWindow()
    w.show()
    # ★ v2.0.1：首屏链路的第一段耗时（进程启动 → 窗口可见）。
    #   与 on_data 里的 since_boot 配对，就能看出"启动慢"到底慢在哪一段：
    #     · 这一段大 → 是 import / 界面构建的成本（Python + PySide6 + matplotlib）
    #     · on_data 那段大 → 是抓取 / 计算 / 绘图的成本
    log_line(f"boot · window shown at {(time.perf_counter() - T_BOOT) * 1000:.0f}ms "
             f"(import+UI build)")

    # ★★ v3.0.6：把**当前数据源**在启动时就说清楚。
    #   为什么必须显式记录：换了源之后数据能力是**不同**的（例如腾讯源没有
    #   分钟级资金流历史）。如果日志里不写，事后排查"为什么今天曲线是断的"
    #   会完全找不到线索。同时把降级能力一并列出。
    try:
        import source as _src
        _p, _how = _src.resolve()
        log_line("source · " + _src.status())
        # ★ 暗门只写进日志、**不做任何界面提示**（用户要求"默认不激活"）：
        #   平时完全无感；真需要时（东财被封）看一眼日志就知道怎么切。
        if _p == _src.EASTMONEY:
            log_line(
                f"source · 暗门（默认不激活）：设 {_src.SOURCE_ENV}=tencent 或 "
                f"auto（环境变量 / data/{_src.SOURCE_FILE}），"
                f"或按 Ctrl+Shift+Alt+S 显示「工具→数据源…」后选择")
        else:
            log_line(f"source · ⚠ 当前使用替代源 {_p}：{_src.CAP_NOTES.get(_p, '')}")
    except Exception as e:                               # noqa: BLE001
        log_line(f"source · 数据源层不可用：{type(e).__name__}: {e}")

    # --shot <前缀>：无人值守自检（等数据 → 逐页截图 → 退出）
    #   可选 --date YYYYMMDD：先切到该历史交易日再截图（v1.1.0 回看验证用）
    if "--shot" in sys.argv:
        i = sys.argv.index("--shot")
        prefix = sys.argv[i + 1] if i + 1 < len(sys.argv) else "shot"
        want_date = None
        if "--date" in sys.argv:
            j = sys.argv.index("--date")
            if j + 1 < len(sys.argv):
                want_date = sys.argv[j + 1]
        st = {"n": 0, "shots": 0, "hist_asked": False, "hist_t0": 0}

        # ★ 截图尺寸必须钉死，否则「源码版 vs exe 版逐字节比对」会失效。
        #   踩过的坑：show() 时窗口会被**桌面可用区域**夹住
        #   （本机屏幕 1536x864、任务栏占掉一条 → 请求 1480x940 被压成 1480x844），
        #   于是同一份代码在不同机器/不同时间跑出来的截图尺寸不一致
        #   （曾出现 1850x1175 与 1850x1055 两种）。
        #   这里在 show() 之后**再 resize 一次**把尺寸抢回来；抢不回来就明确报错，
        #   避免拿着尺寸不同的截图去做"逐字节一致"的假结论。
        #   注：offscreen 平台虽然天然无此约束，但会丢失中文字形（渲染成豆腐块），
        #   所以不能用 offscreen，只能走正常平台 + show() 后二次 resize。
        _shot_w, _shot_h = 1480, 940
        w.resize(_shot_w, _shot_h)
        for _ in range(4):
            app.processEvents(); time.sleep(.1)
        if (w.width(), w.height()) != (_shot_w, _shot_h):
            print(f"  [警告] 窗口尺寸被平台夹成 {w.width()}x{w.height()}，"
                  f"期望 {_shot_w}x{_shot_h} —— 截图尺寸可能不可复现", flush=True)

        def tick():
            st["n"] += 1
            if st["shots"] >= w.tabs.count() or st["n"] > 240:
                print("自检完成"); w.close(); return
            if not w.data:
                QTimer.singleShot(1000, tick); return
            # --date：实盘先加载完毕后切到指定历史日，到位再开拍
            if want_date and not st["hist_asked"]:
                st["hist_asked"] = True
                st["hist_t0"] = st["n"]
                w.load_history(want_date)
                QTimer.singleShot(1000, tick); return
            if (want_date and w.data.get("date_tag") != want_date
                    and st["n"] - st["hist_t0"] < 45):
                QTimer.singleShot(1000, tick); return
            outdir = shots_dir()
            k = st["shots"]
            w.tabs.setCurrentIndex(k)
            app.processEvents(); time.sleep(.45); app.processEvents()
            name = w.tabs.tabText(k)
            p = os.path.join(outdir, f"{prefix}_{k}_{name}.png")
            w.grab().save(p)
            print(f"  截图 {os.path.basename(p)}  {os.path.getsize(p):,} B", flush=True)
            # ★★ v1.2.5：截「关键节点」页时把**表格里的真实字符串**一并导出。
            #   【为什么需要】数据层排好序 ≠ 屏幕上有序。中间还隔着
            #     on_data → fill_table → QTableWidget 三层，任何一层做了
            #     二次排序/截断/缓存，用户看到的仍然是乱的 —— 而用户看的是**屏幕**。
            #     只断言数据层，等于漏掉整条显示链路。
            #   【为什么不读截图像素】本机没有 OCR 依赖（tesseract 未安装），
            #     而"自己写模板匹配认数字"既不可靠又难维护。
            #     直接问 Qt 要 QTableWidgetItem 的 text() 才是**同一份字符串**，
            #     零依赖、零误差：表格显示什么，这里就导出什么。
            if name == "关键节点" and getattr(w, "t_kn", None) is not None:
                try:
                    tk = w.t_kn
                    rows_dump = []
                    for r in range(tk.rowCount()):
                        rows_dump.append([
                            (tk.item(r, c).text() if tk.item(r, c) else "")
                            for c in range(tk.columnCount())])
                    dp = os.path.join(outdir, f"{prefix}_{k}_{name}.table.txt")
                    with open(dp, "w", encoding="utf-8") as fh:
                        fh.write(f"# 关键节点页 QTableWidget 实际内容\n")
                        fh.write(f"# rows={tk.rowCount()} cols={tk.columnCount()}\n")
                        for row in rows_dump:
                            fh.write("\t".join(row) + "\n")
                    print(f"  表格导出 {os.path.basename(dp)}  "
                          f"{len(rows_dump)} 行", flush=True)
                except Exception as _e:                            # noqa: BLE001
                    print(f"  [警告] 表格导出失败：{_e}", flush=True)
            # ★★ v2.0.1：把当前页**所有具名表格**也导出一份。
            #   与上面关键节点那条同一目的（问 Qt 要字符串，不读像素），
            #   只是从"只覆盖一页"扩到"每一页都覆盖" —— 本次改动涉及
            #   实时榜单四个模块、实时事件、总览拆分等多处，都必须能验证到屏幕。
            #   用 objectName 定位，不依赖 findChildren 的遍历顺序。
            try:
                from PySide6.QtWidgets import QTableWidget as _QTW
                for tb in w.findChildren(_QTW):
                    on = tb.objectName()
                    if not on or not tb.isVisible() or tb.rowCount() == 0:
                        continue
                    rows_dump = []
                    for r in range(tb.rowCount()):
                        rows_dump.append([
                            (tb.item(r, c).text() if tb.item(r, c) else "")
                            for c in range(tb.columnCount())])
                    dp = os.path.join(outdir, f"{prefix}_{k}_{name}.{on}.txt")
                    with open(dp, "w", encoding="utf-8") as fh:
                        fh.write(f"# {name} · {on} · QTableWidget 实际内容\n")
                        fh.write(f"# rows={tb.rowCount()} cols={tb.columnCount()}\n")
                        for row in rows_dump:
                            fh.write("\t".join(row) + "\n")
                    print(f"  表格导出 {os.path.basename(dp)}  "
                          f"{len(rows_dump)} 行", flush=True)
            except Exception as _e2:                                # noqa: BLE001
                print(f"  [警告] 具名表格导出失败：{_e2}", flush=True)
            st["shots"] += 1
            QTimer.singleShot(600, tick)

        QTimer.singleShot(2000, tick)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
