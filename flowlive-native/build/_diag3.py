# -*- coding: utf-8 -*-
import os, sys, time, statistics, traceback
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

# 统计：全量 draw 次数 + 每次的调用栈来源
cnt = {"draw":0, "resize":0, "stacks":[]}
_draw_orig = fc.draw
def draw():
    cnt["draw"] += 1
    if cnt["draw"] <= 40:
        st = [f"{f.name}:{f.lineno}" for f in traceback.extract_stack()[-6:-1]]
        cnt["stacks"].append("  ".join(st))
    t=time.perf_counter(); _draw_orig(); cnt.setdefault("dt",[]).append((time.perf_counter()-t)*1000)
fc.draw = draw
_res_orig = fc.resizeEvent
def resizeEvent(ev):
    cnt["resize"] += 1
    return _res_orig(ev)
fc.resizeEvent = resizeEvent

widths = []
fc._hover_px = None; app.processEvents()
widths.append(fc.width())
x0, x1, y = 60, fc.width()-60, int(fc.height()*0.45)
N = 200
t0=time.perf_counter()
for j in range(N):
    x = int(round(x0 + (x1-x0)*j/max(1,N-1)))
    app.sendEvent(fc, QMouseEvent(QEvent.MouseMove, QPointF(x,y), QPointF(x,y),
                                  Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    app.processEvents()
    if fc.width() not in widths: widths.append(fc.width())
tot=(time.perf_counter()-t0)*1000
print(f"总 {tot:.0f} ms / {N} = {tot/N:.2f} ms/事件")
print(f"  全量 draw 次数 = {cnt['draw']}   合计 {sum(cnt.get('dt',[])):.0f} ms")
print(f"  resizeEvent 次数 = {cnt['resize']}")
print(f"  画布宽度变化序列 = {widths}")
print()
print("  前几次 draw 的调用来源：")
for s in cnt["stacks"][:8]:
    print(f"    {s}")
