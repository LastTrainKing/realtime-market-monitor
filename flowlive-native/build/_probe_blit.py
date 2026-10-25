# -*- coding: utf-8 -*-
"""拆解 draw() 的 120ms，并验证 blit 局部重绘的可行性/成本。"""
import os, sys, time, statistics
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
import native_app as NA
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
import bench_hover as BH

def med(fn, n=9):
    xs=[]
    for _ in range(n):
        t=time.perf_counter(); fn(); xs.append((time.perf_counter()-t)*1000)
    return statistics.median(xs)

fc, series = BH.make_fan(); fc.resize(1002, 367); fc.draw()
fig = fc.fig; ax = fig.axes[0]; leg = fig.legends[0]
R = fig.canvas.get_renderer()

print("=== A. 全量 draw 的成本构成 ===")
print(f"  draw() 原始                       {med(fc.draw):8.2f} ms")
leg.set_visible(False); t_noleg = med(fc.draw); leg.set_visible(True); fc.draw()
print(f"  draw() 隐藏图例                    {t_noleg:8.2f} ms")
# 曲线：去掉 20 条曲线
for ln in list(ax.get_lines()):
    if len(ln.get_xdata()) == 240:
        ln.set_visible(False)
t_noline = med(fc.draw)
for ln in list(ax.get_lines()):
    if len(ln.get_xdata()) == 240:
        ln.set_visible(True)
fc.draw()
print(f"  draw() 隐藏20条曲线                {t_noline:8.2f} ms")

print()
print("=== B. 图例为什么这么贵 ===")
print(f"  leg.get_window_extent(R) 单次      {med(lambda: leg.get_window_extent(R)):8.2f} ms")
print(f"  leg.draw(R) 单次                   {med(lambda: leg.draw(R)):8.2f} ms")
print(f"  图例条目数 = {len(leg.get_texts())}")

print()
print("=== C. blit 局部重绘路径（候选方案） ===")
bg = fc.copy_from_bbox(ax.bbox)
vt = ax.axvline(120, color=fc.th['accent'], linewidth=1.0, alpha=.75, zorder=1)
fc.draw()
bg = fc.copy_from_bbox(ax.bbox)          # 含竖线的位置? 不, snapshot 在画之前
print(f"  copy_from_bbox(ax.bbox)            {med(lambda: fc.copy_from_bbox(ax.bbox)):8.2f} ms")
print(f"  restore_region(bg)                 {med(lambda: fc.restore_region(bg)):8.2f} ms")
print(f"  ax.draw_artist(vline)              {med(lambda: ax.draw_artist(vt)):8.2f} ms")
print(f"  canvas.blit(ax.bbox)               {med(lambda: fc.blit(ax.bbox)):8.2f} ms")

def _one_move(k=[100]):
    fc.restore_region(bg)
    vt.set_xdata([k[0], k[0]]); k[0] = 40 + (k[0] + 7) % 900
    ax.draw_artist(vt)
    fc.blit(ax.bbox)
print(f"  ★ 完整一次「移动竖线」             {med(_one_move):8.2f} ms")
print()
print("=== D. plot() 端到端（含图例二分定列数） ===")
t0=time.perf_counter(); fc.plot(series); dt=(time.perf_counter()-t0)*1000
print(f"  FanChart.plot() 一次               {dt:8.2f} ms   ← 数据刷新/切主题时的一次性成本")
