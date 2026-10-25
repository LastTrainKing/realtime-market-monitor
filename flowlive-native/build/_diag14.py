# -*- coding: utf-8 -*-
"""剩余的 31ms 到底是谁？把 processEvents 内部的事件按类型统计。"""
import os, sys, time, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent, QFont
from PySide6.QtWidgets import QApplication
import probe_lag_e2e as P

app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))
win, series = P.build_window(app)
fc = win.ch_fan; fc._n = P.T; fc._times = P._times()

# 事件循环里到底发生了什么：统计 widget 上的 paint / 各种事件
ev_cnt = collections.Counter()
_orig_notify = app.notify
t_by_type = collections.Counter()
def notify(receiver, ev):
    t = time.perf_counter()
    r = _orig_notify(receiver, ev)
    dt = (time.perf_counter() - t) * 1000
    key = f"{ev.type().name}"
    ev_cnt[key] += 1
    t_by_type[key] += dt
    return r
app.notify = notify

# 关掉探针里的启动定时器干扰：记录它们是否触发
fired = []
def _hook():
    fired.append(1)
QTimer.singleShot(200, _hook)

fc._hover_px = None; app.processEvents()
N = 200
t0 = time.perf_counter()
for j in range(N):
    x = 60 + int((fc.width()-120) * j / max(1, N-1))
    app.sendEvent(fc, QMouseEvent(QEvent.MouseMove, QPointF(x,140), QPointF(x,140),
                                  Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    app.processEvents()
tot = (time.perf_counter()-t0)*1000
app.notify = _orig_notify
print(f"总 {tot:.0f} ms / {N} = {tot/N:.2f} ms/事件")
print(f"hook 被调次数 = {len(fired)}")
print()
print("  按事件类型统计（通知次数 / 累计耗时 ms）：")
for k, v in t_by_type.most_common(14):
    print(f"    {k:36s} n={ev_cnt[k]:6d}  {v:9.1f} ms")
