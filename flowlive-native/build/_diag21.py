# -*- coding: utf-8 -*-
import os, sys, time, statistics
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QFont
from PySide6.QtWidgets import QApplication
import probe_lag_e2e as P, native_app as NA
app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))
win, series = P.build_window(app)
fc = win.ch_fan; fc._n = P.T; fc._times = P._times()
t = win.t_hover
hh = t.horizontalHeader()

# 先真实填一次表（真实运行里 on_fan_hover 会做这件事）
win.on_fan_hover(100, P._times()[100])
app.processEvents()
print("  填表后列宽模式 =", [hh.sectionResizeMode(c).name for c in range(t.columnCount())])
print("  _widths_frozen =", getattr(t, "_widths_frozen", False))
print("  列宽 =", [t.columnWidth(c) for c in range(t.columnCount())])
print()

st={"cb":[], "mark":[], "draw":[]}
_cb=win.on_fan_hover
def cb(i,hm):
    t0=time.perf_counter(); _cb(i,hm); st["cb"].append((time.perf_counter()-t0)*1000)
fc.hover_cb=cb
_m=fc._mark
def mark(k):
    t0=time.perf_counter(); _m(k); st["mark"].append((time.perf_counter()-t0)*1000)
fc._mark=mark
_d=fc.draw
def draw():
    t0=time.perf_counter(); _d(); st["draw"].append((time.perf_counter()-t0)*1000)
fc.draw=draw

fc._hover_px=None; app.processEvents()
N=200
t0=time.perf_counter()
for j in range(N):
    x = 60 + int((fc.width()-120)*j/max(1,N-1))
    app.sendEvent(fc, QMouseEvent(QEvent.MouseMove, QPointF(x,140), QPointF(x,140),
                                  Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    app.processEvents()
tot=(time.perf_counter()-t0)*1000
print(f"总 {tot:.0f} ms / {N} = {tot/N:.2f} ms/事件")
print(f"  _mark   n={len(st['mark'])} 合计={sum(st['mark']):7.1f} 中位={statistics.median(st['mark']) if st['mark'] else 0:.2f}")
print(f"  cb      n={len(st['cb'])} 合计={sum(st['cb']):7.1f} 中位={statistics.median(st['cb']) if st['cb'] else 0:.2f}")
print(f"  draw    n={len(st['draw'])} 合计={sum(st['draw']):7.1f}")
print(f"  未归因  = {tot-sum(st['mark'])-sum(st['cb'])-sum(st['draw']):7.1f} ms")
