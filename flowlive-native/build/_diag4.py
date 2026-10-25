# -*- coding: utf-8 -*-
"""验证假设：悬停表格内容变化 → 布局重算 → 图表被 resize → blit 快照失效。"""
import os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QFont
from PySide6.QtWidgets import QApplication
import probe_lag_e2e as P

app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))

def run(label, wire_cb):
    win, series = P.build_window(app)
    fc = win.ch_fan; fc._n = P.T; fc._times = P._times()
    fc.hover_cb = win.on_fan_hover if wire_cb else None
    n_draw = [0]; widths = []
    _d = fc.draw
    def draw():
        n_draw[0] += 1; return _d()
    fc.draw = draw
    fc._hover_px = None; app.processEvents()
    widths.append(fc.width())
    w0 = fc.width()
    x0, x1, y = 60, w0-60, int(fc.height()*0.45)
    N = 60
    t0 = time.perf_counter()
    for j in range(N):
        x = int(round(x0 + (x1-x0)*j/max(1,N-1)))
        app.sendEvent(fc, QMouseEvent(QEvent.MouseMove, QPointF(x,y), QPointF(x,y),
                                      Qt.NoButton, Qt.NoButton, Qt.NoModifier))
        app.processEvents()
        if fc.width() != widths[-1]:
            widths.append(fc.width())
    tot = (time.perf_counter()-t0)*1000
    print(f"[{label}]  {tot/N:6.2f} ms/事件 · 全量 draw={n_draw[0]} · 宽度序列={widths}")
    win.close()

run("接通 hover_cb（填表）", True)
run("断开 hover_cb（不填表）", False)

# 再看表格自身的 sizeHint 是否随内容变化
import native_app as NA
t = NA.make_table(["#", "题材", "净额(亿)", "5分钟(亿)", "辨识度"], stretch_col=1,
                  align={0:"c", 1:"l"})
t.resize(560, 420)
sh0 = t.sizeHint().width(); mh0 = t.minimumSizeHint().width()
NA.fill_table(t, [[i, f"板块名称示例{i}概念", f"+{i*1.11:.2f}", f"+{i*.33:.2f}", "1.23"] for i in range(4)])
sh1 = t.sizeHint().width(); mh1 = t.minimumSizeHint().width()
NA.fill_table(t, [[i, f"超长题材名称示例第{i}号概念板块", f"+{i*1111.11:.2f}", f"-{i*333.99:.2f}", "12.34"] for i in range(14)])
sh2 = t.sizeHint().width(); mh2 = t.minimumSizeHint().width()
print()
print(f"  表格 sizeHint  宽: 空={sh0} 4行短={sh1} 14行长={sh2}   ← 变化 {sh2-sh0:+d}px")
print(f"  表格 minimumSizeHint 宽: 空={mh0} 4行短={mh1} 14行长={mh2}   ← 变化 {mh2-mh0:+d}px")
