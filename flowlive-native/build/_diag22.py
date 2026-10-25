# -*- coding: utf-8 -*-
"""未归因的 2080ms：直接量 sendEvent 与 processEvents 各自耗时。"""
import os, sys, time, collections
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
win.on_fan_hover(100, P._times()[100]); app.processEvents()

t_send=t_pe=0.0
t_by=collections.Counter(); n_by=collections.Counter()
_orig_notify=app.notify
def notify(r,e):
    t0=time.perf_counter(); x=_orig_notify(r,e); dt=(time.perf_counter()-t0)*1000
    k=e.type().name; t_by[k]+=dt; n_by[k]+=1; return x
app.notify=notify

fc._hover_px=None; app.processEvents()
t_by.clear(); n_by.clear()
N=200
t0=time.perf_counter()
for j in range(N):
    x = 60 + int((fc.width()-120)*j/max(1,N-1))
    ev = QMouseEvent(QEvent.MouseMove, QPointF(x,140), QPointF(x,140),
                     Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    a=time.perf_counter(); app.sendEvent(fc, ev); t_send+=(time.perf_counter()-a)*1000
    a=time.perf_counter(); app.processEvents(); t_pe+=(time.perf_counter()-a)*1000
tot=(time.perf_counter()-t0)*1000
app.notify=_orig_notify
print(f"总 {tot:.0f} ms / {N} = {tot/N:.2f} ms/事件")
print(f"  sendEvent 合计    = {t_send:8.1f}  均 {t_send/N:.2f}")
print(f"  processEvents 合计 = {t_pe:8.1f}  均 {t_pe/N:.2f}")
print()
for k,v in t_by.most_common(10):
    print(f"    {k:34s} n={n_by[k]:6d}  {v:9.1f} ms  均 {v/max(1,n_by[k]):7.3f}")
