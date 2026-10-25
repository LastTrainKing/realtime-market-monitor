# -*- coding: utf-8 -*-
"""验证：把 hover 表格与标签更新去掉，延迟立刻降下来吗？"""
import os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QFont
from PySide6.QtWidgets import QApplication
import probe_lag_e2e as P, native_app as NA

app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))

def run(label, kind):
    win, series = P.build_window(app)
    fc = win.ch_fan; fc._n = P.T; fc._times = P._times()
    if kind == "none":
        fc.hover_cb = None
    elif kind == "label_only":
        fc.hover_cb = lambda i, hm: win.lbl_hover.setText(
            f"时点 {hm} · 图上可见 14 条 · 合计 +950.8 亿")
    elif kind == "full":
        fc.hover_cb = win.on_fan_hover
    fc._hover_px = None; app.processEvents()
    N = 100
    t0 = time.perf_counter()
    for j in range(N):
        x = 60 + int((fc.width()-120)*j/max(1,N-1))
        app.sendEvent(fc, QMouseEvent(QEvent.MouseMove, QPointF(x,140),
                                      QPointF(x,140), Qt.NoButton,
                                      Qt.NoButton, Qt.NoModifier))
        app.processEvents()
    tot = (time.perf_counter()-t0)*1000
    print(f"  [{label:22s}] {tot/N:6.2f} ms/事件")
    win.close()

run("无回调", "none")
run("仅更新标签文字", "label_only")
run("完整 on_fan_hover", "full")
