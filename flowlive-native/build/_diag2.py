# -*- coding: utf-8 -*-
import os, sys, time, statistics
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QFont
from PySide6.QtWidgets import QApplication
import native_app as NA
import probe_lag_e2e as P

app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))
win, series = P.build_window(app)
fc = win.ch_fan; fc._n = P.T; fc._times = P._times()

# 插桩：统计 hover_cb 的内部耗时 vs blit 耗时 vs 快照重建次数
stat = {"cb": [], "mark": [], "rebuild": [0], "leave": 0}
_cb_orig = win.on_fan_hover
def cb(i, hm):
    t=time.perf_counter(); _cb_orig(i, hm); stat["cb"].append((time.perf_counter()-t)*1000)
win.ch_fan.hover_cb = cb
_mark_orig = fc._mark
def _mark(k):
    t=time.perf_counter(); _mark_orig(k); stat["mark"].append((time.perf_counter()-t)*1000)
fc._mark = _mark
_ensure = fc._ensure_blit_bg
def _ensure2(ax):
    had = fc._blit_bg is not None and fc._blit_bbox == fc._bg_bbox_key(ax)
    r = _ensure(ax)
    if not had: stat["rebuild"][0] += 1
    return r
fc._ensure_blit_bg = _ensure2
_leave_orig = fc._on_leave
def _leave(ev=None):
    stat["leave"] += 1; return _leave_orig(ev)
fc._on_leave = _leave

fc._hover_px = None; app.processEvents()
x0, x1, y = 60, fc.width()-60, int(fc.height()*0.45)
N = 200
t0=time.perf_counter()
for j in range(N):
    x = int(round(x0 + (x1-x0)*j/max(1,N-1)))
    app.sendEvent(fc, QMouseEvent(QEvent.MouseMove, QPointF(x,y), QPointF(x,y),
                                  Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    app.processEvents()
tot=(time.perf_counter()-t0)*1000
print(f"总 {tot:.0f} ms / {N} 事件 = {tot/N:.2f} ms/事件")
print(f"  hover_cb 合计        {sum(stat['cb']):8.1f} ms  n={len(stat['cb'])}  均 {statistics.median(stat['cb']) if stat['cb'] else 0:.2f} ms")
print(f"  _mark 合计           {sum(stat['mark']):8.1f} ms  n={len(stat['mark'])}  均 {statistics.median(stat['mark']) if stat['mark'] else 0:.2f} ms")
print(f"  背景快照重建次数      {stat['rebuild'][0]}")
print(f"  _on_leave 次数        {stat['leave']}")
print(f"  未归因               {tot - sum(stat['cb']) - sum(stat['mark']):8.1f} ms")
print()
print(f"  悬停表行数 = {win.t_hover.rowCount()}   表行高 = {win.t_hover.rowHeight(0) if win.t_hover.rowCount() else '-'}")
print(f"  lbl_hover 文本 = {win.lbl_hover.text()[:60]!r}")
print(f"  画布尺寸 = {fc.width()}x{fc.height()}  轴上四边 = {tuple(round(v,1) for v in fc.fig.axes[0].bbox.extents)}")
print(f"  blit_bbox = {fc._blit_bbox}")
print(f"  当前 key   = {fc._bg_bbox_key(fc.fig.axes[0])}")
