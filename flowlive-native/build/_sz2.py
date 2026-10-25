import os, sys
os.environ["LOCALAPPDATA"] = os.path.join(os.environ.get("TEMP","/tmp"), "szt2")
sys.argv = ["x", "--shot", "z"]
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "core"))
from PySide6.QtWidgets import QApplication
app = QApplication([])
import native_app as NA
w = NA.MainWindow(); w.show(); app.processEvents()
print("窗口 minimumSizeHint:", w.minimumSizeHint().width())
for i in range(w.tabs.count()):
    pg = w.tabs.widget(i)
    print(f"  页[{i}] {w.tabs.tabText(i):<8s} minW={pg.minimumSizeHint().width():>5d}"
          f"  hintW={pg.sizeHint().width():>5d}")
print()
print("--- 各画布 minW ---")
for nm in ("ch_index","ch_flow","ch_fan","ch_lead","ch_stage","ch_theme"):
    c = getattr(w, nm, None)
    if c is not None:
        print(f"  {nm:<10s} minW={c.minimumSizeHint().width():>5d} hintW={c.sizeHint().width():>5d}")
print()
for nm in ("t_hover","t_watch","t_base_in","t_base_out"):
    t = getattr(w, nm, None)
    if t is not None:
        print(f"  {nm:<12s} minW={t.minimumSizeHint().width():>5d} hintW={t.sizeHint().width():>5d}"
              f"  cols={t.columnCount()}")
w.close()
