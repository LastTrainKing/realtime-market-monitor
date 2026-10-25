# -*- coding: utf-8 -*-
"""on_fan_hover 里 42ms 的真实机制：ResizeToContents 每次 setItem 都重算列宽？"""
import os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QFont
from PySide6.QtWidgets import QApplication, QTableWidgetItem, QHeaderView
import probe_lag_e2e as P, native_app as NA

app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))
win, series = P.build_window(app)
t = win.t_hover

print("  列宽模式：")
hh = t.horizontalHeader()
for c in range(t.columnCount()):
    mode = hh.sectionResizeMode(c)
    print(f"    col{c}: {mode}")

def timed(label, fn, n=20):
    xs=[]
    for i in range(n):
        t0=time.perf_counter(); fn(i); xs.append((time.perf_counter()-t0)*1000)
    xs.sort()
    print(f"    {label:44s} 中位 {xs[len(xs)//2]:7.2f} ms  最快 {xs[0]:7.2f}")

NA.fill_table(t, [[i, f"板块{i}", f"+{i}.11", f"+{i}.33", "1.23"] for i in range(14)])
app.processEvents()

print()
print("  在**真实可见**的表格上测量：")
def full_fill(i):
    NA.fill_table(t, [[k, f"板块{k*7%50}名称", f"+{k}.{i%99:02d}", f"+{k}.33", f"{k}.23"] for k in range(14)])
timed("fill_table(14行) 完整重建", full_fill)

def inplace(i):
    for r in range(14):
        for c in range(5):
            it = t.item(r, c)
            if it is not None:
                it.setText(f"{r*7%50}.{i%99:02d}" if c==2 else (f"板块{r}名称" if c==1 else "1.23"))
timed("原地 setText（不重建 item）", inplace)

# 关掉 ResizeToContents 后再测完整重建
for c in range(t.columnCount()):
    if c != 1:
        hh.setSectionResizeMode(c, QHeaderView.Fixed)
timed("fill_table 完整重建（列宽改 Fixed）", full_fill)

def with_off(i):
    t.setUpdatesEnabled(False)
    NA.fill_table(t, [[k, f"板块{k*7%50}名称", f"+{k}.{i%99:02d}", f"+{k}.33", f"{k}.23"] for k in range(14)])
    t.setUpdatesEnabled(True)
timed("fill_table + setUpdatesEnabled(False)", with_off)
