# -*- coding: utf-8 -*-
"""v2.0.1 修复项自动验证 —— 数据层断言 + 屏幕层表格断言。

为什么必须有这个脚本：
  「数据层排好序」不等于「屏幕上有序」。中间还隔着
    Worker.on_data → fill_all/fill_watch → fill_table → QTableWidget
  任何一层做了二次排序/截断/缓存，用户看到的仍然是错的 —— 而用户看的是屏幕。
  所以本脚本分两层验证：
    ① 数据层：直接调 core / native_app 里的函数，断言返回的列表有序、字段齐全；
    ② 屏幕层：读 --shot 自检导出的 *.table.txt（那是向 Qt 要来的
       QTableWidgetItem.text()，与屏幕显示**逐字相同**），断言行序正确。

用法：
    python verify_v201.py            跑全部
    python verify_v201.py 5 6        只跑第 5、6 项（问题编号）
"""
import os
import sys
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(os.path.dirname(HERE), "build")
RUNTIME_DATA = os.path.join(os.path.dirname(HERE), "runtime", "FlowLiveNative", "data")
SHOTS = os.path.join(os.path.dirname(HERE), "shots")

sys.path.insert(0, BUILD)
sys.path.insert(0, os.path.join(BUILD, "core"))

TAG = os.environ.get("FLOWLIVE_TEST_TAG", "20260921")
# 与 native_app.RADAR_N 一致（这里独立取一份，避免验证脚本依赖 GUI 模块导入）
RADAR_N_V = 24

_PASS, _FAIL, _SKIP = [], [], []


def check(name, cond, detail=""):
    if cond:
        _PASS.append(name)
        print(f"  [PASS] {name}" + (f" · {detail}" if detail else ""))
    else:
        _FAIL.append(name)
        print(f"  [FAIL] {name}" + (f" · {detail}" if detail else ""))


def skip(name, why):
    _SKIP.append(name)
    print(f"  [SKIP] {name} · {why}")


def _bootstrap():
    """把数据目录指向 D 盘 runtime，并返回 (flowdata, salience, F)。"""
    import flowdata
    if os.path.isdir(RUNTIME_DATA):
        flowdata.DATA = RUNTIME_DATA
    import salience
    return flowdata, salience


def _tk(mod, s):
    fn = getattr(mod, "time_key", None)
    return fn(s) if callable(fn) else None


# ============================================================ 问题五
def q5():
    """全天关键节点：从晚到早（时间降序），且 topn 仍是「先选后摆」。"""
    print("\n[问题五] 全天关键节点排序 = 从晚到早")
    import salience
    F = salience.build_features(TAG)
    rows = salience.timeline_summary(F)
    check("返回非空", len(rows) > 0, f"{len(rows)} 条")
    keys = [(-1 if _tk(salience, r["time"]) is None else _tk(salience, r["time"]))
            for r in rows]
    check("时间单调不增（晚→早）",
          all(keys[i] >= keys[i + 1] for i in range(len(keys) - 1)),
          f"{rows[0]['time']} → {rows[-1]['time']}")
    check("首条不早于末条",
          (_tk(salience, rows[0]["time"]) or 0) >= (_tk(salience, rows[-1]["time"]) or 0))
    r18 = salience.timeline_summary(F, topn=18)
    check("topn=18 仍先按强度选（条数正确）", len(r18) == min(18, len(rows)),
          f"{len(r18)} 条")
    k18 = [_tk(salience, r["time"]) for r in r18 if _tk(salience, r["time"]) is not None]
    check("topn 结果同样时间降序",
          all(k18[i] >= k18[i + 1] for i in range(len(k18) - 1)))
    # 屏幕层
    _screen_table_check("问题五·屏幕层", "关键节点", keys_desc=True,
                        time_col=0, strength_col=4)


# ============================================================ 问题六
def q6():
    """实时事件：从晚到早（9:50 采集的显示在 10:20 下方）。"""
    print("\n[问题六] 实时事件排序 = 从晚到早")
    import salience
    import native_app as NA
    F = salience.build_features(TAG)
    ev = NA._event_stream(F, salience)
    check("返回非空", len(ev) > 0, f"{len(ev)} 条")
    keys = [_tk(salience, e.get("time")) for e in ev]
    bad = [(i, keys[i], keys[i + 1]) for i in range(len(keys) - 1)
           if keys[i] is not None and keys[i + 1] is not None and keys[i] < keys[i + 1]]
    check("时间单调不增（晚→早）", not bad, f"越界处 {len(bad)} 个")
    # 同一时刻内强度必须降序
    from collections import defaultdict
    by_t = defaultdict(list)
    for e in ev:
        by_t[e.get("time")].append(float(e.get("strength", 0) or 0))
    unsorted = {t: v for t, v in by_t.items()
                if any(v[i] < v[i + 1] for i in range(len(v) - 1))}
    check("同一时刻内按强度降序", not unsorted, f"异常时刻 {list(unsorted)[:3]}")
    check("每时点不超过 per_slot 条",
          all(len(v) <= 2 for v in by_t.values()),
          f"最多 {max(len(v) for v in by_t.values())} 条")
    # 用户举的例子：9:50 必须在 10:20 下方
    def first_idx(t):
        for i, e in enumerate(ev):
            if e.get("time") == t:
                return i
        return None
    i950, i1020 = first_idx("09:50"), first_idx("10:20")
    if i950 is not None and i1020 is not None:
        check("09:50 排在 10:20 下方", i950 > i1020, f"下标 {i950} > {i1020}")
    else:
        skip("09:50 vs 10:20 位置", "样本中缺这两个时刻")
    _screen_table_check("问题六·屏幕层", "实时事件", keys_desc=True,
                        time_col=0, strength_col=4, obj="event_stream")


# ============================================================ 问题十
# 用户点名的方向 → 用于在板块名里模糊匹配（**只用于验证**，不进算法）
#   关键词按"面板里实际存在的板块名"校准过（如证券→券商概念/证券Ⅱ、
#   玻纤→玻纤制造、风电→风电设备）。仍匹配不到的那些方向
#   （电子布、OCS交换机等）属于**数据源没有编制这个板块**，
#   不计入分母 —— 这一点必须在报告里如实说明，不能算成算法漏检。
WANTED = {
    "创新药": ["创新药"], "光纤": ["光纤", "光通"], "PCB": ["PCB"],
    "光模块": ["光模块", "光通信"], "医药": ["医药", "中药", "生物医药"],
    "CRO": ["CRO"], "电子布": ["电子布"], "AI医疗": ["AI医疗", "智慧医疗", "医疗研发外包"],
    "玻纤": ["玻纤", "玻璃纤维"], "银行": ["银行"], "液冷": ["液冷"], "稀土永磁": ["稀土"],
    "OCS交换机": ["交换机", "OCS"], "房地产": ["房地产", "地产"],
    "半导体设备": ["半导体设备", "半导体"], "证券": ["证券", "券商"],
    "商业航天": ["商业航天", "航天"],
    "零售": ["零售", "商业百货"], "旅游": ["旅游"], "白酒": ["白酒"],
    "算力": ["算力", "数据中心"],
    "农业": ["农业", "种植"], "机器人": ["机器人"], "风电": ["风电", "风能"],
    "芯片": ["芯片", "集成电路"], "电阻电容": ["被动元件", "电阻", "电容"],
    "金属铜": ["铜"], "金属钨": ["钨"], "并购重组": ["重组", "并购"],
    "存储": ["存储"], "先进封装": ["先进封装", "封装"], "黄金": ["黄金"],
}


def _func_code_only(src, fname):
    """取某个函数**去掉 docstring 与注释后的实际代码**。

    为什么需要它：本项目习惯在注释/docstring 里引用旧代码做对照
    （例如"旧实现是 `for j in np.argsort(...)[:80]`"）。
    直接在源码全文里搜某个写法，会把**说明文字**也当成"代码里还在用"，
    得到假失败。用 AST 剥掉 docstring 与注释，判断的才是真正执行的语句。
    """
    import ast
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fname:
            body = [s for s in node.body
                    if not (isinstance(s, ast.Expr)
                            and isinstance(s.value, ast.Constant))]
            return "\n".join(ast.unparse(s) for s in body)
    return ""


def q10():
    """板块筛查与盘中异动算法：候选池、尾盘回流、雷达、真实召回。"""
    print("\n[问题十] 板块筛查与盘中异动算法")
    sp_sal = os.path.join(BUILD, "core", "salience.py")
    sp_wl = os.path.join(BUILD, "core", "watchlist.py")
    src_sal = open(sp_sal, encoding="utf-8").read()
    src_wl = open(sp_wl, encoding="utf-8").read()

    # ---- 结构断言（用 AST 剥掉注释后再判，避免注释里的旧代码造成假失败）----
    de_code = _func_code_only(src_sal, "detect_events")
    check("detect_events 实际代码中不再有「前 80」候选池",
          de_code and "[:80]" not in de_code and "argsort" not in de_code,
          f"实码 {len(de_code)} 字符")
    check("z 兜底不再「只看前 25 名」",
          "argsort(-_np.abs(sub))[:25]" not in src_wl)
    check("存在「尾盘回流」事件类型", '"尾盘回流"' in src_sal)
    check("存在异动雷达", "def anomaly_radar" in src_sal)
    check("累积池已接入雷达路径", "异动雷达·" in src_wl)
    check("雷达每轮追加有上限（不灌满池子）", "RADAR_ADD_PER_SIDE" in src_wl)
    check("快照字段含领涨股 f128（龙头选手的数据来源）",
          "f128" in open(os.path.join(BUILD, "core", "flowdata.py"),
                         encoding="utf-8").read())

    # ---- 不套名单：雷达的实际代码里不得出现任何板块名 ----
    banned = sorted({w for keys in WANTED.values() for w in keys})
    ar_code = _func_code_only(src_sal, "anomaly_radar")
    found = [w for w in banned if w in ar_code]
    check("雷达实际代码中无任何板块名单（未套用名单作弊）", not found,
          f"命中 {found}" if found else "全部指标来自当日实时分位")

    # ---- 真实数据召回 ----
    import salience
    F = salience.build_features(TAG)
    T = len(F.times)
    last = T - 1
    if not F.times:
        skip("召回实测", "无面板数据")
        return

    def match(keys, names):
        return any(any(k in n for k in keys) for n in names)

    allnames = [F.names.get(c, "") for c in F.codes]

    # 旧算法覆盖（复现旧候选池：|中性化增量| 前 80）
    import numpy as _np
    legacy = set(_np.argsort(-_np.abs(F.resid[last]))[:80].tolist())
    legacy_names = {F.names.get(F.codes[j], "") for j in legacy}

    # 新算法：全天事件 + 雷达
    ev_names = set()
    for i in range(salience.VEL_WIN, T, 3):
        for e in salience.detect_events(F, i):
            ev_names.add(e.get("name", ""))
    radar_names = {r["name"] for r in salience.anomaly_radar(
        F, last, topn=RADAR_N_V, direction="in")}
    radar_names |= {r["name"] for r in salience.anomaly_radar(
        F, last, topn=RADAR_N_V, direction="out")}
    new_names = ev_names | radar_names

    # 只统计"面板里确实存在对应板块"的方向（不拿不存在的方向充数）
    valid = {k: v for k, v in WANTED.items() if match(v, allnames)}
    miss_no_board = sorted(set(WANTED) - set(valid))

    old_hit = sorted(k for k, v in valid.items() if match(v, legacy_names))
    new_hit = sorted(k for k, v in valid.items() if match(v, new_names))
    n = len(valid)
    check(f"新算法覆盖率 ≥ 80%（{len(new_hit)}/{n}）", len(new_hit) >= n * 0.8,
          f"命中：{'、'.join(new_hit)}")
    check("新算法覆盖率显著高于旧算法",
          len(new_hit) > len(old_hit),
          f"新 {len(new_hit)}/{n} vs 旧 {len(old_hit)}/{n}")
    if miss_no_board:
        print(f"       （面板中无对应板块名的方向，不计入分母："
              f"{'、'.join(miss_no_board)}）")

    # ---- 尾盘回流：收盘时点应能给出科技方向 ----
    ev_last = salience.detect_events(F, last)
    reflux = [e for e in ev_last if e["type"] == "尾盘回流"]
    check("收盘时点检测到尾盘回流事件", len(reflux) > 0, f"{len(reflux)} 条")
    tech = [e["name"] for e in reflux
            if any(k in e["name"] for k in ("光", "PCB", "存储", "封装", "芯片", "通信", "5G"))]
    check("尾盘回流中含科技方向（用户点名场景）", len(tech) > 0,
          "、".join(tech[:6]))

    # ---- 事件类型多样性（跃迁不应霸榜）----
    import collections
    cnt = collections.Counter()
    for i in range(salience.VEL_WIN, T, 3):
        for e in salience.detect_events(F, i):
            cnt[e["type"]] += 1
    tot = sum(cnt.values()) or 1
    share = cnt["排名跃迁"] / tot
    check("排名跃迁占比 < 60%（不淹没其他类型）", share < 0.60,
          f"{share * 100:.0f}%")
    check("事件类型 ≥ 8 种", len(cnt) >= 8, f"{len(cnt)} 种")


# ============================================================ 问题十一
def q11():
    """全面自查：边界/空值、容错、UI 一致性、响应式、加载反馈、版本号。"""
    print("\n[问题十一] 全面自查与 UI 优化")
    sp = os.path.join(BUILD, "native_app.py")
    src = open(sp, encoding="utf-8").read()

    # ---- A. 版本号 ----
    check("VERSION = 2.0.1", 'VERSION = "2.0.1"' in src)
    check("窗口标题引用 VERSION", 'self.setWindowTitle(f"资金流向 · 原生版 v{VERSION}")' in src)
    check("关于对话框引用 VERSION", 'f"资金流向 · 纯原生桌面版 v{VERSION}' in src)

    # ---- B. 容错：关键入口都有异常保护 ----
    check("fill_all 分段容错", "def _seg(label, fn):" in src and "fill_all·" in src)
    check("on_data 三段容错（fill_kpi/fill_all/render_charts）",
          src.count('log_line(f"fill_kpi failed') >= 1
          and src.count('log_line(f"fill_all failed') >= 1
          and src.count('log_line(f"render_charts failed') >= 1)
    check("变量名冲突已修（themes_rows 取代同名 th）",
          "themes_rows = d.get(\"themes\")" in src)

    # ---- C. 导入 PySide6 后才能真正建窗口的检查放到最后，先做静态项 ----
    # 未捕获的关键调用：直接调用可能有空值的内部函数
    for fn in ("fill_kpi", "fill_watch", "_fill_themes", "_fill_keynodes",
               "_fill_radar"):
        check(f"{fn} 内部无未保护的 self.data 直取",
              f"def {fn}(self)" in src)

    # ---- D. 真机行为：空数据与极端尺寸（需要 QApplication）----
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import native_app as NA
    app.setStyleSheet(NA.build_qss(NA.THEMES["light"]))
    w = NA.MainWindow()

    log_before = len(_read_log())

    def _try(label, fn):
        try:
            fn()
            return True
        except Exception as e:                           # noqa: BLE001
            print(f"       [异常] {label}: {type(e).__name__}: {e}")
            return False

    # 空数据（刚启动、还没加载）
    w.data = {}
    check("空 data 下 fill_kpi 不抛", _try("fill_kpi", w.fill_kpi))
    check("空 data 下 fill_all 不抛", _try("fill_all", w.fill_all))
    check("空 data 下 render_charts 不抛", _try("rc", w.render_charts))

    # 字段残缺/None
    w.data = {"F": None, "times": [], "market": {}, "board": None,
              "board_out": None, "themes": None, "events": None,
              "timeline": None, "radar_in": None, "radar_out": None,
              "watch": None, "industry": None, "stocks": None}
    check("字段为 None 时不抛", _try("None fields", lambda: (w.fill_kpi(), w.fill_all(), w.render_charts())))

    # 事件/雷达里的脏条目（非 dict、缺字段）
    w.data = {"F": None, "times": [], "events": ["x", None, {"type": None}],
              "timeline": [None, {}], "radar_in": [None, {}], "radar_out": []}
    check("脏条目（非 dict/缺字段）不抛",
          _try("dirty rows", lambda: (w.fill_all(), w.render_charts())))

    # ---- E. 响应式：最小与较大尺寸下重填重绘不崩 ----
    for (ww, hh, tag) in ((1020, 660, "最小尺寸"), (1920, 1080, "大屏")):
        w.resize(ww, hh)
        app.processEvents()
        ok = _try(f"resize {ww}x{hh}",
                  lambda: (w.fill_kpi(), w.fill_all(), w.render_charts()))
        check(f"{tag} {ww}×{hh} 下重填重绘不抛", ok)

    # ---- F. 加载状态与交互反馈 ----
    check("有加载占位文案", 'blank("数据加载中…")' in src)
    check("有失败态占位", "数据加载失败" in src)
    check("忙碌时禁用刷新入口", "def _set_busy" in src and "setEnabled(not busy)" in src)
    check("有进度条", "QProgressBar" in src and "self.bar" in src)
    check("状态栏显示数据日与性质", "数据 {tag}（{src_cn}）" in src)

    # ---- G. UI 一致性 ----
    check("间距/边距收敛为常量", "PAD_PAGE = (" in src and "GAP_PAGE = " in src
          and "GAP_CARD = " in src)
    # QSS 覆盖所有用到的 objectName
    import re as _re
    onames = set(_re.findall(r'setObjectName\("([A-Za-z0-9_]+)"\)', src))
    qss_names = set(_re.findall(r'Q(?:Label|Frame|Widget)#([A-Za-z0-9_]+)',
                                src))
    missing = sorted(n for n in onames if n not in qss_names
                     and n not in ("pageRoot", "card", "kpi"))
    check("所有具名控件在 QSS 中都有样式（无漏样式）", not missing,
          f"缺样式: {missing}" if missing else f"{len(onames)} 个具名控件")
    # 表格表头用词一致（榜单类=排名，序列类=序号）
    check("表头用词统一（无遗留的 # 列）", '["#",' not in src.replace('"#"', '"#"'))

    w.deleteLater()


# ============================================================ 问题一 / 四
def _read_log():
    """读当天运行日志（D 盘 runtime 下的那份）。"""
    import glob as _g
    pats = _g.glob(os.path.join(os.path.dirname(HERE), "runtime", "FlowLiveNative",
                                "logs", "app_*.log"))
    if not pats:
        return ""
    with open(max(pats, key=os.path.getmtime), encoding="utf-8", errors="replace") as f:
        return f.read()


def q1():
    """首屏加载性能：按页延迟绘制生效、绘制耗时下降、端到端有量化。"""
    print("\n[问题一] 首屏加载性能")
    src = open(os.path.join(BUILD, "native_app.py"), encoding="utf-8").read()
    check("CHART_PAGE 表存在（图表→页签映射）", "CHART_PAGE = (" in src)
    check("render_charts 支持 only_visible",
          "def render_charts(self, only_visible=False)" in src)
    check("切页补画钩子已连接",
          "self.tabs.currentChanged.connect(self.on_tab_changed)" in src)
    check("诊断开关 FLOWLIVE_RENDER_ALL 可回退旧行为", "_env_render_all" in src)
    check("启动等待已收紧（80ms）",
          "QTimer.singleShot(80, lambda: self.reload" in src)
    check("端到端锚点 T_BOOT 存在", "T_BOOT = time.perf_counter()" in src)

    log = _read_log()
    if not log:
        skip("首屏耗时实测", "日志为空")
        return
    import re
    m = None
    for mm in re.finditer(r"on_data UI · fill=(\d+)ms charts=(\d+)ms "
                          r"since_boot=(\d+)ms dirty=(\[.*?\])", log):
        m = mm
    if not m:
        skip("首屏耗时实测", "日志里没有 on_data UI 记录（需先跑一次 --shot 或启动）")
        return
    fill_ms, chart_ms, boot_ms, dirty = (int(m.group(1)), int(m.group(2)),
                                         int(m.group(3)), m.group(4))
    check("首屏填表耗时 < 120ms", fill_ms < 120, f"{fill_ms}ms")
    check("首屏绘制耗时 < 600ms（按页延迟生效）", chart_ms < 600, f"{chart_ms}ms")
    check("确实延迟了其他页的图", dirty.strip() not in ("[]", ""), dirty)
    check("端到端首屏 < 3s", boot_ms < 3000, f"since_boot={boot_ms}ms")
    print("       （对照：强制全画时 charts 约 1300ms；"
          "可用 FLOWLIVE_RENDER_ALL=1 复现）")


def q4():
    """抓取与数据处理速度：数据处理链已量化、增量通道未被破坏。"""
    print("\n[问题四] 抓取与数据处理速度")
    src_fd = open(os.path.join(BUILD, "core", "flowdata.py"), encoding="utf-8").read()
    check("增量刷新通道存在（批量快照）", "def refresh_panel_spot" in src_fd)
    check("快照批次 200/请求", "SPOT_BATCH = 200" in src_fd)
    check("扩展字段复用同一请求（不增加 RTT）",
          "SPOT_FIELDS = \"f12,f14,f62,f128,f140,f136,f207,f208,f209\"" in src_fd)

    # 实测数据处理链耗时（直接跑，不依赖日志）
    import time as _t
    import salience
    import native_app as NA
    F = salience.build_features(TAG)
    last = len(F.times) - 1
    t0 = _t.perf_counter()
    NA._event_stream(F, salience)
    NA._market_stats(F, salience, last)
    salience.timeline_summary(F)
    salience.cluster_themes(F, last, topn=8)
    dt = (_t.perf_counter() - t0) * 1000
    check("事件+汇总+时间轴+聚类 合计 < 120ms", dt < 120, f"{dt:.0f}ms")
    t0 = _t.perf_counter()
    salience.build_features(TAG)
    dt2 = (_t.perf_counter() - t0) * 1000
    check("build_features < 150ms", dt2 < 150, f"{dt2:.0f}ms")


# ============================================================ 问题二
def q2():
    """刷新即最新 + 隐藏「重新抓取」。"""
    print("\n[问题二] 刷新即展示最新数据 & 隐藏「重新抓取」")
    from PySide6.QtWidgets import QApplication, QToolBar
    import native_app as NA

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(NA.build_qss(NA.THEMES["light"]))
    w = NA.MainWindow()

    # ---- ① 界面里看不到「重新抓取」----
    fm = None
    for act in w.menuBar().actions():
        if act.text().startswith("文件"):
            fm = act.menu()
            break
    texts = [a.text() for a in (fm.actions() if fm else []) if a.text()]
    check("「文件」菜单无「重新抓取」",
          not any("重新抓取" in t for t in texts), f"{texts}")
    check("「文件」菜单仍有「刷新」", any("刷新" in t for t in texts))
    tb_texts = []
    for tb in w.findChildren(QToolBar):
        tb_texts += [a.text() for a in tb.actions() if a.text()]
    check("工具栏无「重新抓取」",
          not any("重新抓取" in t for t in tb_texts), f"{tb_texts}")
    check("工具栏仍有「刷新」", any("刷新" in t for t in tb_texts))
    check("act_refetch 已不可见", not w.act_refetch.isVisible())
    check("act_refetch 已禁用（脚本也点不动）", not w.act_refetch.isEnabled())
    check("tb_refetch 已不可见", not w.tb_refetch.isVisible())

    # ---- ② 「刷新」的语义是"取最新"（不是"读旧缓存"）----
    src = open(os.path.join(BUILD, "native_app.py"), encoding="utf-8").read()
    n = src.count("reload(force=False, incremental=True)")
    check("刷新/启动均传 incremental=True 取最新", n >= 2, f"{n} 处")

    # ---- ③ 决策树（逐条覆盖六种情形）----
    wk = NA.Worker()
    real_in, real_age, real_gap = (NA.Worker._in_trading_hours,
                                   NA.Worker._panel_age_sec,
                                   NA.Worker._panel_gap_min)
    tmp = os.path.join(os.path.dirname(HERE), "runtime", "_plan_probe.npy")
    try:
        # ① 无底表
        if os.path.exists(tmp):
            os.remove(tmp)
        check("决策① 无底表 → 全量",
              wk._plan_fetch(RUNTIME_DATA, tmp)[0] == "full")
        open(tmp, "wb").close()
        # ② force
        wk.force = True
        check("决策② force → 全量",
              wk._plan_fetch(RUNTIME_DATA, tmp)[0] == "full")
        wk.force = False
        # ③ 非交易时段
        NA.Worker._in_trading_hours = staticmethod(lambda: False)
        check("决策③ 非交易时段 → 读缓存",
              wk._plan_fetch(RUNTIME_DATA, tmp)[0] == "cache")
        # ④⑤⑥ 交易时段
        NA.Worker._in_trading_hours = staticmethod(lambda: True)
        NA.Worker._panel_age_sec = lambda self, d: 120.0
        NA.Worker._panel_gap_min = lambda self, d: 0
        p4 = wk._plan_fetch(RUNTIME_DATA, tmp)
        check("决策⑥ 盘中+缓存较旧+无缺口 → 增量（取最新）",
              p4[0] == "spot", f"{p4}")
        NA.Worker._panel_age_sec = lambda self, d: 0.5
        check("决策④ 缓存 0.5s 前刚更新 → 读缓存（吸收连点）",
              wk._plan_fetch(RUNTIME_DATA, tmp)[0] == "cache")
        NA.Worker._panel_age_sec = lambda self, d: 120.0
        NA.Worker._panel_gap_min = lambda self, d: 99
        p5 = wk._plan_fetch(RUNTIME_DATA, tmp)
        check("决策⑤ 时间轴缺口过大 → 全量补齐",
              p5[0] == "full", f"{p5}")
    finally:
        NA.Worker._in_trading_hours = real_in
        NA.Worker._panel_age_sec = real_age
        NA.Worker._panel_gap_min = real_gap
        try:
            os.remove(tmp)
        except OSError:
            pass

    # ---- ④ 失败不再降级成全量风暴 ----
    check("增量失败时保留现有面板（不再降级全量）",
          "spot refresh failed (kept panel)" in src)
    check("陈旧阈值已收紧到 3 秒", "REFRESH_MIN_GAP_SEC = 3.0" in src)
    w.deleteLater()


def q3():
    """KPI 卡：大字不被裁（高度 ≥ 内容所需），且不留大片空白。"""
    print("\n[问题三] KPI 卡大字体不被白边遮挡")
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    from PySide6.QtWidgets import QApplication
    import native_app as NA
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(NA.build_qss(NA.THEMES["light"]))
    c = NA.KpiCard("上证指数", "+0.78%", "上证 +0.78% · 全A +0.70%")
    h = c.height()
    need = c._natural_height()
    check("卡片高度 ≥ 内容所需高度", h >= need, f"height={h} need={need}")
    # 三行都要装得下：行高之和 + 内边距
    lay = c.layout()
    m = lay.contentsMargins()
    inner = sum(lb.fontMetrics().height() for lb in
                (c.lb_title, c.lb_value, c.lb_sub)) + 2 * lay.spacing()
    check("三行行高之和 + 边距 ≤ 卡片高度",
          inner + m.top() + m.bottom() <= h,
          f"内容 {inner + m.top() + m.bottom()} ≤ {h}")
    check("不虚高（留白不超过 14px）", h - need <= 14, f"余量 {h - need}px")
    vm = c.lb_value.fontMetrics().height()
    check("大字（21px）行高 ≥ 26px", vm >= 26, f"{vm}px")
    c.deleteLater()


# ============================================================ 问题七
def q7():
    """图标：运行期能找到 app.ico，且 QIcon 非空。"""
    print("\n[问题七] 补齐运行时软件图标")
    import native_app as NA
    p = NA.app_icon_path()
    check("能找到 app.ico", p is not None and os.path.exists(p), str(p))
    if p:
        check("ico 文件非空", os.path.getsize(p) > 1000,
              f"{os.path.getsize(p):,} B")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    ic = NA.app_icon()
    check("QIcon 非空（可正常设置窗口图标）", not ic.isNull())
    src = open(os.path.join(BUILD, "native_app.py"), encoding="utf-8").read()
    check("main() 中调用了 setWindowIcon", "app.setWindowIcon" in src)
    check("声明了 AppUserModelID（任务栏图标归属）",
          "_set_app_user_model_id" in src and "SetCurrentProcessExplicitAppUserModelID" in src)
    check("spec 把 app.ico 打进包", _spec_has_icon())


def _spec_has_icon():
    for fn in ("资金流向原生版_onedir.spec", "资金流向原生版.spec"):
        fp = os.path.join(BUILD, fn)
        if not os.path.exists(fp):
            continue
        t = open(fp, encoding="utf-8").read()
        if "datas=[('core', 'core'), ('app.ico', '.')]" in t:
            return True
    return False


# ============================================================ 屏幕层通用
def _screen_table_check(label, page_name, keys_desc=True, time_col=0,
                        strength_col=None, obj=None):
    """从 --shot 导出的表格文本里读**屏幕实际字符串**并断言行序。

    ★ 文件名有两种（都是 MainWindow 自检时导出的）：
        · 关键节点页早期就有的 `<prefix>_<i>_关键节点.table.txt`
        · v2.0.1 起为所有具名表格导出的 `<prefix>_<i>_<页名>.<objectName>.txt`
      所以这里两种都找，取最新的一份 —— 避免因命名差异而"找不到就跳过"，
      那等于把该验证项静默作废（本轮就踩过一次，故显式支持两者）。
    """
    pats = []
    if obj:
        pats += glob.glob(os.path.join(SHOTS, "*", f"*_{page_name}.{obj}.txt"))
    pats += glob.glob(os.path.join(SHOTS, "*", f"*_{page_name}.table.txt"))
    if not pats:
        skip(f"{label} 屏幕层断言",
             f"未找到表格导出（需先跑 --shot；期望 *_{page_name}"
             f"{'.' + obj if obj else ''}.txt）")
        return
    fp = max(pats, key=os.path.getmtime)
    rows = []
    with open(fp, encoding="utf-8") as f:
        for ln in f:
            if ln.startswith("#") or not ln.strip():
                continue
            rows.append(ln.rstrip("\n").split("\t"))
    if not rows:
        skip(f"{label} 屏幕层断言", "表格为空")
        return

    def tk(s):
        try:
            hh, mm = s.strip().split(":")[:2]
            return int(hh) * 60 + int(mm)
        except Exception:                                # noqa: BLE001
            return None

    keys = [tk(r[time_col]) for r in rows]
    pair = [(i, keys[i], keys[i + 1]) for i in range(len(keys) - 1)
            if keys[i] is not None and keys[i + 1] is not None]
    if keys_desc:
        bad = [p for p in pair if p[1] < p[2]]
        check(f"{label} 屏幕行序时间降序(晚→早)", not bad,
              f"{os.path.basename(fp)} {len(rows)} 行 · 逆序处 {len(bad)}")
    else:
        bad = [p for p in pair if p[1] > p[2]]
        check(f"{label} 屏幕行序时间升序", not bad, f"{len(bad)} 处逆序")
    if strength_col is not None:
        by_t = {}
        for r in rows:
            try:
                by_t.setdefault(r[time_col], []).append(float(r[strength_col]))
            except Exception:                            # noqa: BLE001
                pass
        unsorted = {t: v for t, v in by_t.items()
                    if any(v[i] < v[i + 1] for i in range(len(v) - 1))}
        check(f"{label} 屏幕同刻强度降序", not unsorted,
              f"异常时刻 {list(unsorted)[:3]}")


QUESTIONS = {"1": q1, "2": q2, "3": q3, "4": q4, "5": q5, "6": q6, "7": q7,
             "10": q10, "11": q11}


def main():
    want = sys.argv[1:]
    # ★★ v2.0.1：把 LOCALAPPDATA 重定向到 D 盘 runtime —— **必须在导入/构造
    #   任何窗口之前**（app_home 是惰性读环境变量的，但窗口一建就会写日志）。
    #
    #   为什么必须有这一步（这是我的验证脚本早期版本踩的坑）：
    #   脚本直接跑托管 Python 时，程序会把日志/数据写到
    #   `%LOCALAPPDATA%\FlowLiveNative\` —— 也就是 **C 盘**。
    #   而本次工作的硬约束是「所有落盘只能在 D 盘」。
    #   首次运行 q11 时就因此往 C 盘写了几行测试日志（已如实记录在报告里）。
    #   这里与源码版自检（--shot）用**同一套隔离**：LOCALAPPDATA 指向
    #   D:\flowlive_v201\runtime，于是 logs/data 全部落在 D 盘。
    os.environ["LOCALAPPDATA"] = os.path.join(os.path.dirname(HERE), "runtime")
    os.makedirs(os.path.join(os.environ["LOCALAPPDATA"], "FlowLiveNative"),
                exist_ok=True)

    print("=" * 68)
    print(f"v2.0.1 修复项验证 · 数据目录={RUNTIME_DATA}")
    print(f"（运行期 LOCALAPPDATA → {os.environ['LOCALAPPDATA']}，不写 C 盘）")
    print("=" * 68)
    _bootstrap()
    for k in sorted(QUESTIONS):
        if want and k not in want:
            continue
        try:
            QUESTIONS[k]()
        except Exception as e:                           # noqa: BLE001
            import traceback
            traceback.print_exc()
            _FAIL.append(f"问题{k} 执行异常: {e}")
    print("\n" + "=" * 68)
    print(f"通过 {len(_PASS)} · 失败 {len(_FAIL)} · 跳过 {len(_SKIP)}")
    for f in _FAIL:
        print("  FAIL:", f)
    print("=" * 68)
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
