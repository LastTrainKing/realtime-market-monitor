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

print("=== 4e：换主题后竖线颜色 ===")
fc, s = BH.make_fan(); fc.resize(1002,367); fc.plot(s)
fc._mark(100)
print(f"  light accent={fc.th['accent']}  vline颜色={fc._vline.get_color()}")
fc.apply_theme(NA.coloring(NA.THEMES['dark']))
print(f"  dark  accent={fc.th['accent']}  vline颜色={fc._vline.get_color()}   ← 是否更新?")
print()

print("=== 2a：连续移动后 117px 差异在哪 ===")
fc2, s2 = BH.make_fan(); fc2.resize(1002,367); fc2.plot(s2)
for k in range(20,60): fc2._mark(k)
got = np.asarray(fc2.buffer_rgba()).copy()
# 参考
old = fc2._vline
old.remove(); fc2._vline=None; fc2._invalidate_blit(); fc2.draw()
ax = fc2.fig.axes[0]
ax.axvline(59, color=fc2.th['accent'], linewidth=1.0, alpha=.75, zorder=3)
fc2.draw(); ref = np.asarray(fc2.buffer_rgba()).copy()
d = (ref != got).any(axis=2)
xs = np.where(d.any(axis=0))[0]; ys = np.where(d.any(axis=1))[0]
print(f"  差异像素={int(d.sum())}")
print(f"  差异列 x={xs.min()}..{xs.max()}  行 y={ys.min()}..{ys.max()}")
print(f"  各列差异数 = {[(int(c), int(d[:,c].sum())) for c in xs]}")
x59 = ax.transData.transform((59,0))[0]
print(f"  k=59 在像素 x≈{x59:.2f}")
print(f"  ax.bbox = {ax.bbox.extents}")
