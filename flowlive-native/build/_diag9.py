# -*- coding: utf-8 -*-
"""第三处：8 次全量 draw 是谁触发的？其余 38ms 去哪了？"""
import os, sys, time, statistics, traceback
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QFont
from PySide6.QtWidgets import QApplication
import probe_lag_e2e as P

app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))
win, series = P.build_window(app)
fc = win.ch_fan; fc._n = P.T; fc._times = P._times()

st = {"draw":[], "cb":[], "mark":[], "pe":[]}
_d = fc.draw
def draw():
    st["draw"].append(traceback.extract_stack()[-6:-1])
    t=time.perf_counter(); r=_d(); st.setdefault("dt",[]).append((time.perf_counter()-t)*1000); return r
fc.draw = draw
_cb = win.on_fan_hover
def cb(i,hm):
    t=time.perf_counter(); _cb(i,hm); st["cb"].append((time.perf_counter()-t)*1000)
fc.hover_cb = cb
_m = fc._mark
def mark(k):
    t=time.perf_counter(); _m(k); st["mark"].append((time.perf_counter()-t)*1000)
fc._mark = mark
_pe = app.processEvents
def pe(*a):
    t=time.perf_counter(); r=_pe(*a); st["pe"].append((time.perf_counter()-t)*1000); return r
app.processEvents = pe

fc._hover_px = None; app.processEvents()
N=200; t0=time.perf_counter()
for j in range(N):
    x = 60 + int((fc.width()-120) * j / max(1,N-1))
    app.sendEvent(fc, QMouseEvent(QEvent.MouseMove, QPointF(x,140), QPointF(x,140),
                                  Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    app.processEvents()
tot=(time.perf_counter()-t0)*1000
print(f"总 {tot:.0f} ms / {N} = {tot/N:.2f} ms/事件")
print(f"  _mark    n={len(st['mark'])} 合计={sum(st['mark']):7.1f} 均={statistics.median(st['mark']) if st['mark'] else 0:.2f}")
print(f"  hover_cb n={len(st['cb'])} 合计={sum(st['cb']):7.1f} 均={statistics.median(st['cb']) if st['cb'] else 0:.2f}")
print(f"  draw     n={len(st['dt'])} 合计={sum(st['dt']):7.1f}")
print(f"  processEvents 合计={sum(st['pe']):7.1f}  n={len(st['pe'])}")
print(f"  未归因 = {tot - sum(st['mark']) - sum(st['cb']) - sum(st['dt']):7.1f}")
print()
print("  draw 触发来源（去重）：")
seen=set()
for s in st["draw"]:
    key="  ".join(f"{f.name}:{f.lineno}" for f in s)
    if key not in seen:
        seen.add(key); print(f"    {key}")
