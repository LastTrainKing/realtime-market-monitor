# -*- coding: utf-8 -*-
"""找出 card_side 链路上所有会随内容变化的 sizeHint。"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
import probe_lag_e2e as P
import native_app as NA

app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))
win, series = P.build_window(app)

def snap(tag):
    out = []
    for nm, w in (("card_fan", win.ch_fan.parentWidget()),
                  ("ch_fan", win.ch_fan),
                  ("card_side", win.t_hover.parentWidget()),
                  ("lbl_hover", win.lbl_hover),
                  ("t_hover", win.t_hover)):
        out.append(f"{nm}: w={w.width():4d} sh={w.sizeHint().width():4d} min={w.minimumSizeHint().width():4d}")
    print(f"  [{tag}]")
    for o in out:
        print(f"     {o}")

snap("初始")
NA.fill_table(win.t_hover, [[i, f"超长题材名称第{i}号概念板块", f"+{i*111.1:.2f}", f"-{i*333.99:.2f}", "12.34"] for i in range(14)])
win.lbl_hover.setText("时点 10:30  ·  图上可见 14 条 ·  合计 -1234.5 亿")
app.processEvents()
snap("填入长内容")
