# -*- coding: utf-8 -*-
"""假设：blit 里竖线被画在曲线**之上**，全量 draw 里 zorder=1 在曲线**之下** → 像素差异。"""
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

for zo in (1, 3):
    fc, series = BH.make_fan(); fc.resize(1002, 367); fc.plot(series)
    ax = fc.fig.axes[0]
    # 参考：全量 draw，竖线 zorder=zo
    fc.draw()
    rl = ax.axvline(120, color=fc.th['accent'], linewidth=1.0, alpha=.75, zorder=zo)
    fc.draw(); ref = np.asarray(fc.buffer_rgba()).copy()
    rl.remove()
    # 被测：blit
    fc._vline = None; fc._invalidate_blit()
    fc._vline = ax.axvline(120, color=fc.th['accent'], linewidth=1.0, alpha=.75, zorder=zo)
    bg = fc._ensure_blit_bg(ax)
    fc.restore_region(bg); ax.draw_artist(fc._vline); fc.blit(ax.bbox)
    got = np.asarray(fc.buffer_rgba()).copy()
    d = (ref != got).any(axis=2)
    print(f"  zorder={zo}: 差异像素 = {int(d.sum()):5d}")
    if d.sum():
        xs = np.where(d.any(axis=0))[0]; ys = np.where(d.any(axis=1))[0]
        # 竖线 x=120 在像素上的位置
        x120 = ax.transData.transform((120, 0))[0]
        print(f"           差异列范围 x=[{xs.min()},{xs.max()}]  (竖线在 x≈{x120:.0f})")
        print(f"           差异行范围 y=[{ys.min()},{ys.max()}]  ax高={ax.bbox.height:.0f}")
