# -*- coding: utf-8 -*-
"""验证 lbl_hover 的 sizeHint 是否随文字变化 → 驱动布局 → 挤压图表。"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
import probe_lag_e2e as P

app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))
win, series = P.build_window(app)
lb = win.lbl_hover
print(f"lbl_hover: wordWrap={lb.wordWrap()}  sizePolicy.hPolicy={lb.sizePolicy().horizontalPolicy()}")
print()
print("  文本 → sizeHint 宽度")
for txt in ["时点数据 · 鼠标移到曲线上查看任意分钟",
            "时点 09:31  ·  图上可见 14 条 ·  合计 +0.0 亿",
            "时点 14:56  ·  图上可见 14 条 ·  合计 +950.8 亿",
            "时点 10:30  ·  图上可见 14 条 ·  合计 -1234.5 亿"]:
    lb.setText(txt)
    print(f"    {lb.sizeHint().width():5d}px   {txt}")
print()
print("→ sizeHint 宽度相差 %.0f px，而它直接进 fan_row 的水平布局" % 0)

# 实测：改 lbl_hover 文本是否直接把画布挤窄
w_before = win.ch_fan.width()
seqs = []
for txt in ["时点数据 · 鼠标移到曲线上查看任意分钟",
            "时点 09:31  ·  图上可见 14 条 ·  合计 +0.0 亿",
            "时点 10:30  ·  图上可见 14 条 ·  合计 -1234.5 亿",
            "时点数据 · 鼠标移到曲线上查看任意分钟"]:
    lb.setText(txt); app.processEvents()
    seqs.append(win.ch_fan.width())
print(f"  改 lbl_hover 文本 → 画布宽度序列 = {seqs}")
print(f"  （初始 {w_before}）")
