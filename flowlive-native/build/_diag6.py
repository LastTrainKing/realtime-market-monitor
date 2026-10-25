# -*- coding: utf-8 -*-
"""干净单窗口测试：宽度是否随悬停变化？（排除多窗口干扰）"""
import os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QFont
from PySide6.QtWidgets import QApplication
import probe_lag_e2e as P
import native_app as NA

app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))
win, series = P.build_window(app)
fc = win.ch_fan; fc._n = P.T; fc._times = P._times()

print("=== 单窗口 · 接通 hover_cb（真实路径）===")
seq = [fc.width()]
draws = [0]
_d = fc.draw
def draw():
    draws[0] += 1; return _d()
fc.draw = draw
fc._hover_px = None; app.processEvents()
N = 120
t0 = time.perf_counter()
for j in range(N):
    x = 60 + int((fc.width()-120) * j / max(1, N-1))
    app.sendEvent(fc, QMouseEvent(QEvent.MouseMove, QPointF(x, 140),
                                  QPointF(x, 140), Qt.NoButton,
                                  Qt.NoButton, Qt.NoModifier))
    app.processEvents()
    if fc.width() != seq[-1]:
        seq.append(fc.width())
    if seq[-1] != seq[0]:
        break
tot = (time.perf_counter()-t0)*1000
print(f"  耗时 {tot:.0f} ms / {N} = {tot/N:.2f} ms/事件")
print(f"  宽度序列 = {seq}")
print(f"  全量 draw 次数 = {draws[0]}")
print(f"  悬停表行数 = {win.t_hover.rowCount()}")
