# -*- coding: utf-8 -*-
"""找出既快又**列宽一致**的方案。
   候选：A 保持 ResizeToContents
        B 首次算好后转 Fixed（后续只 setItem）
        C setResizeContentsPrecision 调小
        D 全部 Fixed + 自己估宽"""
import os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QHeaderView
import native_app as NA
app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))

HDR = ["#", "题材", "净额(亿)", "5分钟(亿)", "辨识度"]
def rows(seed=0):
    return [[k, f"板块{k*7%50}名称示例{seed}", f"{'+' if k%2 else '-'}{k}.12",
             f"{'+' if k%3 else '-'}{k}.33", f"{k}.23"] for k in range(14)]

def med(fn, n=15):
    xs=[]
    for _ in range(n):
        t0=time.perf_counter(); fn(); xs.append((time.perf_counter()-t0)*1000)
    return sorted(xs)[len(xs)//2]

def build(mode, prec=None):
    t = NA.make_table(HDR, stretch_col=1, align={0:"c",1:"l"})
    t.resize(560,420); t.show(); app.processEvents()
    hh = t.horizontalHeader()
    if mode == "C" and prec is not None:
        hh.setResizeContentsPrecision(prec)
    NA.fill_table(t, rows(0)); app.processEvents()
    if mode == "B":
        # 首次按内容定宽，之后锁成 Fixed
        for c in range(t.columnCount()):
            if c != 1 and hh.sectionResizeMode(c) == QHeaderView.ResizeToContents:
                w = t.columnWidth(c)
                hh.setSectionResizeMode(c, QHeaderView.Fixed)
                t.setColumnWidth(c, w)
    return t

# --- 列宽对照：以 A（原样）为基准
ta = build("A"); wa = [ta.columnWidth(c) for c in range(5)]
print(f"  A 基准列宽 = {wa}")
tb = build("B"); wb = [tb.columnWidth(c) for c in range(5)]
print(f"  B 锁定列宽 = {wb}   {'一致 ✓' if wb==wa else '★不一致'}")
for p in (1, 3, 5):
    tc = build("C", p); print(f"  C prec={p} 列宽 = {[tc.columnWidth(c) for c in range(5)]}")
print()
tA = build("A"); tB = build("B"); tC = build("C", 3)
print(f"  A 原样 ResizeToContents        {med(lambda: NA.fill_table(tA, rows(1))):7.2f} ms")
print(f"  B 转为 Fixed 后 setItem        {med(lambda: NA.fill_table(tB, rows(1))):7.2f} ms")
print(f"  C prec=3                       {med(lambda: NA.fill_table(tC, rows(1))):7.2f} ms")
print()
# B 方案下，内容变长时列宽会不会不够？测极端
long_rows = [[k, f"超长题材名称第{k}号概念板块指数", f"{'+' if k%2 else '-'}{k*1111}.12",
              f"{'+' if k%3 else '-'}{k*999}.33", f"{k}.23"] for k in range(14)]
NA.fill_table(tB, long_rows); app.processEvents()
print(f"  B 灌入超长内容后列宽 = {[tB.columnWidth(c) for c in range(5)]}   （基准 {wa}）")
tA2 = build("A"); NA.fill_table(tA2, long_rows); app.processEvents()
print(f"  A 灌入超长内容后列宽 = {[tA2.columnWidth(c) for c in range(5)]}")
