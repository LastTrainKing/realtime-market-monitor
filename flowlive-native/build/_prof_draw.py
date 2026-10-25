# -*- coding: utf-8 -*-
import os, sys, cProfile, pstats, io
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
import native_app as NA
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
import bench_hover as BH
fc, _ = BH.make_fan(); fc.resize(1002, 367)
fc.draw()
pr = cProfile.Profile(); pr.enable()
for _ in range(3): fc.draw()
pr.disable()
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(28)
out = s.getvalue()
print(out[:6000])
