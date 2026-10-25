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
import os, sys, time, json, traceback
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(BASE, "core")
if os.path.isdir(CORE):
    sys.path.insert(0, CORE)

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize
from PySide6.QtGui import QAction, QIcon, QFont, QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QLabel, QStatusBar,
    QToolBar, QSplitter, QProgressBar, QMessageBox, QFrame, QSizePolicy,
    QAbstractItemView, QTextBrowser, QComboBox, QDialog, QPushButton,
)

import matplotlib
matplotlib.use("QtAgg")                      # 原生 Qt 画布
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.transforms import Bbox       # 悬停局部重绘的取景框（v1.2.2）

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
matplotlib.rcParams["axes.unicode_minus"] = False

VERSION = "2.0.2"

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
    ("10 秒（理想档 · 需网络稳定）", 10 * 1000),
    ("30 秒（推荐）", 30 * 1000),
    ("1 分钟", 60 * 1000),
    ("5 分钟（省流量）", 5 * 60 * 1000),
]
AUTO_MS_DEFAULT = 30 * 1000          # 用户明确要求的默认值

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
REFRESH_MIN_GAP_SEC = 3.0


# ==================================================================== 布局常量
# ★ v1.1.6：把间距/边距收成常量，避免"改一处漏一处"。
#   起因：总览页写的是 setSpacing(7)，其余 8 个页面写的是 setSpacing(8) ——
#   同一套界面里两种节奏，切标签页时能看出细微差别。根因是当初逐页手写数字。
#   现在统一从这里取，要调也只调一处。
PAD_PAGE = (8, 6, 8, 8)      # 页面级 layout 外边距（左,上,右,下）
GAP_PAGE = 8                 # 页面内主要区块之间的间距
GAP_CARD = 7                 # 同排卡片之间的间距（与卡片内边距视觉对齐）

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

    /* ---------------- 卡片容器 ---------------- */
    QFrame#card {{
        background: {th['bg']};
        border: 1px solid {th['border']};
        border-radius: 10px;
    }}
    QFrame#kpi {{
        background: {th['card']};
        border: 1px solid {th['border']};
        border-radius: 10px;
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
    QLabel#kpiTitle {{ color: {th['sub']}; font-size: 11.5px; }}
    QLabel#kpiValue {{ font-size: 21px; font-weight: 700; }}
    QLabel#kpiSub {{ color: {th['muted']}; font-size: 10.5px; }}

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
        border-top: 1px solid {th['border']};
        color: {th['sub']};
        padding: 3px 8px;
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
                times = json.load(f).get("times") or []
            return flowdata.spot_gap_minutes(times)
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
        """是否处于 A 股交易时段（含集合竞价与收盘缓冲）。"""
        now = datetime.now()
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

    def run(self):
        try:
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
                    days, approx = tradecal.get_trading_days(refresh=True)
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

                if plan == "full":
                    log_line(f"full fetch · {why}")
                    self.progress.emit("抓取全量分钟数据（约 1000 个板块）…")
                    try:
                        flowdata.build_panel(self.tag, workers=24,
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
                        #   按"历史回看"语义处理：只读、不生长实时榜单池。
                        self.local_only = True
                        log_line(f"use local fallback · {why}")
                        self.progress.emit(
                            f"使用本地 {self.tag} 的数据（非今日实时）…")
                    else:
                        log_line(f"use local cache · {why}")
                        self.progress.emit("读取本地缓存…")

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
                if not self.local_only:
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
            self.done.emit(out)
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")


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
    rows = []
    for j in idxs:
        code = F.codes[j]
        series = [float(x) for x in F.V[:, j]]
        rows.append({
            "code": code,
            "name": F.names.get(code, code),
            "kind": F.kinds.get(code, ""),
            "net": float(vals[j]),
            "d5": float(d5row[j]) if j < len(d5row) else 0.0,
            "series": series,
        })

    ins = sorted([r for r in rows if r["net"] > 0],
                 key=lambda r: -r["net"])
    outs = sorted([r for r in rows if r["net"] < 0],
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

    return {
        "n_in": len(ins), "n_out": len(outs),
        "sum_in": round(sum_in, 2), "sum_out": round(sum_out, 2),
        "net_total": round(sum_in + sum_out, 2),
        "top_in": ins[:10], "top_out": outs[:10],
        # ★ 资金曲线选线（v2.0.1 改为"体量 + 动向"混合，见 _fan_pick）：
        #   每个方向 FAN_TOP_N//2 条，两条腿合计 20 条
        #   （沿用 v1.1.5 用户要求「不少于 20 个板块概念标的」的口径）。
        "in_list": _fan_pick(ins, FAN_TOP_N),
        "out_list": _fan_pick(outs, FAN_TOP_N),
        "index_pct": index_pct,
        "n_total": len(rows),
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


# ==================================================================== 图表基类
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


# ---------------------------------------------------------------- 资金曲线
# 曲线配色序列（参考 HTML 版 UP_SEQ / DN_SEQ）：
#   20 条线用同一个红/绿会糊成一片，改成像 HTML 版那样按序取不同色相 ——
#   同属"涨"的色系内做区分，既保持红涨绿跌的语义，又能一眼追踪单条线。
#   ★ v1.2.1：配色方向已固定为红涨绿跌，这两个序列不再需要对调。
#     （v1.1.5 曾随方向翻转，当时的教训保留在 seq_for 的注释里：
#      "只让 th['up']/th['dn'] 互换、却把色序写死" 会造成
#      表格与曲线配色不一致 —— 现在没有方向开关，这条坑自然消失。）
UP_SEQ = ["#ff4d5e", "#ff7a45", "#f7b731", "#d94f8a", "#c0392b",
          "#e8590c", "#f06595", "#ffa94d", "#e64980", "#ff8787"]
DN_SEQ = ["#22c39a", "#2f8fd8", "#5b6dd8", "#0fa3a3", "#1f9d6a",
          "#3fa9a0", "#2b8a8a", "#4c6ef5", "#186d78", "#12b886"]


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
# 图例排版（v1.1.6）：图例从绘图区内部移到**轴下方**，必须知道每一行多高、
# 一行能放几列，才能算出要给底部留多少空间（见 FanChart._reserve_bottom）。
#   FAN_LEG_FS    图例字号：20 条 × 长板块名要一行铺满约 900px，
#                 6.4pt 是实测能塞进 1480px 窗口又不至于读不清的档位。
#   FAN_LEG_NCOL  兜底列数：窗口窄到一行放不下时按这个列数折行。
FAN_LEG_FS = 6.4
FAN_LEG_NCOL = 7

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
PAGE_NAMES = ["总览", "市场全景", "异动雷达", "辨识度榜", "行业概况", "实时事件",
              "题材簇", "主角更替", "阶段对比", "关键节点", "实时榜单"]
PAGE_INDEX = {nm: i for i, nm in enumerate(PAGE_NAMES)}

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
CHART_PAGE = (
    ("ch_fan", "总览"),
    ("ch_index", "市场全景"),
    ("ch_flow", "市场全景"),
    ("ch_theme", "题材簇"),
    ("ch_lead", "主角更替"),
    ("ch_stage", "阶段对比"),
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
      xpos   —— 长度 n 的横坐标列表，**严格单调递增**（保证不画成折返线）
      mapped —— True 表示按分钟序号定位；False 表示后退回数组下标

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
    if len(raw) < n:                       # times 比曲线短：尾部按"续一格"补
        raw = raw + [None] * (n - len(raw))
    if not any(v is not None for v in raw):
        return list(range(n)), False       # 一个都认不出来 → 退回下标

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


def _fill_gaps(series):
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
    bad = ~np.isfinite(arr)
    if not bad.any():
        return [float(x) for x in arr]                   # 没缺口：原样返回
    idx = np.arange(arr.size, dtype=float)
    good = np.isfinite(arr)
    if good.any():
        # np.interp 天然是「最近的前后两点之间线性插值」：
        #   中间的空洞被两侧真实值夹着补上，两端延伸用最近端点值 ——
        #   正是我们要的「前向填充 + 开头回填」两步合一，且一次算完。
        arr = np.interp(idx, idx[good], arr[good])
    else:
        arr = np.zeros(arr.size, dtype=float)
    return [float(x) for x in arr]


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
      图例条目也按同向着色：流入着涨色、流出着跌色。
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
        # matplotlib 的鼠标事件：进入/离开/移动/按下
        self.mpl_connect("motion_notify_event", self._on_motion)
        self.mpl_connect("axes_leave_event", self._on_leave)
        self.setMouseTracking(True)

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
            self.draw()                          # 内部会作废快照，故紧随其后重建
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
        self._vline = None
        self._hover_px = None
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
                #   曲线长度不足时按已有的点画到哪算哪（不补假数据）。
                xs = xpos[:len(s)] if len(xpos) >= len(s) else range(len(s))
                # 按序取色（HTML 版做法）：流入走暖色族、流出走冷色族
                c = seq[seq_i % len(seq)]
                ln, = ax.plot(
                    xs, s, color=c,
                    linewidth=1.7 if is_up else 1.5,
                    linestyle="-",
                    alpha=.93,
                    label=f'{d["name"]} {d["net"]:+.1f}')
                handles.append(ln)
            return handles

        h_in = _draw(ins, True)
        h_out = _draw(outs, False)

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
        ax.set_xticklabels(tick_lab, fontsize=7.6)
        ax.set_xlim(0, TRADING_MINUTES - 1)       # 刻度可能撑动视野，再钉一次
        ax.grid(True, color=th["grid"], linewidth=.8, axis="x", alpha=.55)

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

        # 午休分界：11:30(119) 与 13:01(120) 在轴上是相邻两格，光看刻度看不出
        # "中间断了两小时"。画一条淡色分界竖线，读者才知道此处是跳空的。
        ax.axvline(119.5, color=th["sub"], linewidth=.9, linestyle=":",
                   alpha=.5)
        ax.text(119.5, ax.get_ylim()[0], " 午休", fontsize=6.8,
                color=th["sub"], alpha=.75, va="bottom", ha="left")

        # ★ 图例放到绘图区**外面**（v1.1.6 修复遮挡）：
        #   旧写法是 ax.legend(loc="upper left") + ax.legend(loc="lower left")，
        #   两块图例各占绘图区高度的 39% —— 20 条曲线铺满全图时根本没有空位，
        #   图例直接压在数据上。实测 240 分钟 × 20 条共 4800 个数据点里，
        #   有 **790 个（16.5%）** 落在图例矩形内，「流出 Top10」那组尤其严重
        #   （曲线本身就是往下走的，正好撞在图例所在的下半区）。
        #
        #   现在改为：绘图区下方**单独留出一条图例带**（图例带之上是曲线、
        #   之下是画布边缘），图例在带内折行排布，与曲线彻底不重叠。
        # ★ 顺序要紧（v1.1.6 踩过两次，务必照此顺序）：
        #     ① tight_layout  —— 先让 matplotlib 排出轴内边距
        #     ② _legend_outside —— 实测标签宽度定列数，把图例建出来
        #     ③ _reserve_bottom —— 按图例真实高度压缩轴的下沿
        #   顺序颠倒过的两个坑：
        #     · 先 reserve 后 tight_layout → tight_layout 把 position 整个覆盖
        #       （实测 bottom 从 0.208 被打回 0.046，轴重新铺满整高）；
        #     · 先 reserve 后 legend → 不知道图例真实折了几行，留的空间不对。
        self.fig.tight_layout(pad=.45)
        self._legend_outside(h_in, h_out, ax, th)
        self._reserve_bottom(ax)
        self.draw()
        # ★ v1.2.2：整幅 draw 已到此结束、几何也已落定，**顺手把 blit 背景快照建好**
        #   （此刻 _vline 必为 None，背景天然不含竖线）。
        #   若不预建，第一次悬停时还得再付一次 120 ms 的整幅 draw ——
        #   表现为"鼠标刚移上去的第一下明显一顿"。这里预建后，
        #   首次悬停就是 0.16 ms 的纯 blit。
        self._ensure_blit_bg(ax)

    # ---------------------------------------------------------- 图例（轴外）
    def _reserve_bottom(self, ax):
        """按图例真实高度压缩轴的下沿，把图例带腾出来。

        ★ 关键技巧：**先量再排**。早先按「行数 × 估的行高」去猜，中文/长名/
          改字体都会估偏；现在直接量 legend.get_window_extent()，是一手数据。
        ★ 布局自上而下（画布坐标 y 向上）：
              ┌─────────────┐  轴顶
              │   曲线区     │
              ├─────────────┤  轴底 (bottom)
              │ 横轴刻度标签 │  tick_px
              ├─────────────┤  ← 图例**顶边**贴这里
              │   图例带     │  leg_px
              └─────────────┘  画布底
          所以轴的下沿 = (图例高 + 刻度标签高 + 空隙) / 画布高。
          ★ 图例必须用 `loc="upper left"` 锚定，否则默认从锚点向**上**长，
            会直接顶进刻度标签里（实测踩过：图例 y=[24,71]、标签 y=[53,64]，
            整条标签被盖住）。
        ★ 只改 bottom、不动上沿：写 height = 1 - bottom - y0 是错的 ——
          tight_layout 之后 y0 已是含刻度标签的新值，那样算会把上沿顶出画布
          （实测图例跑到曲线上方的元凶就是这个）。
        """
        leg = getattr(self, "_legend", None)
        fig_h_px = max(1.0, self.fig.get_figheight() * self.fig.dpi)
        # ★★ v1.2.3：这里的 `canvas.draw()` 同样可以省掉（见 _legend_outside
        #   的同类优化）。它唯一的作用是"让 renderer 与各 artist 的布局就绪"，
        #   而本方法要量的三样东西（轴框、刻度标签、图例）都只依赖**布局**，
        #   与画布上的曲线像素无关。
        #   ★ 但这里比 _legend_outside 更需谨慎：刻度标签若尚未布局，
        #     `get_window_extent` 会**静默返回 0 高度**（不抛异常），
        #     于是 tick_px 被算成"轴底到 0"的巨大值 → 图例带被撑爆。
        #     所以必须**校验量出来的值是否合理**，不合理就退回光栅化，
        #     而不是盲目相信"没抛异常就是对的"。
        tick_px, leg_px = 14.0, 14.0

        def _measure():
            R = self.fig.canvas.get_renderer()
            ax_bb = ax.get_window_extent(R)
            tbs = [t.get_window_extent(R) for t in ax.get_xticklabels()]
            t = (ax_bb.y0 - min(b.y0 for b in tbs)) if tbs else 14.0
            lp = (leg.get_window_extent(R).height if leg is not None else 14.0)
            return t, lp

        try:
            t_, l_ = _measure()
            # 合理性检查：刻度标签高度应落在 4~80px（窗口再矮/再高也不会出界）
            if 4.0 <= t_ <= 80.0 and 4.0 <= l_ <= fig_h_px:
                tick_px, leg_px = t_, l_
            else:
                raise ValueError(f"implausible tick_px={t_:.1f}")
        except Exception:                                # noqa: BLE001
            try:
                self.fig.canvas.draw()
                tick_px, leg_px = _measure()
            except Exception:                            # noqa: BLE001
                tick_px, leg_px = 14.0, 14.0
        # 轴底之上留 4px 给刻度标签、之下留图例带
        band_px = leg_px + max(10.0, tick_px) + 4.0
        p = ax.get_position()
        # 上限 48%：给图例带留出足够空间，同时保证曲线区至少占一半。
        #   实测 5.5in 宽 + 11 字长名会把图例撑到 10 行（150px），
        #   45% 上限（135px）差 8px 不够，图例会探出画布下沿。
        bottom = min(.48, band_px / fig_h_px)
        ax.set_position([p.x0, bottom, p.width, max(.16, p.height - bottom)])
        # 把图例**顶边**对齐到「轴底 - 刻度标签高 - 3px」，向下生长；
        #   若算出的底边会探出画布（极端窄窗），就上移到贴住画布底边。
        if leg is not None:
            top_frac = (bottom - max(10.0, tick_px) / fig_h_px - 3.0 / fig_h_px)
            min_top = (leg_px + 2.0) / fig_h_px          # 保证底边不低于 2px
            top_frac = max(top_frac, min_top, .005)
            try:
                leg.set_loc("upper left")
                leg.set_bbox_to_anchor((0.045, top_frac, 0.92, 0.001),
                                       transform=self.fig.transFigure)
            except Exception:                            # noqa: BLE001
                pass
        return bottom

    def _legend_outside(self, h_in, h_out, ax, th):
        """在绘图区下方横向铺开两组图例（流入在左 / 流出在右）。

        ★ 只建**一个** Legend，而不是两个 —— fig.legend 每次调用都会替换掉
          上一个，要并存就得先 add_artist 再叠，叠出来的两块没法保证左右
          对齐、也压不住行距。这里把两组 handles 拼进一个 Legend，一次排完。
        ★ 列数用**二分实测**定，不做宽度估算（v1.1.6 踩过两次）：
          按「字号 × 字数」估宽再贪心装箱，估出来的列数在真机上一试就溢出
          画布 —— 中文字宽、handle 宽度、columnspacing 三者叠加都算不准。
          改成：建出来量真实 bbox，超限就换列数。用**二分**而非线性 ——
          列数与宽度单调（列数越少 → 越窄越高），二分 ~5 次即可，
          线性要试十几次 × 每次重画整幅画布（28ms）≈ 半秒，会卡。
        ★ 流入 / 流出用**文字颜色**区分（涨色 vs 跌色），比加两个组标题省空间，
          也顺带与「红涨绿跌」切换联动 —— 反向配色时两组颜色自动对调。
        """
        handles = list(h_in) + list(h_out)
        fig_w_px = self.fig.get_figwidth() * self.fig.dpi
        fig_h_px = max(1.0, self.fig.get_figheight() * self.fig.dpi)
        self._legend = None
        if not handles:
            return None
        n_in = len(h_in)
        # 图例带的高度上限：最多让出 48% 的画布高，剩下的必须留给曲线。
        #   画布高 367px（真实窗口）→ 上限 158px ≈ 12 行；
        #   实测 5.5in 宽 + 11 字长名会撑到 10 行（150px），48% 刚好够。
        max_leg_px = fig_h_px * 0.48 - 18.0
        # ★ 列数用**二分**找，不线性试 —— 线性从 20 列递减要试十几次，
        #   每次都得重画整幅画布（含 20 条曲线 + 坐标轴），单次 28ms，
        #   20 次就是半秒，切主题/换数据时会卡一下（实测压测脚本直接超时）。
        #   二分只需 ~5 次。单调性成立：列数越少 → 每行条数越少 → 越窄越高，
        #   所以「宽度是否超限」随列数单调，可以二分。
        tried = []

        def _try(ncol):
            """建一个 ncol 列的图例并量尺寸，返回 (legend, 是否超限)。

            ★★ v1.2.3 关键优化：**量尺寸不再光栅化整幅画布**。
              原实现每试一个列数就 `self.fig.canvas.draw()` 一次 —— 而
              二分要试 ~5 次，每次全量重绘（20 条曲线 + 图例，实测 ~80ms），
              单这一处就是 **~400ms**，且发生在**每次刷新**里。
              实测归因：FanChart 单轮被 draw() 7 次，其中 4 次来自这里
              （占全部重绘耗时 57%）。

              ★ 为什么能去掉：`get_window_extent` 量的是**图例的布局盒**，
                它由文字尺寸 + 列数 + 间距决定，**与画布上的曲线无关**。
                曲线画没画完，不影响图例有多宽多高。
                而图例自身的布局只需要一个 renderer（字体的真实度量），
                不必把整幅 figure 光栅化。

              做法：优先复用**已有的 renderer**；没有就只对图例那一小块
              强制一次布局（`cand.get_window_extent(renderer)` 本身会
              按需触发文本布局，无需 canvas.draw）。
              仅当拿不到 renderer 的极端情况下，才退回原来的 draw()。
            """
            cand = self._make_legend(handles, ncol, th)
            if cand is None:
                return None, False
            try:
                R = self.fig.canvas.get_renderer()
                bb = cand.get_window_extent(R)
            except Exception:                            # noqa: BLE001
                try:
                    # 拿不到 renderer（极罕见）→ 退回光栅化，保证功能不变
                    self.fig.canvas.draw()
                    bb = cand.get_window_extent(self.fig.canvas.get_renderer())
                except Exception:                        # noqa: BLE001
                    return cand, False
            tried.append(cand)
            return cand, (bb.x0 < 3 or bb.x1 > fig_w_px - 3
                          or bb.height > max_leg_px)

        n_total = len(handles)
        leg = None
        lo, hi = 1, n_total                             # 可行列数必然在 [1, n_total]
        best_ok = None
        while lo <= hi:
            mid = (lo + hi) // 2
            cand, bad = _try(mid)
            if cand is None:
                lo = mid + 1
                continue
            if bad:
                # 太宽/太高 → 需要更少的列，往左半边找
                hi = mid - 1
            else:
                best_ok = cand                           # 记下可行的，再试着多放几列
                lo = mid + 1
        if best_ok is not None:
            leg = best_ok
        elif tried:
            # 画布极窄、一列都嫌高时的兜底：优先选**宽度不超限**的里最矮的，
            #   一个都没有再退到全局最矮。
            #   （不能只按高度选：实测会选到 1 列那版 —— 矮是够矮，但宽 1363px
            #   直接把画布横着捅穿了。宽度溢出比高度溢出更难看。）
            def _met(c):
                try:
                    b = c.get_window_extent(self.fig.canvas.get_renderer())
                    return b.width, b.height, (b.x0 >= 3 and b.x1 <= fig_w_px - 3)
                except Exception:                        # noqa: BLE001
                    return 1e9, 1e9, False
            fits_w_list = [c for c in tried if _met(c)[2]]
            pool = fits_w_list or tried
            leg = min(pool, key=lambda c: _met(c)[1])    # 取最矮的
        # 清掉所有没被采用的图例（否则会与选中的那个叠着一起画）
        for c in tried:
            if c is not leg:
                try:
                    c.remove()
                except Exception:                        # noqa: BLE001
                    pass
        if leg is None:
            return None
        self._legend = leg                               # 供 _reserve_bottom 量高度
        # 流入条目着涨色、流出条目着跌色 —— 一眼分得清哪几条是往下走的
        try:
            for i, t in enumerate(leg.get_texts()):
                t.set_color(th["dn"] if i >= n_in else th["up"])
                t.set_alpha(.95)
        except Exception:                                # noqa: BLE001
            pass
        return leg

    def _make_legend(self, handles, ncol, th):
        """建一个轴外图例（列数由调用方定），失败返回 None。

        ★ loc 必须是 "upper left"：图例从锚点向**下**生长，锚点才等同于
          「图例顶边」。用默认的 "lower left" 会从锚点向上长，
          直接顶进横轴刻度标签里（实测踩过）。
        """
        try:
            return self.fig.legend(
                handles=handles, fontsize=FAN_LEG_FS, frameon=False,
                ncol=ncol, labelcolor=th["fg"],
                loc="upper left",
                bbox_to_anchor=(0.045, 0.25, 0.92, 0.001),
                handlelength=1.5, handletextpad=.5,
                columnspacing=1.2, borderaxespad=0.0)
        except Exception:                                # noqa: BLE001
            return None


# ---------------------------------------------------------------- 指数分时
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
            ax.plot(range(len(c1)), c1, color=th["accent"], linewidth=1.8,
                    label="上证指数")
            ax.fill_between(range(len(c1)), c1, 0, color=th["accent"], alpha=.08)
        # 东财全A：第二条线用可区分的橙色，虚线以便重叠时也分得清
        if c2:
            ax.plot(range(len(c2)), c2, color="#f0932b", linewidth=1.6,
                    linestyle=(0, (5, 2.2)), label="东财全A")

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
    # 紧凑行高：默认 30px 在 30 行的榜单上会多出近 200px 空白
    vh = t.verticalHeader()
    vh.setDefaultSectionSize(24)
    vh.setMinimumSectionSize(20)
    hh.setFixedHeight(28)
    return t


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
            所以只给明确标记的表启用（见 t_hover 的创建处），不全局生效。
    """
    align = getattr(t, "_align_map", {}) or {}
    t.clearSpans()
    if not rows:
        t.setRowCount(1)
        it = QTableWidgetItem("（暂无数据）")
        it.setTextAlignment(Qt.AlignCenter)
        it.setForeground(QColor(_theme_ref["th"]["muted"]))
        t.setSpan(0, 0, 1, t.columnCount())
        t.setItem(0, 0, it)
        return
    t.setRowCount(len(rows))
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
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(2)

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
        """三行文字实际所需高度（含行距与内边距），最少 74px。"""
        try:
            lay = self.layout()
            m = lay.contentsMargins()
            fms = (self.lb_title.fontMetrics(), self.lb_value.fontMetrics(),
                   self.lb_sub.fontMetrics())
            inner = sum(fm.height() for fm in fms) + 2 * lay.spacing()
            return max(74, inner + m.top() + m.bottom() + 2)
        except Exception:                                # noqa: BLE001
            return 80                                    # 兜底：与实测值同量级

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


def make_card(title=None, hint=None, body_layout=None):
    """带标题栏的圆角卡片容器。返回 (外框, 内容区布局)。

    用法：
        card, body = make_card("主力净流入榜")
        body.addWidget(table)

    ★ 边距刻意收紧（8/6/8/8）：卡片原先上下留 10~12px，一屏里两三层卡片
      叠加就是几十像素的纯空白。压到 6~8px 后视觉上仍然透气，但信息密度明显提升。
    """
    frame = QFrame()
    frame.setObjectName("card")
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(9, 7, 9, 9)
    outer.setSpacing(5)

    if title:
        head = QHBoxLayout()
        head.setSpacing(7)
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
                       align=None, widths=None):
    """标题 + 表格的卡片，返回 (卡片, 表格)"""
    card, body = make_card(title, hint)
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
        self.theme_name = "light"
        self.worker = None
        self.timer_auto = None
        self._auto_ms = AUTO_MS_DEFAULT        # v1.2.3：默认 30 秒（用户要求）
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
        """★ 退出前必须等后台线程结束（v1.0.5 修复）：
        抓取进行中直接关窗口，QThread 会在运行中被销毁 —— 控制台报
        "QThread: Destroyed while thread is still running"，进程可能直接崩溃。
        先停自动刷新定时器，再限时等待 worker；超时才强制 terminate
        （此时进程马上退出，泄漏的 socket 无所谓）。"""
        if self.timer_auto is not None:
            self.timer_auto.stop()
        if self.worker is not None and self.worker.isRunning():
            try:
                if not self.worker.wait(3000):
                    self.worker.terminate()
                    self.worker.wait(1000)
            except Exception:                            # noqa: BLE001
                pass
        # ★ 补全线程同样要等（v1.1.0）：它也在跑网络与磁盘写，
        #   与 Worker 是同一个「关窗崩 QThread」的坑。
        if self.backfiller is not None and self.backfiller.isRunning():
            try:
                if not self.backfiller.wait(3000):
                    self.backfiller.terminate()
                    self.backfiller.wait(1000)
            except Exception:                            # noqa: BLE001
                pass
        # ★ 龙头分析线程同理（v1.1.5）
        if self.leader_worker is not None and self.leader_worker.isRunning():
            try:
                if not self.leader_worker.wait(3000):
                    self.leader_worker.terminate()
                    self.leader_worker.wait(1000)
            except Exception:                            # noqa: BLE001
                pass
        ev.accept()

    # ---------------------------------------------------------- UI
    def _build_ui(self):
        self._build_menu()
        self._build_toolbar()

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        # ---------------------------------------------------------- 1 总览
        # ★★ v2.0.1 结构（问题九）：KPI 卡行  →  放大的资金曲线。
        #   原来的「指数分时 | 流入流出对比」与「时点数据面板」已移到
        #   新页「市场全景」，理由见下方曲线段的注释。
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
        self.ch_fan = FanChart(6.4)
        card_fan, body_fan = make_card(
            "主力资金累计曲线",
            f"流入 Top{FAN_TOP_N // 2}（暖色）· 流出 Top{FAN_TOP_N // 2}（冷色）"
            f"均为实线 · 纵轴量程自适应 · 横轴固定 9:30–15:00 · "
            f"鼠标移到图上可查看任意分钟（明细见「市场全景 → 时点数据面板」）")
        # 悬停摘要行：一个时刻只占一行，不挤压曲线高度。
        # ★ 为什么不在这里保留原来那张 7 行的表格：
        #   一屏里表格会把曲线再压掉三成高度，而"看某分钟前十名"是**查询式**需求，
        #   放到「市场全景」页整宽展示反而更清楚（一屏能看到流入+流出共 14 行）。
        #   这里只留一行摘要，保证"指着曲线就能读出这一点是多少"。
        self.lbl_fan_tip = QLabel("时点数据 · 鼠标移到曲线上查看任意分钟")
        self.lbl_fan_tip.setObjectName("cardHint")
        self.lbl_fan_tip.setWordWrap(False)
        self.lbl_fan_tip.setMinimumWidth(0)
        self.lbl_fan_tip.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        body_fan.addWidget(self.lbl_fan_tip)
        body_fan.addWidget(self.ch_fan, 1)
        v.addWidget(card_fan, 1)

        self.tabs.addTab(page, "总览")

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

        self.ch_index = IndexChart(4.0)
        card_idx, body_idx = make_card("指数分时（上证 + 东财全A）",
                                       "各自以开盘为基准的涨跌幅 · 全A 覆盖沪深全市场")
        body_idx.addWidget(self.ch_index)
        g.addWidget(card_idx, 0, 0)

        self.ch_flow = FlowBarChart(4.0)
        card_flow, body_flow = make_card("主力资金流入 / 流出对比",
                                         "右侧红=流入 · 左侧绿=流出")
        body_flow.addWidget(self.ch_flow)
        g.addWidget(card_flow, 0, 1)

        # 时点数据面板：由「总览」的曲线悬停驱动（见 on_fan_hover）。
        #   文案与 render_charts 里保持一致，避免重绘前后自相矛盾。
        self.lbl_hover = QLabel("时点数据 · 在「总览」把鼠标移到曲线上查看任意分钟")
        # ★★ v1.2.2 光标跟随延迟修复（第 2 处，静默的连锁反应）★★★★★★★★★★
        #   【症状】只改上面那一行文本，就能把右侧曲线图挤窄、进而毁掉 blit 快照。
        #   【实测】这句话随悬停不断变长：
        #               "时点数据 · 鼠标移到曲线上查看任意分钟" ........ 240 px
        #               "时点 09:31 · 图上可见 14 条 · 合计 +0.0 亿" ..... 420 px
        #               "时点 10:30 · 图上可见 14 条 · 合计 -1234.5 亿" .. 456 px
        #           QLabel 默认 wordWrap=False + SizePolicy.Preferred，
        #           minimumSizeHint 会**跟着文字宽度一起长**（240 → 456 px）——
        #           侧栏因此变宽，而侧栏与曲线同在 fan_row 这个 QHBoxLayout 里，
        #           布局把多出来的宽度从 7:3 的另一侧扣走：
        #               画布宽度实测 1002 → 1001 → 965 px（来回振荡）
        #           画布一 resize，Qt 后端就 draw_idle() 整幅重绘，
        #           blit 快照的几何指纹随即失效 —— 于是又退回 120 ms/次的旧路径。
        #           即：局部重绘做了，但每几帧就被这条文本打回原形。
        #   【修法】同 v1.2.1 的 make_card 坑：让它**不参与最小宽度计算**。
        #           换行 + QSizePolicy.Ignored —— 文本再长也只在既有宽度内折行，
        #           sizeHint 不再驱动布局，画布宽度因此保持恒定。
        #   （v2.0.1 这张面板已整宽、不再与曲线并排，但本条策略继续保留：
        #     它同时防住"文本变长把卡片撑宽"这一类问题，成本为零。）
        self.lbl_hover.setWordWrap(True)
        self.lbl_hover.setMinimumWidth(0)
        self.lbl_hover.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        card_side, body_side = make_card(
            "时点数据面板",
            "在「总览」把鼠标移到曲线上 → 这里显示该分钟的流入/流出前七名")
        body_side.addWidget(self.lbl_hover)
        self.t_hover = make_table(
            ["序号", "题材", "净额(亿)", "5分钟(亿)", "辨识度"],
            stretch_col=1, align={0: "c", 1: "l"}, widths={0: SEQ_COL_W})
        # ★ v1.2.2：这张表是**唯一**被鼠标高频重填的表 ——
        #   横轴划一遍会重建上百次。首次填完后把列宽锁成 Fixed，
        #   避免每次 setItem 都触发 ResizeToContents 的全表文本测量
        #   （实测 12.73 ms → 0.64 ms，见 fill_table 的 docstring）。
        #   它的内容结构固定（榜单前 7 + 后 7，字段长度稳定），锁宽无副作用。
        self.t_hover._freeze_widths = True
        body_side.addWidget(self.t_hover, 1)
        g.addWidget(card_side, 1, 0, 1, 2)

        g.setColumnStretch(0, 4); g.setColumnStretch(1, 5)
        g.setRowStretch(0, 4); g.setRowStretch(1, 6)
        self.tabs.addTab(w, "市场全景")

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
            stretch_col=6, align=_radar_align, widths={0: SEQ_COL_W})
        c_r2, self.t_radar_out = make_labeled_table(
            "异动雷达 · 流出（此刻资金异常撤离）",
            ["序号", "题材", "净额(亿)", "5分钟(亿)", "z", "雷达分", "识别原因"],
            hint="方向按近 5 分钟增量的符号判定　·　识别原因 = 该条最突出的那一维",
            stretch_col=6, align=_radar_align, widths={0: SEQ_COL_W})
        g.addWidget(c_r1, 0, 0); g.addWidget(c_r2, 0, 1)
        g.setColumnStretch(0, 1); g.setColumnStretch(1, 1)
        self.tabs.addTab(w, "异动雷达")

        # ---------------------------------------------------------- 2 辨识度榜
        w = QWidget(); g = QGridLayout(w)
        g.setContentsMargins(*PAD_PAGE); g.setSpacing(GAP_PAGE)
        # 排名列定宽 46px + 居中：序号紧贴左侧，题材名左对齐紧跟其后，
        # 两者之间只隔一个单元格内边距（原来题材名被推到 685px 列的最右端）。
        RANK_W = {0: 46}
        RANK_A = {0: "c", 1: "l"}
        c1, self.t_in = make_labeled_table(
            "主力净流入榜", ["排名", "题材", "净额(亿)", "5分钟(亿)", "辨识度"],
            align=RANK_A, widths=RANK_W)
        c2, self.t_out = make_labeled_table(
            "主力净流出榜", ["排名", "题材", "净额(亿)", "5分钟(亿)", "辨识度"],
            align=RANK_A, widths=RANK_W)
        g.addWidget(c1, 0, 0); g.addWidget(c2, 0, 1)
        g.setColumnStretch(0, 1); g.setColumnStretch(1, 1)
        self.tabs.addTab(w, "辨识度榜")

        # ---------------------------------------------------------- 3 行业概况
        w = QWidget(); g = QGridLayout(w)
        g.setContentsMargins(*PAD_PAGE); g.setSpacing(GAP_PAGE)
        c1, self.t_ind_in = make_labeled_table(
            "行业净流入", ["排名", "一级行业", "净额(亿)", "5分钟(亿)", "辨识度"],
            align=RANK_A, widths=RANK_W)
        c2, self.t_ind_out = make_labeled_table(
            "行业净流出", ["排名", "一级行业", "净额(亿)", "5分钟(亿)", "辨识度"],
            align=RANK_A, widths=RANK_W)
        g.addWidget(c1, 0, 0); g.addWidget(c2, 0, 1)
        g.setColumnStretch(0, 1); g.setColumnStretch(1, 1)
        self.tabs.addTab(w, "行业概况")

        # ---------------------------------------------------------- 4 实时事件
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(*PAD_PAGE); v.setSpacing(GAP_PAGE)
        card, self.t_ev = make_labeled_table(
            "盘中异动事件", ["时刻", "事件", "题材", "说明", "强度"],
            hint="全天事件流 · 按时间倒序（晚→早）· 同刻按强度 · "
                 "加速流入 / 新晋 / 资金回流 / 失血 / 跳水 / 排名跃迁",
            stretch_col=3, align={0: "c", 1: "c", 2: "l", 3: "l"})
        v.addWidget(card)
        self.tabs.addTab(w, "实时事件")

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
            stretch_col=4, align={0: "l", 1: "c", 3: "c"})
        c_out, self.t_th_out = make_labeled_table(
            "流出主线", ["主线", "成员", "总净额(亿)", "集中", "代表板块"],
            hint="净额为负 = 被资金抛弃",
            stretch_col=4, align={0: "l", 1: "c", 3: "c"})
        g.addWidget(c_in, 0, 0)
        g.addWidget(c_out, 0, 1)

        # 第三象限：题材簇预览（红八 / 绿八 对称，两栏柱条均向右）
        self.ch_theme = ThemeBoard(3.2)
        card_th, body_th = make_card(
            "题材簇预览",
            "左 = 流出 8 条（绿）· 右 = 流入 8 条（红）· 两栏柱条**均向右**伸 · "
            "柱长按各栏最大值归一 → 两侧同一条数、视觉重量相当；"
            "量级差异见各栏标题「最长」，真实金额见柱尾标签")
        body_th.addWidget(self.ch_theme)
        g.addWidget(card_th, 1, 0)

        # 第四象限：龙头领先反应（新）
        card_ld, body_ld = make_card(
            "龙头领先反应",
            "龙头 = 在板块拐点上先动手的那只（不是净额最大）· 领先′ = 板块拐点 − 个股反应")
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
        self.tabs.addTab(w, "题材簇")

        # ---------------------------------------------------------- 6 主角更替
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(*PAD_PAGE); v.setSpacing(GAP_PAGE)
        self.ch_lead = LeaderGrid(6.0)
        card, body = make_card(
            "主角更替", "每行三个色块 = 该时刻辨识度前三 · 右侧为三强合计与环比")
        body.addWidget(self.ch_lead)
        v.addWidget(card)
        self.tabs.addTab(w, "主角更替")

        # ---------------------------------------------------------- 7 阶段对比
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(*PAD_PAGE); v.setSpacing(GAP_PAGE)
        self.ch_stage = StageGrid(6.0)
        card, body = make_card(
            "阶段对比", "一天 6 个时点 · 每格上半段红=流入 Top6 / 下半段绿=流出 Top6，柱长正比金额")
        body.addWidget(self.ch_stage)
        v.addWidget(card)
        self.tabs.addTab(w, "阶段对比")

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
            align={0: "c", 1: "c", 2: "l", 3: "l"})
        v.addWidget(card)
        self.tabs.addTab(w, "关键节点")

        # ---------------------------------------------------------- 8.5 实时榜单
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
            "龙头选手 = 该板块拐点上先动手的那只（来自龙头领先反应，未分析到显示 —）")
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

        # ---- 下半部分：四个「当下榜」（2×2）
        grid_w = QGridLayout(); grid_w.setSpacing(GAP_CARD)
        # 序号列宽度统一走 SEQ_COL_W（见布局常量区说明）
        SEQ_W = {0: SEQ_COL_W}
        SEQ_A = {0: "c", 1: "l"}
        c_b1, self.t_wb_in = make_labeled_table(
            "当下辨识度榜 · 流入",
            ["序号", "题材", "净额(亿)", "5分钟(亿)", "辨识度"],
            hint="按累计净额排序 · 全天体量口径",
            align=SEQ_A, widths=SEQ_W)
        c_b2, self.t_wb_out = make_labeled_table(
            "当下辨识度榜 · 流出",
            ["序号", "题材", "净额(亿)", "5分钟(亿)", "辨识度"],
            hint="按累计净额排序 · 全天体量口径",
            align=SEQ_A, widths=SEQ_W)
        c_b3, self.t_wb_d5in = make_labeled_table(
            "当下辨识度榜 · 五分钟净流入",
            ["序号", "题材", "净额(亿)", "5分钟(亿)", "辨识度"],
            hint="按近 5 分钟增量排序 · 此刻资金正在涌入",
            align=SEQ_A, widths=SEQ_W)
        c_b4, self.t_wb_d5out = make_labeled_table(
            "当下辨识度榜 · 五分钟净流出",
            ["序号", "题材", "净额(亿)", "5分钟(亿)", "辨识度"],
            hint="按近 5 分钟增量排序 · 此刻资金正在撤离",
            align=SEQ_A, widths=SEQ_W)
        grid_w.addWidget(c_b1, 0, 0); grid_w.addWidget(c_b2, 0, 1)
        grid_w.addWidget(c_b3, 1, 0); grid_w.addWidget(c_b4, 1, 1)
        grid_w.setColumnStretch(0, 1); grid_w.setColumnStretch(1, 1)
        grid_w.setRowStretch(0, 1); grid_w.setRowStretch(1, 1)
        v.addLayout(grid_w, 3)
        self.tabs.addTab(w, "实时榜单")

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
                        (getattr(self, "t_hover", None), "hover_panel"),
                        (getattr(self, "t_radar_in", None), "radar_in"),
                        (getattr(self, "t_radar_out", None), "radar_out"),
                        (getattr(self, "t_leader", None), "leader_grid")):
            if _t is not None:
                try:
                    _t.setObjectName(_on)
                except Exception:                        # noqa: BLE001
                    pass

        # ★★ v2.0.1：切页时补画该页的图表（配合 render_charts 的按页延迟绘制）。
        #   放在 setCentralWidget 之前连接 —— 信号必须挂在已创建的 tabs 上。
        try:
            self.tabs.currentChanged.connect(self.on_tab_changed)
            log_line("lazy-render hook connected (tabs.currentChanged)")
        except Exception as _e:                          # noqa: BLE001
            log_line(f"lazy-render hook connect FAILED: {_e}")

        self.setCentralWidget(self.tabs)

        # ★ 页签一致性自检：addTab 的顺序必须与 PAGE_NAMES 完全一致。
        #   不一致会让 Ctrl+N 与 PAGE_INDEX 全部错位 —— 而这种错位
        #   不会抛异常、只是"跳错页"，属于最难发现的一类问题。
        #   这里**只记日志不抛异常**：生产环境宁可界面能开、留痕排查，
        #   也不要因为一个静态清单写错就直接起不来。测试套件里另有硬断言。
        _tabs_now = [self.tabs.tabText(i) for i in range(self.tabs.count())]
        self._tab_names_actual = _tabs_now
        if _tabs_now != PAGE_NAMES:
            log_line(f"TAB MISMATCH: tabs={_tabs_now} vs PAGE_NAMES={PAGE_NAMES}")

        sb = QStatusBar()
        self.setStatusBar(sb)
        self.lbl = QLabel("就绪")
        sb.addWidget(self.lbl)
        self.bar = QProgressBar(); self.bar.setMaximumWidth(220)
        self.bar.setRange(0, 0); self.bar.hide()
        sb.addPermanentWidget(self.bar)

        # ★ 首屏加载占位（v1.0.5）：数据到位前图表区先给出明确提示，
        #   而不是一片空白 —— 用户能看出「程序在干活」而不是「坏了」。
        #   数据到达后 render_charts 会整体重画，占位自然消失。
        for c in (self.ch_index, self.ch_flow, self.ch_fan,
                  self.ch_lead, self.ch_stage, self.ch_theme):
            c.blank("数据加载中…")

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
        if "--date" in sys.argv:
            _i = sys.argv.index("--date")
            _d = sys.argv[_i + 1] if _i + 1 < len(sys.argv) else None
            if _d:
                QTimer.singleShot(80, lambda: self.load_history(_d))
                return
        QTimer.singleShot(80, lambda: self.reload(force=False, incremental=True))

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
        # ★ v1.2.1：页签清单收敛为**唯一常量** PAGE_NAMES（见文件顶部），
        #   addTab 的顺序与这里必须完全一致；需要"跳到某页"时用
        #   PAGE_INDEX[名字] 取索引，不要写字面量数字、更不要用 count()-1。
        for i, nm in enumerate(PAGE_NAMES):
            act = QAction(nm, self)
            act.setShortcut(f"Ctrl+{i + 1}")            # 页面快捷键
            act.triggered.connect(lambda _=False, k=i: self.tabs.setCurrentIndex(k))
            v.addAction(act)
        v.addSeparator()
        self.act_dark = QAction("暗色主题", self, checkable=True)
        self.act_dark.triggered.connect(self.toggle_theme)
        v.addAction(self.act_dark)

        v.addSeparator()
        # ★★ v1.2.3：自动刷新改为「开关 + 周期子菜单」。
        #   旧版写死 5 分钟且文案里带周期，改周期就得改代码。
        #   现在 10/30/60/300 秒四档，默认 30 秒（用户要求）。
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

    def _auto_tick(self):
        """只在交易时段（09:25~15:05）且没有正在跑的抓取时才刷新。

        ★ v1.2.3：刷新走**增量通道**（批量快照，~350ms），
          因此 10 秒周期也来得及 —— 上一轮还没跑完就跳过本轮，
          天然不会堆积（见下方 `worker.isRunning()` 守卫）。
        """
        now = datetime.now()
        if now.weekday() >= 5:                            # 周末
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
        for c in (self.ch_index, self.ch_flow, self.ch_fan,
                  self.ch_lead, self.ch_stage, self.ch_theme):
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
                for c in (self.ch_index, self.ch_flow, self.ch_fan,
                          self.ch_lead, self.ch_stage, self.ch_theme):
                    c.blank("数据加载失败\n（帮助 → 运行状态 可看详情 · 按 F5 重试）")
                self.lbl_hover.setText("数据加载失败 — 按 F5 重试")
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
        self.data = d
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
        self.tb_info.setText(
            f"  板块 {n1}   分钟点 {n2}"
            + (f"   分时到 {last_hm}" if last_hm else "")
            + (f"   数据日 {tag}（{src_cn}）" if tag else ""))
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
        # ★★ v2.0.1：only_visible=True —— 首屏只画「当前可见页」的图。
        #   理由与实测数据见 CHART_PAGE 的注释：数据处理只要 94 ms，
        #   首屏的时间几乎全花在"把 6 张图一次性画完"上，
        #   而其中 5 张在别的页签、用户此刻根本看不到。
        #   FLOWLIVE_RENDER_ALL=1 可强制全画（诊断/对照用，见 _env_render_all）。
        try:
            self.render_charts(only_visible=not _env_render_all())
        except Exception as _e:                          # noqa: BLE001
            log_line(f"render_charts failed: {type(_e).__name__}: {_e}")
        _t3 = time.perf_counter()
        log_line(f"on_data UI · fill={(_t1 - _t0) * 1000:.0f}ms"
                 f"+{(_t2 - _t1) * 1000:.0f}ms "
                 f"charts={(_t3 - _t2) * 1000:.0f}ms "
                 f"since_boot={(_t3 - T_BOOT) * 1000:.0f}ms "
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
        if is_live_view:
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
        if is_live_view:
            self.start_leader(auto=True)
        self.show_status()

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

        _seg("题材簇", self._fill_themes)
        _seg("关键节点", self._fill_keynodes)
        _seg("异动雷达", self._fill_radar)

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
        d = self.data
        if not d:
            return
        F = d.get("F")
        m = d.get("market") or {}
        times = d.get("times") or []

        cur_page = ""
        try:
            if self.tabs.count() and self.tabs.currentIndex() >= 0:
                cur_page = self.tabs.tabText(self.tabs.currentIndex())
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
        if _should("ch_index", "市场全景"):
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
                self.ch_index.plot(idx, idx_all)
            elif (d.get("source") or "live") == "backfill":
                # ★ 历史日指数分钟行情不可得（东财接口限制，2026-09-19 五轮实测）。
                #   明确告知原因，而不是留一片空白让人误以为程序坏了。
                self.ch_index.blank("历史日指数行情不可得\n（接口限制 · 资金流数据不受影响）")
            else:
                self.ch_index.blank()

        # ★ 流入 / 流出双向对比图（总览补齐流出的核心图）
        if _should("ch_flow", "市场全景"):
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
            self.ch_fan.plot(series)

        if _should("ch_lead", "主角更替"):
            self.ch_lead.plot(d.get("leaders") or [])
        if _should("ch_stage", "阶段对比"):
            self.ch_stage.plot(d.get("stages") or [])
        if _should("ch_theme", "题材簇"):
            try:
                self.ch_theme.plot(d.get("themes") or [])
            except Exception:                            # noqa: BLE001
                pass

        # ★ 悬停联动回调（v1.1.5）：曲线鼠标移动 → 面板切换该分钟数据。
        #   注意它**不属于绘制**，无论本页画没画都要设好 ——
        #   否则切回总览后曲线不再响应悬停（且不报错，很难发现）。
        self.ch_fan.hover_cb = self.on_fan_hover
        # 未悬停时的引导文案：与 _build_ui 里的初始文案保持一致
        #   （原来两处写的不一样：构建时是"鼠标未悬停"，
        #   每次重绘后又变成"鼠标移到曲线上查看任意分钟"，
        #   用户会看到这句话自己变来变去。统一为引导性的这句。）
        self.lbl_hover.setText("时点数据 · 鼠标移到曲线上查看任意分钟")
        try:
            self.lbl_fan_tip.setText("时点数据 · 鼠标移到曲线上查看任意分钟")
        except Exception:                                # noqa: BLE001
            pass

    def on_tab_changed(self, idx):
        """切页时补画该页的图表（★ v2.0.1 按页延迟绘制）。

        只在确有脏图时才动作 —— 每次切页都重画会让切页变卡，
        而"这一页的图本来就是最新的"时重画毫无收益。
        """
        dirty = getattr(self, "_charts_dirty", None)
        if not dirty:
            return
        try:
            name = self.tabs.tabText(idx)
        except Exception:                                # noqa: BLE001
            return
        if any(p == name for _n, p in CHART_PAGE if _n in dirty):
            # 只画当前页，其余继续留在 dirty 里等下一次切页
            self.render_charts(only_visible=True)
            log_line(f"lazy render · page={name} "
                     f"still_dirty={sorted(getattr(self, '_charts_dirty', []))}")
        # 其它页（表格页）没有待画图表 —— **不记日志**：
        # 切到无图页是绝大多数情况，每次记一条只会把日志淹掉。

    # ---------------------------------------------------------- 悬停联动
    def on_fan_hover(self, i, hm):
        """鼠标停在曲线的第 i 分钟 → 左侧面板显示该时点的榜单。

        ★ 与 HTML 版的关系：HTML 版只有"看某时刻"的静态图，
          这里是用户本次新要求的**交互联动**（鼠标到 10 点那一列，
          左边就显示 10 点的数据）。
        实现上完全复用既有分析管线：salience.score_table(F, i) 取该分钟快照，
        与「阶段对比」页用的是同一个函数 —— 口径一致，不用另写一套算法。

        ★ 结果缓存（v1.1.6 性能优化）：score_table 内部要对**全部 1000 个
          板块**做一次 argsort（只为取前 7 名）。单次不到 1ms 听着不多，
          但鼠标横扫横轴会触发上百次，还要连带重建整张表；
          实测快速划动时有可感知的顿挫。
          这里按分钟号缓存 (榜单, 合计)：反向划回同一列不再重算；
          数据换天（date_tag 变化）时整块作废。
        """
        d = self.data
        if not d:
            return
        F = d.get("F")
        if F is None:
            return
        T = len(getattr(F, "times", []) or [])
        if T == 0:
            return
        i = max(0, min(int(i), T - 1))
        times = d.get("times") or list(F.times)
        hm_now = hm or (times[i] if i < len(times) else "")

        # 缓存键跟随数据标识：换日/换面板自动失效
        ckey = d.get("date_tag")
        if getattr(self, "_hover_key", None) != ckey:
            self._hover_key = ckey
            self._hover_cache = {}
        hit = self._hover_cache.get(i)
        if hit is None:
            # 该时点的榜单快照（复用「阶段对比」页同一函数，口径一致）
            try:
                import salience as _sal
                rows_in = _sal.score_table(F, i, topn=7)
                rows_out = _sal.score_table(F, i, topn=7, direction="out")
            except Exception:                            # noqa: BLE001
                rows_in, rows_out = [], []
            # 合并成一张榜：流入在前、流出在后，各取前 7 条（面板高度有限）
            merged = list(rows_in or []) + list(rows_out or [])
            net_sum = 0.0
            for r in merged:
                net_sum += _fnum(r.get("net_yi"), 0.0)
            hit = (merged, net_sum)
            if len(self._hover_cache) < 512:             # 上限保护（240 列封顶）
                self._hover_cache[i] = hit
        merged, net_sum = hit

        cols = {2: self._net_color, 3: self._net_color}
        fill_table(self.t_hover, self._fmt_rows(merged), cols)
        tip = (f"时点 {hm_now}  ·  图上可见 {len(merged)} 条 ·  合计 {net_sum:+.1f} 亿")
        self.lbl_hover.setText(tip)
        # ★ v2.0.1：总览页那一行摘要同步更新。
        #   曲线在「总览」、明细表在「市场全景」—— 两页都要能读到当前指着的时点，
        #   否则用户切页回来会以为自己指错了地方。
        try:
            self.lbl_fan_tip.setText(
                f"{tip}　·　明细见「市场全景」页")
        except Exception:                                # noqa: BLE001
            pass

    # ---------------------------------------------------------- 状态页
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

    for m in ["numpy", "matplotlib", "PySide6"]:
        try:
            __import__(m)
        except Exception as e:                        # noqa: BLE001
            problems.append(f"{m}: {e}")

    # core/ 模块（打包后位于 _MEIPASS/core）；
    # v1.1.0 新增 tradecal/store/backfill；v1.1.5 新增 archive/leader
    for m in ["flowdata", "salience", "universe", "tradecal", "store",
              "backfill", "archive", "leader"]:
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

    probs = preflight()
    if probs:
        QMessageBox.critical(
            None, "启动自检失败",
            "程序组件不完整，无法运行：\n\n" + "\n".join(probs[:6]) +
            f"\n\n日志：{app_log_path()}")
        return 2

    if not self_check and not acquire_single_instance():
        QMessageBox.information(
            None, "资金流向",
            "程序已在运行中。\n\n请切换到已打开的窗口；"
            "若确实需要重开，请先关闭现有窗口。")
        return 0

    w = MainWindow()
    w.show()
    # ★ v2.0.1：首屏链路的第一段耗时（进程启动 → 窗口可见）。
    #   与 on_data 里的 since_boot 配对，就能看出"启动慢"到底慢在哪一段：
    #     · 这一段大 → 是 import / 界面构建的成本（Python + PySide6 + matplotlib）
    #     · on_data 那段大 → 是抓取 / 计算 / 绘图的成本
    log_line(f"boot · window shown at {(time.perf_counter() - T_BOOT) * 1000:.0f}ms "
             f"(import+UI build)")

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
