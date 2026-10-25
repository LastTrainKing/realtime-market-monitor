# -*- coding: utf-8 -*-
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
import bench_hover as BH
app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))
fc,s = BH.make_fan(); fc.resize(1002,367); fc.plot(s)
ax=fc.fig.axes[0]
vl = ax.axvline(59, color='#2f6fe4', linewidth=1.0, alpha=.75, zorder=3)
fc.draw()
bb = vl.get_window_extent(fc.get_renderer())
print(f"  vline 像素包围盒 = {bb.extents}  高={bb.height:.2f} 宽={bb.width:.2f}")
print(f"  ax.bbox          = {ax.bbox.extents}")
print(f"  matplotlib 用 marking 风格时的线帽：")
print(f"    get_solid_capstyle = {vl.get_solid_capstyle()}")
print(f"    get_dash_capstyle  = {vl.get_dash_capstyle()}")
print(f"  轴高 = {ax.bbox.height:.1f}")
print()
print(f"  ★ vline 高 {bb.height:.2f} vs 轴高 {ax.bbox.height:.1f}  → 超出 {bb.height-ax.bbox.height:+.2f} px")
print(f"  ★ vline 顶边 y1={bb.y1:.2f} vs 轴顶 y1={ax.bbox.y1:.2f}")
# 2026-09: axvline 的顶部因 projecting cap 会探出上边
print()
# 检查 copy_from_bbox 的取整
region = fc.copy_from_bbox(ax.bbox)
print(f"  copy_from_bbox 取整后 extent = {region.get_extents()}")
