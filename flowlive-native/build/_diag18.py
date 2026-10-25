# -*- coding: utf-8 -*-
"""方案验证：setUpdatesEnabled(False) 能否在**保留 ResizeToContents** 的前提下提速？
   并且列宽是否与原来**完全一致**？"""
import os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QTableWidgetItem, QHeaderView
import native_app as NA
app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))

ROWS = [[k, f"板块{k*7%50}名称示例", f"+{k}.12", f"+{k}.33", f"{k}.23"] for k in range(14)]
HDR = ["#", "题材", "净额(亿)", "5分钟(亿)", "辨识度"]

def widths(kind):
    t = NA.make_table(HDR, stretch_col=1, align={0:"c",1:"l"})
    t.resize(560, 420); t.show(); app.processEvents()
    if kind == "plain":
        NA.fill_table(t, ROWS)
    elif kind == "suppress":
        t.setUpdatesEnabled(False)
        NA.fill_table(t, ROWS)
        t.setUpdatesEnabled(True)
    elif kind == "suppress_resize":
        t.setUpdatesEnabled(False)
        NA.fill_table(t, ROWS)
        t.setUpdatesEnabled(True)
        t.resizeColumnsToContents()
    app.processEvents()
    return [t.columnWidth(c) for c in range(t.columnCount())]

w_plain = widths("plain")
w_supp  = widths("suppress")
w_sr    = widths("suppress_resize")
print(f"  plain           列宽 = {w_plain}")
print(f"  suppress        列宽 = {w_supp}    {'一致 ✓' if w_supp==w_plain else '★不一致'}")
print(f"  suppress+resize 列宽 = {w_sr}    {'一致 ✓' if w_sr==w_plain else '★不一致'}")
print()

def med(fn, n=15):
    xs=[]
    for _ in range(n):
        t0=time.perf_counter(); fn(); xs.append((time.perf_counter()-t0)*1000)
    return sorted(xs)[len(xs)//2]

t = NA.make_table(HDR, stretch_col=1, align={0:"c",1:"l"})
t.resize(560,420); t.show(); NA.fill_table(t, ROWS); app.processEvents()
print(f"  fill_table 原样                {med(lambda: NA.fill_table(t, ROWS)):7.2f} ms")
def sup():
    t.setUpdatesEnabled(False); NA.fill_table(t, ROWS); t.setUpdatesEnabled(True)
print(f"  fill_table + 抑制重绘           {med(sup):7.2f} ms")
def sup_r():
    t.setUpdatesEnabled(False); NA.fill_table(t, ROWS); t.setUpdatesEnabled(True)
    t.resizeColumnsToContents()
print(f"  fill_table + 抑制 + 末尾重算一次  {med(sup_r):7.2f} ms")
