# -*- coding: utf-8 -*-
"""隔离：画布宽度振荡是否由鼠标事件引起？还是布局自激？"""
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
win, series = P.build_window(app)
fc = win.ch_fan; fc._n = P.T; fc._times = P._times()
fc.hover_cb = None

def watch(label, inject_mouse):
    fc._hover_px = None
    app.processEvents()
    seq = [fc.width()]
    for j in range(60):
        if inject_mouse:
            x = 60 + int((fc.width()-120) * j / 59)
            app.sendEvent(fc, QMouseEvent(QEvent.MouseMove, QPointF(x, 140),
                                          QPointF(x, 140), Qt.NoButton,
                                          Qt.NoButton, Qt.NoModifier))
        app.processEvents()
        if fc.width() != seq[-1]:
            seq.append(fc.width())
    print(f"  [{label}] 宽度变化序列 = {seq[:12]}{' ...' if len(seq)>12 else ''}  共 {len(seq)-1} 次变化")

print("A. 完全不注入鼠标事件，只泵事件循环：")
watch("无鼠标", False)
print("B. 注入鼠标事件（mpl 事件已断开？否，仍连着）：")
watch("有鼠标", True)

# C. 断开 mpl motion 处理器，再注入鼠标
fc.mpl_disconnect(fc._motion_cid) if hasattr(fc, "_motion_cid") else None
print("C. 断不开（未保存 cid）→ 改为临时把 _on_motion 置空：")
fc._on_motion = lambda ev: None
watch("鼠标·处理器置空", True)

# D. 找出是谁在改宽度：打印侧栏与表格宽度
print()
print("D. 布局快照：")
card = win.t_hover.parentWidget()
for nm, w_ in (("画布", fc), ("悬停表", win.t_hover), ("表父容器", card)):
    print(f"  {nm:10s} w={w_.width():5d}  sizeHint={w_.sizeHint().width():5d}"
          f"  minSizeHint={w_.minimumSizeHint().width():5d}")
