import os, sys
os.environ["LOCALAPPDATA"] = os.path.join(os.environ.get("TEMP","/tmp"), "szt")
sys.argv = ["x", "--shot", "z"]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))
from PySide6.QtWidgets import QApplication
app = QApplication([])
import native_app as NA
w = NA.MainWindow()
w.show(); app.processEvents()
print("minimumSizeHint :", w.minimumSizeHint())
print("minimumSize     :", w.minimumSize())
print("sizeHint        :", w.sizeHint())
w.resize(1480, 940)
for _ in range(4):
    app.processEvents()
print("resize 后实际   :", w.width(), "x", w.height())
print("屏幕可用区域    :", app.primaryScreen().availableGeometry())
print("屏幕分辨率      :", app.primaryScreen().size(), "DPR:", app.primaryScreen().devicePixelRatio())
tabs = w.tabs
print("tabs minimumSizeHint:", tabs.minimumSizeHint(), " sizeHint:", tabs.sizeHint())
print("tabBar minimumSizeHint:", tabs.tabBar().minimumSizeHint())
w.close()
