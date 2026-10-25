# -*- coding: utf-8 -*-
"""MetaCall 是谁？9659 次 Paint 来自哪些控件？"""
import os, sys, time, collections, traceback
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

meta = collections.Counter(); paint = collections.Counter()
_orig = app.notify
def notify(recv, ev):
    t = ev.type()
    if t == QEvent.MetaCall:
        try:
            nm = type(recv).__name__
            if hasattr(recv, "__self__"):
                nm = f"{type(recv.__self__).__name__}.{getattr(recv,'__name__','?')}"
            meta[nm] += 1
        except Exception: pass
    elif t == QEvent.Paint:
        paint[type(recv).__name__] += 1
    return _orig(recv, ev)
app.notify = notify

fc._hover_px = None; app.processEvents()
meta.clear(); paint.clear()
N = 100
t0=time.perf_counter()
for j in range(N):
    x = 60 + int((fc.width()-120)*j/max(1,N-1))
    app.sendEvent(fc, QMouseEvent(QEvent.MouseMove, QPointF(x,140), QPointF(x,140),
                                  Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    app.processEvents()
tot=(time.perf_counter()-t0)*1000
app.notify = _orig
print(f"总 {tot:.0f} ms / {N} = {tot/N:.2f} ms/事件")
print()
print("  MetaCall 接收者 Top10（每 100 次注入）：")
for k,v in meta.most_common(10):
    print(f"    {v:6d}  {k}")
print()
print("  Paint 接收者 Top10：")
for k,v in paint.most_common(10):
    print(f"    {v:6d}  {k}")
