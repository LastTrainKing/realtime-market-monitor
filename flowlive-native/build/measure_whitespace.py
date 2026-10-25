# -*- coding: utf-8 -*-
"""v3.0.1 留白测量 —— 用程序量化每个子页的空白占比，而不是靠眼睛估。

做法：离屏渲染每个子页到 PNG，统计"接近背景色"的像素占比。
输出按子页排序的留白率表，用来决定压哪里、压多少。

★ 用真实截图而不是猜：本项目既有教训「先量化再动手」。
"""
import importlib.util as ilu
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

spec = ilu.spec_from_file_location("native_app", os.path.join(HERE, "app_main_v203.py"))
NA = ilu.module_from_spec(spec)
sys.modules["native_app"] = NA
spec.loader.exec_module(NA)
NA.ensure_mpl()

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap
from PySide6.QtCore import QTimer

app = QApplication.instance() or QApplication(sys.argv)
win = NA.MainWindow()
W, H = 1480, 940
win.resize(W, H)
win.show()
app.processEvents()

OUT = os.path.join(HERE, "_whitespace_shots")
os.makedirs(OUT, exist_ok=True)

targets = []          # (母模块, 子页名 or None)
for i in range(win.tabs.count()):
    g = win.tabs.tabText(i)
    if g in win.group_tabs:
        for j in range(win.group_tabs[g].count()):
            targets.append((i, g, j, win.group_tabs[g].tabText(j)))
    else:
        targets.append((i, g, None, None))

print("%-10s %-10s %8s %8s %8s" % ("母模块", "子模块", "留白px", "总px", "留白率"))
print("-" * 50)
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
    p = os.path.join(OUT, "%s_%s.png" % (g, sname or g))
    pm.save(p)

    # 统计留白：与四角背景色相近的像素
    img = pm.toImage()
    w, h = img.width(), img.height()
    # 取右下角内 3px 处作为背景参考
    bg = img.pixelColor(w - 4, h - 4)
    blank = 0
    step = 2                     # 隔点采样，够用且快
    tot = 0
    for y in range(0, h, step):
        for x in range(0, w, step):
            c = img.pixelColor(x, y)
            tot += 1
            if (abs(c.red() - bg.red()) < 6 and abs(c.green() - bg.green()) < 6
                    and abs(c.blue() - bg.blue()) < 6):
                blank += 1
    ratio = blank / max(tot, 1)
    rows.append((g, sname or g, ratio))
    print("%-10s %-10s %8d %8d %7.1f%%" % (
        g, sname or g, blank, tot, ratio * 100))

print("\n按留白率降序：")
for g, s, r in sorted(rows, key=lambda x: -x[2]):
    print("  %5.1f%%  %s / %s" % (r * 100, g, s))
