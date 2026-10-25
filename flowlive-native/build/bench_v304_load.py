# -*- coding: utf-8 -*-
"""高频刷新负载分析：把刷新周期降到 1~10 秒，谁会先崩？

★ 目的：用实测的**每次刷新的真实成本**去推演不同周期下的 CPU 占用，
  找出"降到多少秒会开始出问题"，而不是盲目把定时器调快。

★ 关键洞察（本脚本要验证的）：
   刷新不是"一次请求"，而是**一条链**：
     采集(网络) → 聚合(CPU) → 图表重绘(CPU+GPU) → 表格重建(CPU)
   只要**任何一环**的耗时接近刷新周期，就会出现"上一轮没跑完、下一轮又来"，
   表现为界面持续卡顿、定时器堆积。所以要看**链的总时长**，不是单点。
"""
import os
import sys
import time
import json
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_CANDS = [
    os.path.join(os.path.dirname(HERE), "runtime", "FlowLiveNative", "data"),
    os.path.join(os.path.dirname(HERE), "backup_v126", "data"),
]
DATA = next((d for d in _CANDS if os.path.isdir(d)), _CANDS[0])


def hr(t):
    print("\n" + "=" * 76)
    print(t)
    print("=" * 76)


hr("高频刷新负载推演")

import flowdata                                                          # noqa: E402
flowdata.set_data_dir(DATA)

# ---------------------------------------------------------------- 实测: 各环耗时
hr("A. 逐环实测")

# A1 采集（打桩 60ms RTT，1000 板块）
REAL_RTT = 0.060
log = {"n": 0}
_orig = flowdata._get


def _stub(path_or_url, tries=3, timeout=15):
    time.sleep(REAL_RTT)
    log["n"] += 1
    return {"data": {"diff": [{"f12": "BK%04d" % (log["n"] % 900 + 100),
                              "f14": "板块%d" % log["n"], "f62": 1.0e7,
                              "f128": "A", "f140": "600001", "f136": 1.0,
                              "f207": "B", "f208": "600002"}]}}


codes = ["90.BK%04d" % (1000 + i) for i in range(1000)]
flowdata._get = _stub
log["n"] = 0
t0 = time.time()
flowdata.fetch_spot_batch(codes, detail=True)
t_serial = (time.time() - t0) * 1000
n_req = log["n"]
flowdata._get = _orig
print("  A1 采集(串行 %d 请求 @%.0fms RTT)   %8.1f ms" % (n_req, REAL_RTT * 1000, t_serial))

# 理论并行下限：ceil(n/并发) × RTT
for w in (3, 5, 6, 10):
    par = ((n_req + w - 1) // w) * REAL_RTT * 1000
    print("     并行 w=%-2d 的理论下限            %8.1f ms  (%.1fx 提速)"
          % (w, par, t_serial / par))

# A2 聚合
import salience                                                          # noqa: E402
samples = []
for _ in range(5):
    t0 = time.time()
    F = salience.build_features("today")
    samples.append((time.time() - t0) * 1000)
t_proc = statistics.median(samples)
print("  A2 聚合 build_features              %8.1f ms" % t_proc)

# A3/A4 渲染与表格
from PySide6.QtWidgets import (QApplication, QTableWidget, QTableWidgetItem,   # noqa: E402
                               QHeaderView, QAbstractItemView)
from PySide6.QtGui import QColor                                              # noqa: E402
import matplotlib                                                             # noqa: E402
matplotlib.use("QtAgg")
from matplotlib.figure import Figure                                          # noqa: E402
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg               # noqa: E402
import numpy as np                                                            # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


def make_canvas(n_lines):
    fig = Figure(figsize=(10, 4), dpi=100)
    ax = fig.add_subplot(111)
    x = np.arange(240)
    for i in range(n_lines):
        ax.plot(x, np.cumsum(np.random.randn(240)), lw=1.0)
    ax.set_xlim(0, 239)
    c = FigureCanvasQTAgg(fig)
    c.resize(1000, 400)
    c.draw()
    return c, ax


# 单图整幅重绘
c1, _ = make_canvas(24)
s = []
for _ in range(12):
    t0 = time.time(); c1.draw(); s.append((time.time() - t0) * 1000)
t_draw1 = statistics.median(s)
print("  A3 单图整幅重绘 (24线×240点)          %8.1f ms" % t_draw1)

# 6 图（真实页面规模）整幅重绘
canvases = [make_canvas(k) for k in (24, 2, 12, 8, 6, 10)]
s = []
for _ in range(8):
    t0 = time.time()
    for c, _ax in canvases:
        c.draw()
    s.append((time.time() - t0) * 1000)
t_draw6 = statistics.median(s)
print("  A3' 六图整幅重绘（全页）              %8.1f ms" % t_draw6)

# 表格
tbl = QTableWidget(40, 7)
tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
s = []
for k in range(15):
    t0 = time.time()
    tbl.setRowCount(0); tbl.setRowCount(40)
    for i in range(40):
        for j in range(7):
            tbl.setItem(i, j, QTableWidgetItem("%d" % (k * i * j)))
    s.append((time.time() - t0) * 1000)
t_tbl = statistics.median(s)
print("  A4 表格清空重建 (40×7)                %8.1f ms" % t_tbl)

# ---------------------------------------------------------------- 推演
hr("B. 不同刷新周期下的负载推演")
print()
print("  假设：每次刷新 = 采集 + 聚合 + 重绘 + 表格")
print("  场景① 全量重绘（最坏，所有页都重画）")
print("  场景② 仅当前页重绘（现状 only_visible=True，只画 1~2 张图）")
print()

chain_full = t_serial + t_proc + t_draw6 + t_tbl
chain_page = t_serial + t_proc + t_draw1 + t_tbl
# 优化后：采集并行 w=6 + 单页重绘
t_serial_opt = ((n_req + 5) // 6) * REAL_RTT * 1000
chain_opt = t_serial_opt + t_proc + t_draw1 + t_tbl

print("  %-34s %10s" % ("链路", "总耗时"))
print("  " + "-" * 46)
print("  %-34s %8.1f ms" % ("① 现状·全量重绘", chain_full))
print("  %-34s %8.1f ms" % ("② 现状·仅当前页", chain_page))
print("  %-34s %8.1f ms" % ("③ 优化后·采集并行+仅当前页", chain_opt))
print()

print("  %-8s %12s %12s %12s" % ("刷新周期", "①占空比", "②占空比", "③占空比"))
print("  " + "-" * 50)
for ms in (1000, 2000, 3000, 5000, 10000):
    a = chain_full / ms * 100
    b = chain_page / ms * 100
    c = chain_opt / ms * 100
    flag = "  ← 危险" if c > 50 else ("  ← 偏重" if c > 25 else "")
    print("  %-8s %11.1f%% %11.1f%% %11.1f%%%s" % ("%d s" % (ms // 1000), a, b, c, flag))
print()
print("  ★ 判据：占空比 > 50% 时，界面会持续卡顿（没有空闲处理输入事件）；")
print("     > 25% 时开始能感觉到迟滞，尤其是拖窗口/切页时。")
print("     占空比 = 单轮链路耗时 / 刷新周期。")

hr("C. 结论")
print("""
  1. 采集（%.0fms）是链路里**最大的一块**，且 100%% 可并行压缩到 ~%.0fms。
  2. 全量重绘六图（%.0fms）在 1 秒周期下占 %.0f%%，**必须避免每轮都全量重绘**。
  3. 聚合（%.1fms）与表格（%.1fms）在 ms 级，**不是瓶颈**，不必动用复杂手段。
  4. 结论：要做的是「采集并行化」+「重绘按脏区/可见页」，而不是"优化 numpy"。
""" % (t_serial, t_serial_opt, t_draw6, t_draw6 / 1000 * 100, t_proc, t_tbl))

out = {"t_serial": t_serial, "t_serial_opt": t_serial_opt, "t_proc": t_proc,
       "t_draw1": t_draw1, "t_draw6": t_draw6, "t_tbl": t_tbl,
       "chain_full": chain_full, "chain_page": chain_page, "chain_opt": chain_opt}
with open(os.path.join(HERE, "_load_v304.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("已写 _load_v304.json")
