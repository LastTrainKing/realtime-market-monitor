# -*- coding: utf-8 -*-
"""对照实验：不注入任何鼠标，只泵事件循环同样次数 —— 看 MetaCall 是否照样出现。
   若照样出现，说明它是**后台抓取线程的信号投递**（探针自身的噪声），
   与光标跟随无关。"""
import os, sys, time, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QFont
from PySide6.QtWidgets import QApplication
import probe_lag_e2e as P
app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))

t_by=collections.Counter(); n_by=collections.Counter()
_orig=app.notify
def notify(r,e):
    t0=time.perf_counter(); x=_orig(r,e); dt=(time.perf_counter()-t0)*1000
    k=e.type().name; t_by[k]+=dt; n_by[k]+=1; return x

def run(label, inject):
    global t_by, n_by
    win, series = P.build_window(app)
    fc = win.ch_fan; fc._n=P.T; fc._times=P._times()
    win.on_fan_hover(100, P._times()[100]); app.processEvents()
    app.notify = notify
    t_by.clear(); n_by.clear()
    fc._hover_px=None; app.processEvents()
    N=200
    t0=time.perf_counter()
    for j in range(N):
        if inject:
            x = 60 + int((fc.width()-120)*j/max(1,N-1))
            app.sendEvent(fc, QMouseEvent(QEvent.MouseMove, QPointF(x,140), QPointF(x,140),
                                          Qt.NoButton, Qt.NoButton, Qt.NoModifier))
        app.processEvents()
    tot=(time.perf_counter()-t0)*1000
    app.notify=_orig
    mc_t = t_by.get("MetaCall",0); mc_n = n_by.get("MetaCall",0)
    print(f"  [{label}] 总 {tot/N:6.2f} ms/次   MetaCall n={mc_n:4d}  {mc_t:8.1f} ms"
          f"   其余 = {(tot-mc_t)/N:6.2f} ms/次")
    win.close()

run("不注入鼠标（对照）", False)
run("注入鼠标（悬停）  ", True)
print()
print("  → 若两者 MetaCall 量级相当，说明那是**后台线程信号**的固定背景开销，")
print("    应从光标延迟里扣除，而不是当作悬停成本。")
