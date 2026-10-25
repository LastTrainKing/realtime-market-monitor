# -*- coding: utf-8 -*-
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
import bench_hover as BH, native_app as NA
app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))

print("=== 3a: _on_leave 后 3px 差异在哪 ===")
fc, s = BH.make_fan(); fc.resize(1002,367); fc.plot(s)
fc._mark(59); fc._hover_px = 59
fc._on_leave()
after = np.asarray(fc.buffer_rgba()).copy()
# 干净参考
if fc._vline is not None:
    fc._vline.remove(); fc._vline=None
fc._invalidate_blit(); fc.draw()
clean = np.asarray(fc.buffer_rgba()).copy()
d = (clean != after).any(axis=2)
xs=np.where(d.any(axis=0))[0]; ys=np.where(d.any(axis=1))[0]
print(f"  差异={int(d.sum())}  列={list(xs)}  行={list(ys)}")
for x in xs:
    for y in ys:
        print(f"    ({x},{y}) clean={clean[y,x][:3]} after={after[y,x][:3]}")
print()
print("=== 2a 117px: 差异行 y=33 是什么 ===")
fc2, s2 = BH.make_fan(); fc2.resize(1002,367); fc2.plot(s2)
ax=fc2.fig.axes[0]
print(f"  y=33 对应 data 坐标 y={ax.transData.inverted().transform((200, 33))[1]:.3f}")
print(f"  axhline(0) 位于像素 y = {ax.transData.transform((0,0))[1]:.2f}")
print(f"  ax.bbox.y0={ax.bbox.y0:.1f} y1={ax.bbox.y1:.1f}")
# 检查是不是 axhline(0) 的抗锯齿
print(f"  曲线数 = {len([l for l in ax.get_lines() if len(list(l.get_xdata()))==240])}")
print(f"  axhline(0) 的线宽/alpha = {[ (l.get_linewidth(), l.get_alpha()) for l in ax.get_lines() if len(list(l.get_xdata()))==2 ]}")
