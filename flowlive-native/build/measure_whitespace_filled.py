# -*- coding: utf-8 -*-
"""v3.0.1 留白测量（**有数据**版）—— 空态测不准，必须灌入合成数据再测。

【为什么要这个脚本】
  measure_whitespace.py 测的是"刚启动、一条数据都没有"的状态。那一版的结果
  极具误导性：
    · 表格全空 → 表内留白被当成"版面留白"，占比 16~28%；
    · 一旦把空表收缩（Maximum 策略），留白率反而涨到 77%（卡片内空出来）。
  真正的版面留白只能在**数据铺满**之后看 —— 因为实盘 09:30~15:00 大部分
  时间表格是有数据的，空表只在开盘前那几分钟出现。

【做法】
  构造一份结构与真实 payload 同形的合成数据（板块 60 条、行业 40 条、
  事件 30 条、题材若干），塞进 win.data，然后按页面依次调用
  fill_all / fill_open_pages，再截图统计留白。

★ 合成数据只用于**测版面**，不写入任何交付物；命名一律带"合成"字样，避免
  与真实数据混淆（本项目既有纪律：绝不把合成值当真实值）。
"""
import importlib.util as ilu
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

spec = ilu.spec_from_file_location("native_app", os.path.join(HERE, "app_main_v203.py"))
NA = ilu.module_from_spec(spec)
sys.modules["native_app"] = NA
spec.loader.exec_module(NA)
NA.ensure_mpl()

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

app = QApplication.instance() or QApplication(sys.argv)
win = NA.MainWindow()
W, H = 1480, 940
win.resize(W, H)
win.show()
app.processEvents()

rnd = random.Random(20260923)          # 固定种子 → 可复现

NAMES = ["合成题材%02d" % i for i in range(1, 61)]
INDS = ["合成一级行业%02d" % i for i in range(1, 41)]


def _row(i, name, net=None, d5=None, sal=None):
    net = net if net is not None else rnd.uniform(-30, 30)
    d5 = d5 if d5 is not None else rnd.uniform(-8, 8)
    sal = sal if sal is not None else rnd.uniform(0, 100)
    return {"name": name, "net": net, "d5": d5, "salience": sal}


board = [_row(i, n) for i, n in enumerate(NAMES[:30])]
board_out = [_row(i, n, net=-abs(rnd.uniform(1, 30))) for i, n in enumerate(NAMES[30:60])]
ind_in = [_row(i, n) for i, n in enumerate(INDS[:20])]
ind_out = [_row(i, n, net=-abs(rnd.uniform(1, 30))) for i, n in enumerate(INDS[20:40])]

times = ["%02d:%02d" % (9 + (30 + m) // 60, (30 + m) % 60) for m in range(0, 241, 1)]
sectors = []
for n in NAMES:
    series = []
    v = 0.0
    for _ in times:
        v += rnd.uniform(-0.5, 0.55)
        series.append(round(v, 3))
    sectors.append({"name": n, "series": series})

events = [{"time": times[rnd.randrange(len(times))], "type": rnd.choice(["拉升", "跳水", "放量"]),
           "name": rnd.choice(NAMES), "desc": "合成事件描述", "strength": rnd.uniform(1, 9)}
          for _ in range(30)]

themes = []
for i in range(8):
    themes.append({"name": "合成簇%d" % (i + 1),
                   "boards": rnd.sample(NAMES, 5),
                   "net": rnd.uniform(-40, 40),
                   "members": rnd.sample(NAMES, 6)})

win.data = {
    "times": times,
    "sectors": sectors,
    "board": board,
    "board_out": board_out,
    "industry": {"in": ind_in, "out": ind_out},
    "events": events,
    "themes": themes,
    "market": {
        "range_ra": [{"name": n, "net": rnd.uniform(1, 20), "days": 5} for n in NAMES[:10]],
        "range_sh": [{"name": n, "net": -rnd.uniform(1, 20), "days": 5} for n in NAMES[10:20]],
        "index": [100 + rnd.uniform(-1, 1) for _ in times],
        "all_a": [100 + rnd.uniform(-1, 1) for _ in times],
        "total": [rnd.uniform(-50, 50) for _ in times],
    },
    "date_tag": "2026-09-23",
}

try:
    win.fill_kpi()
except Exception as e:                                            # noqa: BLE001
    print("fill_kpi:", e)
try:
    win.fill_all()
except Exception as e:                                            # noqa: BLE001
    print("fill_all:", e)

# ★ 「开盘检测 / 个股异动 / 个股精选」不走 self.data，而是读当天快照
#   （core/opensnap.build_payload）。没有快照时它填"该节点未采集"占位行 ——
#   那样测出来的留白是**假留白**（表格只有 1~2 行自然留空）。
#   这里把 build_payload 打桩成"三个节点都采满"的合成 payload，
#   才能测到真实的版面占用。
try:
    import opensnap as _os

    def _fake_payload(tag, data_dir=None, topn_stock=10):
        nodes = {}
        for _m, _tr, _lab, key in _os.SNAP_NODES:
            nodes[key] = {
                "missing": False, "label": _lab, "captured_at": "09:%02d" % _m,
                "sector_vol_top5": [{"rank": i + 1, "name": NAMES[i % len(NAMES)],
                                     "vol_wan": rnd.uniform(1e4, 9e4),
                                     "amt_yi": rnd.uniform(1, 90)}
                                    for i in range(5)],
                "sector_net_top5": [{"rank": i + 1, "name": NAMES[i % len(NAMES)],
                                     "net_yi": rnd.uniform(-20, 20)}
                                    for i in range(5)],
                "stock_lb_top10": [{"rank": i + 1, "code": "00%04d" % i,
                                    "name": "合成股%02d" % i,
                                    "lb": rnd.uniform(1, 9), "vol_wan": rnd.uniform(1e3, 9e4),
                                    "net_yi": rnd.uniform(-5, 5), "pct": rnd.uniform(-9, 9)}
                                   for i in range(10)],
            }
            nodes[key]["stock_vol_top10"] = nodes[key]["stock_lb_top10"]
            nodes[key]["stock_net_top10"] = nodes[key]["stock_lb_top10"]
        sel = [{"rank": i + 1, "code": "00%04d" % i, "name": "合成股%02d" % i,
                "count": 9 if i < 5 else 3,
                "lists": ["开盘后5分钟量比", "开盘后10分钟成交量"],
                "net_yi": rnd.uniform(-5, 5), "all9": i < 5}
               for i in range(50)]
        return {"nodes": nodes, "select": sel}

    _os.build_payload = _fake_payload
except Exception as e:                                            # noqa: BLE001
    print("patch opensnap failed:", e)

try:
    win.fill_open_pages()
except Exception as e:                                            # noqa: BLE001
    print("fill_open_pages:", e)
app.processEvents()

OUT = os.path.join(HERE, "_whitespace_shots_filled")
os.makedirs(OUT, exist_ok=True)

targets = []
for i in range(win.tabs.count()):
    g = win.tabs.tabText(i)
    if g in win.group_tabs:
        for j in range(win.group_tabs[g].count()):
            targets.append((i, g, j, win.group_tabs[g].tabText(j)))
    else:
        targets.append((i, g, None, None))

print("%-10s %-12s %8s %8s %8s" % ("母模块", "子模块", "留白px", "总px", "留白率"))
print("-" * 52)
rows = []
for ti, g, sj, sname in targets:
    win.tabs.setCurrentIndex(ti)
    app.processEvents()
    if sj is not None:
        win.group_tabs[g].setCurrentIndex(sj)
        app.processEvents()
    QTimer.singleShot(0, lambda: None)
    app.processEvents()
    pm = win.grab()
    pm.save(os.path.join(OUT, "%s_%s.png" % (g, sname or g)))

    img = pm.toImage()
    w, h = img.width(), img.height()
    bg = img.pixelColor(w - 4, h - 4)
    blank = 0
    tot = 0
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            c = img.pixelColor(x, y)
            tot += 1
            if (abs(c.red() - bg.red()) < 6 and abs(c.green() - bg.green()) < 6
                    and abs(c.blue() - bg.blue()) < 6):
                blank += 1
    ratio = blank / max(tot, 1)
    rows.append((g, sname or g, ratio))
    print("%-10s %-12s %8d %8d %7.1f%%" % (g, sname or g, blank, tot, ratio * 100))

print("\n按留白率降序（有数据）：")
for g, s, r in sorted(rows, key=lambda x: -x[2]):
    print("  %5.1f%%  %s / %s" % (r * 100, g, s))
